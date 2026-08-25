"""Is BY wired 0x04 separable at all? The decisive experiment.

The first diff said DISCRIMINATOR_FOUND and it should not be believed. Every one
of its 40 "discriminating" offsets was zero on the `setPerformance` side, and
those zeros came from this harness: the synthetic device answered `getPerformance`
with zeros, so the page's record was all zeros, so its writes were too. The reset
frame carries the vendor's factory defaults, which are not zeros. Comparing them
measured the harness, not the protocol.

## What the bundle establishes

From `hpe`:

```js
["setPerformance", {default: {wiredCommand: "04", wiredParser: bh.setPerformanceWiredParser, ...}}]
["setReset",       {default: {wiredCommand: "04", wiredParser: bh.setResetWireParser, ...}}]
bh.setPerformanceWiredParser = () => j_(!0)
bh.setResetWireParser        = () => j_(!0)
```

Same lead byte, same serialiser, same argument. From `writeCommand`, the frame is
`serialise(parser, data)` zero-padded to 519 -- a pure function of parser and
data. And from the `otherSetting` component, the reset's data is a **constant**:

```js
const {lightRgbObj: a, lightDiyObj: d, otherObj: h} = x8[whichDevice];
...
_ = h; navigator.deviceHandler.sendCommand("set", "setReset", _, {delay: 50});
```

`x8.K99.otherObj` is `Okt`, a fully-specified performance record holding the
factory defaults. So `setReset` is not a distinct command on the wire -- it is
`setPerformance` carrying one particular value of the same record.

## The experiment

Give the page a device whose stored performance record IS the factory default,
by answering `getPerformance` with the reset payload itself (realigned: the read
parser `Oie` skips 2 + 6 bytes, the write emits 1 command + 6, so
`reply[2:] == write[1:]`; the two layouts are otherwise identical, only the field
names `space1` and `macSwitch` swap positions).

Then drive a routine settings change through the vendor's UI and change it back,
so the record returns to defaults, and compare that routine frame with the reset
frame.

**If they are byte-identical, no wire-level discriminator can exist** -- not
"we did not find one", but "a routine write and a factory reset are the same 519
bytes, and no classifier reading the frame can separate them."

No real device. `assert_no_real_hid` runs on the live page first, and the reset
confirmation is clicked only because the only thing on the other side is us.
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
from miner.dynamic.mchose_by_oracle import (  # noqa: E402
    BY_FEATURE_REPORT_BYTES, BY_PROFILES, battery_reply, build_config,
)
from miner.dynamic.mchose_by_reset_diff import (  # noqa: E402
    ARM_RESET_OBSERVER_JS, LIST_VISIBLE_JS, TAB_JS,
)

print = functools.partial(print, flush=True)  # noqa: A001

# Wired read command bytes from `hpe` (TICKET-24's extraction).
READ_GET_BATTERY = 0x87
READ_GET_PERFORMANCE = 0x84


def performance_reply_from_write(write_payload: bytes,
                                 size: int = BY_FEATURE_REPORT_BYTES) -> bytes:
    """Turn a captured `setPerformance`/`setReset` frame into a `getPerformance` reply.

    Alignment, from the vendor's own parsers:

        write : [0] command, [1..6] space0, [7..] the record
        read  : IG drops 2, then Oie skips space0[6], so the record starts at 8

    so `reply[2:] = write[1:]` puts the same record at the same place. The two
    layouts are otherwise identical field-for-field; only the names `space1` and
    `macSwitch` occupy each other's slot, which changes nothing about bytes.
    """
    body = write_payload[1:]
    out = bytearray(size)
    out[2:2 + len(body)] = body[: size - 2]
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(BY_PROFILES), default="k99")
    ap.add_argument("--reset-frame", required=True,
                    help="by_0x04_diff artifact holding a captured setReset frame")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=30.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    prior = json.loads(Path(args.reset_frame).read_text(encoding="utf-8"))
    reset_hex = next(
        (f["payload_hex"] for f in prior["frames"]
         if str(f.get("ui_action", "")).startswith("reset_confirm")
         and (f.get("payload_hex") or "").startswith("04")),
        None)
    if not reset_hex:
        raise SystemExit(f"{args.reset_frame} holds no captured setReset frame")
    reset_payload = bytes.fromhex(reset_hex)
    perf_state_reply = performance_reply_from_write(reset_payload)
    print(f"[state] serving the factory-default record as getPerformance "
          f"({len(perf_state_reply)} bytes), taken from the captured reset frame")

    profile = BY_PROFILES[args.profile]
    frames: list[dict] = []
    state = {"action": "boot", "last_read": None}
    battery = battery_reply(100)

    def on_frame(_src, req):
        method = req.get("method")
        payload = req.get("bytes_hex") or ""
        rec = {"seq": len(frames) + 1, "ui_action": state["action"], "method": method,
               "report_id": req.get("report_id"), "payload_hex": payload}
        if method == "sendFeatureReport":
            lead = payload[:2].lower()
            if lead in ("87", "84", "83", "86", "8a", "82"):
                state["last_read"] = int(lead, 16)
            frames.append(rec)
            mark = "  <== WIRED 0x04" if lead == "04" else ""
            print(f"  [{rec['seq']:>4}] {rec['ui_action']:<28} TX lead={lead} "
                  f"len={len(payload)//2}{mark}")
            return {"ack": True, "strategy": "NO_RESPONSE"}
        if method == "receiveFeatureReport":
            # Answer the read that was just issued. A device answers the question
            # it was asked; answering every read with the same bytes would be a
            # different (and less honest) experiment.
            if state["last_read"] == READ_GET_PERFORMANCE:
                body, tag = perf_state_reply, "factory-default performance record"
            elif state["last_read"] == READ_GET_BATTERY:
                body, tag = battery, "battery"
            else:
                body, tag = bytes(BY_FEATURE_REPORT_BYTES), "zeros"
            rec["evidence_class"] = "synthetic_from_vendor_schema"
            rec["reply_for_command"] = state["last_read"]
            rec["reply_kind"] = tag
            frames.append(rec)
            return {"hex": body.hex(), "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA"}
        frames.append(rec)
        return {"ack": True, "strategy": "NO_RESPONSE"}

    init = (f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
            f"window.__protocolMinerCannedResponses = {{}};\n"
            + _RUNTIME_JS.read_text(encoding="utf-8"))

    routine: list[str] = []
    dialog = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()

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
            raise SystemExit("page never rendered; nothing is claimed from this run")

        assert_no_real_hid(page)
        print("[safety] navigator.hid is the fake runtime")

        state["action"] = "connect"
        page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        page.evaluate("() => document.querySelector('button.mc-button').click()")
        page.wait_for_timeout(int(args.settle * 1000))

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
        tabs = page.evaluate(TAB_JS)
        perf_tab = next((t for t in tabs if t["id"] and "perf" in t["id"].lower()), None)
        other_tab = next((t for t in tabs if t["id"] and "other" in t["id"].lower()), None)
        print(f"[tabs] perf={perf_tab and perf_tab['id']!r} other={other_tab and other_tab['id']!r}")

        # A routine change, then the same change back. The record ends where it
        # started -- at the factory defaults the device reported -- so the second
        # write is a routine write of exactly the reset's data.
        if perf_tab:
            page.evaluate("(id) => document.getElementById(id).click()", perf_tab["id"])
            page.wait_for_timeout(3500)
            for label in ("toggle_on", "toggle_back"):
                state["action"] = f"routine:{label}"
                routine.append(state["action"])
                page.evaluate(
                    """() => { const e = Array.from(document.querySelectorAll('.el-switch__core'))
                          .filter(x => { const r = x.getBoundingClientRect();
                                         return r.width > 0 && r.height > 0 && x.offsetParent !== null; })[0];
                        if (e) e.click(); }""")
                page.wait_for_timeout(2500)

        if other_tab:
            page.evaluate("(id) => document.getElementById(id).click()", other_tab["id"])
            page.wait_for_timeout(3500)
            cands = [c for c in page.evaluate(LIST_VISIBLE_JS, "button.mc-button")
                     if "is-disabled" not in (c["cls"] or "")]
            print(f"[other] {[c['text'] for c in cands]}")
            if cands:
                page.evaluate(ARM_RESET_OBSERVER_JS)
                state["action"] = "reset_confirm"
                page.evaluate(
                    """(i) => { const els = Array.from(document.querySelectorAll('button'))
                          .filter(e => { const r = e.getBoundingClientRect();
                                         return r.width > 0 && r.height > 0 && e.offsetParent !== null; })
                          .filter(e => (e.className || '').includes('mc-button') && !e.disabled);
                        const e = els[i]; if (e) e.click(); }""",
                    0 if len(cands) == 1 else len(cands) - 1)
                page.wait_for_timeout(8000)
                seen = page.evaluate("() => window.__byReset || null")
                dialog = (seen or {}).get("seen")
                if dialog:
                    print(f"[dialog] {dialog['text'][:100]!r} confirmed with "
                          f"{(seen or {}).get('confirmed')!r}")
        ctx.close()
        browser.close()

    def leads(actions):
        return [f for f in frames
                if f.get("method") == "sendFeatureReport"
                and (f.get("payload_hex") or "").startswith("04")
                and f["ui_action"] in actions]

    routine_frames = leads(set(routine))
    reset_frames = leads({"reset_confirm"})

    identical = []
    for rf in routine_frames:
        for xf in reset_frames:
            if rf["payload_hex"] == xf["payload_hex"]:
                identical.append({"routine_action": rf["ui_action"], "seq": rf["seq"]})

    if identical:
        verdict = "IMPOSSIBLE_AT_WIRE_LEVEL"
        why = ("a routine setPerformance frame and the factory-reset frame are BYTE-IDENTICAL. "
               "No classifier reading the frame can separate them, because there is nothing in "
               "the frame to read: same lead byte, same serialiser, and a data value the "
               "settings UI can reach.")
    elif routine_frames and reset_frames:
        diffs = []
        r0 = bytes.fromhex(reset_frames[0]["payload_hex"])
        for rf in routine_frames:
            b = bytes.fromhex(rf["payload_hex"])
            w = min(len(b), len(r0))
            diffs.append({"routine_action": rf["ui_action"],
                          "offsets": [i for i in range(w) if b[i] != r0[i]][:40]})
        verdict = "NOT_ESTABLISHED"
        why = ("routine and reset frames were captured and are not identical, but the routine "
               "record did not return exactly to the factory value, so this run does not "
               "settle whether they CAN coincide")
    else:
        verdict = "NOT_ESTABLISHED"
        why = (f"routine 0x04 frames: {len(routine_frames)}, reset 0x04 frames: "
               f"{len(reset_frames)}; both are needed")
        diffs = []

    doc = {
        "_what": "BY wired 0x04: can a routine write and a factory reset be the same frame?",
        "_method": ("the page was given a device whose stored performance record IS the factory "
                    "default, by answering getPerformance with the captured reset payload "
                    "realigned; then a setting was changed and changed back through the "
                    "vendor's UI"),
        "_why_the_earlier_diff_was_wrong": (
            "it compared a reset frame carrying factory defaults against setPerformance frames "
            "that were nearly all zeros, and the zeros came from this harness answering "
            "getPerformance with zeros. Those 40 'discriminating' offsets measured the harness."),
        "_safety": "every frame went to the fake runtime, asserted on the live page first",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "dialog": dialog,
        "routine_0x04_frames": len(routine_frames),
        "reset_0x04_frames": len(reset_frames),
        "identical_pairs": identical,
        "verdict": verdict,
        "why": why,
        "samples": {
            "routine": [f["payload_hex"][:200] for f in routine_frames[:3]],
            "reset": [f["payload_hex"][:200] for f in reset_frames[:2]],
        },
        "frames": frames,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nroutine 0x04: {len(routine_frames)}   reset 0x04: {len(reset_frames)}")
    print(f"identical pairs: {len(identical)}")
    print(f"VERDICT: {verdict}")
    print(f"  {why}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
