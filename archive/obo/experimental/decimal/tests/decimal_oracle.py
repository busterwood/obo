#!/usr/bin/env python3
"""Independent integer model for the dec24 prototype.

The model is deliberately not Python decimal's context semantics: it keeps
sign, coefficient, exponent and display scale explicitly, rounds to 38
significant digits only at the operation's final step, and uses an explicit
mode argument.  Python decimal is used only as a secondary cross-check under
an explicitly selected context, never as the language authority.
"""
from __future__ import annotations

import decimal
import json
import random
from dataclasses import dataclass, asdict
from fractions import Fraction
from typing import Optional

import arithmetic_oracle as _ar

MAX_COEFF = 10 ** 38 - 1
MAX_DSCALE = 65535            # uint16 layout maximum, including zero dscale
NONZERO_QUANTIZE_MAX_DSCALE = 32768  # strict quantize exp=-target fits int16

OK = 0
OVERFLOW = 1
UNSUPPORTED_EXACT = 2
INEXACT = 3
INVALID = 4
DIV_ZERO = 5
UNSUPPORTED_RANGE = 6

STATUS = {
    OK: "OK", OVERFLOW: "OVERFLOW", UNSUPPORTED_EXACT: "UNSUPPORTED_EXACT",
    INEXACT: "INEXACT", INVALID: "INVALID", DIV_ZERO: "DIV_ZERO",
    UNSUPPORTED_RANGE: "UNSUPPORTED_RANGE",
}

MODES = {
    "HALF_EVEN": 0, "HALF_AWAY": 1, "HALF_TOWARD_ZERO": 2,
    "TOWARD_ZERO": 3, "AWAY": 4, "FLOOR": 5, "CEILING": 6,
    "UNNECESSARY": 7,
}
MODE_NAMES = {v: k for k, v in MODES.items()}


@dataclass(frozen=True)
class Dec:
    sign: int
    coeff: int
    exp: int
    dscale: int

    def parts(self) -> dict:
        return {
            "sign": self.sign,
            "lo": self.coeff & ((1 << 64) - 1),
            "hi": self.coeff >> 64,
            "exp": self.exp,
            "dscale": self.dscale,
        }


def make(sign: int, coeff: int, exp: int, dscale: int) -> Dec:
    if sign not in (0, 1):
        raise ValueError("sign")
    if coeff < 0 or coeff > MAX_COEFF:
        raise ValueError("coefficient out of range")
    if exp < -32768 or exp > 32767:
        raise ValueError("exponent out of range")
    if coeff == 0:
        sign = 0
        exp = 0
    return Dec(sign, coeff, exp, dscale)


def zero(dscale: int = 0) -> Dec:
    return Dec(0, 0, 0, dscale)


def digits(c: int) -> int:
    return 0 if c == 0 else len(str(c))


def inc_after_round(q: int, rem: int, divisor: int, sign: int,
                    mode: int) -> int:
    """Whether to increment q after dividing magnitude by divisor."""
    if mode == MODES["UNNECESSARY"]:
        return -1 if rem else 0
    if mode == MODES["HALF_EVEN"]:
        twice = 2 * rem
        return 1 if twice > divisor or (twice == divisor and (q & 1)) else 0
    if mode == MODES["HALF_AWAY"]:
        return 1 if 2 * rem >= divisor else 0
    if mode == MODES["HALF_TOWARD_ZERO"]:
        return 1 if 2 * rem > divisor else 0
    if mode == MODES["TOWARD_ZERO"]:
        return 0
    if mode == MODES["AWAY"]:
        return 1 if rem else 0
    if mode == MODES["FLOOR"]:
        return 1 if sign == 1 and rem else 0   # sign 1 is negative
    if mode == MODES["CEILING"]:
        return 1 if sign == 0 and rem else 0   # sign 0 is positive
    raise ValueError("mode")


def round_to_38(c: int, sign: int, mode: int) -> tuple[int, int, int]:
    """Return (coefficient, places_removed, status)."""
    if c == 0:
        return 0, 0, OK
    d = digits(c)
    if d <= 38:
        return c, 0, OK
    k = d - 38
    divisor = 10 ** k
    q, rem = divmod(c, divisor)
    inc = inc_after_round(q, rem, divisor, sign, mode)
    if inc == -1:
        if rem:
            return 0, 0, INEXACT
        inc = 0
    q += inc
    if q == 10 ** 38:
        # Prototype choice only: one divide-by-ten, exponent +1.
        q //= 10
        k += 1
    if q > MAX_COEFF:
        return 0, 0, OVERFLOW
    return q, k, OK


