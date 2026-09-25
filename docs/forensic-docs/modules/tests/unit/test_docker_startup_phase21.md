# tests/unit/test_docker_startup_phase21.py

- GUARDS: Docker startup & environment contract (DOCKER-REPAIR, 2026-08-20): the container-facing surface — compose file parses, expected services declared, env contract for the container runtime.
- KEY ASSERTIONS:
  - `docker compose config --quiet` parses; service set matches spec; required env vars present with sane defaults; container entrypoint wiring consistent (27 asserts).
- PITFALLS IT ENCODES: compose config must remain valid as a machine-checkable contract (CI parses it too); env drift between compose and runtime config is a startup failure class.
- NOTES: Runs real `docker compose config` (subprocess), so it is skipped when docker is absent.
