"""TASK-03-70D-PARITY registry claim (additive rows only, CRLF-safe)."""
from pathlib import Path

ROOT = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")


def read_text(p: Path) -> str:
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(p: Path, s: str) -> None:
    with open(p, "w", newline="", encoding="utf-8") as f:
        f.write(s)


# ---------- 1. taskboard.md ----------
tb = ROOT / "agents" / "taskboard.md"
content = read_text(tb)

row = (
    "| TASK-03-70D-PARITY | Hermes-Parity | CRITICAL | 70D Dataset/Replay/Inference/Runtime "
    "Contract Parity: canonical 70D schema contract (scalp_v3 = Base 0..49 + News 10D 50..59 + "
    "Liquidity 10D 60..69), schema hash, canonical snapshot, dataset builder + quality gates + "
    "reproducibility, replay parity + anti-leakage, inference validator (10 rejection codes), model "
    "manifest + scaler + legacy protection (60D/70D mismatch blocked), four News/Liquidity toggle "
    "combos, golden corpus, real-data parity, no fake fallback, no DB on hot path | 70D series "
    "TASK-01 (liquidity base), TASK-02 (integration), INV-009/INV-008 | features/schema_contract.py "
    "(new), features/schema.py (scalp_v3=70D), model_generation/schema_v2.py (compute_70d_frame), "
    "model_generation/models.py (manifest extension), model_generation/news_bridge.py (news 10D "
    "producer), model_generation/replay.py, inference validator (new), live_engine (guarded hook), "
    "tests/unit/test_70d_contract_parity_task3.py, docs/70D_DATA_CONTRACT.md, "
    "docs/agent_handoffs/TASK-03-70D-PARITY.md | FEATURE_SCHEMA_70D v1 (scalp_v3 70D canonical), "
    "FEATURE_SCHEMA_HASH v1 (new), INFERENCE_CONTRACT v1 (new) | none | IN_PROGRESS |\r\n"
)
anchor_t4 = "| TASK-04-70D-MODEL-VALIDATION |"
idx = content.find(anchor_t4)
assert idx > 0, "TASK-04 row anchor not found"
content = content[:idx] + row + content[idx:]

note = (
    "- TASK-03-70D-PARITY (2026-08-19, Hermes-Parity): canonical 70D contract owner. "
    "Decision record: scalp_v3 = 70D (Base 0..49 | News 10D 50..59 | Liquidity 10D 60..69) per 70D "
    "brief; scalp_v4 (TASK-02) left as candidate integration contract; news 10D = canonical "
    "news_context_v1 first-10 selection (documented in docs/70D_DATA_CONTRACT.md); liquidity 10D = "
    "features/liquidity_engine.compute_liquidity_features at 60..69. Parallel swarm WIP respected; "
    "additive rows only. TASK-04 benchmark unblocks after this task.\r\n"
)
note_anchor = "## Notes\r\n"
ni = content.find(note_anchor)
assert ni > 0
content = content[:ni] + note + content[ni:]
write_text(tb, content)
print("taskboard rows:", content.count("TASK-03-70D-PARITY"))

# ---------- 2. change_control.md ----------
cc = ROOT / "agents" / "change_control.md"
cc_text = read_text(cc)
chg = (
    "CHANGE-ID: CHG-0015\r\n"
    "Agent: Hermes-Parity\r\n"
    "Role: 70D Dataset/Replay/Inference/Runtime Contract Engineer\r\n"
    "Task: TASK-03-70D-PARITY\r\n"
    "Scope: canonical 70D feature contract (scalp_v3 = Base 0..49 + News 10D 50..59 + Liquidity "
    "10D 60..69); deterministic feature_schema_hash; immutable 70D snapshot with provenance; "
    "dataset builder (compute_70d_frame) + quality gates + reproducibility; replay parity + "
    "anti-leakage; inference validator with explicit rejection codes; model manifest extension "
    "(feature_schema_hash, training_dataset_id) + scaler compatibility (no pad/truncate); legacy "
    "60D protection (mismatch blocked); four News/Liquidity toggle combinations; golden corpus; "
    "real-data parity; no fake fallback values; no DB on tick hot path (INV-001). Trading behavior "
    "untouched; no Champion change; no auto-promotion.\r\n"
    "Affected files: features/schema_contract.py (new), features/schema.py, "
    "model_generation/schema_v2.py, model_generation/models.py, model_generation/news_bridge.py, "
    "model_generation/replay.py, inference validator (new module), application/live_engine.py "
    "(guarded additive hook), tests/unit/test_70d_contract_parity_task3.py, docs/70D_DATA_CONTRACT.md, "
    "docs/agent_handoffs/TASK-03-70D-PARITY.md, agents/{taskboard,change_control,contracts,"
    "runtime_invariants,repository_state,bugs}.md (additive rows)\r\n"
    "Contracts: FEATURE_SCHEMA_70D v1 (scalp_v3 70D canonical), FEATURE_SCHEMA_HASH v1 (new), "
    "INFERENCE_CONTRACT v1 (new)\r\n"
    "Risk: LOW-MEDIUM (feature-contract hardening only; live 50D hot path untouched; guarded 70D "
    "hook behind config flag)\r\n"
    "Status: IN_PROGRESS\r\n"
    "\r\n"
)
anchor_chg = "## Open / recent changes"
ci = cc_text.find(anchor_chg)
assert ci > 0
insert_at = cc_text.find("\r\n", ci)
cc_text = cc_text[: insert_at + 2] + chg + cc_text[insert_at + 2 :]
write_text(cc, cc_text)
print("change_control rows:", cc_text.count("CHG-0015"))
print("OK")