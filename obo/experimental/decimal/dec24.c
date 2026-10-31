/* dec24.c — original freestanding prototype code, no vendor code copied.
 *
 * No libc, libm or libgcc arithmetic is used.  Division uses an inline-asm
 * divq primitive and a bitwise 256/128 long division.  See dec24.h for the
 * prototype status and layout.
 */
#include "dec24.h"
#include <stddef.h>

_Static_assert(sizeof(dec24) == 24, "dec24 must be 24 bytes");
_Static_assert(_Alignof(dec24) == 8, "dec24 must be 8-byte aligned");
_Static_assert(offsetof(dec24, lo) == 0, "lo offset");
_Static_assert(offsetof(dec24, hi) == 8, "hi offset");
_Static_assert(offsetof(dec24, exp) == 16, "exp offset");
_Static_assert(offsetof(dec24, dscale) == 18, "dscale offset");
_Static_assert(offsetof(dec24, sign) == 20, "sign offset");
_Static_assert(offsetof(dec24, flags) == 21, "flags offset");
_Static_assert(offsetof(dec24, pad) == 22, "pad offset");

#define DEC10_38_HI 0x4b3b4ca85a86c47aULL
#define DEC10_38_LO 0x098a224000000000ULL

/* ------------------------------ primitives ------------------------------ */

void dec_mul64(uint64_t a, uint64_t b, uint64_t *lo, uint64_t *hi) {
    uint64_t l, h;
    __asm__ volatile("mulq %3" : "=a"(l), "=d"(h) : "a"(a), "rm"(b) : "cc");
    *lo = l;
    *hi = h;
}

static inline void divq(uint64_t hi, uint64_t lo, uint64_t d,
                        uint64_t *q, uint64_t *r) {
    __asm__ volatile("divq %4"
                     : "=a"(*q), "=d"(*r)
                     : "a"(lo), "d"(hi), "rm"(d)
                     : "cc");
}

int dec_div128_64(uint64_t hi, uint64_t lo, uint64_t d,
                  uint64_t *qhi, uint64_t *qlo, uint64_t *rem) {
    if (d == 0) return DEC_DIV_ZERO;
    uint64_t q1, r1, q2, r2;
    divq(0, hi, d, &q1, &r1);
    divq(r1, lo, d, &q2, &r2);
    *qhi = q1;
    *qlo = q2;
    *rem = r2;
    return DEC_OK;
}

