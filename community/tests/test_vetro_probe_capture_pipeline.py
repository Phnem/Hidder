"""Pipeline regression: Runtime.bindingCalled -> _binding -> read_events -> collect_frames.

Catches DEFECT 1 (read_events always returned [] because _binding never fed `out`),
DEFECT 2 (schema), DEFECT 4 (duplicate), DEFECT 3 (iframe coverage). Synthetic CDP WS, no browser.
"""

import json
from pathlib import Path

import community.vetro_probe.webhid_capture as wc


class FakeWS:
    def __init__(self, msgs=None):
        self.msgs = list(msgs or [])

    def send_json(self, msg):  # noqa: D401
        pass

    def recv_json(self, timeout=0.5):
        if self.msgs:
            return self.msgs.pop(0)
        return None

    def close(self):
        pass


def _binding_msg(payload: dict):
    return {"method": "Runtime.bindingCalled",
            "params": {"name": "__peripheral_webhid_event__",
                       "payload": json.dumps(payload)}}


def _raw(api="sendReport", direction="out", report_id=9, hex_="06000001", seq=1):
    return {"api": api, "direction": direction, "report_id": report_id,
            "length": len(hex_) // 2, "bytes_hex": hex_,
            "vendor_id": 14126, "product_id": 4158, "product_name": "HERO 84 HE",
            "origin": "https://hero.aulastar.com", "frame": "top", "seq": seq,
            "timestamp": 123.0}


def _pt(msgs, target_type="page"):
    pt = wc.PageTarget.__new__(wc.PageTarget)
    pt.id = "t1"
    pt.url = "https://hero.aulastar.com/"
    pt.ws = FakeWS(msgs)
    pt.bind_name = "__peripheral_webhid_event__"
    pt._cmd_id = 0
    pt.health = {}
    pt._last_seq = 0
    pt.target_type = target_type
    return pt


def test_read_events_returns_parsed_binding_event():
    pt = _pt([_binding_msg(_raw())])
    evs = pt.read_events()
    assert len(evs) == 1
    assert evs[0]["method"] == "sendReport"
    assert evs[0]["direction"] == "OUT"
    assert evs[0]["hex"] == "06000001"
    # also available to health accounting
    assert len(pt.health["events"]) == 1


def test_one_binding_event_one_canonical_event():
    pt = _pt([_binding_msg(_raw())])
    evs = pt.read_events()
    assert len(evs) == 1
    for key in ("timestamp", "method", "direction", "report_id", "length", "hex",
                "vendor_id", "product_id", "product_name", "origin", "frame",
                "target_id", "target_url", "target_type"):
        assert key in evs[0]


def test_collect_frames_receives_event():
    pt = _pt([_binding_msg(_raw())])
    cap = wc.WebHidCapture.__new__(wc.WebHidCapture)
    cap.targets = {"t1": pt}
    cap.trace_path = Path("_pipeline_trace.jsonl")
    frames = cap.collect_frames(0.05, annotation="a1")
    assert len(frames) == 1
    assert frames[0]["annotation"] == "a1"
    assert frames[0]["method"] == "sendReport"
    Path("_pipeline_trace.jsonl").unlink(missing_ok=True)


def test_normalization_aliases():
    pt = _pt([_binding_msg(_raw())])
    ev = pt.read_events()[0]
    assert ev["method"] == "sendReport"  # api -> method
    assert ev["hex"] == "06000001"       # bytes_hex -> hex


def test_duplicate_seq_dropped():
    # same seq twice (relay + direct) -> exactly one event
    pt = _pt([_binding_msg(_raw(seq=5)), _binding_msg(_raw(seq=5))])
    evs = pt.read_events()
    assert len(evs) == 1
    assert len(pt.health["events"]) == 1


def test_health_then_collect_on_new_binding(tmp_path):
    # OLD FAILURE: health observed>0 but subsequent collect_frames returned 0
    pt = _pt([_binding_msg(_raw(seq=1))])
    first = pt.read_events()  # health now sees 1 event
    assert len(pt.health["events"]) == 1
    # new binding arrives after input() wait
    pt.ws = FakeWS([_binding_msg(_raw(seq=2))])
    cap = wc.WebHidCapture.__new__(wc.WebHidCapture)
    cap.targets = {"t1": pt}
    cap.trace_path = tmp_path / "t.jsonl"
    frames = cap.collect_frames(0.05, annotation="smoke")
    assert len(frames) == 1  # collect_frames now returns >0


def test_smoke_raw_frames_gt_zero_with_synthetic_ws():
    pt = _pt([_binding_msg(_raw(seq=1)), _binding_msg(_raw(seq=2))])
    cap = wc.WebHidCapture.__new__(wc.WebHidCapture)
    cap.targets = {"t1": pt}
    cap.trace_path = Path("_smoke_trace.jsonl")
    frames = cap.collect_frames(0.05, annotation="brightness_1")
    assert len(frames) == 2
    out = [f for f in frames if f["direction"] == "OUT"]
    assert len(out) == 2
    Path("_smoke_trace.jsonl").unlink(missing_ok=True)


def test_iframe_target_not_discarded(monkeypatch):
    class FakeResp:
        def __init__(self, data):
            self._data = data
        def read(self):
            return self._data.encode()

    def fake_urlopen(url, timeout=5):
        body = json.dumps([
            {"id": "p", "type": "page", "url": "https://hero.aulastar.com/", "webSocketDebuggerUrl": "ws://x"},
            {"id": "if", "type": "iframe", "url": "https://cfg.hero.aulastar.com/", "webSocketDebuggerUrl": "ws://y"},
            {"id": "sw", "type": "service_worker", "url": "sw.js"},  # no debugger url -> excluded
            {"id": "bg", "type": "background_page", "url": "bg.html", "webSocketDebuggerUrl": "ws://z"},
        ])
        return FakeResp(body)

    import urllib.request as u
    monkeypatch.setattr(u, "urlopen", fake_urlopen)
    cap = wc.WebHidCapture.__new__(wc.WebHidCapture)
    cap.port = 1
    targets = cap._fetch_targets()
    ids = {t["id"] for t in targets}
    assert "p" in ids and "if" in ids
    assert "sw" not in ids  # service worker excluded
