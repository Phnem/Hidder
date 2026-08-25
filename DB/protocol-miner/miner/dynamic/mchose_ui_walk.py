"""TICKET-25 consolidation, item 3: drive the keyboard UI and record what it emits.

Produces, per action actually performed:

    UI action -> frame -> report id -> transport family -> safety class -> provenance

and, with equal weight, the list of controls **found but not performed**. Playbook
trap O-4: `NO_DESTRUCTIVE_PATH_FOUND` announced over an incomplete walk is a
statement about coverage, not about the device. This tool therefore never emits
that verdict; it emits the coverage numbers a person needs before considering it.

## Safety

Every frame goes to the fake runtime. `assert_no_real_hid()` checks that on the
live page before the first click, so a confirmation dialog that would wipe a
keyboard can be confirmed here without a keyboard being anywhere near it. That
is the point of driving the vendor's real UI against a fake device: the only way
to see a factory-reset frame without sending one.

## Two structural facts this tool has to respect

The CZ keyboard configurator is a separate app in a same-origin iframe
(`/cizhou/`), so controls are enumerated **inside that frame**, not in the host
page. And the app opens informational modals over it, which are recorded as
actions in their own right rather than being clicked away silently -- a modal
that was dismissed without being read is exactly where a destructive
confirmation hides.
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
    _RUNTIME_JS,
    assert_no_real_hid,
    build_config,
    wait_device_ready,
)
from miner.static import mchose_cz_codec as cz  # noqa: E402

CZ_FRAME_URL = "/cizhou/"

# Controls the CZ app renders. Enumerated by role/class rather than by label,
# because the page renders in the browser's locale and matching localised text
# is how an earlier pass concluded a control "did not exist".
ENUMERATE_JS = """() => {
  const sel = 'button,[role=tab],[role=switch],[role=slider],input,select,'
            + '[class*=tab-],[class*=menu-item],[class*=switch],[class*=slider]';
  const out = [];
  document.querySelectorAll(sel).forEach((e, i) => {
    const r = e.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    out.push({
      idx: i,
      tag: e.tagName,
      role: e.getAttribute('role'),
      type: e.getAttribute('type'),
      cls: String(e.className || '').slice(0, 90),
      text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 48),
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2)
    });
  });
  return out;
}"""

MODAL_JS = """() => {
  const btns = Array.from(document.querySelectorAll('button.maicong-btn'));
  if (!btns.length) return null;
  const primary = btns.find(b => (b.className || '').includes('type-primary')) || btns[0];
  return {
    text: (document.body.innerText || '').trim().slice(0, 400),
    buttons: btns.map(b => (b.innerText || '').trim().slice(0, 40)),
    will_click: (primary.innerText || '').trim().slice(0, 40)
  };
}"""

CLICK_PRIMARY_JS = """() => {
  const btns = Array.from(document.querySelectorAll('button.maicong-btn'));
  const primary = btns.find(b => (b.className || '').includes('type-primary')) || btns[0];
  if (!primary) return false;
  primary.click();
  return true;
}"""


def classify(decoded: dict | None, report_id, payload_hex: str) -> tuple[str, str, str, str]:
    """(safety_class, transport_family, direction, reason).

    The CZ family gets its own rules. Nothing is carried over from BY, from the
    mouse receiver, from the AA-55 OTA path, or from AULA.
    """
    if decoded and decoded.get("flag") == cz.FLAG_REQUEST:
        # One direction IS decidable, in one direction only. The vendor's read
        # builder (`_simpleGetCommand` -> `data: {size: N}`) never places bytes
        # after the 8-byte header, so a frame with a non-zero byte in the
        # trailing region cannot be a read. That makes it a WRITE by observation.
        #
        # The converse does NOT hold: an all-zero trailing region is a read OR a
        # write of zeros, byte-identical. So the safe direction of the inference
        # is used and the unsafe one is refused.
        if not decoded.get("trailing_is_all_zero"):
            return (
                "POTENTIALLY_DESTRUCTIVE",
                "keyboard/cz",
                "WRITE",
                "non-zero bytes after the 8-byte header; the vendor's read builder never "
                "emits those, so this is a write. What it writes is unknown, and an "
                "unexplained write is not a safe one.",
            )
        return (
            "UNKNOWN",
            "keyboard/cz",
            "UNKNOWN",
            "CZ request with an all-zero trailing region; byte 4 is ambiguous between "
            "bytes-requested and bytes-supplied, so a read and a write of zeros are "
            "byte-identical here",
        )
    rid = f"{int(report_id):02x}" if report_id is not None else "??"
    if rid == "4d":  # 77
        return ("UNKNOWN", "ota/aa55", "UNKNOWN", "report id 77 is the AA-55 OTA path")
    return ("UNKNOWN", "unclassified", "UNKNOWN", "does not match a characterised family")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="god60")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=25.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = PROFILES[args.profile]
    display_name = profile["label"]
    frames: list[dict] = []
    state = {"action": "boot"}

    def on_frame(_src, req):
        rec = {
            "seq": len(frames) + 1,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "ui_action": state["action"],
            "method": req.get("method"),
            "report_id": req.get("report_id"),
            "payload_hex": req.get("bytes_hex"),
        }
        reply_hex = None
        try:
            raw = bytes.fromhex(req.get("bytes_hex") or "")
            rec["decoded"] = cz.parse(raw).as_dict() if (
                len(raw) >= cz.HEADER_LENGTH and raw[0] in (cz.FLAG_REQUEST, cz.FLAG_EXPECT_REPLY)
            ) else None
            # The inventory is ANALYSIS, not corpus: it exists to state what a
            # frame is, so it is written under analysis/ and the leakage gate
            # exempts it by role. The raw captures under oracle/ stay clean.
            if req.get("method") == "sendReport" and rec["decoded"] and raw[0] == cz.FLAG_REQUEST:
                reply_hex = cz.synthesize_reply(raw).hex()
        except Exception:  # noqa: BLE001
            rec["decoded"] = None
        if reply_hex:
            rec["reply_hex"] = reply_hex
            rec["evidence_class"] = "synthetic_from_vendor_schema"
            rec["reply_provenance"] = cz.PROVENANCE
        else:
            rec["evidence_class"] = "no_reply"
        frames.append(rec)
        if reply_hex:
            return {"ack": True, "reply": {"reportId": req.get("report_id"), "hex": reply_hex},
                    "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA", "confidence": 0.0}
        if req.get("method") == "receiveFeatureReport":
            return {"hex": "", "strategy": "NO_RESPONSE", "confidence": 0.0}
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    init = (
        f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
        f"window.__protocolMinerCannedResponses = {{}};\n"
        + _RUNTIME_JS.read_text(encoding="utf-8")
    )

    modals: list[dict] = []
    controls: list[dict] = []
    performed: list[str] = []
    console: list[str] = []
    route = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text[:260]}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {str(e)[:260]}"))

        loaded = False
        for _ in range(6):
            try:
                page.goto(TARGET, wait_until="commit", timeout=90000)
            except Exception as exc:  # noqa: BLE001
                print(f"[nav] goto raised ({exc}); polling anyway")
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
            raise SystemExit("page never rendered; this run says nothing about the vendor")

        assert_no_real_hid(page)
        print("[safety] navigator.hid is the fake runtime")

        state["action"] = "connect"
        page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        page.evaluate("() => document.querySelector('button.mc-button').click()")
        ready = wait_device_ready(page)
        print(f"[ready] {json.dumps(ready, ensure_ascii=False)}")

        state["action"] = "open_device"
        page.evaluate(
            """(namePattern) => {
                 const re = new RegExp(namePattern, 'i');
                 const c = Array.from(document.querySelectorAll('div,span,button,li,p'))
                   .filter(e => re.test((e.innerText || '').trim()))
                   .map(e => { const r = e.getBoundingClientRect();
                               return {e, a: r.width * r.height}; })
                   .filter(o => o.a > 0).sort((x, y) => x.a - y.a);
                 if (c.length) c[0].e.click();
               }""",
            f"^{display_name}$",
        )
        page.wait_for_timeout(int(args.settle * 1000))
        route = page.evaluate(
            "() => { try { return window.$router.currentRoute.value.name; } catch (e) { return null; } }")
        print(f"[route] {route!r}")

        def cz_frame():
            for f in page.frames:
                if CZ_FRAME_URL in f.url:
                    return f
            return None

        # Modals first, and read before clicking. A modal dismissed without its
        # text recorded is an unlabelled action, and this is the one place a
        # destructive confirmation could be waiting.
        for round_no in range(5):
            f = cz_frame()
            if not f:
                break
            try:
                m = f.evaluate(MODAL_JS)
            except Exception:  # noqa: BLE001
                break
            if not m:
                break
            state["action"] = f"modal:{m['will_click'][:24]}"
            before = len(frames)
            m["round"] = round_no
            m["frames_before"] = before
            try:
                f.evaluate(CLICK_PRIMARY_JS)
            except Exception as exc:  # noqa: BLE001
                m["click_failed"] = str(exc)[:120]
            page.wait_for_timeout(6000)
            m["frames_after"] = len(frames)
            modals.append(m)
            print(f"[modal {round_no}] {m['will_click']!r} of {m['buttons']} "
                  f"-> frames {before}->{len(frames)}")

        f = cz_frame()
        if f:
            try:
                controls = f.evaluate(ENUMERATE_JS)
            except Exception as exc:  # noqa: BLE001
                print(f"[controls] enumeration failed: {exc}")
        print(f"[controls] {len(controls)} found in the CZ frame")

        for c in controls:
            label = c["text"] or f"{c['tag']}.{(c['cls'] or '').split()[0] if c['cls'] else '?'}"
            state["action"] = f"ui:{label[:32]}"
            before = len(frames)
            try:
                f.evaluate(
                    """(pt) => { const el = document.elementFromPoint(pt.x, pt.y);
                                 if (el) el.click(); }""",
                    {"x": c["x"], "y": c["y"]},
                )
                page.wait_for_timeout(2500)
            except Exception as exc:  # noqa: BLE001
                c["click_failed"] = str(exc)[:120]
            c["frames_emitted"] = len(frames) - before
            performed.append(label)
            print(f"  ui {label[:32]!r}: +{c['frames_emitted']} frames")

        state["action"] = "idle"
        page.wait_for_timeout(5000)
        body = ""
        try:
            fr = cz_frame()
            body = fr.evaluate("() => (document.body.innerText || '').slice(0, 1500)") if fr else ""
        except Exception:  # noqa: BLE001
            pass
        ctx.close()
        browser.close()

    by_action: dict[str, list] = {}
    counts: dict[str, int] = {}
    for r in frames:
        cls, fam, direction, why = classify(r.get("decoded"), r.get("report_id"), r.get("payload_hex"))
        entry = {
            "seq": r["seq"], "method": r["method"], "report_id": r["report_id"],
            "payload_hex": r["payload_hex"], "decoded": r["decoded"],
            "transport_family": fam, "direction": direction,
            "safety_class": cls, "reason": why,
            "evidence_class": r["evidence_class"],
            "provenance": r.get("reply_provenance") or cz.PROVENANCE,
        }
        by_action.setdefault(r["ui_action"], []).append(entry)
        counts[cls] = counts.get(cls, 0) + 1

    not_performed = [c["text"] or c["tag"] for c in controls if c.get("click_failed")]
    doc = {
        "_what": "MCHOSE keyboard UI action inventory, TICKET-25 consolidation item 3",
        "_no_destructive_path_found_is_not_emitted": (
            "This tool never emits NO_DESTRUCTIVE_PATH_FOUND. Over an incomplete walk that "
            "verdict describes coverage, not the device (trap O-4). The coverage numbers are "
            "below so a person can judge."
        ),
        "_unknown_is_not_safe": (
            "UNKNOWN frames are unexplained, not benign, and are never folded into SAFE_READ."
        ),
        "_families_kept_apart": [
            "keyboard/cz (this run)", "keyboard/by (hpe, navigator.deviceHandler)",
            "mouse receiver", "ota/aa55 report id 77",
        ],
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "route_reached": route,
        "frames_total": len(frames),
        "safety_class_counts": counts,
        "modals_encountered": modals,
        "controls_found_in_cz_frame": len(controls),
        "controls_performed": performed,
        "controls_found_but_not_performed": not_performed,
        "cz_frame_body_after_walk": body,
        "by_action": by_action,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nframes            : {len(frames)}")
    print(f"safety classes    : {counts}")
    print(f"controls found    : {len(controls)}  performed: {len(performed)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
