/* dec24_bind.c — private exact binding validation for the experimental
 * dec24 core.  No libc, libm, libgcc, allocation or binary floating point.
 * See dec24_bind.h for the mathematical contract and limits.
 */
#include "dec24_bind.h"

static int bind_coeff_shape(const dec24 *v, int *len, int *trailing) {
    uint64_t lo = v->lo, hi = v->hi;
    int n = 0, t = 0, seen_nonzero = 0;
    while (lo != 0 || hi != 0) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(hi, lo, 10, &qh, &ql, &rem) != DEC_OK)
            return DEC_INVALID;
        if (!seen_nonzero) {
            if (rem == 0)
                t++;
            else
                seen_nonzero = 1;
        }
        hi = qh;
        lo = ql;
        n++;
        if (n > 38) return DEC_INVALID;
    }
    *len = n;
    *trailing = t;
    return DEC_OK;
}

int dec_bind_exact_fits(const dec24 *v, int64_t precision, int64_t scale) {
    if (!dec_valid(v) || precision < 1) return DEC_INVALID;
    if (dec_is_zero(v)) return DEC_OK;

    int len = 0, trailing = 0;
    int st = bind_coeff_shape(v, &len, &trailing);
    if (st != DEC_OK) return st;

    __int128 es = (__int128)(int16_t)v->exp + (__int128)scale;
    if (es >= 0) {
        __int128 int_digits = (__int128)len + es;
        return int_digits > (__int128)precision ? DEC_PRECISION_OVERFLOW
                                                : DEC_OK;
    }
    __int128 removed = -es;
    if (removed > (__int128)trailing) return DEC_INEXACT;
    __int128 int_digits = (__int128)len - removed;
    return int_digits > (__int128)precision ? DEC_PRECISION_OVERFLOW : DEC_OK;
}

static int bind_inc_after_round(unsigned mode, int sign, int first, int sticky,
                                int q_odd) {
    switch (mode) {
        case DEC_ROUND_HALF_EVEN:
            return first > 5 || (first == 5 && (sticky || q_odd));
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

static int bind_zero_result(dec24 *dst, int64_t scale) {
    uint16_t ds;
    if (scale < 0)
        ds = 0;
    else if (scale > 65535)
        return DEC_UNSUPPORTED_RANGE;
    else
        ds = (uint16_t)scale;
    dec24 tmp;
    int st = dec_set(&tmp, 0, 0, 0, 0, ds);
    if (st != DEC_OK) return st;
    dec_copy(dst, &tmp);
    return DEC_OK;
}

int dec_bind_quantize(dec24 *dst, const dec24 *v, int64_t precision,
                      int64_t scale, unsigned mode) {
    if (dst == 0 || !dec_valid(v) || precision < 1 ||
        mode >= DEC_ROUND_MODE_COUNT)
        return DEC_INVALID;
    if (dec_is_zero(v)) return bind_zero_result(dst, scale);
    if (scale < -32767 || scale > 32768) return DEC_UNSUPPORTED_RANGE;

    int len = 0, trailing = 0;
    int st = bind_coeff_shape(v, &len, &trailing);
    if (st != DEC_OK) return st;
    (void)trailing;

    __int128 shift = (__int128)(int16_t)v->exp + (__int128)scale;
    unsigned __int128 q;
    if (shift >= 0) {
        if (shift > (__int128)(38 - len)) return DEC_OVERFLOW;
        q = v->lo;
        q |= (unsigned __int128)v->hi << 64;
        for (__int128 i = 0; i < shift; i++) q *= 10u;
    } else {
        __int128 removed = -shift;
        uint64_t lo = v->lo, hi = v->hi;
        int first = 0, sticky = 0;
        if (removed > 38) {
            first = 0;
            sticky = 1;
            lo = hi = 0;
        } else {
            for (__int128 i = 0; i < removed; i++) {
                uint64_t qh, ql, rem;
                st = dec_div128_64(hi, lo, 10, &qh, &ql, &rem);
                if (st != DEC_OK) return st;
                if (i == removed - 1)
                    first = (int)rem;
                else if (rem != 0)
                    sticky = 1;
                hi = qh;
                lo = ql;
            }
        }
        q = lo;
        q |= (unsigned __int128)hi << 64;
        int inc = bind_inc_after_round(mode, v->sign, first, sticky,
                                       (int)(q & 1u));
        if (inc == -1) {
            if (first > 0 || sticky) return DEC_INEXACT;
            inc = 0;
        } else if (inc == -2) {
            return DEC_INVALID;
        }
        if (inc) {
            if (q == (((unsigned __int128)DEC_COEFF_MAX_HI << 64) |
                      (unsigned __int128)DEC_COEFF_MAX_LO))
                return DEC_OVERFLOW;
            q++;
        }
    }

    if (q == 0) return bind_zero_result(dst, scale);

    unsigned __int128 maxc = ((unsigned __int128)DEC_COEFF_MAX_HI << 64) |
                             (unsigned __int128)DEC_COEFF_MAX_LO;
    if (q > maxc) return DEC_OVERFLOW;

    uint64_t lo = (uint64_t)q;
    uint64_t hi = (uint64_t)(q >> 64);
    int digits = 0;
    {
        uint64_t th = hi, tl = lo;
        while (th != 0 || tl != 0) {
            uint64_t qh, ql, rem;
            st = dec_div128_64(th, tl, 10, &qh, &ql, &rem);
            if (st != DEC_OK) return st;
            th = qh;
            tl = ql;
            digits++;
        }
    }
    if ((__int128)digits > (__int128)precision) return DEC_PRECISION_OVERFLOW;

    uint16_t ds = scale >= 0 ? (uint16_t)scale : 0;
    int exp = (int)(-scale);
    dec24 tmp;
    st = dec_set(&tmp, v->sign, lo, hi, exp, ds);
    if (st != DEC_OK) return st;
    dec_copy(dst, &tmp);
    return DEC_OK;
}
