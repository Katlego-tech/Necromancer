/* Browser audio and text I/O for the voice server (/io socket, see voice/server.py).
   Up: mic PCM16 mono 16 kHz binary frames; text frames {"type":"text"|"summon"}.
   Down: PCM16 mono 24 kHz binary (played); text frame {"type":"clear"} empties the queue.
   Enabled only when the page URL has ?io=ws://... */
(function () {
  "use strict";
  var doc = document;
  var url = new URLSearchParams(location.search).get("io");
  var micBtn = doc.getElementById("mic");
  var form = doc.getElementById("text-form");
  var input = doc.getElementById("text-input");
  if (!url) {
    if (micBtn) micBtn.disabled = true;
    if (form) form.hidden = true;
    return;
  }
  var ws = null, playCtx = null, nextTime = 0, sources = [];
  var queue = [], denied = false, restartBtn = doc.getElementById("restart");
  var DENIED = [4401, 4429];

  function slot() { return window.NecroMascotSlot; }

  function connect() {
    denied = false;
    var mine = ws = new WebSocket(url);
    mine.binaryType = "arraybuffer";
    mine.onopen = function () {
      var code = window.NecroAuth && window.NecroAuth.get();
      if (code) mine.send(JSON.stringify({ type: "auth", code: code }));
      queue.splice(0).forEach(function (m) { mine.send(m); });
    };
    mine.onmessage = function (m) {
      if (typeof m.data === "string") {
        try { if (JSON.parse(m.data).type === "clear") clearPlayback(); } catch (e) { /* ignore */ }
      } else {
        play(m.data);
      }
    };
    mine.onclose = function (ev) {
      if (mine !== ws) return;
      if (ev && DENIED.indexOf(ev.code) >= 0) { denied = true; return; }
      setTimeout(connect, 1000);
    };
  }
  if (window.NecroAuth) {
    window.NecroAuth.onChange(function () {
      var old = ws; ws = null;
      if (old) { try { old.close(); } catch (e) { /* already closed */ } }
      connect();
    });
  }
  if (restartBtn) {
    restartBtn.addEventListener("click", function () { ensurePlayCtx(); send(JSON.stringify({ type: "start" })); });
  }

  function send(msg) {
    if (ws && ws.readyState === 1) ws.send(msg);
    else if (typeof msg === "string") queue.push(msg);
  }

  function ensurePlayCtx() {
    if (!playCtx) playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    if (playCtx.state === "suspended") playCtx.resume();
    return playCtx;
  }

  function play(buf) {
    var ctx = ensurePlayCtx();
    var pcm = new Int16Array(buf, 0, Math.floor(buf.byteLength / 2));
    if (!pcm.length) return;
    var audio = ctx.createBuffer(1, pcm.length, 24000);
    var ch = audio.getChannelData(0), sum = 0;
    for (var i = 0; i < pcm.length; i++) { ch[i] = pcm[i] / 32768; sum += ch[i] * ch[i]; }
    var s = slot();
    if (s && s.setSpeakingLevel) s.setSpeakingLevel(Math.min(1, Math.sqrt(sum / pcm.length) * 4));
    var src = ctx.createBufferSource();
    src.buffer = audio;
    src.connect(ctx.destination);
    var at = Math.max(ctx.currentTime, nextTime);
    src.start(at);
    nextTime = at + audio.duration;
    sources.push(src);
    src.onended = function () {
      var k = sources.indexOf(src);
      if (k >= 0) sources.splice(k, 1);
      if (!sources.length && s && s.setSpeakingLevel) s.setSpeakingLevel(0);
    };
  }

  function clearPlayback() {
    sources.splice(0).forEach(function (src) { try { src.stop(); } catch (e) { /* already ended */ } });
    nextTime = 0;
    var s = slot();
    if (s && s.setSpeakingLevel) s.setSpeakingLevel(0);
  }

  async function startMic() {
    ensurePlayCtx();
    var stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    await ctx.audioWorklet.addModule("audio-worklet.js");
    var node = new AudioWorkletNode(ctx, "necro-mic");
    node.port.onmessage = function (m) { if (ws && ws.readyState === 1) ws.send(m.data); };
    ctx.createMediaStreamSource(stream).connect(node);
  }

  connect();
  if (micBtn) {
    micBtn.addEventListener("click", function () {
      micBtn.disabled = true;
      startMic().then(function () { micBtn.textContent = "Microphone on"; }).catch(function (e) {
        micBtn.disabled = false;
        micBtn.textContent = "Microphone unavailable: " + (e && e.name ? e.name : "error");
      });
    });
  }
  if (form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var text = input.value.trim();
      if (!text) return;
      ensurePlayCtx();
      send(JSON.stringify({ type: "text", text: text }));
      input.value = "";
    });
  }
})();
