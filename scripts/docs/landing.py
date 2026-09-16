"""Cinematic landing homepage — section markup for the multilingual site build.

NSE-Docs / Nexus-Docs owned. Imported by build_site.py; every internal link
is produced through the builder's page_href/asset_href helpers (LINK LAW:
depth-relative, no root-absolute URLs on Pages).

Truth contract (EVIDENCE BEFORE CLAIMS):
  * Every capability state here is copied from docs/project/status.md +
    docs/project/capabilities.md — CERTIFIED / IMPLEMENTED / EXPERIMENTAL /
    RESEARCH grades, and the published 70D NOT_ELIGIBLE OOS result.
  * No profitability, win-rate, volume or user figures appear anywhere.
  * The market visualization uses deterministic demonstration candles and is
    labelled DEMO / ILLUSTRATIVE in all locales.
  * Live values (release, commit, CI, stars) come from data/project-status.json
    generated at build time; the client may refresh them from the public
    GitHub API with graceful fallback — the section renders "N/A" when a
    value is unavailable and never fabricates.
"""

from __future__ import annotations

import html
import json


def _land(B, lang: str, key: str) -> str:
    """Landing copy from the locale contract: LOCALES[lang]['land'] -> EN.
    Keys may be dotted-flat (stored as-is) or nested."""
    land = B.LOCALES[lang].get("land", {})
    en = B.LOCALES["en"].get("land", {})

    def lookup(d: dict) -> str | None:
        if key in d and not isinstance(d[key], dict):
            return str(d[key])
        node: dict = d
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return str(node) if not isinstance(node, dict) else None

    value = lookup(land)
    if value is None:
        value = lookup(en)
    if value is None:
        value = key.rsplit(".", maxsplit=1)[-1].replace("_", " ")
    return value


def status_board(B, lang: str, from_rel: str) -> str:
    """Hero status board — static facts proven by the repo docs. Live values
    (release/commit/CI) are hydrated client-side from data/project-status.json;
    the markup ships with build-time version/revision which ARE known."""
    rows = [
        ("engine", "READY", ""),
        ("market", "XAUUSD · M1", ""),
        ("mode", "PAPER", "default"),
        ("model", "50D", "live-contract"),
        ("research", "70D", "candidate"),
        ("risk", "GUARDED", ""),
        ("observability", "ACTIVE", ""),
    ]
    cells = "".join(
        f"<div class='st-cell'><span class='st-k'>{html.escape(_land(B, lang, 'status.' + k))}</span>"
        f"<span class='st-v {extra}'>{html.escape(v)}</span></div>"
        for k, v, extra in rows
    )
    return (
        "<div class='status-board' role='group' "
        f"aria-label='{html.escape(_land(B, lang, 'status_title'))}'>"
        f"<div class='st-head'><span class='st-dot' aria-hidden='true'></span>"
        f"{html.escape(_land(B, lang, 'status_title'))}</div>"
        f"<div class='st-grid'>{cells}</div></div>"
    )


