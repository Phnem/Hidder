"""Numeric model fitting for protocol inference.

Given observed (ui_value, raw_value) pairs, find the SIMPLEST closed-form model
that explains every one of them.  Simplicity order, cheapest first:

    identity  <  affine  <  modular  <  piecewise powers-of-two  <  lookup

A lookup table always "fits", so it is ranked last and marked as
non-generalising: a model that cannot predict a held-out point is reported as
such rather than being allowed to score.
"""
from __future__ import annotations

import math
from fractions import Fraction


class Model:
    kind = "none"
    generalises = False

    def predict(self, x):
        raise NotImplementedError

    def invert(self, raw):
        raise NotImplementedError

    def describe(self):
        return self.kind


class Identity(Model):
    kind = "identity"
    generalises = True

    def predict(self, x):
        return x

    def invert(self, raw):
        return raw

    def describe(self):
        return "raw = x"


class Affine(Model):
    kind = "affine"
    generalises = True

    def __init__(self, a: Fraction, b: Fraction):
        self.a, self.b = a, b

    def predict(self, x):
        return self.a * Fraction(str(x)) + self.b

    def invert(self, raw):
        return (Fraction(raw) - self.b) / self.a

    def describe(self):
        if self.b == 0:
            return f"raw = {self.a} * x"
        return f"raw = {self.a} * x + {self.b}"


class Pow2Ladder(Model):
    """UI values form a geometric series; raw is a small integer index into it.

    Write p = log2(value / base), so p is the rung of the ladder.  Then the raw
    encoding is affine in p, `raw = a*p + c` with a = +/-1, and real firmwares
    often use two or three such ladders side by side (AULA's polling enum uses
    exactly two).  Grouping points by the invariant `c = raw - a*p` recovers the
    ladders directly and, unlike run-detection, tolerates gaps -- which matters,
    because leave-one-out testing deliberately punches gaps in the data.

    Inversion is deliberately allowed to fail.  When two ladders both explain a
    raw value, the model returns None and exposes both readings as alternatives
    rather than picking one.
    """

    kind = "pow2_ladder"
    generalises = True

    def __init__(self, base, a, classes, p_lo, p_hi):
        self.base = Fraction(str(base))
        self.a = a
        self.classes = sorted(classes)
        self.p_lo, self.p_hi = p_lo, p_hi
        self.last_alternatives = []

    def _p_for_raw(self, raw, strict):
        out = []
        # Strict mode confines p to the rungs actually observed and is used when
        # checking the fit against its own data.  Non-strict allows one rung of
        # slack on each side, which is what a genuinely unseen value needs.
        lo, hi = (self.p_lo, self.p_hi) if strict else (self.p_lo - 1, self.p_hi + 1)
        for c in self.classes:
            p = (raw - c) / self.a
            if p != int(p):
                continue
            p = int(p)
            if not (lo <= p <= hi):
                continue
            val = self.base * Fraction(2) ** p
            # Device-reported quantities in these ladders are integers; a rung
            # that would require a fractional value is not a real rung.
            if val.denominator != 1:
                continue
            out.append((p, val))
        return out

    def invert(self, raw, strict=False):
        cands = self._p_for_raw(raw, strict)
        vals = {v for _, v in cands}
        self.last_alternatives = sorted(vals)
        if len(vals) == 1:
            return cands[0][1]
        return None

    def predict(self, x):
        r = Fraction(str(x)) / self.base
        if not _is_pow2(r):
            return None
        p = int(round(math.log2(float(r))))
        for c in self.classes:
            if self.p_lo <= p <= self.p_hi:
                return self.a * p + c
        return None

    def describe(self):
        return (f"geometric ladder base={self.base}: value = base*2^p, "
                f"raw = {self.a:+d}*p + c for c in {self.classes} "
                f"(p observed over [{self.p_lo},{self.p_hi}])")


class Lookup(Model):
    kind = "lookup"
    generalises = False

    def __init__(self, table):
        self.table = dict(table)
        self.rev = {v: k for k, v in self.table.items()}

    def predict(self, x):
        return self.table.get(x)

    def invert(self, raw):
        return self.rev.get(raw)

    def describe(self):
        return f"lookup table with {len(self.table)} entries (does not generalise)"


def _is_pow2(fr: Fraction) -> bool:
    if fr <= 0:
        return False
    n, d = fr.numerator, fr.denominator
    return (n & (n - 1)) == 0 and (d & (d - 1)) == 0


def _fit_affine(pairs):
    """Exact affine fit through the data, if one exists."""
    if len(pairs) < 2:
        return None
    (x0, r0), (x1, r1) = pairs[0], pairs[1]
    if Fraction(str(x1)) == Fraction(str(x0)):
        return None
    a = (Fraction(r1) - Fraction(r0)) / (Fraction(str(x1)) - Fraction(str(x0)))
    if a == 0:
        return None
    b = Fraction(r0) - a * Fraction(str(x0))
    m = Affine(a, b)
    for x, r in pairs:
        if m.predict(x) != Fraction(r):
            return None
    return m


def _fit_pow2(pairs):
    """Fit a geometric ladder: value = base*2^p, raw affine in p."""
    if len(pairs) < 4:
        return None
    base = min(Fraction(str(x)) for x, _ in pairs)
    ps = {}
    for x, r in pairs:
        ratio = Fraction(str(x)) / base
        if not _is_pow2(ratio) or ratio.denominator != 1:
            return None
        ps[r] = int(round(math.log2(float(ratio))))
    p_lo, p_hi = min(ps.values()), max(ps.values())
    best = None
    for a in (-1, 1):
        classes = {}
        for raw, p in ps.items():
            classes.setdefault(raw - a * p, []).append(raw)
        # Too many ladders means we have not found structure, just noise.
        if len(classes) > 3 or any(len(v) < 2 for v in classes.values()):
            continue
        m = Pow2Ladder(base, a, classes.keys(), p_lo, p_hi)
        if all(m.invert(r, strict=True) == Fraction(str(x)) for x, r in pairs):
            if best is None or len(classes) < len(best.classes):
                best = m
    return best


def fit(pairs):
    """Return (model, alternatives) for the observed (ui, raw) pairs."""
    pairs = [(x, int(r)) for x, r in pairs]
    if not pairs:
        return None, []
    candidates = []
    ident = Identity()
    if all(Fraction(str(x)) == Fraction(r) for x, r in pairs):
        candidates.append(ident)
    aff = _fit_affine(pairs)
    if aff is not None and not (aff.a == 1 and aff.b == 0):
        candidates.append(aff)
    p2 = _fit_pow2(pairs)
    if p2 is not None:
        candidates.append(p2)
    candidates.append(Lookup({x: r for x, r in pairs}))
    return candidates[0], candidates[1:]


def leave_one_out(pairs):
    """Honest generalisation test: refit without each point, then predict it."""
    pairs = [(x, int(r)) for x, r in pairs]
    results = []
    for i in range(len(pairs)):
        held = pairs[i]
        rest = pairs[:i] + pairs[i + 1:]
        model, _ = fit(rest)
        if model is None or not model.generalises:
            results.append((held, None, False))
            continue
        got = model.invert(held[1])
        ok = got is not None and Fraction(str(got)) == Fraction(str(held[0]))
        results.append((held, got, ok))
    return results
