#!/usr/bin/env python3
"""Tests for arithmetic_oracle.py's exact-rational D2 model."""
from __future__ import annotations

import random
import unittest
from fractions import Fraction

import arithmetic_oracle as ar


def D(sign, coeff, exp, dscale=0):
    return ar.Dec(sign, coeff, exp, dscale)


class ExactTests(unittest.TestCase):
    def test_add_sub_mul_exact(self):
        a, b = D(0, 150, -2, 2), D(0, 15, -1, 1)
        st, r = ar.add_exact(a, b, 7)
        self.assertEqual(st, ar.OK)
        self.assertEqual(ar.value(r), Fraction(3))
        self.assertEqual(r.dscale, 7)
        st, r = ar.sub_exact(a, b, 0)
        self.assertEqual(st, ar.OK)
        self.assertEqual(ar.value(r), Fraction(0))
        st, r = ar.mul_exact(D(0, 12, -1), D(0, 12, -1), 0)
        self.assertEqual(st, ar.OK)
        self.assertEqual(ar.value(r), Fraction(144, 100))

    def test_wide_gap_exact_is_inexact(self):
        st, _ = ar.add_exact(D(0, 12, 76), D(0, 1, 0), 0)
        self.assertEqual(st, ar.INEXACT)
        st, _ = ar.add_exact(D(0, 9, 76), D(0, 1, 0), 0)
        self.assertEqual(st, ar.INEXACT)
        st, _ = ar.sub_exact(D(0, 1, 37), D(0, 1, -100), 0)
        self.assertEqual(st, ar.INEXACT)

    def test_div_exact_terminating_and_not(self):
        for a, b, want in [(1, 2, Fraction(1, 2)), (1, 8, Fraction(1, 8)),
                           (1, 40, Fraction(1, 40)), (10, 2, Fraction(5))]:
            st, r = ar.div_exact(D(0, a, 0), D(0, b, 0), 3)
            self.assertEqual(st, ar.OK)
            self.assertEqual(ar.value(r), want)
            self.assertEqual(r.dscale, 3)
        for a, b in [(1, 3), (1, 7), (1, 10 ** 38 - 1)]:
            st, _ = ar.div_exact(D(0, a, 0), D(0, b, 0), 0)
            self.assertEqual(st, ar.INEXACT)
        st, _ = ar.div_exact(D(0, 1, 0), D(0, 0, 0), 0)
        self.assertEqual(st, ar.DIV_ZERO)

    def test_rem_trunc_identity_and_sign(self):
        for a, b in [(7, 3), (-7, 3), (7, -3), (-7, -3), (15, 4)]:
            st, r = ar.rem_trunc(D(1 if a < 0 else 0, abs(a), 0),
                                 D(1 if b < 0 else 0, abs(b), 0), 2)
            self.assertEqual(st, ar.OK)
            va = Fraction(a)
            vb = Fraction(b)
            q = abs(va) // abs(vb)
            if (va < 0) != (vb < 0):
                q = -q
            self.assertEqual(ar.value(r), va - q * vb)
            self.assertLess(abs(ar.value(r)), abs(vb))
            if r.coeff:
                self.assertEqual(r.sign, 1 if a < 0 else 0)


