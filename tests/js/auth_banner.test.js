const test = require("node:test");
const assert = require("node:assert");
const Auth = require("../../ui/auth.js");
const Banner = require("../../ui/banner.js");

function memStorage() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), removeItem: k => m.delete(k), m };
}
test("NecroAuth keeps the code in the given storage and notifies listeners", () => {
  const st = memStorage(), a = Auth.create(st);
  let calls = 0;
  a.onChange(() => calls++);
  assert.strictEqual(a.get(), null);
  a.set("abc");
  assert.strictEqual(a.get(), "abc");
  assert.strictEqual(st.getItem(Auth.KEY), "abc");
  assert.strictEqual(Auth.create(st).get(), "abc");
  a.set("");
  assert.strictEqual(a.get(), null);
  assert.strictEqual(st.m.size, 0);
  assert.strictEqual(calls, 2);
});
test("NecroAuth works without storage and survives a throwing storage", () => {
  const a = Auth.create(null);
  a.set("x"); assert.strictEqual(a.get(), "x");
  const bad = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };
  const b = Auth.create(bad);
  b.set("y"); assert.strictEqual(b.get(), "y");
});

function el() {
  const attrs = {};
  return { hidden: true, textContent: "", attrs,
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, getAttribute: k => (k in attrs ? attrs[k] : null) };
}
function banner() {
  const els = { banner: el(), msg: el(), form: el(), restart: el(), videoSlot: el(), videoLink: el(), body: el() };
  return { els, b: Banner.create(els) };
}
const ev = (o) => ({ type: "paused", reason: "session_limit", message: "m", can_restart: true, video_url: null, ...o });

test("banner shows restart only when can_restart and the code form only for code reasons", () => {
  const { els, b } = banner();
  b.show(ev({}));
  assert.strictEqual(els.banner.hidden, false);
  assert.strictEqual(els.restart.hidden, false);
  assert.strictEqual(els.form.hidden, true);
  assert.strictEqual(els.body.getAttribute("data-live"), "paused");
  b.show(ev({ can_restart: false, reason: "daily_cap" }));
  assert.strictEqual(els.restart.hidden, true);
  for (const reason of ["code_required", "bad_code"]) {
    b.show(ev({ reason }));
    assert.strictEqual(els.form.hidden, false, reason);
  }
  b.show(ev({}));
  assert.strictEqual(els.form.hidden, true);
  b.live();
  assert.strictEqual(els.banner.hidden, true);
  assert.strictEqual(els.body.getAttribute("data-live"), "on");
});
test("banner renders hostile message as text only and sets href only for https", () => {
  const { els, b } = banner();
  const hostile = "<img src=x onerror=alert(1)>";
  b.show(ev({ message: hostile }));
  assert.strictEqual(els.msg.textContent, hostile);
  assert.deepStrictEqual(Object.keys(els.msg.attrs), []);
  assert.strictEqual(els.videoSlot.hidden, true);
  b.show(ev({ video_url: "https://example.invalid/demo" }));
  assert.strictEqual(els.videoSlot.hidden, false);
  assert.strictEqual(els.videoLink.getAttribute("href"), "https://example.invalid/demo");
  for (const bad of ["javascript:alert(1)", "http://x.invalid/", 5, null]) {
    b.show(ev({ video_url: bad }));
    assert.strictEqual(els.videoSlot.hidden, true);
    assert.strictEqual(els.videoLink.getAttribute("href"), null);
  }
});
test("isDenial is true only for the paused events that ask for a code (T7.10: one neutral form for every code)", () => {
  assert.strictEqual(Banner.isDenial(ev({ reason: "code_required" })), true);
  assert.strictEqual(Banner.isDenial(ev({ reason: "bad_code" })), true);
  for (const reason of ["daily_cap", "total_cap", "session_limit", "idle_timeout", "not_started"]) {
    assert.strictEqual(Banner.isDenial(ev({ reason })), false, reason);
  }
  assert.strictEqual(Banner.isDenial({ type: "state", state: "idle" }), false);
  assert.strictEqual(Banner.isDenial(null), false);
});
test("a cap pause (public or judge) shows the server text with textContent and the https video link, no code form", () => {
  const { els, b } = banner();
  b.show(ev({ reason: "total_cap", can_restart: false, message: "<img src=x onerror=alert(1)>", video_url: "https://example.com/v" }));
  assert.strictEqual(els.msg.textContent, "<img src=x onerror=alert(1)>");
  assert.strictEqual(els.videoSlot.hidden, false);
  assert.strictEqual(els.videoLink.getAttribute("href"), "https://example.com/v");
  assert.strictEqual(els.form.hidden, true);
  assert.strictEqual(els.restart.hidden, true);
  b.show(ev({ reason: "daily_cap", can_restart: false, video_url: "javascript:alert(1)" }));
  assert.strictEqual(els.videoSlot.hidden, true);
});
