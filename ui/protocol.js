/* Event validator for the Necromancer UI protocol (see EVENTS.md).
   Pure logic, no DOM. Works in browsers (window.NecroProtocol) and Node (module.exports). */
(function (root) {
  "use strict";
  var STATES = ["idle", "listening", "speaking", "summoning", "shapeshifting", "alert"];
  var SPIRITS = ["enron", "wayback", "grid"];
  var ROLES = ["user", "spirit"];
  var KINDS = ["email", "snapshot", "grid"];
  var REQUIRED_META = {
    email: ["evidence_id", "sender", "recipients", "date", "subject", "source_path"],
    snapshot: ["url", "timestamp"],
    grid: ["stage", "area", "start"]
  };
  var REASONS = ["daily_cap", "total_cap", "session_limit", "idle_timeout", "bad_code",
                 "code_required", "disconnected", "shutdown", "not_started"];
  var MAX_TEXT = 2000000;

  function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function isStr(v) { return typeof v === "string"; }
  function isScalar(v) { return v === null || isStr(v) || typeof v === "number" || typeof v === "boolean"; }
  function fail(msg) { return { ok: false, error: msg }; }

  function validateMetadata(kind, meta, cold) {
    if (meta === undefined && cold) return null;
    if (!isObj(meta)) return "metadata must be an object";
    var keys = Object.keys(meta);
    for (var i = 0; i < keys.length; i++) {
      var v = meta[keys[i]];
      var okArr = Array.isArray(v) && v.every(isScalar);
      if (!isScalar(v) && !okArr) return "metadata." + keys[i] + " must be a scalar or array of scalars";
    }
    if (cold) return null;
    var req = REQUIRED_META[kind];
    for (var j = 0; j < req.length; j++) {
      if (!(req[j] in meta)) return "metadata." + req[j] + " is required for kind " + kind;
    }
    if (kind === "snapshot" && !/^\d{14}$/.test(String(meta.timestamp))) {
      return "metadata.timestamp must be a 14-digit string";
    }
    return null;
  }

  function validateEvent(ev) {
    if (!isObj(ev)) return fail("event must be a JSON object");
    if (!isStr(ev.type)) return fail("type must be a string");
    switch (ev.type) {
      case "transcript":
        if (!isStr(ev.turn_id) || !ev.turn_id) return fail("turn_id must be a non-empty string");
        if (ROLES.indexOf(ev.role) < 0) return fail("role must be user or spirit");
        if (!isStr(ev.text) || ev.text.length > MAX_TEXT) return fail("text must be a string");
        if (typeof ev.final !== "boolean") return fail("final must be a boolean");
        break;
      case "state":
        if (STATES.indexOf(ev.state) < 0) return fail("unknown state");
        break;
      case "spirit":
        if (SPIRITS.indexOf(ev.spirit) < 0) return fail("unknown spirit");
        break;
      case "appearance":
        if (!isObj(ev.config)) return fail("config must be an object");
        break;
      case "evidence": {
        if (KINDS.indexOf(ev.kind) < 0) return fail("unknown evidence kind");
        var cold = ev.source === null;
        if (!cold && (!isStr(ev.source) || !ev.source)) return fail("source must be a non-empty string or null");
        if (cold) {
          if (ev.content !== null) return fail("content must be null when source is null");
        } else if (!isStr(ev.content) || ev.content.length > MAX_TEXT) {
          return fail("content must be a string");
        }
        if (ev.excerpt !== undefined && ev.excerpt !== null && !isStr(ev.excerpt)) return fail("excerpt must be a string");
        var m = validateMetadata(ev.kind, ev.metadata, cold);
        if (m) return fail(m);
        break;
      }
      case "paused":
        if (REASONS.indexOf(ev.reason) < 0) return fail("unknown pause reason");
        if (!isStr(ev.message) || !ev.message) return fail("message must be a non-empty string");
        if (typeof ev.can_restart !== "boolean") return fail("can_restart must be a boolean");
        if (ev.video_url !== undefined && ev.video_url !== null &&
            !(isStr(ev.video_url) && /^https:\/\//.test(ev.video_url))) {
          return fail("video_url must be null or an https URL");
        }
        break;
      case "error":
        if (!isStr(ev.message)) return fail("message must be a string");
        break;
      default:
        return fail("unknown event type");
    }
    return { ok: true, event: ev };
  }

  /* Parse a raw WebSocket frame. Never throws. */
  function parseFrame(data) {
    var obj;
    try { obj = JSON.parse(data); } catch (e) { return fail("invalid JSON"); }
    return validateEvent(obj);
  }

  /* Split text into segments [{text, mark}] marking the first exact occurrence of excerpt.
     Concatenating the segments always reproduces text exactly. */
  function splitExcerpt(text, excerpt) {
    if (!excerpt) return [{ text: text, mark: false }];
    var i = text.indexOf(excerpt);
    if (i < 0) return [{ text: text, mark: false }];
    var out = [];
    if (i > 0) out.push({ text: text.slice(0, i), mark: false });
    out.push({ text: excerpt, mark: true });
    if (i + excerpt.length < text.length) out.push({ text: text.slice(i + excerpt.length), mark: false });
    return out;
  }

  var api = { STATES: STATES, SPIRITS: SPIRITS, ROLES: ROLES, KINDS: KINDS, REASONS: REASONS, REQUIRED_META: REQUIRED_META,
              validateEvent: validateEvent, parseFrame: parseFrame, splitExcerpt: splitExcerpt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NecroProtocol = api;
})(typeof window !== "undefined" ? window : this);
