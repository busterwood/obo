/* dec24_arith.c — private exact and explicitly rounded decimal arithmetic.
 *
 * This file is independent of the D1 core's static internals: it uses the
 * public limb primitives and adds fixed bounded helpers.  No libc, libm,
 * libgcc division, allocation or binary floating point.  See dec24_arith.h
 * for the contract; README.md records what is proposed versus implemented.
 */
#include "dec24_arith.h"

#include <stddef.h>

typedef unsigned __int128 dec_u128;

#define DEC10_38_HI 0x4b3b4ca85a86c47aULL
#define DEC10_38_LO 0x098a224000000000ULL
#define DEC_COEFF_MAX_HI 0x4b3b4ca85a86c47aULL
#define DEC_COEFF_MAX_LO 0x098a223fffffffffULL

static const uint64_t dec_pow10_64[20] = {
    0x0000000000000001ULL, 0x000000000000000aULL,
    0x0000000000000064ULL, 0x00000000000003e8ULL,
    0x0000000000002710ULL, 0x00000000000186a0ULL,
    0x00000000000f4240ULL, 0x0000000000989680ULL,
    0x0000000005f5e100ULL, 0x000000003b9aca00ULL,
    0x00000002540be400ULL, 0x000000174876e800ULL,
    0x000000e8d4a51000ULL, 0x000009184e72a000ULL,
    0x00005af3107a4000ULL, 0x00038d7ea4c68000ULL,
    0x002386f26fc10000ULL, 0x016345785d8a0000ULL,
    0x0de0b6b3a7640000ULL, 0x8ac7230489e80000ULL
};

/* ------------------------------ 256-bit helpers ------------------------- */

static void u256_from_u128(dec_u256 *a, uint64_t hi, uint64_t lo) {
    a->w[0] = lo;
    a->w[1] = hi;
    a->w[2] = 0;
    a->w[3] = 0;
}

static int u256_to_u128(const dec_u256 *a, dec_u128 *out) {
    if (a->w[2] != 0 || a->w[3] != 0) return DEC_OVERFLOW;
    *out = ((dec_u128)a->w[1] << 64) | a->w[0];
    return DEC_OK;
}

static int u256_add(dec_u256 *r, const dec_u256 *a, const dec_u256 *b) {
    dec_u256 t = {{0, 0, 0, 0}};
    uint64_t carry = 0;
    for (int i = 0; i < 4; i++) {
        uint64_t s = a->w[i] + b->w[i];
        uint64_t c1 = (s < a->w[i]) ? 1 : 0;
        uint64_t s2 = s + carry;
        uint64_t c2 = (s2 < s) ? 1 : 0;
        t.w[i] = s2;
        carry = c1 | c2;
    }
    *r = t;
    return carry != 0;
}

static int u256_sub(dec_u256 *r, const dec_u256 *a, const dec_u256 *b) {
    dec_u256 t = {{0, 0, 0, 0}};
    uint64_t borrow = 0;
    for (int i = 0; i < 4; i++) {
        uint64_t s = a->w[i] - b->w[i];
        uint64_t b1 = (a->w[i] < b->w[i]) ? 1 : 0;
        uint64_t s2 = s - borrow;
        uint64_t b2 = (s < borrow) ? 1 : 0;
        t.w[i] = s2;
        borrow = b1 | b2;
    }
    *r = t;
    return borrow != 0;
}

static int u256_mul_small(dec_u256 *a, uint64_t m) {
    uint64_t carry = 0;
    for (int i = 0; i < 4; i++) {
        uint64_t lo, hi;
        dec_mul64(a->w[i], m, &lo, &hi);
        uint64_t s = lo + carry;
        uint64_t c = (s < lo) ? 1 : 0;
        a->w[i] = s;
        carry = hi + c;
        if (carry < hi) return DEC_OVERFLOW;
    }
    return carry == 0 ? DEC_OK : DEC_OVERFLOW;
}

