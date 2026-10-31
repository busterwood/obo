#!/usr/bin/env python3
"""Independent exact model for the private dec24 text protocol.

This is a development oracle, not a language grammar or public API.  It uses
Python integers/strings and Fraction-free digit placement; it deliberately does
not reimplement the C limb routines.  Python `decimal` is not used here and no
binary float appears anywhere.
"""
from __future__ import annotations

import random
import re

MAX_COEFF = 10 ** 38 - 1
MAX_DSCALE = 65535
CANON_FIXED_MAX = 128
MAX_OUTPUT = 98342

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

STATUS = {
    OK: "OK", OVERFLOW: "OVERFLOW",
    UNSUPPORTED_EXACT: "UNSUPPORTED_EXACT", INEXACT: "INEXACT",
    INVALID: "INVALID", DIV_ZERO: "DIV_ZERO",
    UNSUPPORTED_RANGE: "UNSUPPORTED_RANGE",
    PRECISION_OVERFLOW: "PRECISION_OVERFLOW",
    EXPONENT_RANGE: "EXPONENT_RANGE", DISPLAY_RANGE: "DISPLAY_RANGE",
    BUFFER_SMALL: "BUFFER_SMALL",
}

# Private grammar, matching dec24_text.h but implemented with one regex and
# string slicing rather than the C scanner.
_GRAMMAR = re.compile(
    r"([+-]?)(?:([0-9]+)(?:\.([0-9]*))?|\.([0-9]+))"
    r"(?:[eE]([+-]?[0-9]+))?\Z"
)


def _invalid() -> dict:
    return {"status": INVALID, "sign": 0, "coeff": 0, "exp": 0, "dscale": 0}


def parse_text(text: str) -> dict:
    if not isinstance(text, str) or text == "":
        return _invalid()
    if any(ord(ch) == 0 or ord(ch) > 127 for ch in text):
        return _invalid()
    m = _GRAMMAR.fullmatch(text)
    if m is None:
        return _invalid()
    sign = 1 if m.group(1) == "-" else 0
    ipart = m.group(2) or ""
    fpart = m.group(3) if m.group(3) is not None else (m.group(4) or "")
    # group(4) is the .digits form; it is all fractional digits.
    if m.group(4) is not None:
        fpart = m.group(4)
    # Saturate the exponent scan; statuses only depend on being far outside
    # int16/uint16, and this avoids Python's huge-int string guard.
    raw_exp = m.group(5)
    explicit = 0
    if raw_exp is not None:
        eneg = raw_exp.startswith("-")
        digits_exp = raw_exp[1:] if raw_exp[:1] in "+-" else raw_exp
        mag = 0
        cap = 10 ** 15
        for ch in digits_exp:
            d = ord(ch) - 48
            mag = cap if mag > (cap - d) // 10 else mag * 10 + d
        explicit = -mag if eneg else mag

    digits = ipart + fpart
    dscale = max(0, len(fpart) - explicit)
    nonleading = digits.lstrip("0")
    if nonleading == "":
        if dscale > MAX_DSCALE:
            return {"status": DISPLAY_RANGE, "sign": 0, "coeff": 0,
                    "exp": 0, "dscale": 0}
        return {"status": OK, "sign": 0, "coeff": 0, "exp": 0,
                "dscale": dscale}

    core = nonleading.rstrip("0")
    if len(core) > 38:
        return {"status": PRECISION_OVERFLOW, "sign": 0, "coeff": 0,
                "exp": 0, "dscale": 0}
    if dscale > MAX_DSCALE:
        return {"status": DISPLAY_RANGE, "sign": 0, "coeff": 0,
                "exp": 0, "dscale": 0}
    coeff = int(core)
    trailing = len(nonleading) - len(core)
    exp = explicit - len(fpart) + trailing
    if exp > 32767:
        # Normalization can push exp above int16.  Put back the minimal
        # coefficient zeros needed for exponent 32767.  1e32804 fits with 38
        # digits; 1e32805 would need 39 and remains unrepresentable.
        shift = exp - 32767
        if len(core) + shift <= 38:
            coeff *= 10 ** shift
            exp = 32767
    if exp < -32768 or exp > 32767:
        return {"status": EXPONENT_RANGE, "sign": 0, "coeff": 0,
                "exp": 0, "dscale": 0}
    return {"status": OK, "sign": sign, "coeff": coeff, "exp": exp,
            "dscale": dscale}


