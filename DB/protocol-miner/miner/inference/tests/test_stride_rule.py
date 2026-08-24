"""O-3: the record stride is decided by a rule, not by a statistic.

The record size must divide every observed body length, so the only candidates
are the divisors of their gcd.  The largest of those, the gcd itself, is the
most parsimonious reading and is what the engine names.  What matters as much
as the name is the confidence attached to it:

* if the gcd is prime the candidate set has one member and the stride is pinned
  by the evidence;
* otherwise every proper divisor fits the lengths equally well and the stride is
  under-determined -- the engine must say so rather than sound certain;
* if no body as short as the gcd was ever observed, no frame ever showed a
  single record, and the reading is a bound rather than an observation.

None of these three clauses can be evaluated against an answer key.  They are
properties of the evidence.
"""
from __future__ import annotations

from .. import engine, engine_v3
from ..hypothesis import HIGH_CONFIDENCE
from . import synth


def _rec(hyps):
    return {h.target: h for h in hyps if h.target.startswith("record.")}


def _run(prior=None):
    pkts = engine.load(synth.corpus_records())
    return _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, prior))


def test_stride_is_the_gcd_of_observed_body_lengths():
    got = _run()
    assert got["record.0x50"].prediction["stride"] == 5     # gcd(5, 55)
    assert got["record.0x51"].prediction["stride"] == 8     # gcd(8)
    assert got["record.0x52"].prediction["stride"] == 2     # gcd(14, 22)


def test_prime_gcd_pins_the_stride_and_earns_confidence():
    h = _run()["record.0x50"]
    assert h.confidence >= HIGH_CONFIDENCE
    assert h.alternatives == []


def test_composite_gcd_is_under_determined_and_must_not_sound_certain():
    h = _run()["record.0x51"]
    assert h.confidence < HIGH_CONFIDENCE
    assert {a["stride"] for a in h.alternatives} == {2, 4}


def test_never_seeing_a_single_record_body_caps_confidence():
    """0x52's gcd is prime, but the shortest body seen is seven records long."""
    h = _run()["record.0x52"]
    assert h.confidence < HIGH_CONFIDENCE
    assert any("shortest body observed" in s for s in h.supporting)


def test_v1_column_consistency_gets_these_wrong():
    """The statistic the rule replaces, on the same three commands."""
    pkts = engine.load(synth.corpus_records())
    got = _rec(engine.discover_records(pkts, 0, 5, 6, 62))
    wrong = [t for t, want in (("record.0x50", 5), ("record.0x51", 8),
                               ("record.0x52", 2))
             if got[t].prediction["stride"] != want]
    assert wrong, "expected the column-consistency statistic to miss at least one"
