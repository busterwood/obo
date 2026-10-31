/* dec24_arith_test.c — freestanding D2 arithmetic contract tests.  No libc.
 * Expected data comes from arithmetic_oracle.py fixtures; a few power-of-ten
 * boundary and status cases are pinned explicitly below.
 */
#include "dec24.h"
#include "dec24_arith.h"
#include "arithmetic_vectors_generated.h"

static int failures;
static unsigned long checks;

static long sys_write(int fd, const void *buf, unsigned long n) {
    long r;
    __asm__ volatile("syscall"
                     : "=a"(r)
                     : "a"(1L), "D"((long)fd), "S"(buf), "d"(n)
                     : "rcx", "r11", "memory");
    return r;
}

static unsigned long str_len(const char *s) {
    unsigned long n = 0;
    while (s[n]) n++;
    return n;
}

static void write_str(const char *s) { sys_write(1, s, str_len(s)); }

static void write_u64(uint64_t v) {
    char b[24];
    int i = 24;
    do {
        b[--i] = (char)('0' + (v % 10));
        v /= 10;
    } while (v);
    sys_write(1, b + i, (unsigned long)(24 - i));
}

static void fail_field(const char *name, unsigned idx, const char *field,
                       uint64_t got, uint64_t want) {
    failures++;
    write_str("FAIL ");
    write_str(name);
    write_str(" idx=");
    write_u64(idx);
    write_str(" field=");
    write_str(field);
    write_str(" got=");
    write_u64(got);
    write_str(" want=");
    write_u64(want);
    write_str("\n");
}

static void fail_status(const char *name, unsigned idx, int got, int want) {
    failures++;
    write_str("FAIL ");
    write_str(name);
    write_str(" idx=");
    write_u64(idx);
    write_str(" status=");
    write_str(dec_status_name(got));
    write_str(" want=");
    write_str(dec_status_name(want));
    write_str("\n");
}

static void fill_byte(dec24 *d, uint8_t v) {
    unsigned char *p = (unsigned char *)d;
    for (unsigned long i = 0; i < sizeof(*d); i++) p[i] = v;
}

static int all_byte(const dec24 *d, uint8_t v) {
    const unsigned char *p = (const unsigned char *)d;
    for (unsigned long i = 0; i < sizeof(*d); i++)
        if (p[i] != v) return 0;
    return 1;
}

static void check_status_unchanged(const char *name, unsigned idx,
                                   const dec24 *out, int got, int want) {
    checks++;
    if (got != want) fail_status(name, idx, got, want);
    checks++;
    if (!all_byte(out, 0xAA))
        fail_field(name, idx, "output_unchanged", 0, 1);
}

static void check_dec_raw(const char *name, unsigned idx, const dec24 *got,
                          const dec24 *want) {
    checks++;
    if (got->sign != want->sign)
        fail_field(name, idx, "sign", got->sign, want->sign);
    checks++;
    if (got->lo != want->lo)
        fail_field(name, idx, "lo", got->lo, want->lo);
    checks++;
    if (got->hi != want->hi)
        fail_field(name, idx, "hi", got->hi, want->hi);
    checks++;
    if ((uint16_t)(int16_t)got->exp != (uint16_t)(int16_t)want->exp)
        fail_field(name, idx, "exp", (uint16_t)got->exp,
                   (uint16_t)want->exp);
    checks++;
    if (got->dscale != want->dscale)
        fail_field(name, idx, "dscale", got->dscale, want->dscale);
}

static void check_dec_numeric(const char *name, unsigned idx,
                              const dec24 *got, const dec24 *want) {
    checks++;
    if (dec_cmp(got, want) != 0)
        fail_field(name, idx, "numeric_equal", 0, 1);
    checks++;
    if (got->dscale != want->dscale)
        fail_field(name, idx, "dscale", got->dscale, want->dscale);
}

static int mk(dec24 *d, int sign, uint64_t lo, uint64_t hi, int exp,
              unsigned dscale) {
    return dec_set(d, sign, lo, hi, exp, (uint16_t)dscale);
}

