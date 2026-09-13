# config — bounded context

Platform settings + the lane's shared validation engine. Invariants: an invalid
payload is NEVER sent (client rules → /api/settings/validate per key → apply);
masked secrets (telegram token) are never resubmitted as credentials (BUG-072/
080 discipline); mode switch and applies are decided by the engine report, not
HTTP status. validation.ts + ui/kit.tsx + ui/kit.css are lane-shared (rules,
debug, health, database reuse them). EDD: diagnostics_state_routes.py:1453-1813
(config/runtime-config/settings/telegram), api_v1/runtime.py (mode matrix).
