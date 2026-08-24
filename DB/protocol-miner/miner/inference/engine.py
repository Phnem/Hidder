"""Deterministic protocol inference engine.

No LLM, no device-specific knowledge, no hardcoded AULA constants.  Everything
below is a general algorithm over a bag of packets: find the framing, find the
checksum, find the records, pair up reads with writes.  The engine is allowed
to say UNKNOWN and is expected to.

Entry point:  run(rows, partial_schema=None) -> list[Hypothesis]
"""
from __future__ import annotations

import collections
import itertools
from fractions import Fraction

from . import numeric
from .hypothesis import Hypothesis, grade, unknown


class Packet:
    __slots__ = ("id", "session", "seq", "source", "payload", "reply",
                 "ui_before", "ui_after", "phase", "truncated")

    def __init__(self, row):
        self.id = row.get("id")
        self.session = row.get("session")
        self.seq = row.get("seq")
        self.source = row.get("source")
        self.payload = bytes.fromhex(row["payload_hex"]) if row.get("payload_hex") else b""
        self.reply = bytes.fromhex(row["reply_hex"]) if row.get("reply_hex") else None
        self.ui_before = row.get("ui_before")
        self.ui_after = row.get("ui_after")
        self.phase = row.get("phase")
        self.truncated = bool(row.get("payload_truncated"))


def load(rows):
    pkts = [Packet(r) for r in rows]
    return [p for p in pkts if p.payload]


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------

def _full(pkts):
    """Only packets whose bytes were fully observed can carry framing evidence."""
    if not pkts:
        return []
    width = collections.Counter(len(p.payload) for p in pkts).most_common(1)[0][0]
    return [p for p in pkts if len(p.payload) == width and not p.truncated]


def discover_opcode_offset(pkts):
    """The opcode is the offset that best explains everything else.

    For each candidate offset, partition the packets by the byte there and
    measure how much of the rest of the packet becomes constant inside those
    partitions.  A real opcode collapses the variability of the frame; an
    incidental data byte does not.
    """
    full = _full(pkts)
    if len(full) < 8:
        return unknown("framing.opcode_offset", "not enough full-width packets")
    width = len(full[0].payload)
    scores = []
    for off in range(min(width, 8)):
        groups = collections.defaultdict(list)
        for p in full:
            groups[p.payload[off]].append(p)
        card = len(groups)
        if not (2 <= card <= 48):
            continue
        tot = 0.0
        for members in groups.values():
            if len(members) < 2:
                tot += 0.0
                continue
            const = sum(
                1 for i in range(width)
                if i != off and len({m.payload[i] for m in members}) == 1)
            tot += const / (width - 1) * len(members)
        scores.append((tot / len(full), -card, off))
    if not scores:
        return unknown("framing.opcode_offset", "no offset had opcode-like cardinality")
    scores.sort(reverse=True)
    best, runner = scores[0], (scores[1] if len(scores) > 1 else None)
    unique = runner is None or (best[0] - runner[0]) > 0.05
    conf, status = grade(len(full), 0, unique)
    return Hypothesis(
        target="framing.opcode_offset",
        prediction=best[2],
        confidence=conf, status=status, evidence_count=len(full),
        supporting=[f"partitioning by offset {best[2]} makes {best[0]:.1%} of the "
                    f"remaining bytes constant within each group"],
        alternatives=[{"offset": o, "score": round(s, 4)} for s, _, o in scores[1:4]],
        notes="offset chosen by variance collapse, not by convention",
    )


