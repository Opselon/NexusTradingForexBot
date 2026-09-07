# ABSORPTION DISCLOSURE — commit 3f8f6206 (2026-09-07 08:13:48, Hermes-NewsMission/FinalAudit)

**Type:** ABSORPTION (parallel-agent contract §5)

Intended scope: `src/nexus_scalp/application/live/maintenance.py` (calendar
worker composition) + `tests/unit/test_calendar_maintenance_wiring.py` (new).

Foreign WIP swept in between `git add` and `git commit` (another agent's
walk-forward expanding-mode work, in flight in the shared tree):

    src/nexus_scalp/training/walk_forward_trainer.py   (walk_forward_mode="blocked|expanding" plumbed)
    tests/unit/test_wft_expanding_mode.py              (NEW)

Content verified: the trainer diff is additive config plumbing with purge/
embargo semantics unchanged ("blocked" default preserves existing geometry);
no content was altered by me. The owning agent should treat 3f8f6206 as the
CARRIER of the expanding-mode plumbing and run their gates at HEAD.

Third absorption in this session — the shared-index race is systemic while
multiple agents commit concurrently. `git diff --cached --name-only` was run
immediately before commit and the files were NOT staged at that moment; they
were staged by the parallel agent in the seconds between that check and the
commit.