def hero(B, lang: str, from_rel: str) -> str:
    ui = B.UI(lang)
    chips = " · ".join(
        html.escape(x)
        for x in [
            "XAUUSD M1",
            "MT5",
            _land(B, lang, "chip_oss"),
            _land(B, lang, "chip_evidence"),
            "PAPER / SHADOW / LIVE",
        ]
    )
    return (
        "<section class='land-hero' id='top'>"
        "<div class='lh-grid'>"
        "<div class='lh-copy'>"
        f"<div class='hero-kicker lh-kicker'>{html.escape(ui['hero_kicker'])}</div>"
        f"<h1 class='lh-title'><span>{html.escape(ui['hero_title_a'])}</span>"
        f"<span class='lh-grad'>{html.escape(ui['hero_title_b'])}</span></h1>"
        f"<p class='lh-tag'>{html.escape(_land(B, lang, 'tagline'))}</p>"
        f"<p class='lh-sub'>{html.escape(ui['hero_sub'])}</p>"
        f"<div class='lh-actions'>"
        f"<a class='btn btn-primary btn-lg' href='{B.page_href('getting-started/quickstart/', lang, from_rel)}'>{html.escape(_land(B, lang, 'cta_explore'))} →</a>"
        f"<a class='btn btn-ghost btn-lg' href='{B.REPO_URL}'>GitHub ↗</a>"
        "</div>"
        f"<div class='lh-chips'>{chips}</div>"
        "</div>"
        "<div class='lh-visual'>"
        "<div class='core-stage' id='nexus-core' data-revision='"
        + B.REVISION
        + "' aria-hidden='true'>"
        "<canvas id='core-canvas' width='560' height='560'></canvas>"
        "<div class='core-fallback' aria-hidden='true'></div>"
        "<div class='core-tags'>"
        "<span class='core-tag'>TCN + Self-Attention</span>"
        "<span class='core-tag'>INV-001…010</span>"
        "<span class='core-tag'>OOS GATE</span>"
        "</div>"
        "</div>" + status_board(B, lang, from_rel) + "</div></div>"
        "<p class='sr-only'>" + html.escape(_land(B, lang, "hero_3d_alt")) + "</p>"
        "</section>"
    )


# stage -> (icon, docs page rel, name, description key)
PIPELINE_STAGES = [
    ("📡", "architecture/data-flow", "MARKET DATA", "d_market"),
    ("🧬", "architecture/data-flow", "CAUSAL FEATURES", "d_features"),
    ("🧠", "architecture/model-pipeline", "INFERENCE · SCALPNET", "d_infer"),
    ("♟️", "architecture/execution-pipeline", "SMC POLICY", "d_policy"),
    ("🛡️", "architecture/execution-pipeline", "RISK ENGINE", "d_risk"),
    ("⚡", "architecture/runtime", "EXECUTION", "d_exec"),
    ("🧾", "architecture/database", "ACCOUNTING", "d_acct"),
    ("👁️", "architecture/observability", "OBSERVABILITY", "d_obs"),
    ("🔁", "research/methodology", "RESEARCH LOOP", "d_loop"),
]


def data_core(B, lang: str, from_rel: str) -> str:
    nodes = []
    for i, (icon, rel, name, dkey) in enumerate(PIPELINE_STAGES):
        href = B.page_href(rel, lang, from_rel)
        nodes.append(
            f"<a class='pc-node' href='{href}' style='--i:{i}'>"
            f"<span class='pc-num'>{i + 1:02d}</span>"
            f"<span class='pc-ic' aria-hidden='true'>{icon}</span>"
            f"<span class='pc-name'>{html.escape(name)}</span>"
            f"<span class='pc-desc'>{html.escape(_land(B, lang, dkey))}</span></a>"
        )
    arrows = "<span class='pc-arrow' aria-hidden='true'>↓</span>"
    return (
        "<section class='land-sec' id='pipeline'>"
        "<div class='section-head'><h2><span class='sec-ic'>⚙️</span> "
        + html.escape(_land(B, lang, "pipe_title"))
        + "</h2>"
        + f"<a class='wn-more' href='{B.page_href('architecture/system-map/', lang, from_rel)}'>"
        + html.escape(_land(B, lang, "system_map"))
        + " →</a></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "pipe_desc"))
        + "</p>"
        + "<div class='pipe-col'>"
        + arrows.join(nodes)
        + "</div></section>"
    )


WHY_ITEMS = [
    ("⚖️", "project/status", "why_evidence_t", "why_evidence_b"),
    ("🧬", "architecture/data-flow", "why_causal_t", "why_causal_b"),
    ("🔬", "research/reproducibility", "why_det_t", "why_det_b"),
    ("🛡️", "architecture/execution-pipeline", "why_risk_t", "why_risk_b"),
    ("👁️", "architecture/runtime", "why_truth_t", "why_truth_b"),
    ("🕵️", "architecture/observability", "why_forensic_t", "why_forensic_b"),
]