static void run_op_vec(const char *name, unsigned idx, const ArithOpVec *v,
                       int op) {
    dec24 a, b, out, want;
    int st = mk(&a, v->sign_a, v->alo, v->ahi, v->ea, v->da);
    if (st != DEC_OK) { fail_status(name, idx, st, DEC_OK); return; }
    st = mk(&b, v->sign_b, v->blo, v->bhi, v->eb, v->db);
    if (st != DEC_OK) { fail_status(name, idx, st, DEC_OK); return; }
    fill_byte(&out, 0xAA);
    if (op == 0) st = dec_add(&out, &a, &b, v->mode);
    else if (op == 1) st = dec_sub(&out, &a, &b, v->mode);
    else st = dec_mul(&out, &a, &b, v->mode);
    checks++;
    if (st != v->status) {
        fail_status(name, idx, st, v->status);
        return;
    }
    if (st != DEC_OK) {
        checks++;
        if (!all_byte(&out, 0xAA))
            fail_field(name, idx, "output_unchanged", 0, 1);
        return;
    }
    int ws = mk(&want, v->sign_r, v->rlo, v->rhi, v->er, v->dr);
    if (ws != DEC_OK) { fail_status(name, idx, ws, DEC_OK); return; }
    check_dec_numeric(name, idx, &out, &want);
}

static void run_exact_vec(unsigned idx, const ArithExactVec *v) {
    dec24 a, b, out, want;
    int st = mk(&a, v->sign_a, v->alo, v->ahi, v->ea, v->da);
    if (st != DEC_OK) { fail_status("exact_set_a", idx, st, DEC_OK); return; }
    st = mk(&b, v->sign_b, v->blo, v->bhi, v->eb, v->db);
    if (st != DEC_OK) { fail_status("exact_set_b", idx, st, DEC_OK); return; }
    fill_byte(&out, 0xAA);
    switch (v->op) {
        case 0: st = dec_add_exact(&out, &a, &b, (uint16_t)v->dscale); break;
        case 1: st = dec_sub_exact(&out, &a, &b, (uint16_t)v->dscale); break;
        case 2: st = dec_mul_exact(&out, &a, &b, (uint16_t)v->dscale); break;
        case 3: st = dec_div_exact(&out, &a, &b, (uint16_t)v->dscale); break;
        case 4: st = dec_rem_trunc(&out, &a, &b, (uint16_t)v->dscale); break;
        default: st = DEC_INVALID; break;
    }
    checks++;
    if (st != v->status) {
        fail_status("exact", idx, st, v->status);
        return;
    }
    if (st != DEC_OK) {
        checks++;
        if (!all_byte(&out, 0xAA))
            fail_field("exact", idx, "output_unchanged", 0, 1);
        return;
    }
    int ws = mk(&want, v->sign_r, v->rlo, v->rhi, v->er, v->dr);
    if (ws != DEC_OK) { fail_status("exact", idx, ws, DEC_OK); return; }
    check_dec_numeric("exact", idx, &out, &want);
}

static void run_div_vec(unsigned idx, const ArithDivVec *v) {
    dec24 a, b, out, want;
    int st = mk(&a, v->sign_a, v->alo, v->ahi, v->ea, v->da);
    if (st != DEC_OK) { fail_status("div_set_a", idx, st, DEC_OK); return; }
    st = mk(&b, v->sign_b, v->blo, v->bhi, v->eb, v->db);
    if (st != DEC_OK) { fail_status("div_set_b", idx, st, DEC_OK); return; }
    fill_byte(&out, 0xAA);
    if (v->kind == DEC_DIV_ROUND_SCALE)
        st = dec_div_scale(&out, &a, &b, v->scale, v->mode,
                           (uint16_t)v->dscale);
    else
        st = dec_div_precision(&out, &a, &b, v->precision, v->mode,
                               (uint16_t)v->dscale);
    checks++;
    if (st != v->status) {
        fail_status("div", idx, st, v->status);
        return;
    }
    if (st != DEC_OK) {
        checks++;
        if (!all_byte(&out, 0xAA))
            fail_field("div", idx, "output_unchanged", 0, 1);
        return;
    }
    int ws = mk(&want, v->sign_r, v->rlo, v->rhi, v->er, v->dr);
    if (ws != DEC_OK) { fail_status("div", idx, ws, DEC_OK); return; }
    check_dec_raw("div", idx, &out, &want);
}

