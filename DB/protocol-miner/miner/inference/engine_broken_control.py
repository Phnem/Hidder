"""A deliberately broken engine, used as the gate's negative control.

The ticket's verification plan asks for this directly: a green gate means
nothing unless a red one is reachable on the same corpus, through the same
freezer and the same scorer.  So before the real runs are scored, this engine
is frozen and scored exactly like them, and it is expected to come back RED.

It is v1's already-known-defective framing search with the confidence clamp
removed -- the two defects C-1 and C-2 left in place, and every answer asserted
as STRONGLY_SUPPORTED regardless of how thin the evidence behind it is.  That
is the shape of failure the gate exists to catch: not an engine that knows
nothing, but one that is wrong and sure.

This module is never a submission and must never be cited as a result.
"""
from __future__ import annotations

from . import engine
from .engine import (_full, discover_checksum, discover_constants,  # noqa: F401
                     discover_controlled, discover_get_set,
                     discover_length_field, discover_opcode_offset,
                     discover_registers, load)
from .hypothesis import Hypothesis


def _overconfident(h):
    if h.prediction is None:
        return h
    return Hypothesis(
        target=h.target, prediction=h.prediction, confidence=0.95,
        status="STRONGLY_SUPPORTED", evidence_count=h.evidence_count,
        supporting=h.supporting, alternatives=h.alternatives,
        notes="NEGATIVE CONTROL: confidence asserted, not earned")


def run(rows, partial_schema=None):
    pkts = load(rows)
    hyps = []

    h_op = discover_opcode_offset(pkts)
    hyps.append(_overconfident(h_op))
    op_off = h_op.prediction if h_op.prediction is not None else 0

    # C-1 and C-2 left in: v1's sub-opcode search, singleton-blind and counting
    # one vote per opcode.
    h_sub = engine.discover_sub_offset(pkts, op_off)
    hyps.append(_overconfident(h_sub))
    sub_off = h_sub.prediction if h_sub.prediction is not None else 1

    hyps.append(discover_constants(pkts, op_off))

    h_chk = discover_checksum(pkts)
    hyps.append(h_chk)
    chk_off = (h_chk.prediction or {}).get("offset", len(_full(pkts)[0].payload) - 1)

    h_len, h_body = discover_length_field(pkts, op_off, chk_off)
    hyps += [_overconfident(h_len), _overconfident(h_body)]
    len_off = h_len.prediction if h_len.prediction is not None else 5
    body_off = h_body.prediction if h_body.prediction is not None else 6

    hyps += [_overconfident(h)
             for h in engine.discover_records(pkts, op_off, len_off, body_off, chk_off)]
    hyps.append(discover_get_set(pkts, op_off, sub_off))
    hyps += discover_registers(pkts, op_off, sub_off, len_off, body_off)
    hyps += discover_controlled(pkts, op_off, sub_off, body_off)
    return hyps
