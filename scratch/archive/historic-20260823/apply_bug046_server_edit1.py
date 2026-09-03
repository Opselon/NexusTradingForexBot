"""Apply BUG-046 server.py edit via deterministic byte-level replacement."""

from pathlib import Path

p = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\web\server.py")
text = p.read_text(encoding="utf-8")

anchor = """    @app.post("/api/research/self-heal")
    def trigger_research_self_heal() -> dict[str, Any]:
        \"\"\"Rebuilds derived research state from the immutable ledger.\"\"\"
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import self_heal_research

            repaired = self_heal_research(engine.audit, engine.strategy_registry)
            return {"available": True, "repaired": int(repaired)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research self-heal failed"})
            return _err("INTERNAL_ERROR")
"""

replacement = """    @app.post("/api/research/self-heal")
    def trigger_research_self_heal() -> dict[str, Any]:
        \"\"\"Rebuilds derived research state from the immutable ledger.\"\"\"
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.research.store import self_heal_research

            repaired = self_heal_research(engine.audit, engine.strategy_registry)
            return {"available": True, "repaired": int(repaired)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Research self-heal failed"})
            return _err("INTERNAL_ERROR")

    @app.post("/api/research/repair-outcomes")
    def trigger_outcome_repair() -> dict[str, Any]:
        \"\"\"
        BUG-046: repairs historical zero-R closed outcomes from broker deal
        history. Bounded, idempotent, observable. Never touches the immutable
        decision rows; only the derived outcome layer is corrected.
        \"\"\"
        engine = _research()
        if engine is None:
            return {"available": False}
        try:
            from nexus_scalp.experience.outcome_repair import OutcomeRepairJob

            ledger = engine.experience_ledger
            adapter = engine.adapter
            job = OutcomeRepairJob(
                ledger=ledger,
                broker_deals_fn=lambda ticket, hours_back: adapter.get_closed_deals_history(
                    symbol="XAUUSD", hours_back=hours_back
                ),
            )
            result = job.run()
            return {"available": True, "result": result.to_dict()}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Outcome repair failed"})
            return _err("INTERNAL_ERROR")
"""

assert text.count(anchor) == 1, f"anchor count={text.count(anchor)}"
text = text.replace(anchor, replacement)

p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("server.py repair endpoint added")
