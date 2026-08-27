/* Dependency Intelligence — SVG graph renderer (no external library).
 * Deterministic package-cluster layout with zoom-aware progressive rendering,
 * selection focus, and severity/criticality visual language.
 * Designed to stay readable for hundreds of nodes.
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
    this._focusHighlight = new Set();
    this._pendingHighlight = new Set();
    this._bindPan();
  }

  GraphRenderer.prototype.setLayout = function (nodes, edges) {
    this.nodes = nodes || [];
    this.edges = edges || [];
    this._layout();
    this.render();
  };

  GraphRenderer.prototype._layout = function () {
    var groups = {};
    var order = [];
    this.nodes.forEach(function (n) {
      var pkg = (n.qualified_name || n.id || "?").split(".")[0];
      if (!groups[pkg]) { groups[pkg] = []; order.push(pkg); }
      groups[pkg].push(n);
    });
    var cx = 500, cy = 350;
    var baseR = 240;
    var self = this;
    order.forEach(function (pkg, gi) {
      var members = groups[pkg];
      var ang = (gi / Math.max(1, order.length)) * Math.PI * 2;
      var gx = cx + Math.cos(ang) * baseR;
      var gy = cy + Math.sin(ang) * baseR;
      members.forEach(function (n, mi) {
        // Progressive density: at full zoom, show all; at medium, filter; at far, only clusters
        var zoomFactor = Math.max(0.3, Math.min(1, self._view.k));
        var isClusterNode = zoomFactor < 0.8 && mi > 0 && mi % 4 === 0;
        var isFarNode = zoomFactor < 0.5;
        var membersLength = members.length;
        var clusterIdx = Math.floor(mi / 4);
        var sub = membersLength > 1 ? (mi % 4 - 0.5) * 0.8 : 0;
        var a2 = ang + sub;
        var baseRad = isFarNode ? baseR * 0.3 : (isClusterNode ? baseR * 0.5 : baseR * 0.7);
        var r = baseRad + (mi % 5) * 4;
        var px = gx + Math.cos(a2) * (20 + (mi % 5) * 14);
        var py = gy + Math.sin(a2) * (20 + (mi % 5) * 14);
        self.pos[n.id] = { x: px, y: py, gx: gx, gy: gy, pkg: pkg };
      });
    });
    this.nodes.forEach(function (n) {
      if (!self.pos[n.id]) self.pos[n.id] = { x: cx, y: cy };
    });
  };

  GraphRenderer.prototype._getClusterCenter = function (pkg) {
    // Return center of gravity for a cluster
    var members = this._layoutGroups ? this._layoutGroups[pkg] : [];
    if (!members || members.length === 0) return null;
    var sumX = 0, sumY = 0;
    members.forEach(function (n) { sumX += this.pos[n.id].x; sumY += this.pos[n.id].y; }, this);
    return { x: sumX / members.length, y: sumY / members.length };
  };

  GraphRenderer.prototype.render = function () {
    var svg = this.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var self = this;

    var gEdges = document.createElementNS(SVGNS, "g");
    var gNodes = document.createElementNS(SVGNS, "g");
    svg.appendChild(gEdges);
    svg.appendChild(gNodes);

    // Render edges with progressive opacity based on zoom
    var visibleEdges = this._getVisibleEdges();
    visibleEdges.forEach(function (e) {
      var a = self.pos[e.source], b = self.pos[e.target];
      if (!a || !b) return;
      var line = document.createElementNS(SVGNS, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      var opacity = 0.3 + 0.7 * Math.max(0.3, Math.min(1, self._view.k));
      line.style.opacity = opacity;
      line.setAttribute("class", "edge-line");
      line.setAttribute("data-s", e.source);
      line.setAttribute("data-t", e.target);
      line.setAttribute("data-kind", e.kind || "");
      gEdges.appendChild(line);
    });

    // Render nodes
    this.nodes.forEach(function (n) {
      var p = self.pos[n.id];
      if (!p) return;
      var g = document.createElementNS(SVGNS, "g");
      g.setAttribute("class", "node-rect");
      g.setAttribute("transform", "translate(" + p.x + "," + p.y + ")");
      g.setAttribute("data-id", n.id);
      
      // Node radius based on kind and criticality
      var r = 5;
      if (n.criticality === "CRITICAL") r = 9;
      else if (n.criticality === "HIGH") r = 7;
      else if (n.kind === "CLASS" || n.kind === "PROTOCOL" || n.kind === "INTERFACE") r = 6;
      else if (n.kind === "MODULE") r = 6;
      else if (n.kind === "EXTERNAL") r = 4;
      
      var color = "#38bdf8";
      if (n.kind === "EXTERNAL") color = "#64748b";
      else if (n.status === "UNRESOLVED") color = "#f87171";
      else if (n.criticality === "CRITICAL") color = "#fbbf24";
      else if (self.selected === n.id) color = "#fcd34d";
      
      var c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", r);
      c.setAttribute("fill", color);
      c.setAttribute("stroke", n.id === self.selected ? "#fff" : "#0a0f1d");
      c.setAttribute("stroke-width", n.id === self.selected ? "2.5" : "1.5");
      g.appendChild(c);
      
      // Node label - only render at higher zoom or for selected nodes
      var showLabel = self._view.k >= 0.5 || n.id === self.selected;
      if (showLabel) {
        var label = document.createElementNS(SVGNS, "text");
        label.setAttribute("class", "node-label");
        label.setAttribute("x", r + 4);
        label.setAttribute("y", 4);
        // Truncate based on zoom
        var labelText = (n.display_name || n.id).slice(0, Math.max(12, Math.min(32, Math.round(22 * self._view))));
        label.textContent = labelText;
        g.appendChild(label);
      }
      
      // Tooltip area - invisible but clickable
      var tooltip = document.createElementNS(SVGNS, "rect");
      tooltip.setAttribute("class", "node-tooltip");
      tooltip.setAttribute("data-id", n.id);
      tooltip.setAttribute("x", p.x - r - 10);
      tooltip.setAttribute("y", p.y - r - 10);
      tooltip.setAttribute("width", r * 4 + 20);
      tooltip.setAttribute("height", r * 4 + 20);
      tooltip.style.opacity = 0;
      g.appendChild(tooltip);
      
      // Click handler
      g.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (self.onSelect) self.onSelect(n.id);
      });
      gNodes.appendChild(g);
    });

    // Render cycle highlights
    this._renderCycleHighlights(gEdges);
  };

  GraphRenderer.prototype._getVisibleEdges = function () {
    var visible = [];
    this.edges.forEach(function (e) {
      var a = self.pos[e.source], b = self.pos[e.target];
      if (!a || !b) return;
      // Always show edges connected to selected node
      if (self.selected && (e.source === self.selected || e.target === self.selected)) {
        visible.push(e);
      } else if (self._focusHighlight && self._focusHighlight.has(e.source) && self._focusHighlight.has(e.target)) {
        visible.push(e);
      } else if (self._view.k >= 0.7) {
        // Full zoom: show most edges
        if (Math.random() > 0.1) visible.push(e); // ~90% at full zoom
      } else if (self._view.k >= 0.4) {
        // Medium zoom: show edges involving hotspot-like nodes
        var n1 = self.nodes.find(function (n) { return n.id === e.source; });
        var n2 = self.nodes.find(function (n) { return n.id === e.target; });
        if (n1 && n2 && (n1.kind === "EXTERNAL" || n2.kind === "EXTERNAL" || n1.status === "UNRESOLVED" || n2.status === "UNRESOLVED")) {
          visible.push(e);
        }
      }
    });
    return visible;
  };

  GraphRenderer.prototype._renderCycleHighlights = function (gEdges) {
    // Highlight cycle edges if any are selected
    if (this._focusHighlight && this._focusHighlight.size > 0) {
      var lines = gEdges.querySelectorAll("line.edge-line");
      lines.forEach(function (l) {
        var s = l.getAttribute("data-s"), t = l.getAttribute("data-t");
        var bothInFocus = this._focusHighlight.has(s) && this._focusHighlight.has(t);
        if (bothInFocus) {
          l.style.opacity = "1";
          l.style.stroke = "#f87171";
          l.style.strokeWidth = "2.5";
        } else {
          // Reduce opacity for non-cycle edges
          l.style.opacity = "0.2";
        }
      });
    }
  };

  GraphRenderer.prototype.highlight = function (ids) {
    this._focusHighlight = new Set(ids || []);
    this._pendingHighlight = new Set();
    this.render();
  };

  GraphRenderer.prototype.focus = function (id) {
    this.selected = id;
    var circles = this.svg.querySelectorAll("g.node-rect");
    circles.forEach(function (g) {
      var c = g.querySelector("circle");
      if (g.getAttribute("data-id") === id) c.setAttribute("stroke", "#fff");
      else c.setAttribute("stroke", "#0a0f1d");
    });
    // Highlight connected edges
    var lines = this.svg.querySelectorAll("line.edge-line");
    lines.forEach(function (l) {
      var s = l.getAttribute("data-s"), t = l.getAttribute("data-t");
      var conn = (s === id || t === id);
      l.style.opacity = conn ? "1" : "0.3";
    });
  };

  GraphRenderer.prototype.unfocus = function () {
    this.selected = null;
    var circles = this.svg.querySelectorAll("g.node-rect");
    circles.forEach(function (g) {
      var c = g.querySelector("circle");
      c.setAttribute("stroke", n.id === self.selected ? "#fff" : "#0a0f1d");
    });
    var lines = this.svg.querySelectorAll("line.edge-line");
    lines.forEach(function (l) { l.style.opacity = "0.3"; });
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
    svg.addEventListener("mousemove", function (e) {
      // Tooltip on hover
      var el = document.elementFromPoint(e.clientX, e.clientY);
      var nodeEl = el.closest(".node-rect");
      if (nodeEl) {
        var id = nodeEl.getAttribute("data-id");
        self._showTooltip(nodeEl, id);
      } else {
        self._hideTooltip();
      }
    });
    svg.addEventListener("mouseleave", function () { self._hideTooltip(); });
  };

  GraphRenderer.prototype._showTooltip = function (el, id) {
    var tooltip = document.getElementById("graph-tooltip");
    var node = this.nodes.find(function (n) { return n.id === id; });
    if (!node) return;
    tooltip.style.display = "block";
    tooltip.innerHTML = '<div class="nx-tooltip-title">' + (node.display_name || node.id) + '</div>' +
      '<div class="nx-tooltip-section"><span class="nx-tooltip-section-title">Kind:</span> ' + node.kind + '</div>' +
      '<div class="nx-tooltip-section"><span class="nx-tooltip-section-title">Criticality:</span> ' + node.criticality + '</div>' +
      '<div class="nx-tooltip-section"><span class="nx-tooltip-section-title">Fan-in:</span> ' + (node.fan_in || 0) + '</div>' +
      '<div class="nx-tooltip-section"><span class="nx-tooltip-section-title">Fan-out:</span> ' + (node.fan_out || 0) + '</div>';
    var rect = el.getBoundingClientRect();
    tooltip.style.left = (rect.left + window.scrollX) + "px";
    tooltip.style.top = (rect.top + window.scrollY + 20) + "px";
  };

  GraphRenderer.prototype._hideTooltip = function () {
    var tooltip = document.getElementById("graph-tooltip");
    if (tooltip) tooltip.style.display = "none";
  };

  GraphRenderer.prototype.fit = function () { this._view = { x: 0, y: 0, k: 1 }; this._applyView(); };

  GraphRenderer.prototype.setFocusHighlight = function (ids) {
    this._pendingHighlight = new Set(ids || []);
  };

  window.NXDependencyGraph = GraphRenderer;
})();