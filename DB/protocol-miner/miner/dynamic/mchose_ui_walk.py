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
import functools
import json
import sys
import time
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
from miner.static.mchose_cz_device_image import build_god60_image  # noqa: E402

CZ_FRAME_URL = "/cizhou/"

print = functools.partial(print, flush=True)  # noqa: A001

# Controls the CZ app renders. Enumerated by role/class rather than by label,
# because the page renders in the browser's locale and matching localised text
# is how an earlier pass concluded a control "did not exist".
# The configurator's top-level sections. Found by ROLE and position rather than
# by label, because the page renders in the browser's locale and matching
# localised text is how an earlier pass concluded a control "did not exist".
TABS_JS = """() => {
  // The section strip is a ROW OF SIBLINGS, and that is what identifies it.
  //
  // Position and size do not: the first version took every short-text element
  // near the top of the frame and swept in 50 keycaps ('Esc', '1 !', 'Q', ...),
  // which are also short, also near the top, and would have consumed the entire
  // walk budget before reaching Performance or Trigger. Grouping candidates by
  // parent separates them cleanly -- the keyboard grid has 60+ children, a tab
  // strip has a handful -- without hardcoding a single localised label.
  const cands = [];
  document.querySelectorAll('div,li,button,span,[role=tab]').forEach(e => {
    const txt = (e.innerText || '').trim();
    if (!txt || txt.length > 24 || txt.indexOf(String.fromCharCode(10)) >= 0) return;
    const r = e.getBoundingClientRect();
    if (r.width < 30 || r.width > 300 || r.height < 16 || r.height > 70) return;
    cands.push({e, txt, r});
  });
  const byParent = new Map();
  cands.forEach(c => {
    const key = c.e.parentElement;
    if (!key) return;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(c);
  });
  // EVERY qualifying row is returned, not the "best" one. This page has at
  // least two: a profile strip (God 60 / Кабельный / инст. / Облако / ...) and
  // the section strip (Освещение / Триггера / ... / Производительность). Picking
  // one by a size-or-position rule picked the wrong one, and a rule that can
  // pick the wrong one silently is worse than walking both and saying so.
  const groups = [];
  byParent.forEach((group) => {
    if (group.length < 4 || group.length > 12) return;          // strip, not grid
    const ys = group.map(g => g.r.y);
    if (Math.max(...ys) - Math.min(...ys) > 24) return;         // one row
    const texts = new Set(group.map(g => g.txt));
    if (texts.size !== group.length) return;                    // distinct labels
    groups.push({top: Math.min(...ys), group});
  });
  groups.sort((a, b) => a.top - b.top);
  const out = [];
  groups.forEach((entry, gi) => {
    entry.group.sort((a, b) => a.r.x - b.r.x).forEach(g => {
      out.push({text: g.txt, strip: gi,
                x: Math.round(g.r.x + g.r.width / 2),
                y: Math.round(g.r.y + g.r.height / 2),
                area: Math.round(g.r.width * g.r.height)});
    });
  });
  return out;
}"""

# Controls INSIDE the currently visible panel. Returned with a stable selector
# index so a click can re-query rather than trust a cached coordinate: the panel
# reflows while frames are in flight, and a stale point clicks whatever moved
# into it -- which is how a walk silently attributes frames to the wrong action.
CONTROL_SEL = ('button,[role=switch],[role=slider],input,select,'
               '[class*=switch],[class*=Switch],[class*=slider],[class*=Slider],'
               '[class*=radio],[class*=Radio],[class*=checkbox]')

ENUMERATE_JS = """(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((e, i) => {
    const r = e.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    if (r.bottom < 0 || r.top > window.innerHeight) return;
    out.push({
      sel_index: i,
      tag: e.tagName,
      role: e.getAttribute('role'),
      type: e.getAttribute('type'),
      cls: String(e.className || '').slice(0, 90),
      text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 48),
      disabled: !!e.disabled
    });
  });
  return out;
}"""

CLICK_BY_INDEX_JS = """(arg) => {
  const el = document.querySelectorAll(arg.sel)[arg.i];
  if (!el) return 'gone';
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return 'not-visible';
  el.scrollIntoView({block: 'center'});
  el.click();
  return 'clicked';
}"""

# A modal is an OVERLAY, not "a page with a primary button on it". The first
# version matched any `button.maicong-btn.type-primary` anywhere, so once the
# real dialog closed it went on clicking whatever primary button the panel
# happened to render -- which on this page is "По умолчанию", restore-defaults.
# Clicking a restore-defaults control while believing you are dismissing a
# dialog is precisely the unlabelled-action hazard this tool exists to avoid, so
# the container is required and the dialog's text is recorded before anything is
# clicked.
MODAL_JS = """() => {
  const containers = Array.from(document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[class*=modal],[class*=Modal],[class*=dialog],[class*=Dialog]'));
  for (const c of containers) {
    const r = c.getBoundingClientRect();
    if (r.width < 120 || r.height < 60) continue;
    const style = window.getComputedStyle(c);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const btns = Array.from(c.querySelectorAll('button'));
    if (!btns.length) continue;
    const primary = btns.find(b => (b.className || '').includes('type-primary')) || btns[0];
    return {
      container_class: String(c.className || '').slice(0, 80),
      text: (c.innerText || '').trim().slice(0, 400),
      buttons: btns.map(b => (b.innerText || '').trim().slice(0, 40)),
      will_click: (primary.innerText || '').trim().slice(0, 40)
    };
  }
  return null;
}"""

