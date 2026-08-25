"""TICKET-25: why the BY reset dialog appears under one reply size and not another.

Runs the SAME walk twice, changing exactly one thing -- the size of the synthetic
`receiveFeatureReport` reply -- and diffs the observable state at the moment the
reset control is clicked. Under-sized replies made the dialog open every time;
correctly-sized replies made it stop. Until that is explained, "the reset sends
nothing" is a claim about the harness wearing the clothes of a claim about the
protocol.

## What the bundle already rules out

From `purify.es`, the `otherSetting` component:

```js
const v = async Y => {
  if (Y.key === "keyboardVersion")  { if (Y.status === "disable") return; ... }
  else if (Y.key === "receiverVersion") { if (Y.status === "disable") return; ... }
  else Y.key === "restoreFactorySetting" && er.confirm(...).then(async () => { ... })
};
// template:
Ce(McButton, {disabled: O.status === "disable", onClick: ee => v(O)}, ...)
```

There is **no status guard on the reset branch** -- only the two firmware items
have one. So if `v(item)` runs at all, the confirm dialog is opened
unconditionally. That narrows the question to: does the click reach the handler?

This tool therefore instruments the click itself rather than guessing:

* a capture-phase `click` listener at document level, so a click that lands on
  the button is recorded even if nothing downstream happens;
* a `MutationObserver` for the dialog, so its appearance is observed rather than
  polled for and possibly missed;
* `window.onerror` / `unhandledrejection`, because an async handler that rejects
  silently looks exactly like a click that did nothing.

No real device. `assert_no_real_hid` runs on the live page before anything else.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import functools
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_MINER_ROOT = _THIS.parents[2]
_DB_ROOT = _MINER_ROOT.parent
for _p in (_DB_ROOT, _MINER_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from miner.dynamic.mchose_oracle import TARGET, _RUNTIME_JS, assert_no_real_hid  # noqa: E402
from miner.dynamic.mchose_by_oracle import BY_PROFILES, battery_reply, build_config  # noqa: E402

print = functools.partial(print, flush=True)  # noqa: A001

PROBE_JS = """() => {
  if (window.__byProbe) return;
  window.__byProbe = {clicks: [], dialogs: [], errors: []};
  document.addEventListener('click', e => {
    const t = e.target;
    window.__byProbe.clicks.push({
      tag: t.tagName,
      cls: String(t.className || '').slice(0, 70),
      text: (t.innerText || '').trim().slice(0, 30),
      closestButton: !!(t.closest && t.closest('button')),
      buttonDisabled: !!(t.closest && t.closest('button') && t.closest('button').disabled),
      at: Date.now()
    });
  }, true);
  new MutationObserver(muts => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        const cls = String(n.className || '');
        if (cls.includes('message-box') || cls.includes('el-overlay') || cls.includes('el-dialog')) {
          window.__byProbe.dialogs.push({cls: cls.slice(0, 70),
                                         text: (n.innerText || '').trim().slice(0, 200),
                                         at: Date.now()});
        }
      }
    }
  }).observe(document.body, {childList: true, subtree: true});
  window.addEventListener('error', e =>
    window.__byProbe.errors.push('error: ' + String(e.message).slice(0, 160)));
  window.addEventListener('unhandledrejection', e =>
    window.__byProbe.errors.push('rejection: ' + String(e.reason).slice(0, 160)));
}"""

STATE_JS = """() => {
  const btns = Array.from(document.querySelectorAll('button')).filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && e.offsetParent !== null;
  });
  const mc = btns.filter(e => (e.className || '').includes('mc-button'));
  let route = null;
  try { route = window.$router.currentRoute.value.name; } catch (e) {}
  return {
    route: route,
    hasHandler: !!navigator.deviceHandler,
    handlerWired: (navigator.deviceHandler || {}).isWired ?? null,
    whichDevice: (navigator.device || {}).whichDeivice ?? null,
    visible_mc_buttons: mc.map(e => ({text: (e.innerText || '').trim().slice(0, 24),
                                      disabled: !!e.disabled,
                                      cls: String(e.className || '').slice(0, 60)})),
    active_tab: (document.querySelector('.el-tabs__item.is-active,[id^=tab-][aria-selected=true]') || {}).id || null,
    body_head: (document.body.innerText || '').slice(0, 200),
    probe: window.__byProbe || null
  };
}"""


def run_once(pw, profile: dict, reply_size: int, settle: float) -> dict:
    frames: list[dict] = []
    reply = battery_reply(100, size=reply_size)
    state = {"action": "boot"}

    def on_frame(_src, req):
        method = req.get("method")
        rec = {"ui_action": state["action"], "method": method,
               "report_id": req.get("report_id"),
               "payload_hex": req.get("bytes_hex")}
        frames.append(rec)
        if method == "receiveFeatureReport":
            return {"hex": reply.hex(), "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA"}
        return {"ack": True, "strategy": "NO_RESPONSE"}

    init = (f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
            f"window.__protocolMinerCannedResponses = {{}};\n"
            + _RUNTIME_JS.read_text(encoding="utf-8"))

    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(ignore_https_errors=True)
    ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
    ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
    ctx.add_init_script(init)
    page = ctx.new_page()
    console: list[str] = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:200]}"))
    page.on("pageerror", lambda e: console.append(f"pageerror: {str(e)[:200]}"))

    try:
        loaded = False
        for _ in range(6):
            try:
                page.goto(TARGET, wait_until="commit", timeout=90000)
            except Exception:  # noqa: BLE001
                pass
            for _ in range(45):
                try:
                    if page.evaluate("() => !!document.querySelector('#app') "
                                     "&& document.body.innerText.length > 40"):
                        loaded = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(1000)
            if loaded:
                break
        if not loaded:
            return {"reply_size": reply_size, "error": "page never rendered"}

        assert_no_real_hid(page)
        page.evaluate(PROBE_JS)

        state["action"] = "connect"
        page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        page.evaluate("() => document.querySelector('button.mc-button').click()")
        page.wait_for_timeout(int(settle * 1000))

        state["action"] = "open_device"
        page.evaluate(
            """(p) => { const re = new RegExp(p, 'i');
                 const c = Array.from(document.querySelectorAll('div,span,button,li,p'))
                   .filter(e => re.test((e.innerText || '').trim()))
                   .map(e => { const r = e.getBoundingClientRect(); return {e, a: r.width * r.height}; })
                   .filter(o => o.a > 0).sort((x, y) => x.a - y.a);
                 if (c.length) c[0].e.click(); }""",
            f"^{profile['label']}$")
        page.wait_for_timeout(20000)
        page.evaluate(PROBE_JS)   # the route change replaced the body node

        page.evaluate("() => { const e = document.getElementById('tab-otherSetting');"
                      "        if (e) e.click(); }")
        page.wait_for_timeout(4000)

        before = page.evaluate(STATE_JS)

        state["action"] = "reset_click"
        clicked = page.evaluate(
            """() => { const b = Array.from(document.querySelectorAll('button'))
                   .filter(e => { const r = e.getBoundingClientRect();
                                  return r.width > 0 && r.height > 0 && e.offsetParent !== null; })
                   .filter(e => (e.className || '').includes('mc-button') && !e.disabled);
                 if (!b.length) return null;
                 const target = b[b.length - 1];
                 target.click();
                 return {text: (target.innerText || '').trim(), cls: String(target.className||'').slice(0,60)}; }""")
        page.wait_for_timeout(6000)
        after = page.evaluate(STATE_JS)

        dialog_open = page.evaluate(
            """() => { const d = document.querySelector('.el-message-box, .el-dialog, [role=dialog]');
                 if (!d) return null;
                 const r = d.getBoundingClientRect();
                 if (r.width < 100) return null;
                 return {text: (d.innerText || '').trim().slice(0, 300),
                         buttons: Array.from(d.querySelectorAll('button'))
                                    .map(b => (b.innerText || '').trim())}; }""")

        confirmed_frames = 0
        confirm_label = None
        if dialog_open:
            state["action"] = "reset_confirm"
            n0 = len(frames)
            confirm_label = page.evaluate(
                """() => { const d = document.querySelector('.el-message-box, .el-dialog, [role=dialog]');
                     const bs = Array.from(d.querySelectorAll('button'));
                     const p = bs.find(b => (b.className || '').includes('primary'))
                            || bs.find(b => (b.className || '').includes('red-btn'))
                            || bs[bs.length - 1];
                     if (!p) return null; p.click();
                     return (p.innerText || '').trim(); }""")
            page.wait_for_timeout(7000)
            confirmed_frames = len(frames) - n0

        return {
            "reply_size": reply_size,
            "state_before_click": before,
            "clicked": clicked,
            "state_after_click": after,
            "dialog": dialog_open,
            "confirm_button": confirm_label,
            "frames_after_confirm": confirmed_frames,
            "frames": frames,
            "console_tail": [c for c in console if "__PM_TRACE__" not in c][-25:],
        }
    finally:
        ctx.close()
        browser.close()


def compare(a: dict, b: dict) -> dict:
    """LAST_COMMON_STATE -> FIRST_DIVERGENT_STATE over the observable fields."""
    checks = [
        ("route reached", lambda r: (r.get("state_before_click") or {}).get("route")),
        ("deviceHandler present", lambda r: (r.get("state_before_click") or {}).get("hasHandler")),
        ("deviceHandler wired", lambda r: (r.get("state_before_click") or {}).get("handlerWired")),
        ("active tab", lambda r: (r.get("state_before_click") or {}).get("active_tab")),
        ("visible mc-buttons", lambda r: [x["text"] for x in
                                          (r.get("state_before_click") or {}).get("visible_mc_buttons", [])]),
        ("reset button disabled", lambda r: [x["disabled"] for x in
                                             (r.get("state_before_click") or {}).get("visible_mc_buttons", [])]),
        ("click landed on a button", lambda r: bool(r.get("clicked"))),
        ("click target text", lambda r: (r.get("clicked") or {}).get("text")),
        ("click events seen by document",
         lambda r: len((((r.get("state_after_click") or {}).get("probe")) or {}).get("clicks", []))),
        ("dialog nodes observed",
         lambda r: len((((r.get("state_after_click") or {}).get("probe")) or {}).get("dialogs", []))),
        ("dialog present after click", lambda r: bool(r.get("dialog"))),
        ("frames after confirm", lambda r: r.get("frames_after_confirm")),
        ("page errors",
         lambda r: (((r.get("state_after_click") or {}).get("probe")) or {}).get("errors", [])[:4]),
    ]
    rows, last_common, first_div = [], None, None
    for name, get in checks:
        va, vb = get(a), get(b)
        same = va == vb
        rows.append({"check": name, "under_sized": va, "correct_sized": vb, "same": same})
        if same and first_div is None:
            last_common = name
        elif not same and first_div is None:
            first_div = {"check": name, "under_sized": va, "correct_sized": vb}
    return {"rows": rows, "LAST_COMMON_STATE": last_common,
            "FIRST_DIVERGENT_STATE": first_div}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(BY_PROFILES), default="k99")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=30.0)
    ap.add_argument("--sizes", default="64,519")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = BY_PROFILES[args.profile]
    sizes = [int(x) for x in args.sizes.split(",")]
    runs = {}
    with sync_playwright() as pw:
        for size in sizes:
            print(f"\n=== reply size {size} bytes")
            r = run_once(pw, profile, size, args.settle)
            runs[str(size)] = r
            if "error" in r:
                print(f"  {r['error']}")
                continue
            print(f"  route={r['state_before_click']['route']!r} "
                  f"buttons={[b['text'] for b in r['state_before_click']['visible_mc_buttons']]}")
            print(f"  clicked={r['clicked']}")
            print(f"  dialog={'YES' if r['dialog'] else 'no'} "
                  f"confirm={r['confirm_button']!r} frames_after_confirm={r['frames_after_confirm']}")

    diff = None
    if len(sizes) == 2 and all("error" not in runs[str(s)] for s in sizes):
        diff = compare(runs[str(sizes[0])], runs[str(sizes[1])])
        print("\n=== STATE DIFF ===")
        for row in diff["rows"]:
            mark = "  " if row["same"] else "**"
            print(f"{mark} {row['check']:<32} {str(row['under_sized'])[:40]:<42} "
                  f"{str(row['correct_sized'])[:40]}")
        print(f"\nLAST_COMMON_STATE      : {diff['LAST_COMMON_STATE']}")
        print(f"FIRST_DIVERGENT_STATE  : {diff['FIRST_DIVERGENT_STATE']}")

    doc = {
        "_what": "BY reset-dialog state diff across synthetic reply sizes, TICKET-25",
        "_method": ("one variable changed between runs: the size of the synthetic "
                    "receiveFeatureReport reply. Everything else identical."),
        "_bundle_fact": ("the reset branch of the otherSetting handler has NO status guard, so "
                         "if v(item) runs the confirm dialog opens unconditionally; the question "
                         "is whether the click reaches the handler"),
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "runs": runs,
        "state_diff": diff,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
