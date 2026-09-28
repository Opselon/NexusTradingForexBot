"""Phase 2J — database constraint audit (read-only).

For every important lifecycle relationship, determine WHERE the integrity is
enforced:

    application | database | both | neither

Output: phase2/phase2_constraint_audit.{json,md}

The audit NEVER modifies a schema. Where a critical invariant has no
enforcement the finding is recorded with the exact relationship; a follow-up
item is opened, not a migration.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MAIN_CHECKOUT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")
LIVE_AUDIT_DB = MAIN_CHECKOUT / "artifacts" / "audit.db"
OUT_DIR = Path(__file__).resolve().parents[3] / "phase2"


# ---------------------------------------------------------------------------
# The relationships the lifecycle depends on. Each is stated as the invariant
# a downstream consumer relies on, with the evidence used to classify it.
# ---------------------------------------------------------------------------
RELATIONSHIPS = [
    {
        "name": "research_gates.research_run_id -> research_runs.run_id",
        "stage": "VALIDATION",
        "invariant": "every gate belongs to a research run that exists",
        "child": ("research_gates", "research_run_id"),
        "parent": ("research_runs", "run_id"),
        "importance": "critical",
        "guard": "a gate verdict for a run that does not exist is fabricated evidence",
    },
    {
        "name": "research_evidence.gate_id -> research_gates.gate_id",
        "stage": "VALIDATION",
        "invariant": "every evidence artifact belongs to a gate (or the run, when gate_id is NULL)",
        "child": ("research_evidence", "gate_id"),
        "parent": ("research_gates", "gate_id"),
        "importance": "critical",
        "note": "gate_id is nullable by design — evidence may be per-run",
        "guard": "evidence attributed to a gate that does not exist is forgery",
    },
    {
        "name": "shadow_decisions.run_id -> shadow_runs.run_id",
        "stage": "REPLAY",
        "invariant": "every shadow decision belongs to a shadow run",
        "child": ("shadow_decisions", "run_id"),
        "parent": ("shadow_runs", "run_id"),
        "importance": "critical",
        "guard": "replay statistics would count decisions from a run that never happened",
    },
    {
        "name": "shadow_comparisons.run_id -> shadow_runs.run_id",
        "stage": "COUNTERFACTUAL",
        "invariant": "every comparison belongs to a shadow run",
        "child": ("shadow_comparisons", "run_id"),
        "parent": ("shadow_runs", "run_id"),
        "importance": "critical",
        "guard": "a promotion decision read from a comparison with no run has no basis",
    },
    {
        "name": "training_runs.model_id -> experience_model_registry.model_id",
        "stage": "MODEL",
        "invariant": "every completed training run produces a model the registry knows",
        "child": ("training_runs", "model_id"),
        "parent": ("experience_model_registry", "model_id"),
        "importance": "critical",
        "note": "model_id is nullable in training_runs; the registry is keyed on "
        "(model_id, model_version). LIVE EVIDENCE: all 6 completed training "
        "runs persist model_id = '' (empty string, not NULL) while the "
        "registry holds 6 distinct model ids — the training stage completes "
        "without recording which model it produced, so no training run can be "
        "joined to its model. PRODUCTION DEFECT (owned by Agent 1's runtime "
        "scope; reported, not patched).",
        "guard": "a model trained but never linked cannot be promoted or rolled back",
        "nullable_by_design": False,
        "live_empty_model_id": 6,
    },
    {
        "name": "research_runs.strategy_id -> strategy_registry.strategy_id",
        "stage": "STRATEGY",
        "invariant": "every research run validates a registered strategy",
        "child": ("research_runs", "strategy_id"),
        "parent": ("strategy_registry", "strategy_id"),
        "importance": "critical",
        "guard": "a run's verdict attached to no strategy cannot be acted on",
    },
    {
        "name": "model_governance_events.model_id -> model_governance_state.model_id",
        "stage": "PROMOTION",
        "invariant": "every MODEL-scoped governance event describes a model the state table tracks",
        "child": ("model_governance_events", "model_id"),
        "parent": ("model_governance_state", "model_id"),
        "importance": "high",
        "note": "model_id is nullable in both — an event may be system-scoped "
        "(REGISTRY_RECONCILED / FEATURE_PARITY_FAILURE fire without a model). "
        "The probe counts events whose model_id does not appear in the state "
        "table, which is EXPECTED for system-scoped events and only a defect "
        "for events that claim a specific model.",
        "guard": "an audit trail for an unknown model is unverifiable",
        "nullable_by_design": True,
    },
]


def _db_schema(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = {
            name: conn.execute(f"PRAGMA table_info({name})").fetchall()
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        fks = {t: conn.execute(f"PRAGMA foreign_key_list({t})").fetchall() for t in tables}
        uniques = {
            t: [i for i in conn.execute(f"PRAGMA index_list({t})").fetchall() if i[2] == 2]
            for t in tables
        }
        # UNIQUE / CHECK inside the CREATE statement text
        ddl = {
            t: (
                conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone()
                or ("",)
            )[0]
            for t in tables
        }
        return {
            "tables": {
                t: {
                    "columns": [c[1] for c in cols],
                    "notnull": [c[1] for c in cols if c[3]],
                    "pk": [c[1] for c in cols if c[5]],
                }
                for t, cols in tables.items()
            },
            "foreign_keys": fks,
            "unique_indexes": uniques,
            "ddl": ddl,
        }
    finally:
        conn.close()


def _live_violations(db: Path, child: tuple[str, str], parent: tuple[str, str]) -> int:
    """Count live rows that break the invariant (orphans)."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return conn.execute(
                f"SELECT COUNT(*) FROM {child[0]} c WHERE c.{child[1]} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {parent[0]} p WHERE p.{parent[1]} = c.{child[1]})"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return -1


