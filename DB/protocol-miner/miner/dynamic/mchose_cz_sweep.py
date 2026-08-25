"""TICKET-25 consolidation, item 3: controlled sweeps against the CZ configurator.

Moves ONE control through >= 4 distinct values and diffs the frames the vendor's
own code emits. Offset, width and encoding are reported only where the diff is
stable across every step; anything less is withheld with the reason attached.

## What separates a sweep from a story

Three rules, each of which has a way of being violated quietly:

*   **Transport bytes are excluded before the diff, not explained after it.**
    The CZ envelope's byte 3 is `sum(bytes 4..) & 0xFF`, so it changes whenever
    anything else does. Reporting it as a field that tracks the parameter would
    be true and useless. Header bytes 0..7 are separated from payload bytes and
    labelled `transport_derived`.
*   **One parameter at a time.** The tool sets a single control per sweep and
    records everything the page emitted, so a frame that moved for another
    reason shows up as an unexplained group rather than as evidence.
*   **A stride needs several records.** Not asserted from two positions.

## Provenance

Frames are `OBSERVED_FROM_VENDOR_UI`: the vendor's client built them in response
to its own control being moved. That is stronger than static inference and
weaker than hardware -- the device that answered the reads was this harness, so
any value the UI *displayed* is `synthetic_from_vendor_schema` and only the
values we *set* are ours to reason from.
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

from miner.dynamic.mchose_ui_walk import CONTROL_SEL, enter_configurator  # noqa: E402
from miner.dynamic.mchose_oracle import (  # noqa: E402
    PROFILES, TARGET, _RUNTIME_JS, assert_no_real_hid, build_config,
)
from miner.static import mchose_cz_codec as cz  # noqa: E402
from miner.static.mchose_cz_device_image import build_god60_image  # noqa: E402

CZ_FRAME_URL = "/cizhou/"

print = functools.partial(print, flush=True)  # noqa: A001

# Numeric controls only: a sweep needs an ordered set of values, and a toggle
# gives two. Range inputs and number inputs are addressed by their own value
# property so the vendor's handlers run exactly as they do for a user drag.
NUMERIC_JS = """(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((e, i) => {
    const r = e.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    const isRange = e.tagName === 'INPUT' && (e.type === 'range' || e.type === 'number');
    const isSlider = (e.getAttribute('role') === 'slider');
    if (!isRange && !isSlider) return;
    out.push({
      sel_index: i, tag: e.tagName, type: e.getAttribute('type'),
      role: e.getAttribute('role'),
      cls: String(e.className || '').slice(0, 90),
      min: e.min !== undefined && e.min !== '' ? Number(e.min)
           : (e.getAttribute('aria-valuemin') !== null ? Number(e.getAttribute('aria-valuemin')) : null),
      max: e.max !== undefined && e.max !== '' ? Number(e.max)
           : (e.getAttribute('aria-valuemax') !== null ? Number(e.getAttribute('aria-valuemax')) : null),
      step: e.step !== undefined && e.step !== '' ? Number(e.step) : null,
      value: e.value !== undefined && e.value !== '' ? Number(e.value)
             : (e.getAttribute('aria-valuenow') !== null ? Number(e.getAttribute('aria-valuenow')) : null)
    });
  });
  return out;
}"""

# React/MUI keep their own copy of an input's value, so assigning `.value`
# directly is swallowed on the next render. The native setter plus a bubbling
# `input` event is the documented way to make the vendor's own onChange run --
# which matters, because the whole point is that the VENDOR builds the frame.
SET_VALUE_JS = """(arg) => {
  const el = document.querySelectorAll(arg.sel)[arg.i];
  if (!el) return 'gone';
  const proto = el.tagName === 'INPUT' ? window.HTMLInputElement.prototype : null;
  const setter = proto && Object.getOwnPropertyDescriptor(proto, 'value')
      ? Object.getOwnPropertyDescriptor(proto, 'value').set : null;
  if (setter) setter.call(el, String(arg.value));
  else el.value = String(arg.value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return 'set';
}"""


def sweep_values(ctrl: dict, want: int = 5) -> list[int]:
    """Distinct, ordered values spanning the control's own declared range."""
    lo = ctrl.get("min")
    hi = ctrl.get("max")
    if lo is None or hi is None or hi <= lo:
        return []
    step = ctrl.get("step") or 1
    span = hi - lo
    raw = [lo + span * k / (want - 1) for k in range(want)]
    vals: list[int] = []
    for v in raw:
        snapped = round((v - lo) / step) * step + lo
        snapped = max(lo, min(hi, snapped))
        iv = int(round(snapped))
        if iv not in vals:
            vals.append(iv)
    return vals


def diff_groups(by_value: dict[int, list[dict]]) -> dict:
    """Compare frames across sweep values, grouped by (command, offset)."""
    groups: dict[tuple, dict[int, str]] = {}
    for value, frames in by_value.items():
        for fr in frames:
            d = fr.get("decoded")
            if not d:
                continue
            key = (d["command"], d["offset"])
            groups.setdefault(key, {})[value] = fr["payload_hex"]

    results = []
    for (command, offset), per_value in sorted(groups.items()):
        if len(per_value) < 4:
            results.append({
                "command": f"{command:#04x}", "offset": offset,
                "values_seen": sorted(per_value),
                "verdict": "WITHHELD",
                "why": f"only {len(per_value)} distinct values produced a frame here; "
                       "4 is the minimum before a field can be argued",
            })
            continue
        values = sorted(per_value)
        raws = [bytes.fromhex(per_value[v]) for v in values]
        width = min(len(r) for r in raws)
        moving = [i for i in range(width) if len({r[i] for r in raws}) > 1]
        header = [i for i in moving if i < cz.HEADER_LENGTH]
        payload = [i for i in moving if i >= cz.HEADER_LENGTH]

        fields = []
        for i in payload:
            seq = [r[i] for r in raws]
            monotonic = all(a <= b for a, b in zip(seq, seq[1:])) or \
                        all(a >= b for a, b in zip(seq, seq[1:]))
            exact = all(s == (v & 0xFF) for s, v in zip(seq, values))
            # A CZ write is chunked: the same record is sent at offset 0 and
            # again at offset 8, so a field appears at two different payload
            # offsets in the same sweep. Reducing both to a RECORD offset makes
            # them agree, and that agreement is a free consistency check on the
            # whole inference -- if the two chunks disagreed, the reading would
            # be wrong somewhere.
            record_offset = offset + (i - cz.HEADER_LENGTH)
            scale = None
            scale_note = None
            if all(s_ for s_ in seq) and all(v % s_ == 0 for s_, v in zip(seq, values) if s_):
                ks = {v // s_ for s_, v in zip(seq, values) if s_}
                if len(ks) == 1:
                    k = ks.pop()
                    if k != 1:
                        scale = k
                        scale_note = (f"control value = byte * {k} across all "
                                      f"{len(values)} points")
            fields.append({
                "payload_offset": i - cz.HEADER_LENGTH,
                "frame_offset": i,
                "record_offset": record_offset,
                "values": seq,
                "control_values": values,
                "tracks_control_value_exactly": exact,
                "scale_factor": scale,
                "encoding": ("byte == control value" if exact else
                             (f"byte == control value / {scale}" if scale else
                              "not established: the byte moves with the control but no exact "
                              "or single-scale relation holds across every point")),
                "encoding_note": scale_note,
                "monotonic_in_control_value": monotonic,
                "width_bits": 8,
                "width_note": "one byte moved; a wider field would show adjacent bytes moving together",
            })
        results.append({
            "command": f"{command:#04x}", "offset": offset,
            "values_seen": values,
            "frame_bytes": width,
            "transport_derived_bytes_that_moved": header,
            "transport_note": ("byte 3 is sum(bytes 4..) & 0xFF, so it moves whenever the "
                               "payload does; it is not a field"),
            "semantic_bytes_that_moved": payload,
            "fields": fields,
            "record_offsets": sorted({f["record_offset"] for f in fields}),
            "verdict": "FIELDS_OBSERVED" if fields else "NO_PAYLOAD_MOVEMENT",
            "why": ("payload bytes moved with the control and nothing else was touched"
                    if fields else
                    "the frame did not change with the control; this command does not carry it"),
        })
    return {"groups": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="god60")
    ap.add_argument("--default-keys", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tab", default=None, help="section to sweep in; default: every section")
    ap.add_argument("--max-controls", type=int, default=4)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = PROFILES[args.profile]
    image, image_manifest = build_god60_image(Path(args.default_keys))
    frames: list[dict] = []
    state = {"action": "boot"}

    def on_frame(_src, req):
        rec = {"seq": len(frames) + 1, "ui_action": state["action"],
               "method": req.get("method"), "report_id": req.get("report_id"),
               "payload_hex": req.get("bytes_hex"), "decoded": None}
        reply_hex = None
        try:
            raw = bytes.fromhex(req.get("bytes_hex") or "")
            if len(raw) >= cz.HEADER_LENGTH and raw[0] in (cz.FLAG_REQUEST, cz.FLAG_EXPECT_REPLY):
                rec["decoded"] = cz.parse(raw).as_dict()
                if req.get("method") == "sendReport" and raw[0] == cz.FLAG_REQUEST:
                    f = cz.parse(raw)
                    backed = image.read(f.command, f.offset, f.size)
                    reply_hex = cz.synthesize_reply(raw, list(backed) if backed else None).hex()
        except Exception:  # noqa: BLE001
            pass
        rec["evidence_class"] = "synthetic_from_vendor_schema" if reply_hex else "no_reply"
        frames.append(rec)
        if reply_hex:
            return {"ack": True, "reply": {"reportId": req.get("report_id"), "hex": reply_hex},
                    "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA", "confidence": 0.0}
        if req.get("method") == "receiveFeatureReport":
            return {"hex": "", "strategy": "NO_RESPONSE", "confidence": 0.0}
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    init = (f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
            f"window.__protocolMinerCannedResponses = {{}};\n"
            + _RUNTIME_JS.read_text(encoding="utf-8"))

    sweeps = []
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

        # enter_configurator does the connect click, the readiness wait, the
        # device-row click and the modal dismissal. Doing any of it here as well
        # is what broke the previous run: the second wait for the connect button
        # timed out because the first click had already removed it.
        state["action"] = "connect"
        frame, tabs = enter_configurator(page, profile, settle=26.0)
        strips: dict[int, list[str]] = {}
        for tb in tabs:
            strips.setdefault(tb.get("strip", 0), []).append(tb["text"])
        for gi, names in sorted(strips.items()):
            print(f"[strip {gi}] {names}")
        wanted = [tb for tb in tabs if (args.tab is None or args.tab.lower() in tb["text"].lower())]

        for tab in wanted:
            try:
                frame.evaluate("""(pt) => { const el = document.elementFromPoint(pt.x, pt.y);
                                            if (el) el.click(); }""",
                               {"x": tab["x"], "y": tab["y"]})
                page.wait_for_timeout(3000)
            except Exception:  # noqa: BLE001
                continue
            try:
                numeric = frame.evaluate(NUMERIC_JS, CONTROL_SEL)
            except Exception:  # noqa: BLE001
                continue
            if not numeric:
                continue
            print(f"[{tab['text']}] numeric controls: {len(numeric)}")

            for ctrl in numeric[: args.max_controls]:
                values = sweep_values(ctrl)
                if len(values) < 4:
                    sweeps.append({"tab": tab["text"], "control": ctrl,
                                   "verdict": "SKIPPED",
                                   "why": f"the control declares min={ctrl['min']} max={ctrl['max']}; "
                                          "fewer than 4 distinct values are reachable"})
                    continue
                print(f"  sweeping {ctrl['cls'][:34]!r} over {values}")
                by_value: dict[int, list[dict]] = {}
                for v in values:
                    state["action"] = f"sweep:{tab['text'][:12]}:{ctrl['sel_index']}={v}"
                    start = len(frames)
                    try:
                        frame.evaluate(SET_VALUE_JS,
                                       {"sel": CONTROL_SEL, "i": ctrl["sel_index"], "value": v})
                        page.wait_for_timeout(2200)
                    except Exception as exc:  # noqa: BLE001
                        print(f"    set {v} failed: {exc}")
                    emitted = frames[start:]
                    by_value[v] = [f for f in emitted if f.get("decoded")]
                    print(f"    {v}: +{len(emitted)} frames")
                sweeps.append({
                    "tab": tab["text"], "control": ctrl, "values": values,
                    "provenance": "OBSERVED_FROM_VENDOR_UI in a SYNTHETIC_ENVIRONMENT",
                    "confidence_note": (
                        "the vendor's client built these frames from its own control; the device "
                        "that answered its reads was this harness, so displayed values are not "
                        "observations and only the values we set are ours to reason from"),
                    **diff_groups(by_value),
                    "raw_frames_by_value": {
                        str(v): [{"payload_hex": fr["payload_hex"], "decoded": fr["decoded"]}
                                 for fr in fs]
                        for v, fs in by_value.items()
                    },
                })
        ctx.close()
        browser.close()

    doc = {
        "_what": "MCHOSE CZ controlled sweeps, TICKET-25 consolidation item 3",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "device_image": image_manifest,
        "frames_total": len(frames),
        "sweeps": sweeps,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsweeps: {len(sweeps)}  frames: {len(frames)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
