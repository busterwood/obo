/* dec24_text_test.c — freestanding tests for the private D1 text helpers.
 * No libc.  Expected values come from the independent Python oracle fixture.
 */
#include "dec24.h"
#include "dec24_text.h"
#include "text_vectors_generated.h"

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

static int bytes_equal(const void *a, const void *b, unsigned long n) {
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    for (unsigned long i = 0; i < n; i++)
        if (x[i] != y[i]) return 0;
    return 1;
}

static int str_equal(const char *a, const char *b) {
    unsigned long i = 0;
    while (a[i] && b[i]) {
        if (a[i] != b[i]) return 0;
        i++;
    }
    return a[i] == 0 && b[i] == 0;
}

static void check_dec_fields(const char *name, unsigned idx, const dec24 *got,
                             const dec24 *want, int check_dscale) {
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
    if (check_dscale) {
        checks++;
        if (got->dscale != want->dscale)
            fail_u64(name, idx, "dscale", got->dscale, want->dscale);
    }
}

static void check_parse_ok(unsigned idx, const DecTextVec *v, const dec24 *got) {
    dec24 want;
    int st = dec_set(&want, v->sign, v->lo, v->hi, v->exp,
                     (uint16_t)v->dscale);
    if (st != DEC_OK) {
        fail_status("parse_expected_set", idx, st, DEC_OK);
        return;
    }
    check_dec_fields("parse", idx, got, &want, 1);

    size_t n = 0;
    st = dec_format_canonical_len(got, &n);
    checks++;
    if (st != DEC_OK) {
        fail_status("canon_len", idx, st, DEC_OK);
        return;
    }
    checks++;
    if (n != str_len(v->canonical))
        fail_u64("canon_len_value", idx, "len", n, str_len(v->canonical));

    char buf[130];
    fill_byte(buf, sizeof buf, 0x5A);
    size_t out_len = 0;
    st = dec_format_canonical(got, buf, n, &out_len);
    checks++;
    if (st != DEC_BUFFER_SMALL) {
        fail_status("canon_small", idx, st, DEC_BUFFER_SMALL);
    } else {
        checks++;
        if (out_len != n) fail_u64("canon_small_len", idx, "len", out_len, n);
        checks++;
        if (!all_byte(buf, sizeof buf, 0x5A))
            fail_u64("canon_small_write", idx, "unchanged", 0, 1);
    }

    fill_byte(buf, sizeof buf, 0x5A);
    st = dec_format_canonical(got, buf, n + 1, &out_len);
    checks++;
    if (st != DEC_OK) {
        fail_status("canon", idx, st, DEC_OK);
    } else {
        checks++;
        if (out_len != n) fail_u64("canon_out_len", idx, "len", out_len, n);
        checks++;
        if (!str_equal(buf, v->canonical))
            fail_u64("canon_text", idx, "text", 0, 1);
        dec24 reparsed;
        int rst = dec_parse(&reparsed, buf, n);
        checks++;
        if (rst != DEC_OK) {
            fail_status("canon_reparse", idx, rst, DEC_OK);
        } else {
            checks++;
            if (dec_cmp(got, &reparsed) != 0)
                fail_u64("canon_value", idx, "equal", 0, 1);
        }
    }

    size_t dn = 0;
    st = dec_format_display_len(got, &dn);
    checks++;
    if (st != v->display_status) {
        fail_status("disp_len", idx, st, v->display_status);
        return;
    }
    if (st != DEC_OK) return;
    checks++;
    if (dn != str_len(v->display))
        fail_u64("disp_len_value", idx, "len", dn, str_len(v->display));
    /* Huge fixed displays are verified by size query only; the C harness
     * deliberately does not allocate a 32 KiB fixture buffer. */
    if (dn + 1 > 1024) return;
    char dbuf[1024];
    fill_byte(dbuf, sizeof dbuf, 0x5A);
    st = dec_format_display(got, dbuf, dn, &out_len);
    checks++;
    if (st != DEC_BUFFER_SMALL) {
        fail_status("disp_small", idx, st, DEC_BUFFER_SMALL);
    } else {
        checks++;
        if (out_len != dn) fail_u64("disp_small_len", idx, "len", out_len, dn);
        checks++;
        if (!all_byte(dbuf, sizeof dbuf, 0x5A))
            fail_u64("disp_small_write", idx, "unchanged", 0, 1);
    }
    st = dec_format_display(got, dbuf, dn + 1, &out_len);
    checks++;
    if (st != DEC_OK) {
        fail_status("disp", idx, st, DEC_OK);
    } else {
        checks++;
        if (!str_equal(dbuf, v->display))
            fail_u64("disp_text", idx, "text", 0, 1);
        dec24 reparsed;
        int rst = dec_parse(&reparsed, dbuf, dn);
        checks++;
        if (rst != DEC_OK) {
            fail_status("disp_reparse", idx, rst, DEC_OK);
        } else {
            check_dec_fields("disp_reparse", idx, &reparsed, got, 1);
        }
    }
}

