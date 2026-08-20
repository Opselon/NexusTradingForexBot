# src/nexus_scalp/news/analysis/consensus.py

- PURPOSE: Multi-source consensus for one canonical news event — multiple
  independent high-quality sources reporting the same event increase
  confidence; disagreement yields MIXED/CONFLICTED with reduced
  confidence. Source count alone is NEVER certainty: tier weights drive
  the weighted direction and confidence (docstring).
- ARCHITECTURE LAYER: Domain/analysis logic (pure functions).
- RESPONSIBILITY: compute_consensus from (direction, confidence)
  observations + source registry (tier trust); combine_consensus over
  evidence articles; fall back to honest NEUTRAL low-confidence when no
  analysis exists yet.
- DEPENDENCIES: models (NewsConsensus, NewsDirection, NewsSource);
  stdlib defaultdict/datetime.
- CONNECTS TO: analysis pipeline / web UI consensus display; consumers of
  the news_consensus table.
- KEY CONCEPTS:
  - Thresholds: AGREEMENT_HIGH = 0.66 (direction-agreement ratio above
    this = "agreeing"); CONFLICT_HIGH = 0.34 (conflict share above this
    marks the consensus CONFLICTED).
  - compute_consensus (line 24): per observation weight = source
    trust_weight * (0.5 + conf*0.5) — tier weight meets the caller's
    confidence in a single multiplicative term; weights accumulate per
    direction; agreement = top-direction weight / total weight;
    conflict = 1 - agreement.
  - Direction resolution: conflict >= 0.34 AND agreement <= 0.66 ->
    CONFLICTED; agreement >= 0.66 -> top direction; otherwise MIXED.
  - Confidence = agreement * (1 - min(conflict, 0.5)) clamped [0.05, 1.0]
    — never zero (full conflict still reports 5%) and never > 1.
  - Empty directions -> neutral consensus (source_count=0).
  - combine_consensus (line 77): builds observations from evidence
    articles' stored direction/confidence (analysis payload embedded in
    the article row); unparsable direction falls back to NEUTRAL with
    conf 0.3; missing registry entries synthesize a default NewsSource
    (TIER_3, trust 0.55 via defaults).
- HOT PATH / PERFORMANCE: O(n) over evidence articles — trivial at
  article-analysis frequency.
- EDGE CASES & PITFALLS: `independent_count` is set equal to source_count
  — no real cross-source independence detection despite the field name;
  observations are paired POSITIONALLY with sources (i-th direction uses
  the i-th source) — callers must keep both lists in sync; line 109
  includes a dead `src.tier` expression inside an else-branch where src
  is None (the ternary already guards) — harmless but misleading; the 5%
  confidence floor means a fully-conflicted consensus still reports
  non-zero confidence (callers must interpret via the CONFLICTED
  direction).