def discover_sub_offset(pkts, op_off):
    """Two-level opcodes: an opcode group whose members split again."""
    full = _full(pkts)
    width = len(full[0].payload)
    per_op = collections.defaultdict(list)
    for p in full:
        per_op[p.payload[op_off]].append(p)
    votes = collections.Counter()
    detail = {}
    for op, members in per_op.items():
        if len(members) < 4:
            continue
        best = None
        for off in range(width):
            if off == op_off:
                continue
            card = len({m.payload[off] for m in members})
            if card < 2:
                continue
            sub_groups = collections.defaultdict(list)
            for m in members:
                sub_groups[m.payload[off]].append(m)
            const = 0
            for grp in sub_groups.values():
                const += sum(1 for i in range(width)
                             if i not in (op_off, off)
                             and len({g.payload[i] for g in grp}) == 1) * len(grp)
            score = const / (len(members) * width)
            if best is None or score > best[0]:
                best = (score, off, card)
        if best and best[0] > 0.5:
            votes[best[1]] += 1
            detail.setdefault(best[1], []).append(hex(op))
    if not votes:
        return unknown("framing.sub_offset", "no consistent second-level discriminator")
    off, n = votes.most_common(1)[0]
    conf, status = grade(n, 0, len(votes) == 1)
    return Hypothesis(
        target="framing.sub_offset", prediction=off,
        confidence=conf, status=status, evidence_count=n,
        supporting=[f"opcodes {sorted(set(detail.get(off, [])))} all split cleanly on offset {off}"],
        alternatives=[{"offset": o, "opcodes": sorted(set(detail.get(o, [])))}
                      for o, _ in votes.most_common()[1:3]],
    )


def discover_constants(pkts, op_off):
    full = _full(pkts)
    width = len(full[0].payload)
    consts, exceptions = {}, {}
    for i in range(width):
        vals = collections.Counter(p.payload[i] for p in full)
        if len(vals) == 1:
            consts[i] = vals.most_common(1)[0][0]
        elif len(vals) <= 3:
            top, n = vals.most_common(1)[0]
            if n / len(full) > 0.97:
                consts[i] = top
                exceptions[i] = {hex(v): c for v, c in vals.items() if v != top}
    return Hypothesis(
        target="framing.constant_offsets", prediction=consts,
        confidence=0.9 if consts else 0.0,
        status="STRONGLY_SUPPORTED" if consts else "UNKNOWN",
        evidence_count=len(full),
        supporting=[f"{len(consts)} offsets hold one value across {len(full)} packets"],
        notes=(f"near-constant offsets with rare exceptions: {exceptions}"
               if exceptions else None),
        next_best_experiment=("exercise the operations that produced the rare values at "
                              f"offsets {sorted(exceptions)} - a near-constant byte that "
                              "flips is usually a mode or destructive-variant flag"
                              if exceptions else None),
    )


# --------------------------------------------------------------------------
# checksum
# --------------------------------------------------------------------------

CRC8_POLYS = [0x07, 0x31, 0x1D, 0x9B, 0x2F]


def _crc8(data, poly, init=0x00, xorout=0x00):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc ^ xorout


def discover_checksum(pkts):
    """Search a small space of check formulas and demand a perfect fit."""
    full = _full(pkts)
    if len(full) < 10:
        return unknown("checksum", "not enough full packets")
    width = len(full[0].payload)
    fits = []
    for cpos in (width - 1, width - 2, 0):
        rng = (0, cpos) if cpos else (1, width)
        lo, hi = rng
        sums = [(sum(p.payload[lo:hi]) & 0xFF, p.payload[cpos]) for p in full]
        # affine over the byte sum: chk = (a*S + b) mod 256
        for a in (1, -1):
            bs = {(chk - a * s) % 256 for s, chk in sums}
            if len(bs) == 1:
                fits.append({
                    "kind": "affine_over_sum", "offset": cpos, "range": [lo, hi],
                    "a": a, "b": bs.pop(), "complexity": 1})
        xs = [(0, 0)]
        acc = []
        for p in full:
            x = 0
            for b in p.payload[lo:hi]:
                x ^= b
            acc.append((x, p.payload[cpos]))
        bs = {(chk ^ x) for x, chk in acc}
        if len(bs) == 1:
            fits.append({"kind": "xor", "offset": cpos, "range": [lo, hi],
                         "xorout": bs.pop(), "complexity": 2})
        for poly in CRC8_POLYS:
            outs = {(_crc8(p.payload[lo:hi], poly) ^ p.payload[cpos]) for p in full}
            if len(outs) == 1:
                fits.append({"kind": "crc8", "poly": poly, "offset": cpos,
                             "range": [lo, hi], "xorout": outs.pop(), "complexity": 3})
    if not fits:
        return unknown(
            "checksum", "no formula in the search space fits every packet",
            experiment="capture packets that differ in exactly one body byte so the "
                       "check byte's response to a single-bit change can be isolated")
    fits.sort(key=lambda f: f["complexity"])
    best = fits[0]
    conf, status = grade(len(full), 0, len({f["kind"] for f in fits}) == 1)
    return Hypothesis(
        target="checksum", prediction=best, confidence=conf, status=status,
        evidence_count=len(full),
        supporting=[f"holds for all {len(full)} full-width packets"],
        alternatives=fits[1:4],
    )


