"""Engine v2 -- POST-HOC.  Not the pre-registered benchmark result.

v1 (engine.py) is frozen and its score stands as the honest, pre-registered
number.  v2 exists to answer a different and still useful question: how much of
what v1 missed was a genuine limit of automatic inference, and how much was
just three specific defects in v1?

Every change here was made AFTER v1's score was known, so v2's numbers must
never be quoted as blind-run results.  The three changes:

1. `discover_sub_offset` lacked the singleton guard that its sibling
   `discover_opcode_offset` already had, so an offset that shatters a command
   group into groups of one scored a perfect 1.0.  A real bug.

2. Record-stride selection had no way to reject a stride that is an integer
   multiple of the true one.  Fixed by reducing the winning stride to the
   minimal period of its column signature -- if columns repeat with period p,
   the record is p bytes, not s.

3. The engine accepted a `partial_schema` but only consulted it when a
   discovery returned None, which never happened.  Mode C was therefore
   identical to mode A.  v2 actually uses prior family knowledge, which is the
   whole question mode C was built to answer.
"""
from __future__ import annotations

import collections

from .engine import (Packet, _endianness, _full, _monotone, discover_checksum,
                     discover_constants, discover_controlled,
                     discover_get_set, discover_opcode_offset,
                     discover_length_field, discover_registers, load)
from .hypothesis import Hypothesis, grade, unknown


def discover_sub_offset_v2(pkts, op_off):
    """As v1, but groups of one no longer count as evidence."""
    full = _full(pkts)
    width = len(full[0].payload)
    per_op = collections.defaultdict(list)
    for p in full:
        per_op[p.payload[op_off]].append(p)
    votes = collections.Counter()
    detail = collections.defaultdict(list)
    for op, members in per_op.items():
        if len(members) < 4:
            continue
        best = None
        for off in range(width):
            if off == op_off:
                continue
            sub_groups = collections.defaultdict(list)
            for m in members:
                sub_groups[m.payload[off]].append(m)
            if len(sub_groups) < 2:
                continue
            # A discriminator must actually group things.  Splitting a command
            # into singletons explains nothing, so singletons score zero and
            # a split that is mostly singletons is rejected outright.
            usable = [g for g in sub_groups.values() if len(g) >= 2]
            covered = sum(len(g) for g in usable)
            if covered < 0.5 * len(members):
                continue
            const = 0
            for grp in usable:
                const += sum(1 for i in range(width)
                             if i not in (op_off, off)
                             and len({g.payload[i] for g in grp}) == 1) * len(grp)
            score = const / (covered * width)
            if best is None or score > best[0]:
                best = (score, off)
        if best and best[0] > 0.5:
            votes[best[1]] += 1
            detail[best[1]].append(hex(op))
    if not votes:
        return unknown("framing.sub_offset", "no consistent second-level discriminator")
    off, n = votes.most_common(1)[0]
    conf, status = grade(n, 0, len(votes) == 1)
    return Hypothesis(
        target="framing.sub_offset", prediction=off, confidence=conf, status=status,
        evidence_count=n,
        supporting=[f"opcodes {sorted(set(detail[off]))} split cleanly on offset {off}"],
        alternatives=[{"offset": o, "opcodes": sorted(set(detail[o]))}
                      for o, _ in votes.most_common()[1:3]],
    )


def _minimal_period(columns):
    """Smallest p dividing len(columns) with columns[i] == columns[i % p]."""
    s = len(columns)
    for p in range(1, s):
        if s % p:
            continue
        if all(columns[i] == columns[i % p] for i in range(s)):
            return p
    return s


def _column_signature(members, len_off, body_off, stride):
    cols = []
    for j in range(stride):
        vals = collections.Counter()
        for p in members:
            n = p.payload[len_off] // stride
            for k in range(n):
                vals[p.payload[body_off + k * stride + j]] += 1
        # Signature is the value SET, not the counts: two columns holding the
        # same alphabet are interchangeable for periodicity purposes.
        cols.append(frozenset(vals))
    return cols


