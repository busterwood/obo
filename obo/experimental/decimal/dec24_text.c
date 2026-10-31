/* dec24_text.c — private exact parse/format helpers for the experimental
 * dec24 core.  No libc, libm, libgcc, allocation or binary floating point.
 * See dec24_text.h for the documented private grammar and display protocol.
 */
#include "dec24_text.h"

#include <stdint.h>

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
    if (exp_final < -32768 || exp_final > 32767) return DEC_EXPONENT_RANGE;
    return dec_set(out, sign, lo, hi, (int)exp_final, (uint16_t)dscale);
}

/* ------------------------------ formatting ------------------------------ */

/* Full coefficient digits, most significant first, plus the count of trailing
 * zero digits.  The caller passes a 38-byte array; zero returns len 0. */
static int coeff_digits(const dec24 *v, char digits[38], int *len,
                        int *trailing) {
    uint64_t lo = v->lo, hi = v->hi;
    int n = 0;
    while (lo != 0 || hi != 0) {
        uint64_t qh, ql, rem;
        if (dec_div128_64(hi, lo, 10, &qh, &ql, &rem) != DEC_OK)
            return DEC_INVALID;
        digits[n++] = (char)('0' + (int)rem);
        hi = qh;
        lo = ql;
        if (n > 38) return DEC_INVALID;
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
