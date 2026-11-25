/* Access-code holder. The code lives in memory and sessionStorage only, never in a URL or persistent storage. */
(function (root) {
  "use strict";
  var KEY = "necro_access_code";
  function create(storage) {
    var code = null, listeners = [];
    try { if (storage && storage.getItem(KEY)) code = storage.getItem(KEY); } catch (e) { /* storage blocked */ }
    return {
      get: function () { return code; },
      set: function (value) {
        code = (typeof value === "string" && value) ? value : null;
        try {
          if (storage) { if (code) storage.setItem(KEY, code); else storage.removeItem(KEY); }
        } catch (e) { /* storage blocked */ }
        listeners.slice().forEach(function (fn) { try { fn(); } catch (e) { /* listener failure is not ours */ } });
      },
      onChange: function (fn) { listeners.push(fn); }
    };
  }
  var store = null;
  try { store = root.sessionStorage || null; } catch (e) { store = null; }
  var api = create(store);
  api.create = create;
  api.KEY = KEY;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NecroAuth = api;
})(typeof window !== "undefined" ? window : this);
