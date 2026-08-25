"""TICKET-25 identity lane: ask the vendor's own SDK to name each identity.

The CZ SDK (`window.loadCZSharedData()`) exposes pure lookup functions over the
vendor's device tables -- `getDeviceName`, `getDeviceType`, `getDeviceStorageKey`,
`isBootUpdateMode`, `getDeviceData` and friends. Calling them with an identity
tuple is a source that carries BOTH halves of a vid:pid -> name edge in one
record, which is the only kind of source the name-link rule admits. Nothing is
inferred from similarity, vendor id, or family.

No device is touched. These are table lookups; the sweep runs with the fake HID
runtime installed and asserts that before it starts, for the same reason the
oracle does.

One caveat that has to travel with the output: `isBootUpdateMode` telling us a
product id is the DFU interface is the vendor's own claim about its firmware,
not an observation of hardware. It is recorded as a vendor claim. It happens to
be corroborated by observation for God 60 -- advertising 0x301a made the live
app open its firmware-update dialog -- and that corroboration is noted per row.
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
)

SWEEP_JS = """async (cands) => {
  const sdk = await window.loadCZSharedData();
  const call = (fn, ...a) => {
    try {
      const r = sdk[fn] ? sdk[fn](...a) : '<absent>';
      if (r === undefined) return null;
      return (r && typeof r === 'object') ? JSON.parse(JSON.stringify(r)) : r;
    } catch (e) { return 'THREW: ' + String(e).slice(0, 90); }
  };
  return cands.map(([v, p]) => ({
    vendorId: v, productId: p,
    deviceName: call('getDeviceName', v, p, null),
    fullName: call('getDeviceFullName', v, p, null),
    storageKey: call('getDeviceStorageKey', v, p, null),
    glbKey: call('getDeviceGLBKeyboardKey', v, p, null),
    deviceType: call('getDeviceType', v, p, null),
    layoutId: call('getDeviceLayoutID', v, p, null),
    isWireless: call('getDeviceIsWireless', v, p, null),
    isBootUpdateMode: call('isBootUpdateMode', v, p, null),
    hasDeviceData: call('getDeviceData', v, p, null) == null ? false : true
  }));
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filters", default=None,
                    help="cz_remote_config.json holding HidIndexDeviceFilters")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    filters_path = Path(args.filters) if args.filters else (
        _DB_ROOT / "reports" / "protocol_knowledge" / "mchose" / "static" / "cz_remote_config.json")
    cfg = json.loads(filters_path.read_text(encoding="utf-8"))
    entries = cfg["config"]["HidIndexDeviceFilters"]
    pairs = sorted({(e["vendorId"], e["productId"]) for e in entries if "productId" in e})
    print(f"identities to ask about: {len(pairs)}")

    from playwright.sync_api import sync_playwright

    profile = PROFILES["god60"]
    init = (
        f"window.__protocolMinerDeviceConfig = {json.dumps(build_config(profile))};\n"
        f"window.__protocolMinerCannedResponses = {{}};\n"
        + _RUNTIME_JS.read_text(encoding="utf-8")
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        ctx.expose_binding("__protocolMinerBridgeRespond",
                           lambda s, r: {"ack": True, "strategy": "NO_RESPONSE"})
        ctx.expose_binding("__protocolMinerBridgeRecord", lambda s, t: None)
        ctx.add_init_script(init)
        page = ctx.new_page()

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
            raise SystemExit("page never rendered; nothing was asked and nothing is claimed")

        assert_no_real_hid(page)
        print("[safety] navigator.hid is the fake runtime")

        # Drive the app's own initialisation before calling into the SDK.
        # Calling loadCZSharedData() cold from an evaluate throws inside the SDK
        # ("setImmediate is not defined") because the app installs environment
        # shims before its first call; using its path keeps this a read of the
        # vendor's tables rather than a differently-configured re-run of them.
        page.wait_for_selector("button.mc-button", state="attached", timeout=40000)
        page.evaluate("() => document.querySelector('button.mc-button').click()")
        for _ in range(60):
            if page.evaluate("() => { const s = window.SingletonDeviceStore;"
                             "        return !!(s && s.allDeviceState && s.allDeviceState.size > 0); }"):
                break
            page.wait_for_timeout(1000)

        rows = page.evaluate(SWEEP_JS, [list(p) for p in pairs])
        ctx.close()
        browser.close()

    # `getDeviceName` ALWAYS returns a string. For an identity the CZ table does
    # not know it returns the default "Ace60" -- no space, empty fullName, empty
    # storageKey, null glbKey. Taking that at face value would have recorded 59
    # devices (mice, audio, and the whole non-CZ keyboard family) as being named
    # "Ace60", which is a fabricated edge dressed as a vendor statement.
    #
    # A row counts as a real link only when the table actually holds the device:
    # getDeviceData non-null AND fullName/storageKey non-empty. Everything else
    # is recorded as a fallback, explicitly NOT a name.
    def is_real(r: dict) -> bool:
        return bool(r.get("hasDeviceData")) and bool(r.get("fullName")) and bool(r.get("storageKey"))

    for r in rows:
        r["name_link"] = "RESOLVED" if is_real(r) else "SDK_FALLBACK_NOT_A_NAME"

    named = [r for r in rows if r["name_link"] == "RESOLVED"]
    unnamed = [r for r in rows if r["name_link"] != "RESOLVED"]
    boot = [r for r in named if r.get("isBootUpdateMode") is True]

    doc = {
        "_what": "MCHOSE identity table as the vendor's own CZ SDK reports it, TICKET-25",
        "_method": ("pure lookups on window.loadCZSharedData(); each row carries the "
                    "identity tuple and the name in one record, so no edge here rests "
                    "on similarity, vendor id, or family"),
        "_caveat": ("isBootUpdateMode is the VENDOR'S CLAIM about which product id is the "
                    "DFU interface, not a hardware observation. For God 60 it is "
                    "corroborated by observation: advertising 0x301a made the live app "
                    "open its firmware-update dialog instead of the configurator."),
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "_fallback_warning": (
            "getDeviceName never returns empty. For an identity outside the CZ table it "
            "returns the default 'Ace60' with empty fullName/storageKey. Those rows are "
            "marked SDK_FALLBACK_NOT_A_NAME and are NOT name links."
        ),
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "identities_asked": len(rows),
        "resolved": len(named),
        "sdk_fallback_not_a_name": len(unnamed),
        "distinct_products_resolved": len({r["deviceName"] for r in named}),
        "boot_mode_identities": len(boot),
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"resolved name links : {len(named)}/{len(rows)}  "
          f"({len({r['deviceName'] for r in named})} distinct products)")
    print(f"SDK fallback (not a name): {len(unnamed)}")
    print(f"boot-mode identities: {len(boot)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
