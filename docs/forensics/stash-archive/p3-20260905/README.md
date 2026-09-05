# P3 Untracked-Tree Archive — 2026-09-05

Five ordinary stashes contained a third parent (untracked files). Their `refs/forensic/stash-backup/*` objects are immutable and remain the recovery authority (91/91 intact). This directory preserves a verified file-level inventory per stash so the original stash can be pruned safely.

For each `stash@{idx}` the `P3_FILE_LIST.txt` was generated via:
```
git ls-tree -r --name-only <p3>
```
and `P3_META.txt` records stash message, stash object, p3 commit, parents.

The full p3 tar can be re-materialized at any time via:
```
git archive --format=tar <p3> -o /tmp/p3-<stash>.tar
# or
git show <p3>:<path> > /tmp/<path>
```
because the p3 objects remain reachable via both `refs/stash` ancestry and `refs/forensic/stash-backup/*` (91 verified).

Recovery:
```
# restore a single file from a pruned stash after pruning:
git show <p3>:scratch/ns_agent18_final5k.py > /tmp/restored.py
# or restore entire stash object (still reachable):
git show stash@{<orig-idx-after-pruning-shift>}:<path>
# canonical recovery is always the forensic backup ref:
git show refs/forensic/stash-backup/<orig-idx>:<path>
```

Stashes covered: 16, 34, 37, 40, 54 (current indices at time of archival, pre-pruning 85-stash state).
