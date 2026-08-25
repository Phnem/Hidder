"""The CZ envelope must reproduce a CAPTURED frame, not just a transcription.

A codec that only round-trips its own output proves that the author was
self-consistent. The anchor here is the frame the live M HUB Web actually wrote
into the fake device, so a transcription error shows up as a test failure rather
than as a confident wrong document.
"""

import pytest

from miner.static import mchose_cz_codec as cz

# Captured 2026-08-25 from the live vendor client driving fake 0x3837:0x3020.
# reports/protocol_knowledge/mchose/oracle/god60_normal.jsonl, 88 identical rows.
OBSERVED = bytes.fromhex("5503003838" + "00" * 59)


def test_the_observed_frame_decodes_to_a_coherent_envelope():
    f = cz.parse(OBSERVED)
    assert f.flag == cz.FLAG_REQUEST
    assert f.command == 0x03
    assert f.size == 56
    assert f.offset == 0
    assert f.trailing == [0] * 56
    assert f.checksum_ok, "the vendor's own checksum rule must accept its own frame"


def test_the_builder_reproduces_the_observed_frame_byte_for_byte():
    rebuilt = cz.build(command=0x03, data=[0] * 56, offset=0)
    assert rebuilt == OBSERVED, (
        "the transcribed builder does not reproduce a frame the vendor actually sent; "
        f"got {rebuilt.hex()} want {OBSERVED.hex()}"
    )


def test_offset_is_16_bit_little_endian():
    # 0x0102 -> lo 0x02, hi 0x01. Checked away from zero, since offset 0 cannot
    # tell little-endian from big-endian and the captured frame uses offset 0.
    frame = cz.build(command=0x11, data=[], offset=0x0102)
    assert frame[5] == 0x02 and frame[6] == 0x01
    assert cz.parse(frame).offset == 0x0102


def test_checksum_covers_the_body_and_not_the_header():
    a = cz.build(command=0x03, data=[1, 2, 3], offset=0)
    b = cz.build(command=0x7F, data=[1, 2, 3], offset=0)
    assert a[3] == b[3], "the command byte is outside the checksummed range"
    c = cz.build(command=0x03, data=[1, 2, 4], offset=0)
    assert a[3] != c[3], "a payload change must change the checksum"


def test_a_synthetic_reply_satisfies_the_vendors_own_matcher():
    reply = cz.synthesize_reply(OBSERVED)
    assert cz.reply_matches_request(OBSERVED, reply)
    assert reply[0] == cz.FLAG_EXPECT_REPLY


def test_the_matcher_rejects_the_request_echoed_back():
    """An echo is not a reply. The vendor distinguishes them by the flag byte,
    and so must we -- this is the same trap the echo audit exists for."""
    assert not cz.reply_matches_request(OBSERVED, OBSERVED)


def test_the_matcher_rejects_a_wrong_command_or_offset():
    good = cz.synthesize_reply(OBSERVED)
    wrong_cmd = bytearray(good)
    wrong_cmd[1] = 0x04
    assert not cz.reply_matches_request(OBSERVED, bytes(wrong_cmd))
    wrong_off = bytearray(good)
    wrong_off[5] = 0x01
    assert not cz.reply_matches_request(OBSERVED, bytes(wrong_off))


def test_a_reply_longer_than_requested_is_refused_at_build_time():
    with pytest.raises(ValueError):
        cz.synthesize_reply(OBSERVED, payload=[0] * 57)


def test_a_read_and_an_all_zero_write_are_byte_identical():
    """The safety-relevant ambiguity, pinned so it cannot be quietly resolved.

    The vendor puts 'bytes requested' and 'bytes supplied' in the same byte, so a
    write of zeros and a read of the same size at the same offset produce the
    same 64 bytes. Any classifier that reports a direction for a CZ frame is
    reporting an assumption.
    """
    read = cz.build(command=0x07, offset=0x0038, read_size=56)
    write_of_zeros = cz.build(command=0x07, data=[0] * 56, offset=0x0038)
    assert read == write_of_zeros
    assert cz.parse(read).direction == "UNKNOWN"


def test_parse_reports_trailing_bytes_without_calling_them_data():
    frame = cz.build(command=0x09, data=[0xAA, 0xBB], offset=0)
    assert len(frame) == 64
    f = cz.parse(frame)
    assert f.trailing == [0xAA, 0xBB]
    assert not f.trailing_is_all_zero
    assert not hasattr(f, "data"), "naming these bytes 'data' decides the direction question"


def test_the_provenance_record_separates_what_the_frame_can_and_cannot_prove():
    p = cz.checksum_is_sum_not_xor_provenance()
    assert p["checksum_is_sum"]["NOT_evidence"], (
        "the captured frame has an all-zero payload, so it cannot tell sum from xor; "
        "the record must say so rather than let the frame be cited for it"
    )
    assert p["command_semantics"]["claim"] is None, (
        "this module must not claim to know what any command byte means"
    )
    assert p["direction"]["claim"] is None, (
        "read vs write is not readable off a CZ frame and must not be asserted"
    )


def test_an_oversized_frame_is_refused_as_the_vendor_asserts():
    with pytest.raises(ValueError):
        cz.build(command=0x01, data=[0] * 57, offset=0)
