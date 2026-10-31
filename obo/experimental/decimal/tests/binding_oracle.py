#!/usr/bin/env python3
"""MODEL EXPLORATION ONLY: exact decimal(p,s) binding arithmetic.

This is not an approved language type, compiler rule, database adapter or
rounding default.  It exists to check a mathematical contract for the later
database discussion:

    a finite value v fits decimal(p,s) exactly iff v * 10**s is an integer and
    abs(v * 10**s) < 10**p.

Negative scale and s > p are accepted by the mathematical formula.  Vendor
metadata/rules (SQL Server's s in 0..p, Oracle's p/s ranges, PostgreSQL's
typmod limits) are deliberately separate, and no live database was used.
All values are Fractions/integers; there is no binary float.
"""
from __future__ import annotations

import random
from fractions import Fraction
from typing import Optional

MAX_COEFF = 10 ** 38 - 1
MAX_DSCALE = 65535

OK = 0
OVERFLOW = 1
UNSUPPORTED_EXACT = 2
INEXACT = 3
INVALID = 4
DIV_ZERO = 5
UNSUPPORTED_RANGE = 6
PRECISION_OVERFLOW = 7
EXPONENT_RANGE = 8
DISPLAY_RANGE = 9
BUFFER_SMALL = 10

MODES = {
    "HALF_EVEN": 0, "HALF_AWAY": 1, "HALF_TOWARD_ZERO": 2,
    "TOWARD_ZERO": 3, "AWAY": 4, "FLOOR": 5, "CEILING": 6,
    "UNNECESSARY": 7,
}


def value(sign: int, coeff: int, exp: int) -> Fraction:
    if sign not in (0, 1):
        raise ValueError("sign")
    if coeff < 0:
        raise ValueError("coefficient")
    v = Fraction(coeff)
    if exp >= 0:
        v *= 10 ** exp
    else:
        v /= 10 ** (-exp)
    return -v if sign else v


def scaled_integer(v: Fraction, scale: int) -> Optional[int]:
    """Return v*10**scale if it is an exact integer, else None."""
    if scale >= 0:
        scaled = v * (10 ** scale)
    else:
        scaled = v / (10 ** (-scale))
    return scaled.numerator if scaled.denominator == 1 else None


def exact_fits(v: Fraction, precision: int, scale: int) -> bool:
    """Exact (no rounding) fit under the stated mathematical contract."""
    if precision < 1:
        return False
    si = scaled_integer(v, scale)
    return si is not None and abs(si) < 10 ** precision


def round_half_away_scaled(v: Fraction, scale: int) -> int:
    """Round v to the target scale, ties away from zero, return the integer."""
    if scale >= 0:
        scaled = v * (10 ** scale)
    else:
        scaled = v / (10 ** (-scale))
    negative = scaled < 0
    magnitude = -scaled if negative else scaled
    q, r = divmod(magnitude.numerator, magnitude.denominator)
    if 2 * r >= magnitude.denominator:
        q += 1
    return -q if negative else q


def rounded_fits(v: Fraction, precision: int, scale: int) -> bool:
    """Half-away bind after rounding; catch carry out of precision."""
    if precision < 1:
        return False
    return abs(round_half_away_scaled(v, scale)) < 10 ** precision


def _scaled(v: Fraction, scale: int) -> Fraction:
    return v * (10 ** scale) if scale >= 0 else v / (10 ** (-scale))


def round_scaled(v: Fraction, scale: int, mode: int) -> tuple[int, int]:
    """Return (scaled_integer, status) after one explicit rounding."""
    if mode not in range(8):
        return 0, INVALID
    x = _scaled(v, scale)
    negative = x < 0
    mag = -x if negative else x
    q, r = divmod(mag.numerator, mag.denominator)
    den = mag.denominator

    def inc() -> int:
        if mode == MODES["UNNECESSARY"]:
            return -1 if r else 0
        if mode == MODES["HALF_EVEN"]:
            twice = 2 * r
            return 1 if twice > den or (twice == den and (q & 1)) else 0
        if mode == MODES["HALF_AWAY"]:
            return 1 if 2 * r >= den else 0
        if mode == MODES["HALF_TOWARD_ZERO"]:
            return 1 if 2 * r > den else 0
        if mode == MODES["TOWARD_ZERO"]:
            return 0
        if mode == MODES["AWAY"]:
            return 1 if r else 0
        if mode == MODES["FLOOR"]:
            return 1 if negative and r else 0
        if mode == MODES["CEILING"]:
            return 1 if (not negative) and r else 0
        raise ValueError("mode")

    n = inc()
    if n == -1:
        return 0, INEXACT
    q += n
    return (-q if negative else q), OK


def exact_status(v: Fraction, precision: int, scale: int) -> int:
    if precision < 1:
        return INVALID
    si = scaled_integer(v, scale)
    if si is None:
        return INEXACT
    return OK if abs(si) < 10 ** precision else PRECISION_OVERFLOW