static int u256_add_limb(dec_u256 *a, int i, uint64_t v) {
    while (v != 0 && i < 4) {
        uint64_t s = a->w[i] + v;
        v = (s < a->w[i]) ? 1 : 0;
        a->w[i] = s;
        i++;
    }
    return v != 0;
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

int dec_u256_is_zero(const dec_u256 *a) {
    return (a->w[0] | a->w[1] | a->w[2] | a->w[3]) == 0;
}

int dec_u256_cmp(const dec_u256 *a, const dec_u256 *b) {
    for (int i = 3; i >= 0; i--) {
        if (a->w[i] < b->w[i]) return -1;
        if (a->w[i] > b->w[i]) return 1;
    }
    return 0;
}

static int u256_cmp_u128(const dec_u256 *a, uint64_t hi, uint64_t lo) {
    if (a->w[3] != 0 || a->w[2] != 0) return 1;
    if (a->w[1] != hi) return a->w[1] < hi ? -1 : 1;
    if (a->w[0] != lo) return a->w[0] < lo ? -1 : 1;
    return 0;
}

static void u256_from_u128(dec_u256 *a, uint64_t hi, uint64_t lo) {
    a->w[0] = lo;
    a->w[1] = hi;
    a->w[2] = 0;
    a->w[3] = 0;
}

int dec_mul128x128(dec_u256 *r, uint64_t alo, uint64_t ahi,
                   uint64_t blo, uint64_t bhi) {
    r->w[0] = r->w[1] = r->w[2] = r->w[3] = 0;
    uint64_t a[2] = {alo, ahi};
    uint64_t b[2] = {blo, bhi};
    for (int i = 0; i < 2; i++) {
        for (int j = 0; j < 2; j++) {
            uint64_t lo, hi;
            dec_mul64(a[i], b[j], &lo, &hi);
            if (u256_add_limb(r, i + j, lo)) return DEC_OVERFLOW;
            if (u256_add_limb(r, i + j + 1, hi)) return DEC_OVERFLOW;
        }
    }
    return DEC_OK;
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
    return u256_add_limb(a, 0, v) ? DEC_OVERFLOW : DEC_OK;
}

int dec_div256_64(dec_u256 *q, uint64_t *rem, const dec_u256 *n, uint64_t d) {
    if (d == 0) return DEC_DIV_ZERO;
    dec_u256 out = {{0, 0, 0, 0}};
    uint64_t r = 0;
    for (int i = 3; i >= 0; i--) {
        uint64_t qh, ql, rr;
        int st = dec_div128_64(r, n->w[i], d, &qh, &ql, &rr);
        if (st != DEC_OK) return st;
        if (qh != 0) return DEC_OVERFLOW; /* rem < d makes qh zero */
        out.w[i] = ql;
        r = rr;
    }
    *q = out;
    *rem = r;
    return DEC_OK;
}

int dec_div256_128(dec_u256 *q, dec_u256 *rem,
                   const dec_u256 *n, uint64_t dlo, uint64_t dhi) {
    if (dlo == 0 && dhi == 0) return DEC_DIV_ZERO;
    dec_u256 out = {{0, 0, 0, 0}};
    uint64_t r0 = 0, r1 = 0, r2 = 0;
    for (int bit = 255; bit >= 0; bit--) {
        uint64_t in = (n->w[bit >> 6] >> (bit & 63)) & 1ULL;
        uint64_t c2 = r2 >> 63;
        uint64_t c1 = r1 >> 63;
        uint64_t c0 = r0 >> 63;
        (void)c2;
        r2 = (r2 << 1) | c1;
        r1 = (r1 << 1) | c0;
        r0 = (r0 << 1) | in;
        int ge = (r2 != 0) ||
                 (r1 > dhi) ||
                 (r1 == dhi && r0 >= dlo);
        if (ge) {
            uint64_t b0 = (r0 < dlo) ? 1 : 0;
            uint64_t t0 = r0 - dlo;
            uint64_t b1 = (r1 < dhi) ? 1 : 0;
            uint64_t t1 = r1 - dhi;
            uint64_t b2 = (t1 < b0) ? 1 : 0;
            uint64_t t1b = t1 - b0;
            uint64_t b3 = (r2 < (b1 | b2)) ? 1 : 0;
            r0 = t0;
            r1 = t1b;
            r2 = r2 - (b1 | b2);
            (void)b3;
            out.w[bit >> 6] |= 1ULL << (bit & 63);
        }
    }
    *q = out;
    rem->w[0] = r0;
    rem->w[1] = r1;
    rem->w[2] = r2;
    rem->w[3] = 0;
    return DEC_OK;
}

int dec_u256_digits(const dec_u256 *a) {
    dec_u256 t = *a;
    int digits = 0;
    while (!dec_u256_is_zero(&t)) {
        uint64_t rem;
        if (dec_div256_64(&t, &rem, &t, 10) != DEC_OK) return -1;
        digits++;
    }
    return digits;
}

/* --------------------------- decimal helpers --------------------------- */

static int coeff_ok(uint64_t lo, uint64_t hi) {
    return hi < DEC_COEFF_MAX_HI || (hi == DEC_COEFF_MAX_HI && lo <= DEC_COEFF_MAX_LO);
}

/* Validate the documented raw-struct invariants.  This is intentionally a
 * small explicit entry point rather than a change to every return API. */
int dec_valid(const dec24 *a) {
    if (a == 0) return 0;
    if (a->sign > 1) return 0;
    if (!coeff_ok(a->lo, a->hi)) return 0;
    if (a->flags != 0 || a->pad[0] != 0 || a->pad[1] != 0) return 0;
    if (a->lo == 0 && a->hi == 0) return a->sign == 0 && a->exp == 0;
    return 1;
}

void dec_zero(dec24 *d) {
    d->lo = d->hi = 0;
    d->exp = 0;
    d->dscale = 0;
    d->sign = 0;
    d->flags = 0;
    for (int i = 0; i < 2; i++) d->pad[i] = 0;
}

int dec_set(dec24 *d, int sign, uint64_t lo, uint64_t hi,
            int exp, uint16_t dscale) {
    if (sign != 0 && sign != 1) return DEC_INVALID;
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    if (!coeff_ok(lo, hi)) return DEC_OVERFLOW;
    dec_zero(d);
    d->lo = lo;
    d->hi = hi;
    d->dscale = dscale;
    if (lo == 0 && hi == 0) {
        d->sign = 0;
        d->exp = 0;
    } else {
        d->sign = (uint8_t)sign;
        d->exp = (uint16_t)(int16_t)exp;
    }
    return DEC_OK;
}

void dec_copy(dec24 *dst, const dec24 *src) {
    dst->lo = src->lo;
    dst->hi = src->hi;
    dst->exp = src->exp;
    dst->dscale = src->dscale;
    dst->sign = src->sign;
    dst->flags = src->flags;
    for (int i = 0; i < 2; i++) dst->pad[i] = src->pad[i];
}

int dec_is_zero(const dec24 *a) {
    return a->lo == 0 && a->hi == 0;
}

static int mode_ok(unsigned mode) {
    return mode < DEC_ROUND_MODE_COUNT;
}

/* Round a 256-bit magnitude to at most 38 digits.
 * k_done receives the number of decimal places removed (including a
 * post-carry normalization by one digit). */
static int round_to_38(dec_u256 *v, int sign, unsigned mode, int *k_done) {
    if (!mode_ok(mode)) return DEC_INVALID;
    if (dec_u256_is_zero(v)) {
        *k_done = 0;
        return DEC_OK;
    }
    int digits = dec_u256_digits(v);
    if (digits <= 38) {
        *k_done = 0;
        return DEC_OK;
    }
    int k = digits - 38;
    dec_u256 q = *v;
    int first = 0, sticky = 0;
    for (int i = 0; i < k; i++) {
        uint64_t rem;
        int st = dec_div256_64(&q, &rem, &q, 10);
        if (st != DEC_OK) return st;
        if (i == k - 1) first = (int)rem;
        else if (rem != 0) sticky = 1;
    }
    int inc = 0;
    switch (mode) {
        case DEC_ROUND_HALF_EVEN:
            inc = first > 5 || (first == 5 && (sticky || (q.w[0] & 1)));
            break;
        case DEC_ROUND_HALF_AWAY:
            inc = first > 5 || first == 5;
            break;
        case DEC_ROUND_HALF_TOWARD_ZERO:
            inc = first > 5 || (first == 5 && sticky);
            break;
        case DEC_ROUND_TOWARD_ZERO:
            inc = 0;
            break;
        case DEC_ROUND_AWAY:
            inc = first > 0 || sticky;
            break;
        case DEC_ROUND_FLOOR:
            inc = sign != 0 && (first > 0 || sticky);   /* sign 1 is negative */
            break;
        case DEC_ROUND_CEILING:
            inc = sign == 0 && (first > 0 || sticky);   /* sign 0 is positive */
            break;
        case DEC_ROUND_UNNECESSARY:
            if (first > 0 || sticky) return DEC_INEXACT;
            inc = 0;
            break;
        default:
            return DEC_INVALID;
    }
    if (inc) {
        int st = u256_add_small(&q, 1);
        if (st != DEC_OK) return st;
    }
    /* q can be exactly 10^38 after a carry.  This prototype normalises by one
     * decimal digit (coefficient 10^37, exponent +1).  The language rule is
     * still open; tests pin this prototype choice only. */
    if (u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) >= 0) {
        if (u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) != 0) return DEC_OVERFLOW;
        uint64_t rem;
        int st = dec_div256_64(&q, &rem, &q, 10);
        if (st != DEC_OK) return st;
        k++;
    }
    if (u256_cmp_u128(&q, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0) return DEC_OVERFLOW;
    *v = q;
    *k_done = k;
    return DEC_OK;
}