def _rescue_dec(sign: int, c: int, exp: int, ds: int) -> tuple[Dec, int]:
    """Apply the D2 parser-style exponent rescue to a rounded/exact value."""
    if c == 0:
        return zero(ds), OK
    while exp < -32768 and c % 10 == 0:
        c //= 10
        exp += 1
    while exp > 32767 and digits(c) < 38:
        c *= 10
        exp -= 1
    if exp < -32768 or exp > 32767 or c > MAX_COEFF:
        return zero(0), OVERFLOW
    return make(sign, c, exp, ds), OK


def _rounded_wide(a: Dec, b: Dec, mode: int, ds: int) -> tuple[Dec, int]:
    """D2 sticky/rounded fallback for a result the exact accumulator cannot
    represent.  The independent D2 rational model supplies the 38-digit
    nearest/directed value; this is still not the C code."""
    st, r = _ar.add_rounded(_ar.Dec(a.sign, a.coeff, a.exp, a.dscale),
                            _ar.Dec(b.sign, b.coeff, b.exp, b.dscale),
                            mode, ds)
    if st != _ar.OK:
        return zero(0), st
    return make(r.sign, r.coeff, r.exp, r.dscale), st


def add(a: Dec, b: Dec, mode: int) -> tuple[Dec, int]:
    if mode not in MODE_NAMES:
        return zero(0), INVALID
    ds = max(a.dscale, b.dscale)
    if a.coeff == 0 and b.coeff == 0:
        return zero(ds), OK
    if a.coeff == 0:
        return make(b.sign, b.coeff, b.exp, ds), OK
    if b.coeff == 0:
        return make(a.sign, a.coeff, a.exp, ds), OK
    e = min(a.exp, b.exp)
    sha, shb = a.exp - e, b.exp - e
    if sha > 76 or shb > 76:
        return _rounded_wide(a, b, mode, ds)
    ma, mb = a.coeff * 10 ** sha, b.coeff * 10 ** shb
    if ma.bit_length() > 256 or mb.bit_length() > 256:
        return _rounded_wide(a, b, mode, ds)
    if a.sign == b.sign:
        m, sign = ma + mb, a.sign
        if m.bit_length() > 256:
            return _rounded_wide(a, b, mode, ds)
    else:
        if ma == mb:
            return zero(ds), OK
        if ma > mb:
            m, sign = ma - mb, a.sign
        else:
            m, sign = mb - ma, b.sign
    if m == 0:
        return zero(ds), OK
    q, k, st = round_to_38(m, sign, mode)
    if st != OK:
        return zero(0), st
    return _rescue_dec(sign, q, e + k, ds)


def sub(a: Dec, b: Dec, mode: int) -> tuple[Dec, int]:
    if mode not in MODE_NAMES:
        return zero(0), INVALID
    nb = make(b.sign ^ 1 if b.coeff else 0, b.coeff, b.exp, b.dscale)
    return add(a, nb, mode)


def mul(a: Dec, b: Dec, mode: int) -> tuple[Dec, int]:
    if mode not in MODE_NAMES:
        return zero(0), INVALID
    ds = min(a.dscale + b.dscale, 65535)
    if a.coeff == 0 or b.coeff == 0:
        return zero(ds), OK
    p = a.coeff * b.coeff
    sign = a.sign ^ b.sign
    q, k, st = round_to_38(p, sign, mode)
    if st != OK:
        return zero(0), st
    return _rescue_dec(sign, q, a.exp + b.exp + k, ds)


def quantize(a: Dec, target: int, mode: int) -> tuple[Dec, int]:
    if mode not in MODE_NAMES:
        return zero(0), INVALID
    if target < 0 or target > MAX_DSCALE:
        return zero(0), INVALID
    # Zero's display scale is independent metadata, so every uint16 target is
    # representable.  Strict nonzero quantize below still uses exp = -target.
    if a.coeff == 0:
        return zero(target), OK
    if target > NONZERO_QUANTIZE_MAX_DSCALE:
        return zero(0), UNSUPPORTED_RANGE
    shift = a.exp + target
    c = a.coeff
    if shift >= 0:
        if shift > 76:
            return zero(0), OVERFLOW
        c *= 10 ** shift
        if c > MAX_COEFF:
            return zero(0), OVERFLOW
    else:
        k = -shift
        divisor = 10 ** k
        q, rem = divmod(c, divisor)
        inc = inc_after_round(q, rem, divisor, a.sign, mode)
        if inc == -1:
            if rem:
                return zero(0), INEXACT
            inc = 0
        q += inc
        if q == 0:
            return zero(target), OK
        if q > MAX_COEFF:
            return zero(0), OVERFLOW
        c = q
    return make(a.sign, c, -target, target), OK


