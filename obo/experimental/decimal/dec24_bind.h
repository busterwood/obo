/* dec24_bind.h — private exact database-binding validation for dec24.
 *
 * Experimental.  It implements a mathematical contract for the proposed,
 * still-unapproved decimal(p,s) discussion; it adds no decimal(p,s) language
 * syntax and no database adapter.  Vendor precision/scale ranges are the
 * caller's metadata and are deliberately not encoded here.
 *
 * Exact contract:
 *   a finite value v fits decimal(p,s) exactly iff
 *       v * 10^s is an integer and abs(v * 10^s) < 10^p
 *   with p >= 1 and s any int64.  Negative s and s > p are accepted when the
 *   mathematics allows them; no SQL Server-style s in 0..p rule is imposed.
 *   Display scale does not affect this value check, and zero fits every legal
 *   p/s pair.
 *
 * dec_bind_exact_fits never rounds:
 *   DEC_OK                 exact integer and abs < 10^p
 *   DEC_INEXACT            v * 10^s is not an integer
 *   DEC_PRECISION_OVERFLOW abs(v * 10^s) >= 10^p
 *   DEC_INVALID            malformed value or p < 1
 *
 * dec_bind_quantize rounds v to exactly s fractional digits with an explicit
 * mode, then applies the same precision check and writes the rounded value
 * only when it fits.  There is no default rounding mode.  Its result uses
 * exponent -s, so nonzero values are supported for -32767 <= s <= 32768 (the
 * core's int16 exponent limit); zero is supported for s <= 65535 because it
 * keeps canonical exponent 0 and only needs display scale.  Other scales return
 * DEC_UNSUPPORTED_RANGE.  A coefficient that cannot fit the 38-digit core
 * returns DEC_OVERFLOW even if a wider database column could hold it.
 *
 * Failed quantize calls leave *dst unchanged.  This is a validation helper,
 * not evidence that any database driver, NULL representation or vendor
 * metadata rule has been implemented or tested.
 */
#ifndef DEC24_BIND_H
#define DEC24_BIND_H

#include <stdint.h>

#include "dec24.h"

int dec_bind_exact_fits(const dec24 *v, int64_t precision, int64_t scale);
int dec_bind_quantize(dec24 *dst, const dec24 *v, int64_t precision,
                      int64_t scale, unsigned mode);

#endif
