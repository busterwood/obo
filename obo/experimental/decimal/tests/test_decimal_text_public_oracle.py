#!/usr/bin/env python3
"""Tests for the strict public decimal text model.

The private protocol oracle and its fixtures are intentionally untouched; this
suite exercises the public grammar, the exact-fit check predicate and the
database typmod record.
"""
from __future__ import annotations

import decimal
import unittest
from fractions import Fraction

import decimal_text_oracle as o


def value_of(r: dict) -> Fraction:
    v = Fraction(r["coeff"])
    v = v * (10 ** r["exp"]) if r["exp"] >= 0 else v / (10 ** (-r["exp"]))
    return -v if r["sign"] else v


class PublicTextTests(unittest.TestCase):
    def test_strict_grammar(self):
        for text in (".5", "+.5", "-.5", "1.", ".0", "", "+", "-", ".",
                     "1e", "1e+", "1e-", " 1", "1 ", "1_000", "0x10",
                     "NaN", "Inf", "Infinity", "1\x00", "1\u00e9",
                     "\uff11\uff12\uff13", "1\u00a0"):
            self.assertEqual(o.parse_public_text(text)["status"], o.INVALID,
                             repr(text))
        for text in ("0", "-0", "+0", "0.00", "-0.00", "1", "+1", "-1",
                     "0001.2300", "1.2300", "1e3", "1E+3", "1e+003",
                     "1e0003", "1e-0003", "1.2300e3", "1.2300e-2"):
            self.assertEqual(o.parse_public_text(text)["status"], o.OK, text)

    def test_private_oracle_still_accepts_dot_forms(self):
        # The private protocol is deliberately unchanged.
        self.assertEqual(o.parse_text(".5")["status"], o.OK)
        self.assertEqual(o.parse_text("1.")["status"], o.OK)

    def test_value_and_display_scale(self):
        r = o.parse_public_text("1.2300")
        self.assertEqual((r["sign"], r["coeff"], r["exp"], r["dscale"]),
                         (0, 123, -2, 4))
        r = o.parse_public_text("-0.00")
        self.assertEqual((r["sign"], r["coeff"], r["exp"], r["dscale"]),
                         (0, 0, 0, 2))
        r = o.parse_public_text("1.2300e3")
        self.assertEqual((r["coeff"], r["exp"], r["dscale"]), (123, 1, 1))
        r = o.parse_public_text("1.2300e-2")
        self.assertEqual((r["coeff"], r["exp"], r["dscale"]), (123, -4, 6))

    def test_boundaries_and_precedence(self):
        self.assertEqual(o.parse_public_text("9" * 38)["status"], o.OK)
        self.assertEqual(o.parse_public_text("9" * 39)["status"],
                         o.PRECISION_OVERFLOW)
        r = o.parse_public_text("1e32804")
        self.assertEqual((r["status"], r["coeff"], r["exp"]),
                         (o.OK, 10 ** 37, 32767))
        self.assertEqual(o.parse_public_text("1e32805")["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_public_text("1e-32768")["status"], o.OK)
        self.assertEqual(o.parse_public_text("1e-32769")["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_public_text("1e-65535")["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_public_text("1e-65536")["status"],
                         o.DISPLAY_RANGE)
        self.assertEqual(o.parse_public_text("1e-70000")["status"],
                         o.DISPLAY_RANGE)
        self.assertEqual(o.parse_public_text("1e40000")["status"],
                         o.EXPONENT_RANGE)
        self.assertEqual(o.parse_public_text("1" * 39 + ".")["status"],
                         o.INVALID)
        self.assertEqual(o.parse_public_text("0" * 100000 + "1")["status"],
                         o.OK)

    def test_generated_public_cases(self):
        cases = o.build_public_text_cases()
        self.assertGreaterEqual(cases["counts"]["parse"], 60)
        self.assertGreaterEqual(cases["counts"]["invalid"], 20)
        self.assertGreaterEqual(cases["counts"]["precision_overflow"], 1)
        self.assertGreaterEqual(cases["counts"]["exponent_range"], 3)
        self.assertGreaterEqual(cases["counts"]["display_range"], 3)
        for c in cases["cases"]:
            self.assertEqual(o.parse_public_text(c["text"])["status"],
                             c["status"], c["text"])
            if c["status"] != o.OK:
                continue
            want = value_of(c)
            for text in (c["canonical"],):
                self.assertEqual(Fraction(decimal.Decimal(text)), want, text)
            st, disp = o.display_text(c["sign"], c["coeff"], c["exp"],
                                      c["dscale"])
            self.assertEqual((st, disp), (o.OK, c["display"]), c["text"])
            self.assertEqual(Fraction(decimal.Decimal(c["display"])), want,
                             c["text"])

    def test_check_predicate(self):
        cases = o.build_public_check_cases()
        self.assertEqual(cases["counts"]["ok"] > 0, True)
        self.assertEqual(cases["counts"]["inexact"] > 0, True)
        self.assertEqual(cases["counts"]["precision"] > 0, True)
        self.assertEqual(cases["counts"]["metadata"] > 0, True)
        for c in cases["cases"]:
            self.assertEqual(o.check_public(c["value"], c["p"], c["s"]),
                             c["status"], (c["text"], c["p"], c["s"]))
        # Explicit section 5.3 witnesses.
        self.assertEqual(o.check_public(o.parse_public_text("123456.7890"),
                                        10, 4), o.OK)
        self.assertEqual(o.check_public(o.parse_public_text("1234567.890"),
                                        10, 4), o.PRECISION_OVERFLOW)
        self.assertEqual(o.check_public(o.parse_public_text("1500"),
                                        2, -3), o.INEXACT)
        self.assertEqual(o.check_public(o.parse_public_text("99000"),
                                        2, -3), o.OK)
        self.assertEqual(o.check_public(o.parse_public_text("0"), 0, 0),
                         o.UNSUPPORTED_RANGE)
        self.assertEqual(o.check_public(o.parse_public_text("1.23456"),
                                        0, 4), o.UNSUPPORTED_RANGE)

    def test_typmod_vectors(self):
        want = {
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
        }
        self.assertEqual(o.TYPMOD_VECTORS["unconstrained"], -1)
        for name, value in want.items():
            self.assertEqual(o.TYPMOD_VECTORS[name], value)
            p, s = name[len("numeric("):-1].split(",")
            self.assertEqual(o.typmod(int(p), int(s)), value, name)

    def test_public_cases_are_deterministic(self):
        self.assertEqual(o.build_public_text_cases(),
                         o.build_public_text_cases())
        self.assertEqual(o.build_public_check_cases(),
                         o.build_public_check_cases())


if __name__ == "__main__":
    unittest.main(verbosity=2)