def why_nexus(B, lang: str, from_rel: str) -> str:
    cards = "".join(
        f"<a class='why-card' href='{B.page_href(rel, lang, from_rel)}'>"
        f"<span class='bento-icon' aria-hidden='true'>{icon}</span>"
        f"<h3>{html.escape(_land(B, lang, tkey))}</h3>"
        f"<p>{html.escape(_land(B, lang, bkey))}</p>"
        f"<span class='why-explore'>{html.escape(_land(B, lang, 'explore'))} →</span></a>"
        for icon, rel, tkey, bkey in WHY_ITEMS
    )
    return (
        "<section class='land-sec' id='why'>"
        "<div class='section-head'><h2><span class='sec-ic'>✦</span> "
        + html.escape(_land(B, lang, "why_title"))
        + "</h2></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "why_desc"))
        + "</p>"
        + f"<div class='why-grid'>{cards}</div></section>"
    )


MODES = [
    ("paper", "🟢", "mode_paper_d", "getting-started/first-run"),
    ("shadow", "👁", "mode_shadow_d", "architecture/runtime"),
    ("live", "🔴", "mode_live_d", "getting-started/configuration"),
]


def three_modes(B, lang: str, from_rel: str) -> str:
    # SHADOW docs live under architecture/system-map + runtime; use runtime.
    tabs = "".join(
        f"<button type='button' class='mode-tab m-{key}{' is-active' if key == 'paper' else ''}'"
        f" data-mode='{key}' role='tab' aria-selected='{str(key == 'paper').lower()}'"
        f" id='tab-{key}'>{html.escape(key.upper())}</button>"
        for key, _icon, _dkey, _rel in MODES
    )
    panels = "".join(
        f"<div class='mode-panel m-{key}{' is-active' if key == 'paper' else ''}'"
        f" role='tabpanel' aria-labelledby='tab-{key}'>"
        f"<div class='mp-env' aria-hidden='true'><span class='mp-orb m-{key}'></span>"
        f"<span class='mp-badge'>{html.escape(_land(B, lang, 'mode_' + key + '_badge'))}</span></div>"
        f"<h3>{icon} {html.escape(key.upper())} — {html.escape(_land(B, lang, 'mode_' + key + '_t'))}</h3>"
        f"<p>{html.escape(_land(B, lang, dkey))}</p>"
        f"<a class='chip' href='{B.page_href(rel, lang, from_rel)}'>{html.escape(_land(B, lang, 'docs'))} →</a>"
        f"</div>"
        for key, icon, dkey, rel in MODES
    )
    return (
        "<section class='land-sec' id='modes'>"
        "<div class='section-head'><h2><span class='sec-ic'>▦</span> "
        + html.escape(_land(B, lang, "modes_title"))
        + "</h2></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "modes_desc"))
        + "</p>"
        + f"<div class='mode-switch' role='tablist' aria-label='{html.escape(_land(B, lang, 'modes_title'))}'>{tabs}</div>"
        + f"<div class='mode-panels'>{panels}</div></section>"
    )


RESEARCH_STAGES = [
    "DATASET",
    "FEATURE CONTRACT",
    "MODEL",
    "EXPERIMENT",
    "WALK-FORWARD",
    "OOS GATE",
    "REPLAY",
    "VALIDATION",
    "PROMOTION",
]

RESEARCH_GRADES = [
    ("50D live contract (scalp_v1)", "chip-cert", "grade_certified", "architecture/data-flow"),
    ("Walk-forward + hard OOS gate", "chip-cert", "grade_certified", "research/out-of-sample"),
    (
        "Artifact-first Model Factory",
        "chip-impl",
        "grade_implemented",
        "architecture/model-pipeline",
    ),
    (
        "70D contract (Base+News+Liquidity)",
        "chip-exp",
        "grade_experimental",
        "architecture/research-stack",
    ),
    ("70D OOS evidence (real data)", "chip-rej", "grade_not_eligible", "research/validation"),
    (
        "Counterfactual engine (NO_TRADE walk)",
        "chip-res",
        "grade_research",
        "research/counterfactuals",
    ),
]