def exact_value(a: Dec) -> Fraction:
    """Exact mathematical value as a Fraction; no binary float is used."""
    v = Fraction(a.coeff)
    if a.exp >= 0:
        v *= 10 ** a.exp
    else:
        v /= 10 ** (-a.exp)
    return -v if a.sign else v


def cmp(a: Dec, b: Dec) -> int:
    """Independent exact-rational ordering.

    Deliberately not a reimplementation of dec_cmp's magnitude/exponent
    branches: compare the exact Fraction values and derive -1/0/1 from the
    mathematical relation.  This keeps the oracle independent of the C bug.
    """
    va, vb = exact_value(a), exact_value(b)
    return (va > vb) - (va < vb)


def equal(a: Dec, b: Dec) -> bool:
    return cmp(a, b) == 0


def to_decimal(a: Dec) -> decimal.Decimal:
    d = decimal.Decimal(a.coeff).scaleb(a.exp)
    return -d if a.sign else d


# ------------------------- deterministic vectors -------------------------

SEED = 0xDEC24


def gen_coeff(rng: random.Random) -> int:
    choice = rng.randrange(10)
    if choice == 0:
        return 0
    if choice == 1:
        return MAX_COEFF
    if choice == 2:
        return 10 ** rng.randrange(1, 38)
    return rng.randrange(1, MAX_COEFF + 1)


def gen_operand(rng: random.Random, exp_lo=-40, exp_hi=40) -> Dec:
    return make(
        rng.randrange(2), gen_coeff(rng),
        rng.randrange(exp_lo, exp_hi + 1),
        rng.randrange(0, 39),
    )


