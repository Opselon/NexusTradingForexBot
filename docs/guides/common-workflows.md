---
title: Common Workflows
description: Everyday operating recipes — run, verify, research a candidate, read evidence, update.
lang: en
---

# Common Workflows

## 1. Daily development loop

```bash
git pull && .venv/Scripts/Activate.ps1        # Windows dev
nexus test --mode quick                        # fast sanity
# ... edit ...
pytest tests/unit -q -k "70 or schema"         # targeted suite
./beforePush.ps1 -SkipPush                     # full gate before pushing
```

## 2. Evaluate the engine on real data (risk-free)

```bash
nexus start --mode shadow      # live feed, zero order authority
# observe Control Center → SHADOW section: disagreement analysis, drift, health
nexus stop
```

## 3. Research a candidate

```bash
nexus model-dataset-build      # canonical dataset (fingerprinted)
nexus model-experiment-create  # fair A/B/C protocol
nexus model-train              # deterministic, seeded
nexus model-validate           # walk-forward + OOS gate + robustness
nexus model-replay             # bit-exact parity proof
```

Anything that fails the OOS gate is REJECTED — that is the system working.

## 4. Read the evidence

- `nexus forensic` — health engine verdicts
- `nexus incidents list` — correlated incidents
- Control Center → Debug Hub — `/api/debug/state` snapshot (18 sections)
- `artifacts/forensics/`, `artifacts/validation/` — machine-readable evidence

## 5. Update the installation

```bash
nexus update check → download → install → verify
nexus rollback                 # if a release misbehaves
```

## 6. Database care

```bash
nexus db hygiene status        # what would be cleaned
nexus db hygiene run --dry-run --deep
nexus db migrate               # additive migrations
```

## 7. Docker

```bash
docker compose up -d --build   # PAPER mode; :9090; /health readiness
```
