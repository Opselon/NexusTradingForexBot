# PARTIAL ABSORPTION NOTE — commit 248507e9 carried TASK-MARKET-CONTEXT seed triage

**Owner:** Hermes-NewsMission (market-context mission)
**Context:** while I was preparing my source-triage commit, a parallel agent's
commit `248507e9` ("style(governance): flatten nested min in feature_drift")
landed carrying `src/nexus_scalp/news/seed.py` WITH my market-context source
triage changes (SEED_VERSION 2026-09-07-v3, bls/bea disabled, reuters disabled
+ cnbc_business + fxstreet replacements, treasury JSON manifest entry).

This is the reverse of the usual absorption: MY staged-then-reverted file was
swept into a foreign commit (the shared working tree again). Content verified:
the seed.py diff in 248507e9 matches my intended changes exactly (57 added
lines — all seed triage; no other seed semantics).

The companion `JSONManifestSourceAdapter` (base.py) that the seed row depends
on landed separately in my `15971845` — at HEAD the pair is complete and
verified live (Treasury shard fetch 200/15 items).

No action needed by the 248507e9 owner; no content was altered.
