/* WebSocket client with exponential-backoff reconnect. Emits raw frames; never throws on bad data.
   Sends {type:"auth",code} as the first frame when NecroAuth holds a code. Close codes 4401 (bad code)
   and 4429 (too many attempts) stop reconnecting until reconnect() is called. */
(function (root) {
  "use strict";
  var DENIED = [4401, 4429];
  function connect(url, handlers) {
    var ws = null, attempt = 0, closed = false, timer = null;
    function open() {
      if (closed) return;
      var mine;
      try { mine = ws = new WebSocket(url); } catch (e) { handlers.onStatus("invalid ws url"); return; }
      mine.onopen = function () {
        attempt = 0;
        var auth = root.NecroAuth && root.NecroAuth.get();
        if (auth) mine.send(JSON.stringify({ type: "auth", code: auth }));
        handlers.onStatus("connected");
      };
      mine.onmessage = function (m) { handlers.onFrame(typeof m.data === "string" ? m.data : ""); };
      mine.onerror = function () {};
      mine.onclose = function (ev) {
        if (closed || mine !== ws) return;
        if (ev && DENIED.indexOf(ev.code) >= 0) { handlers.onStatus("access denied"); return; }
        var delay = Math.min(10000, 500 * Math.pow(2, attempt++));
        handlers.onStatus("disconnected, retrying in " + Math.round(delay / 1000 * 10) / 10 + "s");
        timer = setTimeout(open, delay);
      };
    }
    function reconnect() {
      closed = false; attempt = 0; clearTimeout(timer);
      var old = ws; ws = null;
      if (old) { try { old.close(); } catch (e) { /* already closed */ } }
      open();
    }
    open();
    return { reconnect: reconnect, close: function () { closed = true; clearTimeout(timer); if (ws) ws.close(); } };
  }
  root.NecroClient = { connect: connect };
})(typeof window !== "undefined" ? window : this);
