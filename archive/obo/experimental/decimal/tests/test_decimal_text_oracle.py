#!/usr/bin/env python3
"""Tests for the independent private text oracle and exact round-trips."""
from __future__ import annotations

import decimal
import time
import unittest
from fractions import Fraction

import decimal_text_oracle as o


def value_of(r: dict) -> Fraction:
    v = Fraction(r["coeff"])
    if r["exp"] >= 0:
        v *= 10 ** r["exp"]
    else:
        v /= 10 ** (-r["exp"])
    return -v if r["sign"] else v


def decimal_exponent(text: str) -> int:
    return decimal.Decimal(text).as_tuple().exponent


class TextOracleTests(unittest.TestCase):
    def test_fixed_parse_contract(self):
        r = o.parse_text("1.2300")
        self.assertEqual(r["status"], o.OK)
        self.assertEqual((r["sign"], r["coeff"], r["exp"], r["dscale"]),
                         (0, 123, -2, 4))
        self.assertEqual(o.canonical_text(r["sign"], r["coeff"], r["exp"],
                                          r["dscale"]), "1.23")
        self.assertEqual(o.display_text(r["sign"], r["coeff"], r["exp"],
                                        r["dscale"]), (o.OK, "1.2300"))

        r = o.parse_text("-0.00")
        self.assertEqual((r["status"], r["sign"], r["coeff"], r["exp"],
                          r["dscale"]), (o.OK, 0, 0, 0, 2))
        self.assertEqual(o.canonical_text(r["sign"], r["coeff"], r["exp"],
                                          r["dscale"]), "0")
        self.assertEqual(o.display_text(r["sign"], r["coeff"], r["exp"],
                                        r["dscale"]), (o.OK, "0.00"))

        r = o.parse_text("1.5e3")
        self.assertEqual((r["sign"], r["coeff"], r["exp"], r["dscale"]),
                         (0, 15, 2, 0))
        self.assertEqual(o.display_text(r["sign"], r["coeff"], r["exp"],
                                        r["dscale"]), (o.OK, "1500"))

        r = o.parse_text("1.2300e-2")
        self.assertEqual((r["coeff"], r["exp"], r["dscale"]), (123, -4, 6))
        self.assertEqual(o.display_text(r["sign"], r["coeff"], r["exp"],
                                        r["dscale"]), (o.OK, "0.012300"))

    def test_plus_and_dot_forms(self):
        for text in ("+.5", ".5", "+1", "1."):
            r = o.parse_text(text)
            self.assertEqual(r["status"], o.OK, text)
        self.assertEqual(o.parse_text("+.5")["coeff"], 5)
        self.assertEqual(o.parse_text("1.")["dscale"], 0)

    def test_malformed_and_non_ascii(self):
        for text in ("", "+", "-", ".", "e1", "1e", "1e+", "1e-", "1..2",
                     "1.2.3", " 1", "1 ", "1\t", "1_000", "0x10", "NaN",
                     "Infinity", "-Infinity", "1\x002", "\x00", "1\u00e9",
                     "\u0661"):
            self.assertEqual(o.parse_text(text)["status"], o.INVALID, repr(text))

    def test_boundaries(self):
        nines38 = "9" * 38
        nines39 = "9" * 39
        self.assertEqual(o.parse_text(nines38)["status"], o.OK)
        self.assertEqual(o.parse_text(nines39)["status"],
                         o.PRECISION_OVERFLOW)
        r = o.parse_text("1" + "0" * 39)
        self.assertEqual(r["status"], o.OK)
        self.assertEqual((r["coeff"], r["exp"]), (1, 39))
        r = o.parse_text("0" * 200 + nines38)
        self.assertEqual(r["status"], o.OK)
        self.assertEqual(r["coeff"], int(nines38))
        self.assertEqual(o.parse_text("1e32767")["status"], o.OK)
        r = o.parse_text("1e32768")
        self.assertEqual((r["status"], r["coeff"], r["exp"]),
                         (o.OK, 10, 32767))
        r = o.parse_text("1e32804")
        self.assertEqual((r["status"], r["coeff"], r["exp"]),
                         (o.OK, 10 ** 37, 32767))
        self.assertEqual(o.parse_text("1e32805")["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_text("1e-32768")["status"], o.OK)
        self.assertEqual(o.parse_text("1e-32769")["status"], o.EXPONENT_RANGE)
        self.assertEqual(o.parse_text("1e-65536")["status"], o.DISPLAY_RANGE)
        r = o.parse_text("0." + "0" * 65535)
        self.assertEqual(r["status"], o.OK)
        self.assertEqual(r["dscale"], 65535)
        self.assertEqual(o.parse_text("0." + "0" * 65536)["status"],
                         o.DISPLAY_RANGE)

    def test_canonical_trailing_zero_exponent(self):
        # 120 * 10^200 is 1.2e202.  A formatter that uses the stripped digit
        # count instead of the full coefficient length would emit e201.
        self.assertEqual(o.canonical_text(0, 120, 200, 0), "1.2e202")
        self.assertEqual(o.canonical_text(0, 120, -200, 0), "1.2e-198")
        self.assertEqual(o.canonical_text(0, 1200, 0, 0), "1200")
        self.assertEqual(o.canonical_text(0, 12300, -4, 4), "1.23")

    def test_display_never_rounds(self):
        self.assertEqual(o.display_text(0, 12, -1, 0), (o.INEXACT, ""))
        self.assertEqual(o.display_text(0, 120, -2, 1), (o.OK, "1.2"))
        self.assertEqual(o.display_text(0, 12, -1, 3), (o.OK, "1.200"))
        self.assertEqual(o.display_text(1, 1, 0, 2), (o.OK, "-1.00"))

    def test_generated_cases_against_python_decimal(self):
        cases = o.build_text_cases()
        self.assertGreaterEqual(cases["counts"]["parse"], 300)
        self.assertGreaterEqual(cases["counts"]["invalid"], 10)
        self.assertGreaterEqual(cases["counts"]["precision_overflow"], 1)
        self.assertGreaterEqual(cases["counts"]["exponent_range"], 3)
        self.assertGreaterEqual(cases["counts"]["display_range"], 1)
        for c in cases["cases"]:
            if c["status"] != o.OK:
                continue
            want = value_of(c)
            canon = c["canonical"]
            self.assertEqual(Fraction(decimal.Decimal(canon)), want, c["text"])
            st, disp = o.display_text(c["sign"], c["coeff"], c["exp"],
                                      c["dscale"])
            self.assertEqual(st, o.OK, c["text"])
            self.assertEqual(disp, c["display"], c["text"])
            self.assertEqual(Fraction(decimal.Decimal(disp)), want, c["text"])
            self.assertEqual(decimal_exponent(disp), -c["dscale"], c["text"])

    def test_display_scale_is_independent_of_value(self):
        r = o.parse_text("1.5")
        self.assertEqual(o.display_text(r["sign"], r["coeff"], r["exp"], 6),
                         (o.OK, "1.500000"))
        self.assertEqual(o.canonical_text(r["sign"], r["coeff"], r["exp"], 6),
                         "1.5")

    def test_upper_exponent_canonical_and_display_roundtrip(self):
        import random

        def exact(sign, coeff, exp):
            v = Fraction(coeff)
            v = v * (10 ** exp) if exp >= 0 else v / (10 ** (-exp))
            return -v if sign else v

        # Raw core values, not just the normalized coefficient 1.  These are
        # all representable (coefficient <= 10^38-1) even when the canonical
        # scientific adjusted exponent is above 32767.
        cases = []
        for sign in (0, 1):
            for exp in (32767, 32766):
                for k in range(1, 38):
                    cases.append((sign, 10 ** k, exp, (k * 7) % 39))
        cases.append((0, o.MAX_COEFF, 32767, 0))
        cases.append((1, o.MAX_COEFF, 32767, 65535))
        cases.append((0, 10 ** 37 + 12345, 32767, 3))

        rng = random.Random(0xD1E0A)
        for _ in range(300):
            coeff = rng.choice([
                1, 9, 10 ** rng.randrange(1, 38),
                rng.randrange(1, o.MAX_COEFF + 1), o.MAX_COEFF,
            ])
            cases.append((rng.randrange(2), coeff,
                          rng.randrange(32700, 32768),
                          rng.randrange(0, 39)))

        for sign, coeff, exp, dscale in cases:
            want = exact(sign, coeff, exp)
            canon = o.canonical_text(sign, coeff, exp, dscale)
            r = o.parse_text(canon)
            self.assertEqual(r["status"], o.OK, (sign, coeff, exp, canon))
            self.assertEqual(value_of(r), want, canon)

            st, disp = o.display_text(sign, coeff, exp, dscale)
            self.assertEqual(st, o.OK, (sign, coeff, exp, dscale))
            r2 = o.parse_text(disp)
            self.assertEqual(r2["status"], o.OK, disp)
            self.assertEqual(value_of(r2), want)
            self.assertEqual(r2["dscale"], dscale)

        self.assertEqual(
            (o.parse_text("10e32767")["coeff"],
             o.parse_text("10e32767")["exp"]), (10, 32767))
        self.assertEqual(
            (o.parse_text("1e32804")["coeff"],
             o.parse_text("1e32804")["exp"]), (10 ** 37, 32767))
        self.assertEqual(o.parse_text("1e32805")["status"],
                         o.EXPONENT_RANGE)

    def test_long_inputs_are_linear_and_bounded(self):
        t0 = time.monotonic()
        self.assertEqual(o.parse_text("1" + "0" * 100000)["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_text("0." + "0" * 100000)["status"],
                         o.DISPLAY_RANGE)
        self.assertEqual(o.parse_text("1e" + "9" * 20000)["status"],
                         o.EXPONENT_RANGE)
        r = o.parse_text("0." + "0" * 60000)
        self.assertEqual(r["status"], o.OK)
        self.assertEqual(r["dscale"], 60000)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_cases_are_deterministic(self):
        a = o.build_text_cases()
        b = o.build_text_cases()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
