/* Paused banner. Server text goes in through textContent only; the one attribute set from server data is the
   video link href, and only when it is an https URL. els: banner, msg, form, restart, videoSlot, videoLink, body. */
(function (root) {
  "use strict";
  var CODE_REASONS = ["code_required", "bad_code"];
  function isHttps(u) { return typeof u === "string" && /^https:\/\//.test(u); }
  function create(els) {
    return {
      show: function (ev) {
        var needsCode = CODE_REASONS.indexOf(ev.reason) >= 0;
        els.msg.textContent = ev.message;
        els.form.hidden = !needsCode;
        els.restart.hidden = !(ev.can_restart === true && !needsCode);
        if (isHttps(ev.video_url)) {
          els.videoLink.setAttribute("href", ev.video_url);
          els.videoSlot.hidden = false;
        } else {
          els.videoLink.removeAttribute("href");
          els.videoSlot.hidden = true;
        }
        els.banner.hidden = false;
        els.body.setAttribute("data-live", "paused");
      },
      live: function () {
        els.banner.hidden = true;
        els.body.setAttribute("data-live", "on");
      }
    };
  }
  var api = { create: create, isHttps: isHttps };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.NecroBanner = api;
})(typeof window !== "undefined" ? window : this);
