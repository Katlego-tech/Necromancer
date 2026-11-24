const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { parseFrame } = require("../../ui/protocol.js");
const { create } = require("../../ui/mascot-slot.js");

const LOG = path.join(__dirname, "..", "fixtures", "voice_events_fake.jsonl");
const lines = fs.readFileSync(LOG, "utf8").split("\n").filter(Boolean);

test("recorded engine events all pass the UI validator", () => {
  assert.ok(lines.length > 10);
  const rejects = [];
  for (const line of lines) {
    const r = parseFrame(line);
    if (!r.ok) rejects.push(r.error + ": " + line.slice(0, 80));
  }
  assert.deepStrictEqual(rejects, []);
});

test("replayed log drives the mascot slot through the voice states and shows evidence", () => {
  const states = [];
  const scope = { Necromancer: { mount: () => ({
    setState: (s) => states.push(s), setAppearance: () => {}, setSpeakingLevel: () => {} }) } };
  const slot = create(scope);
  slot.mount({});
  const evidence = [];
  for (const line of lines) {
    const ev = parseFrame(line).event;
    if (ev.type === "state") slot.setState(ev.state);
    if (ev.type === "evidence") evidence.push(ev);
  }
  for (const s of ["listening", "speaking", "summoning"]) assert.ok(states.includes(s), s);
  assert.strictEqual(evidence.length, 1);
  assert.strictEqual(evidence[0].source, "ev-0001");
});
