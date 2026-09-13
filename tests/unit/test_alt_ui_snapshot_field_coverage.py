"""
Snapshot-field coverage gate for the NSE Alternative UI — offline, source-level.

The canonical snapshot the console renders is built by exactly one producer:
``create_app().get_system_state()`` in src/nexus_scalp/web/server.py (served by
GET /api/status and by the SSE ``event: state`` frames). Pages read dotted fields
off it (``snapshot.provenance.price``). If a page reads a field the builder never
produces, the UI shows a silent undefined — invisible at runtime, trivially
visible in source. This file makes that visible at commit time.

Method (no server, no engine, no network):
  1. Parse the builder with AST: the ``state = {...}`` literal gives the produced
     top-level keys; dict-literal / annotated-dict / subscript-assignment /
     local-dict-returning-call values give ONE nesting level (``provenance.price``).
  2. Regex the console's snapshot consumers (frontend/src/pages, components,
     hooks, app) for dotted reads on the EngineSnapshot receivers.
  3. First-level coverage is a HARD assert (missing field = red). Anything the
     scanner cannot prove from source — deeper than one nesting level, or an
     opaque producer (live_freshness payload, engine-provided dicts) — is NOT
     silently passed: it becomes an explicit xfail entry per field (with a
     reason) and a ledger row in tests/js/coverage_ledger.json under
     ``unverified_fields``.

The xfail is conditional, which is what keeps it honest: each entry re-checks the
live scan, so if a refactor or a smarter scanner ever proves the field, that test
fails (XPASS-equivalent) and demands a ledger refresh via ``write_field_ledger()``.
A green run therefore means "exactly this set of fields is unproven", never
"nothing checked". A brand-new unprovable field cannot appear quietly either: it
breaks the ledger-equality gate below.

Drift guard: the ledger lists must equal what the live scan derives, so an
unrecorded gap fails test_ledger_field_lists_match_scan instead of passing.

Mid-edit tolerance (parallel orchestrator owns src/nexus_scalp/web): the builder
parse is wrapped; on failure we fall back to the ledger's cached snapshot key set,
warn in the report, and disclose the degradation under
ledger['snapshot_parse_failures'].
"""

from __future__ import annotations

import ast
import json
import os
import re
import warnings
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER_PY = os.path.join(REPO_ROOT, "src", "nexus_scalp", "web", "server.py")
FRONTEND_SRC = os.path.join(REPO_ROOT, "frontend", "src")
LEDGER_PATH = os.path.join(REPO_ROOT, "tests", "js", "coverage_ledger.json")

