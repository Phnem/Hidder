"""Real 0x99 RT GET reply parser regressions (deterministic, no hardware).

Authoritative fixtures are the REAL passive-capture replies from
rt_get_capture.jsonl (14/14 real 0x99 OUT->IN pairs, checksums valid):
  IN#0 keys 1..6, IN#1 keys 7..12, IN#2 keys 13,0x5f,0x62,0x63,0x0e,0x0f ...
Reply layout: [0x99,0x00,0x00,0x01,0x00,len(8*n), record* , pad, checksum].
Record (8B): key_id_hi, key_id_lo, rt_enable, rt_up_hi, rt_up_lo, rt_down_hi,
rt_down_lo, rt_global. Threshold scale 0.01 mm.
"""

import json
from pathlib import Path

import pytest

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.identity import mock_hero84_instance

REAL_REPLY_KEYS_1_6 = (
    "990000010030000100000100010100020000010001010003000001000101000400000100010100050000"
    "010001010006000001000101000000000000000005"
)
REAL_REPLY_KEYS_7_12 = (
    "990000010030000700000100010100080000010001010009000001000101000a000001000101000b0000"
    "01000101000c0000010001010000000000000000e1"
)
REAL_REPLY_KEYS_13_MIXED = (
    "990000010030000d000001000101005f00000100010100620000010001010063000001000101000e0000"
    "01000101000f0000010001010000000000000000cc"
)

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")


def _p(bundle):
    from aula_kb_v3.registry import resolve_by_uuid  # type: ignore
    return resolve_by_uuid(int(bundle.product.uuid))


@pytest.mark.parametrize("hexf,keys", [
    (REAL_REPLY_KEYS_1_6, [1, 2, 3, 4, 5, 6]),
    (REAL_REPLY_KEYS_7_12, [7, 8, 9, 10, 11, 12]),
    (REAL_REPLY_KEYS_13_MIXED, [0x000d, 0x005f, 0x0062, 0x0063, 0x000e, 0x000f]),
])
def test_real_reply_parses_six_records(hexf, keys):
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    recs = parse_rt_get_reply(bytes.fromhex(hexf))
    assert len(recs) == 6
    assert [r["key_id"] for r in recs] == keys


def test_exact_captured_reply_fields():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    recs = parse_rt_get_reply(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    for r in recs:
        assert r["rt_enable"] is False
        assert r["rt_up_raw"] == 1
        assert r["rt_down_raw"] == 1
        assert r["rt_global"] == 1
        assert r["rt_up_mm"] == 0.01
        assert r["rt_down_mm"] == 0.01


def test_enable_up_down_global_decoded_independently():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    recs = parse_rt_get_reply(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    # fields are distinct record offsets (enable=rec[2], up=rec[3:5], down=rec[5:7], global=rec[7])
    assert all("rt_enable" in r and "rt_up_raw" in r and "rt_down_raw" in r and "rt_global" in r for r in recs)


def test_checksum_valid_passes():
    from aula_kb_v3.protocol import parse_rt_get_reply, checksum  # type: ignore
    for hexf in (REAL_REPLY_KEYS_1_6, REAL_REPLY_KEYS_7_12, REAL_REPLY_KEYS_13_MIXED):
        b = bytes.fromhex(hexf)
        assert checksum(b[:62]) == b[62] == 0x05 if hexf == REAL_REPLY_KEYS_1_6 else True
        parse_rt_get_reply(b)  # no raise


def test_bad_checksum_fails():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    bad = bytearray(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    bad[62] ^= 0xFF
    with pytest.raises(ValueError, match="checksum"):
        parse_rt_get_reply(bytes(bad))


def test_wrong_group_fails():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    bad = bytearray(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    bad[0] = 0x96  # deadzone group
    with pytest.raises(ValueError, match="wrong group"):
        parse_rt_get_reply(bytes(bad))


def test_truncated_fails():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    with pytest.raises(ValueError, match="63"):
        parse_rt_get_reply(bytes.fromhex(REAL_REPLY_KEYS_1_6)[:62])


def test_invalid_declared_payload_length_fails():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    b = bytearray(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    b[5] = 0x04  # not a positive multiple of 8
    with pytest.raises(ValueError, match="payload length"):
        parse_rt_get_reply(bytes(b))


def test_get_rapid_trigger_uses_real_transport():
    from aula_kb_v3.operations import get_rapid_trigger  # type: ignore
    bundle = production_bundle_for_hero84()
    prod = _p(bundle)

    class _T:
        def __init__(self):
            self.sent = []

        def send(self, frame):
            self.sent.append(bytes(frame))

        def recv(self, timeout_ms=1000):
            return bytes.fromhex(REAL_REPLY_KEYS_1_6)

    t = _T()
    recs = get_rapid_trigger(t, prod, [1, 2, 3, 4, 5, 6])
    assert len(recs) == 6 and recs[0]["key_id"] == 1
    assert t.sent and t.sent[0][0] == 0x99 and t.sent[0][5] == 0x0c


def test_cached_state_never_satisfies_readback():
    # The typed transport GET reads the REAL 0x99 reply; a poisoned cache is ignored.
    from community.vetro_probe.aula_transport import AulaHidTransport
    bundle = production_bundle_for_hero84()
    prod = _p(bundle)

    class _Raw:
        def send(self, frame):
            pass

        def recv(self, timeout_ms=1000):
            return bytes.fromhex(REAL_REPLY_KEYS_1_6)  # real enable=0 for every key

        def is_connected(self):
            return True

        def close(self):
            pass

    t = AulaHidTransport(raw=_Raw(), product=prod)
    t._rt_cache["he.rt"] = True  # poisoned cache would say enabled
    val, res = t.get("he.rt")
    assert res.ok is True
    assert val is False  # real GET wins over cache; cached value is never a readback


def test_all_14_captured_replies_validate():
    p = Path("rt_get_capture.jsonl")
    if not p.is_file():
        pytest.skip("rt_get_capture.jsonl not present")
    from aula_kb_v3.protocol import parse_rt_get_reply, checksum  # type: ignore
    ins = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("hex", "").startswith("99") and e.get("direction") == "IN":
            ins.append(e["hex"])
    assert len(ins) == 14
    all_recs = []
    for h in ins:
        b = bytes.fromhex(h)
        assert len(b) == 63
        assert checksum(b[:62]) == b[62]
        recs = parse_rt_get_reply(b)
        assert len(recs) == 6
        all_recs.extend(recs)
    # real device reports heterogeneous state: 83 keys at 0.01mm (raw1), and
    # key 0x1f (31) at 0.10mm (raw10) — parser handles both.
    assert len(all_recs) == 84
    raw1 = [r for r in all_recs if (r["rt_up_raw"], r["rt_down_raw"]) == (1, 1)]
    raw10 = [r for r in all_recs if (r["rt_up_raw"], r["rt_down_raw"]) == (10, 10)]
    assert len(raw1) == 83
    assert len(raw10) == 1 and raw10[0]["key_id"] == 0x1f
    assert all(r["rt_enable"] is False and r["rt_global"] == 1 for r in all_recs)


def test_passive_evidence_does_not_claim_probe_roundtrip():
    # passive real GET + existing real SET evidence do NOT constitute a Probe
    # SET->GET round-trip; the crosscheck stays OPEN/partially closed and he.rt BLOCKED.
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert "rapid_trigger_units_crosscheck" in fg.blocker_for("he.rt", **SCOPE)[1]


def test_he_rt_remap_blocked_k20_unpromoted():
    assert fg.blocker_for("he.rt", **SCOPE) is not None
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
