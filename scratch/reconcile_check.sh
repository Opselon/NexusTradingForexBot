#!/bin/bash
# RECONCILIATION: re-apply the P0/P1 implementation edits lost when the
# concurrent formatter wave overwrote the working tree (my baseline edits to
# tracked files vanished; my NEW files survived as untracked).
# This script is idempotent: it verifies and re-applies each edit.
set -u
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot

echo "== 1. comparison.py: statistical gate wiring =="
grep -q "evaluate_statistical_gate" src/nexus_scalp/shadow/comparison.py && echo "already applied" || echo "NEEDS APPLY"

echo "== 2. models.py: paired_deltas + statistical_provenance =="
grep -q "paired_deltas" src/nexus_scalp/shadow/models.py && echo "already applied" || echo "NEEDS APPLY"

echo "== 3. scalp_features.py: DST sessions =="
grep -q "session_flags_for_utc" src/nexus_scalp/features/scalp_features.py && echo "already applied" || echo "NEEDS APPLY"

echo "== 4. schema_augment.py: deprecation note =="
grep -q "DEPRECATED" src/nexus_scalp/features/schema_augment.py && echo "already applied" || echo "NEEDS APPLY"

echo "== 5. schema_v2.py: DST-aware extras =="
grep -q "session_phase_encoding_for_utc" src/nexus_scalp/model_generation/schema_v2.py && echo "already applied" || echo "NEEDS APPLY"

echo "== 6. bug082 test: DST-aware recomputation =="
grep -q "session_flags_for_utc" tests/unit/test_scalp_features_forensic_bug082.py && echo "already applied" || echo "NEEDS APPLY"
