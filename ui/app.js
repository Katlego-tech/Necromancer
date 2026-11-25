/* Wires the page. Source selected by URL params: ?mock=enron  or  ?ws=ws://host:port/path */
(function () {
  "use strict";
  var doc = document, P = window.NecroProtocol, R = window.NecroRender;
  var params = new URLSearchParams(location.search);
  var $ = function (id) { return doc.getElementById(id); };
  var statusEl = $("status"), stateEl = $("state"), spiritEl = $("spirit"), issuesEl = $("issues");
  var transcript = R.createTranscript(doc, $("transcript"));
  var evidence = R.createEvidence(doc, $("evidence-body"));
  var mascot = window.NecroMascotSlot;
  mascot.mount($("mascot-slot"));
  evidence.reset("No evidence yet. Cited sources appear here.");
  var banner = window.NecroBanner.create({ banner: $("paused-banner"), msg: $("paused-msg"), form: $("code-form"),
    restart: $("restart"), videoSlot: $("video-slot"), videoLink: $("video-link"), body: doc.body });

  function issue(msg) {
    var li = doc.createElement("li");
    li.textContent = msg;
    issuesEl.appendChild(li);
    while (issuesEl.children.length > 20) issuesEl.removeChild(issuesEl.firstChild);
  }

  function handle(ev) {
    switch (ev.type) {
      case "transcript": transcript.update(ev); break;
      case "state":
        stateEl.textContent = ev.state; doc.body.setAttribute("data-state", ev.state); mascot.setState(ev.state);
        if (ev.state !== "idle" && doc.body.getAttribute("data-live") === "paused") banner.live();
        break;
      case "paused": banner.show(ev); break;
      case "spirit": spiritEl.textContent = ev.spirit; doc.body.setAttribute("data-spirit", ev.spirit); break;
      case "appearance": mascot.setAppearance(ev.config); break;
      case "evidence": evidence.show(ev); break;
      case "error": issue("backend error: " + ev.message); break;
    }
  }

  var codeSubmitted = false;
  function onFrame(data) {
    try {
      var r = P.parseFrame(data);
      if (!r.ok) { issue("ignored malformed event: " + r.error); return; }
      if (codeSubmitted && !window.NecroBanner.isDenial(r.event)) {
        codeSubmitted = false;
        statusEl.textContent = "access granted";   // neutral: the same text for every kind of code
      }
      handle(r.event);
    } catch (e) {
      issue("ignored event that failed to render: " + (e && e.message));
    }
  }

  var wsUrl = params.get("ws");
  if (wsUrl) {
    statusEl.textContent = "connecting";
    var conn = window.NecroClient.connect(wsUrl, { onFrame: onFrame, onStatus: function (s) { statusEl.textContent = s; } });
    $("code-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var input = $("code-input");
      if (!input.value) return;
      window.NecroAuth.set(input.value);
      input.value = "";
      codeSubmitted = true;
      conn.reconnect();
    });
  } else {
    var mock = params.get("mock") || "enron";
    statusEl.textContent = "mock session (" + mock + "), synthetic content";
    var speed = parseFloat(params.get("speed")) || 1;
    var player = window.NecroMock.play(onFrame, { speed: speed, scenario: mock, video: params.get("video") });
    $("replay").addEventListener("click", function () { location.reload(); });
    window.addEventListener("pagehide", player.stop);
  }
})();
