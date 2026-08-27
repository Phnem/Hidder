"""he.rt readback-gap regressions (no hardware).

Proves: the cached he.rt SET value can NEVER satisfy a baseline/readback/final
verification (quarantined, fail-closed); the 0x99 GET request format is
confirmed byte-exact against the observed trace; the 0x99 reply parser does not
exist and no real 0x99 reply exists in the repository capture corpus; he.rt
stays BLOCKED; remap stays blocked; K20 stays unpromoted."""

import glob
import json

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


# 1. cached RT state cannot satisfy physical readback (quarantined, fail-closed)
def test_cached_rt_cannot_satisfy_readback():
    transport, _ = _real_transport()
    val, res = transport.get("he.rt")
    assert res.ok is False
    assert "readback NOT implemented" in res.error
    assert val is None


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


# 3. absence of an authoritative 0x99 reply parser is fail-closed (no guessing)
def test_no_parser_no_guessing():
    import aula_kb_v3.protocol as p  # type: ignore
    import aula_kb_v3.operations as o  # type: ignore
    assert hasattr(p, "parse_rt_get_reply") is False
    assert hasattr(o, "get_rapid_trigger") is False


# 4. no real 0x99 reply exists anywhere in the repository capture corpus
def test_no_real_0x99_reply_in_capture_corpus():
    real_replies = []
    for path in glob.glob("DB/reports/oracle/aula_web/HERO_84_HE/**/*.jsonl", recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("transport") != "webhid_real":
                        continue
                    if e.get("method") == "inputreport" and e.get("bytes_hex", "").startswith("99"):
                        real_replies.append((path, e.get("seq"), e.get("bytes_hex", "")[:40]))
        except OSError:
            continue
    assert real_replies == [], f"unexpected real 0x99 replies found: {real_replies[:3]}"


# 5/6. he.rt stays BLOCKED; remap stays blocked; K20 unpromoted
def test_rt_remap_blocked_and_k20_unpromoted():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
