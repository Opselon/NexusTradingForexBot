# health — bounded context

Subsystem matrix over 12 independent reads (10s budget): /api/debug/health,
/api/v1/system/health|readiness|workers|version|capabilities|runtime|status,
/api/mt5/status, /api/news/health, /api/forensics/health, /health probe.
Invariants: each cell keeps its OWN fetch age — a stale GOOD recolors to amber;
a failed endpoint fails only its cell; verdict words map only known backend
states, unknown renders UNKNOWN. EDD: api_v1/system.py, debug_research_routes.py:325.