static int u256_add_small(dec_u256 *a, uint64_t v) {
    dec_u256 one = {{v, 0, 0, 0}};
    return u256_add(a, a, &one) ? DEC_OVERFLOW : DEC_OK;
}

static int u256_mul_pow10(dec_u256 *a, int shift) {
    if (shift < 0) return DEC_INVALID;
    while (shift > 0) {
        int take = shift > 19 ? 19 : shift;
        int st = u256_mul_small(a, dec_pow10_64[take]);
        if (st != DEC_OK) return st;
        shift -= take;
    }
    return DEC_OK;
}

static int u256_cmp_u128(const dec_u256 *a, uint64_t hi, uint64_t lo) {
    if (a->w[3] != 0 || a->w[2] != 0) return 1;
    if (a->w[1] != hi) return a->w[1] < hi ? -1 : 1;
    if (a->w[0] != lo) return a->w[0] < lo ? -1 : 1;
    return 0;
}

static int u256_mod_u128(const dec_u256 *n, dec_u128 d, dec_u128 *rem) {
    if (d == 0) return DEC_DIV_ZERO;
    if (d == 1) {
        *rem = 0;
        return DEC_OK;
    }
    dec_u256 q, r;
    if ((uint64_t)(d >> 64) == 0) {
        uint64_t rr = 0;
        int st = dec_div256_64(&q, &rr, n, (uint64_t)d);
        if (st != DEC_OK) return st;
        *rem = rr;
        return DEC_OK;
    }
    int st = dec_div256_128(&q, &r, n, (uint64_t)d, (uint64_t)(d >> 64));
    if (st != DEC_OK) return st;
    if (r.w[2] != 0 || r.w[3] != 0) return DEC_OVERFLOW;
    *rem = ((dec_u128)r.w[1] << 64) | r.w[0];
    return DEC_OK;
}

/* Divide two 256-bit magnitudes.  A divisor above 128 bits is larger than any
 * coefficient numerator used here, so the quotient is zero. */
static void divmod_u256(dec_u256 *q, dec_u256 *r,
                        const dec_u256 *n, const dec_u256 *d) {
    if (d->w[2] != 0 || d->w[3] != 0) {
        *q = (dec_u256){{0, 0, 0, 0}};
        *r = *n;
        return;
    }
    if (d->w[1] != 0) {
        (void)dec_div256_128(q, r, n, d->w[0], d->w[1]);
    } else {
        uint64_t rr = 0;
        (void)dec_div256_64(q, &rr, n, d->w[0]);
        r->w[0] = rr;
        r->w[1] = r->w[2] = r->w[3] = 0;
    }
}

static int digits_u128(dec_u128 v) {
    dec_u256 t;
    u256_from_u128(&t, (uint64_t)(v >> 64), (uint64_t)v);
    return dec_u256_digits(&t);
}

