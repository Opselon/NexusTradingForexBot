# Comment Style Guide — Nexus Scalp Engine

> Applies to all Python source in `src/`, `tests/`, and `scripts/`.
> Repo rule of thumb: **the repository should not contain more comments — it should contain better comments.**

## The one test

> Could a competent developer derive this from the code itself?

If YES → do not comment. If NO → the comment may be valuable; then ask:
"Will this still be true and useful six months from now?" If NO → do not write it.

## What comments are FOR

A comment must be one of:

| Category | Documents | Example |
|---|---|---|
| INVARIANT | a rule the code must keep but does not enforce visibly | "Resume-safe hash: the hasher must cover the already-downloaded bytes too." |
| SAFETY | fail-closed / fail-safe semantics | "Fail closed: no inference must never yield a trade decision." |
| CONCURRENCY | lock ownership, ordering, atomicity | "Hold the bundle lock across tmp-write and replace." |
| PROTOCOL | external protocol subtleties | "206 without a verifiable Content-Range start is ambiguous → treat as full body." |
| MODEL_CONTRACT | dimensions, ordering, dtype, device assumptions | see `features/schema_contract.py` (canonical 70D layout) |
| TRADING_SAFETY | broker ambiguity, idempotency | "A non-DONE retcode does NOT prove rejection; never blind-retry a market order." |
| STATE_MACHINE | illegal/terminal/special transitions | "Emergency transitions bypass the debounce (handled above)." |
| RELEASE_CONSTRAINT | ordering chicken-and-eggs in packaging | "Full checksums are generated after ISCC because they include setup.exe." |
| COMPATIBILITY | a shim that must remain until a condition | "Remove after all supported artifacts are migrated." |

## What NEVER becomes a comment

- Narration: `# Initialize x`, `# Increment count`, `# Return result`, `# Check if ...`.
  (The code below already says exactly that.)
- Ticket storage: bare `# BUG-170` / `# FIXED` / `# TEMP FIX` lines.
  A BUG-NNN reference is allowed only when the bug **explains a live constraint**;
  prefer writing the invariant, optionally with the id: "keep O_CREAT|O_EXCL
  claim so two concurrent starts cannot both own the pidfile".
- Historical storytelling: phases, old file paths, deleted code, "we used to".
- Duplicates of README / docstring / registry content in the same file.
- Generic labels: `# Optimize performance`, `# Build installer`, `# Section X`.

## Form

- Short: one idea, ideally 1–3 lines. No essays inline.
- Say the constraint, not the mechanism restated.
- Docstrings for API contracts (purpose/inputs/side effects/invariants);
  inline comments for local rationale, ordering, and invariants.
- Do not document obvious private one-line helpers.
- `datetime.now()` vs UTC, market-session quirks, and host-local gates get an
  explicit comment AT the gate (they are the most commonly "fixed by mistake" lines).

## Stale-comment rule

A stale comment is worse than no comment. When touching code, verify adjacent
comments still hold: referenced files/functions exist, referenced bug numbers
mean what the comment claims, "Requirement N"-style tags map to a live contract.
If not: repair the comment to the current truth, or delete it.

## TODO / FIXME

Must be actionable: what, why, and ideally the condition that unblocks it.
`# TODO: fix this` gets deleted or rewritten by any agent that touches the file.

## Machine check

No separate NLP tooling — the review gates (ruff/mypy/tests) plus this guide
are the enforcement. Agents doing bulk edits MUST byte-preserve CRLF/LF per
file (several tracked files are CRLF; `strategies/__init__.py`-style LF files
exist inside an autocrlf tree — never bulk-convert).
