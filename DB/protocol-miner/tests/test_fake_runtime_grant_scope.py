"""Regression test: a WebHID grant is per-ORIGIN, not per-JS-realm.

Real WebHID records a grant against the origin, so a same-origin iframe that
never called `requestDevice` itself still sees the device in `getDevices()`.
The fake runtime used to keep grant state in a module-scoped boolean, which
gets a fresh copy in every frame's realm, so an iframe saw an empty list
forever.

That is not a cosmetic difference. MCHOSE serves its keyboard configurator as a
separate app under /cizhou/ mounted in an iframe; with realm-scoped state it
polled `getDevices()` about ten times a second and then threw
"No HID devices found". Read at face value that says "the vendor app does not
support this device" -- a wrong FINDING manufactured by the harness, which is
strictly worse than a crash, because a crash gets fixed and a finding gets
written down.

The test drives the shape of the real failure: parent grants, iframe never
does, iframe must see the device.
"""

import json
from pathlib import Path

import pytest

from miner.dynamic.playwright_webhid import PlaywrightUnavailableError

_RUNTIME_JS = (
    Path(__file__).parent.parent / "miner" / "dynamic" / "fake_browser" / "runtime.js"
).read_text(encoding="utf-8")

_CHILD_HTML = """<!DOCTYPE html>
<html><body><script>
  // The child NEVER calls requestDevice. It only asks what it has been granted,
  // which is exactly what the vendor's iframe app does.
  window.__childSees = null;
  window.__childAsk = async function () {
    const d = await navigator.hid.getDevices();
    window.__childSees = d.map(x => x.productId);
    return window.__childSees;
  };
</script></body></html>
"""

_PARENT_HTML = """<!DOCTYPE html>
<html><body>
<iframe id="kid" src="child.html"></iframe>
<script>
  window.__parentGrant = async function () {
    const d = await navigator.hid.requestDevice({ filters: [] });
    return d.map(x => x.productId);
  };
</script>
</body></html>
"""


def _run(tmp_path: Path):
    from playwright.sync_api import sync_playwright

    # Served from a routed https origin rather than file://. Chromium gives every
    # file:// document an opaque origin, so two file:// documents do not share
    # storage and the test would fail for a reason that has nothing to do with
    # what it is testing. A single synthetic origin reproduces the real
    # parent/iframe relationship.
    pages = {"/parent.html": _PARENT_HTML, "/child.html": _CHILD_HTML}

    cfg = (
        "window.__protocolMinerDeviceConfig = "
        + json.dumps({"vendorId": 0x1234, "productId": 0x5678, "productName": "Grant Scope Device"})
        + ";\nwindow.__protocolMinerCannedResponses = {};\n"
    )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            context.expose_binding("__protocolMinerBridgeRespond", lambda s, r: {"ack": True})
            context.add_init_script(cfg + "\n" + _RUNTIME_JS)

            def serve(route):
                path = route.request.url.split("://", 1)[1].split("/", 1)[1]
                body = pages.get("/" + path)
                if body is None:
                    route.fulfill(status=404, body="")
                else:
                    route.fulfill(status=200, content_type="text/html", body=body)

            context.route("https://harness.invalid/**", serve)
            page = context.new_page()
            page.goto("https://harness.invalid/parent.html", wait_until="load")

            # Before the grant the child must see nothing: the fresh-pairing
            # default is the behaviour that keeps vendor auto-reconnect paths
            # from being triggered, and it must not be lost to this fix.
            before = page.frames[1].evaluate("() => window.__childAsk()")
            granted = page.evaluate("() => window.__parentGrant()")
            after = page.frames[1].evaluate("() => window.__childAsk()")
            browser.close()
            return before, granted, after
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc) or "playwright" in str(exc).lower():
            raise PlaywrightUnavailableError(str(exc)) from exc
        raise


def test_grant_scope_matches_real_webhid(tmp_path: Path) -> None:
    try:
        before, granted, after = _run(tmp_path)
    except PlaywrightUnavailableError:
        pytest.skip("Playwright Chromium unavailable in environment")

    assert before == [], (
        "an ungranted frame must see an empty device list; returning a device "
        f"unconditionally makes sites take their auto-reconnect path, got {before!r}"
    )
    assert granted == [0x5678], f"requestDevice should hand back the fake device, got {granted!r}"
    assert after == [0x5678], (
        "a same-origin frame that never called requestDevice must still see the granted "
        f"device, because WebHID grants are per-origin, not per-realm; got {after!r}"
    )