def _parts(v: dict) -> tuple[int, int, int, int]:
    return v["sign"], v["coeff"], v["exp"], v["dscale"]


def canonical_text(sign: int, coeff: int, exp: int, dscale: int = 0) -> str:
    del dscale
    if coeff == 0:
        return "0"
    digits = str(coeff)
    core = digits.rstrip("0")
    trailing = len(digits) - len(core)
    e = exp + trailing
    sd = len(core)
    pos = sd + e
    if pos <= 0:
        fixed_len = 2 + (-pos) + sd
        fixed = "0." + ("0" * (-pos)) + core
    elif pos >= sd:
        fixed_len = pos
        fixed = core + ("0" * (pos - sd))
    else:
        fixed_len = sd + 1
        fixed = core[:pos] + "." + core[pos:]
    if fixed_len <= CANON_FIXED_MAX:
        out = fixed
    else:
        adj = e + sd - 1
        out = core[0]
        if sd > 1:
            out += "." + core[1:]
        out += "e" + str(adj)
    return ("-" if sign else "") + out


def display_text(sign: int, coeff: int, exp: int, dscale: int) -> tuple[int, str]:
    """Return (status, text).  A negative status means not exactly formattable."""
    if coeff == 0:
        text = "0" + (("." + ("0" * dscale)) if dscale else "")
        return OK, text
    digits = str(coeff)
    core = digits.rstrip("0")
    trailing = len(digits) - len(core)
    if exp + dscale + trailing < 0:
        return INEXACT, ""
    pos = len(digits) + exp
    int_digits = pos if pos > 0 else 1
    if pos > 0:
        intpart = "".join(digits[j] if j < len(digits) else "0"
                          for j in range(pos))
    else:
        intpart = "0"
    frac = "".join(
        digits[pos + j] if 0 <= pos + j < len(digits) else "0"
        for j in range(dscale)
    )
    text = ("-" if sign else "") + intpart
    if dscale:
        text += "." + frac
    return OK, text