static int exact_fit(dec24 *dst, int sign, dec_u256 c, int64_t exp,
                     uint16_t dscale) {
    if (dec_u256_is_zero(&c))
        return dec_set(dst, 0, 0, 0, 0, dscale);
    int digits = dec_u256_digits(&c);
    /* Trailing zeros can be removed both to fit 38 digits and to lift an
     * exponent that is below int16.  A nonzero discarded digit is a precision
     * failure if the coefficient was too long, otherwise a range failure. */
    while (digits > 38 || exp < -32768) {
        uint64_t rem = 0;
        int st = dec_div256_64(&c, &rem, &c, 10);
        if (st != DEC_OK) return st;
        if (rem != 0) return digits > 38 ? DEC_INEXACT : DEC_OVERFLOW;
        exp++;
        digits--;
    }
    /* Parser-style upper rescue: append trailing coefficient zeros, never
     * significant nonzeros, until the exponent fits or 38 digits are used. */
    while (exp > 32767 && digits < 38) {
        int st = u256_mul_small(&c, 10);
        if (st != DEC_OK) return DEC_OVERFLOW;
        exp--;
        digits++;
    }
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    if (u256_cmp_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
        return DEC_INEXACT;
    return dec_set(dst, sign, c.w[0], c.w[1], (int)exp, dscale);
}

/* ------------------------------ exact add/sub --------------------------- */

static int exact_add_impl(dec24 *dst, const dec24 *a, const dec24 *b,
                          uint16_t dscale) {
    if (dst == 0 || a == 0 || b == 0 || !dec_valid(a) || !dec_valid(b))
        return DEC_INVALID;
    if (dec_is_zero(a) && dec_is_zero(b))
        return dec_set(dst, 0, 0, 0, 0, dscale);
    if (dec_is_zero(a))
        return dec_set(dst, b->sign, b->lo, b->hi, (int16_t)b->exp, dscale);
    if (dec_is_zero(b))
        return dec_set(dst, a->sign, a->lo, a->hi, (int16_t)a->exp, dscale);

    int ea = (int16_t)a->exp, eb = (int16_t)b->exp;
    int e = ea < eb ? ea : eb;
    int sha = ea - e, shb = eb - e;
    /* Beyond the exact 256-bit alignment, the smaller operand is at least 39
     * decimal places below the other's leading digit.  The exact result then
     * needs more than 38 significant digits and no approximation is written. */
    if (sha > 76 || shb > 76) return DEC_INEXACT;

    dec_u256 ma, mb;
    u256_from_u128(&ma, a->hi, a->lo);
    u256_from_u128(&mb, b->hi, b->lo);
    int st;
    if (sha && (st = u256_mul_pow10(&ma, sha)) != DEC_OK)
        return DEC_INEXACT;
    if (shb && (st = u256_mul_pow10(&mb, shb)) != DEC_OK)
        return DEC_INEXACT;

    dec_u256 m;
    int sign;
    if (a->sign == b->sign) {
        if (u256_add(&m, &ma, &mb)) return DEC_INEXACT;
        sign = a->sign;
    } else {
        int cmp = dec_u256_cmp(&ma, &mb);
        if (cmp == 0) return dec_set(dst, 0, 0, 0, 0, dscale);
        if (cmp > 0) {
            (void)u256_sub(&m, &ma, &mb);
            sign = a->sign;
        } else {
            (void)u256_sub(&m, &mb, &ma);
            sign = b->sign;
        }
    }
    return exact_fit(dst, sign, m, e, dscale);
}

int dec_add_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale) {
    return exact_add_impl(dst, a, b, result_dscale);
}

int dec_sub_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale) {
    if (dst == 0 || a == 0 || b == 0 || !dec_valid(a) || !dec_valid(b))
        return DEC_INVALID;
    dec24 nb;
    dec_copy(&nb, b);
    if (!dec_is_zero(&nb)) nb.sign ^= 1;
    return exact_add_impl(dst, a, &nb, result_dscale);
}

int dec_mul_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale) {
    if (dst == 0 || a == 0 || b == 0 || !dec_valid(a) || !dec_valid(b))
        return DEC_INVALID;
    if (dec_is_zero(a) || dec_is_zero(b))
        return dec_set(dst, 0, 0, 0, 0, result_dscale);
    dec_u256 p;
    int st = dec_mul128x128(&p, a->lo, a->hi, b->lo, b->hi);
    if (st != DEC_OK) return DEC_INEXACT;
    int64_t exp = (int64_t)(int16_t)a->exp + (int16_t)b->exp;
    return exact_fit(dst, a->sign ^ b->sign, p, exp, result_dscale);
}

/* ------------------------------ exact division -------------------------- */

static unsigned ctz128(dec_u128 x) {
    uint64_t lo = (uint64_t)x;
    if (lo != 0) return (unsigned)__builtin_ctzll(lo);
    return 64u + (unsigned)__builtin_ctzll((uint64_t)(x >> 64));
}

