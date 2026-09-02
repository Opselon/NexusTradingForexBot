"""Restore the REAL factory LLM config (was clobbered by a test write)."""
from nexus_scalp.settings import load_settings_service

svc = load_settings_service()
# Restore the user's real values (seen live at 23:03 before clobbering)
svc.set_factory_llm_config(
    api_key="",            # keep existing secret (do NOT overwrite)
    base_url="http://178.105.20.69:20128/v1",
    model="claude-opus-5",
    temperature=0.7,
    request_timeout_sec=300,
    max_requests_per_generation=60,
    actor="restore",
)
cfg = svc.get_factory_llm_config()
print("restored base:", cfg["api_base_url"])
print("restored model:", cfg["model"])
print("key still set:", bool(cfg["api_key"]))