static void run_parse_vec(unsigned idx, const DecTextVec *v) {
    dec24 out;
    fill_byte(&out, sizeof out, 0xAA);
    int st = dec_parse(&out, v->text, (size_t)v->len);
    checks++;
    if (st != v->status) {
        fail_status("parse", idx, st, v->status);
        return;
    }
    if (st == DEC_OK) {
        check_parse_ok(idx, v, &out);
    } else {
        checks++;
        if (!all_byte(&out, sizeof out, 0xAA))
            fail_u64("parse_error_write", idx, "unchanged", 0, 1);
    }
}

static void run_format_vec(unsigned idx, const DecFormatVec *v) {
    dec24 a;
    int st = dec_set(&a, v->sign, v->lo, v->hi, v->exp,
                     (uint16_t)v->dscale);
    if (st != DEC_OK) {
        fail_status("format_set", idx, st, DEC_OK);
        return;
    }
    size_t n = 0;
    st = dec_format_canonical_len(&a, &n);
    checks++;
    if (st != DEC_OK) {
        fail_status("format_canon_len", idx, st, DEC_OK);
        return;
    }
    checks++;
    if (n != str_len(v->canonical))
        fail_u64("format_canon_len_value", idx, "len", n, str_len(v->canonical));
    char buf[130];
    fill_byte(buf, sizeof buf, 0x5A);
    size_t out_len = 0;
    st = dec_format_canonical(&a, buf, n, &out_len);
    checks++;
    if (st != DEC_BUFFER_SMALL) {
        fail_status("format_canon_small", idx, st, DEC_BUFFER_SMALL);
    } else {
        checks++;
        if (!all_byte(buf, sizeof buf, 0x5A))
            fail_u64("format_canon_small_write", idx, "unchanged", 0, 1);
    }
    st = dec_format_canonical(&a, buf, n + 1, &out_len);
    checks++;
    if (st != DEC_OK) {
        fail_status("format_canon", idx, st, DEC_OK);
    } else {
        checks++;
        if (!str_equal(buf, v->canonical))
            fail_u64("format_canon_text", idx, "text", 0, 1);
    }

    size_t dn = 0;
    st = dec_format_display_len(&a, &dn);
    checks++;
    if (st != v->display_status) {
        fail_status("format_disp_len", idx, st, v->display_status);
        return;
    }
    if (st != DEC_OK) {
        char dbuf[8];
        fill_byte(dbuf, sizeof dbuf, 0x5A);
        st = dec_format_display(&a, dbuf, sizeof dbuf, &out_len);
        checks++;
        if (st != v->display_status)
            fail_status("format_disp_err", idx, st, v->display_status);
        checks++;
        if (!all_byte(dbuf, sizeof dbuf, 0x5A))
            fail_u64("format_disp_err_write", idx, "unchanged", 0, 1);
        checks++;
        if (out_len != 0)
            fail_u64("format_disp_err_len", idx, "len", out_len, 0);
        return;
    }
    checks++;
    if (dn != str_len(v->display))
        fail_u64("format_disp_len_value", idx, "len", dn, str_len(v->display));
    if (dn + 1 > 1024) return;
    char dbuf[1024];
    st = dec_format_display(&a, dbuf, dn + 1, &out_len);
    checks++;
    if (st != DEC_OK) {
        fail_status("format_disp", idx, st, DEC_OK);
    } else {
        checks++;
        if (!str_equal(dbuf, v->display))
            fail_u64("format_disp_text", idx, "text", 0, 1);
    }
}