def build_text_cases() -> dict:
    fixed_texts = [
        "0", "-0", "+0", "0.00", "-0.00", "1", "+1", "-1",
        "1.2300", "-1.2300", "1.23", "1.230", "1.5e3", "1.500e2",
        "1.5e-3", "1.2300e-2", ".5", "+.5", "-.5e2", "1.", ".0",
        "0001.2300", "12345678901234567890123456789012345678",
        "123456789012345678901234567890123456789",
        "1234567890123456789012345678901234567890",
        "99999999999999999999999999999999999999",
        "999999999999999999999999999999999999999",
        "1000000000000000000000000000000000000000",
        "0." + "0" * 100,
        "1e32767", "1e32768", "1e32804", "1e32805",
        "10e32767", "10e32766", "100e32766",
        "1e-32768", "1e-32769",
        "1e-65535", "1e-65536", "1e99999", "1e-99999",
        "0e999999999999999999", "0e-999999999999",
        "9" * 38 + "0",
        "9" * 38 + "e32767",
        "1" + "0" * 37 + "e32767",
        "0.000000000000000000000000000000000000001",
        "", "+", "-", ".", "e1", "1e", "1e+", "1e-", "1..2", "1.2.3",
        " 1", "1 ", "1\t", "1_000", "0x10", "NaN", "Infinity", "-Infinity",
    ]
    # Non-ASCII and embedded NUL are expressed as Python escapes so the case
    # list itself stays readable.
    fixed_texts += ["1\u00e9", "\u0661", "1\x002", "\x00", "1\x00"]

    cases = []
    for text in fixed_texts:
        r = parse_text(text)
        row = {"text": text, **r, "canonical": "", "display": ""}
        if r["status"] == OK:
            row["canonical"] = canonical_text(
                r["sign"], r["coeff"], r["exp"], r["dscale"])
            st, disp = display_text(
                r["sign"], r["coeff"], r["exp"], r["dscale"])
            if st == OK:
                row["display"] = disp
            else:
                row["display_status"] = st
        cases.append(row)

    rng = random.Random(0xD1D0C0DE)
    for _ in range(500):
        sign = rng.choice(["", "+", "-"])
        nd = rng.randint(1, 48)
        ds = "".join(str(rng.randrange(10)) for _ in range(nd))
        # Add leading/trailing zero pressure, but not every time.
        if rng.randrange(3) == 0:
            ds = "0" * rng.randint(1, 99) + ds
        if rng.randrange(3) == 0:
            ds = ds + "0" * rng.randint(1, 99)
        frac = rng.randint(0, min(len(ds), 45))
        if frac:
            if frac == len(ds):
                mant = "." + ds
            else:
                mant = ds[:-frac] + "." + ds[-frac:]
        else:
            mant = ds
        exp = rng.randint(-60, 60)
        text = sign + mant
        if rng.randrange(2):
            text += rng.choice(["e", "E"]) + str(exp)
        r = parse_text(text)
        row = {"text": text, **r, "canonical": "", "display": ""}
        if r["status"] == OK:
            row["canonical"] = canonical_text(
                r["sign"], r["coeff"], r["exp"], r["dscale"])
            st, disp = display_text(
                r["sign"], r["coeff"], r["exp"], r["dscale"])
            if st == OK:
                row["display"] = disp
            else:
                row["display_status"] = st
        cases.append(row)

    return {
        "format": "obo-decimal-text-oracle-v1",
        "seed": "0xD1D0C0DE",
        "cases": cases,
        "counts": {
            "parse": len(cases),
            "ok": sum(1 for c in cases if c["status"] == OK),
            "invalid": sum(1 for c in cases if c["status"] == INVALID),
            "precision_overflow": sum(
                1 for c in cases if c["status"] == PRECISION_OVERFLOW),
            "exponent_range": sum(
                1 for c in cases if c["status"] == EXPONENT_RANGE),
            "display_range": sum(
                1 for c in cases if c["status"] == DISPLAY_RANGE),
        },
    }


# A few explicit format-only values not produced by a convenient literal.
FORMAT_ONLY = [
    # sign, coeff, exp, dscale
    (0, 12, -1, 3),        # 1.200
    (0, 123, -2, 5),       # 1.23000
    (1, 1, 0, 2),          # -1.00
    (0, 1, -1, 0),         # 0.1 with dscale 0 -> INEXACT
    (0, 120, -2, 1),       # 1.20 at dscale 1 -> 1.2 exact
    (0, 0, 0, 65535),      # huge zero display scale (size query only)
    (0, 1, 32767, 0),      # giant fixed integer
    (0, 1, 127, 0),        # canonical fixed threshold, exactly 128 bytes
    (0, 1, 128, 0),        # threshold + 1 -> scientific canonical
    (0, 10 ** 38 - 1, 0, 0),
    (0, 10 ** 38 - 1, 32767, 65535),  # maximum bounded display (size only)
]


# ---------------------------------------------------------------------------
# Public text API model (the strict language surface)
#
# The private oracle above deliberately accepts a leading point and a trailing
# point for the experimental dec24_text protocol.  The public language grammar
# requires an integer digit and at least one digit after a point.  This model
# is kept separate so the private functions and fixtures stay byte-identical.

_PUBLIC_GRAMMAR = re.compile(
    r"([+-]?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?\Z"
)


def _public_invalid() -> dict:
    return {"status": INVALID, "sign": 0, "coeff": 0, "exp": 0, "dscale": 0}