static int u256_mul_pow10(dec_u256 *a, int shift) {
    if (shift < 0) return DEC_INVALID;
    for (int i = 0; i < shift; i++) {
        int st = u256_mul_small(a, 10);
        if (st != DEC_OK) return st;
    }
    return DEC_OK;
}

static int u256_to_coeff(const dec_u256 *a, uint64_t *lo, uint64_t *hi) {
    if (a->w[2] != 0 || a->w[3] != 0) return DEC_OVERFLOW;
    if (!coeff_ok(a->w[0], a->w[1])) return DEC_OVERFLOW;
    *lo = a->w[0];
    *hi = a->w[1];
    return DEC_OK;
}

/* ----------------------------- operations ------------------------------ */

int dec_add(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode) {
    if (dst == 0 || a == 0 || b == 0 || !mode_ok(mode) ||
        !dec_valid(a) || !dec_valid(b)) return DEC_INVALID;
    uint16_t ds = a->dscale > b->dscale ? a->dscale : b->dscale;
    if (dec_is_zero(a) && dec_is_zero(b)) {
        return dec_set(dst, 0, 0, 0, 0, ds);
    }
    if (dec_is_zero(a)) {
        dec_copy(dst, b);
        dst->dscale = ds;
        return DEC_OK;
    }
    if (dec_is_zero(b)) {
        dec_copy(dst, a);
        dst->dscale = ds;
        return DEC_OK;
    }
    int ea = (int16_t)a->exp;
    int eb = (int16_t)b->exp;
    int e = ea < eb ? ea : eb;
    int sha = ea - e;
    int shb = eb - e;
    /* Honest-refusal guard.  This is not a decimal-digit rule
     * (digits + shift <= 76).  We align only when each shift is <= 76, and
     * then each aligned operand and the sum must fit in the 256-bit
     * accumulator.  Thus 9e76 + 1 and 109e75 + 1 are accepted (77/78-digit
     * aligned integers below 2^256), while 12e76 + 1 is refused because the
     * aligned operand exceeds 2^256 even though its 1.2e77 result is
     * representable after rounding.  A wide-gap sticky/one-ulp path is a
     * separate implementation and is deliberately not guessed here. */
    if (sha > 76 || shb > 76) return DEC_UNSUPPORTED_EXACT;
    dec_u256 ma, mb;
    u256_from_u128(&ma, a->hi, a->lo);
    u256_from_u128(&mb, b->hi, b->lo);
    int st;
    if (sha && (st = u256_mul_pow10(&ma, sha)) != DEC_OK) return DEC_UNSUPPORTED_EXACT;
    if (shb && (st = u256_mul_pow10(&mb, shb)) != DEC_OK) return DEC_UNSUPPORTED_EXACT;
    dec_u256 m;
    int sign;
    if (a->sign == b->sign) {
        if (u256_add(&m, &ma, &mb)) return DEC_UNSUPPORTED_EXACT;
        sign = a->sign;
    } else {
        int cmp = dec_u256_cmp(&ma, &mb);
        if (cmp == 0) return dec_set(dst, 0, 0, 0, 0, ds);
        if (cmp > 0) {
            u256_sub(&m, &ma, &mb);
            sign = a->sign;
        } else {
            u256_sub(&m, &mb, &ma);
            sign = b->sign;
        }
    }
    if (dec_u256_is_zero(&m)) return dec_set(dst, 0, 0, 0, 0, ds);
    int k = 0;
    st = round_to_38(&m, sign, mode, &k);
    if (st != DEC_OK) return st;
    int64_t exp = (int64_t)e + k;
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    uint64_t lo, hi;
    st = u256_to_coeff(&m, &lo, &hi);
    if (st != DEC_OK) return st;
    return dec_set(dst, sign, lo, hi, (int)exp, ds);
}

