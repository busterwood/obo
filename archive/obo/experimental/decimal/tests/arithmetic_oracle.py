#!/usr/bin/env python3
"""Independent exact-rational oracle for the D2 private arithmetic contract.

This module is deliberately separate from decimal_oracle.py's D1 model.  It
uses Fraction for exact values and Python's integer arithmetic for the one
final rounding step.  Python decimal is not used here: no default context,
precision or rounding rule can leak into expected values.  No binary float is
used.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

# The private exponent range is int16, so exact Fractions can hold about
# 65,535 decimal digits.  This is still test-only integer arithmetic.
try:
    sys.set_int_max_str_digits(200000)
except AttributeError:  # pragma: no cover - older Python
    pass

MAX_COEFF = 10 ** 38 - 1
MODES = {
    "HALF_EVEN": 0, "HALF_AWAY": 1, "HALF_TOWARD_ZERO": 2,
    "TOWARD_ZERO": 3, "AWAY": 4, "FLOOR": 5, "CEILING": 6,
    "UNNECESSARY": 7,
}
MODE_NAMES = {v: k for k, v in MODES.items()}
OK = 0
OVERFLOW = 1
INEXACT = 3
INVALID = 4
DIV_ZERO = 5
UNSUPPORTED_RANGE = 6


@dataclass(frozen=True)
class Dec:
    sign: int
    coeff: int
    exp: int
    dscale: int = 0

    def parts(self) -> dict:
        return {"sign": self.sign, "coeff": self.coeff, "exp": self.exp,
                "dscale": self.dscale}


def zero(dscale: int = 0) -> Dec:
    return Dec(0, 0, 0, dscale)


def value(a: Dec) -> Fraction:
    v = Fraction(a.coeff)
    v = v * 10 ** a.exp if a.exp >= 0 else v / 10 ** (-a.exp)
    return -v if a.sign else v


def _digits(c: int) -> int:
    return len(str(c)) if c else 0


def _adjusted(v: Fraction) -> int:
    """floor(log10(abs(v))) for a nonzero Fraction, without float."""
    n, d = v.numerator, v.denominator
    e = len(str(n)) - len(str(d))
    while v < Fraction(10) ** e:
        e -= 1
    while v >= Fraction(10) ** (e + 1):
        e += 1
    return e


def _inc(q: int, rem: Fraction, mode: int, sign: int) -> int:
    """Decide an increment after truncating at a power-of-ten grid."""
    if mode == MODES["UNNECESSARY"]:
        return -1 if rem else 0
    twice = 2 * rem
    if mode == MODES["HALF_EVEN"]:
        return 1 if twice > 1 or (twice == 1 and (q & 1)) else 0
    if mode == MODES["HALF_AWAY"]:
        return 1 if twice >= 1 else 0
    if mode == MODES["HALF_TOWARD_ZERO"]:
        return 1 if twice > 1 else 0
    if mode == MODES["TOWARD_ZERO"]:
        return 0
    if mode == MODES["AWAY"]:
        return 1 if rem else 0
    if mode == MODES["FLOOR"]:
        return 1 if sign == 1 and rem else 0
    if mode == MODES["CEILING"]:
        return 1 if sign == 0 and rem else 0
    raise ValueError("mode")


def _round_grid(v: Fraction, te: int, mode: int, sign: int):
    """Round |v| to an integer coefficient at exponent te.
    Returns (coeff, status) where status is OK/INEXACT or (None, -1) for an
    unrepresentable coefficient."""
    scaled = abs(v) / (10 ** te) if te >= 0 else abs(v) * (10 ** (-te))
    q = scaled.numerator // scaled.denominator
    rem = scaled - q
    inc = _inc(q, rem, mode, sign)
    if inc == -1:
        return None, INEXACT
    q += inc
    return q, OK


def _exact_dec(v: Fraction, dscale: int) -> tuple[int, Optional[Dec]]:
    """Exact finite decimal representation, or (status, None)."""
    if v == 0:
        return OK, zero(dscale)
    sign = 1 if v < 0 else 0
    a = abs(v)
    num, den = a.numerator, a.denominator
    # Fast valuations: an int16 exponent can make a plain factor loop
    # quadratic (65,535 divisions of a 65,535-digit integer).
    lowbit = den & -den
    p2 = lowbit.bit_length() - 1 if lowbit else 0
    den >>= p2
    p5 = 0
    if den % 5 == 0:
        lo, hi = 0, den.bit_length()
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if den % (5 ** mid) == 0:
                lo = mid
            else:
                hi = mid - 1
        p5 = lo
        den //= 5 ** p5
    if den != 1:
        return INEXACT, None
    m = max(p2, p5)
    c = num * (2 ** (m - p2)) * (5 ** (m - p5))
    exp = -m
    if c:
        # Count trailing zeros once; a per-digit loop is quadratic for the
        # int16 exponent extremes used by the edge/property probes.
        text = str(c)
        trailing = len(text) - len(text.rstrip("0"))
        if trailing:
            c //= 10 ** trailing
            exp += trailing
    # Parser-style upper rescue: put back only trailing zeros needed to fit
    # int16, never significant nonzeros.
    while exp > 32767 and _digits(c) < 38:
        c *= 10
        exp -= 1
    if _digits(c) > 38:
        return INEXACT, None
    if exp < -32768 or exp > 32767:
        return OVERFLOW, None
    return OK, Dec(sign, c, exp, dscale)


def add_exact(a: Dec, b: Dec, dscale: int) -> tuple[int, Optional[Dec]]:
    return _exact_dec(value(a) + value(b), dscale)


def sub_exact(a: Dec, b: Dec, dscale: int) -> tuple[int, Optional[Dec]]:
    return _exact_dec(value(a) - value(b), dscale)


def mul_exact(a: Dec, b: Dec, dscale: int) -> tuple[int, Optional[Dec]]:
    return _exact_dec(value(a) * value(b), dscale)


def div_exact(a: Dec, b: Dec, dscale: int) -> tuple[int, Optional[Dec]]:
    if b.coeff == 0:
        return DIV_ZERO, None
    return _exact_dec(value(a) / value(b), dscale)


def rem_trunc(a: Dec, b: Dec, dscale: int) -> tuple[int, Optional[Dec]]:
    if b.coeff == 0:
        return DIV_ZERO, None
    va, vb = value(a), value(b)
    q = abs(va) // abs(vb)
    if (va < 0) != (vb < 0):
        q = -q
    r = va - q * vb
    return _exact_dec(r, dscale)


def add_rounded(a: Dec, b: Dec, mode: int, dscale: int) -> tuple[int, Optional[Dec]]:
    return round_precision(value(a) + value(b), 38, mode, dscale)


def sub_rounded(a: Dec, b: Dec, mode: int, dscale: int) -> tuple[int, Optional[Dec]]:
    return round_precision(value(a) - value(b), 38, mode, dscale)


def mul_rounded(a: Dec, b: Dec, mode: int, dscale: int) -> tuple[int, Optional[Dec]]:
    return round_precision(value(a) * value(b), 38, mode, dscale)


def round_precision(v: Fraction, precision: int, mode: int,
                    dscale: int) -> tuple[int, Optional[Dec]]:
    if v == 0:
        return OK, zero(dscale)
    if precision < 1 or precision > 38:
        return INVALID, None
    sign = 1 if v < 0 else 0
    e = _adjusted(abs(v))
    te = e - precision + 1
    q, st = _round_grid(v, te, mode, sign)
    if st != OK:
        return st, None
    if q == 10 ** precision:
        q //= 10
        te += 1
    # A rounded coefficient may carry trailing zeros that let the same value
    # use a coarser in-range exponent.  This is value-preserving, not extra
    # precision: strip only as far as needed for int16 range.
    while te < -32768 and q != 0 and q % 10 == 0:
        q //= 10
        te += 1
    while te > 32767 and q != 0 and _digits(q) < 38:
        q *= 10
        te -= 1
    if q > MAX_COEFF:
        return OVERFLOW, None
    if q == 0:
        return OK, zero(dscale)
    if te < -32768 or te > 32767:
        return OVERFLOW, None
    return OK, Dec(sign, q, te, dscale)


def div_scale(a: Dec, b: Dec, scale: int, mode: int,
              dscale: int) -> tuple[int, Optional[Dec]]:
    if b.coeff == 0:
        return DIV_ZERO, None
    if scale < -32767 or scale > 32768:
        return UNSUPPORTED_RANGE, None
    v = value(a) / value(b)
    if v == 0:
        return OK, zero(dscale)
    sign = 1 if v < 0 else 0
    te = -scale
    q, st = _round_grid(v, te, mode, sign)
    if st != OK:
        # Overflow takes precedence when even truncation cannot fit.
        trunc = abs(v) / (10 ** te) if te >= 0 else abs(v) * (10 ** (-te))
        tq = trunc.numerator // trunc.denominator
        if tq > MAX_COEFF or (tq != 0 and (te < -32768 or te > 32767)):
            return OVERFLOW, None
        return st, None
    if q == 10 ** 38:
        q //= 10
        te += 1
    if q > MAX_COEFF:
        return OVERFLOW, None
    if q == 0:
        return OK, zero(dscale)
    if te < -32768 or te > 32767:
        return OVERFLOW, None
    return OK, Dec(sign, q, te, dscale)


def div_precision(a: Dec, b: Dec, precision: int, mode: int,
                  dscale: int) -> tuple[int, Optional[Dec]]:
    if b.coeff == 0:
        return DIV_ZERO, None
    if precision < 1 or precision > 38:
        return INVALID, None
    return round_precision(value(a) / value(b), precision, mode, dscale)
