/**
 * Command Center Spatial Renderer — 2.5D zones + animated strategy movement
 * -------------------------------------------------------------------------
 * Consumes the authoritative spatial payload from /api/command-center/spatial
 * and renders a GPU-accelerated Canvas2D 2.5D environment:
 *
 *   - Lifecycle zones as horizontal depth bands (DISCOVERED → ACTIVE)
 *   - Strategy nodes positioned by the backend SpatialLayout engine
 *   - Animated transitions when a node's zone changes between refreshes
 *     (interpolated path animation; never a teleport)
 *   - Transition trails (fading polyline of recent moves)
 *   - Camera system: pan (drag), zoom (wheel), fit-all, focus selected
 *   - LOD: labels/rings/metadata appear progressively with zoom
 *   - Selection + inspector integration via window.NX.scc.inspect()
 *
 * TRUTH RULES:
 *   - A node's zone is ONLY what the backend reports. Local animation is
 *     cosmetic; every authoritative refresh reconciles visual state.
 *   - Missing data (health/elevation null) renders as "NOT MEASURED" marker,
 *     never a fabricated value.
 */
(function () {
  'use strict';

  const ZONE_COLORS = {
    DISCOVERED: '#64748b',
    BACKTESTING: '#0ea5e9',
    VALIDATING: '#06b6d4',
    OOS_TESTING: '#14b8a6',
    ROBUSTNESS_TESTING: '#22c55e',
    VALIDATED: '#84cc16',
    SHADOW: '#eab308',
    ACTIVE: '#10b981',
    REJECTED: '#f43f5e',
    DEGRADED: '#f97316',
    RETIRED: '#78716c',
  };

  let canvas = null;
  let ctx = null;
  let nodes = [];
  let zones = [];
  let anims = {};       // strategy_id -> {fromX, fromY, toX, toY, t0, dur}
  let trails = {};      // strategy_id -> [{x, y, t}]
  let camera = { x: 0, y: 0, zoom: 1 };
  let selectedId = null;
  let raf = null;

  function worldToScreen(wx, wy) {
    return [
      (wx - camera.x) * camera.zoom,
      (wy - camera.y) * camera.zoom,
    ];
  }

  function screenToWorld(sx, sy) {
    return [sx / camera.zoom + camera.x, sy / camera.zoom + camera.y];
  }

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
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = Math.max(400, rect.width || 800);
    canvas.height = Math.max(300, rect.height || 500);
  }

  function attachControls() {
    // Wheel zoom
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      const [wx, wy] = screenToWorld(ev.offsetX, ev.offsetY);
      camera.zoom = Math.min(4, Math.max(0.35, camera.zoom * factor));
      // Keep cursor anchored
      camera.x = wx - ev.offsetX / camera.zoom;
      camera.y = wy - ev.offsetY / camera.zoom;
    }, { passive: false });

    // Pan drag
    let dragging = false, lastX = 0, lastY = 0;
    canvas.addEventListener('mousedown', (ev) => { dragging = true; lastX = ev.clientX; lastY = ev.clientY; });
    window.addEventListener('mouseup', () => { dragging = false; });
    window.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      camera.x -= (ev.clientX - lastX) / camera.zoom;
      camera.y -= (ev.clientY - lastY) / camera.zoom;
      lastX = ev.clientX; lastY = ev.clientY;
    });

    // Click selection
    canvas.addEventListener('click', (ev) => {
      const [wx, wy] = screenToWorld(ev.offsetX, ev.offsetY);
      let best = null, bestDist = 18 / camera.zoom;
      for (const n of nodes) {
        const d = Math.hypot(n._sx - wx, n._sy - wy);
        if (d < bestDist) { best = n; bestDist = d; }
      }
      if (best) {
        selectedId = best.strategy_id;
        if (window.NX && window.NX.scc) window.NX.scc.inspect(best.strategy_id);
      }
    });
  }

  function updateFromPayload(payload) {
    if (!payload || !payload.nodes) return;
    zones = payload.zones || [];
    const incoming = payload.nodes;

    const zoneIndex = {};
    zones.forEach((z, i) => { zoneIndex[z.zone] = i; });

    const zoneCountH = canvas ? canvas.height / Math.max(1, zones.length) : 100;

    for (const n of incoming) {
      const zi = zoneIndex[n.zone] !== undefined ? zoneIndex[n.zone] : 0;
      const targetX = (canvas.width / 2) + (n.x || 0) * 1.2;
      const targetY = zi * zoneCountH + zoneCountH / 2 + ((n.y || 0) % zoneCountH) * 0.3;

      const prev = nodes.find(o => o.strategy_id === n.strategy_id);
      if (prev && prev.zone !== n.zone) {
        // REAL transition: animate from previous position to new one.
        anims[n.strategy_id] = {
          fx: prev._tx, fy: prev._ty,
          tx: targetX, ty: targetY,
          t0: performance.now(),
          dur: 900,
        };
        if (!trails[n.strategy_id]) trails[n.strategy_id] = [];
        trails[n.strategy_id].push({ x: targetX, y: targetY, t: Date.now() });
        if (trails[n.strategy_id].length > 12) trails[n.strategy_id].shift();
      }
      n._tx = targetX; n._ty = targetY;
      n._color = ZONE_COLORS[n.zone] || '#94a3b8';
    }
    nodes = incoming;
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function draw(now) {
    if (!ctx) return;
    const w = canvas.width, h = canvas.height;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, w, h);

    ctx.translate(-camera.x * camera.zoom, -camera.y * camera.zoom);
    ctx.scale(camera.zoom, camera.zoom);

    // ---- Zones ----
    const zh = h / Math.max(1, zones.length);
    zones.forEach((z, i) => {
      const y = i * zh;
      ctx.fillStyle = 'rgba(30, 41, 59, 0.45)';
      ctx.fillRect(20, y + 8, w - 40, zh - 16);
      ctx.strokeStyle = 'rgba(51, 65, 85, 0.9)';
      ctx.strokeRect(20, y + 8, w - 40, zh - 16);
      ctx.fillStyle = '#94a3b8';
      ctx.font = 'bold 11px ui-sans-serif, sans-serif';
      ctx.fillText(`${z.zone}  (${z.count})`, 32, y + 28);

      // EXECUTION GATE boundary before ACTIVE
      if (z.zone === 'ACTIVE') {
        ctx.strokeStyle = 'rgba(16, 185, 129, 0.7)';
        ctx.setLineDash([8, 5]);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(20, y + 4);
        ctx.lineTo(w - 20, y + 4);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.95)';
        ctx.font = 'bold 10px monospace';
        ctx.fillText('── EXECUTION BOUNDARY ──', w / 2 - 80, y - 2);
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
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
      ctx.stroke();
    }

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

      const r = 6 + Math.min(8, (n.size_hint || 0) / 40);
      const isSelected = n.strategy_id === selectedId;

      // Validation rings (completed gates)
      const rings = Math.min(4, n.ring_count || 0);
      for (let k = 0; k < rings; k++) {
        ctx.beginPath();
        ctx.arc(x, y, r + 3 + k * 3, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(132, 204, 22, 0.35)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = n._color;
      ctx.fill();
      if (isSelected) {
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = '#ffffff';
      } else {
        ctx.lineWidth = 1;
        ctx.strokeStyle = 'rgba(15, 23, 42, 0.9)';
      }
      ctx.stroke();

      // Elevation hint (health): small vertical stem; NOT MEASURED marker when absent
      if (n.elevation === null || n.elevation === undefined) {
        ctx.fillStyle = 'rgba(148,163,184,0.8)';
        ctx.font = '8px monospace';
        ctx.fillText('?', x - 2, y - r - 4);
      } else {
        const eh = Math.max(2, n.elevation * 14);
        ctx.strokeStyle = 'rgba(56, 189, 248, 0.5)';
        ctx.beginPath();
        ctx.moveTo(x, y - r);
        ctx.lineTo(x, y - r - eh);
        ctx.stroke();
      }

      // LOD: label only at sufficient zoom or selection
      if (camera.zoom > 0.75 || isSelected) {
        ctx.fillStyle = '#cbd5e1';
        ctx.font = '9px ui-monospace, monospace';
        const label = String(n.strategy_id).substring(0, 12);
        ctx.fillText(label, x + r + 3, y + 3);
      }
    }
  }

  function startLoop() {
    function frame(now) {
      draw(now);
      raf = requestAnimationFrame(frame);
    }
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
  }

  // ---- Public API ----
  window.NX = window.NX || {};
  window.NX.spatial = {
    init: initSpatialCanvas,
    update: updateFromPayload,
    focusSelected() {
      const n = nodes.find(o => o.strategy_id === selectedId);
      if (n) { camera.x = n._tx - canvas.width / (2 * camera.zoom); camera.y = n._ty - canvas.height / (2 * camera.zoom); }
    },
    fitAll() {
      if (!nodes.length) return;
      const xs = nodes.map(n => n._tx), ys = nodes.map(n => n._ty);
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      const minY = Math.min(...ys), maxY = Math.max(...ys);
      const pad = 60;
      camera.zoom = Math.min(
        2,
        Math.max(0.35, Math.min(canvas.width / (maxX - minX + pad), canvas.height / (maxY - minY + pad)))
      );
      camera.x = (minX + maxX) / 2 - canvas.width / (2 * camera.zoom);
      camera.y = (minY + maxY) / 2 - canvas.height / (2 * camera.zoom);
    },
    resetCamera() { camera = { x: 0, y: 0, zoom: 1 }; },
  };
})();
