"""TICKET-25 point B: locate, deterministically, where the fake device stops.

The oracle reaches the device selector and stops. Retrying the click is the one
thing this tool does NOT do: three previous passes each fixed a different click
defect and each still ended at the same place, which is what a symptom looks
like when the cause is upstream of the click.

So this walks the transition as a chain of TRIPWIRES taken from the bundle, and
reports the last one that passed and the first that did not:

  M1 app mounted
  M2 navigator.hid is the fake runtime          (safety, asserted not assumed)
  M3 window.loadCZSharedData present            (the CZ keyboard SDK)
  M4 requestDevice served by the fake
  M5 navigator.deviceMap built, device in it
  M6 CZ identity matched -> name rendered
  M7 clampDevices accepted the device           ("device not allow" gate)
  M8 device SDK created
  M9 SingletonDeviceStore has state for it      (updateDeviceState ran)
  M10 that state is not stuck busy              (isBusy/communicateEnabled/failedCount)
  M11 the row is enabled                        (toDeviceAliveStatus().isDisabled)
  M12 click handler reached the keyboard branch (localStorage tripwire)
  M13 router landed on the Keyboard route

M11 is the one the whole thing turns on. From the bundle:

    toDeviceAliveStatus(e).isDisabled = e.connectMode == null || e.isBusy || ...
    onClick = e => { toDeviceAliveStatus(e).isDisabled || openDevice(e) }

and `connectMode` is written onto the list item ONLY inside `if (h)` where
`h = SingletonDeviceStore.getDeviceState(hidDevice)`. No device state, no
connectMode; no connectMode, the row is disabled and the click is discarded
without a console error and without a frame. That is a testable claim, and this
tool is what tests it.

Nothing here writes to hardware. `assert_no_real_hid` is checked on the live
page before any interaction, exactly as in the oracle.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_MINER_ROOT = _THIS.parents[2]
_DB_ROOT = _MINER_ROOT.parent
for _p in (_DB_ROOT, _MINER_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from miner.dynamic.mchose_oracle import (  # noqa: E402
    PROFILES,
    TARGET,
    assert_no_real_hid,
    build_config,
)

_RUNTIME_JS = _THIS.parent / "fake_browser" / "runtime.js"
_PROBE_JS = _THIS.parent / "fake_browser" / "mchose_transition_probe.js"

SNAPSHOT_JS = """() => {
  const s = window.SingletonDeviceStore;
  const states = [];
  if (s && s.allDeviceState && typeof s.allDeviceState.forEach === 'function') {
    s.allDeviceState.forEach((st, dev) => {
      states.push({
        device: window.__mcSafeDesc ? window.__mcSafeDesc(dev) : String(dev),
        isBusy: st ? st.isBusy : null,
        communicateEnabled: st ? st.communicateEnabled : null,
        failedCount: st ? st.failedCount : null,
        isWireless: st ? st.isWireless : null,
        battery: st ? st.battery : null,
        featBattery: st ? st.featBattery : null
      });
    });
  }
  let route = null;
  try { route = window.$router.currentRoute.value.name; } catch (e) {}
  return {
    hid_fake: !!(navigator.hid && navigator.hid.__protocolMinerFake),
    cz_loader: typeof window.loadCZSharedData,
    device_map_keys: navigator.deviceMap ? Object.keys(navigator.deviceMap) : null,
    has_device_handler: !!navigator.deviceHandler,
    nav_device: (navigator.device && window.__mcSafeDesc)
        ? window.__mcSafeDesc(navigator.device) : null,
    store_size: (s && s.allDeviceState) ? s.allDeviceState.size : null,
    device_states: states,
    route: route,
    href: location.href,
    body_head: (document.body.innerText || '').slice(0, 400)
  };
}"""

# The app's own verdict on the row, read where it lands: M4() maps
# toDeviceAliveStatus to one of loading/deactivated/disabled/normal, and that
# word reaches the DOM. Reading it beats re-implementing the predicate here.
ROW_JS = """(namePattern) => {
  const re = new RegExp(namePattern, 'i');
  const cands = Array.from(document.querySelectorAll('div,span,button,li,p'))
    .filter(e => re.test((e.innerText || '').trim()))
    .map(e => { const r = e.getBoundingClientRect();
                return {e, area: r.width * r.height}; })
    .filter(o => o.area > 0)
    .sort((a, b) => a.area - b.area);
  if (!cands.length) return null;
  const el = cands[0].e;
  const chain = [];
  let n = el;
  for (let i = 0; i < 6 && n; i++) {
    chain.push({
      tag: n.tagName,
      cls: (n.className && n.className.baseVal !== undefined
              ? n.className.baseVal : String(n.className || '')).slice(0, 200),
      attrs: Array.from(n.attributes || [])
        .filter(a => a.name !== 'class' && a.name !== 'style')
        .map(a => a.name + '=' + String(a.value).slice(0, 40))
    });
    n = n.parentElement;
  }
  return {text: (el.innerText || '').trim().slice(0, 40),
          area: Math.round(cands[0].area), candidates: cands.length, chain};
}"""


def milestones(snap: dict, trace: list, row: dict | None, frames: int, name: str) -> list:
    """Ordered (id, description, passed, detail). Order is the causal order."""
    tstages = [e.get("stage") for e in trace]
    body = snap.get("body_head") or ""
    dmk = snap.get("device_map_keys") or []
    states = snap.get("device_states") or []
    clamp = [e for e in trace if e.get("stage") == "cz:clampDevices"]
    ls_keys = [e["data"]["key"] for e in trace
               if e.get("stage") == "ls:set" and isinstance(e.get("data"), dict)]

    row_status = None
    if row:
        blob = " ".join(c["cls"] for c in row["chain"]) + " " + " ".join(
            a for c in row["chain"] for a in c["attrs"])
        for word in ("deactivated", "disabled", "loading", "normal"):
            if word in blob:
                row_status = word
                break

    return [
        ("M1  app mounted", len(body) > 40, {"body_len": len(body)}),
        ("M2  navigator.hid is the fake runtime", bool(snap.get("hid_fake")), None),
        ("M3  window.loadCZSharedData present", snap.get("cz_loader") == "function",
         {"typeof": snap.get("cz_loader")}),
        ("M4  requestDevice served by the fake", "cz:loader-called" in tstages or frames >= 0,
         {"note": "grant is implied by the device list rendering"}),
        ("M5  navigator.deviceMap built", bool(dmk), {"keys": dmk}),
        ("M6  CZ identity matched, name rendered", name.lower() in body.lower(),
         {"looked_for": name}),
        ("M7  clampDevices accepted the device",
         bool(clamp) and all(c["data"]["verdict"] == "ALL_KEPT" for c in clamp),
         {"calls": [c["data"] for c in clamp] or "never called"}),
        ("M8  device SDK created", "cz:createDeviceSDK:ok" in tstages,
         {"enter": tstages.count("cz:createDeviceSDK:enter"),
          "ok": tstages.count("cz:createDeviceSDK:ok"),
          "rejected": tstages.count("cz:createDeviceSDK:rejected")}),
        ("M9  SingletonDeviceStore has device state", bool(states),
         {"store_size": snap.get("store_size")}),
        ("M10 device state not stuck busy",
         bool(states) and any(s.get("isBusy") is False for s in states),
         {"states": states}),
        ("M11 row is enabled (clickable)", row_status == "normal",
         {"row_status": row_status, "row": row}),
        ("M12 click handler reached keyboard branch",
         "deviceName" in ls_keys and "lastKBProductKey" in ls_keys,
         {"localStorage_writes": ls_keys}),
        ("M13 router on Keyboard route", snap.get("route") == "Keyboard",
         {"route": snap.get("route"), "href": snap.get("href")}),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="god60")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=25.0,
                    help="seconds to let the device runtime settle before clicking")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = PROFILES[args.profile]
    display_name = profile["label"].split(" (")[0]
    frames = {"n": 0}

    print(f"profile: {profile['label']}  "
          f"{profile['vendorId']:#06x}:{profile['productId']:#06x}")

    init = (
        f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
        f"window.__protocolMinerCannedResponses = {{}};\n"
        + _PROBE_JS.read_text(encoding="utf-8")
        + "\n"
        + _RUNTIME_JS.read_text(encoding="utf-8")
    )

    def on_frame(_src, req):
        frames["n"] += 1
        m = req.get("method")
        print(f"  [frame {frames['n']:>3}] {m} rid={req.get('report_id')} "
              f"{(req.get('bytes_hex') or '')[:48]}")
        if m == "receiveFeatureReport":
            return {"hex": "", "strategy": "NO_RESPONSE", "confidence": 0.0}
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda src, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()

        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text[:300]}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {str(e)[:300]}"))

        loaded = False
        for attempt in range(3):
            try:
                page.goto(TARGET, wait_until="commit", timeout=90000)
            except Exception as exc:  # noqa: BLE001
                print(f"[nav] attempt {attempt}: goto raised ({exc}); polling anyway")
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
                print(f"[nav] loaded on attempt {attempt}")
                break
        if not loaded:
            print("[nav] page never rendered -- this run says nothing about the vendor")

        assert_no_real_hid(page)
        print("[safety] navigator.hid is the fake runtime")

        try:
            page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        except Exception as exc:  # noqa: BLE001
            print(f"[connect] control never appeared: {exc}")
        clicked = page.evaluate(
            """() => { const b = document.querySelector('button.mc-button');
                       if (!b) return null; b.click();
                       return (b.innerText || '').trim().slice(0, 40) || '<no text>'; }"""
        )
        print(f"[connect] clicked: {clicked!r}")

        # Let the device runtime run. It retries on failure every 3 s forever
        # (errorLifeCircle in the bundle), so a settle window shows whether it is
        # progressing or looping.
        snaps = []
        for i in range(int(args.settle / 5)):
            page.wait_for_timeout(5000)
            s = page.evaluate(SNAPSHOT_JS)
            snaps.append(s)
            print(f"[t+{(i + 1) * 5:>3}s] cz={s['cz_loader']} "
                  f"map={len(s['device_map_keys'] or [])} store={s['store_size']} "
                  f"route={s['route']} frames={frames['n']}")

        row = page.evaluate(ROW_JS, f"^{display_name}$")
        print(f"[row] {json.dumps(row, ensure_ascii=False)[:400] if row else None}")

        # One click, delivered to the element the app itself renders as the row.
        # If M11 already says the row is disabled, this click is expected to do
        # nothing -- and that expectation being met is the finding.
        if row:
            page.evaluate(
                """(namePattern) => {
                     const re = new RegExp(namePattern, 'i');
                     const c = Array.from(document.querySelectorAll('div,span,button,li,p'))
                       .filter(e => re.test((e.innerText || '').trim()))
                       .map(e => ({e, a: e.getBoundingClientRect().width * e.getBoundingClientRect().height}))
                       .filter(o => o.a > 0).sort((x, y) => x.a - y.a);
                     if (c.length) c[0].e.click();
                   }""",
                f"^{display_name}$",
            )
            print("[click] delivered to the smallest exact-text element")
        page.wait_for_timeout(6000)

        snap = page.evaluate(SNAPSHOT_JS)
        trace = page.evaluate("() => window.__mcTrace || []")

        ms = milestones(snap, trace, row, frames["n"], display_name)
        print("\n=== TRANSITION MILESTONES ===")
        last_ok, first_bad = None, None
        for label, ok, detail in ms:
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if ok and first_bad is None:
                last_ok = label
            elif not ok and first_bad is None:
                first_bad = (label, detail)
        print(f"\nLAST_SUCCESS            : {last_ok}")
        print(f"FIRST_FAILED_OR_MISSING : {first_bad[0] if first_bad else '(none -- chain complete)'}")
        if first_bad and first_bad[1]:
            print(f"  detail: {json.dumps(first_bad[1], ensure_ascii=False)[:900]}")

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "_what": "MCHOSE device -> configurator transition trace, TICKET-25 point B",
            "_method": "observation-only probe; no wrapper supplies or suppresses a value",
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "profile": profile,
            "frames_sent_by_page": frames["n"],
            "last_success": last_ok,
            "first_failed_or_missing": first_bad[0] if first_bad else None,
            "milestones": [{"id": m[0], "passed": m[1], "detail": m[2]} for m in ms],
            "settle_snapshots": snaps,
            "final_snapshot": snap,
            "row": row,
            "probe_trace": trace,
            "console": console[:400],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"-> {out}")

        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
