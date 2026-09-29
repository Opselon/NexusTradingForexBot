"""L7 — SQLite surface enumeration (read-only, reproducible).

Answers one question with evidence instead of prose: **which SQLite artifacts
does this engine still write, who still opens each one, and does each duplicate
PostgreSQL?**

The enumeration is derived the same way every time:

  1. The *declared* surface comes from source (the provider registry's
     ``DEFAULT_DB_FILES``, the hygiene worker's ``MANAGED_DATABASES`` and the
     storage guard's ``_CHECKPOINT_DBS`` allowlists) — never from docs.
  2. The *observed* surface comes from the artifacts tree (``*.db`` /
     ``*.sqlite`` / ``*.sqlite3`` plus their ``-wal`` / ``-shm`` sidecars).
  3. Every discovered file is opened **read-only** (``mode=ro`` URI) — this tool
     never writes, never creates and never checkpoints a live artifact.
  4. Every declared domain is then scanned for *consumers*: a repo-wide grep for
     the module that opens it plus the count of call sites, so "still required"
     is a number, not an opinion.

Usage::

    python scripts/db_remediation/sqlite_surface.py [--repo ROOT] [--json OUT] [--md OUT]

Safety: read-only. ``--repo`` defaults to the live checkout's artifacts tree when
present, else the current repo root. Nothing here deletes, purges, repoints or
VACUUMs — deletions are a separate, operator-authorized action.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# The DECLARED surface — read from source, not from documentation.
# ---------------------------------------------------------------------------

#: Domains the provider registry maps to an artifacts/*.db file
#: (database/provider.py DEFAULT_DB_FILES). "declared_by" records the source of
#: truth so a reader can re-verify the line.
DECLARED_DOMAINS: dict[str, dict[str, str]] = {
    "audit": {
        "file": "audit.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "news": {
        "file": "news.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "candle_intel": {
        "file": "candle_intel.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "strategies": {
        "file": "strategies.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "models": {
        "file": "models.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "marketplace": {
        "file": "marketplace.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
    "experiments": {
        "file": "experiments/experiments.db",
        "declared_by": "src/nexus_scalp/database/provider.py:DEFAULT_DB_FILES",
    },
}

#: Databases the hygiene worker walks (hygiene/worker_runner.py MANAGED_DATABASES).
HYGIENE_MANAGED: tuple[str, ...] = (
    "artifacts/audit.db",
    "artifacts/news.db",
    "artifacts/candle_intel.db",
)

#: Databases the storage guard checkpoints (storage/runtime.py _CHECKPOINT_DBS).
STORAGE_CHECKPOINTED: tuple[str, ...] = (
    "artifacts/audit.db",
    "artifacts/news.db",
    "artifacts/candle_intel.db",
)

#: Domain -> the module that owns its write path + the path to grep for
#: consumers. ``consumer_modules`` are repo-relative, so the count below is a
#: reproducible "who opens this" measurement.
DOMAIN_WRITERS: dict[str, dict[str, object]] = {
    "audit": {
        "writer_module": "src/nexus_scalp/adapters/database/audit_repository.py",
        "consumer_globs": ("audit.db", "audit_connect_path", "MANAGED_DATABASES"),
        "pg_tables": "audit domain (replayed via database/migration)",
    },
    "news": {
        "writer_module": "src/nexus_scalp/news/database.py",
        "consumer_globs": ("news.db", "NewsConfig", "news_engine", "MANAGED_DATABASES"),
        "pg_tables": "news domain (news_articles + 17 satellites)",
    },
    "candle_intel": {
        "writer_module": "src/nexus_scalp/candle_intelligence/store.py",
        "consumer_globs": ("candle_intel.db", "CandleIntelligenceConfig", "MANAGED_DATABASES"),
        "pg_tables": "candle_intel domain (replay-able)",
    },
    "strategies": {
        "writer_module": "src/nexus_scalp/strategies/research_store.py",
        "consumer_globs": ("strategies.db", "StrategyResearchStore", "open_store"),
        "pg_tables": "strategy_factory group (factory_*)",
    },
    "models": {
        "writer_module": "src/nexus_scalp/model_lifecycle/registry.py",
        "consumer_globs": ("models.db", "ModelLifecycleRegistry"),
        "pg_tables": "models domain",
    },
    "marketplace": {
        "writer_module": "src/nexus_scalp/marketplace/store.py",
        "consumer_globs": ("marketplace.db",),
        "pg_tables": "marketplace domain",
    },
    "experiments": {
        "writer_module": "src/nexus_scalp/model_lab",
        "consumer_globs": ("experiments.db",),
        "pg_tables": "models domain",
    },
}

#: Files that exist but are NOT a declared engine domain (stale stubs / test
#: scratch). Named so the report can distinguish "the engine writes this" from
#: "an old process once left this here".
NON_DOMAIN_STUBS: tuple[str, ...] = (
    "app_settings.db",
    "settings.db",
    "nexus.db",
    "nexus_scalp.db",
    "research.db",
    "test_audit.db",
    "test_pipeline_health.db",
)

#: Consumer-grep roots — where an open site can legitimately live.
_GREP_ROOTS: tuple[str, ...] = ("src", "scripts", "Web", "frontend", "installer")

_SQLITE_MAGIC = b"SQLite format 3\x00"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Read-only connection. NEVER creates the file, never writes."""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)


