/* DOM rendering. All event content goes in via textContent / text nodes only.
   No HTML-string sinks and no dynamic code evaluation. `doc` is injected so tests can use a fake DOM. */
(function (root) {
  "use strict";
  var P = (typeof module !== "undefined" && module.exports) ? require("./protocol.js") : root.NecroProtocol;
  var COLD = "The trail goes cold.";

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function el(doc, tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = String(text);
    return e;
  }
  function fmt(v) {
    if (Array.isArray(v)) return v.map(String).join(", ");
    return v === null ? "(none)" : String(v);
  }

  function createTranscript(doc, container) {
    var turns = {};
    return {
      update: function (ev) {
        var t = turns[ev.turn_id];
        if (!t) {
          t = el(doc, "p", "turn turn-" + ev.role);
          t.appendChild(el(doc, "span", "who", ev.role === "user" ? "You" : "Spirit"));
          t.appendChild(el(doc, "span", "said"));
          turns[ev.turn_id] = t;
          container.appendChild(t);
        }
        t.lastChild.textContent = ev.text;
        t.className = "turn turn-" + ev.role + (ev.final ? "" : " interim");
        if (container.scrollHeight !== undefined) container.scrollTop = container.scrollHeight;
      }
    };
  }

  function createEvidence(doc, container) {
    return {
      show: function (ev) {
        clear(container);
        container.className = "evidence evidence-" + ev.kind + (ev.source === null ? " cold" : "");
        if (ev.source === null) {
          container.appendChild(el(doc, "p", "cold-msg", COLD));
          return;
        }
        container.appendChild(el(doc, "h3", "ev-kind", ev.kind));
        container.appendChild(el(doc, "p", "ev-source", "Source: " + ev.source));
        var dl = el(doc, "dl", "ev-meta");
        Object.keys(ev.metadata).forEach(function (k) {
          dl.appendChild(el(doc, "dt", "", k));
          dl.appendChild(el(doc, "dd", "", fmt(ev.metadata[k])));
        });
        container.appendChild(dl);
        var pre = el(doc, "pre", "ev-body");
        P.splitExcerpt(ev.content, ev.excerpt).forEach(function (seg) {
          if (seg.mark) pre.appendChild(el(doc, "mark", "quoted", seg.text));
          else pre.appendChild(doc.createTextNode(seg.text));
        });
        container.appendChild(pre);
      },
      reset: function (placeholder) {
        clear(container);
        container.className = "evidence empty";
        container.appendChild(el(doc, "p", "placeholder", placeholder));
      }
    };
  }

  var api = { COLD: COLD, createTranscript: createTranscript, createEvidence: createEvidence };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NecroRender = api;
})(typeof window !== "undefined" ? window : this);
