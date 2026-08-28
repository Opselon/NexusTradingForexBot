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
    var graphOverlay = $("graph-loading-overlay");
    var svg = $("graph-svg");

    if (!e) return;
    if (msg) {
      e.innerHTML = '<div class="text-center p-6 bg-[#111a2e] rounded-xl border border-rose-900 shadow-2xl max-w-md"><div class="text-rose-500 font-bold mb-2">DEPENDENCY GRAPH UNAVAILABLE</div><div class="text-xs text-slate-300 mb-4">' + esc(msg) + '</div><button onclick="window.location.reload()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded text-sm font-semibold transition-colors">Retry</button></div>';
      e.classList.remove("hidden");
      e.classList.add("flex");

      // Stop the graph loading state visually to prevent hang UI
      if (graphOverlay) graphOverlay.classList.add("hidden");
      if (svg) svg.style.opacity = "0.1";
    } else {
      e.classList.add("hidden");
      e.classList.remove("flex");
      if (svg) svg.style.opacity = "1";
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
      if (loading) { overlay.classList.remove("hidden"); overlay.style.display = "flex"; }
      else { overlay.classList.add("hidden"); overlay.style.display = "none"; }
    }
    if (globalOverlay) {
      if (loading && !state.graph) { globalOverlay.classList.remove("hidden"); globalOverlay.style.display = "flex"; }
      else { globalOverlay.classList.add("hidden"); globalOverlay.style.display = "none"; }
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

    // Path mode setup
    var pendingPathSource = null;
    els["btn-path"].onclick = function() { 
      var node = state.fullNodes.find(function (n) { return n.id === state.selectedNode; });
      if (!node) return;
      pendingPathSource = node.id;

      els["inspector-title"].textContent = "PATH EXPLORER";
      if (els["inspector-overview"]) els["inspector-overview"].classList.add("hidden");
      if (els["inspector-node-view"]) els["inspector-node-view"].classList.add("hidden");
      if (els["inspector-impact-view"]) els["inspector-impact-view"].classList.add("hidden");
      els["inspector-path-view"].classList.remove("hidden");

      $("path-source-node").textContent = node.qualified_name.split('.').pop();
      $("path-target-node").textContent = "Select node from graph...";
      $("path-results").innerHTML = "";
    };

    // Re-purpose selection when path mode is active
    var originalOnSelect = onNodeSelect;
    onNodeSelect = function(id) {
       if (pendingPathSource && !els["inspector-path-view"].classList.contains("hidden")) {
          runPath(pendingPathSource, id);
          pendingPathSource = null;
          return;
       }
       originalOnSelect(id);
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

    // Explicit path finding execution via button if both nodes are selected
    if (els["btn-execute-path"]) {
      els["btn-execute-path"].onclick = function() {
        // Find path logic handles this if pendingPathSource and target exist.
      };
    }

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
      if (!r.ok) {
        // Stop execution but clear loader on error
        setLoading(false);
        showError("Summary failed: " + r.error + " (HTTP " + r.status + ")");
        return Promise.reject(new Error("summary failed"));
      }
      state.summary = r.data;
      renderHeader(r.data);
      renderHealthStrip(r.data);
      return api.graph();
    }).then(function (r) {
      if (!r || !r.ok) {
        if (r && !r.ok) {
           setLoading(false);
           showError("Graph failed: " + r.error);
        }
        return Promise.reject(new Error("graph failed"));
      }
      state.graph_data = r.data;
      populateFilters(r.data);
      drawGraph(r.data);
      // The graph is the primary payload; do not keep the whole page blocked
      // while optional cycle/violation panels finish loading.
      setLoading(false);
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
      if (err.message !== "summary failed" && err.message !== "graph failed") {
        showError("Failed to load dependency data: " + (err && err.message ? err.message : err));
      }
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
    // The primary graph is ready here; hide its blocking overlay immediately.
    var graphOverlay = $("graph-loading-overlay");
    if (graphOverlay) {
      graphOverlay.classList.add("hidden");
      graphOverlay.style.display = "none";
      graphOverlay.setAttribute("aria-hidden", "true");
    }
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
    });
    if (matches.length > 0) {
      // Auto-select first match to trigger focus/zoom loop
      var bestMatch = matches[0];
      onNodeSelect(bestMatch.id);
      focusSelectedNode();
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
    clearSelection();
  }

  function clearSelection() {
    state.selectedNode = null;
    state.graph.unfocus();
    els["selection-info"].textContent = "Nothing selected";
    els["btn-focus"].disabled = true;
    els["btn-path"].disabled = true;
    els["btn-impact"].disabled = true;

    // Reset to System Overview
    renderInspectorOverview();
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
    if (!state.selectedNode || !state.graph) return;
    var node = (state.fullNodes || []).find(function (n) { return n.id === state.selectedNode; });
    if (!node) return;
    var p = state.graph.pos[state.selectedNode];
    if (!p) return;

    var container = els["graph-svg"].parentElement;
    var w = container.clientWidth || 1000;
    var h = container.clientHeight || 700;

    // Zoom and center calculation
    state.graph._view.k = 1.8;
    state.graph._view.x = -(p.x * state.graph._view.k) + (w / 2);
    state.graph._view.y = -(p.y * state.graph._view.k) + (h / 2);
    state.graph._applyView();
  }

  function renderNodeInspector(d) {
    var n = d.node || {};
    var m = d.metrics || {};

    // Update active view
    els["inspector-title"].textContent = "NODE INSPECTOR";

    if (els["inspector-overview"]) els["inspector-overview"].classList.add("hidden");
    if (els["inspector-path-view"]) els["inspector-path-view"].classList.add("hidden");
    if (els["inspector-impact-view"]) els["inspector-impact-view"].classList.add("hidden");

    els["inspector-node-view"].classList.remove("hidden");
    
    // Identity block
    var nameParts = (n.qualified_name || "—").split(".");
    var shortName = nameParts.pop();
    els["node-identity"].innerHTML = '<div>' + esc(shortName) + '</div><div class="text-xs font-normal nx-muted mt-1 font-mono">' + esc(n.qualified_name || "—") + '</div>';

    els["node-kind"].textContent = n.kind || "—";

    if (n.kind === "EXTERNAL") els["node-kind"].className = "px-2 py-0.5 rounded text-white bg-slate-600";
    else if (n.kind === "MODULE") els["node-kind"].className = "px-2 py-0.5 rounded text-white bg-purple-600";
    else if (n.kind === "INTERFACE" || n.kind === "PROTOCOL") els["node-kind"].className = "px-2 py-0.5 rounded text-slate-900 bg-teal-400";
    else els["node-kind"].className = "px-2 py-0.5 rounded text-slate-900 bg-sky-400";

    els["node-layer"].textContent = (n.layer || "—").toUpperCase();

    if (n.criticality === "CRITICAL") {
      els["node-criticality"].innerHTML = '<span class="text-amber-500 flex items-center gap-1"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg> CRITICAL</span>';
    } else if (m.in_cycle) {
      els["node-criticality"].innerHTML = '<span class="text-rose-400 flex items-center gap-1"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-8.27l5.67-5.67"></path></svg> CYCLE</span>';
    } else {
      els["node-criticality"].textContent = "";
    }
    
    // Metrics
    els["node-metrics"].innerHTML = 
      '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded"><span class="text-xs nx-muted font-semibold tracking-wider">FAN-IN</span><span class="font-bold">' + (m.fan_in || 0) + '</span></div>' +
      '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded"><span class="text-xs nx-muted font-semibold tracking-wider">FAN-OUT</span><span class="font-bold">' + (m.fan_out || 0) + '</span></div>' +
      '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded"><span class="text-xs nx-muted font-semibold tracking-wider">INSTABILITY</span><span class="font-bold">' + (m.instability != null ? m.instability.toFixed(2) : "—") + '</span></div>' +
      '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded"><span class="text-xs nx-muted font-semibold tracking-wider">CENTRALITY</span><span class="font-bold">' + (m.centrality != null ? m.centrality.toFixed(2) : "—") + '</span></div>';
    
    // Dependencies
    var deps = (d.dependencies || []);
    els["node-dependencies"].innerHTML = deps.length > 0 
      ? deps.map(function (x) { return '<div class="text-xs font-mono py-1 px-2 hover:bg-[#1e293b] rounded cursor-pointer truncate transition-colors text-sky-200" data-node="' + esc(x) + '" title="' + esc(x) + '">' + esc(x.split(":").pop()) + '</div>'; }).join("")
      : '<div class="text-xs nx-muted px-2 py-1 italic">No direct dependencies</div>';
    els["node-dependencies"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Dependents
    var dependents = (d.dependents || []);
    els["node-dependents"].innerHTML = dependents.length > 0
      ? dependents.map(function (x) { return '<div class="text-xs font-mono py-1 px-2 hover:bg-[#1e293b] rounded cursor-pointer truncate transition-colors text-sky-200" data-node="' + esc(x) + '" title="' + esc(x) + '">' + esc(x.split(":").pop()) + '</div>'; }).join("")
      : '<div class="text-xs nx-muted px-2 py-1 italic">No direct dependents</div>';
    els["node-dependents"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Evidence
    var edges = d.incident_edges || [];
    var evRows = edges.map(function (e) {
      var ev = e.evidence || {};

      var badgeClass = "badge-status-degraded";
      if (e.kind === "INJECTS" || e.kind === "IMPLEMENTS") badgeClass = "badge-status-ok";
      else if (e.kind === "IMPORTS") badgeClass = "badge-status-info";

      return '<div class="mb-2 p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]">' +
             '<div class="flex items-center justify-between mb-1"><span class="nx-badge text-[10px] ' + badgeClass + '">' + esc(e.kind) + '</span><span class="text-[10px] font-mono text-slate-400 truncate max-w-[150px]" title="' + esc(ev.file || "—") + '">' + esc(ev.file || "—").split('/').pop() + ':' + esc(ev.line || 0) + '</span></div>' +
             '<div class="text-xs text-slate-300 font-mono mt-1 break-all">' + esc(ev.reason || "—") + '</div>' +
             '</div>';
    }).join("");

    els["node-evidence"].innerHTML = evRows.length > 0
      ? evRows
      : '<div class="text-xs nx-muted italic">No edge evidence available</div>';
  }

  function renderInspectorOverview() {
    els["inspector-title"].textContent = "COMMAND CENTER";

    els["inspector-overview"].classList.remove("hidden");
    if (els["inspector-node-view"]) els["inspector-node-view"].classList.add("hidden");
    if (els["inspector-path-view"]) els["inspector-path-view"].classList.add("hidden");
    if (els["inspector-impact-view"]) els["inspector-impact-view"].classList.add("hidden");
    
    // Health
    var h = (state.summary && state.summary.health) || {};
    var html = '<div class="space-y-2">';
    html += '<div class="flex items-center justify-between"><span class="text-xs font-semibold tracking-wider text-slate-400">CYCLES</span><span class="font-bold text-slate-200">' + (h.cycles || 0) + '</span></div>';
    html += '<div class="flex items-center justify-between"><span class="text-xs font-semibold tracking-wider text-slate-400">UNRESOLVED</span><span class="font-bold text-slate-200">' + (h.unresolved_imports || 0) + '</span></div>';
    html += '<div class="flex items-center justify-between"><span class="text-xs font-semibold tracking-wider text-slate-400">VIOLATIONS</span><span class="font-bold text-slate-200">' + (h.architecture_violations || 0) + '</span></div>';
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
      ? '<div class="flex flex-wrap gap-2">' + activeFilters.map(function(f) { return '<span class="px-2 py-0.5 rounded text-xs bg-cyan-900/40 text-cyan-400 border border-solid border-cyan-800">' + esc(f) + '</span>'; }).join('') + '</div>'
      : '<div class="text-xs nx-muted italic">No active filters</div>';
    els["overview-filters"].innerHTML = filtersHtml;
    
    // Hotspots
    var hotspots = (state.summary && state.summary.hotspots) || [];
    var hotspotsHtml = hotspots.length > 0
      ? '<div class="space-y-2">' + hotspots.slice(0, 5).map(function(h) {
          return '<div class="bg-[#1e293b] rounded p-2 border border-solid border-[#2b3a5e] cursor-pointer hover:border-[#38bdf8] transition-colors" data-node="' + esc(h.node_id) + '">' +
            '<div class="text-[13px] font-bold text-slate-200 truncate" title="' + esc(h.node_id) + '">' + esc(h.node_id.split(".").pop()) + '</div>' +
            '<div class="flex items-center gap-4 text-[10px] text-slate-400 mt-1 font-semibold tracking-wider"><span>FAN-IN: <span class="text-slate-200">' + (h.fan_in || 0) + '</span></span><span>FAN-OUT: <span class="text-slate-200">' + (h.fan_out || 0) + '</span></span></div>' +
            '</div>';
        }).join('') + '</div>'
      : '<div class="text-xs nx-muted italic">No hotspots detected</div>';
    els["overview-hotspots"].innerHTML = hotspotsHtml;
    els["overview-hotspots"].querySelectorAll("[data-node]").forEach(function(el) {
      el.onclick = function() { onNodeSelect(this.getAttribute("data-node")); };
    });
    
    // Cycles
    var cycles = state.cycles || [];
    var cyclesHtml = cycles.length > 0
      ? '<div class="space-y-2">' + cycles.slice(0, 5).map(function(c) {
          var severityClass = c.severity === "CRITICAL" ? "text-rose-400" : (c.severity === "HIGH" ? "text-amber-400" : "text-sky-400");
          return '<div class="bg-[#1e293b] rounded p-2 border border-solid border-[#2b3a5e] cursor-pointer hover:border-rose-400 transition-colors" data-cycle="' + esc(c.cycle_id) + '">' +
            '<div class="flex items-center justify-between"><span class="text-xs font-bold text-slate-200">' + esc(c.cycle_id) + '</span><span class="text-[10px] font-bold ' + severityClass + '">' + esc(c.severity) + '</span></div>' +
            '<div class="text-[10px] text-slate-400 mt-1 font-mono truncate">' + (c.path || []).map(function(p) { return esc(p.split(".").pop()); }).join(" → ") + '</div>' +
            '</div>';
        }).join('') + '</div>'
      : '<div class="text-xs nx-muted italic">No cycles detected</div>';
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
    
    // UI Transitions
    els["inspector-title"].textContent = "IMPACT ANALYSIS";
    if (els["inspector-overview"]) els["inspector-overview"].classList.add("hidden");
    if (els["inspector-node-view"]) els["inspector-node-view"].classList.add("hidden");
    if (els["inspector-path-view"]) els["inspector-path-view"].classList.add("hidden");
    els["inspector-impact-view"].classList.remove("hidden");

    api.impact(nodeId).then(function (r) {
      if (!r.ok) {
        showError("Impact analysis failed: " + r.error);
        return;
      }
      var d = r.data || {};

      var riskLevelEl = $("impact-risk-level");
      if (d.impact_kind === "CRITICAL") {
        riskLevelEl.innerHTML = '<span class="text-rose-500">CRITICAL RISK</span>';
      } else if (d.impact_kind === "HIGH") {
        riskLevelEl.innerHTML = '<span class="text-amber-500">HIGH RISK</span>';
      } else {
        riskLevelEl.innerHTML = '<span class="text-emerald-500">' + esc(d.impact_kind || "MODERATE RISK") + '</span>';
      }

      var html = '';
      html += '<div class="bg-[#1e293b] rounded p-3 border border-solid border-[#2b3a5e] mb-3">';
      html += '<div class="text-xs font-semibold text-slate-400 mb-1 tracking-wider">TARGET NODE</div>';
      html += '<div class="text-sm font-bold text-slate-200">' + esc(node.qualified_name.split('.').pop()) + '</div>';
      html += '<div class="text-[10px] font-mono text-slate-500 mt-1">' + esc(node.qualified_name) + '</div>';
      html += '</div>';

      html += '<div class="space-y-2">';
      html += '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]"><span class="text-xs nx-muted font-semibold tracking-wider">DIRECT IMPACT</span><span class="font-bold text-slate-200">' + (d.direct || []).length + '</span></div>';
      html += '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]"><span class="text-xs nx-muted font-semibold tracking-wider">TRANSITIVE IMPACT</span><span class="font-bold text-slate-200">' + (d.transitive || []).length + '</span></div>';
      html += '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]"><span class="text-xs nx-muted font-semibold tracking-wider">TESTS AFFECTED</span><span class="font-bold text-slate-200">' + (d.tests_likely_affected || []).length + '</span></div>';
      html += '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]"><span class="text-xs nx-muted font-semibold tracking-wider">API SURFACES</span><span class="font-bold text-slate-200">' + (d.api_impact || []).length + '</span></div>';
      html += '<div class="flex items-center justify-between p-2 bg-[#1e293b] rounded border border-solid border-[#2b3a5e]"><span class="text-xs nx-muted font-semibold tracking-wider">RUNTIME CRITICAL</span><span class="font-bold text-slate-200">' + (d.runtime_impact || []).length + '</span></div>';
      html += '</div>';

      $("impact-results").innerHTML = html;
    });
  }

  function runPath(sourceId, targetId) {
    if (!sourceId || !targetId) return;

    // UI Transitions
    els["inspector-title"].textContent = "PATH EXPLORER";
    if (els["inspector-overview"]) els["inspector-overview"].classList.add("hidden");
    if (els["inspector-node-view"]) els["inspector-node-view"].classList.add("hidden");
    if (els["inspector-impact-view"]) els["inspector-impact-view"].classList.add("hidden");
    els["inspector-path-view"].classList.remove("hidden");

    var sNode = (state.fullNodes || []).find(function (n) { return n.id === sourceId; });
    var tNode = (state.fullNodes || []).find(function (n) { return n.id === targetId; });

    $("path-source-node").textContent = sNode ? sNode.qualified_name.split('.').pop() : sourceId;
    $("path-target-node").textContent = tNode ? tNode.qualified_name.split('.').pop() : targetId;

    $("path-results").innerHTML = '<div class="text-xs text-center nx-muted py-4">Finding path...</div>';

    api.path(sourceId, targetId).then(function (r) {
      if (!r.ok) {
        $("path-results").innerHTML = '<div class="text-xs text-rose-400 p-2 bg-rose-950/30 border border-solid border-rose-900/50 rounded">' + esc(r.error) + '</div>';
        return;
      }

      var path = r.data || [];
      if (path.length === 0) {
        $("path-results").innerHTML = '<div class="text-xs nx-muted italic p-2 bg-[#1e293b] rounded">No path found.</div>';
        return;
      }

      var html = '<div class="text-xs font-semibold tracking-wider text-emerald-400 mb-3">FOUND PATH (LENGTH: ' + path.length + ')</div>';
      html += '<div class="space-y-1 relative before:absolute before:inset-y-0 before:left-3 before:w-px before:bg-[#2b3a5e]">';

      path.forEach(function(step, idx) {
        var nodeName = esc(step.split('.').pop());
        html += '<div class="flex items-center gap-3 relative z-10">';
        html += '<div class="w-6 h-6 rounded-full bg-[#1e293b] border-2 border-solid border-[#38bdf8] flex items-center justify-center text-[10px] font-bold shrink-0">' + (idx + 1) + '</div>';
        html += '<div class="bg-[#1e293b] rounded px-3 py-1.5 border border-solid border-[#2b3a5e] text-xs font-mono truncate flex-1">' + nodeName + '</div>';
        html += '</div>';
      });

      html += '</div>';
      $("path-results").innerHTML = html;
      
      // Optionally highlight path on graph
      if (state.graph) state.graph.setFocusHighlight(path);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();