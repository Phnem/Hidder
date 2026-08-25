"""Regression for HERO84 firmware identity: 16 02 LE -> 0216, not 1602.

Evidence: DB/reports/.../all_traces.jsonl seq 69-70
  HOST 820200... (0x82 sub2)
  DEVICE 8202000100011602... payload 16 02 little-endian -> 0x0216 -> "0216"
Vendor JS: fetch_firmware_version -> a[0]=0x16, a[1]=0x02 -> o*256+i (LE)
Single source: community/vetro_probe/firmware_identity.py
"""

from community.vetro_probe.firmware_identity import (
    HERO84_FIRMWARE_BRANCH,
    HERO84_FIRMWARE_RAW_LE,
    normalize_firmware_display,
    parse_firmware_reply,
    build_firmware_frame,
)


def test_firmware_single_source_is_0216():
    assert HERO84_FIRMWARE_BRANCH == "0216"
    assert HERO84_FIRMWARE_RAW_LE == bytes([0x16, 0x02])


def test_normalize_little_endian_not_big_endian():
    # Real fixture: payload 16 02 should be 0216, not 1602
    assert normalize_firmware_display(bytes([0x16, 0x02])) == "0216"
    assert normalize_firmware_display(bytes([0x02, 0x16])) == "1602"
    # Ensure we didn't swap
    assert normalize_firmware_display(HERO84_FIRMWARE_RAW_LE) == "0216"
    assert normalize_firmware_display(HERO84_FIRMWARE_RAW_LE) != "1602"


def test_parse_firmware_reply_real_fixture():
    # Build a fake 63B reply as device would: header + payload 16 02 at offset 6
    # Use build_firmware_frame to get request, then simulate reply as device
    # For unit test, craft reply manually: group 0x82 sub2 at 0, payload len 2 at 5, data 16 02 at 6
    reply = bytearray(63)
    reply[0] = 0x82
    reply[1] = 0x02
    reply[5] = 2
    reply[6] = 0x16
    reply[7] = 0x02
    assert parse_firmware_reply(bytes(reply)) == "0216"
    # Big-endian mis-decode would give 1602, which must not happen
    assert parse_firmware_reply(bytes(reply)) != "1602"


def test_parse_firmware_reply_physical_fixture_declared_len_1_still_0216():
    # Physical HERO84 fixture: reply[5]==0x01 but bytes 6:8 == 16 02 -> must still be 0216
    # Old parser returned "unknown" because it gated on n<2 — this is the real-hardware defect
    reply = bytearray(63)
    reply[0] = 0x82
    reply[1] = 0x02
    reply[5] = 1  # declared length 1 as in real capture
    reply[6] = 0x16
    reply[7] = 0x02
    assert parse_firmware_reply(bytes(reply)) == "0216"
    # Also check 64B report-id form (9 + 63B)
    reply64 = bytes([9]) + bytes(reply)
    assert parse_firmware_reply(reply64) == "0216"


def test_parse_firmware_reply_malformed_fail_closed():
    # Wrong group
    r = bytearray(63); r[0]=0x83; r[1]=0x02; r[5]=2; r[6]=0x16; r[7]=0x02
    assert parse_firmware_reply(bytes(r)) == "unknown"
    # Wrong sub
    r = bytearray(63); r[0]=0x82; r[1]=0x03; r[5]=2; r[6]=0x16; r[7]=0x02
    assert parse_firmware_reply(bytes(r)) == "unknown"
    # Short frame
    assert parse_firmware_reply(bytes([0x82, 0x02])) == "unknown"
    # 0000 payload -> unknown (not valid firmware)
    r = bytearray(63); r[0]=0x82; r[1]=0x02; r[5]=2; r[6]=0x00; r[7]=0x00
    assert parse_firmware_reply(bytes(r)) == "unknown"
    # 64B with wrong report_id -> strip fails, still unknown
    assert parse_firmware_reply(bytes([8]+[0]*63)) == "unknown"


def test_build_firmware_frame_is_0x82_sub2():
    frame = build_firmware_frame()
    assert len(frame) == 63
    assert frame[0] == 0x82 and frame[1] == 0x02
    # Must be little-endian path, not big-endian synthetic 1.17
    # Frame should contain 01 00 01 00 01 00 pattern as observed
    assert frame[5] == 1  # len 1
    assert frame[6] == 0x00


def test_observed_vs_expected_match_for_hero84():
    # Bundle expected is 0216, observed via parse is 0216
    from community.vetro_probe.bundle_export import export_bundle_for_uuid

    bundle_data = export_bundle_for_uuid(18691697672197)
    expected = bundle_data["firmware"]["branch"]
    assert expected == "0216"
    # Observed via safe read simulation: use same parse
    observed = parse_firmware_reply(bytes(bytearray([0x82, 0x02, 0, 0, 0, 2, 0x16, 0x02] + [0]*55)))
    assert observed == expected == "0216"