int dec_sub(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode) {
    if (dst == 0 || a == 0 || b == 0 || !mode_ok(mode) ||
        !dec_valid(a) || !dec_valid(b)) return DEC_INVALID;
    dec24 nb;
    dec_copy(&nb, b);
    if (!dec_is_zero(&nb)) nb.sign ^= 1;
    return dec_add(dst, a, &nb, mode);
}

int dec_mul(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode) {
    if (dst == 0 || a == 0 || b == 0 || !mode_ok(mode) ||
        !dec_valid(a) || !dec_valid(b)) return DEC_INVALID;
    uint32_t ds32 = (uint32_t)a->dscale + (uint32_t)b->dscale;
    uint16_t ds = ds32 > 65535 ? 65535 : (uint16_t)ds32;
    if (dec_is_zero(a) || dec_is_zero(b)) {
        return dec_set(dst, 0, 0, 0, 0, ds);
    }
    dec_u256 p;
    int st = dec_mul128x128(&p, a->lo, a->hi, b->lo, b->hi);
    if (st != DEC_OK) return st;
    int sign = a->sign ^ b->sign;
    int k = 0;
    st = round_to_38(&p, sign, mode, &k);
    if (st != DEC_OK) return st;
    int64_t exp = (int64_t)(int16_t)a->exp + (int16_t)b->exp + k;
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    uint64_t lo, hi;
    st = u256_to_coeff(&p, &lo, &hi);
    if (st != DEC_OK) return st;
    return dec_set(dst, sign, lo, hi, (int)exp, ds);
}

/* Decide whether to increment after discarding a known first digit and
 * sticky remainder.  q is used for half-even parity. */
