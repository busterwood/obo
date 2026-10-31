/* decimal24_core.inc — reviewed decimal core integrated into the obo runtime.
 *
 * Copied from obo/experimental/decimal (D2 private contract) without semantic
 * changes: dec24.h/dec24.c, dec24_arith.h/dec24_arith.c and
 * dec24_text.h/dec24_text.c.  Only the include guards, #include lines and the
 * 24-byte static assertions are omitted so the code can be compiled as one
 * freestanding translation unit.  The arithmetic file's private u256 helpers
 * are renamed with an a_ prefix to avoid colliding with the core's same-named
 * static helpers in this single translation unit.  The reviewed source files
 * stay in place and unchanged.
 *
 * Provenance hashes (sha256):
 *   0954ca0e71b4700b8f2a58739f423bb5807a641792c678141e459b29d54742ad  dec24.c
 *   e71df2a6c45d4909fa83f5ae3e6ef48a1368b7f2b01973a53c8924535f2cd3ff  dec24.h
 *   4bfc67b2ff59e0600c26db8563cf202b9ddd53b8f499a9d2b2e60105be05d491  dec24_text.c
 *   04c8b9133686cdd597a0aebbe3fec79f4443c6f79d46df03b83a51e581e5ce7b  dec24_text.h
 *   fce297916a6f1acf2af8cca81e6ed5940be42d29370d835126bebbc09a7c74da  dec24_arith.c
 *   cf3436646008ba53837bec048c31334970ed5a0917971221c4401380225d37be  dec24_arith.h
 *
 * The runtime helpers at the end are compiler integration, not part of the
 * private decimal contract.  They use the C ABI with explicit pointers, trap
 * with exit 10 and codes 40-49, and never allocate in the arithmetic path.
 */
#ifndef OBO_DECIMAL24_CORE_INC
#define OBO_DECIMAL24_CORE_INC

/* Fixed-width types for the copied core (runtime.c is freestanding). */
typedef unsigned char uint8_t;
typedef signed short int16_t;
typedef unsigned short uint16_t;
typedef signed int int32_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef signed long long int64_t;
typedef unsigned long size_t;

/* dec24.h — original prototype for the proposed 24-byte obo decimal value.
 *
 * This is NOT a language ABI and is not integrated with obo.  It is a
 * freestanding C prototype used to test limb primitives and arithmetic
 * semantics against an independent Python integer model.
 *
 * Layout (little-endian word order, static-asserted in dec24.c):
 *   word 0: coefficient bits 0..63
 *   word 1: coefficient bits 64..127
 *   word 2: bits 0..15  int16 exponent
 *           bits 16..31 uint16 display scale
 *           bits 32..39 sign (0 positive, 1 negative)
 *           bits 40..63 reserved/zero
 *
 * Value = (-1)^sign * coefficient * 10^exponent.  Display scale is separate
 * presentation metadata; it is not the same concept as the arithmetic
 * quantum exponent.  Coefficient <= 10^38 - 1; zero is canonical positive
 * (coefficient 0, sign 0, exponent 0) and may carry any uint16 display scale.
 * Finite only.
 *
 * Struct invariants for a raw dec24: sign is 0 or 1; coefficient is at most
 * 10^38 - 1; flags and both pad bytes are zero; a zero coefficient has sign 0
 * and exponent 0.  `dec_valid` checks exactly these invariants.  Arithmetic
 * entry points validate their input values and return DEC_INVALID rather than
 * accepting a malformed raw struct through a zero fast path.
 *
 * Pointer preconditions: `dec_zero`, `dec_copy`, `dec_is_zero`, `dec_cmp` and
 * `dec_equal` require non-null pointers; they are void/order APIs with no
 * status channel.  `dec_valid(NULL)` returns 0.  Outputs of the status-returning
 * operations are not modified when the operation returns an error.
 */


#define DEC_COEFF_MAX_HI 0x4b3b4ca85a86c47aULL
#define DEC_COEFF_MAX_LO 0x098a223fffffffffULL

typedef struct {
    uint64_t lo;
    uint64_t hi;
    uint16_t exp;       /* int16 bit pattern, two's complement */
    uint16_t dscale;
    uint8_t sign;       /* 0 or 1 */
    uint8_t flags;      /* reserved, zero */
    uint8_t pad[2];     /* reserved, zero */
} dec24;

typedef struct {
    uint64_t w[4];      /* little-endian limbs */
} dec_u256;

enum {
    DEC_OK = 0,
    DEC_OVERFLOW = 1,
    DEC_UNSUPPORTED_EXACT = 2,  /* retained; D2 add/sub use the sticky path */
    DEC_INEXACT = 3,            /* UNNECESSARY with discarded nonzero digits */
    DEC_INVALID = 4,
    DEC_DIV_ZERO = 5,
    DEC_UNSUPPORTED_RANGE = 6,  /* representational range, not a language rule */
    /* Additive D1 statuses used by the private text/binding helpers.  They do
     * not change any existing operation or status value. */
    DEC_PRECISION_OVERFLOW = 7, /* >38 significant digits / binding precision */
    DEC_EXPONENT_RANGE = 8,     /* decimal exponent outside int16 */
    DEC_DISPLAY_RANGE = 9,      /* display scale outside uint16 */
    DEC_BUFFER_SMALL = 10       /* caller buffer lacks room for text + NUL */
};

/* Explicit modes.  There is deliberately no default mode in this prototype. */
enum {
    DEC_ROUND_HALF_EVEN = 0,
    DEC_ROUND_HALF_AWAY = 1,
    DEC_ROUND_HALF_TOWARD_ZERO = 2,
    DEC_ROUND_TOWARD_ZERO = 3,
    DEC_ROUND_AWAY = 4,
    DEC_ROUND_FLOOR = 5,
    DEC_ROUND_CEILING = 6,
    DEC_ROUND_UNNECESSARY = 7,
    DEC_ROUND_MODE_COUNT = 8
};

/* --- primitive helpers exposed for independent tests ------------------- */
void dec_mul64(uint64_t a, uint64_t b, uint64_t *lo, uint64_t *hi);
int dec_div128_64(uint64_t hi, uint64_t lo, uint64_t d,
                  uint64_t *qhi, uint64_t *qlo, uint64_t *rem);