def parse_public_text(text: str) -> dict:
    """The strict public grammar: optional sign, mandatory integer digits,
    optional '.digits' with at least one digit, optional exponent digits.
    Semantic statuses and normalisation are the approved core's."""
    if not isinstance(text, str) or text == "":
        return _public_invalid()
    if any(ord(ch) == 0 or ord(ch) > 127 for ch in text):
        return _public_invalid()
    m = _PUBLIC_GRAMMAR.fullmatch(text)
    if m is None:
        return _public_invalid()
    sign = 1 if m.group(1) == "-" else 0
    ipart = m.group(2)
    fpart = m.group(3) if m.group(3) is not None else ""
    raw_exp = m.group(4)
    explicit = 0
    if raw_exp is not None:
        eneg = raw_exp.startswith("-")
        digits_exp = raw_exp[1:] if raw_exp[:1] in "+-" else raw_exp
        mag = 0
        cap = 10 ** 15
        for ch in digits_exp:
            d = ord(ch) - 48
            mag = cap if mag > (cap - d) // 10 else mag * 10 + d
        explicit = -mag if eneg else mag

    digits = ipart + fpart
    dscale = max(0, len(fpart) - explicit)
    nonleading = digits.lstrip("0")
    if nonleading == "":
        if dscale > MAX_DSCALE:
            return _public_invalid() | {"status": DISPLAY_RANGE}
        return {"status": OK, "sign": 0, "coeff": 0, "exp": 0,
                "dscale": dscale}

    core = nonleading.rstrip("0")
    if len(core) > 38:
        return _public_invalid() | {"status": PRECISION_OVERFLOW}
    if dscale > MAX_DSCALE:
        return _public_invalid() | {"status": DISPLAY_RANGE}
    coeff = int(core)
    trailing = len(nonleading) - len(core)
    exp = explicit - len(fpart) + trailing
    if exp > 32767:
        shift = exp - 32767
        if len(core) + shift <= 38:
            coeff *= 10 ** shift
            exp = 32767
    if exp < -32768 or exp > 32767:
        return _public_invalid() | {"status": EXPONENT_RANGE}
    return {"status": OK, "sign": sign, "coeff": coeff, "exp": exp,
            "dscale": dscale}


def _public_row(text: str) -> dict:
    r = parse_public_text(text)
    row = {"text": text, **r, "canonical": "", "display": ""}
    if r["status"] == OK:
        row["canonical"] = canonical_text(
            r["sign"], r["coeff"], r["exp"], r["dscale"])
        st, disp = display_text(r["sign"], r["coeff"], r["exp"], r["dscale"])
        if st == OK:
            row["display"] = disp
        else:
            row["display_status"] = st
    return row


def build_public_text_cases() -> dict:
    """Public valid/invalid/boundary vectors from the design (section 8.2)."""
    texts = [
        # valid
        "0", "0.00", "-0", "+0", "-0.00", "1", "+1", "-1",
        "0001.2300", "+1.2300", "-1.2300", "1.2300", "0.001",
        "1e3", "1E+3", "1e+003", "1e0003", "1e-0003",
        "1.2300e3", "1.2300e-2", "1e32767", "1e32768", "1e32804",
        "1e-32768", "1e-16383", "1e-16384", "1.0e-16383",
        "1000e-16384", "0e999999", "0e-32769", "0e-65535",
        "-0e-32769", "9" * 38, "0." + "0" * 65535,
        "0" * 100000 + "1",
        # invalid syntax
        "", "+", "-", ".", ".5", "1.", "1..2", "1e", "1e+", "1e-",
        "1e3.", "1e3e4", "++1", "--1", "+-1", " 1", "1 ", "1\t2",
        "1_000", "0x10", "NaN", "Inf", "Infinity", "-Infinity",
        "1\x00", "\x00", "1\u00e9", "\uff11\uff12\uff13", "1\u00a0",
        # semantic
        "9" * 39, "1e32805", "1e-32769", "1e-65535", "1e-65536",
        "1e-70000", "1.0e-70000", "0e-70000", "0e-65536",
        "1e40000", "0e70000",
    ]
    # Precedence witnesses: syntax beats precision, display range beats
    # exponent, and an unrepresentable upper rescue beats display range.
    texts += ["1" * 39 + ".", "1e-70000", "1e40000"]
    cases = [_public_row(t) for t in texts]
    return {
        "format": "obo-decimal-text-public-oracle-v1",
        "cases": cases,
        "counts": {
            "parse": len(cases),
            "ok": sum(1 for c in cases if c["status"] == OK),
            "invalid": sum(1 for c in cases if c["status"] == INVALID),
            "precision_overflow": sum(
                1 for c in cases if c["status"] == PRECISION_OVERFLOW),
            "exponent_range": sum(
                1 for c in cases if c["status"] == EXPONENT_RANGE),
            "display_range": sum(
                1 for c in cases if c["status"] == DISPLAY_RANGE),
        },
    }