static int divmod_u128_u128(dec_u128 a, dec_u128 b,
                            dec_u128 *q, dec_u128 *r) {
    if (b == 0) return DEC_DIV_ZERO;
    dec_u256 n, qq, rr;
    u256_from_u128(&n, (uint64_t)(a >> 64), (uint64_t)a);
    if ((uint64_t)(b >> 64) == 0) {
        uint64_t rem = 0;
        int st = dec_div256_64(&qq, &rem, &n, (uint64_t)b);
        if (st != DEC_OK) return st;
        rr = (dec_u256){{rem, 0, 0, 0}};
    } else {
        int st = dec_div256_128(&qq, &rr, &n, (uint64_t)b,
                                (uint64_t)(b >> 64));
        if (st != DEC_OK) return st;
    }
    if (qq.w[2] != 0 || qq.w[3] != 0 || rr.w[2] != 0 || rr.w[3] != 0)
        return DEC_OVERFLOW;
    *q = ((dec_u128)qq.w[1] << 64) | qq.w[0];
    *r = ((dec_u128)rr.w[1] << 64) | rr.w[0];
    return DEC_OK;
}

static dec_u128 gcd128(dec_u128 a, dec_u128 b) {
    if (a == 0) return b;
    if (b == 0) return a;
    unsigned sa = ctz128(a), sb = ctz128(b);
    unsigned s = sa < sb ? sa : sb;
    a >>= sa;
    b >>= sb;
    while (a != b) {
        if (a > b) {
            a -= b;
            if (a == 0) break;
            a >>= ctz128(a);
        } else {
            b -= a;
            if (b == 0) break;
            b >>= ctz128(b);
        }
    }
    return a << s;
}

/* Factor a reduced denominator into 2^p * 5^q.  Any other prime factor makes
 * the decimal quotient nonterminating. */
static int factor_denominator(dec_u128 den, int *p2, int *p5) {
    int p = 0, q = 0;
    while ((den & 1) == 0) {
        den >>= 1;
        p++;
    }
    for (;;) {
        uint64_t hi = (uint64_t)(den >> 64), lo = (uint64_t)den;
        uint64_t qh, ql, rem;
        int st = dec_div128_64(hi, lo, 5, &qh, &ql, &rem);
        if (st != DEC_OK) return st;
        if (rem != 0) break;
        den = ((dec_u128)qh << 64) | ql;
        q++;
    }
    if (den != 1) return DEC_INEXACT;
    *p2 = p;
    *p5 = q;
    return DEC_OK;
}

int dec_div_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale) {
    if (dst == 0 || a == 0 || b == 0 || !dec_valid(a) || !dec_valid(b))
        return DEC_INVALID;
    if (dec_is_zero(b)) return DEC_DIV_ZERO;
    if (dec_is_zero(a))
        return dec_set(dst, 0, 0, 0, 0, result_dscale);

    dec_u128 ca = ((dec_u128)a->hi << 64) | a->lo;
    dec_u128 cb = ((dec_u128)b->hi << 64) | b->lo;
    dec_u128 g = gcd128(ca, cb);
    dec_u128 num = 0, den = 0, rcheck = 0;
    int st = divmod_u128_u128(ca, g, &num, &rcheck);
    if (st != DEC_OK) return st;
    st = divmod_u128_u128(cb, g, &den, &rcheck);
    if (st != DEC_OK) return st;

    int p2 = 0, p5 = 0;
    st = factor_denominator(den, &p2, &p5);
    if (st != DEC_OK) return st;
    int m = p2 > p5 ? p2 : p5;

    dec_u256 c;
    u256_from_u128(&c, (uint64_t)(num >> 64), (uint64_t)num);
    /* num / (2^p2 * 5^p5) = (num * 2^(m-p2) * 5^(m-p5)) / 10^m. */
    for (int i = 0; i < m - p2; i++) {
        st = u256_mul_small(&c, 2);
        if (st != DEC_OK) return DEC_INEXACT;
    }
    for (int i = 0; i < m - p5; i++) {
        st = u256_mul_small(&c, 5);
        if (st != DEC_OK) return DEC_INEXACT;
    }
    int64_t exp = (int64_t)(int16_t)a->exp - (int16_t)b->exp - m;
    return exact_fit(dst, a->sign ^ b->sign, c, exp, result_dscale);
}

