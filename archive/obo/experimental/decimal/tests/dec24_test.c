/* dec24_test.c — freestanding prototype tests.  No libc. */
#include "dec24.h"
#include "vectors_generated.h"

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

static void write_str(const char *s) {
    sys_write(1, s, str_len(s));
}

static void write_u64(uint64_t v) {
    char b[24];
    int i = 24;
    do {
        b[--i] = (char)('0' + (v % 10));
        v /= 10;
    } while (v);
    sys_write(1, b + i, (unsigned long)(24 - i));
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

static void check_dec(const char *name, unsigned idx, const dec24 *got,
                      const dec24 *want) {
    checks++;
    if (got->sign != want->sign) fail_field(name, idx, "sign", got->sign, want->sign);
    if (got->lo != want->lo) fail_field(name, idx, "lo", got->lo, want->lo);
    if (got->hi != want->hi) fail_field(name, idx, "hi", got->hi, want->hi);
    if ((uint16_t)(int16_t)got->exp != (uint16_t)(int16_t)want->exp)
        fail_field(name, idx, "exp", (uint16_t)got->exp, (uint16_t)want->exp);
    if (got->dscale != want->dscale) fail_field(name, idx, "dscale", got->dscale, want->dscale);
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

static void check_cmp(const char *name, unsigned idx, const dec24 *a,
                      const dec24 *b, int want) {
    checks++;
    int got = dec_cmp(a, b);
    if (got != want)
        fail_field(name, idx, "cmp", (uint64_t)(int64_t)got,
                   (uint64_t)(int64_t)want);
    checks++;
    if (dec_equal(a, b) != (want == 0))
        fail_field(name, idx, "equal", dec_equal(a, b), want == 0);
}

static void run_op(const char *name, unsigned idx, const DecOpVec *v, int is_sub, int is_mul) {
    dec24 a, b, out, want;
    int st = dec_set(&a, v->sign_a, v->alo, v->ahi, v->ea, (uint16_t)v->da);
    if (st != DEC_OK) { fail_status(name, idx, st, DEC_OK); return; }
    st = dec_set(&b, v->sign_b, v->blo, v->bhi, v->eb, (uint16_t)v->db);
    if (st != DEC_OK) { fail_status(name, idx, st, DEC_OK); return; }
    dec_zero(&out);
    if (is_sub) st = dec_sub(&out, &a, &b, v->mode);
    else if (is_mul) st = dec_mul(&out, &a, &b, v->mode);
    else st = dec_add(&out, &a, &b, v->mode);
    checks++;
    if (st != v->status) {
        fail_status(name, idx, st, v->status);
        return;
    }
    if (st == DEC_OK) {
        int ws = dec_set(&want, v->sign_r, v->rlo, v->rhi, v->er, (uint16_t)v->dr);
        if (ws != DEC_OK) { fail_status(name, idx, ws, DEC_OK); return; }
        check_dec(name, idx, &out, &want);
    }
}

int dec24_test_main(void) {
    /* Layout checks are compile-time; repeat the public size as a runtime check. */
    checks++;
    if (sizeof(dec24) != 24) fail_field("layout", 0, "sizeof", sizeof(dec24), 24);

    for (unsigned i = 0; i < DEC_ADD_VECTORS_COUNT; i++)
        run_op("add", i, &dec_add_vectors[i], 0, 0);
    for (unsigned i = 0; i < DEC_SUB_VECTORS_COUNT; i++)
        run_op("sub", i, &dec_sub_vectors[i], 1, 0);
    for (unsigned i = 0; i < DEC_MUL_VECTORS_COUNT; i++)
        run_op("mul", i, &dec_mul_vectors[i], 0, 1);

    for (unsigned i = 0; i < DEC_QUANTIZE_VECTORS_COUNT; i++) {
        const DecQuantVec *v = &dec_quantize_vectors[i];
        dec24 a, out, want;
        int st = dec_set(&a, v->sign_a, v->alo, v->ahi, v->ea, (uint16_t)v->da);
        if (st != DEC_OK) { fail_status("quantize", i, st, DEC_OK); continue; }
        dec_zero(&out);
        st = dec_quantize(&out, &a, (uint16_t)v->target, v->mode);
        checks++;
        if (st != v->status) {
            fail_status("quantize", i, st, v->status);
            continue;
        }
        if (st == DEC_OK) {
            int ws = dec_set(&want, v->sign_r, v->rlo, v->rhi, v->er, (uint16_t)v->dr);
            if (ws != DEC_OK) { fail_status("quantize", i, ws, DEC_OK); continue; }
            check_dec("quantize", i, &out, &want);
        }
    }

    for (unsigned i = 0; i < DEC_MUL64_VECTORS_COUNT; i++) {
        const Mul64Vec *v = &dec_mul64_vectors[i];
        uint64_t lo, hi;
        dec_mul64(v->a, v->b, &lo, &hi);
        checks++;
        if (lo != v->lo) fail_field("mul64", i, "lo", lo, v->lo);
        if (hi != v->hi) fail_field("mul64", i, "hi", hi, v->hi);
    }
    for (unsigned i = 0; i < DEC_MUL128_VECTORS_COUNT; i++) {
        const Mul128Vec *v = &dec_mul128_vectors[i];
        dec_u256 r;
        int st = dec_mul128x128(&r, v->alo, v->ahi, v->blo, v->bhi);
        checks++;
        if (st != DEC_OK) { fail_status("mul128", i, st, DEC_OK); continue; }
        if (r.w[0] != v->w0) fail_field("mul128", i, "w0", r.w[0], v->w0);
        if (r.w[1] != v->w1) fail_field("mul128", i, "w1", r.w[1], v->w1);
        if (r.w[2] != v->w2) fail_field("mul128", i, "w2", r.w[2], v->w2);
        if (r.w[3] != v->w3) fail_field("mul128", i, "w3", r.w[3], v->w3);
    }
    for (unsigned i = 0; i < DEC_DIV256_64_VECTORS_COUNT; i++) {
        const Div256Vec *v = &dec_div256_64_vectors[i];
        dec_u256 n = {{v->n0, v->n1, v->n2, v->n3}};
        dec_u256 q;
        uint64_t rem;
        int st = dec_div256_64(&q, &rem, &n, v->d);
        checks++;
        if (st != DEC_OK) { fail_status("div256_64", i, st, DEC_OK); continue; }
        if (q.w[0] != v->q0) fail_field("div256_64", i, "q0", q.w[0], v->q0);
        if (q.w[1] != v->q1) fail_field("div256_64", i, "q1", q.w[1], v->q1);
        if (q.w[2] != v->q2) fail_field("div256_64", i, "q2", q.w[2], v->q2);
        if (q.w[3] != v->q3) fail_field("div256_64", i, "q3", q.w[3], v->q3);
        if (rem != v->rem) fail_field("div256_64", i, "rem", rem, v->rem);
    }
    for (unsigned i = 0; i < DEC_DIV256_128_VECTORS_COUNT; i++) {
        const Div256x128Vec *v = &dec_div256_128_vectors[i];
        dec_u256 n = {{v->n0, v->n1, v->n2, v->n3}};
        dec_u256 q, rem;
        int st = dec_div256_128(&q, &rem, &n, v->dlo, v->dhi);
        checks++;
        if (st != DEC_OK) { fail_status("div256_128", i, st, DEC_OK); continue; }
        if (q.w[0] != v->q0) fail_field("div256_128", i, "q0", q.w[0], v->q0);
        if (q.w[1] != v->q1) fail_field("div256_128", i, "q1", q.w[1], v->q1);
        if (q.w[2] != v->q2) fail_field("div256_128", i, "q2", q.w[2], v->q2);
        if (q.w[3] != v->q3) fail_field("div256_128", i, "q3", q.w[3], v->q3);
        if (rem.w[0] != v->r0) fail_field("div256_128", i, "r0", rem.w[0], v->r0);
        if (rem.w[1] != v->r1) fail_field("div256_128", i, "r1", rem.w[1], v->r1);
        if (rem.w[2] != v->r2) fail_field("div256_128", i, "r2", rem.w[2], v->r2);
        if (rem.w[3] != v->r3) fail_field("div256_128", i, "r3", rem.w[3], v->r3);
    }

    for (unsigned i = 0; i < DEC_CMP_VECTORS_COUNT; i++) {
        const DecCmpVec *v = &dec_cmp_vectors[i];
        dec24 a, b;
        int st = dec_set(&a, v->sign_a, v->alo, v->ahi, v->ea, (uint16_t)v->da);
        if (st != DEC_OK) { fail_status("cmp", i, st, DEC_OK); continue; }
        st = dec_set(&b, v->sign_b, v->blo, v->bhi, v->eb, (uint16_t)v->db);
        if (st != DEC_OK) { fail_status("cmp", i, st, DEC_OK); continue; }
        check_cmp("cmp", i, &a, &b, v->order);
        check_cmp("cmp_reverse", i, &b, &a, -v->order);
    }

    /* Semantic checks that are not just generated vectors. */
    dec24 a, b, out, want;
    dec_set(&a, 0, 150, 0, -2, 2);       /* 1.50 */
    dec_set(&b, 0, 15, 0, -1, 1);        /* 1.5 */
    checks++;
    if (!dec_equal(&a, &b)) fail_field("equal", 0, "1.50==1.5", 0, 1);
    checks++;
    if (a.dscale != 2) fail_field("dscale", 0, "1.50", a.dscale, 2);

    /* Explicit negative / zero ordering repros (the old C returned
     * zero < negative and ranked negative magnitudes ascending). */
    dec_set(&a, 1, 2, 0, 0, 0);          /* -2 */
    dec_set(&b, 1, 1, 0, 0, 3);          /* -1, different dscale */
    check_cmp("order_neg", 0, &a, &b, -1);
    check_cmp("order_neg_rev", 0, &b, &a, 1);

    dec_set(&a, 0, 0, 0, 0, 0);          /* 0 */
    dec_set(&b, 1, 5, 0, 0, 0);          /* -5 */
    check_cmp("order_zero_neg", 0, &a, &b, 1);
    check_cmp("order_zero_neg_rev", 0, &b, &a, -1);

    dec_set(&a, 1, 25, 0, -1, 1);        /* -2.5 */
    dec_set(&b, 1, 150, 0, -2, 2);       /* -1.50 */
    check_cmp("order_neg_exp", 0, &a, &b, -1);
    check_cmp("order_neg_exp_rev", 0, &b, &a, 1);

    dec_set(&a, 1, 150, 0, -2, 0);       /* -1.50 */
    dec_set(&b, 1, 15, 0, -1, 65535);    /* -1.5, dscale ignored */
    check_cmp("order_neg_alias", 0, &a, &b, 0);

    dec_set(&a, 0, 1500, 0, -3, 3);      /* 1.500 */
    dec_set(&b, 0, 15, 0, -1, 0);        /* 1.5 */
    check_cmp("order_pos_alias", 0, &a, &b, 0);

    dec_set(&a, 1, 1, 0, 76, 0);         /* -1e76 */
    dec_set(&b, 1, 1, 0, 75, 0);         /* -1e75 */
    check_cmp("order_neg_adjusted", 0, &a, &b, -1);
    check_cmp("order_neg_adjusted_rev", 0, &b, &a, 1);

    dec_set(&a, 1, 1, 0, 76, 0);         /* -1e76 */
    dec_set(&b, 1, 10, 0, 75, 0);        /* -1e76 alias */
    check_cmp("order_neg_adjusted_alias", 0, &a, &b, 0);

    dec_set(&a, 0, 150, 0, -2, 2);
    dec_set(&b, 0, 150, 0, -2, 2);
    dec_add(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    dec_set(&want, 0, 300, 0, -2, 2);    /* 3.00 */
    check_dec("add_1.50", 0, &out, &want);

    dec_set(&a, 0, 120, 0, -2, 2);       /* 1.20 */
    dec_set(&b, 0, 12, 0, -1, 1);        /* 1.2 */
    dec_mul(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    dec_set(&want, 0, 1440, 0, -3, 3);   /* 1.440 */
    check_dec("mul_1.20", 0, &out, &want);

    dec_set(&a, 0, 15, 0, -1, 1);        /* 1.5 */
    int expected_mode[8] = {2, 2, 1, 1, 2, 1, 2, -1};
    for (unsigned m = 0; m < 8; m++) {
        dec_zero(&out);
        int st = dec_quantize(&out, &a, 0, m);
        checks++;
        if (m == DEC_ROUND_UNNECESSARY) {
            if (st != DEC_INEXACT) fail_status("quantize_mode", m, st, DEC_INEXACT);
        } else {
            if (st != DEC_OK) { fail_status("quantize_mode", m, st, DEC_OK); continue; }
            if ((int)out.lo != expected_mode[m])
                fail_field("quantize_mode", m, "coeff", out.lo, (uint64_t)expected_mode[m]);
            if (out.exp != 0 || out.dscale != 0)
                fail_field("quantize_mode", m, "exp/dscale", out.exp, 0);
        }
    }

    checks++;
    if (dec_quantize(&out, &a, 32769, DEC_ROUND_HALF_EVEN) != DEC_UNSUPPORTED_RANGE)
        fail_field("quantize_range", 0, "target32769", 0, DEC_UNSUPPORTED_RANGE);

    /* Zero quantize accepts every uint16 dscale, under every valid mode. */
    dec_zero(&a);
    a.dscale = 123;  /* arbitrary input display scale must be replaced */
    uint16_t zero_targets[5] = {0, 1, 32768, 32769, 65535};
    for (unsigned ti = 0; ti < 5; ti++) {
        for (unsigned m = 0; m < 8; m++) {
            dec_zero(&out);
            int st = dec_quantize(&out, &a, zero_targets[ti], m);
            checks++;
            if (st != DEC_OK) {
                fail_status("quantize_zero", (ti * 8) + m, st, DEC_OK);
                continue;
            }
            dec_set(&want, 0, 0, 0, 0, zero_targets[ti]);
            check_dec("quantize_zero", (ti * 8) + m, &out, &want);
            checks++;
            if (out.flags != 0 || out.pad[0] != 0 || out.pad[1] != 0)
                fail_field("quantize_zero", (ti * 8) + m, "flags/pad", out.flags, 0);
        }
    }

    /* Zero fast path must still reject an invalid mode without writing dst. */
    fill_byte(&out, 0xAA);
    check_status_unchanged("quantize_zero_badmode", 0, &out,
                           dec_quantize(&out, &a, 65535, 8), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("quantize_zero_badmode2", 0, &out,
                           dec_quantize(&out, &a, 65535, 999), DEC_INVALID);

    /* A nonzero value with dscale 65535 is representable, but strict
     * quantize to quantum exp=-65535 is not; the refusal is separate. */
    dec_set(&a, 0, 1, 0, 0, 65535);
    checks++;
    if (!dec_valid(&a))
        fail_field("nonzero_dscale65535", 0, "valid", 0, 1);
    fill_byte(&out, 0xAA);
    check_status_unchanged("quantize_nonzero_65535", 0, &out,
                           dec_quantize(&out, &a, 65535, DEC_ROUND_HALF_EVEN),
                           DEC_UNSUPPORTED_RANGE);
    fill_byte(&out, 0xAA);
    check_status_unchanged("quantize_nonzero_32769", 0, &out,
                           dec_quantize(&out, &a, 32769, DEC_ROUND_HALF_EVEN),
                           DEC_UNSUPPORTED_RANGE);

    /* Raw-struct validation: the zero shortcut must not accept malformed
     * values, and invalid input must leave the sentinel output unchanged. */
    dec24 bad;
    dec_set(&a, 0, 15, 0, -1, 1);
    fill_byte(&bad, 0);
    bad.lo = 1; bad.sign = 2;
    checks++;
    if (dec_valid(&bad)) fail_field("valid_sign2", 0, "valid", 1, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_add_a", 0, &out,
                           dec_add(&out, &bad, &a, 0), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_add_b", 0, &out,
                           dec_add(&out, &a, &bad, 0), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_sub", 0, &out,
                           dec_sub(&out, &a, &bad, 0), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_mul", 0, &out,
                           dec_mul(&out, &bad, &a, 0), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_quantize", 0, &out,
                           dec_quantize(&out, &bad, 0, 0), DEC_INVALID);

    fill_byte(&bad, 0);
    bad.lo = DEC_COEFF_MAX_LO + 1; bad.hi = DEC_COEFF_MAX_HI;
    checks++;
    if (dec_valid(&bad)) fail_field("valid_coeff", 0, "valid", 1, 0);
    fill_byte(&bad, 0);
    bad.lo = 1; bad.flags = 1;
    checks++;
    if (dec_valid(&bad)) fail_field("valid_flags", 0, "valid", 1, 0);
    fill_byte(&bad, 0);
    bad.lo = 1; bad.pad[0] = 1;
    checks++;
    if (dec_valid(&bad)) fail_field("valid_pad", 0, "valid", 1, 0);
    fill_byte(&bad, 0);
    bad.sign = 1;  /* noncanonical negative zero */
    checks++;
    if (dec_valid(&bad)) fail_field("valid_neg_zero", 0, "valid", 1, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("invalid_neg_zero_add", 0, &out,
                           dec_add(&out, &bad, &a, 0), DEC_INVALID);
    fill_byte(&bad, 0);
    bad.exp = 5;   /* noncanonical zero exponent */
    checks++;
    if (dec_valid(&bad)) fail_field("valid_zero_exp", 0, "valid", 1, 0);
    checks++;
    if (dec_valid(0)) fail_field("valid_null", 0, "valid", 1, 0);
    dec_zero(&bad); bad.dscale = 65535;
    checks++;
    if (!dec_valid(&bad)) fail_field("valid_zero_dscale", 0, "valid", 0, 1);

    /* dec_set itself must not write dst on invalid arguments. */
    fill_byte(&out, 0xAA);
    check_status_unchanged("dec_set_sign2", 0, &out,
                           dec_set(&out, 2, 1, 0, 0, 0), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("dec_set_coeff", 0, &out,
                           dec_set(&out, 0, DEC_COEFF_MAX_LO + 1,
                                   DEC_COEFF_MAX_HI, 0, 0), DEC_OVERFLOW);

    /* Zero add/sub/mul fast paths must also reject an invalid mode. */
    dec_zero(&a);
    a.dscale = 65535;
    dec_set(&b, 0, 1, 0, 0, 0);
    fill_byte(&out, 0xAA);
    check_status_unchanged("zero_add_badmode", 0, &out,
                           dec_add(&out, &a, &b, 8), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("zero_sub_badmode", 0, &out,
                           dec_sub(&out, &a, &b, 8), DEC_INVALID);
    fill_byte(&out, 0xAA);
    check_status_unchanged("zero_mul_badmode", 0, &out,
                           dec_mul(&out, &a, &b, 8), DEC_INVALID);

    /* D2 sticky completion: 12e76 + 1 is no longer refused.  Its exact value
     * needs more than 38 significant digits, but the rounded API returns the
     * 38-digit grid point (12e76 for nearest/truncation) and the exact API
     * reports INEXACT.  77/78-digit aligned integers below 2^256 remain on
     * the existing exact alignment path. */
    dec_set(&a, 0, 12, 0, 76, 0);
    dec_set(&b, 0, 1, 0, 0, 0);
    dec_zero(&out);
    int sticky_add = dec_add(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (sticky_add != DEC_OK) {
        fail_status("sticky_12e76", 0, sticky_add, DEC_OK);
    } else {
        dec_set(&want, 0, 12, 0, 76, 0);
        check_dec("sticky_12e76", 0, &out, &want);
    }
    dec_zero(&out);
    int sticky_sub = dec_sub(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (sticky_sub != DEC_OK) {
        fail_status("sticky_12e76_sub", 0, sticky_sub, DEC_OK);
    } else {
        dec_set(&want, 0, 12, 0, 76, 0);
        check_dec("sticky_12e76_sub", 0, &out, &want);
    }

    dec_set(&a, 0, 9, 0, 76, 0);
    dec_set(&b, 0, 1, 0, 0, 0);
    int st9 = dec_add(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st9 != DEC_OK) {
        fail_status("guard_accept_9e76", 0, st9, DEC_OK);
    } else {
        /* 9e76 + 1 rounds to coefficient 9*10^37, exponent 39. */
        dec_set(&want, 0, 0x0895eba000000000ULL, 0x43b55e9784ac7da1ULL, 39, 0);
        check_dec("guard_accept_9e76", 0, &out, &want);

        int stsub = dec_sub(&out, &a, &b, DEC_ROUND_HALF_EVEN);
        checks++;
        if (stsub != DEC_OK) {
            fail_status("guard_accept_9e76_sub", 0, stsub, DEC_OK);
        } else {
            check_dec("guard_accept_9e76_sub", 0, &out, &want);
        }
    }

    dec_set(&a, 0, 109, 0, 75, 0);      /* 1.09e77, 78 digits */
    dec_set(&b, 0, 1, 0, 0, 0);
    int st109 = dec_add(&out, &a, &b, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st109 != DEC_OK) {
        fail_status("guard_accept_109e75", 0, st109, DEC_OK);
    } else {
        /* 109e75 + 1 rounds to coefficient 109*10^35, exponent 40. */
        dec_set(&want, 0, 0x88b845c800000000ULL, 0x0833437ddedc006cULL, 40, 0);
        check_dec("guard_accept_109e75", 0, &out, &want);
    }

    /* Aliasing regression: dst may equal a or b. */
    dec24 aa;
    dec_set(&aa, 0, 120, 0, -2, 2);
    dec_set(&b, 0, 34, 0, -2, 2);
    dec_add(&out, &aa, &b, DEC_ROUND_HALF_EVEN);
    dec_set(&a, 0, 120, 0, -2, 2);
    dec_add(&a, &a, &b, DEC_ROUND_HALF_EVEN);
    check_dec("alias_add_a", 0, &a, &out);
    dec_set(&b, 0, 34, 0, -2, 2);
    dec_add(&b, &aa, &b, DEC_ROUND_HALF_EVEN);
    check_dec("alias_add_b", 0, &b, &out);

    dec_set(&aa, 0, 120, 0, -2, 2);
    dec_set(&b, 0, 34, 0, -2, 2);
    dec_sub(&out, &aa, &b, DEC_ROUND_HALF_EVEN);
    dec_set(&a, 0, 120, 0, -2, 2);
    dec_sub(&a, &a, &b, DEC_ROUND_HALF_EVEN);
    check_dec("alias_sub", 0, &a, &out);

    dec_set(&aa, 0, 120, 0, -2, 2);
    dec_set(&b, 0, 34, 0, -2, 2);
    dec_mul(&out, &aa, &b, DEC_ROUND_HALF_EVEN);
    dec_set(&a, 0, 120, 0, -2, 2);
    dec_mul(&a, &a, &b, DEC_ROUND_HALF_EVEN);
    check_dec("alias_mul", 0, &a, &out);

    dec_set(&a, 0, 15, 0, -1, 1);
    dec_quantize(&out, &a, 0, DEC_ROUND_HALF_EVEN);
    dec_quantize(&a, &a, 0, DEC_ROUND_HALF_EVEN);
    check_dec("alias_quantize", 0, &a, &out);

    dec_u256 n = {{0, 0, 0, 0}}, q, rem;
    checks++;
    if (dec_div256_128(&q, &rem, &n, 0, 0) != DEC_DIV_ZERO)
        fail_field("div_zero", 0, "divisor0", 0, DEC_DIV_ZERO);

    checks++;
    if (dec_set(&out, 0, DEC_COEFF_MAX_LO + 1, DEC_COEFF_MAX_HI, 0, 0) != DEC_OVERFLOW)
        fail_field("coeff_overflow", 0, "10^38", 0, DEC_OVERFLOW);

    if (failures == 0) {
        write_str("PASS checks=");
        write_u64(checks);
        write_str("\n");
        return 0;
    }
    write_str("FAIL count=");
    write_u64((uint64_t)failures);
    write_str(" checks=");
    write_u64(checks);
    write_str("\n");
    return (failures > 255) ? 255 : failures;
}
