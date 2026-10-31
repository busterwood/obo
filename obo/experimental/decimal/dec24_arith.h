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

#ifndef DEC24_ARITH_H
#define DEC24_ARITH_H

#include <stdint.h>

#include "dec24.h"

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

#endif
