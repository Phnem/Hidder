"""TICKET-25 priority 5: capture BY `setPerformance` and `setReset`, and diff them.

The A Preview blocker in one sentence: on the wired transport both commands lead
with `0x04` and are serialised by the *same* parser function `j_(!0)`, so the
frame LAYOUT cannot say which one a frame is. If a keyboard cannot tell a
settings write from a factory reset before sending it, neither can we.

The bytes are serialised from the DATA object the UI passes, so the vendor's own
UI can produce both frames. This drives it and diffs them.

## Safety, structurally

Every frame goes to `fake_browser/runtime.js`. `assert_no_real_hid` is checked on
the live page before the first click. The factory-reset confirmation IS clicked
-- that is the point, and it is safe for exactly one reason: the object the page
calls `sendFeatureReport` on is ours. This is how you see a reset frame without
sending one to a keyboard.

## Finding the reset by behaviour, not by label

The reset control is not matched on its caption. The page renders in the
browser's locale, and matching localised text is how an earlier pass concluded a
control did not exist. Instead the walker clicks candidate controls and watches
for a **confirmation dialog** to appear; the dialog's own text is recorded before
it is confirmed, so the action is labelled by what the app said, not by what we
assumed.
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
    BY_PROFILES, battery_reply, build_config, classify_by_frame,
)

print = functools.partial(print, flush=True)  # noqa: A001

TAB_JS = """() => {
  const out = [];
  document.querySelectorAll('[id^=tab-],[role=tab],.el-tabs__item').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    out.push({id: e.id || null, text: (e.innerText || '').trim().slice(0, 32),
              x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
  });
  return out;
}"""

CONTROLS_JS = """() => {
  const sel = 'button,input,[role=slider],[class*=slider],[class*=switch],[class*=radio]';
  const out = [];
  document.querySelectorAll(sel).forEach((e, i) => {
    const r = e.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    if (r.bottom < 0 || r.top > window.innerHeight) return;
    out.push({sel_index: i, tag: e.tagName, type: e.getAttribute('type'),
              cls: String(e.className || '').slice(0, 70),
              text: (e.innerText || e.value || '').trim().slice(0, 40),
              min: e.min || null, max: e.max || null, step: e.step || null});
  });
  return out;
}"""

CLICK_JS = """(arg) => {
  const el = document.querySelectorAll(arg.sel)[arg.i];
  if (!el) return 'gone';
  el.scrollIntoView({block: 'center'});
  el.click();
  return 'clicked';
}"""

# Element Plus renders its confirmation as `.el-message-box`. Read before click,
# always: a dialog dismissed without its text recorded is an unlabelled action.
DIALOG_JS = """() => {
  const box = document.querySelector('.el-message-box, .el-dialog, [role=dialog]');
  if (!box) return null;
  const r = box.getBoundingClientRect();
  if (r.width < 100 || r.height < 50) return null;
  const btns = Array.from(box.querySelectorAll('button'));
  return {text: (box.innerText || '').trim().slice(0, 400),
          buttons: btns.map(b => (b.innerText || '').trim().slice(0, 30))};
}"""

CONFIRM_JS = """() => {
  const box = document.querySelector('.el-message-box, .el-dialog, [role=dialog]');
  if (!box) return false;
  const btns = Array.from(box.querySelectorAll('button'));
  const primary = btns.find(b => (b.className || '').includes('primary')) || btns[btns.length - 1];
  if (!primary) return false;
  primary.click();
  return (primary.innerText || '').trim().slice(0, 30);
}"""

# Only what the user can actually see.
#
# Element Plus keeps every tab panel mounted, so an unfiltered `querySelectorAll`
# reaches controls in sections the walk is not in. That is not a cosmetic
# problem: a run that believed it was probing the reset section clicked a
# lighting effect in a hidden panel and recorded the resulting frame under a
# reset label. Indexing is done over the VISIBLE list only, and the same
# predicate is used to enumerate and to click so the two cannot disagree.
_VISIBLE = ("e => { const r = e.getBoundingClientRect();"
            "       return r.width > 0 && r.height > 0 && e.offsetParent !== null; }")

LIST_VISIBLE_JS = ("(sel) => Array.from(document.querySelectorAll(sel)).filter(" + _VISIBLE + ")"
                   ".map((e, i) => { const r = e.getBoundingClientRect();"
                   "   return {i, tag: e.tagName,"
                   "           text: (e.innerText || e.value || '').trim().slice(0, 24),"
                   "           cls: String(e.className || '').slice(0, 60),"
                   "           x: Math.round(r.x + r.width / 2),"
                   "           y: Math.round(r.y + r.height / 2)}; })")

CLICK_VISIBLE_JS = ("(arg) => { const els = Array.from(document.querySelectorAll(arg.sel))"
                    ".filter(" + _VISIBLE + ");"
                    " const e = els[arg.i]; if (!e) return 'gone';"
                    " e.scrollIntoView({block: 'center'}); e.click(); return 'clicked'; }")

FOCUS_VISIBLE_JS = ("(arg) => { const els = Array.from(document.querySelectorAll(arg.sel))"
                    ".filter(" + _VISIBLE + ");"
                    " const e = els[arg.i]; if (!e) return 'gone'; e.focus(); return 'focused'; }")

SET_VALUE_JS = """(arg) => {
  const el = document.querySelectorAll(arg.sel)[arg.i];
  if (!el) return 'gone';
  const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  if (d && d.set) d.set.call(el, String(arg.value)); else el.value = String(arg.value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return 'set';
}"""


def describe_writes(frames: list[dict]) -> dict:
    """Which bytes of a wired 0x04 write move as its own controls are driven.

    Not a discriminator -- these are the bytes that carry the USER'S SETTINGS,
    which is exactly why they can never separate setPerformance from setReset.
    Recording them is still worth it: it is the payload map of the command, and
    it is what a later diff has to exclude.
    """
    raws = [bytes.fromhex(f["payload_hex"]) for f in frames
            if f.get("method") == "sendFeatureReport"
            and (f.get("payload_hex") or "").lower().startswith("04")]
    if len(raws) < 4:
        return {"verdict": "WITHHELD",
                "why": f"only {len(raws)} write frames; 4 is the minimum before a field "
                       "position can be argued"}
    width = min(len(r) for r in raws)
    moving = [i for i in range(width) if len({r[i] for r in raws}) > 1]
    return {
        "verdict": "FIELDS_OBSERVED" if moving else "NO_MOVEMENT",
        "frames": len(raws),
        "distinct_payloads": len({r.hex() for r in raws}),
        "frame_bytes": width,
        "byte_offsets_that_move": moving[:40],
        "values_per_offset": {str(i): sorted({r[i] for r in raws}) for i in moving[:12]},
        "note": ("these are the command's own settings bytes, observed by driving one control "
                 "at a time through the vendor's UI; they are NOT candidate discriminators, "
                 "because a discriminator must be stable within an action"),
        "provenance": "OBSERVED_FROM_VENDOR_UI in a SYNTHETIC_ENVIRONMENT",
    }


def diff(a_frames: list[dict], b_frames: list[dict]) -> dict:
    """Byte-diff the wired 0x04 frames from two actions."""
    def leads(fs):
        return [f for f in fs
                if f.get("method") == "sendFeatureReport"
                and (f.get("payload_hex") or "").lower().startswith("04")]

    a, b = leads(a_frames), leads(b_frames)
    if not a or not b:
        return {
            "verdict": "NOT_ESTABLISHED",
            "why": (f"setPerformance frames captured: {len(a)}; setReset frames captured: "
                    f"{len(b)}. A diff needs at least one of each, and an absent frame is a "
                    "gap in the walk, not evidence that the command does not exist."),
            "setPerformance_frames": len(a), "setReset_frames": len(b),
        }

    ra = [bytes.fromhex(f["payload_hex"]) for f in a]
    rb = [bytes.fromhex(f["payload_hex"]) for f in b]
    width = min(min(len(x) for x in ra), min(len(x) for x in rb))

    # Bytes that vary WITHIN one action are user settings, not discriminators.
    unstable = {i for i in range(width)
                if len({x[i] for x in ra}) > 1 or len({x[i] for x in rb}) > 1}
    differing = [i for i in range(width)
                 if i not in unstable and ra[0][i] != rb[0][i]]

    return {
        "verdict": "DISCRIMINATOR_FOUND" if differing else "NO_STABLE_DISCRIMINATOR",
        "frame_bytes_compared": width,
        "setPerformance_frames": len(a),
        "setReset_frames": len(b),
        "bytes_unstable_within_an_action": sorted(unstable)[:40],
        "unstable_note": ("these move between repeats of the SAME action, so they carry the "
                          "user's current settings and cannot discriminate the two commands"),
        "discriminating_offsets": differing[:40],
        "discriminating_values": [
            {"offset": i, "setPerformance": ra[0][i], "setReset": rb[0][i]} for i in differing[:40]
        ],
        "why": ("these offsets are identical across repeats of each action and differ between "
                "them" if differing else
                "no offset is both stable within each action and different between them; on this "
                "evidence the two commands are indistinguishable and wired 0x04 stays "
                "POTENTIALLY_DESTRUCTIVE"),
        "samples": {
            "setPerformance": [f["payload_hex"][:160] for f in a[:3]],
            "setReset": [f["payload_hex"][:160] for f in b[:3]],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(BY_PROFILES), default="k99")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=30.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = BY_PROFILES[args.profile]
    frames: list[dict] = []
    state = {"action": "boot"}
    reply = battery_reply(100)

    def on_frame(_src, req):
        method = req.get("method")
        rec = {"seq": len(frames) + 1, "ui_action": state["action"], "method": method,
               "report_id": req.get("report_id"), "payload_hex": req.get("bytes_hex"),
               "payload_len": len(req.get("bytes_hex") or "") // 2}
        if method == "receiveFeatureReport":
            rec["evidence_class"] = "synthetic_from_vendor_schema"
            frames.append(rec)
            return {"hex": reply.hex(), "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA",
                    "confidence": 0.0}
        rec["evidence_class"] = "no_reply"
        frames.append(rec)
        if method == "sendFeatureReport":
            lead = (rec["payload_hex"] or "")[:2]
            mark = "  <== WIRED 0x04" if lead == "04" else ""
            print(f"  [{rec['seq']:>4}] {rec['ui_action']:<30} TX rid={rec['report_id']} "
                  f"lead={lead} len={rec['payload_len']}{mark}")
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    init = (f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
            f"window.__protocolMinerCannedResponses = {{}};\n"
            + _RUNTIME_JS.read_text(encoding="utf-8"))

    dialogs: list[dict] = []
    tabs_seen: list = []
    perf_actions: list[str] = []
    reset_actions: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()
        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text[:200]}"))

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
        route = page.evaluate(
            "() => { try { return window.$router.currentRoute.value.name } catch (e) { return null } }")
        print(f"[route] {route!r}")
        if route != "Keyboard":
            raise SystemExit(f"never reached the keyboard page (route {route!r}); nothing claimed")

        tabs_seen = page.evaluate(TAB_JS)
        print(f"[tabs] {[(t['id'], t['text']) for t in tabs_seen]}")

        # Tabs are clicked by their own element id. Coordinates captured once and
        # reused went stale as the panel reflowed: the "Другие" click landed on
        # the lighting panel and the run probed light effects while believing it
        # was probing the reset section. The ids are the app's own
        # (`tab-keySetting`, `tab-otherSetting`, ...), so they are also
        # locale-independent, which the captions are not.
        perf_tab = next((t for t in tabs_seen
                         if t["id"] and ("perf" in t["id"].lower() or "erform" in t["id"])), None)
        other_tab = next((t for t in tabs_seen
                          if t["id"] and "other" in t["id"].lower()), None)
        print(f"[tab ids] performance={perf_tab and perf_tab['id']!r} "
              f"other={other_tab and other_tab['id']!r}")

        # --- pass 1: the factory reset --------------------------------------
        # Run FIRST. With the performance pass ahead of it the reset button
        # stopped opening its dialog, while a run that probed it on a freshly
        # entered page opened it every time. The cause was not chased further
        # because the ordering is free: the diff does not care which capture
        # happened first, and a walk that reliably produces both frames beats
        # a tidier one that produces one.
        # Found by behaviour: enabled buttons in the last section are clicked and
        # a CONFIRMATION DIALOG is what identifies the destructive one. Its text
        # is read before it is confirmed, so the action is labelled by the app's
        # own words rather than by a caption we matched.
        for tab in ([other_tab] if other_tab else []):
            page.evaluate("(id) => { const e = document.getElementById(id); if (e) e.click(); }",
                          tab["id"])
            page.wait_for_timeout(3500)
            cands = [c for c in page.evaluate(LIST_VISIBLE_JS, "button.mc-button")
                     if "is-disabled" not in (c["cls"] or "")]
            print(f"[other] visible enabled mc-buttons: {[c['text'] for c in cands]}")
            # Playwright's own click, not a synthesised one. It waits for the
            # element to be stable, visible, enabled and un-obscured, then
            # dispatches real input events. Two cheaper methods were tried first
            # and both left "no dialog appeared" on the record -- a claim about
            # the app that was actually a claim about the click.
            locator = page.locator("button.mc-button:visible").filter(
                has_not=page.locator(".is-disabled"))
            for c in cands:
                state["action"] = f"probe:{c['text'][:20]}"
                try:
                    locator.filter(has_text=c["text"]).first.click(timeout=8000)
                except Exception as exc:  # noqa: BLE001
                    print(f"[probe] {c['text']!r} click failed: {str(exc)[:90]}")
                    continue
                # Poll rather than sample once. A single check at 1.8 s missed a
                # dialog that a 3.5 s check saw, and "no dialog appeared" is a
                # claim about the app, so it must not rest on one early look.
                dlg = None
                for _ in range(8):
                    page.wait_for_timeout(1000)
                    dlg = page.evaluate(DIALOG_JS)
                    if dlg:
                        break
                if not dlg:
                    print(f"[probe] {c['text']!r} opened no dialog within 8s")
                    continue
                dlg["opened_by"] = c["text"]
                dialogs.append(dlg)
                print(f"[dialog] opened by {c['text']!r}: {dlg['text'][:120]!r} "
                      f"buttons={dlg['buttons']}")
                state["action"] = f"reset_confirm:{c['text'][:20]}"
                reset_actions.append(state["action"])
                clicked = page.evaluate(CONFIRM_JS)
                print(f"[dialog] confirmed with {clicked!r}")
                page.wait_for_timeout(6000)
            break

        # --- pass 2: routine performance writes -----------------------------
        # Element Plus, so: radios and switches are clicked, and sliders are
        # driven with ArrowRight on their own role=slider element, which runs the
        # component's handler exactly as a drag would. The point is that the
        # VENDOR builds the frame.
        for tab in ([perf_tab] if perf_tab else []):
            page.evaluate("(id) => { const e = document.getElementById(id); if (e) e.click(); }",
                          tab["id"])
            page.wait_for_timeout(3000)

            radios = page.evaluate(LIST_VISIBLE_JS, "input[type=radio]")
            print(f"[perf] visible radios: {[r['text'] for r in radios]}")
            for r in radios:
                state["action"] = f"perf:radio={r['text'][:10]}"
                perf_actions.append(state["action"])
                page.evaluate(CLICK_VISIBLE_JS, {"sel": "input[type=radio]", "i": r["i"]})
                page.wait_for_timeout(1600)

            switches = page.evaluate(LIST_VISIBLE_JS, ".el-switch__core")
            print(f"[perf] visible switches: {len(switches)}")
            for i in range(min(len(switches), 4)):
                for _ in range(2):                  # on then off: reversible, and it doubles
                    state["action"] = f"perf:switch{i}"   # the sample for stability testing
                    perf_actions.append(state["action"])
                    page.evaluate(CLICK_VISIBLE_JS, {"sel": ".el-switch__core", "i": i})
                    page.wait_for_timeout(1500)

            sliders = page.evaluate(LIST_VISIBLE_JS, "[role=slider]")
            print(f"[perf] visible sliders: {len(sliders)}")
            for i in range(min(len(sliders), 2)):
                for step in range(3):
                    state["action"] = f"perf:slider{i}+{step}"
                    perf_actions.append(state["action"])
                    page.evaluate(FOCUS_VISIBLE_JS, {"sel": "[role=slider]", "i": i})
                    page.keyboard.press("ArrowRight")
                    page.wait_for_timeout(1500)
            break

        state["action"] = "idle"
        page.wait_for_timeout(3000)
        ctx.close()
        browser.close()

    perf_frames = [f for f in frames if f["ui_action"] in set(perf_actions)]
    reset_frames = [f for f in frames if f["ui_action"] in set(reset_actions)]
    result = diff(perf_frames, reset_frames)

    rows = []
    for f in frames:
        cls, why = classify_by_frame(f["method"], f["report_id"], f["payload_hex"])
        rows.append({**f, "transport_family": "keyboard/by", "safety_class": cls, "reason": why})

    doc = {
        "_what": "BY wired 0x04 discriminator attempt, TICKET-25 priority 5",
        "_family": "keyboard/BY only; nothing shared with CZ, mouse, OTA, or AULA",
        "_safety": ("every frame went to the fake runtime, asserted on the live page before the "
                    "first click; the reset confirmation was clicked precisely so that nobody "
                    "has to send one to a keyboard"),
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "tabs_seen": tabs_seen,
        "dialogs_encountered": dialogs,
        "frames_total": len(frames),
        "discriminator": result,
        "setPerformance_payload_map": describe_writes(perf_frames),
        "frames": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nframes: {len(frames)}")
    print(f"discriminator verdict: {result['verdict']}")
    print(f"  {result['why']}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