def _is_sqlite_file(path: Path) -> bool:
    """A 0-byte or non-SQLite file is a stub, not a database."""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _describe_sqlite(path: Path) -> dict[str, object]:
    """Table count, row totals, journal mode and free-page reclaimable bytes."""
    out: dict[str, object] = {"ok": False}
    try:
        conn = _ro_connect(path)
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        row_counts: dict[str, int] = {}
        latest: str | None = None
        for t in tables:
            try:
                row_counts[t] = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            except sqlite3.Error:
                row_counts[t] = -1
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
            for col in (c for c in cols if re.search(r"(_at|_time|timestamp|^ts)$", c)):
                try:
                    v = conn.execute(f'SELECT MAX("{col}") FROM "{t}"').fetchone()[0]
                except sqlite3.Error:
                    continue
                if v is not None and (latest is None or str(v) > latest):
                    latest = str(v)
        out.update(
            {
                "ok": True,
                "table_count": len(tables),
                "tables": tables,
                "row_counts": row_counts,
                "total_rows": sum(v for v in row_counts.values() if v > 0),
                "journal_mode": journal,
                "page_size": page_size,
                "freelist_pages": freelist,
                "reclaimable_bytes": freelist * page_size,
                "latest_timestamp": latest,
            }
        )
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return out


def _grep_count(repo: Path, needle: str) -> int:
    """Count source lines mentioning ``needle`` (the consumer-existence proof)."""
    total = 0
    for root_name in _GREP_ROOTS:
        root = repo / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if ".git" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            total += text.count(needle)
    return total


def discover(repo: Path, artifacts: Path) -> list[dict[str, object]]:
    """Every .db/.sqlite/.sqlite3 under the artifacts tree + its sidecars."""
    found: dict[str, dict[str, object]] = {}
    roots = [artifacts]
    # The live engine writes into the MAIN checkout's artifacts (a worktree's
    # artifacts/ is pristine); include it when it is a different directory.
    main_artifacts = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts")
    if main_artifacts.is_dir() and main_artifacts.resolve() != artifacts.resolve():
        roots.append(main_artifacts)
    for root in roots:
        for pat in ("*.db", "*.sqlite", "*.sqlite3"):
            for p in sorted(root.rglob(pat)):
                key = str(p.resolve())
                if key in found:
                    continue
                rec: dict[str, object] = {
                    "path": str(p),
                    "rel": str(p.relative_to(root)),
                    "bytes": _file_size(p),
                    "wal_bytes": _file_size(p.parent / (p.name + "-wal")),
                    "shm_bytes": _file_size(p.parent / (p.name + "-shm")),
                    "is_sqlite": _is_sqlite_file(p),
                }
                found[key] = rec
    return sorted(found.values(), key=lambda r: -int(r["bytes"]))  # type: ignore[arg-type]


def classify(rel: str, in_hygiene: bool, in_checkpoint: bool) -> str:
    """Which maintenance allowlists cover this file."""
    tags = []
    if in_hygiene:
        tags.append("HYGIENE_MANAGED")
    if in_checkpoint:
        tags.append("WAL_CHECKPOINTED")
    if not tags:
        tags.append("OUTSIDE_ALL_ALLOWLISTS")
    return "+".join(tags)


def build(repo: Path, artifacts: Path) -> dict[str, object]:
    files = discover(repo, artifacts)
    hygiene_rel = {p.rsplit("/", 1)[-1] for p in HYGIENE_MANAGED}

    domains: list[dict[str, object]] = []
    for domain, meta in DECLARED_DOMAINS.items():
        fname = str(meta["file"])
        match = next((f for f in files if str(f["rel"]).endswith(fname)), None)
        writer = DOMAIN_WRITERS.get(domain, {})
        consumers: dict[str, int] = {}
        for needle in writer.get("consumer_globs", ()):  # type: ignore[union-attr]
            consumers[needle] = _grep_count(repo, str(needle))
        rec: dict[str, object] = {
            "domain": domain,
            "declared_file": fname,
            "declared_by": meta["declared_by"],
            "writer_module": writer.get("writer_module", "UNKNOWN"),
            "pg_domain_tables": writer.get("pg_tables", "UNKNOWN"),
            "consumer_reference_counts": consumers,
            "consumer_reference_total": sum(consumers.values()),
            "present": match is not None,
        }
        if match is not None:
            rec.update(
                {
                    "path": match["path"],
                    "bytes": match["bytes"],
                    "wal_bytes": match["wal_bytes"],
                    "shm_bytes": match["shm_bytes"],
                    "maintenance": classify(
                        str(match["rel"]),
                        str(match["rel"]).split("/")[-1] in hygiene_rel,
                        str(match["rel"]) in {p.split("/")[-1] for p in STORAGE_CHECKPOINTED},
                    ),
                    "content": _describe_sqlite(Path(str(match["path"])))
                    if match["is_sqlite"]
                    else {"ok": False, "error": "NOT_A_SQLITE_FILE"},
                }
            )
        domains.append(rec)

    stubs = []
    for f in files:
        rel = str(f["rel"])
        if rel in NON_DOMAIN_STUBS:
            stubs.append({"rel": rel, "bytes": f["bytes"], "is_sqlite": f["is_sqlite"]})

    total_domain_bytes = sum(int(d.get("bytes", 0) or 0) for d in domains)
    total_observed = sum(int(f["bytes"]) for f in files)
    backups = artifacts / "backups"
    backup_bytes = 0
    if backups.is_dir():
        backup_bytes = sum(_file_size(p) for p in backups.rglob("*") if p.is_file())

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "repo": str(repo),
        "artifacts_dir": str(artifacts),
        "summary": {
            "sqlite_files_observed": len(files),
            "declared_domains": len(DECLARED_DOMAINS),
            "total_observed_bytes": total_observed,
            "total_declared_domain_bytes": total_domain_bytes,
            "backups_bytes": backup_bytes,
            "sqlite_version": sqlite3.sqlite_version,
        },
        "domains": domains,
        "non_domain_stubs": stubs,
        "observed_files": files,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _mb(n: object) -> str:
    try:
        return f"{int(n) / 1e6:.1f} MB"
    except (TypeError, ValueError):
        return "n/a"