SNAPSHOT_READ = re.compile(
    r"\b(?:snapshot|restSnapshot|snap)\s*\??\.\s*([A-Za-z_]\w*)"
    r"(?:\s*\??\.\s*([A-Za-z_]\w*))?(?:\s*\??\.\s*([A-Za-z_]\w*))?"
)
# Array/string/method suffixes that ride the same dotted syntax but are not
# snapshot fields (trimmed off the tail of every match).
_JS_METHODS = frozenset(
    {
        "at",
        "entries",
        "every",
        "filter",
        "find",
        "flatMap",
        "forEach",
        "includes",
        "indexOf",
        "join",
        "keys",
        "length",
        "map",
        "padEnd",
        "padStart",
        "push",
        "reduce",
        "replace",
        "slice",
        "some",
        "sort",
        "split",
        "toFixed",
        "toString",
        "toLowerCase",
        "toUpperCase",
        "trim",
        "values",
    }
)
_CONSUMER_DIRS = ("pages", "components", "hooks", "app")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _strip_ts_comments(src: str) -> str:
    """Drop JSDoc/line comments so a documented (not code) field read cannot count.

    Prose in a page's JSX comments can contain ``snapshot. Foo`` shaped text; only
    real code may register as a consumed field (mirrors the route-parity scanner).
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", " ", src)
    return src


def _dict_keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _local_dict_return(scope: ast.AST, name: str) -> set[str] | None:
    """Keys of the dict literal returned by function ``name`` inside ``scope``.

    None when the function is absent or has no literal-dict return, i.e. its
    payload is opaque to this scanner.
    """
    for node in ast.walk(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            for ret in [x for x in ast.walk(node) if isinstance(x, ast.Return)]:
                if isinstance(ret.value, ast.Dict):
                    return _dict_keys(ret.value)
            return None
    return None


def scan_snapshot_produced_keys(server_py: str = SERVER_PY) -> dict[str, dict[str, Any]]:
    """{top_level_key: {'subs': set, 'opaque_subs': bool}} from get_system_state().

    ``opaque_subs`` means the value is produced elsewhere (another builder call,
    an engine object, a ternary) so its nesting is NOT provable from this file.
    """
    src = _read(server_py)
    tree = ast.parse(src)
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "get_system_state"
        ),
        None,
    )
    assert fn is not None, "get_system_state() not found in server.py — builder moved?"

    assigns: dict[str, list[ast.expr]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns.setdefault(target.id, []).append(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns.setdefault(node.target.id, []).append(node.value)

    state_value = None
    for value in assigns.get("state", []):
        if isinstance(value, ast.Dict):
            state_value = value
    assert state_value is not None, "state = {...} literal not found in get_system_state()"

    writes: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                writes.setdefault(target.value.id, set()).add(target.slice.value)

    produced: dict[str, dict[str, Any]] = {}
    for key, value in zip(state_value.keys, state_value.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        subs: set[str] = set()
        opaque = False
        if isinstance(value, ast.Dict):
            subs |= _dict_keys(value)
        elif isinstance(value, ast.Name):
            inits = assigns.get(value.id, [])
            dicts = [i for i in inits if isinstance(i, ast.Dict)]
            lists = [i for i in inits if isinstance(i, ast.List)]
            if dicts:
                for d in dicts:
                    subs |= _dict_keys(d)
            elif not lists:
                opaque = True
            subs |= writes.get(value.id, set())
        elif isinstance(value, ast.Call):
            fname = getattr(value.func, "id", None)
            got = _local_dict_return(fn, fname) if fname else None
            if got is None and fname:
                got = _local_dict_return(tree, fname)
            if got is None:
                opaque = True
            else:
                subs |= got
        else:
            opaque = True
        produced[key.value] = {"subs": subs, "opaque_subs": opaque}
    return produced


def scan_snapshot_field_reads(frontend_src: str = FRONTEND_SRC) -> dict[str, list[str]]:
    """{dotted_field: [files...]} consumed off the snapshot in the console."""
    reads: dict[str, set[str]] = {}
    seen_any = False
    for name in _CONSUMER_DIRS:
        root = Path(frontend_src) / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.ts*")):
            seen_any = True
            src = _strip_ts_comments(_read(str(path)))
            for match in SNAPSHOT_READ.finditer(src):
                segs = [g for g in match.groups() if g]
                while segs and segs[-1] in _JS_METHODS:
                    segs = segs[:-1]
                if not segs:
                    continue
                reads.setdefault(".".join(segs), set()).add(path.name)
    assert seen_any, f"no snapshot consumer sources under {frontend_src}"
    return {k: sorted(v) for k, v in sorted(reads.items())}


def classify_field_reads(
    reads: dict[str, list[str]], produced: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(missing_fields, unverified_fields) — hard misses vs not-statically-provable."""
    missing: list[str] = []
    unverified: list[str] = []
    for field in sorted(reads):
        segs = field.split(".")
        top = segs[0]
        if top not in produced:
            missing.append(field)
            continue
        if len(segs) == 1:
            continue
        info = produced[top]
        if info["opaque_subs"]:
            unverified.append(field)
            continue
        if segs[1] not in info["subs"]:
            missing.append(field)
            continue
        if len(segs) >= 3:
            unverified.append(field)
    return missing, unverified


def snapshot_ts_interface_keys(frontend_src: str = FRONTEND_SRC) -> set[str]:
    """Top-level keys the console's EngineSnapshot TS contract declares."""
    path = Path(frontend_src) / "types" / "domain.ts"
    if not path.is_file():
        return set()
    src = _read(str(path))
    match = re.search(r"export interface EngineSnapshot \{(.*?)\n\}", src, re.S)
    if not match:
        return set()
    return set(re.findall(r"^\s{2}(\w+)\??:", match.group(1), re.M))


def load_ledger(path: str = LEDGER_PATH) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        return json.loads(_read(path))
    except Exception as exc:
        raise AssertionError(f"{path} is not valid JSON ({exc})") from exc