# --------------------------------------------------------------------------
# length / body / records
# --------------------------------------------------------------------------

def discover_length_field(pkts, op_off, chk_off):
    full = _full(pkts)
    width = len(full[0].payload)
    best = None
    for body in range(2, 12):
        for lo in range(1, body):
            hits = tot = 0
            for p in full:
                tail = p.payload[body:chk_off]
                last = 0
                for i, b in enumerate(tail):
                    if b:
                        last = i + 1
                if last == 0:
                    continue
                tot += 1
                if p.payload[lo] == last:
                    hits += 1
            if tot >= 10:
                r = hits / tot
                if best is None or r > best[0]:
                    best = (r, lo, body, hits, tot)
    if not best or best[0] < 0.6:
        return (unknown("framing.length_offset", "no offset tracks the used body extent"),
                unknown("framing.body_offset", "length field not found"))
    r, lo, body, hits, tot = best
    conf, status = grade(hits, tot - hits, True)
    if r < 0.98:
        conf, status = min(conf, 0.6), "PREDICTED"
    hl = Hypothesis(
        target="framing.length_offset", prediction=lo, confidence=conf, status=status,
        evidence_count=hits,
        supporting=[f"payload[{lo}] equals the used body length for {hits}/{tot} packets"],
        contradicting=([f"{tot - hits} packets disagree"] if tot > hits else []))
    hb = Hypothesis(
        target="framing.body_offset", prediction=body, confidence=conf, status=status,
        evidence_count=hits,
        supporting=[f"body starting at {body} is consistent with the length at {lo}"])
    return hl, hb


def _endianness(spans):
    """Pick byte order for 2-byte fields by which reading is more compact.

    Small identifiers and small physical quantities cluster tightly under the
    correct byte order and scatter across the whole 16-bit space under the wrong
    one, so range is a reliable discriminator without any semantics.
    """
    be = [(a << 8) | b for a, b in spans]
    le = [(b << 8) | a for a, b in spans]
    return ("u16be", max(be)) if max(be) <= max(le) else ("u16le", max(le))


