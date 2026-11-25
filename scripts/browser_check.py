"""Real-browser check of the Necromancer UI, driven over the Chrome DevTools protocol.

No Playwright needed: it launches the Chromium build in ~/.cache/ms-playwright (or --chrome PATH)
and talks to it with aiohttp (already in the project venv).

    .venv/bin/python scripts/browser_check.py layout
    NECRO_LIVE=1 .venv/bin/python scripts/browser_check.py live --db data/enron/enron.sqlite
    NECRO_LIVE=1 .venv/bin/python scripts/browser_check.py live --db ... --mic

`layout`  (free) mock session: for every mascot state and several viewport sizes, checks that no
          part of the mascot, after clipping by its ancestors, overlaps the transcript or evidence
          panel (spec T3.6); also checks reduced-motion stops animations.
`paused`  (free) mock page ?mock=paused: the paused banner is visible, the evidence stays readable and
          not covered, the restart button is visible; screenshot saved. `layout` repeats it at every viewport.
`live`    (billed Bedrock calls) starts the real server, types one question into the page, records
          the mascot states, WebSocket traffic, transcript and evidence the browser actually showed.
`--mic`   also clicks "Start microphone" with Chrome's FAKE microphone (a beep, not speech) to prove
          getUserMedia, the audio worklet and the /io upload path work. It is not a speech test.

Screenshots and a JSON report go to --out (default /tmp/necro_browser). Credentials are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
STATES = ["idle", "listening", "speaking", "summoning", "shapeshifting", "alert"]
VIEWPORTS = [(1280, 800), (1024, 700), (768, 900), (390, 844)]
QUESTION = "What was Ken Lay told about Raptor?"

WS_HOOK = r"""
(function () {
  var O = window.WebSocket;
  window.__ws = {sentBin: 0, sentBytes: 0, sentText: [], recvBin: 0, recvBytes: 0, recvText: 0};
  function W(u, p) {
    var s = p ? new O(u, p) : new O(u);
    if (String(u).indexOf("/io") >= 0) {
      var os = s.send.bind(s);
      s.send = function (d) {
        if (typeof d === "string") window.__ws.sentText.push(d.slice(0, 80));
        else { window.__ws.sentBin++; window.__ws.sentBytes += (d.byteLength || d.size || 0); }
        return os(d);
      };
      s.addEventListener("message", function (m) {
        if (typeof m.data === "string") window.__ws.recvText++;
        else { window.__ws.recvBin++; window.__ws.recvBytes += m.data.byteLength; }
      });
    }
    return s;
  }
  W.prototype = O.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) { W[k] = O[k]; });
  window.WebSocket = W;
  window.__states = [];
  document.addEventListener("DOMContentLoaded", function () {
    new MutationObserver(function () {
      var v = document.body.getAttribute("data-state");
      var a = window.__states;
      if (a[a.length - 1] !== v) a.push(v);
    }).observe(document.body, {attributes: true, attributeFilter: ["data-state"]});
  });
})();
"""

OVERLAP_JS = r"""
(function () {
  function rect(el) { var b = el.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom}; }
  function inter(a, b) {
    var l = Math.max(a.l, b.l), t = Math.max(a.t, b.t), r = Math.min(a.r, b.r), bt = Math.min(a.b, b.b);
    return (r > l && bt > t) ? {l: l, t: t, r: r, b: bt} : null;
  }
  function clipped(el) {
    var box = rect(el);
    for (var p = el.parentElement; p && box; p = p.parentElement) {
      var cs = getComputedStyle(p);
      if (cs.overflow !== "visible" || cs.overflowX !== "visible" || cs.overflowY !== "visible" ||
          cs.contain.indexOf("paint") >= 0) box = inter(box, rect(p));
    }
    return box;
  }
  var targets = [["transcript", document.getElementById("transcript")],
                 ["evidence", document.getElementById("evidence-region")],
                 ["evidence-body", document.getElementById("evidence-body")]];
  var els = [].slice.call(document.querySelectorAll("#mascot-region, #mascot-region *"));
  var hits = [], painted = 0;
  els.forEach(function (el) {
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return;
    var box = clipped(el);
    if (!box) return;
    painted++;
    targets.forEach(function (t) {
      if (t[1] && inter(box, rect(t[1]))) hits.push((el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || el.tagName) + " overlaps " + t[0]);
    });
  });
  var reg = rect(document.getElementById("mascot-region"));
  return {hits: hits, painted: painted, mascotRegion: reg,
          animations: document.getAnimations().filter(function (a) { return a.playState === "running"; }).length};
})()
"""


PAUSED_JS = r"""
(function () {
  function vis(id) { var e = document.getElementById(id); if (!e || e.hidden) return false;
    var b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== "hidden"; }
  var ev = document.getElementById("evidence-body");
  ev.scrollIntoView({block: "center"});
  var b = ev.getBoundingClientRect();
  var top = document.elementFromPoint(b.left + b.width / 2, Math.min(b.top + 10, b.bottom - 1));
  return {banner: vis("paused-banner"), restart: vis("restart"), code_form: vis("code-form"),
          msg: document.getElementById("paused-msg").textContent,
          live: document.body.getAttribute("data-live"),
          evidence_chars: ev.textContent.length,
          evidence_not_covered: !!top && document.getElementById("evidence-region").contains(top),
          mic_hidden: getComputedStyle(document.getElementById("mic")).display === "none"};
})()
"""
class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.problems = ws, 0, {}, []
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(msg.data)
            if "id" in d and d["id"] in self.pending:
                self.pending.pop(d["id"]).set_result(d)
            elif d.get("method") == "Runtime.exceptionThrown":
                x = d["params"]["exceptionDetails"]
                self.problems.append("exception: " + (x.get("exception", {}).get("description") or x.get("text", ""))[:200])
            elif d.get("method") == "Runtime.consoleAPICalled" and d["params"]["type"] in ("error", "assert"):
                self.problems.append("console." + d["params"]["type"] + ": " +
                                     " ".join(str(a.get("value", a.get("description", ""))) for a in d["params"]["args"])[:200])
            elif d.get("method") == "Log.entryAdded" and d["params"]["entry"]["level"] == "error":
                e = d["params"]["entry"]
                self.problems.append("log: " + e.get("text", "")[:120] + " " + e.get("url", "")[:80])

    async def send(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        res = await asyncio.wait_for(fut, 30)
        if "error" in res:
            raise RuntimeError(f"{method}: {res['error']}")
        return res.get("result", {})

    async def eval(self, expr, await_promise=False):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError("eval failed: " + json.dumps(r["exceptionDetails"])[:300])
        return r["result"].get("value")

    async def shot(self, path):
        r = await self.send("Page.captureScreenshot", format="png")
        import base64
        Path(path).write_bytes(base64.b64decode(r["data"]))


def find_chrome(explicit):
    if explicit:
        return explicit
    c = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
    if not c:
        sys.exit("no Chromium found; pass --chrome PATH")
    return c[-1]


async def launch_chrome(chrome, port, mic):
    profile = f"/tmp/necro_chrome_{port}"
    args = [chrome, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
            "--window-size=1280,900", "--disable-gpu", "about:blank"]
    if mic:
        args += ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    async with aiohttp.ClientSession() as s:
        for _ in range(60):
            try:
                async with s.get(f"http://127.0.0.1:{port}/json/list") as r:
                    pages = [p for p in await r.json() if p.get("type") == "page"]
                    if pages:
                        return proc, pages[0]["webSocketDebuggerUrl"]
            except Exception:
                pass
            await asyncio.sleep(0.5)
    proc.kill()
    sys.exit("Chrome did not start")


async def open_page(chrome, port, mic):
    proc, wsurl = await launch_chrome(chrome, port, mic)
    session = aiohttp.ClientSession()
    cdp = CDP(await session.ws_connect(wsurl, max_msg_size=0))
    for m in ("Page.enable", "Runtime.enable", "Log.enable"):
        await cdp.send(m)
    await cdp.send("Network.enable")
    await cdp.send("Network.setCacheDisabled", cacheDisabled=True)
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=WS_HOOK)
    return proc, session, cdp


async def navigate(cdp, url, wait_js="document.readyState==='complete'", timeout=20):
    await cdp.send("Page.navigate", url=url)
    end = time.time() + timeout
    while time.time() < end:
        try:
            if await cdp.eval(wait_js):
                return
        except RuntimeError:
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("page did not load: " + url)


async def check_paused(cdp, url, tag, out):
    await navigate(cdp, url)
    r = None
    for _ in range(40):
        await asyncio.sleep(0.25)
        r = await cdp.eval(PAUSED_JS)
        if r["banner"]:
            break
    await asyncio.sleep(0.3)
    r = await cdp.eval(PAUSED_JS)
    await cdp.shot(f"{out}/paused_{tag}.png")
    r["ok"] = bool(r["banner"] and r["restart"] and not r["code_form"] and r["live"] == "paused"
                   and r["evidence_chars"] > 0 and r["evidence_not_covered"] and r["mic_hidden"])
    r["viewport"] = tag
    return r
async def with_http_chrome(args, fn):
    proc, session, cdp = await open_page(find_chrome(args.chrome), args.cdp_port, False)
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(args.http_port), "--bind", "127.0.0.1"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.sleep(1)
        return await fn(cdp)
    finally:
        srv.kill()
        await session.close()
        proc.kill()
async def run_paused(args, out):
    base = f"http://127.0.0.1:{args.http_port}/ui/index.html?mock=paused"
    async def go(cdp):
        rows = []
        for w, h in VIEWPORTS if args.all_viewports else VIEWPORTS[:1]:
            await cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
            rows.append(await check_paused(cdp, base, f"{w}x{h}", out))
        return {"rows": rows, "ok": all(r["ok"] for r in rows), "console_problems": cdp.problems}
    return await with_http_chrome(args, go)
async def run_layout(args, out):
    proc, session, cdp = await open_page(find_chrome(args.chrome), args.cdp_port, False)
    base = f"http://127.0.0.1:{args.http_port}/ui/index.html?mock=enron"
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(args.http_port), "--bind", "127.0.0.1"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {"rows": [], "reduced_motion": None}
    try:
        await asyncio.sleep(1)
        for w, h in VIEWPORTS:
            await cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
            await navigate(cdp, base)
            await asyncio.sleep(4)  # let the scripted mock session fill transcript and evidence
            filled = await cdp.eval("document.getElementById('evidence-body').textContent.length")
            await cdp.shot(f"{out}/layout_{w}x{h}.png")
            for costume in ("enron", "wayback"):
                if costume == "wayback":  # apply the committed Wayback costume to the mock page
                    cfg = (ROOT / "mascot" / "costumes" / "wayback.json").read_text(encoding="utf-8")
                    await cdp.eval(f"window.NecroMascotSlot.setAppearance({cfg}); true")
                    await asyncio.sleep(1.5)
                    await cdp.shot(f"{out}/layout_wayback_{w}x{h}.png")
                for st in STATES:
                    await cdp.eval(f"window.NecroMascotSlot.setState({json.dumps(st)}); window.NecroMascotSlot.setSpeakingLevel({1 if st == 'speaking' else 0}); true")
                    worst = {"hits": [], "painted": 0}
                    for _ in range(4):
                        await asyncio.sleep(0.25)
                        r = await cdp.eval(OVERLAP_JS)
                        if len(r["hits"]) > len(worst["hits"]) or not worst["painted"]:
                            worst = r
                    results["rows"].append({"viewport": f"{w}x{h}", "costume": costume, "state": st, "evidence_chars": filled,
                                            "painted_elements": worst["painted"], "overlaps": sorted(set(worst["hits"]))})
        await cdp.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await cdp.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        await navigate(cdp, base)
        await asyncio.sleep(2)
        await cdp.eval("window.NecroMascotSlot.setState('speaking'); true")
        await asyncio.sleep(0.5)
        results["reduced_motion"] = {"running_animations": (await cdp.eval(OVERLAP_JS))["animations"]}
        results["console_problems"] = cdp.problems
        results["paused"] = []
        for w, h in VIEWPORTS:
            await cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
            results["paused"].append(await check_paused(cdp, base.replace("mock=enron", "mock=paused"), f"{w}x{h}", out))
    finally:
        srv.kill()
        await session.close()
        proc.kill()
    return results


async def run_live(args, out):
    if os.environ.get("NECRO_LIVE") != "1":
        sys.exit("live mode makes billed Bedrock calls: set NECRO_LIVE=1 to run it")
    record = f"{out}/events_browser_live.jsonl"
    srv = subprocess.Popen([sys.executable, "-m", "voice.server", "--db", args.db, "--port", str(args.http_port),
                            "--record", record], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    proc = session = cdp = None
    results = {"question": QUESTION}
    try:
        await asyncio.sleep(3)
        if srv.poll() is not None:
            sys.exit("server failed to start: " + srv.stderr.read().decode()[-500:])
        proc, session, cdp = await open_page(find_chrome(args.chrome), args.cdp_port, args.mic)
        p = args.http_port
        url = f"http://127.0.0.1:{p}/ui/index.html?ws=ws://127.0.0.1:{p}/events&io=ws://127.0.0.1:{p}/io"
        await navigate(cdp, url)
        await asyncio.sleep(3)
        results["status_after_load"] = await cdp.eval("document.getElementById('status').textContent")
        results["text_form_visible"] = await cdp.eval("!document.getElementById('text-form').hidden")
        if args.mic:
            await cdp.eval("document.getElementById('mic').click(); true")
            await asyncio.sleep(6)
            results["mic_button"] = await cdp.eval("document.getElementById('mic').textContent")
            ws = await cdp.eval("window.__ws")
            results["mic"] = {"binary_frames_sent": ws["sentBin"], "bytes_sent": ws["sentBytes"]}
            await cdp.shot(f"{out}/live_mic.png")
        await cdp.eval(f"var i=document.getElementById('text-input'); i.value={json.dumps(QUESTION)}; "
                       "document.getElementById('text-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})); true")
        t0, last, last_n, overlap_hits = time.time(), time.time(), 0, set()
        while time.time() - t0 < args.timeout:
            await asyncio.sleep(0.5)
            ws = await cdp.eval("window.__ws")
            if ws["recvBin"] != last_n:
                last_n, last = ws["recvBin"], time.time()
            overlap_hits.update((await cdp.eval(OVERLAP_JS))["hits"])
            if last_n > 0 and time.time() - last > 8:
                break
        await asyncio.sleep(1)
        await cdp.shot(f"{out}/live_after_answer.png")
        ws = await cdp.eval("window.__ws")
        results["ws"] = {k: ws[k] for k in ("sentBin", "sentBytes", "recvBin", "recvBytes", "recvText")}
        results["ws"]["sent_text_frames"] = ws["sentText"]
        results["states_seen"] = await cdp.eval("window.__states")
        results["transcript"] = await cdp.eval("document.getElementById('transcript').innerText")
        results["evidence"] = (await cdp.eval("document.getElementById('evidence-body').innerText"))[:1500]
        results["spirit_label"] = await cdp.eval("document.getElementById('spirit').textContent")
        results["protocol_issues"] = await cdp.eval("document.getElementById('issues').children.length")
        results["mascot_overlaps_during_run"] = sorted(overlap_hits)
        results["console_problems"] = cdp.problems
        results["audio_seconds_received"] = round(ws["recvBytes"] / 48000, 1)
    finally:
        if session:
            await session.close()
        if proc:
            proc.kill()
        srv.terminate()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["layout", "paused", "live"])
    ap.add_argument("--db", default=str(ROOT / "data/enron/enron.sqlite"))
    ap.add_argument("--chrome")
    ap.add_argument("--all-viewports", action="store_true", help="paused: check all four viewports")
    ap.add_argument("--mic", action="store_true")
    ap.add_argument("--out", default="/tmp/necro_browser")
    ap.add_argument("--http-port", type=int, default=8791)
    ap.add_argument("--cdp-port", type=int, default=9333)
    ap.add_argument("--timeout", type=float, default=150)
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)
    runner = {"layout": run_layout, "paused": run_paused, "live": run_live}[args.mode]
    res = asyncio.run(runner(args, out))
    Path(f"{out}/report_{args.mode}.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2)[:6000])
    if args.mode == "paused" and not res["ok"]:
        sys.exit(1)
    if args.mode == "layout" and not all(r["ok"] for r in res["paused"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
