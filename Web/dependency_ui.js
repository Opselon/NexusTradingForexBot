/* Dependency Intelligence — UI controller (state, filters, inspector, explorers).
 * Pulls from window.NXDependency.api and window.NXDependencyGraph. No build step.
 */
(function () {
  "use strict";
  var api = window.NXDependency;
  var GraphCtor = window.NXDependencyGraph;

  var state = {
    summary: null,
    graph: null,
    cycles: [],
    violations: [],
    selectedNode: null,
    filters: { layer: "", nodeType: "", edgeType: "", cycleOnly: false, unresolvedOnly: false, criticalOnly: false },
    loading: false,
    error: null,
    lastUpdated: null,
  };

  var els = {};
  function $(id) { return document.getElementById(id); }

  function showError(msg) {
    var e = $("global-error");
    if (!e) return;
    if (msg) { e.textContent = "⚠ " + msg; e.style.display = "block"; }
    else { e.style.display = "none"; }
  }

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms || 250); };
  }

  // ---- bootstrap ----
  function init() {
    ["kpi-row","gen-time","graph-version","repo-status","search","btn-fit","btn-reset",
     "layer-filter","node-type-filter","edge-type-filter","f-cycle","f-unresolved",
     "f-critical","graph-svg","inspector","path-source","path-target","btn-path",
     "btn-impact","path-result","cycles-panel","cyc-count","violations-panel","vio-count"]
      .forEach(function (id) { els[id] = $(id); });

    state.graph = new GraphCtor(els["graph-svg"]);
    state.graph.onSelect = onNodeSelect;

    els["btn-fit"].onclick = function () { state.graph.fit(); };
    els["btn-reset"].onclick = resetFilters;
    els["search"].addEventListener("input", debounce(applyFilters, 250));
    ["layer-filter","node-type-filter","edge-type-filter"].forEach(function (id) {
      els[id].addEventListener("change", applyFilters);
    });
    ["f-cycle","f-unresolved","f-critical"].forEach(function (id) {
      els[id].addEventListener("change", applyFilters);
    });
    els["btn-path"].onclick = runPath;
    els["btn-impact"].onclick = runImpact;

    loadAll();
  }

  function loadAll() {
    state.loading = true;
    showError(null);
    api.summary().then(function (r) {
      if (!r.ok) { showError("summary: " + r.error + " (HTTP " + r.status + ")"); return r; }
      state.summary = r.data;
      renderSummary(r.data);
      return api.graph();
    }).then(function (r) {
      if (!r || !r.ok) { if (r && !r.ok) showError("graph: " + r.error); return; }
      state.graph_data = r.data;
      populateFilters(r.data);
      drawGraph(r.data);
      return api.cycles();
    }).then(function (r) {
      if (!r || !r.ok) return;
      state.cycles = r.data.cycles || [];
      renderCycles(state.cycles);
      return api.violations();
    }).then(function (r) {
      if (!r || !r.ok) return;
      state.violations = r.data.violations || [];
      renderViolations(state.violations);
      state.loading = false;
    }).catch(function (err) {
      state.loading = false;
      showError("Failed to load dependency data: " + (err && err.message ? err.message : err));
    });
  }

  function renderSummary(s) {
    if (!s) return;
    els["gen-time"].textContent = s.generated_at || "—";
    els["graph-version"].textContent = s.analyzer_version || "—";
    var repo = s.repository || {}, health = s.health || {};
    els["repo-status"].textContent =
      "files " + repo.files_analyzed + " · nodes " + repo.nodes + " · edges " + repo.edges +
      " · DI " + repo.di_registrations + " · cycles " + health.cycles + " · unresolved " + health.unresolved_imports;
    var kpis = [
      ["Modules", repo.modules], ["Nodes", repo.nodes], ["Edges", repo.edges],
      ["DI bindings", repo.di_registrations], ["Unresolved DI", health.unresolved_di_bindings],
      ["Cycles", health.cycles], ["Violations", health.architecture_violations],
      ["Hotspots", (s.hotspots || []).length],
    ];
    els["kpi-row"].innerHTML = kpis.map(function (k) {
      return '<div class="nx-card p-2 text-center"><div class="nx-kpi">' + (k[1] != null ? k[1] : "—") +
        '</div><div class="nx-muted text-xs">' + k[0] + "</div></div>";
    }).join("");
  }

  function populateFilters(data) {
    var layers = {}, ntypes = {}, etypes = {};
    (data.nodes || []).forEach(function (n) { if (n.layer) layers[n.layer] = 1; if (n.kind) ntypes[n.kind] = 1; });
    (data.edges || []).forEach(function (e) { if (e.kind) etypes[e.kind] = 1; });
    fillSelect(els["layer-filter"], Object.keys(layers).sort());
    fillSelect(els["node-type-filter"], Object.keys(ntypes).sort());
    fillSelect(els["edge-type-filter"], Object.keys(etypes).sort());
  }
  function fillSelect(sel, vals) {
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
    state.filters.layer = els["layer-filter"].value;
    state.filters.nodeType = els["node-type-filter"].value;
    state.filters.edgeType = els["edge-type-filter"].value;
    state.filters.cycleOnly = els["f-cycle"].checked;
    state.filters.unresolvedOnly = els["f-unresolved"].checked;
    state.filters.criticalOnly = els["f-critical"].checked;
    var q = (els["search"].value || "").toLowerCase();

    var visIds = {};
    var nodes = state.fullNodes.filter(function (n) {
      if (state.filters.layer && n.layer !== state.filters.layer) return false;
      if (state.filters.nodeType && n.kind !== state.filters.nodeType) return false;
      if (state.filters.unresolvedOnly && n.status !== "UNRESOLVED") return false;
      if (state.filters.criticalOnly && n.criticality !== "CRITICAL" && n.criticality !== "HIGH") return false;
      if (q && !(n.qualified_name || "").toLowerCase().includes(q) && !(n.display_name || "").toLowerCase().includes(q)) return false;
      visIds[n.id] = true;
      return true;
    });
    var edges = state.fullEdges.filter(function (e) {
      if (state.filters.edgeType && e.kind !== state.filters.edgeType) return false;
      if (state.filters.cycleOnly && !e.metadata && !e.metadata) return true; // cycle edges lack flag; kept simple
      return visIds[e.source] && visIds[e.target];
    });
    state.graph.setLayout(nodes, edges);
    if (state.selectedNode) state.graph.focus(state.selectedNode);
  }

  function resetFilters() {
    els["layer-filter"].value = ""; els["node-type-filter"].value = "";
    els["edge-type-filter"].value = "";
    els["f-cycle"].checked = false; els["f-unresolved"].checked = false; els["f-critical"].checked = false;
    els["search"].value = "";
    applyFilters();
  }

  function onNodeSelect(id) {
    state.selectedNode = id;
    state.graph.focus(id);
    var node = (state.fullNodes || []).find(function (n) { return n.id === id; });
    if (!node) return;
    api.nodeById(id).then(function (r) {
      if (!r.ok) { els["inspector"].innerHTML = '<div class="err">Failed to load node: ' + r.error + "</div>"; return; }
      renderInspector(r.data);
    });
  }

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, function (c) { return c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"; }); }

  function renderInspector(d) {
    var n = d.node || {};
    var m = d.metrics || {};
    var edges = d.incident_edges || [];
    var depList = (d.dependencies || []).map(function (x) { return '<li class="mono">' + esc(x) + "</li>"; }).join("");
    var depInv = (d.dependents || []).map(function (x) { return '<li class="mono">' + esc(x) + "</li>"; }).join("");
    var evRows = edges.map(function (e) {
      var ev = e.evidence || {};
      return '<tr><td>' + esc(e.kind) + "</td><td>" + esc(e.confidence) + "</td><td class='mono'>" +
        esc(ev.file) + ":" + esc(ev.line) + "</td><td>" + esc(ev.reason) + "</td></tr>";
    }).join("");
    els["inspector"].innerHTML =
      '<div class="font-bold">' + esc(n.qualified_name) + "</div>" +
      '<div class="nx-muted text-xs mb-2">' + esc(n.kind) + " · layer " + esc(n.layer) +
      " · criticality " + esc(n.criticality) + "</div>" +
      '<table class="nx-table"><tr><th>fan_in</th><th>fan_out</th><th>instability</th><th>in_cycle</th></tr>' +
      "<tr><td>" + (m.fan_in || 0) + "</td><td>" + (m.fan_out || 0) + "</td><td>" +
      (m.instability != null ? m.instability : "—") + "</td><td>" + (m.in_cycle ? "yes" : "no") + "</td></tr></table>" +
      '<div class="mt-2 font-bold text-sm">Dependencies</div><ul class="pl-4 list-disc">' + (depList || "<li class='nx-muted'>none</li>") + "</ul>" +
      '<div class="mt-2 font-bold text-sm">Dependents</div><ul class="pl-4 list-disc">' + (depInv || "<li class='nx-muted'>none</li>") + "</ul>" +
      '<div class="mt-2 font-bold text-sm">Edge evidence</div>' +
      '<table class="nx-table"><tr><th>kind</th><th>conf</th><th>file</th><th>why</th></tr>' + (evRows || "<tr><td colspan=4 class='nx-muted'>no edges</td></tr>") + "</table>";
  }

  function runPath() {
    var s = els["path-source"].value.trim(), t = els["path-target"].value.trim();
    if (!s || !t) { els["path-result"].innerHTML = '<span class="err">Provide source and target.</span>'; return; }
    els["path-result"].innerHTML = "loading…";
    api.path({ source: s, target: t }).then(function (r) {
      if (!r.ok) { els["path-result"].innerHTML = '<span class="err">' + esc(r.error) + "</span>"; return; }
      var d = r.data;
      if (!d.found) { els["path-result"].innerHTML = '<span class="warn">No path found.</span>'; return; }
      els["path-result"].innerHTML = '<span class="ok">Path:</span> <span class="mono">' +
        (d.path || []).join(" → ") + "</span>";
      state.graph.highlight(d.path || []);
    });
  }

  function runImpact() {
    var p = els["path-source"].value.trim();
    if (!p) { els["path-result"].innerHTML = '<span class="err">Provide a node for impact.</span>'; return; }
    els["path-result"].innerHTML = "loading…";
    api.impact({ path: p }).then(function (r) {
      if (!r.ok) { els["path-result"].innerHTML = '<span class="err">' + esc(r.error) + "</span>"; return; }
      var d = r.data;
      els["path-result"].innerHTML =
        "<div><b>Impact kind:</b> " + esc(d.impact_kind) + "</div>" +
        "<div>direct: " + (d.direct || []).length + " · transitive: " + (d.transitive || []).length +
        " · tests: " + (d.tests_likely_affected || []).length + " · api: " + (d.api_impact || []).length +
        " · runtime: " + (d.runtime_impact || []).length + "</div>";
    });
  }

  function renderCycles(cycles) {
    els["cyc-count"].textContent = "(" + cycles.length + ")";
    if (!cycles.length) { els["cycles-panel"].innerHTML = '<span class="ok">No cycles detected.</span>'; return; }
    els["cycles-panel"].innerHTML = cycles.map(function (c) {
      return '<div class="mb-1"><span class="warn">' + esc(c.cycle_id) + " [" + esc(c.severity) + "]</span><br><span class='mono'>" +
        (c.path || []).join(" → ") + "</span></div>";
    }).join("");
  }

  function renderViolations(v) {
    els["vio-count"].textContent = "(" + v.length + ")";
    if (!v.length) { els["violations-panel"].innerHTML = '<span class="ok">No architecture violations.</span>'; return; }
    els["violations-panel"].innerHTML = v.map(function (x) {
      return '<div class="mb-1"><span class="warn">' + esc(x.rule) + "</span> " + esc(x.source) + " → " + esc(x.target) +
        "<br><span class='nx-muted'>" + esc(x.remediation) + "</span></div>";
    }).join("");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
