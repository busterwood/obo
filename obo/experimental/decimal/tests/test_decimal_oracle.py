#!/usr/bin/env python3
"""Tests for the independent decimal integer model.  Python decimal is a
secondary cross-check under explicit contexts, not the language authority."""
from __future__ import annotations

import unittest

import decimal_oracle as d


class DecimalOracleTests(unittest.TestCase):
    def test_display_scale_and_numeric_equality(self):
        a = d.make(0, 150, -2, 2)   # 1.50
        b = d.make(0, 15, -1, 1)    # 1.5
        self.assertEqual(a.dscale, 2)
        self.assertEqual(b.dscale, 1)
        self.assertTrue(d.equal(a, b))
        self.assertEqual(d.cmp(a, b), 0)

    def test_add_sub_mul_scale(self):
        a = d.make(0, 150, -2, 2)
        b = d.make(0, 120, -2, 2)
        r, st = d.add(a, b, d.MODES["HALF_EVEN"])
        self.assertEqual(st, d.OK)
        self.assertEqual((r.sign, r.coeff, r.exp, r.dscale), (0, 270, -2, 2))
        r, st = d.mul(d.make(0, 120, -2, 2), d.make(0, 12, -1, 1),
                      d.MODES["HALF_EVEN"])
        self.assertEqual(st, d.OK)
        self.assertEqual((r.sign, r.coeff, r.exp, r.dscale), (0, 1440, -3, 3))

    def test_negative_and_zero_ordering(self):
        # Sign must dominate after the both-zero case; zero is positive.
        self.assertEqual(d.cmp(d.make(1, 2, 0, 0), d.make(1, 1, 0, 3)), -1)
        self.assertEqual(d.cmp(d.make(1, 1, 0, 3), d.make(1, 2, 0, 0)), 1)
        self.assertEqual(d.cmp(d.zero(0), d.make(1, 5, 0, 0)), 1)
        self.assertEqual(d.cmp(d.make(1, 5, 0, 0), d.zero(65535)), -1)
        self.assertEqual(d.cmp(d.zero(0), d.zero(65535)), 0)
        # Different exponent and dscale, same sign.
        self.assertEqual(d.cmp(d.make(1, 25, -1, 1), d.make(1, 150, -2, 2)), -1)
        self.assertEqual(d.cmp(d.make(1, 150, -2, 2), d.make(1, 25, -1, 1)), 1)
        # Adjusted-exponent branch must also be negated for negatives.
        self.assertEqual(d.cmp(d.make(1, 1, 76, 0), d.make(1, 1, 75, 0)), -1)
        self.assertEqual(d.cmp(d.make(1, 1, 75, 0), d.make(1, 1, 76, 0)), 1)

    def test_ordering_aliases_and_dscale(self):
        self.assertEqual(d.cmp(d.make(0, 150, -2, 2), d.make(0, 15, -1, 1)), 0)
        self.assertEqual(d.cmp(d.make(1, 150, -2, 2), d.make(1, 15, -1, 1)), 0)
        self.assertEqual(d.cmp(d.make(1, 150, -2, 2), d.make(1, 1500, -3, 3)), 0)
        self.assertEqual(d.cmp(d.make(0, 150, -2, 0), d.make(0, 15, -1, 65535)), 0)
        self.assertEqual(d.cmp(d.make(1, 150, -2, 0), d.make(1, 15, -1, 65535)), 0)
        self.assertEqual(d.cmp(d.make(1, 1, 76, 0), d.make(1, 10, 75, 65535)), 0)

    def test_ordering_properties(self):
        import random
        rng = random.Random(0xC0DEC0DE)

        def value():
            coeff = rng.choice([
                0, 1, 9, 10, 15, 100, 10 ** 37, d.MAX_COEFF,
                rng.randrange(0, d.MAX_COEFF + 1),
            ])
            sign = rng.randrange(2) if coeff else 0
            return d.make(sign, coeff, rng.randrange(-60, 61),
                          rng.choice([0, 1, 2, 32768, 65535]))

        def neg(x):
            if x.coeff == 0:
                return d.zero(x.dscale)
            return d.make(x.sign ^ 1, x.coeff, x.exp, x.dscale)

        vals = [value() for _ in range(240)]
        for a in vals:
            for b in vals:
                r = d.cmp(a, b)
                # totality and trichotomy
                self.assertIn(r, (-1, 0, 1))
                self.assertEqual(r, -d.cmp(b, a))  # antisymmetry
                self.assertEqual(r == 0, d.equal(a, b))
                # sign reversal, including the adjusted-exponent branch
                if a.coeff and b.coeff:
                    self.assertEqual(d.cmp(neg(a), neg(b)), -r)
        for _ in range(1200):
            a, b, c = value(), value(), value()
            # transitivity of <= and of numeric equality
            if d.cmp(a, b) <= 0 and d.cmp(b, c) <= 0:
                self.assertLessEqual(d.cmp(a, c), 0)
            if d.equal(a, b) and d.equal(b, c):
                self.assertTrue(d.equal(a, c))

    def test_ordering_against_python_decimal_exact_context(self):
        import decimal
        import random
        rng = random.Random(0x0DDB1A5E)
        ctx = decimal.Context(prec=80, Emax=100000, Emin=-100000)

        def py(x):
            v = decimal.Decimal(x.coeff).scaleb(x.exp)
            return -v if x.sign else v

        for _ in range(500):
            def value():
                coeff = rng.choice([0, 1, 9, 10, 15, 10 ** 37, d.MAX_COEFF,
                                    rng.randrange(0, d.MAX_COEFF + 1)])
                sign = rng.randrange(2) if coeff else 0
                return d.make(sign, coeff, rng.randrange(-60, 61),
                              rng.choice([0, 1, 2, 65535]))
            a, b = value(), value()
            with decimal.localcontext(ctx):
                pa, pb = py(a), py(b)
                want = (pa > pb) - (pa < pb)
            self.assertEqual(d.cmp(a, b), want)

    def test_quantize_zero_all_uint16_dscale(self):
        for target in (0, 1, 2, 32767, 32768, 32769, 40000, 65535):
            for mode in d.MODES.values():
                r, st = d.quantize(d.zero(123), target, mode)
                self.assertEqual(st, d.OK, (target, mode))
                self.assertEqual((r.sign, r.coeff, r.exp, r.dscale),
                                 (0, 0, 0, target))
        r, st = d.quantize(d.zero(0), 65535, 8)
        self.assertEqual(st, d.INVALID)
        self.assertEqual((r.sign, r.coeff, r.exp, r.dscale), (0, 0, 0, 0))

    def test_quantize_nonzero_high_dscale_still_refused(self):
        a = d.make(0, 1, 0, 65535)
        for target in (32769, 40000, 65535):
            r, st = d.quantize(a, target, d.MODES["HALF_EVEN"])
            self.assertEqual(st, d.UNSUPPORTED_RANGE)
        r, st = d.quantize(d.make(0, 1, -32768, 0), 32768,
                           d.MODES["HALF_EVEN"])
        self.assertEqual(st, d.OK)
        self.assertEqual((r.sign, r.coeff, r.exp, r.dscale),
                         (0, 1, -32768, 32768))

    def test_quantize_modes(self):
        a = d.make(0, 15, -1, 1)    # 1.5
        expected = {
            "HALF_EVEN": 2, "HALF_AWAY": 2, "HALF_TOWARD_ZERO": 1,
            "TOWARD_ZERO": 1, "AWAY": 2, "FLOOR": 1, "CEILING": 2,
        }
        for name, coeff in expected.items():
            r, st = d.quantize(a, 0, d.MODES[name])
            self.assertEqual(st, d.OK, name)
            self.assertEqual(r.coeff, coeff, name)
        r, st = d.quantize(a, 0, d.MODES["UNNECESSARY"])
        self.assertEqual(st, d.INEXACT)
        self.assertEqual(r.coeff, 0)

    def test_negative_directed_rounding(self):
        neg = d.make(1, 15, -1, 1)  # -1.5
        self.assertEqual(d.quantize(neg, 0, d.MODES["FLOOR"])[0].coeff, 2)
        self.assertEqual(d.quantize(neg, 0, d.MODES["CEILING"])[0].coeff, 1)

    def test_overflow_and_range(self):
        a = d.make(0, d.MAX_COEFF, 32767, 0)
        b = d.make(0, d.MAX_COEFF, 32767, 0)
        r, st = d.add(a, b, d.MODES["HALF_EVEN"])
        self.assertEqual(st, d.OVERFLOW)
        r, st = d.quantize(d.make(0, 1, 0, 0), 32769, d.MODES["HALF_EVEN"])
        self.assertEqual(st, d.UNSUPPORTED_RANGE)

    def test_python_decimal_crosscheck_explicit_context(self):
        v = d.build_vectors()
        c = d.crosscheck_decimal_module(v)
        self.assertGreater(c["checked"], 300)
        self.assertEqual(c["numeric_equal"], c["checked"])
        self.assertEqual(c["mismatches"], [])

    def test_seeded_vectors_are_deterministic(self):
        v1 = d.build_vectors()
        v2 = d.build_vectors()
        self.assertEqual(v1, v2)
        self.assertEqual(v1["seed"], "0xdec24")
        self.assertGreaterEqual(v1["counts"]["add"], 100)
        self.assertGreaterEqual(v1["counts"]["quantize"], 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)