def compute_field_side_ledger() -> dict[str, Any]:
    """Fresh snapshot-coverage view (tolerant of a mid-edit server.py)."""
    try:
        produced = scan_snapshot_produced_keys()
        parse_failure = None
    except Exception as exc:
        cached = load_ledger().get("snapshot_keys_last_good") or {}
        assert cached, f"server.py snapshot builder unparseable and no cache: {exc}"
        produced = {
            k: {"subs": set(v.get("subs") or ()), "opaque_subs": bool(v.get("opaque_subs"))}
            for k, v in cached.items()
        }
        parse_failure = f"src/nexus_scalp/web/server.py: {type(exc).__name__}: {exc}"
    reads = scan_snapshot_field_reads()
    missing, unverified = classify_field_reads(reads, produced)
    return {
        "produced": produced,
        "reads": reads,
        "missing_fields": missing,
        "unverified_fields": unverified,
        "parse_failure": parse_failure,
    }


def write_field_ledger(path: str = LEDGER_PATH) -> dict[str, Any]:
    """Regenerate the snapshot-field half of the coverage ledger.

    Explicit maintenance entry point only (never called from an assertion):
      python -c "import tests.unit.test_alt_ui_snapshot_field_coverage as m; m.write_field_ledger()"
    """
    view = compute_field_side_ledger()
    ledger = load_ledger(path)
    ledger["generated_by"] = "alt-ui route-parity audit"
    ledger["missing_fields"] = view["missing_fields"]
    ledger["unverified_fields"] = view["unverified_fields"]
    declared = set(view["unverified_fields"]) | set(view["missing_fields"])
    ledger["field_sources"] = {k: v for k, v in view["reads"].items() if k in declared}
    ledger["snapshot_keys_last_good"] = {
        k: {"subs": sorted(v["subs"]), "opaque_subs": v["opaque_subs"]}
        for k, v in sorted(view["produced"].items())
    }
    ledger["snapshot_parse_failures"] = [view["parse_failure"]] if view["parse_failure"] else []
    summary = dict(ledger.get("summary") or {})
    summary.update(
        {
            "snapshot_reads": len(view["reads"]),
            "snapshot_top_level_keys": len(view["produced"]),
        }
    )
    ledger["summary"] = summary
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return ledger


# Declared limits of static derivation (see module docstring). Every entry here
# must be exactly what the live scan classifies as unverified — the ledger
# equality gate enforces that, so a stale reason cannot linger.
_UNVERIFIED_REASONS = {
    "health.details.mt5": (
        "health.details is assembled at runtime in _build_health_section; "
        "one nesting level of AST analysis cannot enumerate its keys"
    ),
    "health.subsystems.engine": (
        "health.subsystems is runtime-assembled per subsystem probe; its key set "
        "is not statically enumerable from the snapshot builder"
    ),
    "live_freshness.overall": (
        "live_freshness is delegated to LiveFreshnessService.compute_freshness "
        "(outside web/) and is None while the engine is detached"
    ),
    "model.latency_breakdown.feature_ms": (
        "latency_breakdown is engine-owned (_last_latency_breakdown); opaque to the web-layer builder"
    ),
    "model.latency_breakdown.model_forward_ms": (
        "latency_breakdown is engine-owned (_last_latency_breakdown); opaque to the web-layer builder"
    ),
}


def _unverified_params() -> list[str]:
    try:
        ledger = load_ledger()
    except AssertionError:
        return []
    if ledger:
        return sorted(ledger.get("unverified_fields") or [])
    try:
        return compute_field_side_ledger()["unverified_fields"]
    except Exception:
        return []


_UNVERIFIED_PARAMS = _unverified_params()


@pytest.fixture(scope="session", autouse=True)
def _ensure_ledger_exists() -> None:
    """Regenerate a missing ledger so this file works standalone on a fresh checkout.

    Only fires when the generated artifact is absent; an existing ledger is never
    rewritten by a test run (the writers stay explicit maintenance entry points).
    """
    if os.path.exists(LEDGER_PATH):
        return
    from tests.unit import test_alt_ui_route_parity as route_module

    route_module.write_ledger()
    write_field_ledger()


@pytest.mark.parametrize("field", _UNVERIFIED_PARAMS)
def test_unverified_snapshot_fields(field: str) -> None:
    """Explicit, ledger-backed xfail per snapshot field the scanner cannot prove.

    These are NOT defects of the console — the fields ship and render — they are
    the declared limits of static derivation, so the run is honest (xfail) rather
    than a silent pass. The xfail is conditional: if a refactor or a smarter
    scanner ever *proves* the field, the gap is closed and this test fails (the
    XPASS-equivalent), forcing ``write_field_ledger()`` to refresh the ledger.
    A new unprovable field likewise cannot appear quietly — it fails the ledger
    equality gate above.
    """
    view = compute_field_side_ledger()
    if field not in view["unverified_fields"]:
        pytest.fail(
            f"snapshot field '{field}' is now provable from the builder — the ledger gap is closed; "
            'regenerate: python -c "import tests.unit.test_alt_ui_snapshot_field_coverage as m; '
            'm.write_field_ledger()"'
        )
    pytest.xfail(
        f"ledger:coverage-gap — {_UNVERIFIED_REASONS.get(field, 'producer payload opaque to AST scan')}"
    )


