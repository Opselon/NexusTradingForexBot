"""Read-side resolution for the pre-insert payload value gate.

``nexus_scalp.news.ingest.deduplicator._gate_duplicate_payload`` stops a
``body`` that is a verbatim restatement of ``summary`` from being persisted
(measured: ~28.5 MB of text stored twice, ~9% of the production database,
for zero capability gain). The gate writes ``body = ""`` in that case.

This module is the READ half of that contract. Every consumer that joins
``title + summary + body`` must resolve an empty body back to the summary,
otherwise a gated article would be analyzed as if it had no text — silently
changing the decision inputs.

The resolution is a single ``or``-fallback, not a second source of truth:
the summary IS the body's content, so reading it from ``summary`` is the
lossless re-materialization of the text the gate declined to duplicate.
"""

from __future__ import annotations

__all__ = ["resolve_article_body"]


def resolve_article_body(body: str | None, summary: str | None) -> str:
    """Return the article text a ``body`` consumer should analyze.

    * ``body`` non-empty -> the body carries real extra content; use it.
    * ``body`` empty -> it was deduped against (or never had) the summary;
      the summary holds the same text, so that is the content to analyze.

    Never raises, never returns None; callers that pass ``None`` get "".

    BYTE-IDENTITY: this is a pure ``or``-fallback and deliberately does NOT
    strip or normalize. The analysis consumers join ``title + summary +
    body``; the fallback must reproduce exactly the text the pre-gate row
    would have joined, including any leading/trailing whitespace, or a
    gated article would analyze differently from an ungated one.
    """
    b = body or ""
    return b if b else (summary or "")