static int decide_increment(unsigned mode, int sign, int first, int sticky,
                            const dec_u256 *q) {
    switch (mode) {
        case DEC_ROUND_HALF_EVEN:
            return first > 5 || (first == 5 && (sticky || (q->w[0] & 1)));
        case DEC_ROUND_HALF_AWAY:
            return first > 5 || first == 5;
        case DEC_ROUND_HALF_TOWARD_ZERO:
            return first > 5 || (first == 5 && sticky);
        case DEC_ROUND_TOWARD_ZERO:
            return 0;
        case DEC_ROUND_AWAY:
            return first > 0 || sticky;
        case DEC_ROUND_FLOOR:
            return sign != 0 && (first > 0 || sticky);
        case DEC_ROUND_CEILING:
            return sign == 0 && (first > 0 || sticky);
        case DEC_ROUND_UNNECESSARY:
            return -1;
        default:
            return -2;
    }
}

int dec_quantize(dec24 *dst, const dec24 *a, uint16_t target_dscale,
                 unsigned mode) {
    if (dst == 0 || a == 0 || !mode_ok(mode) || !dec_valid(a))
        return DEC_INVALID;
    /* Zero is canonical positive with exponent 0: its display scale is
     * independent metadata, not a quantization exponent.  Therefore every
     * uint16 target (including 32769 and 65535) is representable for zero. */
    if (dec_is_zero(a)) {
        return dec_set(dst, 0, 0, 0, 0, target_dscale);
    }
    /* For a nonzero value this prototype's strict quantize sets
     * exp = -target.  target > 32768 cannot fit that quantum exponent in
     * int16, even though a value such as coefficient 1, exp 0, dscale 65535
     * is itself representable: display scale and arithmetic quantum are
     * distinct concepts.  Keep the honest refusal; do not reinterpret the
     * result. */
    if (target_dscale > 32768) return DEC_UNSUPPORTED_RANGE;
    int64_t shift = (int64_t)(int16_t)a->exp + (int64_t)target_dscale;
    dec_u256 c;
    u256_from_u128(&c, a->hi, a->lo);
    int sign = a->sign;
    if (shift >= 0) {
        if (shift > 76) return DEC_OVERFLOW;
        int st = u256_mul_pow10(&c, (int)shift);
        if (st != DEC_OK) return DEC_OVERFLOW;
        if (dec_u256_digits(&c) > 38) return DEC_OVERFLOW;
    } else {
        uint64_t k = (uint64_t)(-shift);
        int digits = dec_u256_digits(&c);
        int first = 0, sticky = 0;
        if (k > (uint64_t)digits + 1) {
            first = 0;
            sticky = 1;
            c.w[0] = c.w[1] = c.w[2] = c.w[3] = 0;
        } else {
            for (uint64_t i = 0; i < k; i++) {
                uint64_t rem;
                int st = dec_div256_64(&c, &rem, &c, 10);
                if (st != DEC_OK) return st;
                if (i == k - 1) first = (int)rem;
                else if (rem != 0) sticky = 1;
            }
        }
        int inc = decide_increment(mode, sign, first, sticky, &c);
        if (inc == -1) {
            if (first > 0 || sticky) return DEC_INEXACT;
            inc = 0;
        } else if (inc == -2) {
            return DEC_INVALID;
        }
        if (inc) {
            int st = u256_add_small(&c, 1);
            if (st != DEC_OK) return st;
        }
        if (dec_u256_is_zero(&c)) {
            return dec_set(dst, 0, 0, 0, 0, target_dscale);
        }
        if (u256_cmp_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
            return DEC_OVERFLOW;
    }
    uint64_t lo, hi;
    int st = u256_to_coeff(&c, &lo, &hi);
    if (st != DEC_OK) return st;
    return dec_set(dst, sign, lo, hi, -(int)target_dscale, target_dscale);
}

/* Numeric comparison, ignoring display scale.  Compare after stripping
 * trailing coefficient zeros; this gives 1.50 == 1.5.  Both zeros are equal.
 * Zero is canonical positive, so zero is greater than every negative value.
 * For two nonzero values of the same sign, compare magnitudes and negate the
 * result for sign 1 in every magnitude branch, including the adjusted-
 * exponent early return. */
int dec_cmp(const dec24 *a, const dec24 *b) {
    if (a == 0 || b == 0) return 0;
    int az = dec_is_zero(a), bz = dec_is_zero(b);
    if (az && bz) return 0;
    if (az) return b->sign ? 1 : -1;
    if (bz) return a->sign ? -1 : 1;
    if (a->sign != b->sign) return a->sign ? -1 : 1;
    uint64_t alo = a->lo, ahi = a->hi;
    uint64_t blo = b->lo, bhi = b->hi;
    int ae = (int16_t)a->exp, be = (int16_t)b->exp;
    int adigits = 0, bdigits = 0;
    for (;;) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(ahi, alo, 10, &qh, &ql, &rem) != DEC_OK || rem != 0) break;
        ahi = qh; alo = ql; ae++;
    }
    /* Count digits of the trailing-zero-stripped coefficients.  Adjusted
     * exponent exp + digits is the magnitude scale; comparing raw exponents
     * alone is wrong when the digit counts differ (1e78 vs 9.08e109). */
    {
        uint64_t th = ahi, tl = alo;
        do {
            uint64_t qh, ql, rem;
            if (dec_div128_64(th, tl, 10, &qh, &ql, &rem) != DEC_OK) break;
            th = qh; tl = ql; adigits++;
        } while (th != 0 || tl != 0);
    }
    for (;;) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(bhi, blo, 10, &qh, &ql, &rem) != DEC_OK || rem != 0) break;
        bhi = qh; blo = ql; be++;
    }
    {
        uint64_t th = bhi, tl = blo;
        do {
            uint64_t qh, ql, rem;
            if (dec_div128_64(th, tl, 10, &qh, &ql, &rem) != DEC_OK) break;
            th = qh; tl = ql; bdigits++;
        } while (th != 0 || tl != 0);
    }
    int aa = ae + adigits, ba = be + bdigits;
    int mag;
    if (aa != ba) {
        mag = aa < ba ? -1 : 1;
    } else if (adigits == bdigits) {
        if (ahi != bhi) mag = ahi < bhi ? -1 : 1;
        else if (alo != blo) mag = alo < blo ? -1 : 1;
        else mag = 0;
    } else {
        /* Equal adjusted exponents imply the coefficient scaling is at most
         * 37 decimal digits, so the larger coefficient fits in 128 bits. */
        dec_u256 av, bv;
        u256_from_u128(&av, ahi, alo);
        u256_from_u128(&bv, bhi, blo);
        if (adigits < bdigits) {
            if (u256_mul_pow10(&av, bdigits - adigits) != DEC_OK) return 0;
        } else {
            if (u256_mul_pow10(&bv, adigits - bdigits) != DEC_OK) return 0;
        }
        mag = dec_u256_cmp(&av, &bv);
    }
    return a->sign ? -mag : mag;
}

