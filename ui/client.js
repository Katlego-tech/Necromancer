/* WebSocket client with exponential-backoff reconnect. Emits raw frames; never throws on bad data. */
(function (root) {
  "use strict";
  function connect(url, handlers) {
    var ws = null, attempt = 0, closed = false, timer = null;
    function open() {
      if (closed) return;
      try { ws = new WebSocket(url); } catch (e) { handlers.onStatus("invalid ws url"); return; }
      ws.onopen = function () { attempt = 0; handlers.onStatus("connected"); };
      ws.onmessage = function (m) { handlers.onFrame(typeof m.data === "string" ? m.data : ""); };
      ws.onerror = function () {};
      ws.onclose = function () {
        if (closed) return;
        var delay = Math.min(10000, 500 * Math.pow(2, attempt++));
        handlers.onStatus("disconnected, retrying in " + Math.round(delay / 1000 * 10) / 10 + "s");
        timer = setTimeout(open, delay);
      };
    }
    open();
    return { close: function () { closed = true; clearTimeout(timer); if (ws) ws.close(); } };
  }
  root.NecroClient = { connect: connect };
})(typeof window !== "undefined" ? window : this);