def build_vectors() -> dict:
    rng = random.Random(SEED)
    add_v, sub_v, mul_v, quant_v = [], [], [], []
    # Fixed semantic boundaries.
    fixed = [
        (make(0, 150, -2, 2), make(1, 150, -2, 2), "1.50 +/- 1.50"),
        (make(0, 150, -2, 2), make(0, 15, -1, 1), "1.50 + 1.5"),
        (make(0, MAX_COEFF, 0, 0), make(0, 0, 0, 0), "max + zero"),
        (make(0, 5, -1, 1), make(0, 5, -1, 1), "0.5 + 0.5"),
    ]
    for a, b, _ in fixed:
        for mode in MODES.values():
            r, st = add(a, b, mode)
            add_v.append((a, b, mode, r, st))
    for _ in range(120):
        a, b = gen_operand(rng), gen_operand(rng)
        # Keep alignment inside the prototype's exact 76-digit guard.
        b = make(b.sign, b.coeff, max(b.exp, a.exp - 38), b.dscale)
        mode = rng.choice(list(MODES.values()))
        r, st = add(a, b, mode)
        add_v.append((a, b, mode, r, st))
        r, st = sub(a, b, mode)
        sub_v.append((a, b, mode, r, st))
    for _ in range(120):
        a, b = gen_operand(rng, -20, 20), gen_operand(rng, -20, 20)
        mode = rng.choice(list(MODES.values()))
        r, st = mul(a, b, mode)
        mul_v.append((a, b, mode, r, st))
    for _ in range(160):
        a = gen_operand(rng, -20, 20)
        target = rng.choice([0, 1, 2, 3, 6, 10, 18, 38, 39, 100, 32768])
        mode = rng.choice(list(MODES.values()))
        r, st = quantize(a, target, mode)
        quant_v.append((a, target, mode, r, st))
    # Explicit midpoint cases for every mode.
    for target in (0, 1, 2):
        for mode in MODES.values():
            a = make(0, 15, -1, 1)
            r, st = quantize(a, target, mode)
            quant_v.append((a, target, mode, r, st))

    # Limb/primitive vectors.
    mul64_v, mul128_v, div256_v, div128_v = [], [], [], []
    for _ in range(100):
        a = rng.getrandbits(64)
        b = rng.getrandbits(64)
        p = a * b
        mul64_v.append((a, b, p & ((1 << 64) - 1), p >> 64))
    for _ in range(100):
        alo, ahi = rng.getrandbits(64), rng.getrandbits(64)
        blo, bhi = rng.getrandbits(64), rng.getrandbits(64)
        p = ((ahi << 64) | alo) * ((bhi << 64) | blo)
        mul128_v.append((alo, ahi, blo, bhi,
                         p & ((1 << 64) - 1),
                         (p >> 64) & ((1 << 64) - 1),
                         (p >> 128) & ((1 << 64) - 1),
                         (p >> 192) & ((1 << 64) - 1)))
    for _ in range(100):
        n = rng.getrandbits(256)
        d = rng.randrange(1, 1 << 64)
        q, r = divmod(n, d)
        div256_v.append(tuple((n >> (64 * i)) & ((1 << 64) - 1) for i in range(4)) +
                        (d,) +
                        tuple((q >> (64 * i)) & ((1 << 64) - 1) for i in range(4)) +
                        (r,))
    for _ in range(100):
        n = rng.getrandbits(256)
        d = rng.getrandbits(128)
        if d == 0:
            d = 1
        q, r = divmod(n, d)
        div128_v.append(tuple((n >> (64 * i)) & ((1 << 64) - 1) for i in range(4)) +
                        ((d & ((1 << 64) - 1)), d >> 64) +
                        tuple((q >> (64 * i)) & ((1 << 64) - 1) for i in range(4)) +
                        tuple((r >> (64 * i)) & ((1 << 64) - 1) for i in range(4)))

    # Comparison vectors use the independent Fraction-based cmp above.
    cmp_v = []
    def add_cmp(a: Dec, b: Dec) -> None:
        cmp_v.append((a, b, cmp(a, b)))

    def c(sign: int, coeff: int, exp: int, dscale: int = 0) -> Dec:
        return make(sign, coeff, exp, dscale)

    fixed_cmp = [
        (c(1, 2, 0), c(1, 1, 0), "negative ordering -2 < -1"),
        (c(1, 1, 0), c(1, 2, 0), "negative ordering reverse"),
        (c(0, 0, 0, 0), c(1, 5, 0, 3), "zero > negative"),
        (c(1, 5, 0, 3), c(0, 0, 0, 65535), "negative < zero"),
        (c(0, 0, 0, 0), c(0, 0, 0, 65535), "both zeros equal"),
        (c(0, 0, 0, 0), c(0, 9, 0), "zero < positive"),
        (c(0, 9, 0), c(0, 0, 0, 32769), "positive > zero"),
        (c(1, 25, -1, 1), c(1, 150, -2, 2), "negative different exp/dscale"),
        (c(1, 150, -2, 2), c(1, 25, -1, 1), "negative different exp/dscale reverse"),
        (c(0, 150, -2, 2), c(0, 15, -1, 1), "1.50 alias 1.5"),
        (c(1, 150, -2, 2), c(1, 15, -1, 1), "negative equal alias"),
        (c(1, 150, -2, 2), c(1, 1500, -3, 3), "negative equal alias extra zero"),
        (c(0, 150, -2, 0), c(0, 15, -1, 65535), "dscale ignored"),
        (c(1, 150, -2, 0), c(1, 15, -1, 65535), "negative dscale ignored"),
        (c(0, 12345, -2, 2), c(0, 123450, -3, 3), "123.45 alias 123.450"),
        (c(1, 12345, -2, 2), c(1, 123450, -3, 3), "negative 123.45 alias"),
        (c(0, 1, 76), c(0, 1, 75), "positive large adjusted exponent"),
        (c(1, 1, 76), c(1, 1, 75), "negative large adjusted exponent"),
        (c(1, 1, 75), c(1, 1, 76), "negative large adjusted exponent reverse"),
        (c(1, 1, 76), c(1, 10, 75), "negative equal large alias"),
        (c(1, MAX_COEFF, 0), c(1, MAX_COEFF - 1, 0), "negative coefficient edge"),
        (c(0, MAX_COEFF - 1, 0), c(0, MAX_COEFF, 0), "positive coefficient edge"),
        (c(1, 5, -32768, 65535), c(1, 1, -32768, 0), "negative minimum exponent"),
    ]
    for a, b, _ in fixed_cmp:
        add_cmp(a, b)
        add_cmp(b, a)

    rng_cmp = random.Random(SEED ^ 0xD1FF)
    def gen_cmp_value() -> Dec:
        coeff = rng_cmp.choice([
            0, 1, 9, 10, 15, 100, 10 ** 37, MAX_COEFF,
            rng_cmp.randrange(0, MAX_COEFF + 1),
        ])
        sign = rng_cmp.randrange(2) if coeff else 0
        return make(sign, coeff, rng_cmp.randrange(-80, 81),
                    rng_cmp.choice([0, 1, 2, 5, 19, 38, 32768, 65535]))
    for _ in range(600):
        a = gen_cmp_value()
        b = gen_cmp_value()
        add_cmp(a, b)
        # Numerically equal alias with a different exponent and dscale.
        if a.coeff and a.coeff * 10 <= MAX_COEFF and a.exp - 1 >= -32768:
            alias = make(a.sign, a.coeff * 10, a.exp - 1,
                         rng_cmp.choice([0, 1, 2, 32768, 65535]))
            add_cmp(a, alias)
        if a.coeff == 0:
            add_cmp(a, zero(rng_cmp.choice([0, 1, 32768, 65535])))

    def enc_op(a: Dec, b: Optional[Dec] = None, target: Optional[int] = None,
               mode: Optional[int] = None, r: Optional[Dec] = None,
               st: int = OK) -> dict:
        d = {"a": asdict(a), "mode": mode, "status": st,
             "status_name": STATUS[st]}
        if b is not None:
            d["b"] = asdict(b)
        if target is not None:
            d["target"] = target
        if r is not None:
            d["r"] = asdict(r)
        return d

    return {
        "format": "obo-decimal-model-v1",
        "seed": hex(SEED),
        "max_coeff": str(MAX_COEFF),
        "prototype_max_dscale": MAX_DSCALE,
        "add": [enc_op(a, b, mode=m, r=r, st=s) for a, b, m, r, s in add_v],
        "sub": [enc_op(a, b, mode=m, r=r, st=s) for a, b, m, r, s in sub_v],
        "mul": [enc_op(a, b, mode=m, r=r, st=s) for a, b, m, r, s in mul_v],
        "quantize": [enc_op(a, target=t, mode=m, r=r, st=s)
                     for a, t, m, r, s in quant_v],
        "mul64": mul64_v,
        "mul128": mul128_v,
        "div256_64": div256_v,
        "div256_128": div128_v,
        "cmp": [{"a": asdict(a), "b": asdict(b), "order": order}
                for a, b, order in cmp_v],
        "counts": {
            "add": len(add_v), "sub": len(sub_v), "mul": len(mul_v),
            "quantize": len(quant_v), "mul64": len(mul64_v),
            "mul128": len(mul128_v), "div256_64": len(div256_v),
            "div256_128": len(div128_v), "cmp": len(cmp_v),
        },
    }