def research_lab(B, lang: str, from_rel: str) -> str:
    steps = "".join(
        f"<li class='rl-step'><span class='rl-dot' aria-hidden='true'></span>{html.escape(s)}</li>"
        for s in RESEARCH_STAGES
    )
    rows = "".join(
        f"<tr><td><a href='{B.page_href(rel, lang, from_rel)}'>{html.escape(name)}</a></td>"
        f"<td><span class='chip {chip}'>{html.escape(_land(B, lang, gkey))}</span></td></tr>"
        for name, chip, gkey, rel in RESEARCH_GRADES
    )
    return (
        "<section class='land-sec' id='research'>"
        "<div class='section-head'><h2><span class='sec-ic'>🔬</span> "
        + html.escape(_land(B, lang, "res_title"))
        + "</h2>"
        + f"<a class='wn-more' href='{B.page_href('project/capabilities/', lang, from_rel)}'>"
        + html.escape(B.UI(lang)["full_matrix"])
        + " →</a></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "res_desc"))
        + "</p>"
        + "<div class='res-grid'>"
        + f"<ol class='rl-chain' aria-label='{html.escape(_land(B, lang, 'res_pipeline'))}'>{steps}</ol>"
        + "<div class='table-wrap'><table><thead><tr><th>"
        + html.escape(B.UI(lang)["capability"])
        + "</th><th>"
        + html.escape(B.UI(lang)["status"])
        + "</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
        + "<p class='neg-note'>"
        + html.escape(_land(B, lang, "res_negative"))
        + "</p></div></div></section>"
    )


def market_demo(B, lang: str, from_rel: str) -> str:
    return (
        "<section class='land-sec' id='market'>"
        "<div class='section-head'><h2><span class='sec-ic'>📈</span> "
        + html.escape(_land(B, lang, "market_title"))
        + "</h2></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "market_desc"))
        + "</p>"
        + "<div class='demo-stage'>"
        + "<canvas id='demo-canvas' width='920' height='420'></canvas>"
        + "<span class='demo-tag'>"
        + html.escape(_land(B, lang, "market_demo_label"))
        + "</span>"
        + "</div>"
        + "<p class='demo-legend'>"
        + html.escape(_land(B, lang, "market_legend"))
        + "</p>"
        + "<p class='sr-only'>"
        + html.escape(_land(B, lang, "market_alt"))
        + "</p></section>"
    )


ARCH_NODES = [
    ("DOMAIN", "architecture/overview", "arch_domain"),
    ("PORTS", "architecture/overview", "arch_ports"),
    ("ADAPTERS", "architecture/data-flow", "arch_adapters"),
    ("APPLICATION", "architecture/runtime", "arch_app"),
    ("FEATURES", "architecture/data-flow", "arch_features"),
    ("MODELS", "architecture/model-pipeline", "arch_models"),
    ("RISK", "architecture/execution-pipeline", "arch_risk"),
    ("EXECUTION", "architecture/execution-pipeline", "arch_exec"),
    ("OBSERVABILITY", "architecture/observability", "arch_obs"),
    ("RESEARCH", "research/methodology", "arch_research"),
]