/* ------------------------------ truncating remainder ------------------- */

static dec_u128 mulmod_u128(dec_u128 x, dec_u128 y, dec_u128 m) {
    if (m == 1) return 0;
    dec_u256 p;
    (void)dec_mul128x128(&p, (uint64_t)x, (uint64_t)(x >> 64),
                         (uint64_t)y, (uint64_t)(y >> 64));
    dec_u128 r = 0;
    (void)u256_mod_u128(&p, m, &r);
    return r;
}

static dec_u128 pow10mod(dec_u128 d, dec_u128 m) {
    dec_u128 base = 0, acc = 0;
    (void)u256_mod_u128(&(dec_u256){{10, 0, 0, 0}}, m, &base);
    (void)u256_mod_u128(&(dec_u256){{1, 0, 0, 0}}, m, &acc);
    while (d != 0) {
        if ((d & 1) != 0) acc = mulmod_u128(acc, base, m);
        base = mulmod_u128(base, base, m);
        d >>= 1;
    }
    return acc;
}

int dec_rem_trunc(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale) {
    if (dst == 0 || a == 0 || b == 0 || !dec_valid(a) || !dec_valid(b))
        return DEC_INVALID;
    if (dec_is_zero(b)) return DEC_DIV_ZERO;
    if (dec_is_zero(a))
        return dec_set(dst, 0, 0, 0, 0, result_dscale);

    int ea = (int16_t)a->exp, eb = (int16_t)b->exp;
    int d = ea - eb;
    dec_u128 ca = ((dec_u128)a->hi << 64) | a->lo;
    dec_u128 cb = ((dec_u128)b->hi << 64) | b->lo;
    dec_u128 rem = 0;
    int exp;
    if (d >= 0) {
        dec_u128 amod = 0;
        int st = u256_mod_u128(&(dec_u256){{a->lo, a->hi, 0, 0}}, cb, &amod);
        if (st != DEC_OK) return st;
        rem = mulmod_u128(amod, pow10mod((dec_u128)(unsigned)d, cb), cb);
        exp = eb;
    } else {
        int big = -d;
        if (big >= 38) {
            /* ca < 10^38 <= cb*10^big, so |a| < |b| and trunc(a/b) is zero. */
            rem = ca;
        } else {
            dec_u256 bb;
            u256_from_u128(&bb, (uint64_t)(cb >> 64), (uint64_t)cb);
            int st = u256_mul_pow10(&bb, big);
            if (st != DEC_OK) return DEC_OVERFLOW;
            dec_u256 nn;
            u256_from_u128(&nn, a->hi, a->lo);
            if (dec_u256_cmp(&nn, &bb) >= 0) {
                dec_u256 qq, rr;
                divmod_u256(&qq, &rr, &nn, &bb);
                if (u256_to_u128(&rr, &rem) != DEC_OK) return DEC_OVERFLOW;
            } else {
                rem = ca;
            }
        }
        exp = ea;
    }

    if (rem == 0)
        return dec_set(dst, 0, 0, 0, 0, result_dscale);
    return dec_set(dst, a->sign, (uint64_t)rem, (uint64_t)(rem >> 64), exp,
                   result_dscale);
}

/* ------------------------------ rounded division ----------------------- */

static int cmp_twice_remainder(const dec_u256 *r, const dec_u256 *d) {
    dec_u256 two = {{
        r->w[0] << 1,
        (r->w[1] << 1) | (r->w[0] >> 63),
        (r->w[2] << 1) | (r->w[1] >> 63),
        (r->w[3] << 1) | (r->w[2] >> 63)
    }};
    return dec_u256_cmp(&two, d);
}