static void test_long_and_edge_inputs(void) {
    /* The explicit-length parser must not mutate caller input, even when the
     * caller buffer is writable. */
    char mutable_text[8] = "1.2300";
    char before[8];
    for (unsigned i = 0; i < 8; i++) before[i] = mutable_text[i];
    dec24 mutable_out;
    int mutable_st = dec_parse(&mutable_out, mutable_text, 6);
    checks++;
    if (mutable_st != DEC_OK)
        fail_status("mutable_parse", 0, mutable_st, DEC_OK);
    checks++;
    if (!bytes_equal(mutable_text, before, 8))
        fail_u64("mutable_input", 0, "unchanged", 0, 1);

    static char big[210000];
    /* 1 followed by 100000 zeros: exact coefficient 1, exponent 100000,
     * outside int16.  The scanner must stay linear and not build 10^100000. */
    big[0] = '1';
    for (unsigned long i = 1; i < 100001; i++) big[i] = '0';
    dec24 out;
    fill_byte(&out, sizeof out, 0xAA);
    int st = dec_parse(&out, big, 100001);
    checks++;
    if (st != DEC_EXPONENT_RANGE)
        fail_status("long_exp", 0, st, DEC_EXPONENT_RANGE);
    checks++;
    if (!all_byte(&out, sizeof out, 0xAA))
        fail_u64("long_exp_write", 0, "unchanged", 0, 1);
    /* Input bytes are untouched by the const parser. */
    checks++;
    if (big[0] != '1' || big[100000] != '0')
        fail_u64("long_input", 0, "mutated", 1, 0);

    /* 0. followed by 100000 zeros: display scale 100000 > uint16. */
    big[0] = '0';
    big[1] = '.';
    for (unsigned long i = 2; i < 100002; i++) big[i] = '0';
    fill_byte(&out, sizeof out, 0xAA);
    st = dec_parse(&out, big, 100002);
    checks++;
    if (st != DEC_DISPLAY_RANGE)
        fail_status("long_dscale", 0, st, DEC_DISPLAY_RANGE);

    /* Long exponent digits saturate; no overflow and no big-integer work. */
    big[0] = '1';
    big[1] = 'e';
    for (unsigned long i = 2; i < 20002; i++) big[i] = '9';
    st = dec_parse(&out, big, 20002);
    checks++;
    if (st != DEC_EXPONENT_RANGE)
        fail_status("long_expdigits", 0, st, DEC_EXPONENT_RANGE);

    /* Zero with 60000 fractional zeros is representable: dscale 60000. */
    big[0] = '0';
    big[1] = '.';
    for (unsigned long i = 2; i < 60002; i++) big[i] = '0';
    st = dec_parse(&out, big, 60002);
    checks++;
    if (st != DEC_OK) {
        fail_status("long_zero", 0, st, DEC_OK);
    } else {
        checks++;
        if (out.dscale != 60000)
            fail_u64("long_zero_dscale", 0, "dscale", out.dscale, 60000);
        size_t n = 0;
        st = dec_format_display_len(&out, &n);
        checks++;
        if (st != DEC_OK || n != 60002) {
            fail_u64("long_zero_disp_len", 0, "len", n, 60002);
            fail_status("long_zero_disp_status", 0, st, DEC_OK);
        }
        char one = 0x5A;
        st = dec_format_display(&out, &one, 1, &n);
        checks++;
        if (st != DEC_BUFFER_SMALL)
            fail_status("long_zero_small", 0, st, DEC_BUFFER_SMALL);
        checks++;
        if (one != 0x5A)
            fail_u64("long_zero_small_write", 0, "unchanged", 0, 1);
    }

    /* Embedded NUL and non-ASCII bytes are rejected with explicit lengths. */
    const char nbuf[3] = {'1', 0, '2'};
    fill_byte(&out, sizeof out, 0xAA);
    st = dec_parse(&out, nbuf, 3);
    checks++;
    if (st != DEC_INVALID) fail_status("nul_embedded", 0, st, DEC_INVALID);
    st = dec_parse(&out, nbuf, 1);
    checks++;
    if (st != DEC_OK) fail_status("nul_boundary", 0, st, DEC_OK);
    const unsigned char ub[2] = {0xC3, 0xA9};
    fill_byte(&out, sizeof out, 0xAA);
    st = dec_parse(&out, (const char *)ub, 2);
    checks++;
    if (st != DEC_INVALID) fail_status("utf8", 0, st, DEC_INVALID);
    st = dec_parse(&out, "", 0);
    checks++;
    if (st != DEC_INVALID) fail_status("empty", 0, st, DEC_INVALID);
}

