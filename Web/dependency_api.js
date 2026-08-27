/* Dependency Intelligence — API client.
 * Defensive: AbortController, timeout, no silent failures, null-safe parse.
 * Exposes window.NXDependency.api with Promise-returning methods.
 */
(function () {
  "use strict";
  var root = window;

  function NXDependency() {}

  // Base fetch with timeout + abort. Never throws raw network text into UI;
  // returns {ok, status, data, error}.
  NXDependency.prototype.request = function (path, opts) {
    opts = opts || {};
    var timeoutMs = opts.timeoutMs || 20000;
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, timeoutMs);
    var self = this;
    return fetch(path, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(function (resp) {
        clearTimeout(timer);
        return resp.text().then(function (text) {
          var data = null;
          try {
            data = text ? JSON.parse(text) : null;
          } catch (e) {
            data = null;
          }
          if (!resp.ok) {
            return {
              ok: false,
              status: resp.status,
              data: data,
              error: (data && (data.detail || data.error)) || ("HTTP " + resp.status),
            };
          }
          return { ok: true, status: resp.status, data: data, error: null };
        });
      })
      .catch(function (err) {
        clearTimeout(timer);
        var msg = "Unable to reach the dependency service";
        if (err && err.name === "AbortError") msg = "Dependency request timed out";
        return { ok: false, status: 0, data: null, error: msg };
      });
  };

  var endpoints = [
    "summary", "graph", "node", "path", "impact", "cycles", "violations", "metrics", "health",
  ];

  endpoints.forEach(function (ep) {
    NXDependency.prototype[ep] = function (query) {
      var url = "/api/dependency/" + ep;
      if (query) {
        var q = [];
        for (var k in query) {
          if (query[k] !== undefined && query[k] !== null && query[k] !== "")
            q.push(encodeURIComponent(k) + "=" + encodeURIComponent(query[k]));
        }
        if (q.length) url += "?" + q.join("&");
      }
      return this.request(url);
    };
  });

  // node lookup by id or qualified name (server supports both)
  NXDependency.prototype.nodeById = function (id) {
    return this.request("/api/dependency/node/" + encodeURIComponent(id));
  };

  root.NXDependency = new NXDependency();
})();