int dec_div256_64(dec_u256 *q, uint64_t *rem, const dec_u256 *n, uint64_t d);
int dec_div256_128(dec_u256 *q, dec_u256 *rem,
                   const dec_u256 *n, uint64_t dlo, uint64_t dhi);
int dec_mul128x128(dec_u256 *r, uint64_t alo, uint64_t ahi,
                    uint64_t blo, uint64_t bhi);
int dec_u256_is_zero(const dec_u256 *a);
int dec_u256_cmp(const dec_u256 *a, const dec_u256 *b);
int dec_u256_digits(const dec_u256 *a);

/* --- decimal value API ------------------------------------------------- */
void dec_zero(dec24 *d);
int dec_set(dec24 *d, int sign, uint64_t lo, uint64_t hi,
            int exp, uint16_t dscale);
void dec_copy(dec24 *dst, const dec24 *src);
int dec_is_zero(const dec24 *a);
int dec_valid(const dec24 *a);
/* Numeric comparison ignores dscale.  Both zeros are equal; zero (canonical
 * positive) is greater than every negative value.  Inputs must satisfy the
 * struct invariants above; use dec_valid first for raw/untrusted structs. */
int dec_cmp(const dec24 *a, const dec24 *b);
int dec_equal(const dec24 *a, const dec24 *b);

/* Mode is mandatory: the prototype does not choose a language default.
 * Malformed input values and invalid modes return DEC_INVALID.
 *
 * D2 completed the add/sub range contract: a gap wider than the 38-digit
 * window uses a sticky path with the correct sign- and mode-dependent one-ulp
 * neighbour (including power-of-ten cancellation).  DEC_UNSUPPORTED_EXACT is
 * no longer returned by these entry points.  They remain *rounded* operations:
 * DEC_OK does not mean the exact result was representable; use the exact
 * entry points in dec24_arith.h when that distinction matters. */
int dec_add(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode);
int dec_sub(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode);
int dec_mul(dec24 *dst, const dec24 *a, const dec24 *b, unsigned mode);
/* Zero accepts every uint16 target dscale, including 32769 and 65535.
 * A nonzero value is still strict: target > 32768 returns
 * DEC_UNSUPPORTED_RANGE because its result quantum exponent -target would not
 * fit in int16.  This is a prototype quantization limit, not an ABI limit on
 * the independent display-scale field. */
int dec_quantize(dec24 *dst, const dec24 *a, uint16_t target_dscale,
                 unsigned mode);

const char *dec_status_name(int status);
const char *dec_mode_name(unsigned mode);


/* dec24.c — original freestanding prototype code, no vendor code copied.
 *
 * No libc, libm or libgcc arithmetic is used.  Division uses an inline-asm
 * divq primitive and a bitwise 256/128 long division.  See dec24.h for the
 * prototype status and layout.
 */


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

/* dec24_arith.h — private arithmetic contract for the experimental dec24.
 *
 * Experimental, not a language ABI, public API, literal policy, default
 * rounding rule, division-scale rule or compiler integration.  The D1
 * dec_add/dec_sub/dec_mul/dec_quantize entry points keep their existing
 * rounded-mode contracts for compatibility.  This header adds an explicit
 * separation between:
 *
 *   exact operations   no rounding.  DEC_OK means the exact mathematical
 *                      result is representable as a 38-significant-digit
 *                      core value.  DEC_INEXACT means it is finite but needs
 *                      more than 38 significant digits (or, for division, the
 *                      quotient is nonterminating); no approximation is
 *                      written.  DEC_OVERFLOW means its normalized exponent
 *                      is outside int16.  Exact results may retain trailing
 *                      zeros, so compare values rather than raw limbs.
 *
 *   rounded operations explicit precision/scale and explicit rounding mode.
 *                      A nonterminating quotient is never guessed: the caller
 *                      must choose dec_div_request.kind and supply either a
 *                      fractional target scale or a significant-digit
 *                      precision, plus a mode.
 *
 * Display scale is independent presentation metadata.  Every new operation
 * takes an explicit result_dscale; there is no implicit add/multiply/divide
 * display-propagation policy in this private contract.  The core value's
 * integer exponent already determines the arithmetic quantum.
 *
 * All operations validate inputs with dec_valid, never allocate, use fixed
 * bounded stack state, never use binary floating point and never call libc,
 * libm, libgcc or a big-integer library.  On every non-OK return *dst is
 * unchanged.  Zero is canonical positive with exponent 0 and may carry the
 * caller's uint16 display scale; negative zero is never produced.
 */




/* Exact operations.  The caller supplies the independent display scale. */
int dec_add_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale);
int dec_sub_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale);
int dec_mul_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale);

/* Exact terminating division: a / b.  The quotient is written only when its
 * exact decimal expansion fits 38 significant digits and an int16 exponent.
 * A nonterminating denominator (a reduced denominator with a prime factor
 * other than 2 or 5) returns DEC_INEXACT.  Division by zero returns
 * DEC_DIV_ZERO; zero divided by nonzero returns canonical zero with
 * result_dscale. */
int dec_div_exact(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale);

/* Explicit truncating remainder.  This is NOT Euclidean or floor-mod:
 *
 *     q = trunc(a / b)          (toward zero)
 *     r = a - q * b              exactly
 *     -|b| < r < |b|, and r has the sign of a (zero is canonical positive).
 *
 * The quotient is not rounded before the multiplication.  The remainder is
 * exact and always fits the core for valid finite inputs.  To use it as a
 * divisor/modulus contract, the caller can recover q with dec_div_exact when
 * the quotient is representable, or with an explicit rounded dec_div call.
 * A zero b returns DEC_DIV_ZERO. */
int dec_rem_trunc(dec24 *dst, const dec24 *a, const dec24 *b,
                  uint16_t result_dscale);

/* Rounded division request.  Both variants round the exact quotient once. */
enum {
    DEC_DIV_ROUND_SCALE = 0,     /* target fractional decimal places */
    DEC_DIV_ROUND_PRECISION = 1  /* target significant digits, 1..38 */
};

