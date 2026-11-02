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
    uint64_t qh = 0, rh = hi, ql, r;
    /* Skip the high-word DIV when the high word already fits the divisor.
     * Division by ten uses the compiler's reciprocal sequence. */
    if (d == 10) {
        qh = hi / 10;
        rh = hi % 10;
        if (hi == 0) {
            *qhi = 0;
            *qlo = lo / 10;
            *rem = lo % 10;
            return DEC_OK;
        }
    } else if (hi >= d) {
        divq(0, hi, d, &qh, &rh);
    }
    divq(rh, lo, d, &ql, &r);
    *qhi = qh;
    *qlo = ql;
    *rem = r;
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
        /* Induction: r < d before each DIV, so the 64-bit quotient fits. */
        divq(r, n->w[i], d, &out.w[i], &r);
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

static const dec_u256 dec_pow10_u256[78] = {
    {{0x0000000000000001ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x000000000000000aULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x0000000000000064ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00000000000003e8ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x0000000000002710ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00000000000186a0ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00000000000f4240ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x0000000000989680ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x0000000005f5e100ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x000000003b9aca00ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00000002540be400ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x000000174876e800ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x000000e8d4a51000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x000009184e72a000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00005af3107a4000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00038d7ea4c68000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x002386f26fc10000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x016345785d8a0000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x0de0b6b3a7640000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x8ac7230489e80000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x6bc75e2d63100000ULL, 0x0000000000000005ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x35c9adc5dea00000ULL, 0x0000000000000036ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x19e0c9bab2400000ULL, 0x000000000000021eULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x02c7e14af6800000ULL, 0x000000000000152dULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x1bcecceda1000000ULL, 0x000000000000d3c2ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x161401484a000000ULL, 0x0000000000084595ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0xdcc80cd2e4000000ULL, 0x000000000052b7d2ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x9fd0803ce8000000ULL, 0x00000000033b2e3cULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x3e25026110000000ULL, 0x00000000204fce5eULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x6d7217caa0000000ULL, 0x00000001431e0faeULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x4674edea40000000ULL, 0x0000000c9f2c9cd0ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0xc0914b2680000000ULL, 0x0000007e37be2022ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x85acef8100000000ULL, 0x000004ee2d6d415bULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x38c15b0a00000000ULL, 0x0000314dc6448d93ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x378d8e6400000000ULL, 0x0001ed09bead87c0ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x2b878fe800000000ULL, 0x0013426172c74d82ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0xb34b9f1000000000ULL, 0x00c097ce7bc90715ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x00f436a000000000ULL, 0x0785ee10d5da46d9ULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x098a224000000000ULL, 0x4b3b4ca85a86c47aULL, 0x0000000000000000ULL, 0x0000000000000000ULL}},
    {{0x5f65568000000000ULL, 0xf050fe938943acc4ULL, 0x0000000000000002ULL, 0x0000000000000000ULL}},
    {{0xb9f5610000000000ULL, 0x6329f1c35ca4bfabULL, 0x000000000000001dULL, 0x0000000000000000ULL}},
    {{0x4395ca0000000000ULL, 0xdfa371a19e6f7cb5ULL, 0x0000000000000125ULL, 0x0000000000000000ULL}},
    {{0xa3d9e40000000000ULL, 0xbc627050305adf14ULL, 0x0000000000000b7aULL, 0x0000000000000000ULL}},
    {{0x6682e80000000000ULL, 0x5bd86321e38cb6ceULL, 0x00000000000072cbULL, 0x0000000000000000ULL}},
    {{0x011d100000000000ULL, 0x9673df52e37f2410ULL, 0x0000000000047bf1ULL, 0x0000000000000000ULL}},
    {{0x0b22a00000000000ULL, 0xe086b93ce2f768a0ULL, 0x00000000002cd76fULL, 0x0000000000000000ULL}},
    {{0x6f5a400000000000ULL, 0xc5433c60ddaa1640ULL, 0x0000000001c06a5eULL, 0x0000000000000000ULL}},
    {{0x5986800000000000ULL, 0xb4a05bc8a8a4de84ULL, 0x00000000118427b3ULL, 0x0000000000000000ULL}},
    {{0x7f41000000000000ULL, 0x0e4395d69670b12bULL, 0x00000000af298d05ULL, 0x0000000000000000ULL}},
    {{0xf88a000000000000ULL, 0x8ea3da61e066ebb2ULL, 0x00000006d79f8232ULL, 0x0000000000000000ULL}},
    {{0xb564000000000000ULL, 0x926687d2c40534fdULL, 0x000000446c3b15f9ULL, 0x0000000000000000ULL}},
    {{0x15e8000000000000ULL, 0xb8014e3ba83411e9ULL, 0x000002ac3a4edbbfULL, 0x0000000000000000ULL}},
    {{0xdb10000000000000ULL, 0x300d0e549208b31aULL, 0x00001aba4714957dULL, 0x0000000000000000ULL}},
    {{0x8ea0000000000000ULL, 0xe0828f4db456ff0cULL, 0x00010b46c6cdd6e3ULL, 0x0000000000000000ULL}},
    {{0x9240000000000000ULL, 0xc51999090b65f67dULL, 0x000a70c3c40a64e6ULL, 0x0000000000000000ULL}},
    {{0xb680000000000000ULL, 0xb2fffa5a71fba0e7ULL, 0x006867a5a867f103ULL, 0x0000000000000000ULL}},
    {{0x2100000000000000ULL, 0xfdffc78873d4490dULL, 0x04140c78940f6a24ULL, 0x0000000000000000ULL}},
    {{0x4a00000000000000ULL, 0xebfdcb54864ada83ULL, 0x28c87cb5c89a2571ULL, 0x0000000000000000ULL}},
    {{0xe400000000000000ULL, 0x37e9f14d3eec8920ULL, 0x97d4df19d6057673ULL, 0x0000000000000001ULL}},
    {{0xe800000000000000ULL, 0x2f236d04753d5b48ULL, 0xee50b7025c36a080ULL, 0x000000000000000fULL}},
    {{0x1000000000000000ULL, 0xd762422c946590d9ULL, 0x4f2726179a224501ULL, 0x000000000000009fULL}},
    {{0xa000000000000000ULL, 0x69d695bdcbf7a87aULL, 0x17877cec0556b212ULL, 0x0000000000000639ULL}},
    {{0x4000000000000000ULL, 0x2261d969f7ac94caULL, 0xeb4ae1383562f4b8ULL, 0x0000000000003e3aULL}},
    {{0x8000000000000000ULL, 0x57d27e23acbdcfe6ULL, 0x30eccc3215dd8f31ULL, 0x0000000000026e4dULL}},
    {{0x0000000000000000ULL, 0x6e38ed64bf6a1f01ULL, 0xe93ff9f4daa797edULL, 0x0000000000184f03ULL}},
    {{0x0000000000000000ULL, 0x4e3945ef7a25360aULL, 0x1c7fc3908a8bef46ULL, 0x0000000000f31627ULL}},
    {{0x0000000000000000ULL, 0x0e3cbb5ac5741c64ULL, 0x1cfda3a5697758bfULL, 0x00000000097edd87ULL}},
    {{0x0000000000000000ULL, 0x8e5f518bb6891be8ULL, 0x21e864761ea97776ULL, 0x000000005ef4a747ULL}},
    {{0x0000000000000000ULL, 0x8fb92f75215b1710ULL, 0x5313ec9d329eaaa1ULL, 0x00000003b58e88c7ULL}},
    {{0x0000000000000000ULL, 0x9d3bda934d8ee6a0ULL, 0x3ec73e23fa32aa4fULL, 0x00000025179157c9ULL}},
    {{0x0000000000000000ULL, 0x245689c107950240ULL, 0x73c86d67c5faa71cULL, 0x00000172ebad6ddcULL}},
    {{0x0000000000000000ULL, 0x6b61618a4bd21680ULL, 0x85d4460dbbca8719ULL, 0x00000e7d34c64a9cULL}},
    {{0x0000000000000000ULL, 0x31cdcf66f634e100ULL, 0x3a4abc8955e946feULL, 0x000090e40fbeea1dULL}},
    {{0x0000000000000000ULL, 0xf20a1a059e10ca00ULL, 0x46eb5d5d5b1cc5edULL, 0x0005a8e89d752524ULL}},
    {{0x0000000000000000ULL, 0x746504382ca7e400ULL, 0xc531a5a58f1fbb4bULL, 0x003899162693736aULL}},
    {{0x0000000000000000ULL, 0x8bf22a31be8ee800ULL, 0xb3f07877973d50f2ULL, 0x0235fadd81c2822bULL}},
    {{0x0000000000000000ULL, 0x7775a5f171951000ULL, 0x0764b4abe8652979ULL, 0x161bcca7119915b5ULL}},
    {{0x0000000000000000ULL, 0xaa987b6e6fd2a000ULL, 0x49ef0eb713f39ebeULL, 0xdd15fe86affad912ULL}},
};
int dec_u256_digits(const dec_u256 *a) {
    int limb = 3;
    while (limb >= 0 && a->w[limb] == 0) limb--;
    if (limb < 0) return 0;
    unsigned bits = (unsigned)limb * 64u + 64u -
                    (unsigned)__builtin_clzll(a->w[limb]);
    /* log10(2) ~= 1233/4096; the table compare corrects the estimate. */
    int d = (int)(((bits - 1u) * 1233u) >> 12) + 1;
    if (d < 78 && dec_u256_cmp(a, &dec_pow10_u256[d]) >= 0) d++;
    return d;
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
static const uint64_t dec_pow10_u64[20] = {
    0x0000000000000001ULL,
    0x000000000000000aULL,
    0x0000000000000064ULL,
    0x00000000000003e8ULL,
    0x0000000000002710ULL,
    0x00000000000186a0ULL,
    0x00000000000f4240ULL,
    0x0000000000989680ULL,
    0x0000000005f5e100ULL,
    0x000000003b9aca00ULL,
    0x00000002540be400ULL,
    0x000000174876e800ULL,
    0x000000e8d4a51000ULL,
    0x000009184e72a000ULL,
    0x00005af3107a4000ULL,
    0x00038d7ea4c68000ULL,
    0x002386f26fc10000ULL,
    0x016345785d8a0000ULL,
    0x0de0b6b3a7640000ULL,
    0x8ac7230489e80000ULL
};

/* Drop k decimal digits from q in up to 19-digit chunks, capturing the first
 * discarded digit and a sticky flag for the final rounding. */
static int dec_drop_digits(dec_u256 *q, int k, int *first, int *sticky) {
    *first = 0;
    *sticky = 0;
    while (k > 0) {
        int take = k > 19 ? 19 : k;
        uint64_t rem = 0;
        int st = dec_div256_64(q, &rem, q, dec_pow10_u64[take]);
        if (st != DEC_OK) return st;
        k -= take;
        if (k > 0) {
            *sticky |= rem != 0;
        } else {
            uint64_t p = dec_pow10_u64[take - 1];
            *first = (int)(rem / p);
            *sticky |= rem % p != 0;
        }
    }
    return DEC_OK;
}

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
    int st = dec_drop_digits(&q, k, &first, &sticky);
    if (st != DEC_OK) return st;
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
    while (shift > 0) {
        int take = shift > 19 ? 19 : shift;
        int st = u256_mul_small(a, dec_pow10_u64[take]);
        if (st != DEC_OK) return st;
        shift -= take;
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

/* Write a value whose coefficient is already in range, rescuing an exponent
 * just outside int16 exactly like the parser's upper-edge rule.  Below the
 * range only trailing zeros can be removed; above it, trailing zeros can be
 * appended while the coefficient stays within 38 digits.  This preserves the
 * value; it does not add precision or display scale. */
static int dec_write_rescued(dec24 *dst, int sign, uint64_t lo, uint64_t hi,
                             int64_t exp, uint16_t dscale) {
    if (exp >= -32768 && exp <= 32767)
        return dec_set(dst, sign, lo, hi, (int)exp, dscale);
    /* At most 37 trailing zeros can be moved without exceeding 38 digits. */
    if (exp > 32804 || exp < -32805) return DEC_OVERFLOW;
    dec_u256 c;
    u256_from_u128(&c, hi, lo);
    if (dec_u256_is_zero(&c))
        return dec_set(dst, 0, 0, 0, 0, dscale);
    int digits = dec_u256_digits(&c);
    while (exp < -32768) {
        uint64_t rem = 0;
        int st = dec_div256_64(&c, &rem, &c, 10);
        if (st != DEC_OK || rem != 0) return DEC_OVERFLOW;
        exp++;
        digits--;
    }
    while (exp > 32767 && digits < 38) {
        int st = u256_mul_small(&c, 10);
        if (st != DEC_OK) return DEC_OVERFLOW;
        exp--;
        digits++;
    }
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    if (u256_cmp_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
        return DEC_OVERFLOW;
    return dec_set(dst, sign, c.w[0], c.w[1], (int)exp, dscale);
}

/* ----------------------------- operations ------------------------------ */

/* Sticky fallback for a gap wider than the 38-significant-digit window.
 *
 * The smaller operand's leading digit lies at least 39 places below the
 * larger magnitude, so its only possible effect is an inexact flag plus a
 * one-ulp adjustment for the directed modes.  Nearest modes never reach half
 * an ulp.  The exact result is therefore never representable in 38 digits;
 * the rounded value is the larger operand's 38-digit grid point, optionally
 * moved by one unit.  A subtraction borrow and its sign/mode asymmetry are
 * handled explicitly; the smaller operand is never simply dropped. */
static int u128_is_pow10(uint64_t lo, uint64_t hi, int *power) {
    int n = 0;
    while (lo != 1 || hi != 0) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(hi, lo, 10, &qh, &ql, &rem) != DEC_OK || rem != 0)
            return 0;
        hi = qh;
        lo = ql;
        n++;
        if (lo == 0 && hi == 0) return 0;
    }
    *power = n;
    return 1;
}

/* Compare |small| with half of the lower-decade unit below a power-of-ten
 * larger operand.  Only then can a nearest-mode subtraction see a tie or
 * choose the predecessor; for a non-power-of-ten larger operand the small
 * operand is at most a tenth of the upper-decade unit. */
static int sticky_compare_half(const dec24 *big, const dec24 *small,
                               int dbig, int dsmall) {
    int64_t big_top = (int64_t)(int16_t)big->exp + dbig - 1;
    int64_t small_top = (int64_t)(int16_t)small->exp + dsmall - 1;
    int64_t half_top = big_top - 39;
    if (small_top < half_top) return -1;
    if (small_top > half_top) return 1;
    dec_u256 sc, threshold;
    u256_from_u128(&sc, small->hi, small->lo);
    u256_from_u128(&threshold, 0, 5);
    if (u256_mul_pow10(&threshold, dsmall - 1) != DEC_OK) return 0;
    return dec_u256_cmp(&sc, &threshold);
}

static int dec_add_sticky_round(dec24 *dst, const dec24 *a, const dec24 *b,
                                unsigned mode, uint16_t dscale) {
    dec_u256 av, bv;
    u256_from_u128(&av, a->hi, a->lo);
    u256_from_u128(&bv, b->hi, b->lo);
    int da = dec_u256_digits(&av), db = dec_u256_digits(&bv);
    int64_t adj_a = (int64_t)(int16_t)a->exp + da;
    int64_t adj_b = (int64_t)(int16_t)b->exp + db;
    const dec24 *big = adj_a >= adj_b ? a : b;
    int64_t gap = adj_a >= adj_b ? adj_a - adj_b : adj_b - adj_a;
    if (gap <= 38) return DEC_UNSUPPORTED_EXACT;
    if (mode == DEC_ROUND_UNNECESSARY) return DEC_INEXACT;

    int sign = big->sign;
    int dbig = big == a ? da : db;
    int dsmall = big == a ? db : da;
    const dec24 *small = big == a ? b : a;
    int same_sign = a->sign == b->sign;
    int power = 0;
    int is_pow10 = u128_is_pow10(big->lo, big->hi, &power);
    int inc = 0, dec = 0;
    if (same_sign) {
        inc = mode == DEC_ROUND_AWAY ||
              (mode == DEC_ROUND_FLOOR && sign != 0) ||
              (mode == DEC_ROUND_CEILING && sign == 0);
    } else if (mode == DEC_ROUND_HALF_EVEN ||
               mode == DEC_ROUND_HALF_AWAY ||
               mode == DEC_ROUND_HALF_TOWARD_ZERO) {
        if (is_pow10) {
            int cmp = sticky_compare_half(big, small, dbig, dsmall);
            if (cmp > 0) {
                dec = 1;
            } else if (cmp == 0 && mode == DEC_ROUND_HALF_TOWARD_ZERO) {
                dec = 1;
            }
        }
    } else if (sign == 0) {
        dec = mode == DEC_ROUND_TOWARD_ZERO || mode == DEC_ROUND_FLOOR;
    } else {
        dec = mode == DEC_ROUND_TOWARD_ZERO || mode == DEC_ROUND_CEILING;
    }

    if (!inc && !dec) {
        /* The larger operand is already on the 38-digit grid. */
        return dec_set(dst, sign, big->lo, big->hi, (int16_t)big->exp,
                       dscale);
    }

    int64_t exp = (int64_t)(int16_t)big->exp + dbig - 38;
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    dec_u256 c;
    u256_from_u128(&c, big->hi, big->lo);
    int st = u256_mul_pow10(&c, 38 - dbig);
    if (st != DEC_OK) return DEC_OVERFLOW;
    if (inc) {
        st = u256_add_small(&c, 1);
        if (st != DEC_OK) return DEC_OVERFLOW;
    } else if (is_pow10) {
        /* Just below an exact power of ten the neighbouring 38-digit value
         * lives in the lower decade: 10^k - 10^(k-38), not 10^k - 10^(k-37).
         * This is the power-of-ten cancellation boundary, not a dropped
         * borrow; all nines have 38 significant digits. */
        u256_from_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO);
        exp--;
    } else {
        dec_u256 one = {{1, 0, 0, 0}};
        (void)u256_sub(&c, &c, &one);
    }
    if (u256_cmp_u128(&c, DEC10_38_HI, DEC10_38_LO) == 0) {
        uint64_t rem = 0;
        st = dec_div256_64(&c, &rem, &c, 10);
        if (st != DEC_OK || rem != 0) return DEC_OVERFLOW;
        exp++;
    }
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    if (u256_cmp_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
        return DEC_OVERFLOW;
    return dec_set(dst, sign, c.w[0], c.w[1], (int)exp, dscale);
}

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
    /* Prefer the exact aligned accumulator when it fits.  This is not a
     * decimal-digit rule (digits + shift <= 76): each shift is <= 76 and each
     * aligned operand/sum must fit the 256-bit accumulator.  Thus 9e76 + 1
     * and 109e75 + 1 remain on the exact path, while 12e76 + 1 falls through
     * to dec_add_sticky_round because the aligned operand exceeds 2^256.
     * The sticky path rounds once to 38 digits, handles the sign- and
     * mode-dependent neighbour and records DEC_INEXACT for exact callers;
     * it is not a guessed or silently dropped wide-gap result. */
    if (sha > 76 || shb > 76)
        return dec_add_sticky_round(dst, a, b, mode, ds);
    dec_u256 ma, mb;
    u256_from_u128(&ma, a->hi, a->lo);
    u256_from_u128(&mb, b->hi, b->lo);
    int st;
    if (sha && (st = u256_mul_pow10(&ma, sha)) != DEC_OK)
        return dec_add_sticky_round(dst, a, b, mode, ds);
    if (shb && (st = u256_mul_pow10(&mb, shb)) != DEC_OK)
        return dec_add_sticky_round(dst, a, b, mode, ds);
    dec_u256 m;
    int sign;
    if (a->sign == b->sign) {
        if (u256_add(&m, &ma, &mb))
            return dec_add_sticky_round(dst, a, b, mode, ds);
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
    uint64_t lo, hi;
    st = u256_to_coeff(&m, &lo, &hi);
    if (st != DEC_OK) return st;
    return dec_write_rescued(dst, sign, lo, hi, exp, ds);
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
    uint64_t lo, hi;
    st = u256_to_coeff(&p, &lo, &hi);
    if (st != DEC_OK) return st;
    return dec_write_rescued(dst, sign, lo, hi, exp, ds);
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
            int st = dec_drop_digits(&c, (int)k, &first, &sticky);
            if (st != DEC_OK) return st;
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
    dec_u256 ac = {{alo, ahi, 0, 0}}, bc = {{blo, bhi, 0, 0}};
    int adigits = dec_u256_digits(&ac), bdigits = dec_u256_digits(&bc);
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
