# DECISIONS — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §39 (see `agents/multi-agent-git-contract.md`).
> Important architectural choices receive a decision record DEC-XXXX.
> Required: Decision, Context, Evidence, Alternatives, Chosen path, Why, Consequences.
> This prevents future agents from accidentally reversing established safety decisions.

## DEC-0001 — UNKNOWN broker exit reason remains UNKNOWN
- **Decision:** When broker exit evidence is incomplete/ambiguous, classification stays UNKNOWN rather than being promoted to MANUAL_CLOSE or another confident class.
- **Context:** BUG-081 — exit classifier produced falsehoods by assuming MANUAL under incomplete evidence; ledger lost money twice.
- **Evidence:** agents/bugs.md BUG-081; exit-classification forensics.
- **Alternatives:** (a) default to MANUAL_CLOSE when reason missing; (b) drop the trade from accounting.
- **Chosen path:** UNKNOWN evidence stays UNKNOWN (INV-012).
- **Why:** silent promotion corrupts learning outcomes and accounting lineage.
- **Consequences:** EXIT_CLASSIFICATION contract v2 (evidence precedence); downstream consumers must handle UNKNOWN.

## Registry notes
- New decisions: append DEC-XXXX entries; reference BUG-NNN / CHANGE-ID / TASK-ID where applicable.
