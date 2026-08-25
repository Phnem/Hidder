"""TICKET-25 helper: probe M HUB's live page for its connect control and its
in-memory CZ remote config.

Two things the static lane could not give us and the first oracle run showed
exist:

*   the connect control is not findable by the text a locale happens to render,
    so it has to be found structurally;
*   a second configuration layer, `cizhou` / `CZ_SHARED_DATA`, publishes
    `deviceList`, `HidIndexDeviceFilters`, `KEYBOARD_MODELS` and `czDeviceName`
    at runtime. Those are candidate carriers of the vid:pid <-> name edge that
    TICKET-23 left open, and they are fetched, not bundled.

Read-only. Clicks nothing, sends nothing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TARGET = "https://www.mchose.com.cn/#/home"

_DUMP_JS = r"""
() => {
  const out = {globals: [], clickables: [], czconfig: null};

  // Anything the CZ layer parked on window, by name shape rather than by a
  // guessed identifier.
  for (const k of Object.keys(window)) {
    if (/^(CZ|cz|__CZ)/.test(k) || /RemoteConfig|DeviceSDK|iframeBridge/i.test(k)) {
      let t = typeof window[k];
      out.globals.push({key: k, type: t});
    }
  }

  // Candidate connect controls: visible, clickable-ish, and either carrying a
  // connect-ish class or sitting alone in a small interactive box.
  const els = Array.from(document.querySelectorAll('button,[role=button],a,div,span'));
  for (const e of els) {
    const r = e.getBoundingClientRect();
    if (r.width < 40 || r.height < 16 || r.width > 900) continue;
    const cls = (e.className || '').toString();
    const txt = (e.innerText || '').trim().slice(0, 40);
    const interesting = /connect|btn|button/i.test(cls) || (txt && e.children.length === 0);
    if (!interesting) continue;
    out.clickables.push({tag: e.tagName, cls: cls.slice(0, 70), text: txt,
                         x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                         w: Math.round(r.width), h: Math.round(r.height)});
  }
  out.clickables = out.clickables.slice(0, 40);
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=25.0)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    captured: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        # The CZ layer announces its config through console.info with the object
        # as a second argument; capturing the ARGS rather than the rendered text
        # is what makes the payload readable instead of "[Object]".
        def on_console(msg):
            try:
                if "CZRemoteConfigList" not in msg.text:
                    return
                vals = []
                for a in msg.args:
                    try:
                        vals.append(a.json_value())
                    except Exception:  # noqa: BLE001
                        vals.append("<unserialisable>")
                captured.append({"text": msg.text[:120], "args": vals})
            except Exception:  # noqa: BLE001
                pass

        page.on("console", on_console)
        try:
            page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            print(f"[nav] {exc}")
        page.wait_for_timeout(int(args.seconds * 1000))

        dump = page.evaluate(_DUMP_JS)
        ctx.close()
        browser.close()

    doc = {"globals": dump["globals"], "clickables": dump["clickables"],
           "cz_remote_config": captured}
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"globals    : {[g['key'] for g in dump['globals']]}")
    print(f"clickables : {len(dump['clickables'])}")
    for c in dump["clickables"][:14]:
        print(f"   {c['tag']:<6} {c['w']:>4}x{c['h']:<3} ({c['x']},{c['y']}) cls={c['cls'][:44]!r} txt={c['text']!r}")
    print(f"cz config announcements: {len(captured)}")
    for c in captured:
        print(f"   {c['text'][:90]}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
