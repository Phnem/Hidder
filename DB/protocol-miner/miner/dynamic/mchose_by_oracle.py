"""TICKET-25 priority 5: the BY keyboard family, and the wired 0x04 discriminator.

A SEPARATE tool from the CZ oracle on purpose. BY and CZ share a vendor and
nothing else: different transport, different report ids, different frame sizes,
different code base. A helper shared between them would be the first step toward
a rule crossing between them.

## What the vendor's own transport does (M HUB `purify.es`, verbatim)

```js
async writeCommand(cmd, data, params) {
  if (this.isWired) {
    let parser = oy.getCommandConfig(cmd).wiredParser;
    if (!Array.isArray(parser)) parser = await parser();
    data.command = oy.getCommandConfig(cmd).wiredCommand;
    const bytes = await serialise(parser, data);
    await this.device.sendFeatureReport(this.currentReportId,
        new Uint8Array([...bytes, ...new Array(519 - bytes.length).fill(0)]));
  }
}

async readCommand(cmd) {
  if (this.isWired) {
    const parser = oy.getCommandConfig(cmd).wiredParser;
    const req = oy.getCommandConfig(cmd).wiredCommand.slice(3).split(" ").map(p => "0x" + p);
    await this.device.sendFeatureReport(this.currentReportId, new Uint8Array(req));
    const reply = await this.device.receiveFeatureReport(this.currentReportId);
    const {buf} = IG(reply);              // IG(n, e=2) -> new Uint8Array(n.buffer).slice(2)
    return parser.parse(buf);
  }
}
```

So a wired read is a synchronous `sendFeatureReport` / `receiveFeatureReport`
pair on one report id, and the reply has its first **2** bytes dropped before
parsing. The battery parser is

```js
eae = isWired => { let p = new Parser().endianness("little");
                   if (isWired) p.array("space0", {type: "uint8", length: 6});
                   p.uint8("batteryLevel").bit4("fullStatus").bit4("chargeStatus");
                   return p; }
```

which places `batteryLevel` at **raw index 8** (2 dropped + 6 skipped) and the
status nibbles at index 9. That is the whole of what the connect screen needs:
without it `getBattery` rejects, `batteryLevel` becomes `"breakConnect"`, and
`toDeviceAliveStatus` disables the row -- the same gate that stopped the CZ lane
for three passes, reached by a different route.

## The point of the exercise

`setPerformance` and `setReset` share the wired lead byte `0x04` AND the same
parser function `j_(!0)`, so the frame LAYOUT cannot separate them. But the
bytes are serialised from the DATA object the UI passes, so driving both actions
through the vendor's own UI produces both full frames, and a byte-level diff can
say whether a stable value-level discriminator exists.

**No frame built here reaches hardware.** `assert_no_real_hid` is checked on the
live page first, and the factory-reset frame is captured precisely so nobody has
to send one to a keyboard to learn what it looks like.
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

print = functools.partial(print, flush=True)  # noqa: A001

# BY identities, taken from the vendor's own device predicates in `purify.es`:
#
#   W.vendorId === 1452 && W.productId === 591  && usagePage in [0xff00, 0xff04]
#   W.vendorId === 9610 && W.productId === 268  && usagePage === 0xff00
#   W.vendorId === 9610 && [441,443].includes(W.productId) && usagePage === 0xff00
#
# and the name resolver `getDevName`, which maps (productId, productName) to the
# internal model key:
#
#   [591].includes(pid)      && name.includes("K99")      -> "K99"
#   [268,64009].includes(pid) && name.includes("G98")     -> "G98"
#   [441,443].includes(pid)   && name.includes("G98 Pro") -> "G98_Pro"
#   [441,443].includes(pid)   && name.includes("Z75")     -> "Z75" / "Z75S"
BY_PROFILES: dict[str, dict] = {
    "k99": {
        "label": "K99",
        "vendorId": 1452,
        "productId": 591,
        "productName": "MCHOSE K99 Gaming Keyboard",
        "usagePage": 0xFF00,
        "usage": 0x0001,
        "provenance": ("purify.es device predicate vid 1452 / pid 591 / usagePage 0xff00; "
                       "getDevName maps pid 591 + name containing 'K99' to model key 'K99'; "
                       "'MCHOSE K99 Gaming Keyboard' is in the BY branch's own name list"),
    },
    "g98pro": {
        "label": "G98_Pro",
        "vendorId": 9610,
        "productId": 441,
        "productName": "MCHOSE G98 Pro",
        "usagePage": 0xFF00,
        "usage": 0x0001,
        "provenance": ("purify.es predicate vid 9610 / pid in [441,443] / usagePage 0xff00; "
                       "getDevName maps those pids + 'G98 Pro' to model key 'G98_Pro'"),
    },
    "z75": {
        "label": "Z75",
        "vendorId": 9610,
        "productId": 443,
        "productName": "MCHOSE Z75",
        "usagePage": 0xFF00,
        "usage": 0x0001,
        "provenance": ("purify.es predicate vid 9610 / pid in [441,443] / usagePage 0xff00; "
                       "getDevName maps those pids + 'Z75' to model key 'Z75'"),
    },
}

# Report ids the BY wired path uses (TICKET-24's extraction of `hpe`).
BY_REPORT_IDS = [0x03, 0x04, 0x05, 0x06, 0x10]


# The vendor writes 519-byte feature reports (`writeCommand` pads to exactly
# that) and reads replies from the same report id, so a reply is sized to match.
#
# This is not a detail. A 64-byte reply is enough for `getBattery` -- the device
# shows 100% and the row goes live -- but the other reads the keyboard page makes
# are much larger: the request templates carry a 16-bit little-endian length at
# bytes 6..7, giving 0x01f8 = 504 for getKeySetting, 0x0200 = 512 for
# getLightColor, 0x0080 = 128 for getPerformance. Answering those with 64 bytes
# made the vendor's parsers run off the end -- `Offset is outside the bounds of
# the DataView`, then `Cannot read properties of undefined (reading 'flat')` --
# so the page's config state never loaded, and with nothing to serialise from,
# NO WRITE WAS EVER BUILT. The factory-reset confirmation opened and produced
# zero frames, which looks exactly like "the reset sends nothing".
BY_FEATURE_REPORT_BYTES = 519


def battery_reply(level: int = 100, charging: bool = False, full: bool = False,
                  size: int = BY_FEATURE_REPORT_BYTES) -> bytes:
    """A reply the vendor's own parsers accept, sized to its own report length.

    Derived, not guessed: `IG` drops 2 bytes, the battery parser skips 6 more,
    then reads `batteryLevel` and a nibble pair, so level lands at raw index 8.
    Everything else is zero -- the harness has nothing to say about it, and
    inventing content is how a synthetic reply gets mistaken for an observation.

    `synthetic_from_vendor_schema`. It says the client accepts these bytes and
    nothing whatever about a battery, a key map, or a lighting configuration.
    """
    buf = bytearray(size)
    buf[8] = level & 0xFF
    buf[9] = ((1 if full else 0) << 4) | (1 if charging else 0)
    return bytes(buf)


def read_length_from_request(payload_hex: str) -> int | None:
    """The 16-bit little-endian length a wired read template asks for.

    From `hpe`: `getBattery` is `06 87 00 00 01 00 02 00 ...`, `getKeySetting`
    `06 83 00 00 01 00 F8 01 ...`. The report id is stripped before the write, so
    bytes 6..7 of what reaches `sendFeatureReport` are the length.
    """
    try:
        raw = bytes.fromhex(payload_hex or "")
    except ValueError:
        return None
    if len(raw) < 8 or not (raw[0] & 0x80):
        return None
    return raw[6] | (raw[7] << 8)


def build_config(profile: dict) -> dict:
    return {
        "vendorId": profile["vendorId"],
        "productId": profile["productId"],
        "productName": profile["productName"],
        "collections": [{
            "usagePage": profile["usagePage"],
            "usage": profile["usage"],
            "inputReports": [{"reportId": r} for r in BY_REPORT_IDS],
            "outputReports": [{"reportId": r} for r in BY_REPORT_IDS],
            "featureReports": [{"reportId": r} for r in BY_REPORT_IDS],
        }],
    }


def classify_by_frame(method: str, report_id, payload_hex: str) -> tuple[str, str]:
    """(safety_class, why) for a BY frame, using only BY facts."""
    body = (payload_hex or "").lower()
    lead = body[0:2]
    if method == "sendFeatureReport" and lead == "87":
        return "SAFE_READ", "wired getBattery request template (hpe: 06 87 00 00 01 00 02 00 ...)"
    if method == "sendFeatureReport" and lead in ("82", "83", "84", "86", "8a"):
        return "SAFE_READ", f"wired read template, command byte 0x{lead}"
    if method == "sendFeatureReport" and lead == "04":
        return ("POTENTIALLY_DESTRUCTIVE",
                "wired lead 0x04 is shared by setPerformance and setReset, which are built by "
                "the same parser; the frame shape cannot say which this is")
    if method == "sendFeatureReport" and lead in ("03", "05", "06", "10"):
        return "UNKNOWN", f"wired write with lead byte 0x{lead}; no established inverse"
    return "UNKNOWN", "does not match a characterised BY template"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(BY_PROFILES), default="k99")
    ap.add_argument("--out", required=True)
    ap.add_argument("--battery", type=int, default=100)
    ap.add_argument("--settle", type=float, default=30.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    profile = BY_PROFILES[args.profile]
    frames: list[dict] = []
    state = {"action": "boot"}
    reply = battery_reply(args.battery)

    print(f"profile: BY {profile['label']}  "
          f"{profile['vendorId']:#06x}:{profile['productId']:#06x} "
          f"usagePage {profile['usagePage']:#06x}")
    print(f"provenance: {profile['provenance']}")

    def on_frame(_src, req):
        method = req.get("method")
        rec = {
            "seq": len(frames) + 1,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "ui_action": state["action"],
            "method": method,
            "report_id": req.get("report_id"),
            "payload_hex": req.get("bytes_hex"),
            "payload_len": len((req.get("bytes_hex") or "")) // 2,
        }
        if method == "receiveFeatureReport":
            # The only thing this harness answers, and it answers the same bytes
            # every time so nothing downstream can be mistaken for a measurement.
            rec["evidence_class"] = "synthetic_from_vendor_schema"
            rec["reply_hex"] = reply.hex()
            frames.append(rec)
            print(f"  [{rec['seq']:>4}] {rec['ui_action']:<22} RX rid={rec['report_id']} "
                  f"<- synthetic battery reply")
            return {"hex": reply.hex(), "strategy": "SYNTHETIC_FROM_VENDOR_SCHEMA",
                    "confidence": 0.0}
        rec["evidence_class"] = "no_reply"
        frames.append(rec)
        if method in ("sendFeatureReport", "sendReport"):
            print(f"  [{rec['seq']:>4}] {rec['ui_action']:<22} TX {method} "
                  f"rid={rec['report_id']} len={rec['payload_len']} "
                  f"{(rec['payload_hex'] or '')[:40]}")
        return {"ack": True, "strategy": "NO_RESPONSE", "confidence": 0.0}

    init = (f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
            f"window.__protocolMinerCannedResponses = {{}};\n"
            + _RUNTIME_JS.read_text(encoding="utf-8"))

    console: list[str] = []
    route = None
    body = ""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond", on_frame)
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text[:240]}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {str(e)[:240]}"))

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

        listing = page.evaluate(
            """() => ({
                 body: (document.body.innerText || '').slice(0, 700),
                 deviceMap: navigator.deviceMap ? Object.keys(navigator.deviceMap) : null,
                 hasHandler: !!navigator.deviceHandler,
                 handlerWired: (navigator.deviceHandler || {}).isWired ?? null
               })""")
        print(f"[listing] deviceMap={listing['deviceMap']} handler={listing['hasHandler']} "
              f"wired={listing['handlerWired']}")
        print(f"[listing] body: {listing['body'][:200]!r}")

        state["action"] = "open_device"
        opened = page.evaluate(
            """(p) => { const re = new RegExp(p, 'i');
                 const c = Array.from(document.querySelectorAll('div,span,button,li,p'))
                   .filter(e => re.test((e.innerText || '').trim()))
                   .map(e => { const r = e.getBoundingClientRect(); return {e, a: r.width * r.height}; })
                   .filter(o => o.a > 0).sort((x, y) => x.a - y.a);
                 if (!c.length) return null;
                 c[0].e.click();
                 return {text: (c[0].e.innerText || '').trim().slice(0, 40), candidates: c.length}; }""",
            f"^{profile['label']}$")
        print(f"[open] {opened!r}")
        page.wait_for_timeout(int(args.seconds * 1000))

        route = page.evaluate(
            "() => { try { return window.$router.currentRoute.value.name; } catch (e) { return null; } }")
        print(f"[route] {route!r}")
        try:
            body = page.evaluate("() => (document.body.innerText || '').slice(0, 1800)")
        except Exception:  # noqa: BLE001
            pass
        ctx.close()
        browser.close()

    rows = []
    for r in frames:
        cls, why = classify_by_frame(r["method"], r["report_id"], r["payload_hex"])
        rows.append({**r, "transport_family": "keyboard/by", "safety_class": cls, "reason": why})

    doc = {
        "_what": "MCHOSE BY keyboard oracle, TICKET-25 priority 5",
        "_family": "keyboard/BY only. Nothing here is shared with CZ, the mouse receiver, "
                   "the AA-55 OTA path, or AULA.",
        "_synthetic": "the only reply served is a getBattery frame built from the vendor's own "
                      "parser layout; it is synthetic_from_vendor_schema and is not evidence",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": profile,
        "route_reached": route,
        "frames_total": len(frames),
        "frames": rows,
        "body_snapshot": body,
        "console": [c for c in console if "__PM_TRACE__" not in c][-120:],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nframes: {len(frames)}  route: {route!r}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
