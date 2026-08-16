"""News source consensus (PHASE 12).

When multiple independent high-quality sources report the same event,
confidence increases. When sources disagree, the event is marked
MIXED / CONFLICTED and confidence is reduced. Source count alone is NEVER
turned into certainty: tier weights drive the weighted direction and
confidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.models import NewsConsensus, NewsDirection, NewsSource

#: Direction agreement ratio above this is "agreeing".
AGREEMENT_HIGH = 0.66
#: Conflict ratio above this marks the consensus CONFLICTED.
CONFLICT_HIGH = 0.34


def compute_consensus(
    article_id: str,
    directions: list[tuple[NewsDirection, float]],
    sources: list[NewsSource],
) -> NewsConsensus:
    """Computes a consensus from (direction, confidence) observations.

    Args:
        directions: per-source directional observations and their confidences.
        sources: the NewsSource registry entries involved (for tier weights).
    """
    if not directions:
        return NewsConsensus(article_id=article_id, source_count=0, evaluated_at=datetime.now(UTC))

    weights: list[float] = []
    by_direction: dict[NewsDirection, float] = defaultdict(float)
    for i, (direction, conf) in enumerate(directions):
        src = sources[i] if i < len(sources) else None
        w = (src.trust_weight if src else 0.5) * (0.5 + float(conf) * 0.5)
        weights.append(w)
        by_direction[direction] += w

    total_w = sum(weights) or 1.0
    top_dir, top_w = max(by_direction.items(), key=lambda kv: kv[1])
    agreement = round(top_w / total_w, 4)
    # conflict = share of weight on directions other than the top one
    conflict = round(1.0 - agreement, 4)

    if conflict >= CONFLICT_HIGH and agreement <= AGREEMENT_HIGH:
        weighted_direction = NewsDirection.CONFLICTED
    elif agreement >= AGREEMENT_HIGH:
        weighted_direction = top_dir
    else:
        weighted_direction = NewsDirection.MIXED

    # Confidence: tier-weighted agreement, diluted by conflict.
    base_conf = agreement
    confidence = round(base_conf * (1.0 - min(conflict, 0.5)), 4)
    confidence = round(max(0.05, min(1.0, confidence)), 4)

    return NewsConsensus(
        article_id=article_id,
        source_count=len(directions),
        independent_count=len(directions),
        agreement=round(agreement, 4),
        conflict=round(conflict, 4),
        directions=[d for d, _ in directions],
        weighted_direction=weighted_direction,
        confidence=confidence,
        evaluated_at=datetime.now(UTC),
    )


def combine_consensus(
    article_id: str,
    source_articles: list[dict[str, Any]],
    source_registry: dict[str, NewsSource],
) -> NewsConsensus:
    """Builds a consensus over one canonical event using its evidence
    sources and their stored directional analyses.

    Falls back to NEUTRAL with low confidence when no analysis exists yet.
    """
    observations: list[tuple[NewsDirection, float]] = []
    sources: list[NewsSource] = []
    for art in source_articles:
        src_id = art.get("source_id", "")
        src = source_registry.get(src_id)
        direction = NewsDirection.NEUTRAL
        conf = 0.3
        try:
            # Analysis payload on the article row carries direction/confidence
            # when the analysis pipeline has run.
            direction_str = art.get("direction", "NEUTRAL") or "NEUTRAL"
            direction = NewsDirection(direction_str.upper())
            conf = float(art.get("confidence", 0.3) or 0.3)
        except (ValueError, TypeError):
            pass
        observations.append((direction, conf))
        sources.append(
            src
            if src
            else NewsSource(
                source_id=src_id,
                name=src_id,
                tier=src.tier if src else NewsSource(source_id=src_id, name=src_id).tier,
            )
        )

    return compute_consensus(article_id=article_id, directions=observations, sources=sources)