static int round_increment(unsigned mode, int sign, int cmp_half,
                           int rem_nonzero, int q_odd) {
    switch (mode) {
        case DEC_ROUND_HALF_EVEN:
            return cmp_half > 0 || (cmp_half == 0 && q_odd);
        case DEC_ROUND_HALF_AWAY:
            return cmp_half >= 0;
        case DEC_ROUND_HALF_TOWARD_ZERO:
            return cmp_half > 0;
        case DEC_ROUND_TOWARD_ZERO:
            return 0;
        case DEC_ROUND_AWAY:
            return rem_nonzero;
        case DEC_ROUND_FLOOR:
            return sign != 0 && rem_nonzero;
        case DEC_ROUND_CEILING:
            return sign == 0 && rem_nonzero;
        case DEC_ROUND_UNNECESSARY:
            return rem_nonzero ? -1 : 0;
        default:
            return -2;
    }
}

static int division_adjusted(const dec24 *a, const dec24 *b, int64_t *adj) {
    int da = digits_u128(((dec_u128)a->hi << 64) | a->lo);
    int db = digits_u128(((dec_u128)b->hi << 64) | b->lo);
    int64_t base = (int64_t)(int16_t)a->exp - (int16_t)b->exp;
    dec_u128 ca = ((dec_u128)a->hi << 64) | a->lo;
    dec_u128 cb = ((dec_u128)b->hi << 64) | b->lo;
    if (da >= db) {
        int sh = da - db;
        dec_u256 scaled;
        u256_from_u128(&scaled, (uint64_t)(cb >> 64), (uint64_t)cb);
        if (u256_mul_pow10(&scaled, sh) != DEC_OK) return DEC_OVERFLOW;
        dec_u256 ca_v;
        u256_from_u128(&ca_v, a->hi, a->lo);
        int k = dec_u256_cmp(&ca_v, &scaled) >= 0 ? 0 : -1;
        *adj = base + sh + k;
    } else {
        int sh = db - da;
        dec_u256 scaled;
        u256_from_u128(&scaled, (uint64_t)(ca >> 64), (uint64_t)ca);
        if (u256_mul_pow10(&scaled, sh) != DEC_OK) return DEC_OVERFLOW;
        dec_u256 cb_v;
        u256_from_u128(&cb_v, b->hi, b->lo);
        int k = dec_u256_cmp(&scaled, &cb_v) >= 0 ? 0 : -1;
        *adj = base + (da - db) + k;
    }
    return DEC_OK;
}

static int u256_pow10_value(int p, dec_u256 *out) {
    *out = (dec_u256){{1, 0, 0, 0}};
    return u256_mul_pow10(out, p);
}

static int division_scaled(dec24 *dst, const dec24 *a, const dec24 *b,
                           int32_t scale, unsigned mode, unsigned carry_digits,
                           uint16_t dscale) {
    int64_t t = (int64_t)(int16_t)a->exp - (int16_t)b->exp + scale;
    dec_u256 n, d;
    u256_from_u128(&n, a->hi, a->lo);
    dec_u256 cb_v;
    u256_from_u128(&cb_v, b->hi, b->lo);
    int d_overflow = 0;

    if (t >= 0) {
        int st = u256_mul_pow10(&n, (int)t);
        if (st != DEC_OK) return DEC_OVERFLOW;
        d = cb_v;
    } else {
        d = cb_v;
        int st = u256_mul_pow10(&d, (int)(-t));
        if (st == DEC_OVERFLOW) {
            d_overflow = 1;
        } else if (st != DEC_OK) {
            return st;
        }
    }

    dec_u256 q, r;
    if (d_overflow) {
        q = (dec_u256){{0, 0, 0, 0}};
        r = n;
    } else {
        if (dec_u256_is_zero(&d)) return DEC_DIV_ZERO;
        divmod_u256(&q, &r, &n, &d);
    }

    if (u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) > 0)
        return DEC_OVERFLOW;

    int sign = a->sign ^ b->sign;
    int rem_nonzero = !dec_u256_is_zero(&r);
    int cmp_half = d_overflow ? -1 : cmp_twice_remainder(&r, &d);
    int inc = round_increment(mode, sign, cmp_half, rem_nonzero,
                              (int)(q.w[0] & 1u));
    if (inc == -1) return DEC_INEXACT;
    if (inc == -2) return DEC_INVALID;
    if (inc) {
        if (u256_cmp_u128(&q, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
            return DEC_OVERFLOW;
        int st = u256_add_small(&q, 1);
        if (st != DEC_OK) return DEC_OVERFLOW;
    }
    if (u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) > 0)
        return DEC_OVERFLOW;

    int64_t exp = -(int64_t)scale;
    dec_u256 carry;
    if (u256_pow10_value((int)carry_digits, &carry) != DEC_OK)
        return DEC_OVERFLOW;
    if (dec_u256_cmp(&q, &carry) == 0) {
        uint64_t rem = 0;
        int st = dec_div256_64(&q, &rem, &q, 10);
        if (st != DEC_OK || rem != 0) return DEC_OVERFLOW;
        exp++;
    }
    if (dec_u256_is_zero(&q))
        return dec_set(dst, 0, 0, 0, 0, dscale);
    /* If the requested precision pushes the exponent below int16, trailing
     * coefficient zeros can express the same value with a coarser exponent. */
    while (exp < -32768) {
        uint64_t rem = 0;
        int st = dec_div256_64(&q, &rem, &q, 10);
        if (st != DEC_OK || rem != 0) break;
        exp++;
    }
    while (exp > 32767) {
        if (dec_u256_digits(&q) >= 38) return DEC_OVERFLOW;
        int st = u256_mul_small(&q, 10);
        if (st != DEC_OK) return DEC_OVERFLOW;
        exp--;
    }
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    return dec_set(dst, sign, q.w[0], q.w[1], (int)exp, dscale);
}

