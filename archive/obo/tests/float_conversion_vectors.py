#!/usr/bin/env python3
"""Independent integer/Fraction oracle and permanent float conversion vectors.

This module is a test oracle only.  It uses Python integer/Fraction arithmetic,
never Python float, and is not part of the compiler or runtime implementation.
"""
from __future__ import annotations

import hashlib
import random
from fractions import Fraction

SIGN = 1 << 63
MAXF_BITS = 0x7FEFFFFFFFFFFFFF


def parse_decimal(text: str) -> Fraction:
    """Exact Fraction for finite decimal/exponent text; no float conversion."""
    t = text.strip(" \t\n\r")
    neg = False
    if t[:1] in ("+", "-"):
        neg = t[0] == "-"
        t = t[1:]
    if not t:
        raise ValueError("empty")
    if t.lower() in ("nan", "inf", "infinity"):
        raise ValueError("special")
    mant, exp = t, 0
    if "e" in t.lower():
        i = t.lower().index("e")
        mant, es = t[:i], t[i + 1:]
        if not es or es in ("+", "-"):
            raise ValueError("bad exponent")
        sign = 1
        if es[0] in "+-":
            sign = -1 if es[0] == "-" else 1
            es = es[1:]
        if not es.isdigit():
            raise ValueError("bad exponent")
        exp = sign * int(es)
    if mant.count(".") > 1:
        raise ValueError("bad point")
    if "." in mant:
        a, b = mant.split(".")
    else:
        a, b = mant, ""
    if not a and not b:
        raise ValueError("no digits")
    if a and not a.isdigit():
        raise ValueError("bad integer")
    if b and not b.isdigit():
        raise ValueError("bad fraction")
    if not a:
        a = "0"
    digits = a + b
    if not digits:
        raise ValueError("no digits")
    n = int(digits)
    p = exp - len(b)
    v = Fraction(n * (10 ** p), 1) if p >= 0 else Fraction(n, 10 ** (-p))
    return -v if neg else v


def round_fraction(v: Fraction) -> tuple[int, bool]:
    """Round positive Fraction to binary64; return (positive bits, overflow)."""
    assert v > 0
    n, d = v.numerator, v.denominator
    e = n.bit_length() - d.bit_length()
    if e >= 0:
        if n < (d << e):
            e -= 1
    else:
        if (n << (-e)) < d:
            e -= 1
    if e > 1023:
        return 0, True

    def rnd(A: int, B: int) -> int:
        q, r = divmod(A, B)
        c = 2 * r - B
        if c > 0 or (c == 0 and (q & 1)):
            q += 1
        return q

    if e >= -1022:
        sh = 52 - e
        q = rnd(n << sh, d) if sh >= 0 else rnd(n, d << (-sh))
        if q == (1 << 53):
            q >>= 1
            e += 1
        if e > 1023:
            return 0, True
        return ((e + 1023) << 52) | (q - (1 << 52)), False
    q = rnd(n << 1074, d)
    if q == 0:
        return 0, False
    if q >= (1 << 52):
        return 1 << 52, False
    return q, False


def bits_for_fraction(v: Fraction) -> tuple[int, bool]:
    if v == 0:
        return 0, False
    neg = v < 0
    b, over = round_fraction(abs(v))
    if over:
        return 0, True
    return (b | (SIGN if neg else 0)), False


def bits_for_text(text: str) -> tuple[int, bool]:
    """Finite decimal text -> (bits, overflow)."""
    return bits_for_fraction(parse_decimal(text))


def exact_decimal(v: Fraction) -> str:
    """Finite exact decimal expansion for a Fraction with only 2/5 factors."""
    n, d = v.numerator, v.denominator
    sign = "-" if n < 0 else ""
    n = abs(n)
    a = b = 0
    while d % 2 == 0:
        d //= 2
        a += 1
    while d % 5 == 0:
        d //= 5
        b += 1
    assert d == 1, d
    k = max(a, b)
    digits = str(n * (2 ** (k - a)) * (5 ** (k - b)))
    if k == 0:
        return sign + digits
    digits = digits.rjust(k + 1, "0")
    return sign + digits[:-k] + "." + digits[-k:]


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def fixed_cases() -> list[dict]:
    """Permanent hard cases; expected values are independent Fraction results."""
    tie = ("1.00000000000000033306690738754696212708950042724609375",
           0x3FF0000000000002)
    near = ("1.000000000000000111022302462515654042363166809082031259",
            0x3FF0000000000001)
    normsub = exact_decimal(Fraction(2) ** -1022 - Fraction(2) ** -1075)
    nines = "0." + "9" * 370
    minsub = exact_decimal(Fraction(2) ** -1074)
    threshold = Fraction(2) ** 1024 - Fraction(2) ** 970
    maxbelow = exact_decimal(threshold - Fraction(1, 10 ** 400))
    cases = [
        {"name": "tie-odd-lower", "text": tie[0], "bits": tie[1]},
        {"name": "near-above-even-lower", "text": near[0], "bits": near[1]},
        {"name": "maxsub-minnormal-mid", "text": normsub, "bits": 0x0010000000000000},
        {"name": "long-370-nines", "text": nines, "bits": 0x3FF0000000000000},
        {"name": "exact-minsub", "text": minsub, "bits": 1},
        {"name": "maxf-below", "text": maxbelow, "bits": 0x7FEFFFFFFFFFFFFF},
    ]
    return cases


FIXED_SHA = {
    "tie-odd-lower": "00defdbd3828262cd23b568b487619c0479fc05b476349d7c2f9f53212d3edee",
    "near-above-even-lower": "ee745511919905d1549c573c74299e91f2ac3d6256ebd50614eeb10b81dbd4a8",
    "maxsub-minnormal-mid": "b6fb43132883a2d11f12b2653154850b722435ae7b267d0db283a33873f6ad15",
    "long-370-nines": "f62e1602e84148bf467a92bfb5c8a003bb403b6a9c9d0831c912353d75983218",
    "exact-minsub": "f45aeb158809dfc2e30ccb794028e77653ebdd39eb58ff0f53a66cf3d2e79438",
    "maxf-below": "78ab1e42b4d6737510e48e6290bb77369ecb31e2fef5780b3aeeba29a6c6d349",
}


def random_cases(seed: int, count: int, long: bool = False) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        if long:
            nd = rng.randint(1200, 3000)
            text = "1." + "".join(str(rng.randrange(10)) for _ in range(nd - 1))
        else:
            nd = rng.randint(1, 45)
            nf = rng.randint(0, 20)
            ds = "".join(str(rng.randrange(10)) for _ in range(nd + nf))
            text = ds[:-nf] + "." + ds[-nf:] if nf else ds
            if rng.randrange(2):
                text = "-" + text
            text += "e" + str(rng.randint(-360, 330))
        b, over = bits_for_text(text)
        if over:
            continue
        out.append({"text": text, "bits": b})
    return out


if __name__ == "__main__":
    for c in fixed_cases():
        print(c["name"], len(c["text"]), hex(c["bits"]), sha256_text(c["text"]))
