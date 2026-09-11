# TASK4-SCRATCH-PROBES restore — companion docs note (pr #122)

Swarm QA cycle 2026-09-11 03:40Z: restored tests/helpers/liq60d_{distribution,redundancy}_audit.py
(orphaned when a77f8315 deleted scratch/ while test_70d_model_validation_task4.py:749/772 still
path-loaded them — CI-only FileNotFoundError) and repointed the two loads. Also re-pinned
test_experience_intelligence.py test_41 to the canonical ModelConfig.confidence_threshold=0.35
(config.py:79; stale 0.20 pin, red on every CI run of the file).

Evidence: local slim-venv repro RED at d96760be (0.35!=0.20 + 2x FileNotFoundError); after fix
72 passed, 16 artifact-gated skips; ruff check/format clean; mypy src clean (580 files).
PR: #122 (branch swarm/task4-liq-helpers, commit fda4a27e).
