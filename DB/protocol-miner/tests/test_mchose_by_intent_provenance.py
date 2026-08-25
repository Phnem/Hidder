"""TICKET-25 final A-Preview pass, section 3: intent provenance vs frame bytes.

BY_0X04_INDISTINGUISHABLE.md proved a routine setPerformance write and a
factory-reset setReset can be the SAME 519 bytes. That leaves exactly one place
safety can live: the command name at the call site, before serialise() throws
it away. classify_by_intent() (miner/dynamic/mchose_by_oracle.py) is that
place. These tests pin two things classify_by_frame() cannot provide:

1. the SAME wire bytes must get DIFFERENT verdicts depending on which command
   produced them;
2. a frame offered with no command name -- a captured/replayed 0x04 -- must be
   BLOCKED, never classified by guessing from its bytes.

No hardware, no new capture. Reuses the frames already pinned in
test_mchose_by_0x04_indistinguishable.py.
"""

from miner.dynamic.mchose_by_oracle import classify_by_intent, classify_by_frame

# Same constant as test_mchose_by_0x04_indistinguishable.py: a routine
# setPerformance write, proven byte-identical to the captured factory-reset frame.
SAME_BYTES = (
    "04000001008000000103020000040407000b200100000000000100040100ff06"
    "00000001000101000000000000000000000000000000000000000000000000ffff"
)


def test_classify_by_frame_cannot_tell_these_apart():
    """The baseline this test exists to improve on: one verdict for both intents."""
    cls, _ = classify_by_frame("sendFeatureReport", 6, SAME_BYTES)
    assert cls == "POTENTIALLY_DESTRUCTIVE"


def test_same_wire_bytes_get_different_verdicts_by_intent():
    reset_cls, reset_why = classify_by_intent("setReset", SAME_BYTES)
    perf_cls, perf_why = classify_by_intent("setPerformance", SAME_BYTES)
    assert reset_cls == "DESTRUCTIVE_CONFIRMED"
    assert perf_cls == "POTENTIALLY_DESTRUCTIVE"
    assert reset_cls != perf_cls, (
        "the whole point: identical bytes, different commands, different verdicts"
    )
    assert "setReset" in reset_why or "restoreFactorySetting" in reset_why
    assert "setPerformance" in perf_why


def test_opaque_captured_or_replayed_0x04_is_blocked_not_guessed():
    cls, why = classify_by_intent(None, SAME_BYTES)
    assert cls == "BLOCKED"
    assert "provenance" in why or "refused" in why


def test_blocked_is_not_a_frame_level_classification():
    """BLOCKED must never appear from classify_by_frame -- it is an intent-only verdict."""
    cls, _ = classify_by_frame("sendFeatureReport", 6, SAME_BYTES)
    assert cls != "BLOCKED"


def test_reads_are_unaffected_by_the_intent_path():
    """Intent classification only changes the ambiguous 0x04 pair; reads pass through."""
    cls, _ = classify_by_intent("getBattery", "8700000100020000")
    assert cls == "SAFE_READ"


def test_an_unknown_command_name_on_0x04_does_not_become_safe():
    cls, _ = classify_by_intent("someFutureCommand", SAME_BYTES)
    assert cls != "SAFE_READ"
    assert cls in ("POTENTIALLY_DESTRUCTIVE", "UNKNOWN")
