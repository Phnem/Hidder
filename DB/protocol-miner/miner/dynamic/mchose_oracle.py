"""TICKET-25: fake-WebHID oracle for MCHOSE M HUB Web.

Drives the REAL, unmodified vendor client against a FAKE device so that the
vendor's own code builds the frames and we read them off the wire it thinks it
is writing to.

## Why this is safe, structurally rather than by promise

`fake_browser/runtime.js` replaces `navigator.hid` **entirely** before any page
script runs. There is no code path from this harness to a physical device: the
object the page calls `sendReport` on is ours. A factory-reset frame built here
is captured and discarded, and it is captured precisely so that nobody has to
send one to hardware to learn what it looks like.

`assert_no_real_hid()` states that as a check rather than a comment.

## What it produces

For every `sendReport` / `sendFeatureReport` the page makes:
report id, full payload, the UI action in progress, and a monotonic sequence --
i.e. the `full encoded frame` column of the UI-action inventory, with provenance.

Responses are controlled: by default the device answers **nothing**, so that a
reply appearing in the log can only have come from the page's own state. That is
the EVIDENCE_VOID discipline (playbook §1.1/§1.2) enforced by construction --
see `mchose_echo.py` for the classifier that consumes this log.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import threading
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_MINER_ROOT = _THIS.parents[2]
_DB_ROOT = _MINER_ROOT.parent
for _p in (_DB_ROOT, _MINER_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_RUNTIME_JS = _THIS.parent / "fake_browser" / "runtime.js"
TARGET = "https://www.mchose.com.cn/#/home"

# Identities are the vendor's own, from configCenter/cardList (TICKET-23).
#
# Each carded MCHOSE keyboard publishes TWO product ids. The pairing is NOT
# "generic interface + vendor config channel", which is what this table said
# until the CZ SDK was asked directly. Its own `isBootUpdateMode(vid,pid,name)`
# answers, for every carded pair:
#
#     God 60             0x3020 boot=False   0x301a boot=True
#     Ace 75  8K         0x303c boot=False   0x303d boot=True
#     Ace 75 16K         0x301d boot=False   0x301f boot=True
#     Ace 68 GT          0x3007 boot=False   0x3009 boot=True
#     Ace 60 Pro ISO FR  0x3040 boot=False   0x3041 boot=True
#
# The 0xff00:0x0001 id is the BOOTLOADER / DFU interface. The configurator talks
# to the 0x0001:0x0000 id. Advertising the 0xff00 one made the live app open a
# firmware-update dialog ("current device is in upgrade mode") instead of the
# configurator, and made every read a no-op, because the device runtime guards
# nearly everything with `if (isUpgradeMode) return`.
#
# Worth naming as a transfer error rather than a typo: assuming 0xff00:0x0001
# was "the vendor config channel" is AULA's shape, and on AULA the config
# collection was 0xFF60:0x0061 on the SAME pid while 0xFF00:0x0001 explicitly
# was NOT the config channel. The assumption was wrong on both vendors.
PROFILES: dict[str, dict] = {
    "god60": {
        "label": "God 60",
        "vendorId": 0x3837,
        "productId": 0x3020,
        "productName": "MCHOSE God 60",
        "usagePage": 0x0001,
        "usage": 0x0000,
        "boot_mode_pid": 0x301A,
        "provenance": "configCenter/cardList desc 'God 60 配置'; "
                      "mode from CZ SDK isBootUpdateMode(0x3837,0x3020,...)=False",
    },
    "ace75_8k": {
        "label": "Ace 75 8K",
        "vendorId": 0x3837,
        "productId": 0x303C,
        "productName": "MCHOSE Ace 75",
        "usagePage": 0x0001,
        "usage": 0x0000,
        "boot_mode_pid": 0x303D,
        "provenance": "configCenter/cardList desc 'Ace 75 磁轴键盘（8K 已发布, 16K 未发布）'; "
                      "CZ SDK storageKey 'Ace 75 8K', isBootUpdateMode=False",
    },
    "ace75_16k": {
        "label": "Ace 75 16K",
        "vendorId": 0x3837,
        "productId": 0x301D,
        "productName": "MCHOSE Ace 75",
        "usagePage": 0x0001,
        "usage": 0x0000,
        "boot_mode_pid": 0x301F,
        "provenance": "same card as ace75_8k; CZ SDK storageKey 'Ace 75 16K', "
                      "isBootUpdateMode=False",
    },
    "ace68gt": {
        "label": "Ace 68 GT",
        "vendorId": 0x3837,
        "productId": 0x3007,
        "productName": "MCHOSE Ace 68 GT",
        "usagePage": 0x0001,
        "usage": 0x0000,
        "boot_mode_pid": 0x3009,
        "provenance": "configCenter/cardList desc 'Ace 68 GT 磁轴键盘（有线）'; "
                      "CZ SDK isBootUpdateMode(0x3837,0x3007,...)=False",
    },
}

# Report ids the static lane established for the keyboard family (TICKET-24):
# wired reads use 0x06, wired writes 0x03/0x04/0x05/0x06/0x10, wireless 0x01..0x13.
# Advertising them makes the fake device look plausible to a client that picks a
# report id off the descriptor.
KEYBOARD_REPORT_IDS = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x09, 0x10, 0x13]


def wait_device_ready(page, timeout_s: float = 120.0) -> dict:
    """Block until the app's own device runtime is ready, or time out saying so.

    The three earlier oracle passes all clicked the device row on a timer and
    all reported the same "configurator never opened". The transition trace
    (`mchose_transition_trace.py`) showed why, and it was not the click: the row
    is inert until `SingletonDeviceStore` holds a settled state for the device,
    because the list item only gains `connectMode` inside `if (deviceState)`,
    and `toDeviceAliveStatus(item).isDisabled` is true while `connectMode` is
    null -- so the click handler discards the click with no error and no frame.

    On this host the CZ SDK finishes device setup around t+21 s, which is after
    every fixed wait previously used. Waiting on the state rather than on the
    clock removes the whole class of "did the click land" question.
    """
    deadline = timeout_s
    step = 2.0
    waited = 0.0
    last: dict = {}
    while waited < deadline:
        last = page.evaluate(
            """() => {
              const s = window.SingletonDeviceStore;
              if (!s || !s.allDeviceState) return {ready: false, why: 'no store'};
              const out = [];
              s.allDeviceState.forEach((st) => out.push({
                isBusy: st ? st.isBusy : null,
                communicateEnabled: st ? st.communicateEnabled : null,
                failedCount: st ? st.failedCount : null}));
              if (!out.length) return {ready: false, why: 'store empty', states: out};
              const ok = out.some(o => o.isBusy === false && o.communicateEnabled !== false);
              return {ready: ok, why: ok ? 'settled' : 'busy or disabled', states: out};
            }"""
        )
        if last.get("ready"):
            return last
        page.wait_for_timeout(int(step * 1000))
        waited += step
    return last


def assert_no_real_hid(page) -> None:
    """Fail loudly if the page's navigator.hid is not ours.

    A silent fallback to the real WebHID stack would turn a capture run into a
    write to whatever keyboard is plugged in. This is the one invariant of this
    harness, so it is asserted on the live page rather than assumed from the
    injection order.
    """
    marker = page.evaluate(
        "() => ({ has: !!navigator.hid,"
        "         fake: !!(navigator.hid && navigator.hid.__protocolMinerFake) })"
    )
    if not marker.get("fake"):
        raise SystemExit(
            f"REFUSING TO CONTINUE: navigator.hid is not the fake runtime ({marker}). "
            "Frames built by the page could reach real hardware."
        )


class FrameLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self.n = 0
        self.action = "connect"

    def set_action(self, name: str) -> None:
        self.action = name

    def record(self, req: dict) -> dict:
        with self._lock:
            self.n += 1
            rec = {
                "seq": self.n,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "ui_action": self.action,
                "method": req.get("method"),
                "report_id": req.get("report_id"),
                "payload_hex": req.get("bytes_hex"),
                "payload_len": len(req.get("bytes_hex") or "") // 2,
            }
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        m = req.get("method")
        if m == "sendReport":
            print(f"  [{rec['seq']:>4}] {rec['ui_action']:<22} TX rid={rec['report_id']} "
                  f"len={rec['payload_len']} {(rec['payload_hex'] or '')[:56]}")
        # No canned response: the device answers nothing by default, so anything
        # that looks like a reply in the page's state came from the page.
        if m in ("receiveFeatureReport",):
            return {"hex": "", "strategy": "NO_RESPONSE", "confidence": 0.0}
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    def close(self):
        self._fh.close()


def build_config(profile: dict) -> dict:
    return {
        "vendorId": profile["vendorId"],
        "productId": profile["productId"],
        "productName": profile["productName"],
        "collections": [
            {
                "usagePage": profile["usagePage"],
                "usage": profile["usage"],
                "inputReports": [{"reportId": r} for r in KEYBOARD_REPORT_IDS],
                "outputReports": [{"reportId": r} for r in KEYBOARD_REPORT_IDS],
                "featureReports": [{"reportId": r} for r in KEYBOARD_REPORT_IDS],
            }
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="god60")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = PROFILES[args.profile]
    log = FrameLog(Path(args.out))
    print(f"profile: {profile['label']}  "
          f"{profile['vendorId']:#06x}:{profile['productId']:#06x} "
          f"usage {profile['usagePage']:#06x}:{profile['usage']:#06x}")
    print(f"provenance: {profile['provenance']}")

    runtime = _RUNTIME_JS.read_text(encoding="utf-8")
    cfg = (
        f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
        f"window.__protocolMinerCannedResponses = {{}};\n"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", lambda src, req: log.record(req))
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda src, t: None)
        ctx.add_init_script(cfg + "\n" + runtime)
        page = ctx.new_page()

        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text[:220]}"))

        # This host is slow and intermittently exceeds a 45 s domcontentloaded
        # wait while still serving fine (seen repeatedly since TICKET-23). A
        # goto timeout is therefore not a failure signal; the page's own state
        # is. Poll for the app root instead of trusting the navigation event.
        # Observed reliability on this host: roughly one load in three succeeds
        # within a minute. That is flaky infrastructure, not a finding about the
        # vendor, so it is retried rather than reported as a result.
        loaded = False
        for attempt in range(3):
            try:
                page.goto(TARGET, wait_until="commit", timeout=90000)
            except Exception as exc:  # noqa: BLE001
                print(f"[nav] attempt {attempt}: goto raised ({exc}); polling anyway")
            for _ in range(45):
                try:
                    if page.evaluate(
                        "() => !!document.querySelector('#app') && document.body.innerText.length > 40"
                    ):
                        loaded = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(1000)
            if loaded:
                print(f"[nav] loaded on attempt {attempt}")
                break
            print(f"[nav] attempt {attempt} produced an empty page; retrying")
        if not loaded:
            print("[nav] page never rendered; capture will be empty and that is a HARNESS "
                  "outcome, not evidence about the protocol")
        assert_no_real_hid(page)
        print("[safety] navigator.hid is the fake runtime")

        # Click the app's own connect control. Found STRUCTURALLY by class, not
        # by label text: the first attempt matched on localised strings and hit
        # nothing, because the page renders in the browser's locale and the
        # button's caption is whatever that locale says.
        # Wait for it rather than sleeping a guessed amount: the first run
        # clicked 4 s after navigation and found nothing, while a probe that
        # waited 22 s saw the button. A fixed pause turns "the app is slow" into
        # "the control does not exist", which is a wrong finding, not a flake.
        try:
            page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        except Exception as exc:  # noqa: BLE001
            print(f"[connect] button never appeared: {exc}")
        clicked = page.evaluate(
            """() => {
              const b = document.querySelector('button.mc-button');
              if (!b) return null;
              const r = b.getBoundingClientRect();
              if (r.width <= 0) return 'zero-size';
              b.click();
              return (b.innerText||'').trim().slice(0,40) || '<no text>';
            }"""
        )
        print(f"[connect] clicked: {clicked!r}")
        ready = wait_device_ready(page)
        print(f"[ready] {json.dumps(ready, ensure_ascii=False)}")
        if not ready.get("ready"):
            print("[ready] device runtime never settled -- the row will be inert and "
                  "any empty capture below is a HARNESS outcome, not a protocol fact")

        # The app resolves a connected device to a display name. That name is an
        # OBSERVED id -> name edge, and it is the only route to those edges that
        # does not involve guessing from similarity (TICKET-23 left 25 open).
        observed_name = page.evaluate(
            """() => {
              const t = document.body.innerText || '';
              return {body_head: t.slice(0, 600),
                      device_label: (document.querySelector('.device-name,.deviceName,[class*=device-name]')||{}).innerText || null};
            }"""
        )
        print(f"[identity] {json.dumps(observed_name, ensure_ascii=False)[:300]}")

        # Enter the configurator: the connect screen resolves identity PASSIVELY
        # (name shown with zero HID frames sent), so nothing is on the wire until
        # a device is actually opened.
        log.set_action("open_device")
        # Pick the SMALLEST element whose text is exactly the device name. The
        # previous selector took the first match in document order, which is an
        # ancestor container ("Добавить новое устройство / Облако / ...") whose
        # innerText merely contains the name -- clicking it hit the panel, not
        # the row, and no device was ever opened.
        opened = page.evaluate(
            """() => {
              const name = /^(God 60|Ace 75|Ace 68 GT)$/i;
              const cands = Array.from(document.querySelectorAll('div,span,button,li,p'))
                .filter(e => name.test((e.innerText||'').trim()))
                .map(e => { const r = e.getBoundingClientRect();
                            return {e, area: r.width * r.height, x: r.x + r.width/2, y: r.y + r.height/2}; })
                .filter(o => o.area > 0)
                .sort((a,b) => a.area - b.area);
              if (!cands.length) return null;
              const best = cands[0];
              best.e.click();
              return {text: (best.e.innerText||'').trim().slice(0,40),
                      area: Math.round(best.area), candidates: cands.length};
            }"""
        )
        print(f"[open] clicked device row: {opened!r}")
        page.wait_for_timeout(8000)

        # Confirm the transition rather than assume it. The route name is the
        # app's own answer; an empty capture on the ConnectDevice route and an
        # empty capture inside the configurator mean completely different things,
        # and only one of them is about the protocol.
        route = page.evaluate(
            "() => { try { return window.$router.currentRoute.value.name; } catch (e) { return null; } }"
        )
        print(f"[route] {route!r}  {page.evaluate('() => location.hash')}")
        if route != "Keyboard":
            print("[route] NOT in the configurator; frames below (if any) are from the "
                  "device list, not from keyboard UI actions")

        # Walk whatever top-level navigation the configurator exposes, one
        # action at a time, so every captured frame is attributable to the click
        # that produced it. That attribution is the whole point of the
        # inventory: a frame with no action label is a frame nobody can classify.
        tabs = page.evaluate(
            """() => Array.from(document.querySelectorAll('[class*=tab],[class*=menu-item],[class*=nav]'))
                 .filter(e => { const r = e.getBoundingClientRect();
                                return r.width > 30 && r.width < 320 && r.height > 14 && (e.innerText||'').trim(); })
                 .slice(0, 14)
                 .map(e => { const r = e.getBoundingClientRect();
                             return {text: (e.innerText||'').trim().slice(0,30),
                                     x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}; })"""
        )
        print(f"[tabs] {[t['text'] for t in tabs]}")
        for t in tabs:
            log.set_action(f"tab:{t['text'][:24]}")
            before = log.n
            try:
                page.mouse.click(t["x"], t["y"])
                page.wait_for_timeout(2500)
            except Exception as exc:  # noqa: BLE001
                print(f"  tab {t['text']!r} click failed: {exc}")
            print(f"  tab {t['text'][:24]!r}: frames {before} -> {log.n}")

        log.set_action("idle_after_walk")
        page.wait_for_timeout(int(args.seconds * 1000))

        body = ""
        try:
            body = page.evaluate("() => document.body.innerText")[:2500]
        except Exception:  # noqa: BLE001
            pass
        Path(args.out).with_suffix(".session.json").write_text(
            json.dumps(
                {
                    "profile": profile,
                    "frames_captured": log.n,
                    "body_snapshot": body,
                    "console_head": console[:80],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ctx.close()
        browser.close()

    log.close()
    print(f"\nframes captured: {log.n}")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
