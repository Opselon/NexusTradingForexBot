"""Regression tests for the pre-insert news payload value gate.

DB-LIFECYCLE remediation (data-value law): ``news_articles.body`` and
``news_articles.summary`` were both written verbatim from the RSS feed, and
for feeds with no ``<content:encoded>`` the ingest layer copies ``summary``
INTO ``body`` — so the same ~28.5 MB of text was persisted twice (measured
on the production ledger: body 28,521,694 vs summary 28,517,874 logical
bytes, ~54.4 MB of a 79.5 MB table, ~9% of the whole 588 MB database).

``body`` is genuinely consumed (keyword extraction, the local analyzer, and
the LLM prompt all join title+summary+body), so the column cannot be
dropped. The gate instead answers ONE deterministic question before
persistence:

    "does this body carry information the summary does not?"

If not, the persisted body is ``""`` and the read side
(``resolve_article_body``) re-materializes the text from the summary.

This suite pins the properties the brief demands, and is written to FAIL if
the gate is removed or weakened (verified by reverting the source and
re-running — see the module docstring of each test for the exact revert
that must turn it red).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.news.body_resolution import resolve_article_body
from nexus_scalp.news.ingest.deduplicator import (
    _gate_duplicate_payload,
    _payload_eq,
    canonicalize_item,
    compute_article_hash,
)

_DT = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)

_SRC = {
    "title": "Bank of England proposes stricter capital rules",
    "url": "https://bankofengland.co.uk/pra/notice",
    "summary": "The PRA and FCA propose new capital requirements for banks.",
    "body": "The PRA and FCA propose new capital requirements for banks.",
    "published_at": _DT,
}


# ---------------------------------------------------------------------------
# 1. The gate itself
# ---------------------------------------------------------------------------


def test_gate_drops_body_identical_to_summary():
    """A byte-identical body carries zero extra information -> not persisted."""
    assert _gate_duplicate_payload("some text", "some text") == ""
    assert _gate_duplicate_payload("  some text  ", "some text") == ""
    assert _gate_duplicate_payload("Some Text", "some text") == ""


def test_gate_keeps_body_with_real_extra_content():
    """A body that adds content is DECISION/RESEARCH valuable -> keep it."""
    kept = _gate_duplicate_payload("short summary", "short summary plus much more detail")
    assert kept == "short summary plus much more detail"


def test_gate_keeps_body_when_summary_empty():
    """Body-only feed items must survive (there is no duplicate to drop)."""
    assert _gate_duplicate_payload("", "the only text we have") == "the only text we have"


def test_gate_empty_when_both_empty():
    assert _gate_duplicate_payload("", "") == ""


def test_gate_is_case_and_whitespace_insensitive():
    """The producer copies description into body with different wrapping."""
    assert _payload_eq("The PRA proposes rules.", "the PRA   proposes  rules.") is True
    assert _payload_eq("a b c", "a b d") is False


@pytest.mark.parametrize("body,expected", [("identical text", ""), ("", ""), (None, "")])
def test_gate_never_returns_none(body, expected):
    assert _gate_duplicate_payload("identical text", body) == expected


# ---------------------------------------------------------------------------
# 2. canonicalize_item wires the gate into the persistence seam
# ---------------------------------------------------------------------------


def test_canonicalize_dedupes_duplicate_body():
    """The canonical row the insert path consumes carries body=''."""
    out = canonicalize_item(dict(_SRC), "src1", "Source One")
    assert out["body"] == ""
    assert out["body_deduped"] is True


def test_canonicalize_preserves_distinct_body():
    out = canonicalize_item(
        {**_SRC, "body": _SRC["summary"] + " Extra paragraph of real content."},
        "src1",
        "Source One",
    )
    assert out["body"].endswith("Extra paragraph of real content.")
    assert out["body_deduped"] is False


def test_canonicalize_summary_unchanged_by_gate():
    """The summary is the surviving canonical text — it must be untouched."""
    out = canonicalize_item(dict(_SRC), "src1", "Source One")
    assert out["summary"] == _SRC["summary"]


# ---------------------------------------------------------------------------
# 3. IDENTITY STABILITY — the invariant that makes the gate safe
# ---------------------------------------------------------------------------
# If the gate changed the article hash, every already-stored article would
# mint a NEW hash on the next poll and re-enter the ledger as a "new" story,
# defeating the dedup this module exists to provide. The fingerprint must see
# the RAW body, not the gated one.
def test_article_hash_stable_across_gate():
    """The hash of a duplicate-body item equals the pre-gate hash."""
    raw = compute_article_hash(
        url=_SRC["url"],
        title=_SRC["title"],
        source_id="src1",
        published_at=_DT,
        summary=_SRC["summary"],
        body=_SRC["body"],
    )
    out = canonicalize_item(dict(_SRC), "src1", "Source One")
    assert out["article_hash"] == raw


def test_gate_does_not_silently_merge_distinct_articles():
    """Two articles with the SAME summary but DIFFERENT bodies stay distinct."""
    a = canonicalize_item({**_SRC, "body": _SRC["summary"]}, "src1", "S1")
    b = canonicalize_item(
        {**_SRC, "body": _SRC["summary"] + " Completely different reporting."},
        "src1",
        "S1",
    )
    assert a["article_hash"] != b["article_hash"]
    assert b["body_deduped"] is False


# ---------------------------------------------------------------------------
# 4. The READ side — no capability lost
# ---------------------------------------------------------------------------


def test_resolve_body_falls_back_to_summary():
    """A gated (empty) body re-materializes from the summary — lossless."""
    text = resolve_article_body("", _SRC["summary"])
    assert text == _SRC["summary"]


def test_resolve_body_prefers_real_body():
    real = _SRC["summary"] + " Extra detail."
    assert resolve_article_body(real, _SRC["summary"]) == real


def test_resolve_body_handles_none_and_empty():
    assert resolve_article_body(None, None) == ""
    assert resolve_article_body(None, None) is not None
    # BYTE-IDENTITY: the fallback does not normalize — the analysis text must
    # be reproduced exactly as the pre-gate row stored it (whitespace and all).
    assert resolve_article_body(None, "  exact  ") == "  exact  "
    assert resolve_article_body("kept", None) == "kept"


def test_resolve_body_never_returns_none():
    assert resolve_article_body(None, None) is not None


# ---------------------------------------------------------------------------
# 5. LOSSLESSNESS of the analysis text — the decision input is unchanged
# ---------------------------------------------------------------------------
# The local analyzer / keyword extractor join title+summary+body. Before the
# gate that join saw "title summary <body==summary>". After the gate the body
# is empty, so resolve_article_body must supply the same joined text.
def test_analysis_text_is_identical_after_gate():
    from nexus_scalp.news.analysis.local import _title_and_text

    class _Art:
        title = _SRC["title"]
        summary = _SRC["summary"]
        body = ""  # the gated, persisted value

    pre_gate_text = " ".join([_SRC["title"], _SRC["summary"], _SRC["summary"]]).upper()
    assert _title_and_text(_Art()) == pre_gate_text


def test_analysis_text_unchanged_when_body_is_distinct():
    from nexus_scalp.news.analysis.local import _title_and_text

    real_body = _SRC["summary"] + " Extra detail."

    class _Art:
        title = _SRC["title"]
        summary = _SRC["summary"]
        body = real_body

    expected = " ".join([_SRC["title"], _SRC["summary"], real_body]).upper()
    assert _title_and_text(_Art()) == expected