class TestSnapshotProducerContract:
    """The derived key set must be real, not an artifact of a broken parse."""

    def test_builder_key_set_is_plausible(self) -> None:
        produced = scan_snapshot_produced_keys()
        assert len(produced) >= 30, f"snapshot top-level implausibly small: {sorted(produced)}"
        for key in (
            "state_version",
            "provenance",
            "account",
            "health",
            "diagnostics",
            "model",
            "probs",
        ):
            assert key in produced, f"canonical snapshot section {key} missing"
        assert "price" in produced["provenance"]["subs"]
        assert "overall" in produced["health"]["subs"]
        assert "equity" in produced["account"]["subs"]
        assert "latency_breakdown" in produced["model"]["subs"], (
            "subscript writes onto model_meta must count as produced keys"
        )

    def test_consumer_scan_finds_the_documented_reads(self) -> None:
        reads = scan_snapshot_field_reads()
        assert len(reads) >= 25, f"consumer scan implausibly small: {len(reads)}"
        for field in (
            "state_version",
            "provenance.price",
            "account.equity",
            "diagnostics.tick_age_sec",
        ):
            assert field in reads, f"scanner blind to {field}"

    def test_builder_degradation_is_disclosed_not_silent(self) -> None:
        """If server.py is mid-edit and we ran on cached keys, the ledger must say
        so; when it parses cleanly, no ghost failure may linger.
        """
        view = compute_field_side_ledger()
        live = view["parse_failure"]
        ledger = load_ledger()
        recorded = list(ledger.get("snapshot_parse_failures") or [])
        if live:
            warnings.warn(f"snapshot scan ran on cached keys: {live}", RuntimeWarning, stacklevel=2)
            assert ledger.get("snapshot_keys_last_good"), "fallback cache missing"
        else:
            assert recorded == [], (
                "ledger records a snapshot builder failure the live scan no longer sees — regenerate: "
                'python -c "import tests.unit.test_alt_ui_snapshot_field_coverage as m; m.write_field_ledger()"'
            )
            assert ledger.get("snapshot_keys_last_good"), "cache must exist for the fallback path"


class TestSnapshotFieldCoverage:
    """HARD gate: every consumed dotted field must exist in the builder output."""

    def test_consumed_fields_exist_in_builder(self) -> None:
        view = compute_field_side_ledger()
        offenders = view["missing_fields"]
        assert not offenders, (
            f"console reads snapshot fields the builder never produces: {offenders}"
        )

    def test_ledger_field_lists_match_scan(self) -> None:
        view = compute_field_side_ledger()
        ledger = load_ledger()
        assert view["missing_fields"] == sorted(ledger.get("missing_fields") or []), (
            "ledger missing_fields stale vs live scan — regenerate with write_field_ledger()"
        )
        assert view["unverified_fields"] == sorted(ledger.get("unverified_fields") or []), (
            "ledger unverified_fields stale vs live scan — regenerate with write_field_ledger()"
        )

    def test_frontend_ts_interface_tracks_builder(self) -> None:
        """The TS EngineSnapshot contract must not drift from the builder output."""
        produced = scan_snapshot_produced_keys()
        ts_keys = snapshot_ts_interface_keys()
        assert ts_keys, "EngineSnapshot interface not found in frontend/src/types/domain.ts"
        ghost = sorted(ts_keys - set(produced))
        assert not ghost, f"frontend TS declares snapshot keys the backend never emits: {ghost}"

    def test_gate_would_catch_a_phantom_field(self) -> None:
        """Synthetic offenders prove the hard assert is wired, not vacuous."""
        produced = scan_snapshot_produced_keys()
        reads = {
            "provenance.price": ["Ok.tsx"],
            "provenance.ghost": ["Bad.tsx"],
            "not_a_section.anything": ["Bad.tsx"],
        }
        missing, unverified = classify_field_reads(reads, produced)
        assert "provenance.ghost" in missing
        assert "not_a_section.anything" in missing
        assert "provenance.price" not in missing
        assert "provenance.price" not in unverified
        assert not any("ghost" in u for u in unverified), "hard miss must never demote to xfail"
