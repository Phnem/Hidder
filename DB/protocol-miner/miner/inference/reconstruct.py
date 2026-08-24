"""Rebuild whole packets from an inferred schema.

The hardest question a protocol-inference system can be asked is not "which
byte is the key id" but "emit a frame this device will accept".  This module
answers it using ONLY the frozen hypotheses: framing offsets, the constant
bytes, the length field and the check formula.  Nothing here may consult
ground truth.

Any byte the schema cannot account for is returned as None, so the scorer can
tell a wrong byte apart from an honestly unknown one.
"""
from __future__ import annotations


class Schema:
    def __init__(self, hyps, width=63):
        self.width = width
        self.by_target = {h["target"]: h for h in hyps}
        f = self.by_target
        self.op_off = self._pred("framing.opcode_offset")
        self.sub_off = self._pred("framing.sub_offset")
        self.len_off = self._pred("framing.length_offset")
        self.body_off = self._pred("framing.body_offset")
        consts = self._pred("framing.constant_offsets") or {}
        self.constants = {int(k): v for k, v in consts.items()}
        self.checksum = self._pred("checksum")

    def _pred(self, target):
        h = self.by_target.get(target)
        return h["prediction"] if h else None

    def build(self, opcode, sub, body):
        """Return (bytes_or_None per position, list of unknown offsets)."""
        out = [None] * self.width
        for off, val in self.constants.items():
            if off < self.width:
                out[off] = val
        if self.op_off is not None:
            out[self.op_off] = opcode
        if self.sub_off is not None:
            out[self.sub_off] = sub
        if self.len_off is not None:
            out[self.len_off] = len(body)
        if self.body_off is not None:
            for i, b in enumerate(body):
                if self.body_off + i < self.width:
                    out[self.body_off + i] = b
        # Padding: every position after the body that the schema did not claim
        # and that the constant scan found to be zero-valued stays zero.  If the
        # constant scan never saw it, leave it unknown rather than assuming.
        chk = self.checksum or {}
        cpos = chk.get("offset")
        if self.body_off is not None:
            for i in range(self.body_off + len(body), self.width):
                if i == cpos:
                    continue
                if out[i] is None:
                    out[i] = 0
        if cpos is not None and chk.get("kind") == "affine_over_sum":
            lo, hi = chk["range"]
            if all(out[i] is not None for i in range(lo, hi)):
                s = sum(out[lo:hi]) & 0xFF
                out[cpos] = (chk["a"] * s + chk["b"]) % 256
        unknown = [i for i, v in enumerate(out) if v is None]
        return out, unknown


def compare(built, truth_hex):
    truth = bytes.fromhex(truth_hex)
    n = min(len(built), len(truth))
    correct = wrong = unk = 0
    bad_offsets = []
    for i in range(n):
        if built[i] is None:
            unk += 1
        elif built[i] == truth[i]:
            correct += 1
        else:
            wrong += 1
            bad_offsets.append({"offset": i, "predicted": built[i], "actual": truth[i]})
    return {
        "total": n, "correct": correct, "wrong": wrong, "unknown": unk,
        "exact": wrong == 0 and unk == 0,
        "byte_accuracy": round(correct / n, 4) if n else 0.0,
        "unknown_rate": round(unk / n, 4) if n else 0.0,
        "mismatches": bad_offsets[:8],
    }