def classify(rel: dict, schema: dict, violations: int) -> dict:
    """Decide where the invariant is enforced, and whether an orphan count is
    a defect or an expected consequence of a nullable-by-design link."""
    child_tbl, child_col = rel["child"]
    parent_tbl, parent_col = rel["parent"]
    tables = schema["tables"]

    if child_tbl not in tables or parent_tbl not in tables:
        return {
            "enforcement": "unknown",
            "orphan_class": "UNKNOWN",
            "evidence": f"one of {child_tbl}/{parent_tbl} is absent from the schema",
        }

    fk_list = schema["foreign_keys"].get(child_tbl, [])
    has_fk = any(
        fk[2] == parent_tbl and fk[3] == child_col and fk[4] == parent_col for fk in fk_list
    )
    child_ddl = schema["ddl"].get(child_tbl, "")
    ddl_declares = (
        f"REFERENCES {parent_tbl}" in child_ddl.upper()
        or f"REFERENCES {parent_tbl}({parent_col})" in child_ddl.upper()
    )
    not_null = child_col in tables[child_tbl]["notnull"]
    nullable_by_design = bool(rel.get("nullable_by_design"))

    if has_fk:
        enforcement = "database"
        evidence = f"FOREIGN KEY ({child_col}) REFERENCES {parent_tbl}({parent_col})"
        orphan_class = "EXPECTED" if violations <= 0 else "INCORRECT"
    elif ddl_declares:
        enforcement = "neither"
        evidence = (
            f"{child_tbl} DDL names {parent_tbl} but SQLite ignores a bare "
            "REFERENCES outside a FOREIGN KEY clause — not enforced"
        )
        orphan_class = _orphan_class(violations, nullable_by_design)
    elif violations == 0:
        enforcement = "application"
        evidence = (
            "no DB constraint; 0 orphan rows in the live store, so the "
            "application currently holds the invariant"
        )
        orphan_class = "EXPECTED"
    elif violations < 0:
        enforcement = "unknown"
        evidence = "the orphan probe could not run against the live schema"
        orphan_class = "UNKNOWN"
    else:
        enforcement = "neither"
        evidence = (
            f"no DB constraint AND {violations} orphan rows live: the invariant "
            "is asserted by nothing"
        )
        orphan_class = "EXPECTED" if nullable_by_design else "INCORRECT"

    return {
        "enforcement": enforcement,
        "orphan_class": orphan_class,
        "child_column_not_null": not_null,
        "live_orphan_rows": violations,
        "evidence": evidence,
        "note": rel.get("note", ""),
    }


def _orphan_class(violations: int, nullable_by_design: bool) -> str:
    if violations <= 0:
        return "EXPECTED"
    return "EXPECTED" if nullable_by_design else "INCORRECT"