def architecture(B, lang: str, from_rel: str) -> str:
    # Hexagon explorer: DOMAIN at center, satellites linked to docs pages.
    satellites = ARCH_NODES[1:]
    items = "".join(
        f"<a class='hx-node n{n}' href='{B.page_href(rel, lang, from_rel)}' "
        f"style='--a:{round(360 / len(satellites) * n)}deg'><span>{html.escape(name)}</span>"
        f"<em>{html.escape(_land(B, lang, dkey))}</em></a>"
        for n, (name, rel, dkey) in enumerate(satellites)
    )
    domain_rel = ARCH_NODES[0][1]
    return (
        "<section class='land-sec' id='architecture'>"
        "<div class='section-head'><h2><span class='sec-ic'>🗺️</span> "
        + html.escape(_land(B, lang, "arch_title"))
        + "</h2>"
        + f"<a class='wn-more' href='{B.page_href('architecture/overview/', lang, from_rel)}'>"
        + html.escape(B.UI(lang)["view_architecture"])
        + " →</a></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "arch_desc"))
        + "</p>"
        + f"<div class='hex-wrap'><a class='hex-core' href='{B.page_href(domain_rel, lang, from_rel)}'>"
        + f"<strong>DOMAIN</strong><span>{html.escape(_land(B, lang, 'arch_domain'))}</span></a>"
        + items
        + "</div></section>"
    )


CONSOLE_CHIPS = [
    ("Control Center", "architecture/control-center-ui"),
    ("Live Monitoring", "architecture/runtime"),
    ("Strategy Research", "research/methodology"),
    ("Model Governance", "architecture/model-pipeline"),
    ("News Intelligence", "architecture/system-map"),
    ("Forensic Incident Center", "architecture/observability"),
]


def command_center(B, lang: str, from_rel: str) -> str:
    shot_href = B.asset_href("", "pics/web.png", lang)
    chips = "".join(
        f"<a class='chip' href='{B.page_href(rel, lang, from_rel)}'>{html.escape(name)} →</a>"
        for name, rel in CONSOLE_CHIPS
    )
    return (
        "<section class='land-sec' id='console'>"
        "<div class='section-head'><h2><span class='sec-ic'>🖥️</span> "
        + html.escape(_land(B, lang, "console_title"))
        + "</h2></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "console_desc"))
        + "</p>"
        + "<div class='monitor'>"
        + "<div class='monitor-screen'>"
        + f"<img src='{html.escape(shot_href)}' alt='{html.escape(_land(B, lang, 'console_alt'))}' loading='lazy' decoding='async'>"
        + "<span class='scanline' aria-hidden='true'></span>"
        + "</div><div class='monitor-foot' aria-hidden='true'></div></div>"
        + f"<div class='console-chips'>{chips}</div></section>"
    )


def project_pulse(B, lang: str, from_rel: str) -> str:
    """Build-time + client-refreshed metadata. Every value has an N/A
    placeholder that renders until data/project-status.json hydrates it."""
    pulse = (
        "<section class='land-sec' id='pulse'>"
        "<div class='section-head'><h2><span class='sec-ic'>📊</span> "
        + html.escape(_land(B, lang, "pulse_title"))
        + "</h2>"
        + f"<a class='wn-more' href='{B.page_href('project/status/', lang, from_rel)}'>"
        + html.escape(B.UI(lang)["status_label"])
        + " →</a></div>"
        + "<p class='section-desc'>"
        + html.escape(_land(B, lang, "pulse_desc"))
        + "</p>"
        + "<div class='pulse-grid' data-pulse>"
        + "".join(
            f"<div class='pulse-cell'><span class='pc-k'>{html.escape(_land(B, lang, 'pulse_' + k))}</span>"
            f"<b data-pulse-field='{k}'>{html.escape(v)}</b></div>"
            for k, v in (
                ("version", f"v{B.PROJECT_VERSION}"),
                ("release", "N/A"),
                ("commit", B.REVISION),
                ("ci", "N/A"),
                ("stars", "N/A"),
                ("forks", "N/A"),
                ("open_issues", "N/A"),
                ("last_sync", "N/A"),
            )
        )
        + "</div>"
        + "<div data-pulse-changelog class='pulse-log' hidden></div>"
        + "</section>"
    )
    return pulse


