"""TICKET-23 step 1 (second half): observe what M HUB Web actually fetches.

`mchose_acquire.py` computes the STATIC closure of the module graph. This tool
computes the OBSERVED set: it drives a real browser at the real site and records
every network request, so that chunks whose path is assembled at runtime, and
remote configuration fetched from the config centre, both show up.

Neither set is trusted alone. TICKET-23's completeness criterion is that the two
are compared:

    observed - static   = artifacts a static walk can never find (runtime paths,
                          config-centre JSON, CDN assets)
    static - observed   = artifacts shipped but not exercised by this session
                          (fine, but they are what a deeper drive-through, or a
                          connected device, would reach)

Nothing here connects a device. The app is expected to stop at its connect gate;
what happens past that gate is TICKET-25's job, and doing it here would smuggle
oracle work into an acquisition ticket.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from urllib.parse import urlparse

TARGET = "https://www.mchose.com.cn/#/home"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=45.0, help="observation window")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    seen: list[dict] = []
    console: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def on_response(resp):
            try:
                seen.append(
                    {
                        "url": resp.url,
                        "status": resp.status,
                        "type": resp.request.resource_type,
                        "method": resp.request.method,
                    }
                )
            except Exception:  # noqa: BLE001 - a torn-down response is not a finding
                pass

        page.on("response", on_response)
        page.on(
            "console",
            lambda m: console.append(f"{m.type}: {m.text[:300]}"),
        )

        try:
            page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            print(f"[nav] goto raised ({exc}); continuing to observe anyway")

        # Let lazy routes settle, then walk whatever top-level navigation the
        # app exposes, so route-level chunks load. Structural, not coordinate
        # based: click things that look like nav entries and come back.
        page.wait_for_timeout(int(args.seconds * 1000 / 3))
        try:
            hrefs = page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href^=\"#/\"]'))"
                ".map(a => a.getAttribute('href')).slice(0, 25)"
            )
        except Exception:  # noqa: BLE001
            hrefs = []
        print(f"[nav] in-app routes discovered: {hrefs}")
        for h in hrefs:
            try:
                page.goto(f"https://www.mchose.com.cn/{h}", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1200)
            except Exception:  # noqa: BLE001
                pass

        page.wait_for_timeout(int(args.seconds * 1000 / 3))
        body = ""
        try:
            body = page.evaluate("() => document.body.innerText")[:3000]
        except Exception:  # noqa: BLE001
            pass
        ctx.close()
        browser.close()

    uniq: dict[str, dict] = {}
    for r in seen:
        uniq.setdefault(r["url"], r)

    by_host: dict[str, int] = {}
    for u in uniq:
        h = urlparse(u).netloc
        by_host[h] = by_host.get(h, 0) + 1

    doc = {
        "_what": "MCHOSE M HUB Web observed network set, TICKET-23",
        "_note": "no device was connected; the app stops at its own connect gate",
        "target": TARGET,
        "observed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "request_count": len(uniq),
        "by_host": dict(sorted(by_host.items(), key=lambda kv: -kv[1])),
        "requests": sorted(uniq.values(), key=lambda r: r["url"]),
        "body_snapshot": body,
        "console_head": console[:60],
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"observed {len(uniq)} distinct URLs")
    for h, n in doc["by_host"].items():
        print(f"  {h:<32} {n}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