def audit_uniqueness(schema: dict) -> list[dict]:
    """The identity columns that must be unique, and whether the DB says so."""
    out = []
    for table, cols in {
        "research_runs": "run_id",
        "research_gates": "gate_id",
        "research_evidence": "evidence_id",
        "shadow_runs": "run_id",
        "shadow_decisions": "shadow_decision_id",
        "experience_model_registry": "model_id",
        "training_runs": "run_id",
        "strategy_registry": "strategy_id",
        "model_governance_events": "event_id",
        "model_promotion_audit": "promotion_id",
    }.items():
        if table not in schema["tables"]:
            out.append({"table": table, "column": cols, "unique": "table-absent"})
            continue
        ddl = schema["ddl"].get(table, "").upper()
        unique_index = bool(schema["unique_indexes"].get(table))
        declared = f"{cols} TEXT UNIQUE" in ddl or f"{cols} TEXT NOT NULL UNIQUE" in ddl
        out.append(
            {
                "table": table,
                "column": cols,
                "unique": "yes" if (declared or unique_index) else "no",
                "evidence": (
                    "UNIQUE in the CREATE statement"
                    if declared
                    else "a UNIQUE index exists"
                    if unique_index
                    else "no UNIQUE constraint or index on the identity column"
                ),
            }
        )
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    schema = _db_schema(LIVE_AUDIT_DB)

    relationships = []
    for rel in RELATIONSHIPS:
        violations = _live_violations(LIVE_AUDIT_DB, rel["child"], rel["parent"])
        result = {**rel, **classify(rel, schema, violations)}
        relationships.append(result)

    uniqueness = audit_uniqueness(schema)

    fk_total = sum(len(v) for v in schema["foreign_keys"].values())
    uq_total = sum(len(v) for v in schema["unique_indexes"].values())
    check_total = sum(1 for ddl in schema["ddl"].values() if ddl and "CHECK" in ddl.upper())
    summary = {
        "database": str(LIVE_AUDIT_DB),
        "tables": len(schema["tables"]),
        "foreign_keys": fk_total,
        "unique_indexes": uq_total,
        "check_constraints": check_total,
        "enforcement_counts": {
            e: sum(1 for r in relationships if r["enforcement"] == e)
            for e in ("database", "application", "both", "neither", "unknown")
        },
    }

    payload = {
        "summary": summary,
        "relationships": relationships,
        "uniqueness": uniqueness,
    }
    (OUT_DIR / "phase2_constraint_audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )

    lines = ["# Phase 2J — Database Constraint Audit", ""]
    lines.append(f"Database: `{LIVE_AUDIT_DB}`")
    lines.append(
        f"Tables: {summary['tables']} | "
        f"foreign keys: {summary['foreign_keys']} | "
        f"unique indexes: {summary['unique_indexes']} | "
        f"CHECK constraints: {summary['check_constraints']}"
    )
    lines.append("")
    lines.append("## Enforcement summary")
    lines.append("")
    lines.append("| Enforcement | Relationships |")
    lines.append("| --- | --- |")
    for k, v in summary["enforcement_counts"].items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Relationships")
    lines.append("")
    lines.append("| Relationship | Stage | Enforcement | Child NOT NULL | Live orphans | Class |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in relationships:
        lines.append(
            f"| {r['name']} | {r['stage']} | **{r['enforcement']}** | "
            f"{'yes' if r['child_column_not_null'] else 'no'} | "
            f"{r['live_orphan_rows']} | {r['orphan_class']} |"
        )
    lines.append("")
    for r in relationships:
        lines.append(f"### {r['name']}")
        lines.append("")
        lines.append(f"- stage: **{r['stage']}**  importance: **{r['importance']}**")
        lines.append(f"- invariant: {r['invariant']}")
        lines.append(f"- enforcement: **{r['enforcement']}**")
        lines.append(f"- evidence: {r['evidence']}")
        if r.get("note"):
            lines.append(f"- note: {r['note']}")
        lines.append(f"- guard: {r['guard']}")
        lines.append("")
    lines.append("## Uniqueness of identity columns")
    lines.append("")
    lines.append("| Table | Column | Unique | Evidence |")
    lines.append("| --- | --- | --- | --- |")
    for u in uniqueness:
        lines.append(f"| {u['table']} | {u['column']} | {u['unique']} | {u['evidence']} |")
    lines.append("")

    (OUT_DIR / "phase2_constraint_audit.md").write_text("\n".join(lines))
    print(
        f"constraint audit written -> {OUT_DIR.name} "
        f"(relationships={len(relationships)}, "
        f"enforcement={summary['enforcement_counts']})"
    )


if __name__ == "__main__":
    main()