class RoundedTests(unittest.TestCase):
    def test_ties_all_modes(self):
        a, b = D(0, 1, 0), D(0, 2, 0)     # 0.5 at scale 0
        expected = {ar.MODES["HALF_EVEN"]: 0, ar.MODES["HALF_AWAY"]: 1,
                    ar.MODES["HALF_TOWARD_ZERO"]: 0,
                    ar.MODES["TOWARD_ZERO"]: 0, ar.MODES["AWAY"]: 1,
                    ar.MODES["FLOOR"]: 0, ar.MODES["CEILING"]: 1}
        for mode, coeff in expected.items():
            st, r = ar.div_scale(a, b, 0, mode, 0)
            self.assertEqual(st, ar.OK)
            self.assertEqual(r.coeff, coeff)
        st, _ = ar.div_scale(a, b, 0, ar.MODES["UNNECESSARY"], 0)
        self.assertEqual(st, ar.INEXACT)

    def test_negative_sign_modes(self):
        a, b = D(1, 1, 0), D(0, 2, 0)     # -0.5
        st, r = ar.div_scale(a, b, 0, ar.MODES["FLOOR"], 0)
        self.assertEqual((st, r.sign, r.coeff), (ar.OK, 1, 1))
        st, r = ar.div_scale(a, b, 0, ar.MODES["CEILING"], 0)
        self.assertEqual((st, r.sign, r.coeff), (ar.OK, 0, 0))
        st, r = ar.div_scale(a, b, 0, ar.MODES["AWAY"], 0)
        self.assertEqual((st, r.sign, r.coeff), (ar.OK, 1, 1))

    def test_precision_carry(self):
        st, r = ar.div_precision(D(0, 999, -2), D(0, 1, 0), 2,
                                 ar.MODES["HALF_AWAY"], 0)
        self.assertEqual(st, ar.OK)
        self.assertEqual((r.coeff, r.exp), (10, 0))
        self.assertEqual(ar.value(r), Fraction(10))

    def test_nonterminating_needs_requested_rounding(self):
        a, b = D(0, 1, 0), D(0, 3, 0)
        st, r = ar.div_scale(a, b, 4, ar.MODES["HALF_EVEN"], 0)
        self.assertEqual((st, r.coeff, r.exp), (ar.OK, 3333, -4))
        st, _ = ar.div_scale(a, b, 4, ar.MODES["UNNECESSARY"], 0)
        self.assertEqual(st, ar.INEXACT)

    def test_power_of_ten_predecessor(self):
        st, r = ar.sub_rounded(D(0, 1, 37), D(0, 1, -100),
                               ar.MODES["TOWARD_ZERO"], 0)
        self.assertEqual(st, ar.OK)
        self.assertEqual((r.coeff, r.exp), (10 ** 38 - 1, -1))
        st, r = ar.add_rounded(D(0, 1, 37), D(0, 1, -100),
                               ar.MODES["AWAY"], 0)
        self.assertEqual((r.coeff, r.exp), (10 ** 37 + 1, 0))

    def test_upper_exponent_rescue(self):
        st, r = ar.mul_exact(D(0, 1, 32767), D(0, 1, 1), 5)
        self.assertEqual(st, ar.OK)
        self.assertEqual((r.coeff, r.exp, r.dscale), (10, 32767, 5))
        st, r = ar.div_precision(D(0, 1, 32767), D(0, 1, -1), 1,
                                 ar.MODES["HALF_EVEN"], 0)
        self.assertEqual((st, r.coeff, r.exp), (ar.OK, 10, 32767))
        st, r = ar.div_precision(D(0, 10 ** 33, 32767), D(0, 1, 0), 1,
                                 ar.MODES["HALF_EVEN"], 0)
        self.assertEqual(st, ar.OK)
        self.assertEqual((r.coeff, r.exp), (10 ** 33, 32767))
        st, _ = ar.div_precision(D(0, 1, 32767), D(0, 1, -38), 1,
                                 ar.MODES["HALF_EVEN"], 0)
        self.assertEqual(st, ar.OVERFLOW)

    def test_exponent_overflow_is_explicit(self):
        st, _ = ar.div_precision(D(0, 1, 32767), D(0, 1, -32768), 1,
                                 ar.MODES["HALF_EVEN"], 0)
        self.assertEqual(st, ar.OVERFLOW)
        st, _ = ar.div_precision(D(0, 1, -32768), D(0, 1, 32767), 1,
                                 ar.MODES["HALF_EVEN"], 0)
        self.assertEqual(st, ar.OVERFLOW)

    def test_zero_and_canonical_sign(self):
        st, r = ar.div_exact(D(0, 0, 0, 65535), D(1, 2, 0), 7)
        self.assertEqual(st, ar.OK)
        self.assertEqual((r.sign, r.coeff, r.exp, r.dscale), (0, 0, 0, 7))
        st, r = ar.div_scale(D(0, 0, 0), D(1, 2, 0), 5,
                             ar.MODES["HALF_EVEN"], 9)
        self.assertEqual((st, r.sign, r.coeff, r.exp, r.dscale),
                         (ar.OK, 0, 0, 0, 9))

    def test_seeded_random_remainder_identity(self):
        rng = random.Random(0xD2BEEF)
        for _ in range(400):
            ca = rng.randrange(1, ar.MAX_COEFF + 1)
            cb = rng.randrange(1, ar.MAX_COEFF + 1)
            a = D(rng.randrange(2), ca, rng.randrange(-80, 81), 0)
            b = D(rng.randrange(2), cb, rng.randrange(-80, 81), 0)
            st, r = ar.rem_trunc(a, b, 3)
            self.assertEqual(st, ar.OK)
            va, vb = ar.value(a), ar.value(b)
            q = abs(va) // abs(vb)
            if (va < 0) != (vb < 0):
                q = -q
            self.assertEqual(ar.value(r), va - q * vb)
            self.assertLess(abs(ar.value(r)), abs(vb))

    def test_seeded_random_division_scale(self):
        rng = random.Random(0xD2D1F)
        for _ in range(400):
            a = D(rng.randrange(2), rng.randrange(1, ar.MAX_COEFF + 1),
                  rng.randrange(-40, 41), 0)
            b = D(rng.randrange(2), rng.randrange(1, ar.MAX_COEFF + 1),
                  rng.randrange(-40, 41), 0)
            scale = rng.randrange(-5, 11)
            mode = rng.randrange(7)
            st, r = ar.div_scale(a, b, scale, mode, 5)
            self.assertIn(st, (ar.OK, ar.OVERFLOW))
            if st == ar.OK:
                self.assertEqual(r.dscale, 5)
                self.assertLessEqual(r.coeff, ar.MAX_COEFF)


if __name__ == "__main__":
    unittest.main()
