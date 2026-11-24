const test = require("node:test");
const assert = require("node:assert");
const core = require("../../mascot/mascot.js");

test("mouthShape follows amplitude", () => {
  assert.strictEqual(core.mouthShape(0), "closed");
  assert.strictEqual(core.mouthShape(0.3), "mid");
  assert.strictEqual(core.mouthShape(0.9), "open");
  assert.strictEqual(core.mouthShape(5), "open");
  assert.strictEqual(core.mouthShape("x"), "closed");
});
test("reduced motion: explicit option wins over OS preference", () => {
  assert.strictEqual(core.resolveReducedMotion(null, true), true);
  assert.strictEqual(core.resolveReducedMotion(false, true), false);
  assert.strictEqual(core.resolveReducedMotion(true, false), true);
  assert.strictEqual(core.resolveReducedMotion(undefined, false), false);
});
test("transient states return to the resting state", () => {
  assert.strictEqual(core.nextAfter("summoning", "listening"), "listening");
  assert.strictEqual(core.nextAfter("shapeshifting"), "idle");
  assert.strictEqual(core.nextAfter("alert", "idle"), null);
});
test("blink delay stays in range", () => {
  assert.ok(core.nextBlinkDelay(0) >= 2500 && core.nextBlinkDelay(1) <= 6000);
});
test("normalizeAppearance fills defaults and rejects bad input", () => {
  const a = core.normalizeAppearance({ spirit: "grid", palette: { eyes: "#FFB000" }, props: ["warning_glyph", "bogus"] });
  assert.strictEqual(a.palette.body, "#FFFFFF");
  assert.deepStrictEqual(a.props, ["warning_glyph"]);
  assert.throws(() => core.normalizeAppearance({ palette: { eyes: "red" } }));
  assert.throws(() => core.normalizeAppearance({ renderer: "3d" }));
  assert.throws(() => core.normalizeAppearance(null));
  assert.throws(() => core.normalizeAppearance({ animation: "explode" }));
});
test("all six states are declared", () => {
  assert.deepStrictEqual(core.STATES, ["idle", "listening", "speaking", "summoning", "shapeshifting", "alert"]);
});
