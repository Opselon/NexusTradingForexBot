# Phase 2J — Database Constraint Audit

Database: `C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db`
Tables: 74 | foreign keys: 0 | unique indexes: 0 | CHECK constraints: 8

## Enforcement summary

| Enforcement | Relationships |
| --- | --- |
| database | 0 |
| application | 5 |
| both | 0 |
| neither | 2 |
| unknown | 0 |

## Relationships

| Relationship | Stage | Enforcement | Child NOT NULL | Live orphans | Class |
| --- | --- | --- | --- | --- | --- |
| research_gates.research_run_id -> research_runs.run_id | VALIDATION | **application** | yes | 0 | EXPECTED |
| research_evidence.gate_id -> research_gates.gate_id | VALIDATION | **application** | no | 0 | EXPECTED |
| shadow_decisions.run_id -> shadow_runs.run_id | REPLAY | **application** | yes | 0 | EXPECTED |
| shadow_comparisons.run_id -> shadow_runs.run_id | COUNTERFACTUAL | **application** | yes | 0 | EXPECTED |
| training_runs.model_id -> experience_model_registry.model_id | MODEL | **neither** | no | 6 | INCORRECT |
| research_runs.strategy_id -> strategy_registry.strategy_id | STRATEGY | **application** | yes | 0 | EXPECTED |
| model_governance_events.model_id -> model_governance_state.model_id | PROMOTION | **neither** | no | 59164 | EXPECTED |

### research_gates.research_run_id -> research_runs.run_id

- stage: **VALIDATION**  importance: **critical**
- invariant: every gate belongs to a research run that exists
- enforcement: **application**
- evidence: no DB constraint; 0 orphan rows in the live store, so the application currently holds the invariant
- guard: a gate verdict for a run that does not exist is fabricated evidence

### research_evidence.gate_id -> research_gates.gate_id

- stage: **VALIDATION**  importance: **critical**
- invariant: every evidence artifact belongs to a gate (or the run, when gate_id is NULL)
- enforcement: **application**
- evidence: no DB constraint; 0 orphan rows in the live store, so the application currently holds the invariant
- note: gate_id is nullable by design — evidence may be per-run
- guard: evidence attributed to a gate that does not exist is forgery

### shadow_decisions.run_id -> shadow_runs.run_id

- stage: **REPLAY**  importance: **critical**
- invariant: every shadow decision belongs to a shadow run
- enforcement: **application**
- evidence: no DB constraint; 0 orphan rows in the live store, so the application currently holds the invariant
- guard: replay statistics would count decisions from a run that never happened

### shadow_comparisons.run_id -> shadow_runs.run_id

- stage: **COUNTERFACTUAL**  importance: **critical**
- invariant: every comparison belongs to a shadow run
- enforcement: **application**
- evidence: no DB constraint; 0 orphan rows in the live store, so the application currently holds the invariant
- guard: a promotion decision read from a comparison with no run has no basis

### training_runs.model_id -> experience_model_registry.model_id

- stage: **MODEL**  importance: **critical**
- invariant: every completed training run produces a model the registry knows
- enforcement: **neither**
- evidence: no DB constraint AND 6 orphan rows live: the invariant is asserted by nothing
- note: model_id is nullable in training_runs; the registry is keyed on (model_id, model_version). LIVE EVIDENCE: all 6 completed training runs persist model_id = '' (empty string, not NULL) while the registry holds 6 distinct model ids — the training stage completes without recording which model it produced, so no training run can be joined to its model. PRODUCTION DEFECT (owned by Agent 1's runtime scope; reported, not patched).
- guard: a model trained but never linked cannot be promoted or rolled back

### research_runs.strategy_id -> strategy_registry.strategy_id

- stage: **STRATEGY**  importance: **critical**
- invariant: every research run validates a registered strategy
- enforcement: **application**
- evidence: no DB constraint; 0 orphan rows in the live store, so the application currently holds the invariant
- guard: a run's verdict attached to no strategy cannot be acted on

### model_governance_events.model_id -> model_governance_state.model_id

- stage: **PROMOTION**  importance: **high**
- invariant: every MODEL-scoped governance event describes a model the state table tracks
- enforcement: **neither**
- evidence: no DB constraint AND 59164 orphan rows live: the invariant is asserted by nothing
- note: model_id is nullable in both — an event may be system-scoped (REGISTRY_RECONCILED / FEATURE_PARITY_FAILURE fire without a model). The probe counts events whose model_id does not appear in the state table, which is EXPECTED for system-scoped events and only a defect for events that claim a specific model.
- guard: an audit trail for an unknown model is unverifiable

## Uniqueness of identity columns

| Table | Column | Unique | Evidence |
| --- | --- | --- | --- |
| research_runs | run_id | no | no UNIQUE constraint or index on the identity column |
| research_gates | gate_id | no | no UNIQUE constraint or index on the identity column |
| research_evidence | evidence_id | no | no UNIQUE constraint or index on the identity column |
| shadow_runs | run_id | no | no UNIQUE constraint or index on the identity column |
| shadow_decisions | shadow_decision_id | no | no UNIQUE constraint or index on the identity column |
| experience_model_registry | model_id | no | no UNIQUE constraint or index on the identity column |
| training_runs | run_id | no | no UNIQUE constraint or index on the identity column |
| strategy_registry | strategy_id | no | no UNIQUE constraint or index on the identity column |
| model_governance_events | event_id | no | no UNIQUE constraint or index on the identity column |
| model_promotion_audit | promotion_id | no | no UNIQUE constraint or index on the identity column |
