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
#ifndef DEC24_H
#define DEC24_H

#include <stdint.h>

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
    DEC_UNSUPPORTED_EXACT = 2,  /* exact alignment/range not attempted */
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
 * Malformed input values and invalid modes return DEC_INVALID. */
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

#endif
