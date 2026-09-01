# AGENT HANDOFF — pyproj sync tooling (PowerShell port)

==================================================
AGENT HANDOFF
==================================================
Agent: Nexus-Main
Role: Orchestrator / IDE tooling maintenance
Task: scripts/sync_pyproj.ps1 — durable in-repo regeneration of
NexusTradingForexBot.pyproj (user directive: "make a ps1 that does exactly
what you did, no duplicates, no python script, not different").
TASK-ID: TASK-PYPROJ-SYNC
Starting HEAD: f84dd77
Ending HEAD: (this commit, local only)
Branch: main
Commits: see commit body (NOT pushed — explicit user directive)

Files Changed:
- scripts/sync_pyproj.ps1 (NEW — the tool)
- NexusTradingForexBot.pyproj (regenerated current: agent-landed files since
  f0f391f picked up — event_source.py, provider_gate.py,
  debug_research_routes.py, tests/installer/*, installer/install.ps1,
  docs/INSTALL_*.md)
- agents/taskboard.md (TASK-PYPROJ-SYNC row)

Functions / Classes Changed: none (MSBuild inventory + tooling only)

Shared Functions: none touched

Contracts Changed: none

Invariants: none touched

Tests Added: none (tool self-verifies: XML parse, duplicate check,
on-disk existence check, idempotence check — see Verification)

Tests Run / Runtime Verification:
- BYTE-IDENTICAL proof: script output compared with `cmp` against the
  proven Python generator output on the same tree (post-a412c27: 864
  includes / 907 lines / 57,405 bytes) — 0 differing bytes.
- Idempotence: immediate rerun -> "ALREADY in sync", exit 0, no write.
- -VerifyOnly: reports out-of-sync without writing (exit 4) / in-sync (0).
- Windows PowerShell 5.1 (the VS-bundled engine): full + VerifyOnly runs OK.
- Live catch: rerunning picked up 3 src files + installer docs + tests
  landed by parallel agents since f0f391f (9 insertions) — proof the tool
  reflects the real tracked tree.

Runtime Verification: N/A (no runtime code touched)

Bugs Fixed: none (tooling)

Bugs Discovered: none

Risks:
- Scope is CURATED by design (scratch/, artifacts/, pics/,
  docs/forensic-docs, Web/vendor excluded). Do not "fix" the filter to
  full-tree: a full-tree pyproj (1818 entries) crashed Visual Studio on
  load — that is why this tool exists in this shape.
- Sort fidelity: the script emulates Python sorted(key=str.lower) via a
  composite key (lowerpath + 0x01 + original ordinal index). If the sort
  block is "simplified" to Sort-Object, byte-equality with the historical
  inventory breaks (ordering churn in diffs).

Unfinished Work: none

BLOCKERS: none

EXACT NEXT-AGENT INSTRUCTIONS:
1. Run `powershell -NoProfile -ExecutionPolicy Bypass -File
   scripts\sync_pyproj.ps1` whenever tracked files are added/removed;
   exit 0 = in sync, exit 1 = rewritten (commit it), exit 3 = verification
   failure (fix tree, not the tool), exit 4 = -VerifyOnly out-of-sync.
2. Commit the rewritten pyproj alone with a `<AGENT>: <summary>` message
   that names the tool.
3. Never hand-append entries to the pyproj; regenerate.
==================================================
