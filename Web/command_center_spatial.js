/**
 * Command Center Spatial Renderer — 2.5D lifecycle zones + animated strategy movement
 * ---------------------------------------------------------------------------------
 * Consumes the authoritative spatial payload from /api/command-center/spatial
 * (computed by nexus_scalp.research.spatial_layout.SpatialLayout) and renders a
 * Canvas2D 2.5D environment.
 *
 * REAL LIFECYCLE STATES (verified against src/nexus_scalp/research/models.py
 * CandidateLifecycle — do NOT invent states):
 *   PIPELINE (ordered by maturity / pipeline progression):
 *     DISCOVERED -> BACKTESTING -> VALIDATING -> OOS_TESTING ->
 *     ROBUSTNESS_TESTING -> VALIDATED -> SHADOW -> ACTIVE
 *   TERMINAL / INACTIVE (never live): REJECTED, DEGRADED, RETIRED
 *
 * VISUAL GROUPING (documented, not invented):
 *   - Each lifecycle state renders as its own horizontal zone band; nodes inside
 *     a band are laid out by the backend (x = lateral column jitter, y = zone row).
 *   - Pipeline bands are ordered top->bottom by MATURITY_RANK so the eye reads
 *     "early research" at the top and "live trading" at the bottom.
 *   - Terminal states (REJECTED/DEGRADED/RETIRED) are grouped in a bottom
 *     "INACTIVE / TERMINAL" region, visually separated by a divider, because
 *     they never advance and are not part of the live pipeline.
 *   - An EXECUTION BOUNDARY line is drawn above ACTIVE to mark the gate that
 *     separates validated-but-not-live (SHADOW) from live trading.
 *
 * NODE ENCODINGS (never rely on color alone — at least one structural cue always
 * present):
 *   - position/zone ...... = lifecycle state (primary, structural)
 *   - size ............... = evidence volume (sample_count / size_hint)
 *   - elevation (stem) ... = health score (maturity proxy) — "?" when NOT MEASURED
 *   - rings .............. = validation depth (count of PASSed evidence gates)
 *   - pulse (animated) ... = active processing / live (only ACTIVE/SHADOW pulse)
 *   - trail .............. = movement history between refreshes
 *   - shape .............. = terminal states use a square; pipeline uses a disc
 *
 * TRUTH RULES (ANIMATION RECONCILIATION):
 *   flow = backend event -> local animation -> authoritative snapshot -> reconcile
 *   - A node's zone/position is ONLY what the backend reports. Local animation is
 *     cosmetic; every authoritative refresh reconciles visual state.
 *   - On a real transition the node VISIBLY moves along an eased, interpolated
 *     path with a transition trail. It NEVER teleports.
 *   - If a snapshot disagrees with an in-flight animation, BACKEND WINS: the
 *     animation target is overwritten to the authoritative target and re-eased.
 *   - Duplicate/stale events never re-trigger an animation or regress one: a
 *     transition is scheduled only when the authoritative zone actually changed.
 *   - On browser refresh mid-animation, recovery uses the authoritative snapshot
 *     (we rebuild from the last payload, no ghost positions persisted).
 *   - Missing data (health/elevation null) renders as "NOT MEASURED" marker,
 *     never a fabricated value.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Lifecycle definitions (must match backend CandidateLifecycle + spatial_layout)
  // ---------------------------------------------------------------------------
  const PIPELINE_ZONES = [
    'DISCOVERED', 'BACKTESTING', 'VALIDATING', 'OOS_TESTING',
    'ROBUSTNESS_TESTING', 'VALIDATED', 'SHADOW', 'ACTIVE',
  ];
  const TERMINAL_ZONES = ['REJECTED', 'DEGRADED', 'RETIRED'];
  const ALL_ZONES = PIPELINE_ZONES.concat(TERMINAL_ZONES);

  // Distinct hue per state so zones are distinguishable without relying on color
  // alone (each state is also labelled as text in its band header).
  const ZONE_COLORS = {
    DISCOVERED: '#64748b',         // slate
    BACKTESTING: '#0ea5e9',         // sky
    VALIDATING: '#06b6d4',          // cyan
    OOS_TESTING: '#14b8a6',         // teal
    ROBUSTNESS_TESTING: '#22c55e',  // green
    VALIDATED: '#84cc16',           // lime
    SHADOW: '#eab308',              // amber
    ACTIVE: '#10b981',              // emerald (live)
    REJECTED: '#f43f5e',           // rose
    DEGRADED: '#f97316',            // orange
    RETIRED: '#78716c',             // stone
  };

  // Which states count as "live" (actively trading) or "blocked" (ineligible).
  const LIVE_STATES = new Set(['ACTIVE']);
  const BLOCKED_STATES = new Set(['REJECTED', 'DEGRADED', 'RETIRED']);

  let canvas = null;
  let ctx = null;
  let nodes = [];               // current authoritative node list (internal models)
  let zoneOrder = ALL_ZONES;    // authoritative zone ordering
  let anims = {};               // strategy_id -> {fx,fy,tx,ty,t0,dur}
  let trails = {};              // strategy_id -> [{x,y,t}]
  let camera = { x: 0, y: 0, zoom: 1 };
  let camAnim = null;           // {fromX,fromY,fromZ,toX,toY,toZ,t0,dur}
  let selectedId = null;
  let raf = null;
  let dpr = 1;
  let lastPayload = null;       // authoritative snapshot, used for refresh recovery
  let onSelectionCb = null;     // wired by ui.js for inspector linkage

  // ---------- coordinate transforms -----------------------------------------
  function worldToScreen(wx, wy) {
    return [
      (wx - camera.x) * camera.zoom + canvas.width / (2 * dpr),
      (wy - camera.y) * camera.zoom + canvas.height / (2 * dpr),
    ];
  }

  // Note: drawing is done in device pixels; we keep world math in CSS px and
  // scale by dpr at the transform stage. screenToWorld works in CSS px.
  function screenToWorld(sx, sy) {
    return [
      sx / camera.zoom + camera.x,
      sy / camera.zoom + camera.y,
    ];
  }

  // ---------- initialization -------------------------------------------------
  function initSpatialCanvas(canvasId) {
    canvas = document.getElementById(canvasId);
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    resize();
    window.addEventListener('resize', resize);
    attachControls();
    startLoop();
  }

  function resize() {
    if (!canvas) return;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    const rect = canvas.parentElement.getBoundingClientRect();
    const cssW = Math.max(400, rect.width || 800);
    const cssH = Math.max(300, rect.height || 500);
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssH + 'px';
  }

  // ---------- camera controls -------------------------------------------------
  function setCamera(x, y, zoom, animate) {
    zoom = Math.min(4, Math.max(0.25, zoom));
    if (animate) {
      camAnim = {
        fromX: camera.x, fromY: camera.y, fromZ: camera.zoom,
        toX: x, toY: y, toZ: zoom,
        t0: performance.now(), dur: 420,
      };
    } else {
      camera.x = x; camera.y = y; camera.zoom = zoom;
    }
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function stepCameraAnim(now) {
    if (!camAnim) return;
    const p = camAnim;
    const raw = (now - p.t0) / p.dur;
    if (raw >= 1) {
      camera.x = p.toX; camera.y = p.toY; camera.zoom = p.toZ;
      camAnim = null;
      return;
    }
    const t = easeInOutCubic(raw);
    camera.x = p.fromX + (p.toX - p.fromX) * t;
    camera.y = p.fromY + (p.toY - p.fromY) * t;
    camera.zoom = p.fromZ + (p.toZ - p.fromZ) * t;
  }

  function focusOn(tx, ty, zoom) {
    const vw = canvas.width / dpr;
    const vh = canvas.height / dpr;
    setCamera(
      tx - (vw / 2) / zoom,
      ty - (vh / 2) / zoom,
      zoom || 1.4,
      true
    );
  }

  // ---------- payload ingestion + reconciliation ------------------------------
  function updateFromPayload(payload) {
    if (!payload || !payload.nodes) return;
    lastPayload = payload;
    zoneOrder = (payload.zones || []).map(z => z.zone).filter(Boolean);
    if (!zoneOrder.length) zoneOrder = ALL_ZONES;
    const incoming = payload.nodes;

    // Maturity rank for vertical placement (pipeline order; terminals at bottom).
    const rank = {};
    zoneOrder.forEach((z, i) => { rank[z] = i; });

    // Compute authoritative target screen-independent world coords.
    // The backend already supplies x (lateral) and y (zone row index * spacing).
    // We place nodes in a world where y grows downward by MATURITY_RANK and x is
    // the backend's lateral coordinate. This keeps the layout stable and lets the
    // camera / fit-all reason about real extents.
    const zoneRowH = 120; // matches backend zone_spacing (world units)
    const incomingById = {};
    for (const n of incoming) incomingById[n.strategy_id] = n;

    const prevById = {};
    for (const o of nodes) prevById[o.strategy_id] = o;

    const next = [];
    for (const n of incoming) {
      const zi = rank[n.zone] !== undefined ? rank[n.zone] : 0;
      const targetX = (n.x || 0);
      const targetY = zi * zoneRowH + (n.y || 0);

      const prev = prevById[n.strategy_id];
      const model = {
        strategy_id: n.strategy_id,
        zone: n.zone,
        size_hint: n.size_hint || 0,
        ring_count: n.ring_count || 0,
        elevation: (n.elevation === null || n.elevation === undefined) ? null : n.elevation,
        confidence: n.confidence,
        _tx: targetX,
        _ty: targetY,
        _color: ZONE_COLORS[n.zone] || '#94a3b8',
        _terminal: BLOCKED_STATES.has(n.zone),
      };

      if (prev && prev.zone !== model.zone) {
        // REAL transition: animate from the previous rendered position to the new
        // authoritative target. Never a teleport.
        anims[n.strategy_id] = {
          fx: prev._sx !== undefined ? prev._sx : prev._tx,
          fy: prev._sy !== undefined ? prev._sy : prev._ty,
          tx: targetX, ty: targetY,
          t0: performance.now(), dur: 900,
        };
        if (!trails[n.strategy_id]) trails[n.strategy_id] = [];
        trails[n.strategy_id].push({ x: targetX, y: targetY, t: Date.now() });
        if (trails[n.strategy_id].length > 12) trails[n.strategy_id].shift();
      }
      // BACKEND WINS: if an in-flight animation's target was superseded by a
      // newer snapshot with a different zone, overwrite the target and re-ease.
      else if (anims[n.strategy_id]) {
        const a = anims[n.strategy_id];
        if (a.tx !== targetX || a.ty !== targetY) {
          a.fx = a.fx !== undefined ? a.fx : targetX;
          a.fy = a.fy !== undefined ? a.fy : targetY;
          a.tx = targetX; a.ty = targetY; a.t0 = performance.now();
        }
      }
      next.push(model);
    }
    nodes = next;
  }

  // ---------- input handling --------------------------------------------------
  function attachControls() {
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      const [wx, wy] = screenToWorld(sx, sy);
      const newZoom = Math.min(4, Math.max(0.25, camera.zoom * factor));
      // Keep cursor anchored to the same world point.
      camera.x = wx - sx / newZoom;
      camera.y = wy - sy / newZoom;
      camera.zoom = newZoom;
      camAnim = null;
    }, { passive: false });

    let dragging = false, lastX = 0, lastY = 0, moved = false;
    canvas.addEventListener('mousedown', (ev) => {
      dragging = true; moved = false;
      lastX = ev.clientX; lastY = ev.clientY;
    });
    window.addEventListener('mouseup', () => { dragging = false; });
    window.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      camera.x -= dx / camera.zoom;
      camera.y -= dy / camera.zoom;
      camAnim = null;
      lastX = ev.clientX; lastY = ev.clientY;
    });

    canvas.addEventListener('click', (ev) => {
      if (moved) return; // was a drag, not a click
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      const [wx, wy] = screenToWorld(sx, sy);
      let best = null, bestDist = 18 / camera.zoom;
      for (const n of nodes) {
        const d = Math.hypot(n._sx - wx, n._sy - wy);
        if (d < bestDist) { best = n; bestDist = d; }
      }
      if (best) {
        selectedId = best.strategy_id;
        if (onSelectionCb) onSelectionCb(best.strategy_id);
      }
    });
  }

  // ---------- drawing ---------------------------------------------------------
  function draw(now) {
    if (!ctx) return;
    stepCameraAnim(now);

    const w = canvas.width, h = canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, w, h);

    // World transform: scale by dpr (device px) and zoom; translate by camera.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.translate(canvas.width / (2 * dpr), canvas.height / (2 * dpr));
    ctx.scale(camera.zoom, camera.zoom);
    ctx.translate(-camera.x, -camera.y);

    const zoneRowH = 120;
    const rank = {};
    zoneOrder.forEach((z, i) => { rank[z] = i; });

    // ---- Zone bands ----
    const firstTerminalRank = PIPELINE_ZONES.length;
    zoneOrder.forEach((z) => {
      const i = rank[z];
      const y = i * zoneRowH;
      const isTerminal = TERMINAL_ZONES.includes(z);
      const color = ZONE_COLORS[z] || '#94a3b8';

      // Band background — terminal states get a faint red-tinted wash so they
      // read as "inactive" structurally, not only by color.
      ctx.fillStyle = isTerminal ? 'rgba(60, 30, 36, 0.45)' : 'rgba(30, 41, 59, 0.40)';
      ctx.fillRect(-4000, y + 8, 8000, zoneRowH - 16);
      ctx.strokeStyle = 'rgba(51, 65, 85, 0.9)';
      ctx.lineWidth = 1;
      ctx.strokeRect(-4000, y, 8000, zoneRowH);

      // Zone header label (always present — state is clear without color).
      ctx.fillStyle = color;
      ctx.font = 'bold 12px ui-sans-serif, system-ui, sans-serif';
      ctx.fillText(`${z}  (${countForZone(z)})`, -3980, y + 22);

      // EXECUTION BOUNDARY marker above ACTIVE (the live-trading gate).
      if (z === 'ACTIVE') {
        ctx.strokeStyle = 'rgba(16, 185, 129, 0.85)';
        ctx.setLineDash([10, 6]);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-4000, y + 2);
        ctx.lineTo(4000, y + 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.95)';
        ctx.font = 'bold 10px ui-monospace, monospace';
        ctx.fillText('── EXECUTION GATE (shadow → live boundary) ──', -3980, y - 6);
      }

      // Divider above the terminal region.
      if (i === firstTerminalRank && firstTerminalRank < zoneOrder.length) {
        ctx.strokeStyle = 'rgba(244, 63, 94, 0.55)';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(-4000, y);
        ctx.lineTo(4000, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(244, 63, 94, 0.85)';
        ctx.font = 'bold 10px ui-monospace, monospace';
        ctx.fillText('── INACTIVE / TERMINAL (never live) ──', -3980, y - 6);
      }
    });

    // ---- Trails ----
    const cutoff = Date.now() - 15000;
    for (const sid in trails) {
      const pts = trails[sid].filter(p => p.t > cutoff || sid === selectedId);
      if (pts.length < 2) continue;
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.35)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k].x, pts[k].y);
      ctx.stroke();
    }

    // ---- LOD selection ----
    // zoomed-out: minimal dots only (no rings/labels/stems)
    // medium:     + labels + stems
    // zoomed-in:  + rings + metadata text
    const z = camera.zoom;
    const lod = z < 0.55 ? 'low' : (z < 1.1 ? 'mid' : 'high');

    // ---- Nodes ----
    for (const n of nodes) {
      let x = n._tx, y = n._ty;
      const a = anims[n.strategy_id];
      if (a) {
        const rawT = (now - a.t0) / a.dur;
        if (rawT >= 1) delete anims[n.strategy_id];
        else {
          const t = easeInOutCubic(Math.max(0, rawT));
          x = a.fx + (a.tx - a.fx) * t;
          y = a.fy + (a.ty - a.fy) * t;
        }
      }
      n._sx = x; n._sy = y;

      const baseR = 5 + Math.min(9, (n.size_hint || 0) / 60); // size = evidence volume
      const r = baseR;
      const isSelected = n.strategy_id === selectedId;
      const isLive = LIVE_STATES.has(n.zone);
      const isShadow = n.zone === 'SHADOW';
      const pulse = (isLive || isShadow) ? (0.5 + 0.5 * Math.sin(now / 320 + n._tx)) : 0;

      // Pulsing halo for active processing (live/shadow).
      if (pulse > 0 && lod !== 'low') {
        ctx.beginPath();
        ctx.arc(x, y, r + 4 + pulse * 5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(16, 185, 129, ${0.10 + pulse * 0.18})`;
        ctx.fill();
      }

      // Validation rings (depth = passed gates). Drawn at high/mid LOD.
      if (lod !== 'low') {
        const rings = Math.min(4, n.ring_count || 0);
        for (let k = 0; k < rings; k++) {
          ctx.beginPath();
          ctx.arc(x, y, r + 3 + k * 3, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(132, 204, 22, 0.35)';
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }

      // Node body — terminal states render as SQUARES (structural cue), pipeline
      // as discs.
      ctx.fillStyle = n._color;
      ctx.beginPath();
      if (n._terminal) {
        ctx.rect(x - r, y - r, r * 2, r * 2);
      } else {
        ctx.arc(x, y, r, 0, Math.PI * 2);
      }
      ctx.fill();
      ctx.lineWidth = isSelected ? 2.5 : 1;
      ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(15, 23, 42, 0.9)';
      ctx.stroke();

      // Elevation stem (health maturity). "?" when NOT MEASURED.
      if (lod !== 'low') {
        if (n.elevation === null || n.elevation === undefined) {
          ctx.fillStyle = 'rgba(148,163,184,0.85)';
          ctx.font = '8px ui-monospace, monospace';
          ctx.fillText('?', x - 2, y - r - 4);
        } else {
          const eh = Math.max(3, n.elevation * 16);
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.55)';
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(x, y - r);
          ctx.lineTo(x, y - r - eh);
          ctx.stroke();
        }
      }

      // Label at mid/high LOD or when selected.
      if (lod !== 'low' || isSelected) {
        ctx.fillStyle = isSelected ? '#ffffff' : '#cbd5e1';
        ctx.font = (isSelected ? 'bold ' : '') + '9px ui-monospace, monospace';
        const label = String(n.strategy_id).substring(0, 14);
        ctx.fillText(label, x + r + 3, y + 3);
      }
    }

    // Reset transform for any future HUD draws.
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  function countForZone(zone) {
    let c = 0;
    for (const n of nodes) if (n.zone === zone) c++;
    return c;
  }

  function startLoop() {
    function frame(now) {
      draw(now);
      raf = requestAnimationFrame(frame);
    }
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
  }

  // ---------- camera helpers --------------------------------------------------
  function visibleBounds(liveOnly) {
    const list = liveOnly ? nodes.filter(n => LIVE_STATES.has(n.zone)) : nodes;
    if (!list.length) return null;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of list) {
      minX = Math.min(minX, n._tx); maxX = Math.max(maxX, n._tx);
      minY = Math.min(minY, n._ty); maxY = Math.max(maxY, n._ty);
    }
    return { minX, maxX, minY, maxY };
  }

  function computeFit(bounds) {
    if (!bounds) return null;
    const vw = canvas.width / dpr;
    const vh = canvas.height / dpr;
    const padX = 80, padY = 80;
    const w = Math.max(1, bounds.maxX - bounds.minX + padX);
    const h = Math.max(1, bounds.maxY - bounds.minY + padY);
    const zoom = Math.min(2, Math.max(0.25, Math.min(vw / w, vh / h)));
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cy = (bounds.minY + bounds.maxY) / 2;
    return {
      x: cx - (vw / 2) / zoom,
      y: cy - (vh / 2) / zoom,
      zoom,
    };
  }

  // ---------- public API ------------------------------------------------------
  window.NX = window.NX || {};
  window.NX.spatial = {
    init: initSpatialCanvas,
    update: updateFromPayload,
    // Called by ui.js once the payload is authoritative + a tab is visible.
    fitAll() {
      const b = visibleBounds(false);
      if (!b) return false;
      const fit = computeFit(b);
      if (fit) setCamera(fit.x, fit.y, fit.zoom, true);
      return true;
    },
    resetCamera() { setCamera(0, 0, 1, true); },
    focusSelected() {
      const n = nodes.find(o => o.strategy_id === selectedId);
      if (n) focusOn(n._tx, n._ty, 1.6);
    },
    // Focus the ACTIVE (live) region of the pipeline.
    focusStage() {
      const b = visibleBounds(false);
      if (!b) return;
      // Stage = pipeline region (top -> bottom of SHADOW/ACTIVE), not terminal.
      let minY = Infinity, maxY = -Infinity;
      for (const n of nodes) {
        const rankN = zoneOrder.indexOf(n.zone);
        if (rankN >= 0 && rankN < PIPELINE_ZONES.length) {
          minY = Math.min(minY, n._ty); maxY = Math.max(maxY, n._ty);
        }
      }
      if (minY === Infinity) { this.fitAll(); return; }
      const fit = computeFit({ minX: b.minX, maxX: b.maxX, minY, maxY });
      if (fit) setCamera(fit.x, fit.y, fit.zoom, true);
    },
    focusBlocked() {
      const blocked = nodes.filter(n => BLOCKED_STATES.has(n.zone));
      if (!blocked.length) { return; }
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of blocked) {
        minX = Math.min(minX, n._tx); maxX = Math.max(maxX, n._tx);
        minY = Math.min(minY, n._ty); maxY = Math.max(maxY, n._ty);
      }
      const fit = computeFit({ minX, maxX, minY, maxY });
      if (fit) setCamera(fit.x, fit.y, fit.zoom, true);
    },
    focusActive() {
      const live = nodes.filter(n => LIVE_STATES.has(n.zone));
      if (!live.length) { return; }
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of live) {
        minX = Math.min(minX, n._tx); maxX = Math.max(maxX, n._tx);
        minY = Math.min(minY, n._ty); maxY = Math.max(maxY, n._ty);
      }
      const fit = computeFit({ minX, maxX, minY, maxY });
      if (fit) setCamera(fit.x, fit.y, fit.zoom, true);
    },
    select(id) { selectedId = id; },
    setOnSelect(cb) { onSelectionCb = cb; },
    // Test/QA hooks (harmless in production).
    _test: {
      getNodes: () => nodes,
      getAnims: () => anims,
      getCamera: () => ({ ...camera }),
      setCamera(x, y, zoom) { setCamera(x, y, zoom, false); },
      getZones: () => zoneOrder,
      hasPayload: () => !!lastPayload,
    },
  };
})();
