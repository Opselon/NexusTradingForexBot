"""Apply BUG-046 server.py edit #2: research summary outcome_quality."""

from pathlib import Path

p = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\web\server.py")
text = p.read_text(encoding="utf-8")

anchor = """        try:
            from nexus_scalp.research.store import registry_summary

            summary = registry_summary(engine.audit)
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                summary["worker"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "summary": summary})
"""

replacement = """        try:
            from nexus_scalp.research.store import outcome_quality_summary, registry_summary

            summary = registry_summary(engine.audit)
            summary["outcome_quality"] = outcome_quality_summary(engine.audit)
            worker = getattr(engine, "research_worker", None)
            if worker is not None:
                from nexus_scalp.research.worker import format_research_worker_status

                summary["worker"] = format_research_worker_status(worker)
            return serialize_enums({"available": True, "summary": summary})
"""

assert text.count(anchor) == 1, f"anchor count={text.count(anchor)}"
text = text.replace(anchor, replacement)

p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("server.py summary edit applied")
