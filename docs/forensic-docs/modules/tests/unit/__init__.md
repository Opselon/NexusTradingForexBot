# tests/unit/__init__.py

- GUARDS: Makes `tests/unit` a Python package for pytest collection; companion to the empty `tests/__init__.py`.
- KEY ASSERTIONS: none (0 lines).
- PITFALLS IT ENCODES: none — deliberately empty so unit tests import cleanly without side effects.
- NOTES: The actual shared unit-test logic lives in the non-test module `tests/unit/task4_research_helpers.py` (imported, not collected).
