# tests/unit/test_post70d_monitoring_activation.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-12 POST-70D monitoring activation — TEST-POST70D-01..28: canonical deploy gate, UNKNOWN discipline, fail-safe, news 200-but-wrong classification, experience-gap forensics, liquidity frozen references, governance/champion identity.
- Gate: engine actually invoked and evidence persisted; CRITICAL blocks; UNKNOWN NEVER passes (`test_unknown_review` — policy-map UNKNOWN → review); WARNING allows with warning; DEGRADED → review; pure PASS allows.
- Engine failure: engine crash → BLOCK (fail-safe); snapshot immutability enforced.
- News 200-but-wrong classification detected (HTTP ok but content wrong); experience-gap forensics asserted; liquidity-frozen references flagged.
- Champion/identity truthfulness: dashboard reads from `_FakeEngine.snapshot` + real AuditRepository evidence rows.
- 79 defs / 930 lines; `_mkdb`/`_snapshot` fixtures with queued-writer flush.