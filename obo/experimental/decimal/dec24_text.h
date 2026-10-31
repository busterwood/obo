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
#ifndef DEC24_TEXT_H
#define DEC24_TEXT_H

#include <stddef.h>

#include "dec24.h"

#define DEC_TEXT_CANON_FIXED_MAX 128u
#define DEC_TEXT_MAX_OUTPUT 98342u

int dec_parse(dec24 *out, const char *text, size_t len);

int dec_format_canonical_len(const dec24 *v, size_t *out_len);
int dec_format_canonical(const dec24 *v, char *buf, size_t cap,
                         size_t *out_len);

int dec_format_display_len(const dec24 *v, size_t *out_len);
int dec_format_display(const dec24 *v, char *buf, size_t cap,
                       size_t *out_len);

#endif