def check_public(value: dict, p: int, s: int) -> int:
    """The exact-fit metadata predicate.  `value` is a parse_public_text row
    (or an explicit value dict with sign/coeff/exp/dscale)."""
    if value.get("status", OK) != OK:
        return INVALID
    if p < 1 or p > 1000 or s < -1000 or s > 1000:
        return UNSUPPORTED_RANGE
    coeff = value["coeff"]
    if coeff == 0:
        return OK
    digits = str(coeff)
    n = len(digits)
    t = len(digits) - len(digits.rstrip("0"))
    k = value["exp"] + s
    if k < 0 and t < -k:
        return INEXACT
    if n + k > p:
        return PRECISION_OVERFLOW
    return OK


def _check_case(value: dict, p: int, s: int, text: str = "") -> dict:
    return {"value": value, "text": text, "p": p, "s": s,
            "status": check_public(value, p, s)}


def build_public_check_cases() -> dict:
    """Section 5.3 plus the section 8.4 boundary vectors, including
    unnormalised coefficients that arithmetic can store."""
    table = [
        ("1.2345", 10, 4), ("1.23456", 10, 4),
        ("123456.7890", 10, 4), ("1234567.890", 10, 4),
        ("9" * 38, 38, 0), ("9" * 38, 37, 0),
        ("0", 1, 0), ("0.001", 1, 3), ("0.01", 1, 3),
        ("99000", 2, -3), ("15000", 2, -3), ("1500", 2, -3),
        ("99999", 2, -3), ("1.5e999", 1000, -1000),
        ("-1.2300", 10, 4), ("1.2300", 10, 2), ("1.2300", 10, 1),
        ("1000", 2, -3), ("1000", 1, -3),
        ("0", 0, 0), ("0", 1001, 0), ("0", 1, 1001),
        ("1e999", 1000, 0), ("1e1000", 1000, 0),
        ("1e1000", 1, -1000), ("1e1001", 1, -1000),
        ("1e38", 38, 0), ("1.23456", 0, 4),
        ("9" * 38 + "0", 39, 0), ("9" * 38 + "0", 38, 0),
        ("1.0", 1, -1001),
    ]
    cases = []
    for text, p, s in table:
        v = parse_public_text(text)
        cases.append(_check_case(v, p, s, text))
    # Unnormalised fixtures: coefficient 1800 * 10^-3 is the value 1.8 with
    # two coefficient trailing zeros; 1800 * 10^-2 is 18.
    v1800m3 = {"status": OK, "sign": 0, "coeff": 1800, "exp": -3, "dscale": 4}
    v1800m2 = {"status": OK, "sign": 0, "coeff": 1800, "exp": -2, "dscale": 4}
    cases += [
        _check_case(v1800m3, 2, 1, "1800*10^-3"),
        _check_case(v1800m3, 2, 0, "1800*10^-3"),
        _check_case(v1800m2, 2, 0, "1800*10^-2"),
        _check_case(v1800m2, 1, -3, "1800*10^-2"),
    ]
    return {
        "format": "obo-decimal-text-public-check-oracle-v1",
        "cases": cases,
        "counts": {
            "check": len(cases),
            "ok": sum(1 for c in cases if c["status"] == OK),
            "inexact": sum(1 for c in cases if c["status"] == INEXACT),
            "precision": sum(
                1 for c in cases if c["status"] == PRECISION_OVERFLOW),
            "metadata": sum(
                1 for c in cases if c["status"] == UNSUPPORTED_RANGE),
        },
    }


