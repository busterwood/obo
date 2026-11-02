/* dec24_bind_test.c — freestanding tests for private binding validation.
 * No libc.  Expected values come from binding_oracle.py fixtures.
 */
#include "dec24.h"
#include "dec24_bind.h"
#include "binding_vectors_generated.h"

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

static void fail_u64(const char *name, unsigned idx, const char *field,
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

static void fill_byte(void *p, unsigned long n, uint8_t v) {
    unsigned char *b = (unsigned char *)p;
    for (unsigned long i = 0; i < n; i++) b[i] = v;
}

static int all_byte(const void *p, unsigned long n, uint8_t v) {
    const unsigned char *b = (const unsigned char *)p;
    for (unsigned long i = 0; i < n; i++)
        if (b[i] != v) return 0;
    return 1;
}

static void check_dec(const char *name, unsigned idx, const dec24 *got,
                      const dec24 *want) {
    checks++;
    if (got->sign != want->sign)
        fail_u64(name, idx, "sign", got->sign, want->sign);
    checks++;
    if (got->lo != want->lo)
        fail_u64(name, idx, "lo", got->lo, want->lo);
    checks++;
    if (got->hi != want->hi)
        fail_u64(name, idx, "hi", got->hi, want->hi);
    checks++;
    if ((uint16_t)(int16_t)got->exp != (uint16_t)(int16_t)want->exp)
        fail_u64(name, idx, "exp", (uint16_t)got->exp, (uint16_t)want->exp);
    checks++;
    if (got->dscale != want->dscale)
        fail_u64(name, idx, "dscale", got->dscale, want->dscale);
}

static void run_bind_vec(unsigned idx, const DecBindVec *v) {
    dec24 a, out, want;
    int st = dec_set(&a, v->sign, v->lo, v->hi, v->exp,
                     (uint16_t)v->dscale);
    if (st != DEC_OK) {
        fail_status("bind_set", idx, st, DEC_OK);
        return;
    }
    st = dec_bind_exact_fits(&a, (int64_t)v->p, (int64_t)v->s);
    checks++;
    if (st != v->exact_status)
        fail_status("exact_fits", idx, st, v->exact_status);

    fill_byte(&out, sizeof out, 0xAA);
    st = dec_bind_quantize(&out, &a, (int64_t)v->p, (int64_t)v->s, v->mode);
    checks++;
    if (st != v->round_status) {
        fail_status("bind_round", idx, st, v->round_status);
    }
    if (st == DEC_OK) {
        int ws = dec_set(&want, v->rsign, v->rlo, v->rhi, v->rexp,
                         (uint16_t)v->rdscale);
        if (ws != DEC_OK) {
            fail_status("bind_want", idx, ws, DEC_OK);
        } else {
            check_dec("bind_round", idx, &out, &want);
        }
    } else {
        checks++;
        if (!all_byte(&out, sizeof out, 0xAA))
            fail_u64("bind_error_write", idx, "unchanged", 0, 1);
    }
}

static void test_binding_edges(void) {
    dec24 a, out, want;
    /* The required carrier case: 999.995 rounded half away at scale 2 is
     * 1000.00, whose scaled integer has six digits and does not fit p=5. */
    dec_set(&a, 0, 999995, 0, -3, 3);
    fill_byte(&out, sizeof out, 0xAA);
    int st = dec_bind_quantize(&out, &a, 5, 2, DEC_ROUND_HALF_AWAY);
    checks++;
    if (st != DEC_PRECISION_OVERFLOW)
        fail_status("carry_overflow", 0, st, DEC_PRECISION_OVERFLOW);
    checks++;
    if (!all_byte(&out, sizeof out, 0xAA))
        fail_u64("carry_overflow_write", 0, "unchanged", 0, 1);

    st = dec_bind_quantize(&out, &a, 6, 2, DEC_ROUND_HALF_AWAY);
    checks++;
    if (st != DEC_OK) {
        fail_status("carry_fits", 0, st, DEC_OK);
    } else {
        dec_set(&want, 0, 100000, 0, -2, 2);
        check_dec("carry_fits", 0, &out, &want);
    }
    st = dec_bind_exact_fits(&a, 6, 2);
    checks++;
    if (st != DEC_INEXACT)
        fail_status("exact_inexact", 0, st, DEC_INEXACT);

    /* All explicit modes on 1.5 at scale 0. */
    dec_set(&a, 0, 15, 0, -1, 1);
    int expect[8] = {2, 2, 1, 1, 2, 1, 2, -1};
    for (unsigned m = 0; m < 8; m++) {
        fill_byte(&out, sizeof out, 0xAA);
        st = dec_bind_quantize(&out, &a, 2, 0, m);
        checks++;
        if (m == DEC_ROUND_UNNECESSARY) {
            if (st != DEC_INEXACT)
                fail_status("mode_unnecessary", m, st, DEC_INEXACT);
            checks++;
            if (!all_byte(&out, sizeof out, 0xAA))
                fail_u64("mode_unnecessary_write", m, "unchanged", 0, 1);
        } else {
            if (st != DEC_OK) {
                fail_status("mode", m, st, DEC_OK);
                continue;
            }
            checks++;
            if (out.lo != (uint64_t)expect[m])
                fail_u64("mode_coeff", m, "coeff", out.lo,
                         (uint64_t)expect[m]);
            checks++;
            if (out.exp != 0 || out.dscale != 0)
                fail_u64("mode_exp", m, "exp/dscale", out.exp, 0);
        }
    }
    dec_set(&a, 1, 15, 0, -1, 1);
    st = dec_bind_quantize(&out, &a, 2, 0, DEC_ROUND_FLOOR);
    checks++;
    if (st != DEC_OK || out.sign != 1 || out.lo != 2)
        fail_u64("negative_floor", 0, "value", out.lo, 2);
    st = dec_bind_quantize(&out, &a, 2, 0, DEC_ROUND_CEILING);
    checks++;
    if (st != DEC_OK || out.sign != 1 || out.lo != 1)
        fail_u64("negative_ceiling", 0, "value", out.lo, 1);

    /* Negative scale and s > p are mathematical shapes only. */
    dec_set(&a, 0, 99999, 0, 2, 0);
    st = dec_bind_exact_fits(&a, 5, -2);
    checks++;
    if (st != DEC_OK) fail_status("negscale_fit", 0, st, DEC_OK);
    dec_set(&a, 0, 100000, 0, 2, 0);
    st = dec_bind_exact_fits(&a, 5, -2);
    checks++;
    if (st != DEC_PRECISION_OVERFLOW)
        fail_status("negscale_over", 0, st, DEC_PRECISION_OVERFLOW);
    dec_set(&a, 0, 999, 0, -5, 5);
    st = dec_bind_exact_fits(&a, 3, 5);
    checks++;
    if (st != DEC_OK) fail_status("s_gt_p", 0, st, DEC_OK);
    dec_set(&a, 0, 1000, 0, -5, 5);
    st = dec_bind_exact_fits(&a, 3, 5);
    checks++;
    if (st != DEC_PRECISION_OVERFLOW)
        fail_status("s_gt_p_over", 0, st, DEC_PRECISION_OVERFLOW);

    /* Zero fits every legal mathematical scale; quantize preserves dscale
     * only when it is uint16, and never invents negative zero. */
    dec_zero(&a);
    int64_t zscales[5] = {-1000, -1, 0, 1, 65535};
    for (unsigned i = 0; i < 5; i++) {
        st = dec_bind_exact_fits(&a, 1, zscales[i]);
        checks++;
        if (st != DEC_OK) fail_status("zero_exact", i, st, DEC_OK);
        fill_byte(&out, sizeof out, 0xAA);
        st = dec_bind_quantize(&out, &a, 1, zscales[i], DEC_ROUND_HALF_EVEN);
        checks++;
        if (st != DEC_OK) {
            fail_status("zero_round", i, st, DEC_OK);
        } else {
            uint16_t ds = zscales[i] < 0 ? 0 : (uint16_t)zscales[i];
            dec_zero(&want);
            want.dscale = ds;
            check_dec("zero_round", i, &out, &want);
        }
    }

    /* Unsupported representation scales and invalid parameters. */
    dec_set(&a, 0, 1, 0, 0, 0);
    st = dec_bind_quantize(&out, &a, 5, 40000, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_UNSUPPORTED_RANGE)
        fail_status("scale_big", 0, st, DEC_UNSUPPORTED_RANGE);
    st = dec_bind_quantize(&out, &a, 5, -32768, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_UNSUPPORTED_RANGE)
        fail_status("scale_small", 0, st, DEC_UNSUPPORTED_RANGE);
    st = dec_bind_quantize(&out, &a, 0, 0, DEC_ROUND_HALF_EVEN);
    checks++;
    if (st != DEC_INVALID) fail_status("p_zero", 0, st, DEC_INVALID);
    st = dec_bind_quantize(&out, &a, 5, 0, 8);
    checks++;
    if (st != DEC_INVALID) fail_status("bad_mode", 0, st, DEC_INVALID);
    st = dec_bind_exact_fits(&a, 0, 0);
    checks++;
    if (st != DEC_INVALID) fail_status("exact_p_zero", 0, st, DEC_INVALID);
}

int dec24_bind_test_main(void) {
    for (unsigned i = 0; i < DEC_BIND_VECTORS_COUNT; i++)
        run_bind_vec(i, &dec_bind_vectors[i]);
    test_binding_edges();

    if (failures == 0) {
        write_str("PASS bind checks=");
        write_u64(checks);
        write_str("\n");
        return 0;
    }
    write_str("FAIL bind count=");
    write_u64((uint64_t)failures);
    write_str(" checks=");
    write_u64(checks);
    write_str("\n");
    return (failures > 255) ? 255 : failures;
}
