"""Capture-health gate + injection coverage tests (deterministic, no browser)."""

from pathlib import Path

from community.vetro_probe.webhid_capture import compute_health, CAPTURE_HEALTH_SCRIPT


def _pt(hid=True, granted=0, events=None, out=0, url="https://hero.aulastar.com/lighting"):
    return {"url": url, "target_id": "x", "navigator_hid": hid, "granted_devices": granted,
            "observed_calls": len(events or []), "out_frames": out, "events": events or []}


def test_health_pass_with_observed_calls():
    ev = [{"api": "sendReport", "direction": "out", "report_id": 9, "bytes_hex": "06000001"}]
    h = compute_health([_pt(events=ev, out=1)])
    assert h["capture_health"] == "PASS"
    assert h["observed_webhid_calls"] == 1


def test_health_pass_with_granted_device_only():
    # device granted but no frames yet -> PASS (observation context proven), sweep still needs frames
    h = compute_health([_pt(granted=1)])
    assert h["capture_health"] == "PASS"
    assert h["granted_devices"] == 1


def test_health_fail_when_zero_activity():
    h = compute_health([_pt(hid=True, granted=0, events=[])])
    assert h["capture_health"] == "FAIL"
    assert h["hero84_detected"] is False


def test_health_fail_when_no_target():
    h = compute_health([])
    assert h["capture_health"] == "FAIL"
    assert h["page_attached"] is False


def test_hero84_detection_and_open():
    ev = [{"method": "open", "direction": "OUT", "report_id": -1},
          {"method": "sendFeatureReport", "direction": "OUT", "vendor_id": 14126, "product_id": 4158, "hex": "84010001"}]
    h = compute_health([_pt(events=ev, out=2)])
    assert h["capture_health"] == "PASS"
    assert h["hero84_detected"] is True
    assert h["device_opened"] is True


def test_injection_script_hooks_every_method():
    for token in ("sendReport", "sendFeatureReport", "receiveFeatureReport", "open", "close",
                  "inputreport", "requestDevice", "getDevices", "postMessage"):
        assert token in CAPTURE_HEALTH_SCRIPT


def test_injection_runs_in_main_world():
    # addScriptToEvaluateOnNewDocument + Runtime.addBinding = same main world as the app
    assert "Page.addScriptToEvaluateOnNewDocument" in _source("PageTarget", "_init")
    assert "Runtime.addBinding" in _source("PageTarget", "_init")


def _source(cls: str, meth: str) -> str:
    import inspect
    import community.vetro_probe.webhid_capture as wc
    return inspect.getsource(getattr(getattr(wc, cls), meth))
