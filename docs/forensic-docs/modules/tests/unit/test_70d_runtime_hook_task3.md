# tests/unit/test_70d_runtime_hook_task3.py

- GUARDS: TASK-03-70D-PARITY — runtime 70D hook (TEST-70D-PARITY-14..17, 25, 26): News ON/OFF × Liquidity ON/OFF combinations all produce the canonical 70D frame through the live hook path.
- KEY ASSERTIONS:
  - each of the four News/Liquidity combinations yields full 70 dims with correct news+liquidity segments; hook output feeds the model input unchanged (43 asserts).
- PITFALLS IT ENCODES: the runtime hook is the live compuation point — parity with training/replay must hold under every combination toggle.
- NOTES: Four-way parametrized combinations; closes the loop with test_70d_parity_task3.