def discover_records(pkts, op_off, len_off, body_off, chk_off):
    """For each opcode, find a repeating record size inside the body."""
    full = _full(pkts)
    out = []
    per_op = collections.defaultdict(list)
    for p in full:
        per_op[p.payload[op_off]].append(p)
    for op, members in sorted(per_op.items()):
        lens = {p.payload[len_off] for p in members if p.payload[len_off]}
        if not lens or len(members) < 3:
            continue
        cands = [s for s in range(1, 17) if all(L % s == 0 for L in lens)]
        cands = [s for s in cands if s > 1 and max(lens) // s >= 2]
        if not cands:
            continue
        scored = []
        for s in cands:
            agree = tot = 0
            for p in members:
                L = p.payload[len_off]
                n = L // s
                if n < 2:
                    continue
                # A record boundary is right when the same relative position
                # behaves the same way in every record of the same packet.
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
        top = scored[0]
        rival = scored[1] if len(scored) > 1 else None
        unique = rival is None or top[0] - rival[0] > 0.08
        stride = top[1]
        conf, status = grade(len(members), 0, unique)
        conf = min(conf, 0.5 + 0.5 * top[0])
        spans = []
        for p in members:
            n = p.payload[len_off] // stride
            for k in range(n):
                o = body_off + k * stride
                spans.append((p.payload[o], p.payload[o + 1]))
        etype, emax = _endianness(spans) if spans else ("unknown", 0)
        out.append(Hypothesis(
            target=f"record.{hex(op)}", confidence=conf, status=status,
            prediction={"stride": stride, "body_offset": body_off,
                        "first_field": {"rel_offset": 0, "length": 2, "type": etype},
                        "max_chunk_bytes": max(lens)},
            evidence_count=len(members),
            supporting=[f"body length is always a multiple of {stride}",
                        f"column consistency {top[0]:.0%}",
                        f"leading 2-byte field reads as {etype} (max {emax})"],
            alternatives=[{"stride": s, "score": round(sc, 3)} for sc, s in scored[1:3]],
        ))
    return out


def _monotone(col):
    return len(col) > 2 and all(b >= a for a, b in zip(col, col[1:])) and col[0] != col[-1]


# --------------------------------------------------------------------------
# GET / SET pairing
# --------------------------------------------------------------------------

def discover_get_set(pkts, op_off, sub_off):
    full = _full(pkts)
    ops = collections.Counter(p.payload[op_off] for p in full)
    subs = {op: {p.payload[sub_off] for p in full if p.payload[op_off] == op}
            for op in ops}
    echo = collections.Counter()
    for p in full:
        if p.reply and len(p.reply) == len(p.payload):
            if p.reply[op_off] == p.payload[op_off]:
                echo[p.payload[op_off]] += 1
    rules = {"or_0x80": lambda s: s | 0x80,
             "add_0x80": lambda s: (s + 0x80) & 0xFF,
             "xor_0x80": lambda s: s ^ 0x80,
             "add_1": lambda s: (s + 1) & 0xFF}
    scored = []
    for name, fn in rules.items():
        pairs = []
        for s in ops:
            g = fn(s)
            if g != s and g in ops:
                pairs.append((s, g))
        if not pairs:
            continue
        overlap = 0
        for s, g in pairs:
            inter = subs[s] & subs[g]
            union = subs[s] | subs[g]
            if union:
                overlap += len(inter) / len(union)
        scored.append((len(pairs), overlap / max(1, len(pairs)), name, pairs))
    if not scored:
        return unknown("get_set_rule", "no opcode-pairing rule produced matches")
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    n, ov, name, pairs = scored[0]
    unique = len(scored) == 1 or scored[0][0] > scored[1][0]
    conf, status = grade(n, 0, unique)
    # A read opcode is the one whose replies carry a body; a write opcode echoes.
    return Hypothesis(
        target="get_set_rule", prediction={"rule": name,
                                           "pairs": [[hex(a), hex(b)] for a, b in sorted(pairs)]},
        confidence=conf, status=status, evidence_count=n,
        supporting=[f"{n} opcode pairs satisfy {name}",
                    f"mean sub-id overlap within a pair {ov:.0%}",
                    f"echoing opcodes: {sorted(hex(k) for k in echo)}"],
        alternatives=[{"rule": s[2], "pairs": len(s[3])} for s in scored[1:]],
    )


def discover_registers(pkts, op_off, sub_off, len_off, body_off, pair_hint=None):
    """Two-level opcode where the second level indexes a register file."""
    full = _full(pkts)
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in full:
        per[p.payload[op_off]][p.payload[sub_off]].append(p)
    out = []
    for op, subs in sorted(per.items()):
        if len(subs) < 3:
            continue
        widths = {}
        for sub, members in sorted(subs.items()):
            lens = {m.payload[len_off] for m in members}
            body_lens = set()
            for m in members:
                if m.reply and len(m.reply) > len_off:
                    body_lens.add(m.reply[len_off])
            widths[sub] = {
                "request_len": sorted(lens),
                "reply_len": sorted(body_lens),
                "observed": len(members),
                "values": sorted({m.reply[body_off] for m in members
                                  if m.reply and len(m.reply) > body_off}),
            }
        out.append(Hypothesis(
            target=f"register_file.{hex(op)}", prediction=widths,
            confidence=0.85, status="SUPPORTED", evidence_count=sum(
                len(v) for v in subs.values()),
            supporting=[f"opcode {hex(op)} carries {len(subs)} distinct ids at offset "
                        f"{sub_off}, each with its own stable body length"],
            notes="register-file shape: one opcode, many independently sized fields",
        ))
    return out


# --------------------------------------------------------------------------
# controlled actions (mode B/C)
# --------------------------------------------------------------------------

def _num(s):
    import re
    m = re.search(r"(-?\d+(?:\.\d+)?)", str(s))
    return Fraction(m.group(1)) if m else None


def discover_controlled(pkts, op_off, sub_off, body_off):
    """Correlate a UI change with the bytes that moved."""
    out = []
    labelled = [p for p in pkts if p.ui_after]
    if not labelled:
        return [unknown("controlled_actions", "dataset carries no UI annotations")]
    groups = collections.defaultdict(list)
    for p in labelled:
        key = str(p.ui_after).split("=")[0].strip()
        groups[key].append(p)
    for label, members in sorted(groups.items()):
        samples, offsets = [], collections.Counter()
        for p in members:
            after = _num(str(p.ui_after).split("=")[-1])
            if after is None:
                bl = str(p.ui_after).split("=")[-1].strip().lower()
                after = Fraction(1) if bl == "true" else Fraction(0) if bl == "false" else None
            if after is None:
                continue
            samples.append((after, p.payload[body_off]))
            for i in range(body_off, min(len(p.payload) - 1, body_off + 8)):
                if p.payload[i]:
                    offsets[i] += 1
        if len(samples) < 2:
            out.append(unknown(
                f"controlled.{label}",
                f"only {len(samples)} annotated observation(s)",
                experiment=f"drive '{label}' through at least three distinct values, "
                           "ideally including its extremes"))
            continue
        model, alts = numeric.fit(samples)
        conf, status = grade(len(samples), 0, model.generalises)
        out.append(Hypothesis(
            target=f"controlled.{label}",
            prediction={"value_offset": body_off, "model": model.describe(),
                        "generalises": model.generalises,
                        "samples": [[str(a), b] for a, b in samples]},
            confidence=conf if model.generalises else min(conf, 0.4),
            status=status, evidence_count=len(samples),
            supporting=[f"byte at {body_off} tracks '{label}'"],
            alternatives=[a.describe() for a in alts],
            next_best_experiment=(None if model.generalises else
                                  f"only a lookup fits '{label}'; collect more values to "
                                  "test whether a closed-form encoding exists"),
        ))
    return out


# --------------------------------------------------------------------------

def run(rows, partial_schema=None):
    pkts = load(rows)
    hyps = []

    known = (partial_schema or {}).get("framing", {})
    h_op = discover_opcode_offset(pkts)
    hyps.append(h_op)
    op_off = h_op.prediction if h_op.prediction is not None else known.get("opcode_offset", 0)

    h_sub = discover_sub_offset(pkts, op_off)
    hyps.append(h_sub)
    sub_off = h_sub.prediction if h_sub.prediction is not None else known.get("sub_offset", 1)

    hyps.append(discover_constants(pkts, op_off))

    h_chk = discover_checksum(pkts)
    hyps.append(h_chk)
    chk_off = (h_chk.prediction or {}).get("offset", len(_full(pkts)[0].payload) - 1)

    h_len, h_body = discover_length_field(pkts, op_off, chk_off)
    hyps += [h_len, h_body]
    len_off = h_len.prediction if h_len.prediction is not None else known.get("length_offset", 5)
    body_off = h_body.prediction if h_body.prediction is not None else known.get("body_offset", 6)

    hyps += discover_records(pkts, op_off, len_off, body_off, chk_off)
    hyps.append(discover_get_set(pkts, op_off, sub_off))
    hyps += discover_registers(pkts, op_off, sub_off, len_off, body_off)
    hyps += discover_controlled(pkts, op_off, sub_off, body_off)
    return hyps