int dec_div(dec24 *dst, const dec24 *a, const dec24 *b,
            const dec_div_request *req) {
    if (dst == 0 || a == 0 || b == 0 || req == 0 ||
        !dec_valid(a) || !dec_valid(b) || req->mode >= DEC_ROUND_MODE_COUNT)
        return DEC_INVALID;
    if (dec_is_zero(b)) return DEC_DIV_ZERO;
    if (req->kind == DEC_DIV_ROUND_SCALE) {
        if (req->scale < -32767 || req->scale > 32768)
            return DEC_UNSUPPORTED_RANGE;
        if (dec_is_zero(a))
            return dec_set(dst, 0, 0, 0, 0, req->dscale);
        return division_scaled(dst, a, b, req->scale, req->mode, 38,
                               req->dscale);
    }
    if (req->kind == DEC_DIV_ROUND_PRECISION) {
        if (req->precision < 1 || req->precision > 38)
            return DEC_INVALID;
        if (dec_is_zero(a))
            return dec_set(dst, 0, 0, 0, 0, req->dscale);
        int64_t adj;
        int st = division_adjusted(a, b, &adj);
        if (st != DEC_OK) return st;
        int64_t exp = adj - (int64_t)req->precision + 1;
        /* Let division_scaled try the requested precision; it may rescue an
         * out-of-int16 exponent by moving coefficient trailing zeros on
         * either side.  The derived scale always fits int32. */
        return division_scaled(dst, a, b, (int32_t)(-exp), req->mode,
                               req->precision, req->dscale);
    }
    return DEC_INVALID;
}

int dec_div_scale(dec24 *dst, const dec24 *a, const dec24 *b,
                  int32_t scale, unsigned mode, uint16_t result_dscale) {
    dec_div_request req;
    req.kind = DEC_DIV_ROUND_SCALE;
    req.scale = scale;
    req.precision = 0;
    req.mode = mode;
    req.dscale = result_dscale;
    return dec_div(dst, a, b, &req);
}

int dec_div_precision(dec24 *dst, const dec24 *a, const dec24 *b,
                      unsigned precision, unsigned mode,
                      uint16_t result_dscale) {
    dec_div_request req;
    req.kind = DEC_DIV_ROUND_PRECISION;
    req.scale = 0;
    req.precision = precision;
    req.mode = mode;
    req.dscale = result_dscale;
    return dec_div(dst, a, b, &req);
}
