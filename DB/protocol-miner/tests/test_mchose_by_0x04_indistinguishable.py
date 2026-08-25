"""BY wired 0x04: a routine write and a factory reset are the SAME 519 bytes.

This is not "no discriminator was found". It is a demonstration that none can
exist at the wire level, pinned to two frames the vendor's own client produced:

* `setPerformance` and `setReset` share the wired lead byte `0x04` and the same
  serialiser (`hpe`: both `() => j_(!0)`), so the frame is a pure function of the
  data object;
* the reset's data is a constant, `x8[model].otherObj` -- the factory-default
  performance record;
* that record is reachable through the settings UI, because it is simply what a
  factory-fresh keyboard reports.

So the tests below fix two things at once: the captured evidence, and the rule
that follows from it. Any classifier that ever returns SAFE_READ or a
non-destructive verdict for a wired `0x04` frame is wrong, and will fail here.

Captured 2026-08-25 through fake-WebHID from the live M HUB Web client driving a
fake K99. No hardware was involved, which is the entire point: this is what a
factory-reset frame looks like, obtained without sending one to a keyboard.
"""

import json
from pathlib import Path

import pytest

from miner.dynamic.mchose_by_oracle import classify_by_frame

_ARTIFACT = (Path(__file__).parent.parent.parent / "reports" / "protocol_knowledge"
             / "mchose" / "analysis" / "by_0x04_identity_k99.json")

# The leading 65 bytes of each frame. The real frames are 519 bytes; these are
# PREFIXES, kept short so the file stays readable, and
# `test_the_inline_prefixes_match_the_capture` asserts they really are prefixes
# so they cannot drift into fiction. `toggle_back` is a routine setPerformance
# write issued after toggling one switch off and back on; `reset` is the frame
# the vendor's own factory-reset confirmation produced.
ROUTINE_TOGGLE_BACK = (
    "04000001008000000103020000040407000b200100000000000100040100ff06"
    "00000001000101000000000000000000000000000000000000000000000000ffff"
)
# One byte apart from the pair above: offset 10, the latency switch.
ROUTINE_TOGGLE_ON = (
    "04000001008000000103000000040407000b200100000000000100040100ff06"
    "00000001000101000000000000000000000000000000000000000000000000ffff"
)


def _artifact() -> dict:
    if not _ARTIFACT.exists():
        pytest.skip("by_0x04_identity_k99.json not generated in this checkout")
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def _frames(doc: dict, action: str) -> list[str]:
    return [f["payload_hex"] for f in doc["frames"]
            if f.get("ui_action") == action
            and f.get("method") == "sendFeatureReport"
            and (f.get("payload_hex") or "").startswith("04")]


def test_the_routine_write_and_the_factory_reset_are_the_same_bytes():
    doc = _artifact()
    routine = _frames(doc, "routine:toggle_back")
    reset = _frames(doc, "reset_confirm")
    assert routine and reset, "both frames must be present for the claim to mean anything"
    assert routine[0] == reset[0], (
        "the whole finding is that these are identical; if they ever differ, the "
        "impossibility claim has to be withdrawn, not explained away"
    )
    assert len(bytes.fromhex(reset[0])) == 519


def test_the_capture_is_what_the_vendors_own_confirmation_produced():
    doc = _artifact()
    dlg = doc.get("dialog") or {}
    assert "заводск" in (dlg.get("text") or "").lower(), (
        "the reset frame must come from the vendor's own confirmation dialog, "
        f"not from a direct call; dialog text was {dlg.get('text')!r}"
    )
    assert doc["verdict"] == "IMPOSSIBLE_AT_WIRE_LEVEL"


def test_the_inline_prefixes_match_the_capture():
    """The constants above are excerpts, so they have to be checked as excerpts."""
    doc = _artifact()
    back = _frames(doc, "routine:toggle_back")[0]
    on = _frames(doc, "routine:toggle_on")[0]
    assert back.startswith(ROUTINE_TOGGLE_BACK), "the inline prefix drifted from the capture"
    assert on.startswith(ROUTINE_TOGGLE_ON), "the inline prefix drifted from the capture"
    assert len(bytes.fromhex(back)) == 519 and len(bytes.fromhex(on)) == 519


def test_a_routine_write_differs_from_its_own_neighbour_by_one_settings_byte():
    """The frames DO move -- with settings. That is why they cannot carry a command."""
    a = bytes.fromhex(ROUTINE_TOGGLE_ON)
    b = bytes.fromhex(ROUTINE_TOGGLE_BACK)
    differing = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    assert differing == [10], f"expected exactly the latency-switch byte to move, got {differing}"


def test_every_wired_0x04_frame_is_potentially_destructive():
    for name, frame in (("routine toggle_back", ROUTINE_TOGGLE_BACK),
                        ("routine toggle_on", ROUTINE_TOGGLE_ON)):
        cls, why = classify_by_frame("sendFeatureReport", 6, frame)
        assert cls == "POTENTIALLY_DESTRUCTIVE", (
            f"{name} was classified {cls}; a wired 0x04 frame is indistinguishable from a "
            "factory reset and must never be treated as safe"
        )
        assert "same" in why or "cannot" in why


def test_a_near_miss_is_still_potentially_destructive():
    """One byte off the reset value is still a 0x04 write, so still not safe."""
    b = bytearray(bytes.fromhex(ROUTINE_TOGGLE_BACK))
    b[10] ^= 0xFF
    cls, _ = classify_by_frame("sendFeatureReport", 6, bytes(b).hex())
    assert cls == "POTENTIALLY_DESTRUCTIVE"


def test_a_read_template_is_not_swept_into_the_destructive_class():
    """The rule must stay sharp: reads are reads."""
    cls, _ = classify_by_frame("sendFeatureReport", 6, "8700000100020000")
    assert cls == "SAFE_READ"
    cls, _ = classify_by_frame("sendFeatureReport", 6, "8400000100800000")
    assert cls == "SAFE_READ"


def test_an_unrecognised_frame_is_unknown_and_never_safe():
    cls, _ = classify_by_frame("sendFeatureReport", 6, "77deadbeef")
    assert cls == "UNKNOWN"


def test_the_classifier_never_claims_destructive_confirmed_from_a_frame():
    """It cannot be known from the bytes, so it must not be asserted from them.

    Claiming DESTRUCTIVE_CONFIRMED on frame inspection would be a false positive
    on every routine settings write, which is the mirror-image error of calling
    a reset safe -- and it would be equally unfounded.
    """
    for frame in (ROUTINE_TOGGLE_BACK, ROUTINE_TOGGLE_ON):
        cls, _ = classify_by_frame("sendFeatureReport", 6, frame)
        assert cls != "DESTRUCTIVE_CONFIRMED"