typedef struct {
    unsigned kind;       /* DEC_DIV_ROUND_SCALE or DEC_DIV_ROUND_PRECISION */
    int32_t scale;       /* valid for SCALE: -32767..32768 */
    unsigned precision;  /* valid for PRECISION: 1..38 */
    unsigned mode;       /* DEC_ROUND_*; no default */
    uint16_t dscale;     /* independent result display scale */
} dec_div_request;

/* Round a / b using req.  There is no hidden scale or rounding default.
 *
 * SCALE: the result is the exact quotient rounded once to `scale` fractional
 * decimal places.  A nonzero result has exponent -scale, except the documented
 * core carry normalization: if rounding produces coefficient 10^38, it is
 * divided by ten once and the exponent is raised by one.  If the coefficient
 * before the carry check exceeds 10^38-1, or the exponent is outside int16,
 * DEC_OVERFLOW is returned.  A scale outside -32767..32768 returns
 * DEC_UNSUPPORTED_RANGE.
 *
 * PRECISION: the result is the exact quotient rounded once to `precision`
 * significant digits.  The result exponent is derived from the quotient's
 * adjusted exponent, so a value needing fewer significant digits is padded
 * to the requested precision.  Exponent range is handled like the parser:
 * below int16, coefficient trailing zeros are removed only as far as needed;
 * above int16, trailing zeros are appended (the upper-exponent rescue) until
 * the exponent fits.  The stored coefficient can therefore be longer than the
 * requested precision: 1e32800 at precision 1 becomes coefficient 10^33 with
 * exponent 32767 (34 coefficient digits), and 1e32805 still overflows because
 * 39 digits would be required.  The numeric value is unchanged and display
 * scale remains independent.  DEC_OVERFLOW is returned only when the exponent
 * cannot be brought into int16 within 38 coefficient digits.
 *
 * UNNECESSARY returns DEC_INEXACT if any nonzero digit would be discarded.
 * Representability and range take precedence: if the rounded result cannot fit
 * 38 digits or int16 even before the mode decision, DEC_OVERFLOW is returned.
 * Division by zero returns DEC_DIV_ZERO.  Zero divided by nonzero returns
 * canonical zero with req->dscale.  An invalid kind, mode or precision returns
 * DEC_INVALID.  No IEEE NaN/Inf or binary floating point is involved. */
int dec_div(dec24 *dst, const dec24 *a, const dec24 *b,
            const dec_div_request *req);

/* Convenience wrappers for the two explicit request kinds. */
int dec_div_scale(dec24 *dst, const dec24 *a, const dec24 *b,
                  int32_t scale, unsigned mode, uint16_t result_dscale);
int dec_div_precision(dec24 *dst, const dec24 *a, const dec24 *b,
                      unsigned precision, unsigned mode,
                      uint16_t result_dscale);


/* dec24_arith.c — private exact and explicitly rounded decimal arithmetic.
 *
 * This file is independent of the D1 core's static internals: it uses the
 * public limb primitives and adds fixed bounded helpers.  No libc, libm,
 * libgcc division, allocation or binary floating point.  See dec24_arith.h
 * for the contract; README.md records what is proposed versus implemented.
 */


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