int dec_equal(const dec24 *a, const dec24 *b) {
    return dec_cmp(a, b) == 0;
}

const char *dec_status_name(int status) {
    switch (status) {
        case DEC_OK: return "OK";
        case DEC_OVERFLOW: return "OVERFLOW";
        case DEC_UNSUPPORTED_EXACT: return "UNSUPPORTED_EXACT";
        case DEC_INEXACT: return "INEXACT";
        case DEC_INVALID: return "INVALID";
        case DEC_DIV_ZERO: return "DIV_ZERO";
        case DEC_UNSUPPORTED_RANGE: return "UNSUPPORTED_RANGE";
        case DEC_PRECISION_OVERFLOW: return "PRECISION_OVERFLOW";
        case DEC_EXPONENT_RANGE: return "EXPONENT_RANGE";
        case DEC_DISPLAY_RANGE: return "DISPLAY_RANGE";
        case DEC_BUFFER_SMALL: return "BUFFER_SMALL";
        default: return "UNKNOWN";
    }
}

const char *dec_mode_name(unsigned mode) {
    switch (mode) {
        case DEC_ROUND_HALF_EVEN: return "HALF_EVEN";
        case DEC_ROUND_HALF_AWAY: return "HALF_AWAY";
        case DEC_ROUND_HALF_TOWARD_ZERO: return "HALF_TOWARD_ZERO";
        case DEC_ROUND_TOWARD_ZERO: return "TOWARD_ZERO";
        case DEC_ROUND_AWAY: return "AWAY";
        case DEC_ROUND_FLOOR: return "FLOOR";
        case DEC_ROUND_CEILING: return "CEILING";
        case DEC_ROUND_UNNECESSARY: return "UNNECESSARY";
        default: return "UNKNOWN";
    }
}
