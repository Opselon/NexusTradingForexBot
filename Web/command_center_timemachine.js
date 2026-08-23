/* =========================================================================
 * NEXUS COMMAND CENTER — Historical Time Machine (Frontend Replay)
 * -------------------------------------------------------------------------
 * Consumes /api/command-center/timemachine/bounds and frame
 * to drive historical fleet playback, scrubbing, play/pause, and step.
 * ========================================================================= */

(function () {
  'use strict';

  let playing = false;
  let currentMs = 0;
  let minMs = 0;
  let maxMs = Date.now();
  let stepMs = 3600000; // 1 hour steps by default
  let timer = null;

  async function initTimeMachine() {
    if (!window.NX || !window.NX.api) return;
    try {
      const res = await window.NX.api.get('/api/command-center/timemachine/bounds', { component: 'tm', action: 'bounds' });
      if (res.ok && res.body.available) {
        minMs = new Date(res.body.earliest).getTime();
        maxMs = new Date(res.body.latest).getTime() || Date.now();
        currentMs = minMs;
        const slider = document.getElementById('scc-tm-slider');
        if (slider) {
          slider.min = minMs;
          slider.max = maxMs;
          slider.value = currentMs;
        }
        updateTimeLabel();
      }
    } catch (err) {
      console.warn('[TM] init bounds failed', err);
    }
  }

  async function fetchFrame(ms) {
    const iso = new Date(Number(ms)).toISOString();
    try {
      const res = await window.NX.api.get(`/api/command-center/timemachine/frame?at=${encodeURIComponent(iso)}`, { component: 'tm', action: 'frame' });
      if (res.ok && res.body.available && window.NX.spatial) {
        // Map replay frame nodes into the spatial engine format
        const frameNodes = (res.body.nodes || []).map(n => ({
          strategy_id: n.strategy_id,
          zone: n.zone,
          x: 0, y: 0,
          size_hint: 10,
          ring_count: 2,
          elevation: 0.5,
        }));
        window.NX.spatial.update({ zones: [], nodes: frameNodes });
      }
    } catch (err) {
      console.warn('[TM] frame fetch failed', err);
    }
  }

  function updateTimeLabel() {
    const lbl = document.getElementById('scc-tm-label');
    if (lbl) lbl.textContent = new Date(Number(currentMs)).toUTCString();
  }

  function scrub(val) {
    currentMs = Number(val);
    updateTimeLabel();
    fetchFrame(currentMs);
  }

  function togglePlay() {
    playing = !playing;
    const btn = document.getElementById('scc-tm-play');
    if (btn) btn.textContent = playing ? 'PAUSE' : 'PLAY';
    if (playing) {
      timer = setInterval(() => {
        currentMs += stepMs;
        if (currentMs > maxMs) {
          currentMs = minMs;
          togglePlay();
          return;
        }
        const slider = document.getElementById('scc-tm-slider');
        if (slider) slider.value = currentMs;
        updateTimeLabel();
        fetchFrame(currentMs);
      }, 400);
    } else {
      if (timer) clearInterval(timer);
    }
  }

  window.NX = window.NX || {};
  window.NX.tm = {
    init: initTimeMachine,
    scrub,
    togglePlay,
  };
})();
