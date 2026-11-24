/* Necromancer 2D mascot: vanilla JS state machine + overlay renderer.
 * Public API: Necromancer.mount(el, opts) -> { setState, getState, setAppearance,
 *   setSpeakingLevel, setReducedMotion, destroy }.
 * Pure helpers (no DOM) are exported on Necromancer.core and via module.exports for tests.
 * Spirit logic never touches appearance: pass a declarative config to setAppearance. */
(function (root) {
  "use strict";

  var STATES = ["idle", "listening", "speaking", "summoning", "shapeshifting", "alert"];
  var TRANSIENT_MS = { summoning: 1400, shapeshifting: 900 };
  var PROPS = ["paper", "lapels", "browser_halo", "pixel_accents", "electric_ring", "warning_glyph"];
  var ANIMATIONS = ["none", "idle", "summon", "shapeshift", "alert"];
  var DEFAULT_APPEARANCE = {
    spirit: "default", base: "little_necromancer", renderer: "2d", costume: "default",
    palette: { body: "#FFFFFF", trim: "#8A6ED6", eyes: "#7DF9FF" },
    props: [], animation: "none", reduced_motion: false
  };
  var HEX = /^#[0-9a-fA-F]{6}$/;

  function clamp01(n) { n = Number(n); return isNaN(n) ? 0 : Math.max(0, Math.min(1, n)); }

  /** Mouth position from amplitude: "open" is the sprite's own mouth. */
  function mouthShape(level) {
    level = clamp01(level);
    if (level < 0.12) return "closed";
    if (level < 0.45) return "mid";
    return "open";
  }

  /** Milliseconds until the next blink; rand is a 0..1 source. */
  function nextBlinkDelay(rand) { return 2500 + clamp01(rand) * 3500; }

  /** Explicit option wins; otherwise the OS preference. */
  function resolveReducedMotion(explicit, prefersReduce) {
    if (explicit === true || explicit === false) return explicit;
    return !!prefersReduce;
  }

  /** Where to go when a transient state ends (null = stay). */
  function nextAfter(state, resting) {
    return TRANSIENT_MS[state] ? (resting || "idle") : null;
  }

  /** Merge a config over defaults; throws Error on malformed values (full check lives in appearance.py). */
  function normalizeAppearance(cfg) {
    if (!cfg || typeof cfg !== "object") throw new Error("appearance config must be an object");
    var out = {};
    Object.keys(DEFAULT_APPEARANCE).forEach(function (k) { out[k] = DEFAULT_APPEARANCE[k]; });
    Object.keys(cfg).forEach(function (k) { out[k] = cfg[k]; });
    if (out.renderer !== "2d") throw new Error("unsupported renderer: " + out.renderer);
    var pal = {};
    ["body", "trim", "eyes"].forEach(function (k) {
      var v = (cfg.palette && cfg.palette[k]) || DEFAULT_APPEARANCE.palette[k];
      if (!HEX.test(v)) throw new Error("palette." + k + " must be #RRGGBB");
      pal[k] = v;
    });
    out.palette = pal;
    if (!Array.isArray(out.props)) throw new Error("props must be an array");
    out.props = out.props.filter(function (p) { return PROPS.indexOf(p) >= 0; });
    if (ANIMATIONS.indexOf(out.animation) < 0) throw new Error("unknown animation: " + out.animation);
    return out;
  }

  var core = {
    STATES: STATES, PROPS: PROPS, ANIMATIONS: ANIMATIONS, TRANSIENT_MS: TRANSIENT_MS,
    DEFAULT_APPEARANCE: DEFAULT_APPEARANCE, mouthShape: mouthShape, nextBlinkDelay: nextBlinkDelay,
    resolveReducedMotion: resolveReducedMotion, nextAfter: nextAfter, normalizeAppearance: normalizeAppearance
  };

  if (typeof module !== "undefined" && module.exports) module.exports = core;
  if (typeof document === "undefined") return;

  /* ---------- DOM renderer ---------- */
  var NS = "http://www.w3.org/2000/svg";
  var scriptBase = (function () {
    var s = document.currentScript;
    return s && s.src ? s.src.replace(/[^\/]*$/, "") : "";
  })();

  function mouthMarkup() {
    return '' +
      '<g class="necro-mouth necro-mouth-closed"><ellipse class="necro-mouth-patch" cx="478" cy="472" rx="66" ry="44"/><path class="necro-mouth-line" d="M440 466 Q478 494 516 466"/></g>' +
      '<g class="necro-mouth necro-mouth-mid"><ellipse class="necro-mouth-patch" cx="478" cy="472" rx="66" ry="44"/><path class="necro-mouth-line" d="M446 462 Q478 482 510 462"/><ellipse class="necro-mouth-hole" cx="478" cy="480" rx="36" ry="15"/></g>';
  }
  function detailMarkup() {
    return '' +
      /* enron: grey lapels + paper */
      '<g class="necro-prop" data-prop="lapels"><path d="M330 600 L425 640 L395 720 Z" fill="#b9b9c4" stroke="#6f6f7d" stroke-width="5" stroke-linejoin="round" opacity=".85"/><path d="M618 600 L523 640 L553 720 Z" fill="#b9b9c4" stroke="#6f6f7d" stroke-width="5" stroke-linejoin="round" opacity=".85"/></g>' +
      '<g class="necro-prop" data-prop="paper"><g transform="rotate(10 800 640)"><rect x="745" y="560" width="110" height="140" rx="6" fill="#f6f1de" stroke="#6f6f7d" stroke-width="5"/><path class="necro-trim-stroke" stroke-width="5" stroke-linecap="round" d="M765 595 H835 M765 622 H835 M765 649 H820 M765 676 H828"/></g></g>' +
      /* wayback: browser badge + pixel accents */
      '<g class="necro-prop" data-prop="browser_halo"><rect x="8" y="22" width="176" height="124" rx="10" fill="#f4f4f8" stroke="#6f6f7d" stroke-width="5"/><rect x="8" y="22" width="176" height="30" rx="10" class="necro-trim-fill"/><circle cx="30" cy="37" r="6" fill="#fff"/><circle cx="52" cy="37" r="6" fill="#fff"/><circle cx="74" cy="37" r="6" fill="#fff"/><path class="necro-trim-stroke" stroke-width="6" d="M28 78 H160 M28 100 H130 M28 122 H150"/></g>' +
      '<g class="necro-prop necro-trim-fill" data-prop="pixel_accents"><rect x="790" y="90" width="26" height="26"/><rect x="842" y="130" width="18" height="18"/><rect x="800" y="170" width="18" height="18"/><rect x="866" y="210" width="26" height="26"/><rect x="762" y="240" width="14" height="14"/><rect x="38" y="880" width="22" height="22"/><rect x="880" y="900" width="22" height="22"/></g>' +
      /* grid: thin electric ring + warning glyph */
      '<g class="necro-prop" data-prop="electric_ring"><ellipse class="necro-trim-stroke necro-electric" cx="472" cy="608" rx="458" ry="590"/></g>' +
      '<g class="necro-prop" data-prop="warning_glyph"><path d="M840 80 L912 210 L768 210 Z" fill="#ffb000" stroke="#4a3200" stroke-width="7" stroke-linejoin="round"/><path d="M840 118 V168" stroke="#4a3200" stroke-width="12" stroke-linecap="round"/><circle cx="840" cy="192" r="7" fill="#4a3200"/></g>';
  }
  var FALLBACK = '<svg class="necro-fallback" viewBox="0 0 945 1216" role="img" aria-label="Little Necromancer (static fallback)">' +
    '<path d="M472 40 C300 40 220 220 200 420 C190 560 150 700 120 900 L260 860 L330 1000 L400 960 C420 1080 480 1180 560 1170 C520 1120 540 1060 600 1000 L650 960 L720 1000 L800 860 L830 900 C800 700 760 560 740 420 C720 220 640 40 472 40 Z" fill="#f2f2f8" stroke="#6f6f7d" stroke-width="10" stroke-linejoin="round"/>' +
    '<ellipse class="fb-eye" cx="378" cy="385" rx="44" ry="38"/><ellipse class="fb-eye" cx="570" cy="385" rx="44" ry="38"/></svg>';

  function mount(el, opts) {
    opts = opts || {};
    var spriteUrl = opts.spriteUrl || (scriptBase + "assets/sprites/idle_web.png");
    var mq = root.matchMedia ? root.matchMedia("(prefers-reduced-motion: reduce)") : null;
    var explicitReduced = typeof opts.reducedMotion === "boolean" ? opts.reducedMotion : null;

    el.classList.add("necro");
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", opts.label || "Little Necromancer mascot");
    el.innerHTML =
      '<div class="necro-aura"></div>' +
      '<div class="necro-float"><div class="necro-lean"><div class="necro-bounce"><div class="necro-stage">' +
        '<img class="necro-sprite" alt="" draggable="false" src="' + spriteUrl + '">' +
        '<svg class="necro-ov necro-ov-mouth" viewBox="0 0 945 1216" aria-hidden="true">' + mouthMarkup() + '</svg>' +
        '<div class="necro-tint"></div>' +
        '<div class="necro-eyetint"></div><div class="necro-lids"></div>' +
        '<svg class="necro-ov necro-ov-detail" viewBox="0 0 945 1216" aria-hidden="true">' + detailMarkup() + '</svg>' +
        '<div class="necro-sweep"></div>' +
      '</div></div></div></div>' +
      '<div class="necro-ring"></div>' + FALLBACK;
    var eyesMaskUrl = opts.eyesMaskUrl || spriteUrl.replace(/[^\/]*$/, "eyes_patch.png");
    el.style.setProperty("--necro-mask", 'url("' + spriteUrl + '")');
    el.style.setProperty("--necro-eyes-mask", 'url("' + eyesMaskUrl + '")');
    el.setAttribute("data-mouth", "open");

    var img = el.querySelector(".necro-sprite");
    img.addEventListener("error", function () { el.classList.add("is-fallback"); });

    var state = "idle", resting = "idle", timers = {}, destroyed = false, level = 0, appearance = null;

    function reduced() { return resolveReducedMotion(explicitReduced, mq && mq.matches); }
    function syncReduced() { el.setAttribute("data-reduced", reduced() ? "true" : "false"); }
    function clear(k) { if (timers[k]) { clearTimeout(timers[k]); timers[k] = null; } }

    function scheduleBlink() {
      clear("blink");
      if (destroyed) return;
      timers.blink = setTimeout(function () {
        if (!reduced() && (state === "idle" || state === "speaking" || state === "listening")) {
          el.classList.add("is-blinking");
          timers.unblink = setTimeout(function () { el.classList.remove("is-blinking"); }, 140);
        }
        scheduleBlink();
      }, nextBlinkDelay(Math.random()));
    }

    function applyMouth() {
      el.setAttribute("data-mouth", state === "speaking" ? mouthShape(level) : "open");
    }

    function setState(name) {
      if (STATES.indexOf(name) < 0) throw new Error("unknown mascot state: " + name);
      clear("transient");
      if (TRANSIENT_MS[name] === undefined) resting = name;
      state = name;
      el.setAttribute("data-state", name);
      if (name !== "speaking") { level = 0; el.style.setProperty("--necro-level", 0); }
      applyMouth();
      if (TRANSIENT_MS[name]) {
        var ms = reduced() ? 400 : TRANSIENT_MS[name];
        timers.transient = setTimeout(function () { if (state === name) setState(nextAfter(name, resting)); }, ms);
      }
      el.dispatchEvent(new CustomEvent("necro:statechange", { detail: { state: state } }));
    }

    function setSpeakingLevel(v) {
      level = clamp01(v);
      el.style.setProperty("--necro-level", reduced() ? 0 : level);
      applyMouth();
    }

    function setAppearance(cfg, o) {
      var a = normalizeAppearance(cfg);
      appearance = a;
      el.style.setProperty("--necro-body", a.palette.body);
      el.style.setProperty("--necro-trim", a.palette.trim);
      el.style.setProperty("--necro-eyes", a.palette.eyes);
      el.setAttribute("data-props", a.props.join(" "));
      el.setAttribute("data-spirit", a.spirit);
      el.setAttribute("data-costume", a.costume);
      if (a.reduced_motion === true) { explicitReduced = true; syncReduced(); }
      var animate = !o || o.animate !== false;
      if (animate && a.animation === "shapeshift") setState("shapeshifting");
      else if (animate && a.animation === "summon") setState("summoning");
      else if (animate && a.animation === "alert") setState("alert");
      return a;
    }

    function setReducedMotion(v) { explicitReduced = (v === null || v === undefined) ? null : !!v; syncReduced(); }

    if (mq && mq.addEventListener) mq.addEventListener("change", syncReduced);
    syncReduced();
    setState("idle");
    scheduleBlink();
    if (opts.appearance) setAppearance(opts.appearance, { animate: false });

    return {
      el: el, setState: setState, getState: function () { return state; },
      setAppearance: setAppearance, getAppearance: function () { return appearance; },
      setSpeakingLevel: setSpeakingLevel, setReducedMotion: setReducedMotion,
      isReducedMotion: reduced,
      destroy: function () { destroyed = true; Object.keys(timers).forEach(clear); }
    };
  }

  root.Necromancer = { mount: mount, core: core };
})(typeof window !== "undefined" ? window : globalThis);
