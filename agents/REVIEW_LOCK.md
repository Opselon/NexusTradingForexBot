# NSE REVIEW WORKTREE — LOCKED (read-only review plane)

## Purpose
`C:/Users/Capsizer/source/repos/nse-review-main` is the REVIEW console:
it exists so a human can open the latest `origin/main` in Visual Studio and
the browser, observe, and test. It is NOT a development space.

## HARD RULES (a violation is a defect, report it)

1. **NO agent may commit to this worktree.** `git add` / `git commit` /
   `git switch` / `git checkout <any-branch>` inside this tree is FORBIDDEN
   for every agent except the owner's own `update_review.sh` sync.
2. **NO agent may push from this worktree.**
3. **NO agent may rebase / reset --hard / git clean this worktree.**
   The tree is refreshed only by `update_review.sh` (a fast-forward +
   `reset --hard origin/main` done by the owner's own script, and only when
   the engine is stopped).
4. **No agent may run a test-write / DB-write / artifact-write here.**
   Tests are RUN read-only; nothing writes results into the tree.
5. **The branch checked out here is `review/main` — a tracking ref, not
   `main` itself.** `main` stays a free local mirror per git_governance §4.

## How the lock is ENFORCED (each layer is real, not prose)

| Layer | Mechanism | Status |
|---|---|---|
| L1 | `review/main` is checked out in exactly ONE worktree → the preflight `WORKTREE` check (§8) blocks any agent that tries to check it out a second time | ACTIVE |
| L2 | `agents/locks.yaml` exclusive lock on the worktree path + branch | ACTIVE |
| L3 | The engine launched from this tree runs READ-ONLY: `NSE_REVIEW_MODE=1` prevents any in-tree write | ACTIVE |
| L4 | Any agent's preflight that touches this path must pass `--offline` | DOCUMENTED |

## What IS allowed in this worktree

- `git fetch origin --prune` (read-only, no mutation)
- `update_review.sh` (the owner's sync script, engine stopped first)
- running the app read-only: `python NexusTradingForexBot.py` (read-only)
- `npm run build` inside `frontend/` — the build WRITES to `frontend/dist`,
  which is gitignored, so the tree never becomes dirty
- reading any file, running pytest read-only

## Where development actually happens

Development happens in **dedicated task worktrees** like the BUG-RT008 one:

```bash
git worktree add --detach C:/.../nse-task-<id> origin/main
```

That is the only place an agent's `git add` / `git commit` / `git push`
belongs. This worktree is for OBSERVATION and REVIEW, never for work.

## Recovery (if the lock is ever bypassed)

If an agent wrote to this worktree by mistake:

1. `git -C C:/Users/Capsizer/source/repos/nse-review-main status` — see what
   actually changed. NEVER assume, never `git clean`.
2. If the change is committed: `git reflog` to find the previous HEAD, then
   restore `review/main` with `git branch -f review/main <prev>` from the
   MAIN repo (never `reset --hard` inside the review tree).
3. If the change is uncommitted and foreign: STOP. Ask the owner. PRESERVE
   the work — copy it to a scratch dir, then `git checkout -- <file>` only
   after it is safely copied.