def section_nav(B, lang: str, from_rel: str) -> str:
    """Landing section navigation strip (the docs sidebar is hidden on the
    landing): anchors through the experience + quickstart + releases + a live
    status chip. Labels reuse existing locale keys (no new translation load).
    Note: the EN docs hub IS the site root, so no docs-hub link here."""
    anchors = [
        ("pipeline", "pipe_title"),
        ("why", "why_title"),
        ("modes", "modes_title"),
        ("research", "res_title"),
        ("market", "market_title"),
        ("architecture", "arch_title"),
        ("console", "console_title"),
        ("pulse", "pulse_title"),
    ]
    ui = B.UI(lang)
    items = "".join(
        f"<a class='lnav-link' href='#{aid}'>{html.escape(_land(B, lang, dkey))}</a>"
        for aid, dkey in anchors
    )
    return (
        "<nav class='land-nav glass' aria-label='"
        + html.escape(ui["explore_docs"])
        + "'>"
        + items
        + f"<a class='lnav-link lnav-docs' href='{B.page_href('getting-started/quickstart/', lang, from_rel)}'>{html.escape(ui['quickstart_label'])} →</a>"
        + f"<a class='lnav-link' href='{B.page_href('releases/', lang, from_rel)}'>{html.escape(ui['all_releases'])}</a>"
        + f"<a class='lnav-status' href='{B.page_href('project/status/', lang, from_rel)}'>"
        + "<span class='st-dot' aria-hidden='true'></span>"
        + html.escape(ui["status_label"])
        + "</a>"
        + "</nav>"
    )


def landing_body(
    B, lang: str, from_rel: str, whats_new: str, grid: str, timeline: str, cta: str
) -> str:
    """Full landing page body: cinematic entrance -> product -> docs bridge."""
    return (
        section_nav(B, lang, from_rel)
        + hero(B, lang, from_rel)
        + data_core(B, lang, from_rel)
        + why_nexus(B, lang, from_rel)
        + three_modes(B, lang, from_rel)
        + research_lab(B, lang, from_rel)
        + market_demo(B, lang, from_rel)
        + architecture(B, lang, from_rel)
        + command_center(B, lang, from_rel)
        + project_pulse(B, lang, from_rel)
        + whats_new
        + "<section class='secs reveal'><div class='section-head'><h2><span class='sec-ic'>📚</span> "
        + html.escape(B.UI(lang)["explore_docs"])
        + "</h2></div>"
        + f"<div class='sec-grid'>{grid}</div></section>"
        + "<section class='timeline-block reveal'><div class='section-head'><h2><span class='sec-ic'>🗓️</span> "
        + html.escape(B.UI(lang)["release_timeline"])
        + "</h2>"
        + f"<a class='wn-more' href='{B.page_href('releases/', lang, from_rel)}'>"
        + html.escape(B.UI(lang)["all_releases"])
        + " →</a></div>"
        + f"<div class='timeline'>{timeline}</div></section>"
        + cta
    )


def project_status_json(B, generated_at: str, coverage: dict | None) -> str:
    """data/project-status.json — the self-updating contract consumed by the
    landing page. Values come from build caches (release/repo metadata) or the
    repo itself; anything unavailable is null (renders N/A, never invented)."""
    releases = [r for r in B.load_releases() if not r.get("draft")]
    latest = releases[0] if releases else None
    repo_cache = B.load_repo_cache()
    return json.dumps(
        {
            "generated_at": generated_at,
            "version": B.PROJECT_VERSION,
            "revision": B.REVISION,
            "repo": B.REPO_URL,
            "pages": B.PAGES_URL,
            "release": {
                "tag": latest["tag_name"] if latest else None,
                "date": latest.get("published_at", "")[:10] if latest else None,
                "url": latest["html_url"] if latest else None,
                "highlights": B.release_highlights(latest.get("body", ""), 5) if latest else [],
            },
            "repo_stats": repo_cache or None,
            "translation_coverage": coverage or None,
        },
        ensure_ascii=False,
        indent=1,
    )