def rounded_result(sign: int, coeff: int, exp: int, precision: int,
                   scale: int, mode: int) -> dict:
    """Model dec_bind_quantize including its private representation limits."""
    if precision < 1 or mode not in range(8):
        return {"status": INVALID, "sign": 0, "coeff": 0, "exp": 0,
                "dscale": 0}
    v = value(sign, coeff, exp)
    if coeff == 0:
        if scale < 0:
            ds = 0
        elif scale > MAX_DSCALE:
            return {"status": UNSUPPORTED_RANGE, "sign": 0, "coeff": 0,
                    "exp": 0, "dscale": 0}
        else:
            ds = scale
        return {"status": OK, "sign": 0, "coeff": 0, "exp": 0, "dscale": ds}
    if scale < -32767 or scale > 32768:
        return {"status": UNSUPPORTED_RANGE, "sign": 0, "coeff": 0,
                "exp": 0, "dscale": 0}

    q, st = round_scaled(v, scale, mode)
    if st != OK:
        return {"status": st, "sign": 0, "coeff": 0, "exp": 0, "dscale": 0}
    if q == 0:
        ds = scale if scale >= 0 else 0
        return {"status": OK, "sign": 0, "coeff": 0, "exp": 0, "dscale": ds}
    aq = abs(q)
    if aq > MAX_COEFF:
        return {"status": OVERFLOW, "sign": 0, "coeff": 0, "exp": 0,
                "dscale": 0}
    if len(str(aq)) > precision:
        return {"status": PRECISION_OVERFLOW, "sign": 0, "coeff": 0,
                "exp": 0, "dscale": 0}
    return {"status": OK, "sign": 1 if q < 0 else 0, "coeff": aq,
            "exp": -scale, "dscale": scale if scale >= 0 else 0}


def build_binding_cases() -> dict:
    cases = []

    def add(sign: int, coeff: int, exp: int, dscale: int, p: int, s: int,
            mode: int) -> None:
        v = value(sign, coeff, exp)
        row = {
            "sign": sign, "coeff": coeff, "exp": exp, "dscale": dscale,
            "p": p, "s": s, "mode": mode,
            "exact_status": exact_status(v, p, s),
            "round": rounded_result(sign, coeff, exp, p, s, mode),
        }
        cases.append(row)

    maxc = MAX_COEFF
    fixed = [
        # 999.995 -> 1000.00 carry must not fit decimal(5,2).
        (0, 999995, -3, 3, 5, 2),
        (1, 999995, -3, 3, 5, 2),
        (0, 999994, -3, 3, 5, 2),
        # redundant trailing zeros are exact at scale 2
        (0, 12300, -4, 4, 5, 2),
        (1, 123000, -5, 5, 5, 2),
        (0, 1234, -3, 3, 5, 2),
        # precision boundaries and zero
        (0, maxc, 0, 0, 38, 0),
        (0, maxc, 0, 0, 37, 0),
        (0, 1, 0, 0, 1, 0),
        (0, 10, 0, 0, 1, 0),
        (0, 0, 0, 65535, 1, 0),
        (0, 0, 0, 0, 1, -1000),
        # negative scale and scale > precision are mathematical shapes only
        (0, 99999, 2, 0, 5, -2),
        (0, 100000, 2, 0, 5, -2),
        (0, 999, -5, 5, 3, 5),
        (0, 1000, -5, 5, 3, 5),
        (1, 15, -1, 1, 2, 0),
        # tiny values exercise the removed-quantum > coefficient-digits path
        (0, 1, -50, 0, 5, 0),
        (1, 1, -50, 0, 5, 0),
        (0, 5, -50, 0, 2, 0),
        (0, 1, -38, 0, 5, 0),
        (0, 1, -39, 0, 5, 0),
        # supported representation extremes
        (0, 1, -32768, 0, 1, 32768),
        (0, 1, 0, 0, 1, 32768),
        (0, 0, 0, 0, 1, 65535),
    ]
    for sign, coeff, exp, ds, p, s in fixed:
        for mode in MODES.values():
            add(sign, coeff, exp, ds, p, s, mode)

    rng = random.Random(0xD1B1D)
    for _ in range(300):
        sign = rng.randrange(2)
        coeff = rng.choice([
            0, 1, 9, 10, 15, 100, 999995, 10 ** 37, maxc,
            rng.randrange(0, maxc + 1),
        ])
        exp = rng.randint(-12, 12)
        ds = rng.choice([0, 1, 2, 3, 10, 38, 32768, 65535])
        p = rng.randint(1, 40)
        s = rng.randint(-6, 10)
        mode = rng.choice(list(MODES.values()))
        add(sign, coeff, exp, ds, p, s, mode)

    return {
        "format": "obo-decimal-binding-oracle-v1",
        "cases": cases,
        "counts": {
            "cases": len(cases),
            "exact_ok": sum(1 for c in cases if c["exact_status"] == OK),
            "round_ok": sum(1 for c in cases if c["round"]["status"] == OK),
            "round_precision": sum(
                1 for c in cases if c["round"]["status"] == PRECISION_OVERFLOW),
        },
    }
