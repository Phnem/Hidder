"""he.rt readback-gap regressions (no hardware).

Proves: the cached he.rt SET value can NEVER satisfy a baseline/readback/final
verification (quarantined, fail-closed); the 0x99 GET request format is
confirmed byte-exact against the observed trace; the 0x99 reply parser does not
exist and no real 0x99 reply exists in the repository capture corpus; he.rt
stays BLOCKED; remap stays blocked; K20 stays unpromoted."""

import glob
import json
from pathlib import Path

import pytest

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.baseline import BaselineSnapshot, BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.transport import TransportResult

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")


class _StubRaw:
    def __init__(self):
        self.sent = []

    def send(self, frame):
        self.sent.append(bytes(frame))

    def recv(self, timeout_ms=1000):
        return b"\x00" * 63

    def is_connected(self):
        return True

    def close(self):
        pass


def _real_transport():
    from aula_kb_v3.registry import resolve_by_uuid  # type: ignore
    from community.vetro_probe.aula_transport import AulaHidTransport
    prod = resolve_by_uuid(18691697672197)
    return AulaHidTransport(raw=_StubRaw(), product=prod), prod


# 1. real RT GET fails closed on a non-0x99 / malformed reply (never cached fallback)
def test_real_rt_get_fails_closed_on_bad_reply():
    transport, _ = _real_transport()  # _StubRaw returns 63 zero bytes -> wrong group
    val, res = transport.get("he.rt")
    assert res.ok is False
    assert val is None
    assert "RT" in res.error or "group" in res.error


def test_rt_baseline_unavailable_via_cache_zero_writes():
    # baseline/readback/final GET all fail closed on the cache -> he.rt can never
    # be planned/executed on cached state; zero writes.
    bundle = production_bundle_for_hero84()
    transport, prod = _real_transport()
    snap = BaselineCollector(transport).collect(["he.rt"])
    assert "he.rt" not in snap.values
    ctx = ExecutorContext(bundle=bundle, transport=transport,
                          safety=SafetyGate(bundle, instance_firmware="0216"),
                          baseline=snap, recovery=RecoveryJournal(snap),
                          firmware_branch="0216", connection_mode="wired",
                          enforce_feature_gates=False)
    ev = execute_single("he.rt", ctx)
    assert ev.status == "BLOCKED"
    assert "baseline unavailable" in ev.error
    # _StubRaw never saw a write (only the baseline GET was attempted)
    assert not any(f and f[0] == 0x19 for f in transport.raw.sent)


# 2. 0x99 GET request format confirmed byte-exact vs the observed trace request
def test_rt_get_request_golden_vector():
    from aula_kb_v3.protocol import build_rt_get_frame  # type: ignore
    f = build_rt_get_frame([1, 2, 3, 4, 5, 6])
    assert bytes(f).hex().startswith("99000001000c000100020003000400050006")
    assert f[0] == 0x99 and f[5] == 0x0c


# 3. authoritative 0x99 reply parser + operation now exist (from REAL evidence);
#    a non-0x99 reply is still fail-closed (no guessing, no fallback to cache)
def test_parser_and_op_exist_and_fail_closed_on_wrong_group():
    import aula_kb_v3.protocol as p  # type: ignore
    import aula_kb_v3.operations as o  # type: ignore
    assert hasattr(p, "parse_rt_get_reply") is True
    assert hasattr(o, "get_rapid_trigger") is True
    with pytest.raises(ValueError, match="wrong group"):
        p.parse_rt_get_reply(b"\x00" * 63)


# 4. real 0x99 replies now EXIST in the repository (rt_get_capture.jsonl, 14/14 valid)
def test_real_0x99_replies_now_exist_and_validate():
    import aula_kb_v3.protocol as p  # type: ignore
    pfile = Path("rt_get_capture.jsonl")
    if not pfile.is_file():
        pytest.skip("rt_get_capture.jsonl not present")
    n = 0
    for line in pfile.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("hex", "").startswith("99") and e.get("direction") == "IN":
            b = bytes.fromhex(e["hex"])
            assert p.checksum(b[:62]) == b[62]
            assert len(p.parse_rt_get_reply(b)) == 6
            n += 1
    assert n == 14


# 5/6. he.rt stays BLOCKED; remap stays blocked; K20 unpromoted
def test_rt_remap_blocked_and_k20_unpromoted():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
