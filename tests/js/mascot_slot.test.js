const test = require("node:test");
const assert = require("node:assert");
const { create } = require("../../ui/mascot-slot.js");

function fakeScope() {
  const calls = [];
  const controller = {
    setState: (s) => calls.push(["setState", s]),
    setAppearance: (c) => calls.push(["setAppearance", c]),
    setSpeakingLevel: (l) => calls.push(["setSpeakingLevel", l]),
  };
  const scope = { Necromancer: { mount: (el) => { calls.push(["mount", el]); return controller; } } };
  return { scope, calls };
}

test("slot forwards mount and the voice states to the mascot controller", () => {
  const { scope, calls } = fakeScope();
  const slot = create(scope);
  const el = { id: "slot" };
  slot.mount(el);
  for (const s of ["idle", "listening", "speaking", "summoning"]) slot.setState(s);
  slot.setAppearance({ spirit: "enron" });
  slot.setSpeakingLevel(0.5);
  assert.deepStrictEqual(calls, [
    ["mount", el],
    ["setState", "idle"], ["setState", "listening"], ["setState", "speaking"], ["setState", "summoning"],
    ["setAppearance", { spirit: "enron" }],
    ["setSpeakingLevel", 0.5],
  ]);
});

test("slot keeps working when the mascot rejects a state or appearance", () => {
  const scope = { Necromancer: { mount: () => ({
    setState: () => { throw new Error("unknown mascot state"); },
    setAppearance: () => { throw new Error("bad costume"); },
    setSpeakingLevel: () => {},
  }) } };
  const slot = create(scope);
  slot.mount({});
  assert.doesNotThrow(() => slot.setState("weird"));
  assert.doesNotThrow(() => slot.setAppearance({}));
});

test("no-op fallback shows the state as text when the mascot is absent", () => {
  const p = { textContent: "", className: "" };
  const host = { firstChild: null, textContent: "", ownerDocument: { createElement: () => p },
                 appendChild(n) { this.firstChild = n; } };
  const slot = create({});
  slot.mount(host);
  slot.setState("speaking");
  slot.setAppearance({});
  slot.setSpeakingLevel(1);
  assert.strictEqual(p.textContent, "mascot placeholder: speaking");
});
