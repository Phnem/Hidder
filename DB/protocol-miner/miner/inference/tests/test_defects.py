"""The three named engine defects, C-1 / C-2 / C-3, as executable statements.

Each defect gets two tests: one that pins the defect down in the engine that
has it, and one that requires the repaired engine not to have it.  The first
kind is what makes the second kind mean something -- a test that passes on both
engines is not testing the fix.

Nothing here reads ground_truth/truth.json or any AULA data.  The right answer
is a property of the synthetic corpus, known by construction.
"""
from __future__ import annotations

from .. import engine, engine_v2, engine_v3
from . import synth


# --------------------------------------------------------------------------
# C-1  singleton guard in the sub-opcode search
# --------------------------------------------------------------------------

def test_c1_defect_reproduces_in_v1():
    """v1 picks the serial-number byte because singletons look perfect."""
    pkts = engine.load(synth.corpus_singleton_shatter())
    h = engine.discover_sub_offset(pkts, 0)
    assert h.prediction == 9, "expected v1 to fall for the singleton shatter"


def test_c1_fixed_in_v3():
    pkts = engine.load(synth.corpus_singleton_shatter())
    h = engine_v3.discover_sub_offset(pkts, 0)
    assert h.prediction == 1


def test_c1_singleton_group_contributes_no_evidence():
    """The guard must be about group size, not about the offset's identity."""
    pkts = engine.load(synth.corpus_singleton_shatter())
    members = [p for p in pkts if p.payload[0] == 0x40]
    # offset 9 is a per-frame serial: every group has exactly one member
    assert len({m.payload[9] for m in members}) == len(members)
    assert engine_v3._split_score(members, 0, 9) == 0.0


# --------------------------------------------------------------------------
# C-2  votes weighted by evidence volume, not one vote per opcode
# --------------------------------------------------------------------------

def test_c2_defect_reproduces_in_v1():
    """Five thin commands outvote two fat ones under a per-opcode count."""
    pkts = engine.load(synth.corpus_evidence_volume())
    h = engine.discover_sub_offset(pkts, 0)
    assert h.prediction == 3, "expected v1 to be carried by the thin majority"


def test_c2_defect_reproduces_in_v2():
    """v2 fixed C-1 only; it still counts one vote per opcode."""
    pkts = engine.load(synth.corpus_evidence_volume())
    h = engine_v2.discover_sub_offset_v2(pkts, 0)
    assert h.prediction == 3


def test_c2_fixed_in_v3():
    pkts = engine.load(synth.corpus_evidence_volume())
    h = engine_v3.discover_sub_offset(pkts, 0)
    assert h.prediction == 1
    assert h.evidence_count >= 400, "evidence count must be frames, not opcodes"


# --------------------------------------------------------------------------
# C-3  a prior may move ranking, never confidence
# --------------------------------------------------------------------------

def _rec(hyps):
    return {h.target: h for h in hyps if h.target.startswith("record.")}


def test_c3_defect_reproduces_in_v2():
    """v2's family-transfer bonus raises confidence on an unchanged answer."""
    pkts = engine.load(synth.corpus_records())
    base = _rec(engine_v2.discover_records_v2(pkts, 0, 5, 6, 62, None))
    lead = set()
    for p in pkts:
        if p.payload[0] == 0x51:
            lead.add((p.payload[6] << 8) | p.payload[7])
    boosted = _rec(engine_v2.discover_records_v2(
        pkts, 0, 5, 6, 62, {"known_leading_ids": lead}))
    raised = [t for t in base
              if boosted[t].confidence > base[t].confidence
              and boosted[t].prediction == base[t].prediction]
    assert raised, "expected v2 to raise confidence without changing the answer"


def test_c3_prior_never_raises_confidence_in_v3():
    pkts = engine.load(synth.corpus_records())
    base = _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, None))
    lead = set()
    for p in pkts:
        lead.add((p.payload[6] << 8) | p.payload[7])
    prior = {"known_leading_ids": lead,
             "known_strides": {"0x51": 4, "0x50": 5, "0x52": 2}}
    with_prior = _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, prior))
    for t, h in base.items():
        assert with_prior[t].confidence <= h.confidence, (
            f"{t}: prior raised confidence {h.confidence} -> "
            f"{with_prior[t].confidence}")


def test_c3_prior_may_move_ranking_in_v3():
    """The same prior is allowed to change which candidate is named."""
    pkts = engine.load(synth.corpus_records())
    base = _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, None))
    prior = {"known_strides": {"0x51": 4}}
    with_prior = _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, prior))
    assert base["record.0x51"].prediction["stride"] == 8
    assert with_prior["record.0x51"].prediction["stride"] == 4


def test_c3_prior_cannot_move_ranking_against_the_evidence():
    """A prior that contradicts the observed lengths is refused, not obeyed."""
    pkts = engine.load(synth.corpus_records())
    prior = {"known_strides": {"0x50": 3}}   # 3 does not divide 5
    got = _rec(engine_v3.discover_records(pkts, 0, 5, 6, 62, prior))
    assert got["record.0x50"].prediction["stride"] == 5