static void a_u256_from_u128(dec_u256 *a, uint64_t hi, uint64_t lo) {
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

static int a_u256_add(dec_u256 *r, const dec_u256 *a, const dec_u256 *b) {
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

static int a_u256_sub(dec_u256 *r, const dec_u256 *a, const dec_u256 *b) {
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

static int a_u256_mul_small(dec_u256 *a, uint64_t m) {
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

static int a_u256_add_small(dec_u256 *a, uint64_t v) {
    dec_u256 one = {{v, 0, 0, 0}};
    return a_u256_add(a, a, &one) ? DEC_OVERFLOW : DEC_OK;
}

static int a_u256_mul_pow10(dec_u256 *a, int shift) {
    if (shift < 0) return DEC_INVALID;
    while (shift > 0) {
        int take = shift > 19 ? 19 : shift;
        int st = a_u256_mul_small(a, dec_pow10_64[take]);
        if (st != DEC_OK) return st;
        shift -= take;
    }
    return DEC_OK;
}

static int a_u256_cmp_u128(const dec_u256 *a, uint64_t hi, uint64_t lo) {
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
    a_u256_from_u128(&t, (uint64_t)(v >> 64), (uint64_t)v);
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
        int st = a_u256_mul_small(&c, 10);
        if (st != DEC_OK) return DEC_OVERFLOW;
        exp--;
        digits++;
    }
    if (exp < -32768 || exp > 32767) return DEC_OVERFLOW;
    if (a_u256_cmp_u128(&c, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
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
    a_u256_from_u128(&ma, a->hi, a->lo);
    a_u256_from_u128(&mb, b->hi, b->lo);
    int st;
    if (sha && (st = a_u256_mul_pow10(&ma, sha)) != DEC_OK)
        return DEC_INEXACT;
    if (shb && (st = a_u256_mul_pow10(&mb, shb)) != DEC_OK)
        return DEC_INEXACT;

    dec_u256 m;
    int sign;
    if (a->sign == b->sign) {
        if (a_u256_add(&m, &ma, &mb)) return DEC_INEXACT;
        sign = a->sign;
    } else {
        int cmp = dec_u256_cmp(&ma, &mb);
        if (cmp == 0) return dec_set(dst, 0, 0, 0, 0, dscale);
        if (cmp > 0) {
            (void)a_u256_sub(&m, &ma, &mb);
            sign = a->sign;
        } else {
            (void)a_u256_sub(&m, &mb, &ma);
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
    a_u256_from_u128(&n, (uint64_t)(a >> 64), (uint64_t)a);
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
    a_u256_from_u128(&c, (uint64_t)(num >> 64), (uint64_t)num);
    /* num / (2^p2 * 5^p5) = (num * 2^(m-p2) * 5^(m-p5)) / 10^m. */
    for (int i = 0; i < m - p2; i++) {
        st = a_u256_mul_small(&c, 2);
        if (st != DEC_OK) return DEC_INEXACT;
    }
    for (int i = 0; i < m - p5; i++) {
        st = a_u256_mul_small(&c, 5);
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
            a_u256_from_u128(&bb, (uint64_t)(cb >> 64), (uint64_t)cb);
            int st = a_u256_mul_pow10(&bb, big);
            if (st != DEC_OK) return DEC_OVERFLOW;
            dec_u256 nn;
            a_u256_from_u128(&nn, a->hi, a->lo);
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
        a_u256_from_u128(&scaled, (uint64_t)(cb >> 64), (uint64_t)cb);
        if (a_u256_mul_pow10(&scaled, sh) != DEC_OK) return DEC_OVERFLOW;
        dec_u256 ca_v;
        a_u256_from_u128(&ca_v, a->hi, a->lo);
        int k = dec_u256_cmp(&ca_v, &scaled) >= 0 ? 0 : -1;
        *adj = base + sh + k;
    } else {
        int sh = db - da;
        dec_u256 scaled;
        a_u256_from_u128(&scaled, (uint64_t)(ca >> 64), (uint64_t)ca);
        if (a_u256_mul_pow10(&scaled, sh) != DEC_OK) return DEC_OVERFLOW;
        dec_u256 cb_v;
        a_u256_from_u128(&cb_v, b->hi, b->lo);
        int k = dec_u256_cmp(&scaled, &cb_v) >= 0 ? 0 : -1;
        *adj = base + (da - db) + k;
    }
    return DEC_OK;
}

static int u256_pow10_value(int p, dec_u256 *out) {
    *out = (dec_u256){{1, 0, 0, 0}};
    return a_u256_mul_pow10(out, p);
}

static int division_scaled(dec24 *dst, const dec24 *a, const dec24 *b,
                           int32_t scale, unsigned mode, unsigned carry_digits,
                           uint16_t dscale) {
    int64_t t = (int64_t)(int16_t)a->exp - (int16_t)b->exp + scale;
    dec_u256 n, d;
    a_u256_from_u128(&n, a->hi, a->lo);
    dec_u256 cb_v;
    a_u256_from_u128(&cb_v, b->hi, b->lo);
    int d_overflow = 0;

    if (t >= 0) {
        int st = a_u256_mul_pow10(&n, (int)t);
        if (st != DEC_OK) return DEC_OVERFLOW;
        d = cb_v;
    } else {
        d = cb_v;
        int st = a_u256_mul_pow10(&d, (int)(-t));
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

    if (a_u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) > 0)
        return DEC_OVERFLOW;

    int sign = a->sign ^ b->sign;
    int rem_nonzero = !dec_u256_is_zero(&r);
    int cmp_half = d_overflow ? -1 : cmp_twice_remainder(&r, &d);
    int inc = round_increment(mode, sign, cmp_half, rem_nonzero,
                              (int)(q.w[0] & 1u));
    if (inc == -1) return DEC_INEXACT;
    if (inc == -2) return DEC_INVALID;
    if (inc) {
        if (a_u256_cmp_u128(&q, DEC_COEFF_MAX_HI, DEC_COEFF_MAX_LO) > 0)
            return DEC_OVERFLOW;
        int st = a_u256_add_small(&q, 1);
        if (st != DEC_OK) return DEC_OVERFLOW;
    }
    if (a_u256_cmp_u128(&q, DEC10_38_HI, DEC10_38_LO) > 0)
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
        int st = a_u256_mul_small(&q, 10);
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

/* dec24_text.h — private exact text conversion for the experimental dec24.
 *
 * Experimental, not a language grammar, literal policy, public API or ABI.
 * The parser accepts an optional leading '+' or '-', ASCII digits, one
 * optional '.' and an optional 'e'/'E' exponent with an optional sign.  It
 * requires the whole caller-supplied length to be consumed, rejects embedded
 * NUL and every non-ASCII byte, and never reads a terminator.  This is a
 * private API convenience; the obo literal grammar remains
 * numeric-literals-decision-2026-10-29.md and is not decided here.
 *
 * Accepted grammar (private protocol):
 *
 *   sign    := '+' | '-' | empty
 *   mantissa:= digits [ '.' [digits] ] | '.' digits
 *   exponent:= ('e'|'E') ['+'|'-'] digits
 *   text    := sign mantissa [exponent]
 *
 * No whitespace, underscores, NaN, infinity or negative zero.  A zero
 * coefficient is canonical positive with exponent 0; '-' before zero therefore
 * produces the same canonical zero as '+' or no sign.  Display scale is set to
 * max(0, fractional_digits - explicit_exponent), so "1.2300" is value
 * 123 * 10^-2 with display scale 4 and "1.5e3" is value 15 * 10^2 with display
 * scale 0.  The coefficient is normalized by removing trailing zeros.  The
 * display scale is limited to uint16 and the arithmetic exponent to int16,
 * matching the approved core representation.
 *
 * Failed parses leave *out unchanged.  Semantic statuses, checked only after
 * the whole text is syntactically valid, have this precedence:
 *   DEC_PRECISION_OVERFLOW (>38 significant digits)
 *   DEC_DISPLAY_RANGE (display scale outside uint16)
 *   DEC_EXPONENT_RANGE (nonzero value's exponent outside int16)
 * Zero has canonical exponent 0, so its explicit exponent does not cause an
 * exponent-range error; its display scale can still be out of range.
 *
 * Two separate formatters:
 *   - canonical: exact numeric text, ignoring display scale.  Fixed notation
 *     is used when its magnitude text (excluding the optional '-' sign) is at
 *     most DEC_TEXT_CANON_FIXED_MAX bytes; otherwise the exact scientific form
 *     is used.  A negative fixed value can therefore be one byte longer than
 *     the threshold.  This is the documented private fallback; it is not a
 *     final language display policy.
 *   - display: fixed notation with exactly the value's dscale fractional
 *     digits, preserving trailing zeros.  It never rounds: if the value needs
 *     more fractional digits than dscale, it returns DEC_INEXACT.
 *
 * Both formatters write to a caller buffer, never allocate, always
 * NUL-terminate on success, and require cap >= required_length + 1.  The size
 * query functions return the required byte count excluding NUL.  A short
 * buffer returns DEC_BUFFER_SMALL, sets *out_len to the required length and
 * writes nothing.  Output is bounded by DEC_TEXT_MAX_OUTPUT; an impossible
 * larger form returns DEC_UNSUPPORTED_RANGE.  On any error *out_len is 0
 * except for DEC_BUFFER_SMALL.
 */



#define DEC_TEXT_CANON_FIXED_MAX 128u
#define DEC_TEXT_MAX_OUTPUT 98342u

int dec_parse(dec24 *out, const char *text, size_t len);

int dec_format_canonical_len(const dec24 *v, size_t *out_len);
int dec_format_canonical(const dec24 *v, char *buf, size_t cap,
                         size_t *out_len);

int dec_format_display_len(const dec24 *v, size_t *out_len);
int dec_format_display(const dec24 *v, char *buf, size_t cap,
                       size_t *out_len);


/* dec24_text.c — private exact parse/format helpers for the experimental
 * dec24 core.  No libc, libm, libgcc, allocation or binary floating point.
 * See dec24_text.h for the documented private grammar and display protocol.
 */


#define DEC_EXP_PARSE_CAP 1000000000000000ULL

static int ascii_digit(unsigned char c) {
    return c >= (unsigned char)'0' && c <= (unsigned char)'9';
}

/* ------------------------------- parsing -------------------------------- */

struct parse_acc {
    unsigned __int128 coeff;
    int started;
    int sig;
    int overflow;
    unsigned long long extra_zeros;
};

static void parse_acc_digit(struct parse_acc *p, unsigned d) {
    if (!p->started) {
        if (d == 0) return;
        p->started = 1;
        p->coeff = d;
        p->sig = 1;
        return;
    }
    if (p->overflow) return;
    if (p->sig < 38) {
        p->coeff = p->coeff * 10u + d;
        p->sig++;
    } else if (d != 0) {
        p->overflow = 1;
    } else if (p->extra_zeros != ~0ULL) {
        p->extra_zeros++;
    }
}

int dec_parse(dec24 *out, const char *text, size_t len) {
    if (out == 0 || text == 0 || len == 0) return DEC_INVALID;

    /* No read beyond len and no terminator assumption.  NUL and every
     * non-ASCII byte are malformed, including an embedded NUL. */
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == 0 || c > 0x7fU) return DEC_INVALID;
    }

    size_t i = 0;
    int sign = 0;
    if (text[i] == '+' || text[i] == '-') {
        sign = text[i] == '-';
        i++;
        if (i == len) return DEC_INVALID;
    }

    struct parse_acc acc;
    acc.coeff = 0;
    acc.started = 0;
    acc.sig = 0;
    acc.overflow = 0;
    acc.extra_zeros = 0;
    unsigned long long frac_digits = 0;
    int any_digit = 0;

    while (i < len && ascii_digit((unsigned char)text[i])) {
        parse_acc_digit(&acc, (unsigned)(text[i] - '0'));
        any_digit = 1;
        i++;
    }
    if (i < len && text[i] == '.') {
        i++;
        while (i < len && ascii_digit((unsigned char)text[i])) {
            parse_acc_digit(&acc, (unsigned)(text[i] - '0'));
            if (frac_digits != ~0ULL) frac_digits++;
            any_digit = 1;
            i++;
        }
    }
    if (!any_digit) return DEC_INVALID;

    int have_exp = 0;
    int exp_neg = 0;
    unsigned long long exp_mag = 0;
    if (i < len && (text[i] == 'e' || text[i] == 'E')) {
        have_exp = 1;
        i++;
        if (i < len && (text[i] == '+' || text[i] == '-')) {
            exp_neg = text[i] == '-';
            i++;
        }
        if (i == len || !ascii_digit((unsigned char)text[i])) return DEC_INVALID;
        while (i < len && ascii_digit((unsigned char)text[i])) {
            unsigned d = (unsigned)(text[i] - '0');
            if (exp_mag > (DEC_EXP_PARSE_CAP - (unsigned long long)d) / 10ULL)
                exp_mag = DEC_EXP_PARSE_CAP;
            else
                exp_mag = exp_mag * 10ULL + (unsigned long long)d;
            i++;
        }
    }
    if (i != len) return DEC_INVALID;

    if (acc.overflow) return DEC_PRECISION_OVERFLOW;

    __int128 explicit_exp =
        have_exp ? (exp_neg ? -(__int128)exp_mag : (__int128)exp_mag) : 0;
    __int128 dscale = (__int128)frac_digits - explicit_exp;
    if (dscale < 0) dscale = 0;
    if (dscale > 65535) return DEC_DISPLAY_RANGE;

    if (!acc.started) {
        return dec_set(out, 0, 0, 0, 0, (uint16_t)dscale);
    }

    uint64_t lo = (uint64_t)acc.coeff;
    uint64_t hi = (uint64_t)(acc.coeff >> 64);
    int stripped = 0;
    while (lo != 0 || hi != 0) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(hi, lo, 10, &qh, &ql, &rem) != DEC_OK)
            return DEC_INVALID;
        if (rem != 0) break;
        hi = qh;
        lo = ql;
        stripped++;
    }

    __int128 exp_final = explicit_exp - (__int128)frac_digits +
                         (__int128)stripped + (__int128)acc.extra_zeros;
    /* Normalization strips all coefficient trailing zeros, which can push the
     * exponent above int16 even though the value is representable with a
     * less-normalized coefficient.  Put back only the minimal number of
     * zeros needed to store exponent 32767; 1e32804 needs 37 zeros
     * (38 digits total) and is representable, while 1e32805 needs 38 zeros
     * (39 digits) and is not.  Negative exponents cannot be rescued this way
     * because stripping already made the exponent as large as possible. */
    if (exp_final > 32767) {
        __int128 shift = exp_final - 32767;
        int digits = acc.sig - stripped;
        if (shift > (__int128)(38 - digits)) return DEC_EXPONENT_RANGE;
        unsigned __int128 c = (unsigned __int128)lo |
                              ((unsigned __int128)hi << 64);
        for (__int128 i = 0; i < shift; i++) c *= 10u;
        lo = (uint64_t)c;
        hi = (uint64_t)(c >> 64);
        exp_final = 32767;
    }
    if (exp_final < -32768) return DEC_EXPONENT_RANGE;
    return dec_set(out, sign, lo, hi, (int)exp_final, (uint16_t)dscale);
}

/* ------------------------------ formatting ------------------------------ */

/* Full coefficient digits, most significant first, plus the count of trailing
 * zero digits.  The caller passes a 38-byte array; zero returns len 0. */
static int coeff_digits(const dec24 *v, char digits[38], int *len,
                        int *trailing) {
    uint64_t lo = v->lo, hi = v->hi;
    int n = 0;
    if (hi != 0) {
        uint64_t qh, ql, rem;
        /* coeff < 10^38 implies hi < 10^19, so qh is zero and ql < 10^19. */
        if (dec_div128_64(hi, lo, 10000000000000000000ULL,
                          &qh, &ql, &rem) != DEC_OK)
            return DEC_INVALID;
        if (qh != 0) return DEC_INVALID;
        for (int j = 0; j < 19; j++) {
            digits[n++] = (char)('0' + (int)(rem % 10));
            rem /= 10;
        }
        lo = ql;
    }
    while (lo != 0) {
        digits[n++] = (char)('0' + (int)(lo % 10));
        lo /= 10;
    }
    for (int i = 0; i < n / 2; i++) {
        char t = digits[i];
        digits[i] = digits[n - 1 - i];
        digits[n - 1 - i] = t;
    }
    int t = 0;
    while (t < n && digits[n - 1 - t] == '0') t++;
    *len = n;
    *trailing = t;
    return DEC_OK;
}

static size_t uint_digits(unsigned v) {
    size_t n = 1;
    while (v >= 10) {
        v /= 10;
        n++;
    }
    return n;
}

static char *write_uint(char *p, unsigned v) {
    char tmp[10];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + (int)(v % 10u));
        v /= 10u;
    } while (v != 0);
    while (n > 0) *p++ = tmp[--n];
    return p;
}

struct canon_layout {
    char digits[38];
    int sd;
    int pos;
    int adj_exp;
    size_t req;
    int use_sci;
};

static int canon_build(const dec24 *v, struct canon_layout *l) {
    if (!dec_valid(v)) return DEC_INVALID;
    if (dec_is_zero(v)) {
        l->sd = 0;
        l->req = 1;
        l->use_sci = 0;
        return DEC_OK;
    }
    int len = 0, trailing = 0;
    int st = coeff_digits(v, l->digits, &len, &trailing);
    if (st != DEC_OK) return st;
    int sd = len - trailing;
    int e = (int16_t)v->exp;
    int pos = len + e;
    size_t plain_len;
    if (pos <= 0)
        plain_len = 2u + (size_t)(-pos) + (size_t)sd;
    else if (pos >= sd)
        plain_len = (size_t)pos;
    else
        plain_len = (size_t)sd + 1u;

    /* The first significant digit has place value 10^(exp + len - 1), not
     * 10^(exp + stripped_len - 1): the removed coefficient trailing zeros
     * still move the decimal point. */
    int adj = e + len - 1;
    unsigned a = adj < 0 ? (unsigned)(-(adj + 1)) + 1u : (unsigned)adj;
    size_t sci_len = (size_t)sd + (sd > 1 ? 1u : 0u) + 1u +
                     (adj < 0 ? 1u : 0u) + uint_digits(a);
    int use_sci = plain_len > (size_t)DEC_TEXT_CANON_FIXED_MAX;
    size_t req = (use_sci ? sci_len : plain_len) + (v->sign ? 1u : 0u);
    if (req > (size_t)DEC_TEXT_MAX_OUTPUT) return DEC_UNSUPPORTED_RANGE;

    l->sd = sd;
    l->pos = pos;
    l->adj_exp = adj;
    l->req = req;
    l->use_sci = use_sci;
    return DEC_OK;
}

static int buffer_ready(size_t req, char *buf, size_t cap, size_t *out_len) {
    if (out_len == 0) return DEC_INVALID;
    if (req > (size_t)DEC_TEXT_MAX_OUTPUT) {
        *out_len = 0;
        return DEC_UNSUPPORTED_RANGE;
    }
    if (buf == 0 || cap <= req) {
        *out_len = req;
        return DEC_BUFFER_SMALL;
    }
    *out_len = req;
    return DEC_OK;
}

int dec_format_canonical_len(const dec24 *v, size_t *out_len) {
    if (out_len == 0) return DEC_INVALID;
    struct canon_layout l;
    int st = canon_build(v, &l);
    if (st != DEC_OK) {
        *out_len = 0;
        return st;
    }
    *out_len = l.req;
    return DEC_OK;
}

int dec_format_canonical(const dec24 *v, char *buf, size_t cap,
                         size_t *out_len) {
    struct canon_layout l;
    int st = canon_build(v, &l);
    if (st != DEC_OK) {
        if (out_len != 0) *out_len = 0;
        return st;
    }
    st = buffer_ready(l.req, buf, cap, out_len);
    if (st != DEC_OK) return st;

    char *p = buf;
    if (v->sign) *p++ = '-';
    if (dec_is_zero(v)) {
        *p++ = '0';
    } else if (!l.use_sci) {
        if (l.pos <= 0) {
            *p++ = '0';
            *p++ = '.';
            for (int i = 0; i < -l.pos; i++) *p++ = '0';
            for (int i = 0; i < l.sd; i++) *p++ = l.digits[i];
        } else if (l.pos >= l.sd) {
            for (int i = 0; i < l.sd; i++) *p++ = l.digits[i];
            for (int i = l.sd; i < l.pos; i++) *p++ = '0';
        } else {
            for (int i = 0; i < l.pos; i++) *p++ = l.digits[i];
            *p++ = '.';
            for (int i = l.pos; i < l.sd; i++) *p++ = l.digits[i];
        }
    } else {
        *p++ = l.digits[0];
        if (l.sd > 1) {
            *p++ = '.';
            for (int i = 1; i < l.sd; i++) *p++ = l.digits[i];
        }
        *p++ = 'e';
        int adj = l.adj_exp;
        if (adj < 0) {
            *p++ = '-';
            adj = -adj;
        }
        p = write_uint(p, (unsigned)adj);
    }
    *p = '\0';
    return DEC_OK;
}

struct display_layout {
    char digits[38];
    int len;
    int pos;
    uint16_t dscale;
    size_t req;
};

static int display_build(const dec24 *v, struct display_layout *l) {
    if (!dec_valid(v)) return DEC_INVALID;
    l->dscale = v->dscale;
    if (dec_is_zero(v)) {
        l->len = 0;
        l->pos = 0;
        l->req = 1u + (v->dscale ? 1u + (size_t)v->dscale : 0u);
        if (l->req > (size_t)DEC_TEXT_MAX_OUTPUT)
            return DEC_UNSUPPORTED_RANGE;
        return DEC_OK;
    }
    int len = 0, trailing = 0;
    int st = coeff_digits(v, l->digits, &len, &trailing);
    if (st != DEC_OK) return st;
    int e = (int16_t)v->exp;
    int s = (int)v->dscale;
    /* The fixed form covers exactly s fractional digits.  Omitted trailing
     * coefficient digits must all be zero; otherwise no silent rounding. */
    if ((__int128)e + (__int128)s + (__int128)trailing < 0) return DEC_INEXACT;
    int pos = len + e;
    int int_digits = pos > 0 ? pos : 1;
    size_t req = (size_t)int_digits + (s ? 1u + (size_t)s : 0u) +
                 (v->sign ? 1u : 0u);
    if (req > (size_t)DEC_TEXT_MAX_OUTPUT) return DEC_UNSUPPORTED_RANGE;
    l->len = len;
    l->pos = pos;
    l->req = req;
    return DEC_OK;
}

int dec_format_display_len(const dec24 *v, size_t *out_len) {
    if (out_len == 0) return DEC_INVALID;
    struct display_layout l;
    int st = display_build(v, &l);
    if (st != DEC_OK) {
        *out_len = 0;
        return st;
    }
    *out_len = l.req;
    return DEC_OK;
}

int dec_format_display(const dec24 *v, char *buf, size_t cap,
                       size_t *out_len) {
    struct display_layout l;
    int st = display_build(v, &l);
    if (st != DEC_OK) {
        if (out_len != 0) *out_len = 0;
        return st;
    }
    st = buffer_ready(l.req, buf, cap, out_len);
    if (st != DEC_OK) return st;

    char *p = buf;
    if (v->sign) *p++ = '-';
    if (dec_is_zero(v)) {
        *p++ = '0';
    } else if (l.pos > 0) {
        for (int j = 0; j < l.pos; j++)
            *p++ = j < l.len ? l.digits[j] : '0';
    } else {
        *p++ = '0';
    }
    if (l.dscale != 0) {
        *p++ = '.';
        for (int j = 0; j < (int)l.dscale; j++) {
            int idx = l.pos + j;
            *p++ = (idx >= 0 && idx < l.len) ? l.digits[idx] : '0';
        }
    }
    *p = '\0';
    return DEC_OK;
}

/* -------------------------------------------------------------------------
 * obo runtime integration (compiler-facing helpers)
 *
 * Slots 60..71 append to the runtime table.  Arithmetic uses the exact core
 * entry points, so + - * % never round; a non-representable exact result
 * traps.  Division and quantize have an explicit scale and mode and report a
 * status to the compiler, which builds a Result cell.  Pointers are explicit
 * and all values use the C ABI words.
 * ---------------------------------------------------------------------- */

enum {
    RT_DEC_INEXACT = 40,
    RT_DEC_OVERFLOW = 41,
    RT_DEC_DIV_ZERO = 42,
    RT_DEC_INVALID = 43,
    RT_DEC_UNSUPPORTED_RANGE = 44,
    RT_DEC_PRECISION_OVERFLOW = 45,
    RT_DEC_EXPONENT_RANGE = 46,
    RT_DEC_DISPLAY_RANGE = 47,
    RT_DEC_BUFFER_SMALL = 48,
    RT_DEC_OTHER = 49
};

static const char *rt_dec_status_message(int st) {
    switch (st) {
        case DEC_INEXACT: return "obo: decimal result is not exactly representable\n";
        case DEC_OVERFLOW: return "obo: decimal overflow\n";
        case DEC_DIV_ZERO: return "obo: decimal division by zero\n";
        case DEC_INVALID: return "obo: decimal invalid value\n";
        case DEC_UNSUPPORTED_RANGE: return "obo: decimal value is out of the supported range\n";
        case DEC_PRECISION_OVERFLOW: return "obo: decimal result has more than 38 significant digits\n";
        case DEC_EXPONENT_RANGE: return "obo: decimal exponent is out of range\n";
        case DEC_DISPLAY_RANGE: return "obo: decimal display scale is out of range\n";
        case DEC_BUFFER_SMALL: return "obo: decimal text buffer is too small\n";
        default: return "obo: decimal operation failed\n";
    }
}

static void rt_dec_trap(int st) {
    die_code(rt_dec_status_message(st), 10);
}

void rt_dec_reserved(void) {
    die_code("obo: reserved runtime slot\n", 10);
}

static int rt_dec_coeff_digits(const dec24 *a) {
    dec_u256 v;
    v.w[0] = a->lo;
    v.w[1] = a->hi;
    v.w[2] = 0;
    v.w[3] = 0;
    return dec_u256_digits(&v);
}

/* Display scale for multiply: min(s1+s2, max(0, 38-intd)), where intd comes
 * from the RESULT's exact coefficient/exponent, not either operand.  The value
 * is unchanged; only presentation is capped (design section 4.1).  The exact
 * core has already rejected results needing more than 38 digits, so the result
 * coefficient is the exact normalized one, not a truncated operand. */
static uint16_t rt_dec_mul_result_dscale(const dec24 *r,
                                        const dec24 *a, const dec24 *b) {
    int intd = rt_dec_coeff_digits(r) + (int)(int16_t)r->exp;
    if (intd < 0) intd = 0;
    int cap = 38 - intd;
    if (cap < 0) cap = 0;
    int sum = (int)a->dscale + (int)b->dscale;
    if (sum > 65535) sum = 65535;
    if (sum > cap) sum = cap;
    return (uint16_t)sum;
}

static uint16_t rt_dec_max_dscale(const dec24 *a, const dec24 *b) {
    return a->dscale > b->dscale ? a->dscale : b->dscale;
}

void rt_dec_add(dec24 *dst, const dec24 *a, const dec24 *b) {
    if (!dec_valid(a) || !dec_valid(b)) rt_dec_trap(DEC_INVALID);
    int st = dec_add_exact(dst, a, b, rt_dec_max_dscale(a, b));
    if (st != DEC_OK) rt_dec_trap(st);
}

void rt_dec_sub(dec24 *dst, const dec24 *a, const dec24 *b) {
    if (!dec_valid(a) || !dec_valid(b)) rt_dec_trap(DEC_INVALID);
    int st = dec_sub_exact(dst, a, b, rt_dec_max_dscale(a, b));
    if (st != DEC_OK) rt_dec_trap(st);
}

void rt_dec_mul(dec24 *dst, const dec24 *a, const dec24 *b) {
    if (!dec_valid(a) || !dec_valid(b)) rt_dec_trap(DEC_INVALID);
    /* Multiply once with neutral display metadata, then derive the cap from the
     * exact result.  dscale is presentation-only, so setting it here cannot
     * change the value or round it. */
    int st = dec_mul_exact(dst, a, b, 0);
    if (st != DEC_OK) rt_dec_trap(st);
    dst->dscale = rt_dec_mul_result_dscale(dst, a, b);
}

void rt_dec_rem(dec24 *dst, const dec24 *a, const dec24 *b) {
    if (!dec_valid(a) || !dec_valid(b)) rt_dec_trap(DEC_INVALID);
    int st = dec_rem_trunc(dst, a, b, rt_dec_max_dscale(a, b));
    if (st != DEC_OK) rt_dec_trap(st);
}

void rt_dec_neg(dec24 *dst, const dec24 *a) {
    if (!dec_valid(a)) rt_dec_trap(DEC_INVALID);
    dec_copy(dst, a);
    if (!dec_is_zero(dst)) dst->sign = (uint8_t)(dst->sign ^ 1u);
}

i64 rt_dec_cmp(const dec24 *a, const dec24 *b) {
    if (!dec_valid(a) || !dec_valid(b)) rt_dec_trap(DEC_INVALID);
    return (i64)dec_cmp(a, b);
}

void rt_dec_print(const dec24 *a) {
    char buf[DEC_TEXT_MAX_OUTPUT + 1];
    size_t n = 0;
    if (!dec_valid(a)) rt_dec_trap(DEC_INVALID);
    int st = dec_format_display(a, buf, sizeof buf, &n);
    if (st == DEC_INEXACT) st = dec_format_canonical(a, buf, sizeof buf, &n);
    if (st != DEC_OK) rt_dec_trap(st);
    putb(buf, (u64)n);
}

/* Explicit request block: [0] scale 0..32768, [1] mode 0..7.  The compiler
 * stores the two words in its frame and passes a pointer, so every i64 value is
 * validated without lossy packing or aliasing.  The result value is written only
 * on DEC_OK.  The status is returned so the compiler can build a Result cell; no
 * value error is a fatal trap here.  Invalid scale is DEC_UNSUPPORTED_RANGE;
 * invalid mode is DEC_INVALID. */
static int rt_dec_request(const i64 *req, i64 *scale, i64 *mode) {
    if (req == 0) return DEC_INVALID;
    *scale = req[0];
    *mode = req[1];
    if (*scale < 0 || *scale > 32768) return DEC_UNSUPPORTED_RANGE;
    if (*mode < 0 || *mode > 7) return DEC_INVALID;
    return DEC_OK;
}

i64 rt_dec_div_into(dec24 *dst, const dec24 *a, const dec24 *b,
                    const i64 *req) {
    if (!dec_valid(a) || !dec_valid(b)) return DEC_INVALID;
    i64 scale = 0, mode = 0;
    int st = rt_dec_request(req, &scale, &mode);
    if (st != DEC_OK) return st;
    return dec_div_scale(dst, a, b, (int32_t)scale, (unsigned)mode,
                         (uint16_t)scale);
}

i64 rt_dec_quantize_into(dec24 *dst, const dec24 *a, const i64 *req) {
    if (!dec_valid(a)) return DEC_INVALID;
    i64 scale = 0, mode = 0;
    int st = rt_dec_request(req, &scale, &mode);
    if (st != DEC_OK) return st;
    return dec_quantize(dst, a, (uint16_t)scale, (unsigned)mode);
}

void rt_dec_from_int(dec24 *dst, i64 v) {
    u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    int st = dec_set(dst, v < 0 ? 1 : 0, (uint64_t)u, 0, 0, 0);
    if (st != DEC_OK) rt_dec_trap(st);
}

i64 rt_dec_to_int(i64 *out, const dec24 *a, i64 mode) {
    if (!dec_valid(a)) return DEC_INVALID;
    if (mode < 0 || mode > 7) return DEC_INVALID;
    dec24 q;
    int st = dec_quantize(&q, a, 0, (unsigned)mode);
    if (st != DEC_OK) return st;
    if (q.hi != 0 || (int16_t)q.exp != 0) return DEC_OVERFLOW;
    u64 lim = 0x7fffffffffffffffUL;
    if (q.sign) {
        if (q.lo > (u64)0x8000000000000000UL) return DEC_OVERFLOW;
        *out = q.lo == (u64)0x8000000000000000UL ? (i64)(-9223372036854775807LL - 1) : -(i64)q.lo;
    } else {
        if (q.lo > lim) return DEC_OVERFLOW;
        *out = (i64)q.lo;
    }
    return DEC_OK;
}

static u64 rt_dec_err_cell(u64 descErr, int st) {
    const char *msg = rt_dec_status_message(st);
    u64 n = 0; while (msg[n]) n++;
    if (n > 0 && msg[n - 1] == '\n') n--;   /* Result messages do not include the trap newline */
    u64 p = alloc_raw(3, 8 + n);
    *(u64 *)p = n;
    memcpy((char *)p + 8, msg, n);
    return err_cell(descErr, p, st);
}

u64 rt_dec_result(dec24 *v, i64 status, u64 descErr, u64 descOk) {
    if (status == DEC_OK) {
        u64 *c = (u64 *)rt_alloc_record((u64 *)descOk);
        memcpy(c, v, 24);
        return (u64)c;
    }
    return rt_dec_err_cell(descErr, (int)status);
}

u64 rt_dec_int_result(i64 *v, i64 status, u64 descErr, u64 descOk) {
    if (status == DEC_OK) {
        u64 *c = (u64 *)rt_alloc_record((u64 *)descOk);
        c[0] = (u64)*v;
        return (u64)c;
    }
    return rt_dec_err_cell(descErr, (int)status);
}

#endif /* OBO_DECIMAL24_CORE_INC */
