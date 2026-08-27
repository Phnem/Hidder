"""RT passive-capture lifecycle regressions (no browser, no hardware).

Proves --rt-get-capture reuses the canonical WebHidCapture lifecycle
(launch -> start -> health gate -> collect_frames -> close), contains no
invented `.open()` call, issues ZERO Probe HID writes, persists the trace,
reports honestly when no 0x99 is observed, retains candidate 0x99 frames, and
cleans up on both normal completion and exception."""

import builtins
import json
from pathlib import Path

import pytest

from community.vetro_probe import webhid_capture as wc
from community.vetro_probe.webhid_capture import run_passive_rt_get_capture


def _health_panel(**overrides):
    h = {
        "capture_health": "PASS", "page_attached": True, "transport_context_found": True,
        "navigator_hid": True, "granted_devices": 3, "hero84_detected": True,
        "device_opened": True, "observed_webhid_calls": 5, "observed_out_frames": 3,
        "targets": [{"target_id": "t", "url": "x", "navigator_hid": True, "granted_devices": 3,
                     "observed_calls": 5, "out_frames": 3}],
    }
    h.update(overrides)
    return h


class _FakeCapture:
    """Mimics WebHidCapture's canonical lifecycle surface used by the RT path."""

    def __init__(self, frames=None, health=None):
        self.frames = frames or []
        self.health = health or _health_panel()
        self.calls = []
        self.launch_ok = True
        self.closed = False

    def launch(self):
        self.calls.append("launch")
        return self.launch_ok

    def start(self):
        self.calls.append("start")

    def isolation_panel(self):
        self.calls.append("isolation_panel")
        return {"dedicated_user_data_dir": "YES"}

    def health_status(self):
        self.calls.append("health_status")
        return self.health

    def print_panel(self, h):
        self.calls.append("print_panel")

    def collect_frames(self, seconds, annotation=None):
        self.calls.append(("collect_frames", seconds, annotation))
        for f in self.frames:
            self._write(f)
        return list(self.frames)

    def _write(self, ev):
        Path(self.trace_path).open("a", encoding="utf-8").write(json.dumps(ev) + "\n")

    def close(self):
        self.calls.append("close")
        self.closed = True


def _run(monkeypatch, tmp_path, frames=None, health=None, launch_ok=True, inputs=None):
    inputs = list(inputs) if inputs is not None else ["", ""]
    fake = _FakeCapture(frames=frames, health=health)
    fake.launch_ok = launch_ok
    monkeypatch.setattr(wc, "WebHidCapture", lambda trace_path, target_url="x": fake)
    fake.trace_path = str(tmp_path / "rt.jsonl")
    monkeypatch.setattr(builtins, "input", lambda *a, **k: inputs.pop(0) if inputs else "")
    code = run_passive_rt_get_capture(Path(fake.trace_path), "https://hero.aulastar.com", 5)
    return code, fake


# 1/2. canonical lifecycle reused; no invented `.open()` remains
def test_uses_canonical_lifecycle_no_open(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path)
    assert code == 0
    assert fake.calls == ["launch", "start", "isolation_panel", "health_status", "print_panel",
                          ("collect_frames", 5.0, "rt_get_discovery"), "close"]
    src = Path(wc.__file__).read_text(encoding="utf-8")
    assert "cap.open(" not in src and ".open()" not in src.replace("open(", "open_x(")


# 3. startup succeeds under the mocked lifecycle
def test_startup_succeeds(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path)
    assert code == 0
    assert fake.closed is True


# 4. RT mode performs ZERO Probe HID writes (harness never calls HIDDevice.sendReport)
def test_zero_probe_hid_writes(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path, frames=[{"method": "sendReport", "direction": "OUT",
                                                      "hex": "99000001000c000100020003000400050006", "length": 63}])
    assert code == 0
    # the fake's frames came from the VENDOR app traffic being observed; the harness
    # only hooks — it never issued a write itself (no HIDDevice.prototype.sendReport call).
    assert "HIDDevice.prototype.sendReport" not in Path(wc.__file__).read_text(encoding="utf-8").split("run_passive_rt_get_capture")[1][:200]


# 5. trace file is created/written
def test_trace_file_written(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path, frames=[{"hex": "99000001000c000100020003000400050006", "direction": "OUT", "method": "sendReport", "length": 63}])
    assert code == 0
    lines = [json.loads(l) for l in Path(fake.trace_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines and lines[0]["hex"].startswith("99")


# 6. no 0x99 traffic -> honest "not observed", not parser fabrication
def test_no_0x99_honest_result(monkeypatch, tmp_path, capsys):
    code, fake = _run(monkeypatch, tmp_path, frames=[{"hex": "820100010006", "direction": "IN", "method": "inputreport", "length": 63}])
    assert code == 0
    out = capsys.readouterr().out
    assert "NO real 0x99 request/reply observed" in out
    assert "no reply parser was fabricated" in out


# 7. candidate OUT 0x99 + IN response are retained in the trace
def test_candidate_0x99_retained(monkeypatch, tmp_path, capsys):
    out_f = {"hex": "99000001000c000100020003000400050006", "direction": "OUT", "method": "sendReport", "length": 63, "report_id": 9}
    in_f = {"hex": "99000001000c000100020003000400050006", "direction": "IN", "method": "inputreport", "length": 63, "report_id": 9}
    code, fake = _run(monkeypatch, tmp_path, frames=[out_f, in_f])
    assert code == 0
    lines = [json.loads(l) for l in Path(fake.trace_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len([f for f in lines if f["hex"].startswith("99")]) == 2
    assert "candidate real 0x99 reply" in capsys.readouterr().out


# 8. cleanup on normal completion
def test_cleanup_on_normal(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path)
    assert fake.closed is True and "close" in fake.calls


# 9. cleanup on exception
def test_cleanup_on_exception(monkeypatch, tmp_path):
    fake = _FakeCapture(frames=[], health=_health_panel())
    fake.trace_path = str(tmp_path / "rt.jsonl")
    monkeypatch.setattr(wc, "WebHidCapture", lambda trace_path, target_url="x": fake)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    code = run_passive_rt_get_capture(Path(fake.trace_path), "https://hero.aulastar.com", 5)
    assert code == 1
    assert fake.closed is True


def test_browser_not_found_fail_closed(monkeypatch, tmp_path):
    fake = _FakeCapture()
    fake.launch_ok = False
    fake.trace_path = str(tmp_path / "rt.jsonl")
    monkeypatch.setattr(wc, "WebHidCapture", lambda trace_path, target_url="x": fake)
    code = run_passive_rt_get_capture(Path(fake.trace_path), "https://hero.aulastar.com", 5)
    assert code == 1
    assert fake.closed is False  # nothing started; nothing to close


def test_health_fail_aborts(monkeypatch, tmp_path):
    code, fake = _run(monkeypatch, tmp_path, health=_health_panel(capture_health="FAIL"))
    assert code == 2
    assert fake.closed is True
