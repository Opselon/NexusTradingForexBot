/* Dependency Intelligence — SVG graph renderer (no external library).
 * Deterministic package-cluster layout: nodes grouped by top-level package,
 * positioned in a radial/cluster arrangement. Supports pan, zoom, click,
 * and edge highlighting. Designed to stay readable for hundreds of nodes.
 */
(function () {
  "use strict";
  var SVGNS = "http://www.w3.org/2000/svg";

  function GraphRenderer(svgEl) {
    this.svg = svgEl;
    this.nodes = [];
    this.edges = [];
    this.pos = {}; // id -> {x,y}
    this.selected = null;
    this.onSelect = null;
    this._view = { x: 0, y: 0, k: 1 };
    this._drag = null;
    this._bindPan();
  }

  GraphRenderer.prototype.setLayout = function (nodes, edges) {
    this.nodes = nodes || [];
    this.edges = edges || [];
    this._layout();
    this.render();
  };

  GraphRenderer.prototype._layout = function () {
    // group by package (first segment of qualified name)
    var groups = {};
    var order = [];
    this.nodes.forEach(function (n) {
      var pkg = (n.qualified_name || n.id || "?").split(".")[0];
      if (!groups[pkg]) { groups[pkg] = []; order.push(pkg); }
      groups[pkg].push(n);
    });
    var cx = 500, cy = 350;
    var ringR = 240;
    var self = this;
    order.forEach(function (pkg, gi) {
      var members = groups[pkg];
      var ang = (gi / Math.max(1, order.length)) * Math.PI * 2;
      var gx = cx + Math.cos(ang) * ringR;
      var gy = cy + Math.sin(ang) * ringR;
      members.forEach(function (n, mi) {
        var sub = members.length > 1 ? (mi / (members.length - 1) - 0.5) * 0.6 : 0;
        var a2 = ang + sub;
        var r = ringR * (0.5 + 0.5 * Math.random() * 0 + 0.0) + (mi % 7) * 6;
        // deterministic position: cluster around group center
        var px = gx + Math.cos(a2) * (20 + (mi % 5) * 14);
        var py = gy + Math.sin(a2) * (20 + (mi % 5) * 14);
        self.pos[n.id] = { x: px, y: py, gx: gx, gy: gy, pkg: pkg };
      });
    });
    // nodes with no position (missing) default center
    this.nodes.forEach(function (n) {
      if (!self.pos[n.id]) self.pos[n.id] = { x: cx, y: cy };
    });
  };

  GraphRenderer.prototype.render = function () {
    var svg = this.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var self = this;

    var gEdges = document.createElementNS(SVGNS, "g");
    var gNodes = document.createElementNS(SVGNS, "g");
    svg.appendChild(gEdges);
    svg.appendChild(gNodes);

    this.edges.forEach(function (e) {
      var a = self.pos[e.source], b = self.pos[e.target];
      if (!a || !b) return;
      var line = document.createElementNS(SVGNS, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      line.setAttribute("class", "edge-line");
      line.setAttribute("data-s", e.source);
      line.setAttribute("data-t", e.target);
      line.setAttribute("data-kind", e.kind || "");
      gEdges.appendChild(line);
    });

    this.nodes.forEach(function (n) {
      var p = self.pos[n.id];
      if (!p) return;
      var g = document.createElementNS(SVGNS, "g");
      g.setAttribute("class", "node-rect");
      g.setAttribute("transform", "translate(" + p.x + "," + p.y + ")");
      g.setAttribute("data-id", n.id);
      var r = 5;
      if (n.criticality === "CRITICAL") r = 9;
      else if (n.criticality === "HIGH") r = 7;
      else if (n.kind === "CLASS" || n.kind === "PROTOCOL" || n.kind === "INTERFACE") r = 6;
      var color = "#38bdf8";
      if (n.kind === "EXTERNAL") color = "#64748b";
      else if (n.status === "UNRESOLVED") color = "#f87171";
      else if (n.criticality === "CRITICAL") color = "#fbbf24";
      var c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", r);
      c.setAttribute("fill", color);
      c.setAttribute("stroke", n.id === self.selected ? "#fff" : "#0a0f1d");
      c.setAttribute("stroke-width", "1.5");
      g.appendChild(c);
      var label = document.createElementNS(SVGNS, "text");
      label.setAttribute("class", "node-label");
      label.setAttribute("x", r + 3);
      label.setAttribute("y", 3);
      label.textContent = (n.display_name || n.id).slice(0, 22);
      g.appendChild(label);
      g.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (self.onSelect) self.onSelect(n.id);
      });
      gNodes.appendChild(g);
    });
  };

  GraphRenderer.prototype.highlight = function (ids) {
    ids = ids || [];
    var set = {};
    ids.forEach(function (i) { set[i] = true; });
    var lines = this.svg.querySelectorAll("line.edge-line");
    lines.forEach(function (l) {
      var s = l.getAttribute("data-s"), t = l.getAttribute("data-t");
      if (set[s] && set[t]) l.classList.add("hl");
      else l.classList.remove("hl");
    });
  };

  GraphRenderer.prototype.focus = function (id) {
    this.selected = id;
    var circles = this.svg.querySelectorAll("g.node-rect");
    circles.forEach(function (g) {
      var c = g.querySelector("circle");
      if (g.getAttribute("data-id") === id) c.setAttribute("stroke", "#fff");
      else c.setAttribute("stroke", "#0a0f1d");
    });
  };

  GraphRenderer.prototype._bindPan = function () {
    var self = this;
    var svg = this.svg;
    svg.addEventListener("mousedown", function (e) {
      self._drag = { x: e.clientX, y: e.clientY };
      svg.classList.add("dragging");
    });
    window.addEventListener("mouseup", function () { svg.classList.remove("dragging"); self._drag = null; });
    svg.addEventListener("mousemove", function (e) {
      if (!self._drag) return;
      var dx = e.clientX - self._drag.x, dy = e.clientY - self._drag.y;
      self._view.x += dx; self._view.y += dy;
      self._drag = { x: e.clientX, y: e.clientY };
      self._applyView();
    });
    svg.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.1 : 0.9;
      self._view.k = Math.min(3, Math.max(0.3, self._view.k * factor));
      self._applyView();
    }, { passive: false });
  };

  GraphRenderer.prototype._applyView = function () {
    // simple pan/zoom approximation via viewBox shift
    var base = 1000 / this._view.k;
    var vb = (this._view.x) + " " + (this._view.y) + " " + base + " " + (base * 0.7);
    this.svg.setAttribute("viewBox", vb);
  };

  GraphRenderer.prototype.fit = function () { this._view = { x: 0, y: 0, k: 1 }; this._applyView(); };

  window.NXDependencyGraph = GraphRenderer;
})();