static void run_rem_vec(unsigned idx, const ArithRemVec *v) {
    dec24 a, b, out, want;
    int st = mk(&a, v->sign_a, v->alo, v->ahi, v->ea, v->da);
    if (st != DEC_OK) { fail_status("rem_set_a", idx, st, DEC_OK); return; }
    st = mk(&b, v->sign_b, v->blo, v->bhi, v->eb, v->db);
    if (st != DEC_OK) { fail_status("rem_set_b", idx, st, DEC_OK); return; }
    fill_byte(&out, 0xAA);
    st = dec_rem_trunc(&out, &a, &b, (uint16_t)v->dscale);
    checks++;
    if (st != v->status) {
        fail_status("rem", idx, st, v->status);
        return;
    }
    if (st != DEC_OK) {
        checks++;
        if (!all_byte(&out, 0xAA))
            fail_field("rem", idx, "output_unchanged", 0, 1);
        return;
    }
    int ws = mk(&want, v->sign_r, v->rlo, v->rhi, v->er, v->dr);
    if (ws != DEC_OK) { fail_status("rem", idx, ws, DEC_OK); return; }
    check_dec_numeric("rem", idx, &out, &want);
}

static void boundary_cases(void) {
    dec24 a, b, out, want;
    /* 12e76 + 1: the exact result is not 38-digit representable, but the
     * rounded API produces 12e76 for nearest/truncation and one ulp above for
     * away/ceiling; it is not DEC_UNSUPPORTED_EXACT. */
    mk(&a, 0, 12, 0, 76, 0);
    mk(&b, 0, 1, 0, 0, 0);
    int st = dec_add(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_OK) fail_status("sticky12", 0, st, DEC_OK);
    else {
        mk(&want, 0, 12, 0, 76, 0);
        check_dec_raw("sticky12", 0, &out, &want);
    }
    st = dec_add(&out, &a, &b, DEC_ROUND_AWAY);
    checks++;
    if (st != DEC_OK) fail_status("sticky12_away", 0, st, DEC_OK);
    else {
        mk(&want, 0, 0x678b74c000000001ULL, 0x09071dadcd6c5504ULL, 40, 0);
        check_dec_raw("sticky12_away", 0, &out, &want);
    }
    st = dec_add_exact(&out, &a, &b, 0);
    checks++;
    if (st != DEC_INEXACT)
        fail_status("sticky12_exact", 0, st, DEC_INEXACT);

    /* Power-of-ten cancellation boundary: the predecessor of 1e37 is
     * 10^38-1 at exponent -1 (lower decade), not at exponent 0. */
    dec24 tiny;
    mk(&a, 0, 1, 0, 37, 0);
    mk(&tiny, 0, 1, 0, -100, 0);
    st = dec_sub(&out, &a, &tiny, DEC_ROUND_TOWARD_ZERO);
    checks++;
    if (st != DEC_OK) fail_status("pow10_pred", 0, st, DEC_OK);
    else {
        mk(&want, 0, DEC_COEFF_MAX_LO, DEC_COEFF_MAX_HI, -1, 0);
        check_dec_raw("pow10_pred", 0, &out, &want);
    }
    st = dec_add(&out, &a, &tiny, DEC_ROUND_AWAY);
    checks++;
    if (st != DEC_OK) fail_status("pow10_succ", 0, st, DEC_OK);
    else {
        mk(&want, 0, 0x00f436a000000001ULL, 0x0785ee10d5da46d9ULL, 0, 0);
        check_dec_raw("pow10_succ", 0, &out, &want);
    }
    mk(&a, 1, 1, 0, 37, 0);       /* -1e37, opposite tiny positive */
    st = dec_add(&out, &a, &tiny, DEC_ROUND_TOWARD_ZERO);
    checks++;
    if (st != DEC_OK) fail_status("pow10_neg_tz", 0, st, DEC_OK);
    else {
        mk(&want, 1, DEC_COEFF_MAX_LO, DEC_COEFF_MAX_HI, -1, 0);
        check_dec_raw("pow10_neg_tz", 0, &out, &want);
    }
    st = dec_add(&out, &a, &tiny, DEC_ROUND_FLOOR);
    checks++;
    if (st != DEC_OK) fail_status("pow10_neg_floor", 0, st, DEC_OK);
    else {
        mk(&want, 1, 1, 0, 37, 0);
        check_dec_raw("pow10_neg_floor", 0, &out, &want);
    }
    st = dec_add_exact(&out, &a, &tiny, 0);
    checks++;
    if (st != DEC_INEXACT)
        fail_status("pow10_exact", 0, st, DEC_INEXACT);

    /* Just below a power of ten, a nearest-mode subtraction can choose the
     * lower-decade predecessor once the small operand exceeds half a lower
     * unit (0.05 for 1e37).  This is the tie/nearest boundary, not a sticky
     * drop of the borrow. */
    mk(&a, 0, 1, 0, 37, 0);
    mk(&b, 0, 9, 0, -2, 0);       /* 1e37 - 0.09: predecessor */
    st = dec_sub(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_OK) fail_status("pow10_half_above", 0, st, DEC_OK);
    else {
        mk(&want, 0, DEC_COEFF_MAX_LO, DEC_COEFF_MAX_HI, -1, 0);
        check_dec_raw("pow10_half_above", 0, &out, &want);
    }
    mk(&b, 0, 4, 0, -2, 0);       /* 1e37 - 0.04: stays at 1e37 */
    st = dec_sub(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_OK) fail_status("pow10_half_below", 0, st, DEC_OK);
    else {
        mk(&want, 0, 1, 0, 37, 0);
        check_dec_numeric("pow10_half_below", 0, &out, &want);
    }
    mk(&b, 0, 5, 0, -2, 0);       /* exactly half: even -> 1e37 */
    st = dec_sub(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_OK) fail_status("pow10_half_even", 0, st, DEC_OK);
    else {
        mk(&want, 0, 1, 0, 37, 0);
        check_dec_numeric("pow10_half_even", 0, &out, &want);
    }
    st = dec_sub(&out, &a, &b, DEC_ROUND_HALF_TOWARD_ZERO);
    checks++;
    if (st != DEC_OK) fail_status("pow10_half_tz", 0, st, DEC_OK);
    else {
        mk(&want, 0, DEC_COEFF_MAX_LO, DEC_COEFF_MAX_HI, -1, 0);
        check_dec_raw("pow10_half_tz", 0, &out, &want);
    }

    /* At the low exponent edge the exact result (10^37-1)*10^-32768 is
     * representable; a precision-38 grid at exponent -32769 must remove one
     * trailing zero rather than report an exponent error. */
    mk(&a, 0, 1, 0, -32731, 0);
    mk(&b, 1, 1, 0, -32768, 0);
    st = dec_add(&out, &a, &b, DEC_ROUND_TOWARD_ZERO);
    checks++;
    if (st != DEC_OK) fail_status("low_edge", 0, st, DEC_OK);
    else {
        mk(&want, 0, 0x00f4369fffffffffULL, 0x0785ee10d5da46d9ULL,
           -32768, 0);
        check_dec_raw("low_edge", 0, &out, &want);
    }

    /* Exact division identity/status and canonical zero. */
    mk(&a, 0, 1, 0, 0, 0);
    mk(&b, 0, 8, 0, 0, 0);
    st = dec_div_exact(&out, &a, &b, 11);
    checks++;
    if (st != DEC_OK) fail_status("div_exact_1_8", 0, st, DEC_OK);
    else {
        mk(&want, 0, 125, 0, -3, 11);
        check_dec_raw("div_exact_1_8", 0, &out, &want);
    }
    mk(&b, 0, 3, 0, 0, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("div_exact_1_3", 0, &out,
                           dec_div_exact(&out, &a, &b, 0), DEC_INEXACT);
    mk(&a, 0, 0, 0, 0, 0);
    mk(&b, 1, 2, 0, 0, 0);
    st = dec_div_exact(&out, &a, &b, 65535);
    checks++;
    if (st != DEC_OK) fail_status("div_zero_num", 0, st, DEC_OK);
    else {
        mk(&want, 0, 0, 0, 0, 65535);
        check_dec_raw("div_zero_num", 0, &out, &want);
    }
    mk(&b, 0, 0, 0, 0, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("div_by_zero", 0, &out,
                           dec_div_exact(&out, &a, &b, 0), DEC_DIV_ZERO);
    check_status_unchanged("rem_by_zero", 0, &out,
                           dec_rem_trunc(&out, &a, &b, 0), DEC_DIV_ZERO);
    check_status_unchanged("div_round_zero", 0, &out,
                           dec_div_scale(&out, &a, &b, 0, 1, 0),
                           DEC_DIV_ZERO);

    /* Explicit request validation: no hidden default. */
    mk(&a, 0, 1, 0, 0, 0);
    mk(&b, 0, 2, 0, 0, 0);
    dec_div_request req;
    req.kind = DEC_DIV_ROUND_SCALE;
    req.scale = 2;
    req.precision = 0;
    req.mode = DEC_ROUND_HALF_EVEN;
    req.dscale = 0;
    fill_byte(&out, 0xAA);
    check_status_unchanged("div_null_req", 0, &out,
                           dec_div(&out, &a, &b, 0), DEC_INVALID);
    req.kind = 2;
    check_status_unchanged("div_bad_kind", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_INVALID);
    req.kind = DEC_DIV_ROUND_SCALE;
    req.mode = 8;
    check_status_unchanged("div_bad_mode", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_INVALID);
    req.mode = DEC_ROUND_HALF_EVEN;
    req.scale = -32768;
    check_status_unchanged("div_scale_low", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_UNSUPPORTED_RANGE);
    req.scale = 32769;
    check_status_unchanged("div_scale_high", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_UNSUPPORTED_RANGE);
    req.kind = DEC_DIV_ROUND_PRECISION;
    req.precision = 0;
    check_status_unchanged("div_prec_zero", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_INVALID);
    req.precision = 39;
    check_status_unchanged("div_prec_high", 0, &out,
                           dec_div(&out, &a, &b, &req), DEC_INVALID);

    /* Precision carry: 9.99 / 1 to two significant digits is 10, represented
     * as coefficient 10 with exponent 0 (or 1 with exponent 1); this pins the
     * D2 choice: one divide-by-ten at the requested precision. */
    mk(&a, 0, 999, 0, -2, 0);
    mk(&b, 0, 1, 0, 0, 0);
    st = dec_div_precision(&out, &a, &b, 2, DEC_ROUND_HALF_AWAY, 0);
    checks++;
    if (st != DEC_OK) fail_status("prec_carry", 0, st, DEC_OK);
    else {
        mk(&want, 0, 10, 0, 0, 0);
        check_dec_raw("prec_carry", 0, &out, &want);
    }

    /* Upper-edge rescue mirrors the parser: 1e32768 is coefficient 10 with
     * exponent 32767, and 1e32769 is coefficient 100; 1e32805 still overflows. */
    mk(&a, 0, 1, 0, 32767, 0);
    mk(&b, 0, 1, 0, 1, 0);
    st = dec_mul_exact(&out, &a, &b, 5);
    checks++;
    if (st != DEC_OK) fail_status("upper_rescue_mul", 0, st, DEC_OK);
    else {
        mk(&want, 0, 10, 0, 32767, 5);
        check_dec_raw("upper_rescue_mul", 0, &out, &want);
    }
    st = dec_mul(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_OK) fail_status("upper_rescue_mul_round", 0, st, DEC_OK);
    else {
        mk(&want, 0, 10, 0, 32767, 0);
        check_dec_raw("upper_rescue_mul_round", 0, &out, &want);
    }
    mk(&b, 0, 1, 0, -1, 0);       /* 1e32767 / 0.1 = 1e32768 */
    st = dec_div_precision(&out, &a, &b, 1, DEC_ROUND_HALF_EVEN, 0);
    checks++;
    if (st != DEC_OK) fail_status("upper_rescue_div", 0, st, DEC_OK);
    else {
        mk(&want, 0, 10, 0, 32767, 0);
        check_dec_raw("upper_rescue_div", 0, &out, &want);
    }
    mk(&b, 0, 1, 0, -38, 0);      /* 1e32805 needs 39 digits */
    fill_byte(&out, 0xAA);
    check_status_unchanged("upper_rescue_div_overflow", 0, &out,
                           dec_div_precision(&out, &a, &b, 1,
                                             DEC_ROUND_HALF_EVEN, 0),
                           DEC_OVERFLOW);

    /* Exponent range on rounded division is an explicit error, not zero. */
    mk(&a, 0, 1, 0, 32767, 0);
    mk(&b, 0, 1, 0, -32768, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("div_exp_high", 0, &out,
                           dec_div_precision(&out, &a, &b, 1,
                                             DEC_ROUND_HALF_EVEN, 0),
                           DEC_OVERFLOW);
    mk(&a, 0, 1, 0, -32768, 0);
    mk(&b, 0, 1, 0, 32767, 0);
    check_status_unchanged("div_exp_low", 0, &out,
                           dec_div_precision(&out, &a, &b, 1,
                                             DEC_ROUND_HALF_EVEN, 0),
                           DEC_OVERFLOW);

    /* 0.5 at integer scale: the tie policies are explicit and asymmetric. */
    mk(&a, 0, 5, 0, -1, 0);
    mk(&b, 0, 1, 0, 0, 0);
    int expected[8] = {0, 1, 0, 0, 1, 0, 1, -1};
    for (unsigned m = 0; m < 8; m++) {
        fill_byte(&out, 0xAA);
        st = dec_div_scale(&out, &a, &b, 0, m, 0);
        checks++;
        if (m == DEC_ROUND_UNNECESSARY) {
            if (st != DEC_INEXACT)
                fail_status("tie_unnec", m, st, DEC_INEXACT);
        } else if (st != DEC_OK) {
            fail_status("tie", m, st, DEC_OK);
        } else if (out.lo != (uint64_t)expected[m] || out.hi != 0 ||
                   out.exp != 0) {
            fail_field("tie", m, "coefficient", out.lo,
                       (uint64_t)expected[m]);
        }
    }
    (void)b;
}

int dec24_arith_test_main(void) {
    for (unsigned i = 0; i < ARITH_ADD_VECTORS_COUNT; i++)
        run_op_vec("add", i, &arith_add_vectors[i], 0);
    for (unsigned i = 0; i < ARITH_SUB_VECTORS_COUNT; i++)
        run_op_vec("sub", i, &arith_sub_vectors[i], 1);
    for (unsigned i = 0; i < ARITH_MUL_VECTORS_COUNT; i++)
        run_op_vec("mul", i, &arith_mul_vectors[i], 2);
    for (unsigned i = 0; i < ARITH_EXACT_VECTORS_COUNT; i++)
        run_exact_vec(i, &arith_exact_vectors[i]);
    for (unsigned i = 0; i < ARITH_DIV_VECTORS_COUNT; i++)
        run_div_vec(i, &arith_div_vectors[i]);
    for (unsigned i = 0; i < ARITH_REM_VECTORS_COUNT; i++)
        run_rem_vec(i, &arith_rem_vectors[i]);
    boundary_cases();

    if (failures == 0) {
        write_str("PASS arith checks=");
        write_u64(checks);
        write_str("\n");
        return 0;
    }
    write_str("FAIL arith count=");
    write_u64((uint64_t)failures);
    write_str(" checks=");
    write_u64(checks);
    write_str("\n");
    return (failures > 255) ? 255 : failures;
}
