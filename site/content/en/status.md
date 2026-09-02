---
title: Project status
description: Truthful, evidence-graded status of every major capability — no marketing.
lang: en
---

Status labels are **evidence-graded**: *certified* requires formal forensic
acceptance with reproducible artifacts; *implemented* means in code with
regression coverage; *experimental* means explicitly off the live path.

| Dimension | Status | Evidence |
| :--- | :--- | :--- |
| Version | **v9.0.6** (single source `pyproject.toml`) | release pipeline stamps every artifact |
| Release | Published (v9.0.0 → v9.0.6; SHA-256, manifests, SBOM) | GitHub Releases |
| Live execution (MT5) | Working, account-identity fail-safe | BUG-142 suite |
| 50D live contract (`scalp_v1`) | **ACTIVE** | `features/schema.py` |
| 70D contract (`scalp_v3`) | Candidate-only | `features/schema_contract.py` |
| 70D evidence | **Negative / inconclusive (OOS NOT_ELIGIBLE)** | TASK-05/TASK-09 reports |
| Research loop | Working end-to-end | research/ suites |
| CI | ruff · mypy · ~779-test critical suite · CodeQL · Trivy | workflows |
| Champion governance | RESTORED_CANDIDATE — operator decision pending | MODEL_GOVERNANCE v2 |

## Capability highlights

| Capability | Status |
| :--- | :--- |
| Causal 50D features · risk clamps · shadow runtime · OOS gate · installer | ✅ Certified |
| Model factory · execution router · accounting · incidents · Control Center | 🟢 Implemented |
| 70D series · news gate · temporal · MSLIE | 🟡 Experimental / 🔵 Research |
| Multi-broker | 📌 Planned |

Full matrix: [capabilities.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/capabilities.md)
· known limitations: [status.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/status.md)
· public forensic bug ledger: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