# PostgreSQL typmod vectors from decimal-db-boundary-design.md:205-219.  P0
# does not decode typmods, but the boundary suite records them for P1/P2.
TYPMOD_VECTORS = {
    "numeric(1,0)": 65540,
    "numeric(10,2)": 655366,
    "numeric(10,4)": 655368,
    "numeric(10,-2)": 657410,
    "numeric(2,-3)": 133121,
    "numeric(3,5)": 196617,
    "numeric(38,0)": 2490372,
    "numeric(1000,0)": 65536004,
    "numeric(1000,1000)": 65537004,
    "numeric(1000,-1000)": 65537052,
    "unconstrained": -1,
}


def typmod(p: int, s: int) -> int:
    """The PostgreSQL numeric typmod packing used by the vectors above."""
    return ((p << 16) | (s & 0x7ff)) + 4


PG_DSCALE_MAX = 16383
PG_MAX_PRECISION = 1000
PG_MIN_SCALE = -1000
PG_MAX_SCALE = 1000

# Invalid raw typmods from the boundary design: 0..3 are not numeric typmods,
# -2 is not unconstrained, and the remaining values decode to p/s outside the
# PostgreSQL numeric metadata range.
TYPMOD_INVALID = [0, 1, 2, 3, -2, 4, 65601540, 66541, 66587]


def decode_typmod(value: int):
    """Decode a PGresult numeric typmod to (precision, scale, constrained).

    Returns None for anything that is not -1 or a valid decoded numeric(p,s).
    This is the independent oracle for the adapter's DecimalType decode.
    """
    if value == -1:
        return (0, 0, False)
    if value < 4:
        return None
    t = value - 4
    p = (t >> 16) & 0xFFFF
    s = (((t & 0x7FF) ^ 1024) - 1024)
    if p < 1 or p > PG_MAX_PRECISION or s < PG_MIN_SCALE or s > PG_MAX_SCALE:
        return None
    return (p, s, True)


def build_public_typmod_cases() -> dict:
    """Encode/decode vectors, plus the invalid typmods a fake result can carry."""
    encoded = []
    for name, value in TYPMOD_VECTORS.items():
        if name == "unconstrained":
            continue
        p, s = name[len("numeric("):-1].split(",")
        p = int(p)
        s = int(s)
        encoded.append({"name": name, "p": p, "s": s, "typmod": value,
                        "decode": [p, s, True]})
    return {
        "format": "obo-decimal-typmod-oracle-v1",
        "unconstrained": {"typmod": -1, "decode": [0, 0, False]},
        "encoded": encoded,
        "invalid": list(TYPMOD_INVALID),
    }


__all__ = [
    "OK", "OVERFLOW", "UNSUPPORTED_EXACT", "INEXACT", "INVALID", "DIV_ZERO",
    "UNSUPPORTED_RANGE", "PRECISION_OVERFLOW", "EXPONENT_RANGE",
    "DISPLAY_RANGE", "BUFFER_SMALL", "STATUS", "MAX_COEFF", "MAX_DSCALE",
    "MAX_OUTPUT", "parse_text", "canonical_text", "display_text",
    "build_text_cases", "FORMAT_ONLY",
    "parse_public_text", "build_public_text_cases", "check_public",
    "build_public_check_cases", "TYPMOD_VECTORS", "typmod",
    "PG_DSCALE_MAX", "PG_MAX_PRECISION", "PG_MIN_SCALE", "PG_MAX_SCALE",
    "TYPMOD_INVALID", "decode_typmod", "build_public_typmod_cases",
]

