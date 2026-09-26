"""PR Evidence Reporter — file-level failure intelligence for any pull request.

WHERE/WHY: answers "exactly where did this PR fail, which file, which line,
which function, which test, which CI job, and what proves it?" directly inside
the PR, for ANY pull request in this repository. Evidence is collected from
GitHub check runs + annotations, CI-run summaries, and optional local test
runs, normalized into structured :class:`~nexus_scalp.pr_evidence.models.Failure`
records, then rendered into a single upsertable PR comment.

BOUNDARY: generic only. Nothing in this package may reference a specific PR
number, check name, file path, test name, or failure pattern. The reporter
discovers all of that at runtime from the GitHub API and repo state.

USED BY: cli/pr_commands.py (``nse pr report``), tests/unit/test_pr_evidence*.py.

DO-NOT-PUT-HERE: CLI presentation (cli/pr_commands.py), a specific PR's data.
"""

from __future__ import annotations

__all__: list[str] = []