def crosscheck_decimal_module(vectors: dict) -> dict:
    """Secondary cross-check under explicit contexts; not the authority."""
    results = {"checked": 0, "numeric_equal": 0, "mismatches": []}

    def check(kind: str, a: Dec, b: Optional[Dec], target: Optional[int],
              mode: int, r: Dec, st: int) -> None:
        if st != OK:
            return
        py_mode = {
            0: decimal.ROUND_HALF_EVEN, 1: decimal.ROUND_HALF_UP,
            2: decimal.ROUND_HALF_DOWN, 3: decimal.ROUND_DOWN,
            4: decimal.ROUND_UP, 5: decimal.ROUND_FLOOR,
            6: decimal.ROUND_CEILING,
        }.get(mode)
        if py_mode is None:  # UNNECESSARY has no direct Python context mode
            return
        ctx = decimal.Context(prec=38, rounding=py_mode,
                              traps=[decimal.InvalidOperation])
        with decimal.localcontext(ctx):
            da = decimal.Decimal(a.coeff).scaleb(a.exp)
            if a.sign:
                da = -da
            if kind in ("add", "sub", "mul") and b is not None:
                db = decimal.Decimal(b.coeff).scaleb(b.exp)
                if b.sign:
                    db = -db
                if kind == "add":
                    got = da + db
                elif kind == "sub":
                    got = da - db
                else:
                    got = da * db
            elif kind == "quantize" and target is not None:
                try:
                    got = da.quantize(decimal.Decimal(1).scaleb(-target),
                                      rounding=py_mode)
                except decimal.InvalidOperation:
                    results["checked"] += 1
                    return
            else:
                return
            want = to_decimal(r)
            results["checked"] += 1
            if got == want:
                results["numeric_equal"] += 1
            elif len(results["mismatches"]) < 20:
                results["mismatches"].append({
                    "kind": kind, "a": asdict(a),
                    "b": asdict(b) if b else None, "target": target,
                    "mode": mode, "python": str(got), "model": str(want),
                })
    for kind in ("add", "sub", "mul"):
        for v in vectors[kind]:
            a = Dec(**v["a"])
            b = Dec(**v["b"]) if "b" in v else None
            r = Dec(**v["r"]) if "r" in v else zero()
            check(kind, a, b, None, v["mode"], r, v["status"])
    for v in vectors["quantize"]:
        a = Dec(**v["a"])
        r = Dec(**v["r"]) if "r" in v else zero()
        check("quantize", a, None, v["target"], v["mode"], r, v["status"])
    return results


if __name__ == "__main__":
    print(json.dumps(build_vectors()["counts"], indent=2))
