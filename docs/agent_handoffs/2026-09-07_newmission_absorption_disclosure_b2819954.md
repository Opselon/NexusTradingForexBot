# ABSORPTION DISCLOSURE — commit b2819954 (2026-09-07, Hermes-NewsMission)

**Owner:** Hermes-NewsMission
**Type:** ABSORPTION (parallel-agent contract disclosure)

Intended scope of my commit: `src/nexus_scalp/news/pro_auto.py` ONLY
(deterministic high-impact LLM-skip, P0 5A/5B).

Foreign WIP swept into the same commit between my `git add` and `git commit`
(another agent's Telegram command-surface work, in flight in the shared tree):

    src/nexus_scalp/application/command_intent.py        (NEW, 193L)
    src/nexus_scalp/application/live/maintenance.py      (+24L)
    src/nexus_scalp/observability/tg_command_bus.py      (NEW, 273L)
    src/nexus_scalp/reporting/operational_digest.py      (NEW)
    tests/unit/test_telegram_control_surface.py          (NEW)

My pro_auto.py diff matches the commit message exactly (+39L: force_local
plumbing). The foreign files were NOT modified by me and should be treated as
carried by this commit; the owning agent should verify their gates at HEAD.
