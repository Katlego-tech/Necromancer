/* Mascot mount point. Interface (provided by Track C in mascot/mascot.js, NOT imported here):
     mount(element), setState(state), setAppearance(config)
   Integration is one line: in index.html add <script src="../mascot/mascot.js"></script>
   before this file and expose it as window.NecroMascot = { mount, setState, setAppearance }.
   Until then this no-op fallback shows the state as a text label only. */
(function (root) {
  "use strict";
  var host = null;
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
    setAppearance: function () {}
  };
  root.NecroMascotSlot = root.NecroMascot || fallback;
})(typeof window !== "undefined" ? window : this);
