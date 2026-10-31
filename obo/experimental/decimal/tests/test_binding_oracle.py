#!/usr/bin/env python3
"""Tests for the decimal(p,s) mathematical scratch model only.

No compiler, ABI, database adapter or approved API is exercised here.
"""
from __future__ import annotations

import unittest

from binding_oracle import (exact_fits, round_half_away_scaled, rounded_fits,
                            scaled_integer, value)


class BindingModelTests(unittest.TestCase):
    def test_decimal_28_10_positive_and_negative_boundaries(self):
        max_coeff = 10 ** 28 - 1
        vmax = value(0, max_coeff, -10)   # 999999999999999999.9999999999
        self.assertEqual(scaled_integer(vmax, 10), max_coeff)
        self.assertTrue(exact_fits(vmax, 28, 10))
        self.assertTrue(exact_fits(-vmax, 28, 10))

        just_over = value(0, 10 ** 28, -10)
        self.assertFalse(exact_fits(just_over, 28, 10))
        self.assertFalse(exact_fits(-just_over, 28, 10))
        self.assertFalse(exact_fits(value(0, 1, 18), 28, 10))

    def test_redundant_trailing_zeros_are_exact(self):
        # 1.2300 == 1.23; redundant zeros must not make a value too precise.
        self.assertTrue(exact_fits(value(0, 12300, -4), 5, 2))
        self.assertTrue(exact_fits(value(0, 123000, -5), 5, 2))
        self.assertEqual(scaled_integer(value(0, 12300, -4), 2), 123)

    def test_meaningful_extra_digits_are_not_exact(self):
        # 1.234 needs a third fractional digit.
        self.assertIsNone(scaled_integer(value(0, 1234, -3), 2))
        self.assertFalse(exact_fits(value(0, 1234, -3), 5, 2))
        self.assertFalse(exact_fits(value(1, 1234, -3), 5, 2))

    def test_round_half_away_and_carry_overflow(self):
        v = value(0, 999995, -3)          # 999.995
        self.assertEqual(round_half_away_scaled(v, 2), 100000)
        self.assertFalse(rounded_fits(v, 5, 2))
        self.assertFalse(rounded_fits(-v, 5, 2))
        # One lower and the carry still fits.
        self.assertTrue(rounded_fits(value(0, 999994, -3), 5, 2))

    def test_negative_scale_and_scale_greater_than_precision_math(self):
        # Mathematical contract only: p=5, s=-2.  Maximum scaled integer is
        # 10^5-1, so the value is (10^5-1)*10^2.
        self.assertEqual(scaled_integer(value(0, 99999, 2), -2), 99999)
        self.assertTrue(exact_fits(value(0, 99999, 2), 5, -2))
        self.assertFalse(exact_fits(value(0, 100000, 2), 5, -2))

        # p=3, s=5 is permitted by the formula (Oracle/PostgreSQL-style shape).
        self.assertTrue(exact_fits(value(0, 999, -5), 3, 5))
        self.assertFalse(exact_fits(value(0, 1000, -5), 3, 5))

    def test_zero_and_scale_metadata_are_separate(self):
        self.assertTrue(exact_fits(value(0, 0, 0), 1, 0))
        self.assertTrue(exact_fits(value(0, 0, 0), 1, -1000))
        # A nonzero integer does not fit decimal(1,0).
        self.assertFalse(exact_fits(value(0, 10, 0), 1, 0))

    def test_exact_status_distinguishes_inexact_and_precision(self):
        import binding_oracle as b
        self.assertEqual(b.exact_status(value(0, 1234, -3), 5, 2),
                         b.INEXACT)
        self.assertEqual(b.exact_status(value(0, 100000, -2), 5, 2),
                         b.PRECISION_OVERFLOW)
        self.assertEqual(b.exact_status(value(0, 0, 0), 1, -1000), b.OK)
        self.assertEqual(b.exact_status(value(0, 1, 0), 0, 0), b.INVALID)

    def test_carry_rounding_rejects_precision_overflow(self):
        import binding_oracle as b
        r = b.rounded_result(0, 999995, -3, 5, 2, b.MODES["HALF_AWAY"])
        self.assertEqual(r["status"], b.PRECISION_OVERFLOW)
        r = b.rounded_result(0, 999995, -3, 6, 2, b.MODES["HALF_AWAY"])
        self.assertEqual(r["status"], b.OK)
        self.assertEqual((r["coeff"], r["exp"], r["dscale"]),
                         (100000, -2, 2))
        r = b.rounded_result(0, 999994, -3, 5, 2, b.MODES["HALF_AWAY"])
        self.assertEqual((r["status"], r["coeff"]), (b.OK, 99999))

    def test_all_explicit_modes_and_negative_floor_ceiling(self):
        import binding_oracle as b
        expected = {
            "HALF_EVEN": 2, "HALF_AWAY": 2, "HALF_TOWARD_ZERO": 1,
            "TOWARD_ZERO": 1, "AWAY": 2, "FLOOR": 1, "CEILING": 2,
        }
        for name, coeff in expected.items():
            r = b.rounded_result(0, 15, -1, 2, 0, b.MODES[name])
            self.assertEqual((r["status"], r["coeff"]), (b.OK, coeff), name)
        r = b.rounded_result(0, 15, -1, 2, 0, b.MODES["UNNECESSARY"])
        self.assertEqual(r["status"], b.INEXACT)
        self.assertEqual(b.rounded_result(1, 15, -1, 2, 0,
                                          b.MODES["FLOOR"])["coeff"], 2)
        self.assertEqual(b.rounded_result(1, 15, -1, 2, 0,
                                          b.MODES["CEILING"])["coeff"], 1)

    def test_generated_binding_cases_are_consistent(self):
        import binding_oracle as b
        cases = b.build_binding_cases()
        self.assertGreaterEqual(cases["counts"]["cases"], 300)
        self.assertGreater(cases["counts"]["round_ok"], 100)
        self.assertGreater(cases["counts"]["round_precision"], 0)
        for c in cases["cases"]:
            v = value(c["sign"], c["coeff"], c["exp"])
            self.assertEqual(c["exact_status"], b.exact_status(v, c["p"],
                                                               c["s"]))
            rr = b.rounded_result(c["sign"], c["coeff"], c["exp"], c["p"],
                                  c["s"], c["mode"])
            self.assertEqual(rr, c["round"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
