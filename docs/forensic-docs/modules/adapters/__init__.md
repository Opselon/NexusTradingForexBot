# src/nexus_scalp/adapters/mt5/__init__.py + src/nexus_scalp/adapters/__init__.py + src/nexus_scalp/adapters/database/__init__.py

- **PURPOSE:** Adapter package export surfaces.
- **ARCHITECTURE LAYER:** Adapters.
- **RESPONSIBILITY:** Stable import paths for the adapter implementations
  (mt5: DirectMT5Adapter/RemoteMT5GatewayAdapter/(providers, diagnostics);
  paper: PaperMT5Adapter; database: AuditRepository/broker history).
- **DEPENDENCIES:** the sibling modules.
- **CONNECTS TO:** ports consumers, CLI construction, tests.
- **KEY CONCEPTS:** The `adapters.mt5.__init__` re-exports the diagnostics
  + providers tokens that the PORT (ports/mt5_port.py) itself imports —
  keeping the init light avoids cycles; the database init deliberately
  exports AuditRepository so `from ...adapters.database import
  AuditRepository` is the sanctioned import.
- **EDGE CASES & PITFALLS:** Adding an adapter means updating the
  cross-adapter contract tests (all adapters must satisfy IMT5Port the
  same way); the init files must not import the heavyweight MetaTrader5
  package at package import time (only the concrete adapter module does).