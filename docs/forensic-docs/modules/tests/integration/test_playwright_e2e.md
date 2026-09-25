# tests/integration/test_playwright_e2e.py

- GUARDS: Browser end-to-end smoke for the web UI (canvas + tuner) — the only real-browser test in the suite (6 asserts).
- KEY ASSERTIONS:
  - `TestLiveEngineWiring.test_playwright_e2e_canvas_and_tuner`: page loads, canvas element present, tuner controls respond.
- PITFALLS IT ENCODES: e2e is kept minimal by design (one test, no explosion); the heavy UI contract coverage lives in the DOM/asset unit suites (test_frontend_assets_phase14.py) precisely so the browser test stays cheap.
- NOTES: Requires playwright browsers installed; skipped gracefully when unavailable (env-guarded).
