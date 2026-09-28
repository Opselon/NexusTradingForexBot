# FRONTEND_AUDIT.md — Phase 1 Forensic Frontend Audit
**Date:** 2026-08-23  |  **Target:** `Web/` control center dashboard (`index.html`, `app.js`, `api_client.js`, etc.)

---

## 1. Executive Summary
This audit inspects the existing Nexus Scalp Engine web frontend to establish the foundation for Phase 3–9 Command Center frontend integration. The dashboard is a modular vanilla JS / Tailwind CSS SPA (`index.html` + modular `.js` files) communicating via JSON APIs on port `8080`.

---

## 2. Existing Frontend Files & Architecture

| File | Purpose | Status / Integration Points |
|:---|:---|:---|
| `Web/index.html` | Control Center SPA container (Tailwind CSS, FontAwesome) | Contains tabs for Monitoring, Account, AI Intel, Research, Factory. Needs new Command Center tab/view. |
| `Web/api_client.js` | Central API client (`window.NX.api`) | Implements robust fetch wrapper with `X-Request-ID`, error envelope parsing (`{error:{code,message,request_id}}`), and request deduping. Ready for Command Center endpoints. |
| `Web/app.js` | Main dashboard controller and tab switcher | Handles SSE live updates, market ticker, tabs. |
| `Web/forensic_console.js` | Forensic incident viewer | Structured event table with filters. |
| `Web/news_intelligence.js` | News Intel Pro Mode hub | Keyword matrix and AI analysis. |

---

## 3. Findings & Gaps for Strategy Command Center

1. **Missing UI Surface**: There is no dedicated visual panel or spatial 2.5D view for the Strategy Command Center in `index.html`. A new tab (`tab-command-center`) must be added to the navigation sidebar.
2. **Spatial 2.5D Rendering Stack**: No Canvas2D or WebGL spatial renderer currently exists in `Web/`. A lightweight Canvas2D / CSS-perspective layout renderer (`command_center_spatial.js`) will be created to consume `/api/command-center/spatial`.
3. **Time Machine Controls**: No playback scrubber or time machine UI exists. A playback toolbar must be added.
4. **Inspector Modal / Pane**: Detailed strategy inspector with DNA, attribution, evidence graph, and debug hints needs a dedicated inspector drawer/pane.
5. **Event-Driven Reconciliation**: SSE updates and event polling need to hook into the Command Center spatial nodes for smooth transitions.

---

## 4. Architectural Decision on Rendering Technology
- **Selected**: **Lightweight HTML5 Canvas2D + CSS Transform Layers**
- **Justification**: Zero external bundle size, 60fps GPU acceleration via standard browser compositing, perfectly suited for 2.5D depth zones and node rendering without the memory bloat or dependency overhead of a heavy 3D engine (Three.js/WebGL), fully satisfying the user's explicit preference for spatial 2.5D over heavy 3D.
