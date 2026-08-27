/* Dependency Intelligence — UI controller (state, filters, inspector, explorers).
 * Pulls from window.NXDependency.api and window.NXDependencyGraph. No build step.
 */
(function () {
  "use strict";
  var api = window.NXDependency.api;
  var GraphCtor = window.NXDependencyGraph;

  var state = {
    summary: null,
    graph: null,
    cycles: [],
    violations: [],
    selectedNode: null,
    filters: { layer: "", nodeType: "", edgeType: "", cycleOnly: false, unresolvedOnly: false, criticalOnly: false, hotspotOnly: false, diOnly: false, registrationsOnly: false },
    loading: false,
    error: null,
    lastUpdated: null,
    autoRefreshTimer: null,
  };

  var els = {};
  function $(id) { return document.getElementById(id); }

  function showError(msg) {
    var e = $("global-error-overlay");
    if (!e) return;
    if (msg) {
      e.textContent = msg;
      e.classList.remove("hidden");
    } else {
      e.classList.add("hidden");
    }
  }

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms || 250); };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === '"' ? "&quot;" : "&#39;";
    });
  }

  function setLoading(loading) {
    state.loading = loading;
    var overlay = $("graph-loading-overlay");
    var globalOverlay = $("global-loading-overlay");
    if (overlay) {
      if (loading) overlay.classList.remove("hidden");
      else overlay.classList.add("hidden");
    }
    if (globalOverlay) {
      if (loading && !state.graph) globalOverlay.classList.remove("hidden");
      else globalOverlay.classList.add("hidden");
    }
  }

  // ---- bootstrap ----
  function init() {
    [
      "search-input", "btn-fit", "btn-reset", "btn-zoom-in", "btn-zoom-out",
      "filter-layer", "filter-node-type", "filter-edge-type",
      "filter-cycle", "filter-unresolved", "filter-critical",
      "filter-hotspot", "filter-di", "filter-registrations",
      "graph-svg", "inspector-content", "btn-inspector-collapse",
      "btn-refresh", "cb-auto-refresh", "btn-focus", "btn-path", "btn-impact",
      "selection-info", "graph-status", "graph-info-left", "graph-info-right",
      "scan-timestamp", "api-status", "graph-version-number",
      "health-status-text", "health-status-chip",
      "metric-cycles", "metric-unresolved", "metric-violations", "metric-di-reg", "metric-hotspots",
      "inspector-title", "inspector-overview", "inspector-node-view",
      "node-identity", "node-kind", "node-layer", "node-criticality",
      "node-metrics", "node-dependencies", "node-dependents", "node-evidence",
      "overview-health", "overview-filters", "overview-hotspots", "overview-cycles",
      "btn-show-cycles", "btn-show-unresolved", "btn-show-critical",
      "legend-content"
    ].forEach(function (id) { els[id] = $(id); });

    state.graph = new GraphCtor(els["graph-svg"]);
    state.graph.onSelect = onNodeSelect;

    bindEvents();
    renderLegend();
    loadAll();
  }

  function bindEvents() {
    els["btn-fit"].onclick = function () { state.graph.fit(); };
    els["btn-reset"].onclick = resetFilters;
    els["btn-zoom-in"].onclick = function () { state.graph._view.k = Math.min(3, state.graph._view.k * 1.2); state.graph._applyView(); };
    els["btn-zoom-out"].onclick = function () { state.graph._view.k = Math.max(0.3, state.graph._view.k * 0.8); state.graph._applyView(); };
    
    els["search-input"].addEventListener("input", debounce(function() {
      applyFilters();
      showSearchResults();
    }, 250));
    
    ["filter-layer", "filter-node-type", "filter-edge-type"].forEach(function (id) {
      els[id].addEventListener("change", applyFilters);
    });
    ["filter-cycle", "filter-unresolved", "filter-critical", "filter-hotspot", "filter-di", "filter-registrations"].forEach(function (id) {
      els[id].addEventListener("change", applyFilters);
    });

    els["btn-refresh"].onclick = function() { loadAll(); };
    els["cb-auto-refresh"].addEventListener("change", function() {
      if (this.checked) startAutoRefresh();
      else stopAutoRefresh();
    });

    els["btn-focus"].onclick = function() { focusSelectedNode(); };
    els["btn-path"].onclick = function() { 
      var node = state.fullNodes.find(function (n) { return n.id === state.selectedNode; });
      if (node) els["search-input"].value = node.qualified_name;
    };
    els["btn-impact"].onclick = function() { runImpact(state.selectedNode); };

    els["btn-inspector-collapse"].onclick = function() {
      var right = $("right-inspector");
      if (right) {
        if (right.style.width === "0px") {
          right.style.width = "320px";
          this.textContent = "−";
        } else {
          right.style.width = "0px";
          right.style.padding = "0";
          this.textContent = "+";
        }
      }
    };

    els["btn-show-cycles"].onclick = function() { els["filter-cycle"].checked = true; applyFilters(); };
    els["btn-show-unresolved"].onclick = function() { els["filter-unresolved"].checked = true; applyFilters(); };
    els["btn-show-critical"].onclick = function() { els["filter-critical"].checked = true; applyFilters(); };

    // Keyboard shortcuts
    document.addEventListener("keydown", function(e) {
      if (e.key === "/" && !e.target.matches("input, textarea")) {
        e.preventDefault();
        els["search-input"].focus();
      } else if (e.key === "Escape") {
        clearSelection();
      } else if (e.key === "f" || e.key === "F") {
        if (!e.target.matches("input, textarea")) state.graph.fit();
      } else if (e.key === "r" || e.key === "R") {
        if (!e.target.matches("input, textarea")) resetFilters();
      }
    });
  }

  function startAutoRefresh() {
    state.autoRefreshTimer = setInterval(function() { loadAll(true); }, 30000);
  }

  function stopAutoRefresh() {
    if (state.autoRefreshTimer) {
      clearInterval(state.autoRefreshTimer);
      state.autoRefreshTimer = null;
    }
  }

  function loadAll(isBackground) {
    if (!isBackground) setLoading(true);
    showError(null);
    api.summary().then(function (r) {
      if (!r.ok) { showError("Summary failed: " + r.error + " (HTTP " + r.status + ")"); return r; }
      state.summary = r.data;
      renderHeader(r.data);
      renderHealthStrip(r.data);
      return api.graph();
    }).then(function (r) {
      if (!r || !r.ok) { if (r && !r.ok) showError("Graph failed: " + r.error); return; }
      state.graph_data = r.data;
      populateFilters(r.data);
      drawGraph(r.data);
      return api.cycles();
    }).then(function (r) {
      if (!r || !r.ok) return;
      state.cycles = r.data.cycles || [];
      renderCyclesPanel(state.cycles);
      return api.violations();
    }).then(function (r) {
      if (!r || !r.ok) return;
      state.violations = r.data.violations || [];
      renderViolationsPanel(state.violations);
      if (!state.selectedNode) renderInspectorOverview();
      setLoading(false);
      state.lastUpdated = new Date();
    }).catch(function (err) {
      setLoading(false);
      showError("Failed to load dependency data: " + (err && err.message ? err.message : err));
    });
  }

  function renderHeader(s) {
    if (!s) return;
    els["scan-timestamp"].textContent = "Scan: " + (s.generated_at || "—");
    els["graph-version-number"].textContent = s.analyzer_version || "—";
    els["api-status"].textContent = "API: OK";
  }

  function renderHealthStrip(s) {
    if (!s) return;
    var repo = s.repository || {}, health = s.health || {};
    var healthStatus = "OK";
    var healthClass = "chip-severity-ok";
    if (health.cycles > 0 || health.unresolved_imports > 0 || health.architecture_violations > 0) {
      healthStatus = "DEGRADED";
      healthClass = "chip-severity-high";
    }
    if (health.architecture_violations > 0) {
      healthStatus = "CRITICAL";
      healthClass = "chip-severity-critical";
    }
    els["health-status-text"].textContent = "HEALTH: " + healthStatus;
    els["health-status-chip"].textContent = healthStatus;
    els["health-status-chip"].className = "px-2 py-0.5 text-xs " + healthClass;
    
    els["metric-cycles"].textContent = health.cycles || 0;
    els["metric-cycles"].className = "font-medium nx-chip " + (health.cycles > 0 ? "chip-severity-high" : "chip-severity-ok");
    
    els["metric-unresolved"].textContent = health.unresolved_imports || 0;
    els["metric-unresolved"].className = "font-medium nx-chip " + (health.unresolved_imports > 0 ? "chip-severity-high" : "chip-severity-ok");
    
    els["metric-violations"].textContent = health.architecture_violations || 0;
    els["metric-violations"].className = "font-medium nx-chip " + (health.architecture_violations > 0 ? "chip-severity-critical" : "chip-severity-ok");
    
    els["metric-di-reg"].textContent = repo.di_registrations || 0;
    els["metric-di-reg"].className = "font-medium nx-chip chip-severity-ok";
    
    els["metric-hotspots"].textContent = (s.hotspots || []).length;
    els["metric-hotspots"].className = "font-medium nx-chip " + ((s.hotspots || []).length > 5 ? "chip-severity-high" : "chip-severity-muted");
  }

  function renderLegend() {
    var legend = [
      { shape: "●", color: "#38bdf8", label: "Class / Module" },
      { shape: "●", color: "#fbbf24", label: "Runtime Critical" },
      { shape: "●", color: "#f87171", label: "Unresolved" },
      { shape: "●", color: "#64748b", label: "External" },
    ];
    var html = legend.map(function (item) {
      return '<div class="flex items-center gap-2"><span style="color:' + item.color + '">' + item.shape + '</span><span>' + item.label + '</span></div>';
    }).join("");
    html += '<div class="nx-divider"></div>';
    var edgeLegend = [
      { color: "#2b3a5e", label: "Import" },
      { color: "#38bdf8", label: "Injects" },
      { color: "#a78bfa", label: "Implements" },
      { color: "#34d399", label: "Registers" },
    ];
    html += edgeLegend.map(function (item) {
      return '<div class="flex items-center gap-2"><div style="width:12px;height:2px;background:' + item.color + '"></div><span>' + item.label + '</span></div>';
    }).join("");
    els["legend-content"].innerHTML = html;
  }

  function populateFilters(data) {
    var layers = {}, ntypes = {}, etypes = {};
    (data.nodes || []).forEach(function (n) { if (n.layer) layers[n.layer] = 1; if (n.kind) ntypes[n.kind] = 1; });
    (data.edges || []).forEach(function (e) { if (e.kind) etypes[e.kind] = 1; });
    fillSelect(els["filter-layer"], Object.keys(layers).sort());
    fillSelect(els["filter-node-type"], Object.keys(ntypes).sort());
    fillSelect(els["filter-edge-type"], Object.keys(etypes).sort());
  }
  
  function fillSelect(sel, vals) {
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    vals.forEach(function (v) {
      var o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o);
    });
  }

  function drawGraph(data) {
    var nodes = (data.nodes || []).slice();
    var edges = (data.edges || []).slice();
    state.fullNodes = nodes; state.fullEdges = edges;
    applyFilters();
  }

  function applyFilters() {
    if (!state.fullNodes) return;
    state.filters.layer = els["filter-layer"].value;
    state.filters.nodeType = els["filter-node-type"].value;
    state.filters.edgeType = els["filter-edge-type"].value;
    state.filters.cycleOnly = els["filter-cycle"].checked;
    state.filters.unresolvedOnly = els["filter-unresolved"].checked;
    state.filters.criticalOnly = els["filter-critical"].checked;
    state.filters.hotspotOnly = els["filter-hotspot"].checked;
    state.filters.diOnly = els["filter-di"].checked;
    state.filters.registrationsOnly = els["filter-registrations"].checked;
    var q = (els["search-input"].value || "").toLowerCase();

    // Build cycle member set
    var cycleMembers = {};
    if (state.filters.cycleOnly) {
      (state.cycles || []).forEach(function(c) {
        (c.path || []).forEach(function(nid) { cycleMembers[nid] = true; });
      });
    }

    var hotspotIds = {};
    if (state.summary && state.summary.hotspots) {
      state.summary.hotspots.forEach(function(h) { hotspotIds[h.node_id] = true; });
    }

    var visIds = {};
    var nodes = state.fullNodes.filter(function (n) {
      if (state.filters.layer && n.layer !== state.filters.layer) return false;
      if (state.filters.nodeType && n.kind !== state.filters.nodeType) return false;
      if (state.filters.unresolvedOnly && n.status !== "UNRESOLVED") return false;
      if (state.filters.criticalOnly && n.criticality !== "CRITICAL" && n.criticality !== "HIGH") return false;
      if (state.filters.hotspotOnly && !hotspotIds[n.id]) return false;
      if (state.filters.diOnly && n.kind !== "CLASS") return false;
      if (state.filters.registrationsOnly && !(n.metadata && n.metadata.di_registration)) return false;
      if (state.filters.cycleOnly && !cycleMembers[n.id]) return false;
      if (q && !(n.qualified_name || "").toLowerCase().includes(q) && !(n.display_name || "").toLowerCase().includes(q)) return false;
      visIds[n.id] = true;
      return true;
    });
    var edges = state.fullEdges.filter(function (e) {
      if (state.filters.edgeType && e.kind !== state.filters.edgeType) return false;
      return visIds[e.source] && visIds[e.target];
    });
    state.graph.setLayout(nodes, edges);
    if (state.selectedNode && visIds[state.selectedNode]) {
      state.graph.focus(state.selectedNode);
    }
    updateGraphInfo(nodes.length, edges.length);
  }

  function updateGraphInfo(nodeCount, edgeCount) {
    if (els["graph-info-left"]) {
      els["graph-info-left"].innerHTML = 
        '<span>Nodes: <span class="text-slate-200 font-medium">' + nodeCount + '</span></span>' +
        '<span>Edges: <span class="text-slate-200 font-medium">' + edgeCount + '</span></span>' +
        '<span>Zoom: <span class="text-slate-200 font-medium">' + (state.graph._view.k * 100).toFixed(0) + '%</span></span>';
    }
    if (els["graph-status"]) {
      var filters = [];
      if (state.filters.cycleOnly) filters.push("Cycles");
      if (state.filters.unresolvedOnly) filters.push("Unresolved");
      if (state.filters.criticalOnly) filters.push("Critical");
      if (state.filters.hotspotOnly) filters.push("Hotspots");
      var filterText = filters.length > 0 ? " (Filters: " + filters.join(", ") + ")" : "";
      els["graph-status"].textContent = "Rendering " + nodeCount + " nodes" + filterText;
    }
  }

  function showSearchResults() {
    var q = (els["search-input"].value || "").toLowerCase();
    if (!q || q.length < 2) return;
    var matches = (state.fullNodes || []).filter(function (n) {
      return (n.qualified_name || "").toLowerCase().includes(q) || (n.display_name || "").toLowerCase().includes(q);
    }).slice(0, 5);
    if (matches.length > 0 && !state.selectedNode) {
      // Auto-select first match
      onNodeSelect(matches[0].id);
    }
  }

  function resetFilters() {
    els["filter-layer"].value = ""; els["filter-node-type"].value = "";
    els["filter-edge-type"].value = "";
    ["filter-cycle", "filter-unresolved", "filter-critical", "filter-hotspot", "filter-di", "filter-registrations"].forEach(function (id) {
      els[id].checked = false;
    });
    els["search-input"].value = "";
    applyFilters();
  }

  function clearSelection() {
    state.selectedNode = null;
    state.graph.unfocus();
    els["selection-info"].textContent = "Nothing selected";
    els["btn-focus"].disabled = true;
    els["btn-path"].disabled = true;
    els["btn-impact"].disabled = true;
    els["inspector-overview"].classList.remove("hidden");
    els["inspector-node-view"].classList.add("hidden");
    els["inspector-title"].textContent = "System Overview";
  }

  function onNodeSelect(id) {
    state.selectedNode = id;
    state.graph.focus(id);
    var node = (state.fullNodes || []).find(function (n) { return n.id === id; });
    if (!node) return;
    els["selection-info"].textContent = node.qualified_name;
    els["btn-focus"].disabled = false;
    els["btn-path"].disabled = false;
    els["btn-impact"].disabled = false;
    api.node(id).then(function (r) {
      if (!r.ok) { 
        showError("Node details failed: " + r.error);
        return;
      }
      renderNodeInspector(r.data);
    });
  }

  function focusSelectedNode() {
    if (!state.selectedNode) return;
    var node = (state.fullNodes || []).find(function (n) { return n.id === state.selectedNode; });
    if (!node) return;
    var p = state.graph.pos[state.selectedNode];
    if (!p) return;
    // Center view on node
    state.graph._view.x = p.x - 500;
    state.graph._view.y = p.y - 350;
    state.graph._view.k = Math.max(state.graph._view.k, 1.2);
    state.graph._applyView();
  }

  function renderNodeInspector(d) {
    var n = d.node || {};
    var m = d.metrics || {};
    els["inspector-title"].textContent = "Node Inspector";
    els["inspector-overview"].classList.add("hidden");
    els["inspector-node-view"].classList.remove("hidden");
    
    els["node-identity"].textContent = n.qualified_name || "—";
    els["node-kind"].textContent = n.kind || "—";
    els["node-layer"].textContent = (n.layer || "—").toUpperCase();
    els["node-criticality"].textContent = n.criticality || "—";
    
    // Metrics
    els["node-metrics"].innerHTML = 
      '<div class="nx-card p-3 text-center"><div class="text-2xl font-bold">' + (m.fan_in || 0) + '</div><div class="text-xs nx-muted">Fan-in</div></div>' +
      '<div class="nx-card p-3 text-center"><div class="text-2xl font-bold">' + (m.fan_out || 0) + '</div><div class="text-xs nx-muted">Fan-out</div></div>' +
      '<div class="nx-card p-3 text-center"><div class="text-2xl font-bold">' + (m.instability != null ? m.instability.toFixed(2) : "—") + '</div><div class="text-xs nx-muted">Instability</div></div>' +
      '<div class="nx-card p-3 text-center"><div class="text-2xl font-bold">' + (m.centrality != null ? m.centrality.toFixed(2) : "—") + '</div><div class="text-xs nx-muted">Centrality</div></div>' +
      '<div class="nx-card p-3 text-center col-span-2"><div class="text-2xl font-bold ' + (m.in_cycle ? "nx-severity-high" : "") + '">' + (m.in_cycle ? "YES" : "NO") + '</div><div class="text-xs nx-muted">In Cycle</div></div>';
    
    // Dependencies
    var deps = (d.dependencies || []).slice(0, 8);
    els["node-dependencies"].innerHTML = deps.length > 0 
      ? deps.map(function (x) { return '<div class="text-xs mono py-1 cursor-pointer hover:text-blue-400" data-node="' + esc(x) + '">' + esc(x.split(":").pop()) + '</div>'; }).join("")
      : '<div class="text-xs nx-muted">No direct dependencies</div>';
    els["node-dependencies"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Dependents
    var dependents = (d.dependents || []).slice(0, 8);
    els["node-dependents"].innerHTML = dependents.length > 0
      ? dependents.map(function (x) { return '<div class="text-xs mono py-1 cursor-pointer hover:text-blue-400" data-node="' + esc(x) + '">' + esc(x.split(":").pop()) + '</div>'; }).join("")
      : '<div class="text-xs nx-muted">No direct dependents</div>';
    els["node-dependents"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Evidence
    var edges = d.incident_edges || [];
    var evRows = edges.slice(0, 8).map(function (e) {
      var ev = e.evidence || {};
      return '<tr><td class="text-xs"><span class="nx-badge ' + (e.kind === "INJECTS" ? "badge-status-ok" : e.kind === "IMPLEMENTS" ? "badge-status-degraded" : "badge-status-degraded") + '">' + esc(e.kind) + '</span></td><td class="text-xs mono">' + esc(ev.file || "—") + ':' + esc(ev.line || 0) + '</td><td class="text-xs">' + esc(ev.reason || "—") + '</td></tr>';
    }).join("");
    els["node-evidence"].innerHTML = evRows.length > 0
      ? '<table class="nx-table">' + evRows + '</table>'
      : '<div class="text-xs nx-muted">No edge evidence</div>';
  }

  function renderInspectorOverview() {
    els["inspector-title"].textContent = "System Overview";
    els["inspector-overview"].classList.remove("hidden");
    els["inspector-node-view"].classList.add("hidden");
    
    // Health
    var h = (state.summary && state.summary.health) || {};
    var html = '<div class="space-y-2">';
    html += '<div class="flex items-center justify-between"><span class="text-sm nx-muted">Cycles</span><span class="font-medium">' + (h.cycles || 0) + '</span></div>';
    html += '<div class="flex items-center justify-between"><span class="text-sm nx-muted">Unresolved</span><span class="font-medium">' + (h.unresolved_imports || 0) + '</span></div>';
    html += '<div class="flex items-center justify-between"><span class="text-sm nx-muted">Violations</span><span class="font-medium">' + (h.architecture_violations || 0) + '</span></div>';
    html += '<div class="flex items-center justify-between"><span class="text-sm nx-muted">DI Reg.</span><span class="font-medium">' + ((state.summary && state.summary.repository && state.summary.repository.di_registrations) || 0) + '</span></div>';
    html += '</div>';
    els["overview-health"].innerHTML = html;
    
    // Active Filters
    var activeFilters = [];
    if (state.filters.cycleOnly) activeFilters.push("Cycles");
    if (state.filters.unresolvedOnly) activeFilters.push("Unresolved");
    if (state.filters.criticalOnly) activeFilters.push("Critical");
    if (state.filters.hotspotOnly) activeFilters.push("Hotspots");
    if (state.filters.diOnly) activeFilters.push("DI");
    if (state.filters.registrationsOnly) activeFilters.push("Registrations");
    if (state.filters.layer) activeFilters.push("Layer: " + state.filters.layer);
    if (state.filters.nodeType) activeFilters.push("Type: " + state.filters.nodeType);
    if (state.filters.edgeType) activeFilters.push("Edge: " + state.filters.edgeType);
    
    var filtersHtml = activeFilters.length > 0
      ? '<div class="flex flex-wrap gap-1">' + activeFilters.map(function(f) { return '<span class="nx-badge badge-status-degraded">' + esc(f) + '</span>'; }).join('') + '</div>'
      : '<div class="text-xs nx-muted">No active filters</div>';
    els["overview-filters"].innerHTML = filtersHtml;
    
    // Hotspots
    var hotspots = (state.summary && state.summary.hotspots) || [];
    var hotspotsHtml = hotspots.length > 0
      ? '<div class="space-y-2">' + hotspots.slice(0, 5).map(function(h) {
          return '<div class="nx-card p-2 cursor-pointer hover:border-blue-400" data-node="' + esc(h.node_id) + '">' +
            '<div class="text-xs font-medium">' + esc(h.node_id.split(":").pop()) + '</div>' +
            '<div class="flex items-center gap-2 text-xs nx-muted mt-1"><span>Fan-in: ' + (h.fan_in || 0) + '</span><span>Fan-out: ' + (h.fan_out || 0) + '</span></div>' +
            '</div>';
        }).join('') + '</div>'
      : '<div class="text-xs nx-muted">No hotspots detected</div>';
    els["overview-hotspots"].innerHTML = hotspotsHtml;
    els["overview-hotspots"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Cycles
    var cycles = state.cycles || [];
    var cyclesHtml = cycles.length > 0
      ? '<div class="space-y-2">' + cycles.slice(0, 3).map(function(c) {
          return '<div class="nx-card p-2"><div class="flex items-center justify-between"><span class="text-xs font-medium">' + esc(c.cycle_id) + '</span><span class="nx-badge badge-status-degraded">' + esc(c.severity) + '</span></div><div class="text-xs nx-muted mt-1 mono">' + (c.path || []).slice(0, 3).map(function(p) { return esc(p.split(":").pop()); }).join(" → ") + '</div></div>';
        }).join('') + '</div>'
      : '<div class="text-xs nx-muted">No cycles detected</div>';
    els["overview-cycles"].innerHTML = cyclesHtml;
  }

  function renderCyclesPanel(cycles) {
    // Cycles are shown in the overview panel
    if (!state.selectedNode) renderInspectorOverview();
  }

  function renderViolationsPanel(v) {
    // Violations are shown in the overview panel
    if (!state.selectedNode) renderInspectorOverview();
  }

  function runImpact(nodeId) {
    if (!nodeId) return;
    var node = (state.fullNodes || []).find(function (n) { return n.id === nodeId; });
    if (!node) return;
    
    api.impact(nodeId).then(function (r) {
      if (!r.ok) {
        showError("Impact analysis failed: " + r.error);
        return;
      }
      var d = r.data;
      var html = '<div class="space-y-2">';
      html += '<div class="nx-card p-3"><div class="font-medium mb-2">Impact Analysis: ' + esc(node.qualified_name) + '</div>';
      html += '<div class="grid grid-cols-2 gap-2 text-xs">';
      html += '<div><span class="nx-muted">Kind:</span> <span class="font-medium">' + esc(d.impact_kind || "—") + '</span></div>';
      html += '<div><span class="nx-muted">Direct:</span> <span class="font-medium">' + (d.direct || []).length + '</span></div>';
      html += '<div><span class="nx-muted">Transitive:</span> <span class="font-medium">' + (d.transitive || []).length + '</span></div>';
      html += '<div><span class="nx-muted">Tests:</span> <span class="font-medium">' + (d.tests_likely_affected || []).length + '</span></div>';
      html += '<div><span class="nx-muted">API Impact:</span> <span class="font-medium">' + (d.api_impact || []).length + '</span></div>';
      html += '<div><span class="nx-muted">Runtime:</span> <span class="font-medium">' + (d.runtime_impact || []).length + '</span></div>';
      html += '</div></div></div>';
      
      els["inspector-content"].innerHTML = html;
      els["inspector-title"].textContent = "Impact Analysis";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();