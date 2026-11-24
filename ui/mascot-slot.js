/* Mascot mount point used by app.js and voice-io.js: mount(element), setState(state),
   setAppearance(config), setSpeakingLevel(level 0..1).
   When mascot/mascot.js is loaded (window.Necromancer.mount), the slot wraps its controller.
   Otherwise a no-op fallback shows the state as a text label only. */
(function (root) {
  "use strict";
  function create(scope) {
    var host = null, controller = null;
    var fallback = {
      mount: function (element) {
        host = element;
        host.textContent = "";
        var p = host.ownerDocument.createElement("p");
        p.className = "mascot-placeholder";
        p.textContent = "mascot placeholder: idle";
        host.appendChild(p);
      },
      setState: function (state) {
        if (host && host.firstChild) host.firstChild.textContent = "mascot placeholder: " + state;
      },
      setAppearance: function () {},
      setSpeakingLevel: function () {}
    };
    function usable() { return scope.Necromancer && typeof scope.Necromancer.mount === "function"; }
    return {
      mount: function (element) {
        if (usable()) {
          try { controller = scope.Necromancer.mount(element); return; } catch (e) { controller = null; }
        }
        fallback.mount(element);
      },
      setState: function (state) {
        if (!controller) return fallback.setState(state);
        try { controller.setState(state); } catch (e) { /* state unknown to the mascot: keep the previous one */ }
      },
      setAppearance: function (config) {
        if (!controller) return;
        try { controller.setAppearance(config); } catch (e) { /* invalid costume: keep the previous one */ }
      },
      setSpeakingLevel: function (level) {
        if (controller) controller.setSpeakingLevel(level);
      }
    };
  }
  var slot = create(root);
  root.NecroMascotSlot = slot;
  if (typeof module !== "undefined" && module.exports) module.exports = { create: create };
})(typeof window !== "undefined" ? window : globalThis);