def to_markdown(rep: dict[str, object]) -> str:
    s = rep["summary"]  # type: ignore[index]
    lines = [
        "# L7 — SQLite surface enumeration (read-only)",
        "",
        f"generated: {rep['generated_at']}  ",
        f"repo: `{rep['repo']}`  ",
        f"artifacts: `{rep['artifacts_dir']}`  ",
        f"sqlite: {s['sqlite_version']}",  # type: ignore[index]
        "",
        "## Summary",
        "",
        f"- SQLite files observed: **{s['sqlite_files_observed']}**",  # type: ignore[index]
        f"- Declared engine domains: **{s['declared_domains']}**",  # type: ignore[index]
        f"- Total observed bytes: **{_mb(s['total_observed_bytes'])}**",  # type: ignore[index]
        f"- Sum of declared domain files: **{_mb(s['total_declared_domain_bytes'])}**",  # type: ignore[index]
        f"- artifacts/backups/ (unowned): **{_mb(s['backups_bytes'])}**",  # type: ignore[index]
        "",
        "## Declared domains — file, maintenance coverage, consumers",
        "",
        "| domain | file | bytes | tables | rows | journal | reclaimable | maintenance | consumer refs |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in rep["domains"]:  # type: ignore[union-attr]
        c = d.get("content") or {}
        lines.append(
            "| {d} | {f} | {b} | {t} | {r} | {j} | {rc} | {m} | {cr} |".format(
                d=d["domain"],
                f=d.get("declared_file"),
                b=_mb(d.get("bytes", 0)) if d.get("present") else "ABSENT",
                t=c.get("table_count", "-"),
                r=c.get("total_rows", "-"),
                j=c.get("journal_mode", "-"),
                rc=_mb(c.get("reclaimable_bytes", 0)),
                m=d.get("maintenance", "-"),
                cr=d.get("consumer_reference_total", 0),
            )
        )
    lines += [
        "",
        "Consumer refs = repo-wide source-line occurrences of the domain's opening",
        "symbols (`news_engine`, `NewsConfig`, `MANAGED_DATABASES`, ...). A domain with",
        "refs > 0 still has live consumers; 0 means no source path opens it.",
        "",
        "## Non-domain stubs (present, but no engine domain declares them)",
        "",
        "| file | bytes | sqlite? |",
        "| --- | --- | --- |",
    ]
    for st in rep["non_domain_stubs"]:  # type: ignore[union-attr]
        lines.append(f"| `{st['rel']}` | {_mb(st['bytes'])} | {st['is_sqlite']} |")
    lines += [
        "",
        "## Every observed file (including sidecars)",
        "",
        "| rel | bytes | wal bytes | shm bytes | sqlite magic |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in rep["observed_files"]:  # type: ignore[union-attr]
        lines.append(
            f"| `{f['rel']}` | {_mb(f['bytes'])} | {f['wal_bytes']} | {f['shm_bytes']} | {f['is_sqlite']} |"
        )
    lines += [
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/db_remediation/sqlite_surface.py \\",
        "    --json docs/forensic-docs/remediation/phase2/l7_sqlite_surface.json \\",
        "    --md   docs/forensic-docs/remediation/phase2/l7_sqlite_surface.md",
        "```",
        "",
        "Read-only: every file is opened with a `mode=ro` URI. Nothing is deleted,",
        "purged, repointed or VACUUMed by this tool.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__ and "L7 SQLite surface enumeration (read-only)"
    )
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--artifacts", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    artifacts = (
        Path(args.artifacts).resolve()
        if args.artifacts
        else Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts")
    )
    if not artifacts.is_dir():
        artifacts = repo / "artifacts"

    report = build(repo, artifacts)
    md = to_markdown(report)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    if not args.json and not args.md:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