static void test_canonical_and_display_edges(void) {
    dec24 a;
    char buf[130];
    size_t n = 0;
    /* A trailing coefficient zero must still move the scientific exponent.
     * 120 * 10^200 is 1.2e202, not 1.2e201. */
    dec_set(&a, 0, 120, 0, 200, 0);
    int st = dec_format_canonical(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_OK || !str_equal(buf, "1.2e202")) {
        fail_status("canon_trailing_sci", 0, st, DEC_OK);
        fail_u64("canon_trailing_sci_text", 0, "text", 0, 1);
    }
    dec_set(&a, 0, 120, 0, -200, 0);
    st = dec_format_canonical(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_OK || !str_equal(buf, "1.2e-198")) {
        fail_status("canon_trailing_sci_neg", 0, st, DEC_OK);
        fail_u64("canon_trailing_sci_neg_text", 0, "text", 0, 1);
    }

    /* Display never rounds. */
    dec_set(&a, 0, 12, 0, -1, 0);       /* 1.2 at dscale 0 */
    fill_byte(buf, sizeof buf, 0x5A);
    st = dec_format_display(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_INEXACT) fail_status("disp_inexact", 0, st, DEC_INEXACT);
    checks++;
    if (!all_byte(buf, sizeof buf, 0x5A))
        fail_u64("disp_inexact_write", 0, "unchanged", 0, 1);
    checks++;
    if (n != 0) fail_u64("disp_inexact_len", 0, "len", n, 0);

    dec_set(&a, 0, 120, 0, -2, 1);      /* 1.20 at dscale 1 */
    st = dec_format_display(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_OK || !str_equal(buf, "1.2")) {
        fail_status("disp_redundant", 0, st, DEC_OK);
        fail_u64("disp_redundant_text", 0, "text", 0, 1);
    }

    /* A nonzero value may carry dscale 65535; display size is bounded. */
    dec_set(&a, 0, 1, 0, 0, 65535);
    st = dec_format_display_len(&a, &n);
    checks++;
    if (st != DEC_OK || n != 65537) {
        fail_status("disp_scale_max", 0, st, DEC_OK);
        fail_u64("disp_scale_max_len", 0, "len", n, 65537);
    }
    char one = 0x5A;
    st = dec_format_display(&a, &one, 1, &n);
    checks++;
    if (st != DEC_BUFFER_SMALL)
        fail_status("disp_scale_max_small", 0, st, DEC_BUFFER_SMALL);
    checks++;
    if (one != 0x5A)
        fail_u64("disp_scale_max_write", 0, "unchanged", 0, 1);

    /* 1e32767: fixed text exceeds the canonical threshold, so canonical uses
     * exact scientific notation while display size remains bounded. */
    dec_set(&a, 0, 1, 0, 32767, 0);
    st = dec_format_canonical(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_OK || !str_equal(buf, "1e32767")) {
        fail_status("canon_max_exp", 0, st, DEC_OK);
        fail_u64("canon_max_exp_text", 0, "text", 0, 1);
    }
    st = dec_format_display_len(&a, &n);
    checks++;
    if (st != DEC_OK || n != 32768) {
        fail_status("disp_max_exp", 0, st, DEC_OK);
        fail_u64("disp_max_exp_len", 0, "len", n, 32768);
    }

    /* Canonical threshold: 128 fixed bytes stays fixed; 129 falls back to
     * exact scientific. */
    dec_set(&a, 0, 1, 0, 127, 0);
    st = dec_format_canonical_len(&a, &n);
    checks++;
    if (st != DEC_OK || n != 128) {
        fail_status("canon_threshold_128", 0, st, DEC_OK);
        fail_u64("canon_threshold_128_len", 0, "len", n, 128);
    }
    dec_set(&a, 0, 1, 0, 128, 0);
    st = dec_format_canonical(&a, buf, sizeof buf, &n);
    checks++;
    if (st != DEC_OK || !str_equal(buf, "1e128")) {
        fail_status("canon_threshold_129", 0, st, DEC_OK);
        fail_u64("canon_threshold_129_text", 0, "text", 0, 1);
    }

    /* Maximum bounded display: 38 digits * 10^32767 with dscale 65535 and a
     * sign is 98342 bytes.  It is size-queried; no huge buffer is written. */
    dec_set(&a, 1, DEC_COEFF_MAX_LO, DEC_COEFF_MAX_HI, 32767, 65535);
    st = dec_format_display_len(&a, &n);
    checks++;
    if (st != DEC_OK || n != DEC_TEXT_MAX_OUTPUT) {
        fail_status("disp_max_bound", 0, st, DEC_OK);
        fail_u64("disp_max_bound_len", 0, "len", n, DEC_TEXT_MAX_OUTPUT);
    }
    char one2 = 0x5A;
    st = dec_format_display(&a, &one2, 1, &n);
    checks++;
    if (st != DEC_BUFFER_SMALL)
        fail_status("disp_max_bound_small", 0, st, DEC_BUFFER_SMALL);
    checks++;
    if (one2 != 0x5A)
        fail_u64("disp_max_bound_write", 0, "unchanged", 0, 1);
}

int dec24_text_test_main(void) {
    for (unsigned i = 0; i < DEC_TEXT_VECTORS_COUNT; i++)
        run_parse_vec(i, &dec_text_vectors[i]);
    for (unsigned i = 0; i < DEC_FORMAT_VECTORS_COUNT; i++)
        run_format_vec(i, &dec_format_vectors[i]);
    test_long_and_edge_inputs();
    test_canonical_and_display_edges();

    if (failures == 0) {
        write_str("PASS text checks=");
        write_u64(checks);
        write_str("\n");
        return 0;
    }
    write_str("FAIL text count=");
    write_u64((uint64_t)failures);
    write_str(" checks=");
    write_u64(checks);
    write_str("\n");
    return (failures > 255) ? 255 : failures;
}
