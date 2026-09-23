# config — bounded context

Platform settings + the lane's shared validation engine. Invariants: an invalid
payload is NEVER sent (client rules → /api/settings/validate per key **with the
proposed value** → apply); RESTART_REQUIRED keys are excluded from the hot-apply
payload and named in the report (kept as local edits, never silently dropped);
masked secrets (telegram token, mt5.password from GET /api/config) are never
resubmitted as credentials (BUG-072/080 + TASK-CFGUI-001 restore-on-save);
mode switch and applies are decided by the engine report, not HTTP status —
the confirm modal shows the server's /api/v1/runtime/mode/preview verdict
(200 for valid and invalid proposals; the local matrix is only a pre-filter).
validation.ts + ui/kit.tsx + ui/kit.css are lane-shared (rules, debug, health,
database reuse them); ui/settings.css is cfg-page-scoped styling (tokens only).
EDD: diagnostics_state_routes.py (config/runtime-config/settings/telegram),
api_v1/runtime.py (mode matrix + preview). Tests: tests/unit/
test_config_tab_routes.py.