CLICK_PRIMARY_JS = """() => {
  const containers = Array.from(document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[class*=modal],[class*=Modal],[class*=dialog],[class*=Dialog]'));
  for (const c of containers) {
    const r = c.getBoundingClientRect();
    if (r.width < 120 || r.height < 60) continue;
    const style = window.getComputedStyle(c);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const btns = Array.from(c.querySelectorAll('button'));
    if (!btns.length) continue;
    const primary = btns.find(b => (b.className || '').includes('type-primary')) || btns[0];
    primary.click();
    return true;
  }
  return false;
}"""



def enter_configurator(page, profile: dict, settle: float = 25.0):
    """Drive the app from a cold page to a rendered CZ configurator.

    Shared rather than copied. The sweep tool had its own version of this
    sequence and it diverged: same waits, slightly different modal selector, and
    it never reached the section strip while this one did every time. Two copies
    of a fragile flow is itself the defect, so there is one.

    Returns (cz_frame, tabs). Raises SystemExit if the strip never appears --
    "nothing was swept" is a result worth stating, not an empty file.
    """
    page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
    page.evaluate("() => document.querySelector('button.mc-button').click()")
    ready = wait_device_ready(page)
    print(f"[ready] {json.dumps(ready, ensure_ascii=False)}")

    page.evaluate(
        """(p) => { const re = new RegExp(p, 'i');
             const c = Array.from(document.querySelectorAll('div,span,button,li,p'))
               .filter(e => re.test((e.innerText || '').trim()))
               .map(e => { const r = e.getBoundingClientRect(); return {e, a: r.width * r.height}; })
               .filter(o => o.a > 0).sort((x, y) => x.a - y.a);
             if (c.length) c[0].e.click(); }""",
        f"^{profile['label']}$")
    page.wait_for_timeout(int(settle * 1000))

    def cz_frame():
        for fr in page.frames:
            if CZ_FRAME_URL in fr.url:
                return fr
        return None

    tabs: list = []
    for _ in range(14):
        f = cz_frame()
        if f is None:
            page.wait_for_timeout(4000)
            continue
        try:
            tabs = f.evaluate(TABS_JS)
        except Exception:  # noqa: BLE001
            tabs = []
        if tabs:
            return f, tabs
        try:
            did = f.evaluate(MODAL_JS)
            if did:
                print(f"[modal] {did['will_click']!r} of {did['buttons']}")
                f.evaluate(CLICK_PRIMARY_JS)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(5000)
    raise SystemExit("the section strip never appeared; nothing is claimed from this run")


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
    ap.add_argument("--default-keys", default=None,
                    help="a shipped default-keys-*.js. Backs the key-matrix reads with the "
                         "VENDOR'S OWN default table so the configurator renders. Every byte "
                         "served this way is synthetic_from_vendor_schema, never an observation.")
    ap.add_argument("--budget-seconds", type=float, default=600.0,
                    help="wall-clock budget for the control walk; on expiry the remaining "
                         "controls are recorded as not-performed with that reason, because "
                         "a partial walk that states its coverage beats a killed one")
    ap.add_argument("--per-control-ms", type=int, default=1200)
    ap.add_argument("--max-controls-per-tab", type=int, default=24)
    ap.add_argument("--image-manifest", default=None,
                    help="where to write what the image actually backed")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = PROFILES[args.profile]
    display_name = profile["label"]
    frames: list[dict] = []
    state = {"action": "boot"}

    image = None
    image_manifest = None
    if args.default_keys:
        image, image_manifest = build_god60_image(Path(args.default_keys))
        print(f"[image] {image_manifest['key_count']} keys x "
              f"{image_manifest['bytes_per_key']} bytes -> "
              f"{image_manifest['used_key_area_size']}-byte regions, "
              f"{image_manifest['layer_slots_filled']} slots, from "
              f"{image_manifest['source_file']}")

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
                payload = None
                if image is not None:
                    f = cz.parse(raw)
                    backed = image.read(f.command, f.offset, f.size)
                    if backed is not None:
                        payload = list(backed)
                        rec["reply_backed_by_image"] = True
                reply_hex = cz.synthesize_reply(raw, payload).hex()
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

        # enter_configurator owns the whole sequence: connect click, readiness
        # wait, device-row click, modal dismissal, strip detection. Keeping a
        # copy of the first step here is what broke the sweep -- the second wait
        # for the connect button timed out because the first click removed it.
        state["action"] = "connect"
        f, tabs = enter_configurator(page, profile, settle=args.settle)
        route = page.evaluate(
            "() => { try { return window.$router.currentRoute.value.name; } catch (e) { return null; } }")
        print(f"[route] {route!r}")
        strips: dict[int, list[str]] = {}
        for t in tabs:
            strips.setdefault(t.get("strip", 0), []).append(t["text"])
        for gi, names in sorted(strips.items()):
            print(f"[strip {gi}] {names}")

        deadline = time.monotonic() + args.budget_seconds
        for tab in tabs:
            if time.monotonic() > deadline:
                tab["skipped"] = "walk budget exhausted before this section"
                continue
            state["action"] = f"tab:{tab.get('strip', 0)}:{tab['text'][:22]}"
            before = len(frames)
            try:
                f.evaluate("""(pt) => { const el = document.elementFromPoint(pt.x, pt.y);
                                        if (el) el.click(); }""", {"x": tab["x"], "y": tab["y"]})
                page.wait_for_timeout(3000)
            except Exception as exc:  # noqa: BLE001
                tab["click_failed"] = str(exc)[:120]
            tab["frames_emitted"] = len(frames) - before
            print(f"  tab {tab['text'][:20]!r}: +{tab['frames_emitted']} frames")

            try:
                panel = f.evaluate(ENUMERATE_JS, CONTROL_SEL)
            except Exception as exc:  # noqa: BLE001
                print(f"    controls failed: {exc}")
                continue
            panel = panel[: args.max_controls_per_tab]
            for c in panel:
                c["tab"] = tab["text"]
                if time.monotonic() > deadline:
                    c["skipped"] = "walk budget exhausted before this control"
                    controls.append(c)
                    continue
                label = c["text"] or f"{c['tag']}.{(c['cls'] or '').split()[0] if c['cls'] else '?'}"
                if c["disabled"]:
                    c["skipped"] = "disabled by the app"
                    controls.append(c)
                    continue
                state["action"] = f"ui:{tab['text'][:14]}/{label[:24]}"
                b = len(frames)
                try:
                    r = f.evaluate(CLICK_BY_INDEX_JS,
                                   {"sel": CONTROL_SEL, "i": c["sel_index"]})
                    c["click_result"] = r
                    page.wait_for_timeout(args.per_control_ms)
                except Exception as exc:  # noqa: BLE001
                    c["click_failed"] = str(exc)[:120]
                c["frames_emitted"] = len(frames) - b
                # A control may have opened a dialog. Leaving it up would make
                # every later click land on the overlay and be attributed to the
                # wrong action, so it is dismissed and recorded as its own step.
                try:
                    opened = f.evaluate(MODAL_JS)
                    if opened:
                        c["opened_dialog"] = {"text": opened["text"][:200],
                                              "buttons": opened["buttons"],
                                              "dismissed_with": opened["will_click"]}
                        modals.append({**opened, "opened_by": label})
                        f.evaluate(CLICK_PRIMARY_JS)
                        page.wait_for_timeout(1200)
                except Exception:  # noqa: BLE001
                    pass
                controls.append(c)
                performed.append(f"{tab['text']}/{label}")
                if c["frames_emitted"]:
                    print(f"    {label[:28]!r}: +{c['frames_emitted']} frames")

        state["action"] = "idle"
        page.wait_for_timeout(5000)
        body = ""
        try:
            body = f.evaluate("() => (document.body.innerText || '').slice(0, 1500)")
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

    not_performed = [
        {"control": c.get("text") or c.get("tag"), "tab": c.get("tab"),
         "reason": c.get("skipped") or c.get("click_failed") or c.get("click_result")}
        for c in controls
        if c.get("skipped") or c.get("click_failed") or c.get("click_result") != "clicked"
    ]
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
        "device_image": image_manifest,
        "frames_total": len(frames),
        "safety_class_counts": counts,
        "modals_encountered": modals,
        "tabs_found": tabs,
        "controls_found_in_cz_frame": len(controls),
        "ui_inventory_coverage": f"{len(performed)} / {len(controls)}",
        "coverage_caveat": (
            f"enumeration was capped at {args.max_controls_per_tab} controls per section and "
            f"the walk had a {args.budget_seconds:.0f}s budget, so 'controls found' is not "
            "'controls that exist'. A section with more controls than the cap contributed only "
            "the first ones the DOM returned."),
        "controls_enumeration_cap_per_tab": args.max_controls_per_tab,
        "walk_budget_seconds": args.budget_seconds,
        "controls_performed": performed,
        "controls_found_but_not_performed": not_performed,
        "cz_frame_body_after_walk": body,
        "console": [c for c in console if "__PM_TRACE__" not in c][-120:],
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
