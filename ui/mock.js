/* Mock event player. ALL content is SYNTHETIC placeholder text, not real Enron data. */
(function (root) {
  "use strict";
  var BODY = "[SYNTHETIC PLACEHOLDER - not a real email]\n" +
    "Hello team,\n\nThis is made-up demo text. Markup stays inert: <script>alert('demo')</script> " +
    "and <img src=x onerror=alert(1)> are shown as plain characters.\n\n" +
    "The quarterly widget review is moved to Thursday.\n\nRegards,\nDemo Sender";
  var SCRIPT = [
    [0, { type: "state", state: "idle" }],
    [400, { type: "state", state: "listening" }],
    [300, { type: "transcript", turn_id: "u1", role: "user", text: "Who", final: false }],
    [300, { type: "transcript", turn_id: "u1", role: "user", text: "Who moved the widget review?", final: true }],
    [300, { type: "state", state: "summoning" }],
    [300, { type: "spirit", spirit: "enron" }],
    [100, { type: "appearance", config: { spirit: "enron", base: "little_necromancer", renderer: "2d",
      costume: "enron_ghost", palette: { body: "#FFFFFF", trim: "#8A8A8A", eyes: "#7DF9FF" }, props: [],
      animation: "summon", reduced_motion: false } }],
    [600, { type: "state", state: "speaking" }],
    [200, { type: "transcript", turn_id: "s1", role: "spirit", text: "Searching the archives", final: false }],
    [500, { type: "evidence", kind: "email", source: "mock-email-0001", content: BODY,
      excerpt: "The quarterly widget review is moved to Thursday.",
      metadata: { evidence_id: "mock-email-0001", sender: "demo.sender@example.invalid",
        recipients: ["demo.team@example.invalid"], date: "2001-01-01T09:00:00Z",
        subject: "[SYNTHETIC] Widget review", source_path: "mock/demo/inbox/1." } }],
    [400, { type: "transcript", turn_id: "s1", role: "spirit",
      text: "Demo Sender wrote: \"The quarterly widget review is moved to Thursday.\"", final: true }],
    [1500, { type: "state", state: "listening" }],
    [400, { type: "transcript", turn_id: "u2", role: "user", text: "Anything about gadgets?", final: true }],
    [500, { type: "evidence", kind: "email", source: null, content: null }],
    [200, { type: "transcript", turn_id: "s2", role: "spirit", text: "The trail goes cold.", final: true }],
    [600, { type: "evidence", kind: "grid", source: "mock-grid-1", content: "[SYNTHETIC] Stage 2, Demo Area, 18:00-20:30",
      metadata: { stage: 2, area: "Demo Area", start: "18:00" } }],
    [200, { type: "state", state: "alert" }],
    [300, { type: "transcript", turn_id: "s3", role: "spirit", text: "Stage 2, Demo Area, starts 18:00.", final: true }],
    [800, { type: "bogus", note: "malformed event, must be flagged and ignored" }],
    [1500, { type: "state", state: "idle" }]
  ];

  /* play(onFrame, opts) calls onFrame(rawJsonString) like a socket would. Returns {stop}. */
  function play(onFrame, opts) {
    var speed = (opts && opts.speed) || 1, i = 0, timer = null, stopped = false;
    function next() {
      if (stopped || i >= SCRIPT.length) return;
      var step = SCRIPT[i++];
      timer = setTimeout(function () { onFrame(JSON.stringify(step[1])); next(); }, step[0] / speed);
    }
    next();
    return { stop: function () { stopped = true; clearTimeout(timer); } };
  }

  var api = { SCRIPT: SCRIPT, play: play };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NecroMock = api;
})(typeof window !== "undefined" ? window : this);