def discover_records_v2(pkts, op_off, len_off, body_off, chk_off, family_prior=None):
    full = _full(pkts)
    out = []
    per_op = collections.defaultdict(list)
    for p in full:
        per_op[p.payload[op_off]].append(p)
    prior_ids = set((family_prior or {}).get("known_leading_ids") or ())
    for op, members in sorted(per_op.items()):
        lens = {p.payload[len_off] for p in members if p.payload[len_off]}
        if not lens or len(members) < 3:
            continue
        cands = [s for s in range(2, 17)
                 if all(L % s == 0 for L in lens) and max(lens) // s >= 2]
        if not cands:
            continue
        scored = []
        for s in cands:
            agree = tot = 0
            for p in members:
                n = p.payload[len_off] // s
                if n < 2:
                    continue
                for j in range(s):
                    col = [p.payload[body_off + k * s + j] for k in range(n)]
                    tot += 1
                    if len(set(col)) == 1 or _monotone(col):
                        agree += 1
            if tot:
                scored.append((agree / tot, s))
        if not scored:
            continue
        scored.sort(reverse=True)
        stride = scored[0][1]
        raw_pick = stride
        # Collapse a multiple of the true stride down to the real record size.
        period = _minimal_period(_column_signature(members, len_off, body_off, stride))
        if period != stride and all(L % period == 0 for L in lens):
            stride = period

        spans = []
        for p in members:
            n = p.payload[len_off] // stride
            for k in range(n):
                o = body_off + k * stride
                spans.append((p.payload[o], p.payload[o + 1]))
        etype, emax = _endianness(spans) if spans else ("unknown", 0)

        support = [f"body length is always a multiple of {stride}",
                   f"leading 2-byte field reads as {etype} (max {emax})"]
        if raw_pick != stride:
            support.append(f"column signature repeats with period {stride} inside the "
                           f"{raw_pick}-byte block, so the record is {stride} bytes")
        conf, status = grade(len(members), 0, len(scored) == 1 or
                             scored[0][0] - scored[1][0] > 0.08)
        # Family transfer: if a known sibling command's leading field is a key
        # id, a candidate whose leading values fall in that same id space is
        # much more likely to be right.
        if prior_ids:
            lead = {(a << 8) | b for a, b in spans}
            hit = len(lead & prior_ids) / max(1, len(lead))
            if hit > 0.6:
                conf = min(0.97, conf + 0.1)
                support.append(f"{hit:.0%} of leading values fall inside the id space "
                               "already established for this protocol family")
        out.append(Hypothesis(
            target=f"record.{hex(op)}", confidence=conf, status=status,
            prediction={"stride": stride, "body_offset": body_off,
                        "first_field": {"rel_offset": 0, "length": 2, "type": etype},
                        "max_chunk_bytes": max(lens)},
            evidence_count=len(members), supporting=support,
            alternatives=[{"stride": s, "score": round(sc, 3)} for sc, s in scored[1:3]],
        ))
    return out


def _family_prior(pkts, schema, op_off, len_off, body_off):
    """Turn 'we already know this family' into something usable."""
    if not schema:
        return None
    known = schema.get("known_record_layouts") or {}
    ids = set()
    full = _full(pkts)
    for op_hex, spec in known.items():
        if not spec.get("stride"):
            continue
        op = int(op_hex, 16)
        stride, bo = spec["stride"], spec.get("body_offset", body_off)
        for p in full:
            if p.payload[op_off] != op:
                continue
            n = p.payload[len_off] // stride
            for k in range(n):
                o = bo + k * stride
                ids.add((p.payload[o] << 8) | p.payload[o + 1])
    return {"known_leading_ids": ids} if ids else None


def run(rows, partial_schema=None):
    pkts = load(rows)
    hyps = []
    known = (partial_schema or {}).get("framing", {})

    def adopt(h, key, default):
        """Prior schema wins when it speaks; discovery fills the silence."""
        if key in known:
            return Hypothesis(
                target=h.target, prediction=known[key], confidence=1.0,
                status="VERIFIED_STATIC", evidence_count=0,
                supporting=["supplied by the known family schema"],
                notes=f"engine independently guessed {h.prediction!r}")
        return h

    h_op = adopt(discover_opcode_offset(pkts), "opcode_offset", 0)
    hyps.append(h_op)
    op_off = h_op.prediction if h_op.prediction is not None else 0

    h_sub = adopt(discover_sub_offset_v2(pkts, op_off), "sub_offset", 1)
    hyps.append(h_sub)
    sub_off = h_sub.prediction if h_sub.prediction is not None else 1

    hyps.append(discover_constants(pkts, op_off))

    h_chk = discover_checksum(pkts)
    hyps.append(h_chk)
    chk_off = (h_chk.prediction or {}).get("offset", len(_full(pkts)[0].payload) - 1)

    h_len, h_body = discover_length_field(pkts, op_off, chk_off)
    h_len = adopt(h_len, "length_offset", 5)
    h_body = adopt(h_body, "body_offset", 6)
    hyps += [h_len, h_body]
    len_off = h_len.prediction if h_len.prediction is not None else 5
    body_off = h_body.prediction if h_body.prediction is not None else 6

    prior = _family_prior(pkts, partial_schema, op_off, len_off, body_off)
    hyps += discover_records_v2(pkts, op_off, len_off, body_off, chk_off, prior)
    hyps.append(discover_get_set(pkts, op_off, sub_off))
    hyps += discover_registers(pkts, op_off, sub_off, len_off, body_off)
    hyps += discover_controlled(pkts, op_off, sub_off, body_off)
    return hyps
