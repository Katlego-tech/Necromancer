const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const P = require("./protocol.js");
const R = require("./render.js");
const M = require("./mock.js");

/* Minimal fake DOM. innerHTML/outerHTML/insertAdjacentHTML writes throw. */
class Node {
  constructor(tag, text) { this.tag = tag; this.children = []; this._text = text; this.className = ""; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this.tag === "#text" || !this.children.length ? (this._text || "") : this.children.map(c => c.textContent).join(""); }
  set innerHTML(v) { throw new Error("innerHTML forbidden"); }
  set outerHTML(v) { throw new Error("outerHTML forbidden"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML forbidden"); }
}
const doc = { createElement: t => new Node(t), createTextNode: s => new Node("#text", s) };
const walk = (n, f) => { f(n); n.children.forEach(c => walk(c, f)); };

const md = fs.readFileSync(path.join(__dirname, "EVENTS.md"), "utf8");
const blocks = tag => [...md.matchAll(new RegExp("```" + tag + "\\n([\\s\\S]*?)```", "g"))].map(m => m[1].trim());

test("every valid example in EVENTS.md passes", () => {
  const ex = blocks("json");
  assert.ok(ex.length >= 8);
  ex.forEach(e => assert.ok(P.parseFrame(e).ok, e));
});
test("every invalid example in EVENTS.md is rejected", () => {
  const ex = blocks("json-invalid");
  assert.ok(ex.length >= 5);
  ex.forEach(e => assert.strictEqual(P.parseFrame(e).ok, false, e));
});
test("malformed input never throws", () => {
  ["", "{", "null", "[]", "42", '"x"', '{"type":5}', '{"type":"evidence"}',
   '{"type":"evidence","kind":"email","source":"s","content":"c","metadata":{"a":{"b":1}}}',
   '{"type":"appearance","config":[]}', '{"type":"transcript","turn_id":"a","role":"x","text":"t","final":true}']
    .forEach(f => assert.strictEqual(P.parseFrame(f).ok, false, f));
  assert.strictEqual(P.validateEvent(undefined).ok, false);
});
test("mock script: only the deliberate bogus event is invalid, all others pass", () => {
  const bad = M.SCRIPT.filter(s => !P.validateEvent(s[1]).ok);
  assert.strictEqual(bad.length, 1);
  assert.strictEqual(bad[0][1].type, "bogus");
});
test("splitExcerpt preserves text exactly", () => {
  const t = "abc DEF ghi";
  for (const ex of ["DEF", "abc", "ghi", "zzz", "", null, "abc DEF ghi"]) {
    assert.strictEqual(P.splitExcerpt(t, ex).map(s => s.text).join(""), t);
  }
  assert.deepStrictEqual(P.splitExcerpt(t, "DEF").map(s => s.mark), [false, true, false]);
  assert.strictEqual(P.splitExcerpt(t, "zzz").some(s => s.mark), false);
});

const HOSTILE = "<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n</pre><b>bold</b>";
test("hostile email body renders as literal text, no elements created from it", () => {
  const c = new Node("div");
  const ev = { type: "evidence", kind: "email", source: "e1", content: HOSTILE, excerpt: "<img src=x onerror=alert(1)>",
    metadata: { evidence_id: "e1", sender: "<script>x</script>", recipients: ["<i>r</i>"], date: "d", subject: "<img onerror=1>", source_path: "p" } };
  assert.ok(P.validateEvent(ev).ok);
  R.createEvidence(doc, c).show(ev);
  const tags = []; walk(c, n => tags.push(n.tag));
  assert.ok(!tags.some(t => ["script", "img", "b"].includes(t)), tags.join());
  const pre = []; walk(c, n => { if (n.className === "ev-body") pre.push(n); });
  assert.strictEqual(pre[0].textContent, HOSTILE);
  assert.ok(c.textContent.includes("<script>x</script>"));
  const marks = []; walk(c, n => { if (n.tag === "mark") marks.push(n); });
  assert.strictEqual(marks[0].textContent, "<img src=x onerror=alert(1)>");
});
test("archived HTML snapshot is shown as plain text", () => {
  const c = new Node("div");
  const html = "<html><body onload=alert(1)><script>evil()</script></body></html>";
  R.createEvidence(doc, c).show({ kind: "snapshot", source: "s", content: html,
    metadata: { url: "http://example.invalid/", timestamp: "19990101000000" } });
  const pre = []; walk(c, n => { if (n.className === "ev-body") pre.push(n); });
  assert.strictEqual(pre[0].textContent, html);
});
test("hostile transcript text is inert", () => {
  const c = new Node("div");
  R.createTranscript(doc, c).update({ turn_id: "t", role: "user", text: "<img src=x onerror=alert(1)>", final: false });
  const tags = []; walk(c, n => tags.push(n.tag));
  assert.ok(!tags.includes("img"));
  assert.strictEqual(c.firstChild.lastChild.textContent, "<img src=x onerror=alert(1)>");
});
test("cold trail shows only the fixed message", () => {
  const c = new Node("div");
  R.createEvidence(doc, c).show({ kind: "email", source: null, content: null });
  assert.strictEqual(c.textContent, "The trail goes cold.");
});
test("transcript partials replace same turn, finals mark complete", () => {
  const c = new Node("div"), t = R.createTranscript(doc, c);
  t.update({ turn_id: "a", role: "spirit", text: "Hel", final: false });
  t.update({ turn_id: "a", role: "spirit", text: "Hello", final: true });
  assert.strictEqual(c.children.length, 1);
  assert.strictEqual(c.firstChild.lastChild.textContent, "Hello");
  assert.ok(!c.firstChild.className.includes("interim"));
});
test("UI sources never use unsafe sinks", () => {
  for (const f of fs.readdirSync(__dirname).filter(f => f.endsWith(".js") && !f.endsWith(".test.js"))) {
    const s = fs.readFileSync(path.join(__dirname, f), "utf8");
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new Function/.test(s), f);
  }
});
