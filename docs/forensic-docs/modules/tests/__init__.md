# tests/__init__.py

- GUARDS: Makes `tests` a Python package so pytest collects the suite cleanly; the empty marker of the whole test tree.
- KEY ASSERTIONS: none — zero code (0 lines).
- PITFALLS IT ENCODES: none (empty init: nothing imported, so no cross-module import side effects during collection).
- NOTES: Sibling `tests/unit/__init__.py` and `tests/helpers/*` modules exist for the same packaging reason; all fixture registration lives in `tests/conftest.py`, not here.
