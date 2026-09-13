# Governance (bounded context)

Model governance: champion/candidate promotion ladder, decision ledger
(governance events), registry reconciliation, calibration/drift review,
emergency freeze/unfreeze/disable, experience-intelligence self-heal.

- Invariants enforced by the backend (frozen gates, approval tokens,
  actor-required commands); the UI mirrors verdicts and can never bypass a
  gate. Every command is confirm-guarded with the actor field visible.
- Approve->execute promotion deliberately NOT one-click here: execute needs
  the approval_token minted by the approve transition (documented inline).
- `{available:false}` from the governance engine renders as empty state with
  the backend reason, never as an inferred "no issues" state.
- Evidence: model_governance_routes.py, debug_research_routes.py
  (/api/experience/*), Web/app.js tab-governance.
