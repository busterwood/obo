/* Native fixture for the stage (b) return kinds and the stage (c) typed native fn form. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "return_fixture.h"

static const char rf_one_data[] = {'A'};
static const char rf_nul_data[] = {'a', 0, 'b'};
static const char rf_over_data[] = "abcdefgh";
static const char rf_exact_data[] = {'x', 'y', 0, 'z'};
static const char rf_tail_data[] = {'o', 'k', 0, 'j', 'u', 'n', 'k'};
static int rf_anchor;
static long rf_last;

/* The thunk must enter every C call (both cbytes calls included) with a 16-byte aligned frame. A
 * misaligned value call returns NULL (so a positive length exits 11); a misaligned length call
 * returns a sentinel above the cap (so the wrapper sees nil and the test's output mismatches). */
static int rf_aligned(void) { return (uintptr_t)__builtin_frame_address(0) % 16 == 0; }

const char *rf_cb_empty(void) { return NULL; }
int rf_cb_empty_len(void) { return 0; }

const char *rf_cb_one(void) { return rf_one_data; }
int rf_cb_one_len(void) { return 1; }

const char *rf_cb_nul(void) { return rf_nul_data; }
int rf_cb_nul_len(void) { return 3; }

const char *rf_cb_null_pos(void) { return NULL; }
int rf_cb_null_pos_len(void) { return 5; }

const char *rf_cb_over(void) { return rf_over_data; }
int rf_cb_over_len(void) { return 9; }

const char *rf_cb_exact(void) { return rf_exact_data; }
int rf_cb_exact_len(void) { return 4; }

const char *rf_cb_many(const void *a, int n, const void *b, int m, int x, int y, int z, int w,
                       int v) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z; (void)w; (void)v;
    if (!rf_aligned()) return NULL;
    return "hi";
}

/* The length depends on the last (stack-passed) argument, so a second call with wrong arguments or
 * alignment would not return 2. */
int rf_cb_many_len(const void *a, int n, const void *b, int m, int x, int y, int z, int w, int v) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z; (void)w;
    if (!rf_aligned()) return 1000;
    return v == 9 ? 2 : 0;
}

static const char rf_even_data[] = {'a', 'b', 'c'};
const char *rf_cb_even(const void *a, int n, const void *b, int m, int x, int y, int z, int w) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z; (void)w;
    if (!rf_aligned()) return NULL;
    return rf_even_data;
}
int rf_cb_even_len(const void *a, int n, const void *b, int m, int x, int y, int z, int w) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z;
    if (!rf_aligned()) return 1000;
    return w == 8 ? 3 : 0;
}
long rf_cb_even_len_wrong(const void *a, int n, const void *b, int m, int x, int y, int z, int w) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z;
    return w == 8 ? 3 : 0;
}

void rf_take(long v) { rf_last = v; }
const void *rf_nz_i(long v) { return v == 7 ? (const void *)&rf_anchor : NULL; }

static const char rf_arg_data[] = {'o', 'k'};
const char *rf_cb_arg(long v) { return v == 7 ? rf_arg_data : NULL; }
int rf_cb_arg_len(long v) { return v == 7 ? 2 : 0; }
const char *rf_cs_arg(long v) { return v == 5 ? "arg" : ""; }

const char *rf_cb_neg(void) { return rf_over_data; }
int rf_cb_neg_len(void) { return -1; }

static const char rf_i64_data[] = {'w', 'x', 'y', 'z'};
const char *rf_cb_i64_many(long a, long b, long c, long d, long e, long f, long g, long h) {
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f;
    if (!rf_aligned()) return NULL;
    return (g == 7 && h == 9) ? rf_i64_data : NULL;
}
int rf_cb_i64_many_len(long a, long b, long c, long d, long e, long f, long g, long h) {
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f;
    if (!rf_aligned()) return 1000;
    return (g == 7 && h == 9) ? 4 : 0;
}

int rf_i32_neg(long v) { return v == 9 ? -5 : 0; }

const char *rf_cs_normal(void) { return "hello"; }
const char *rf_cs_empty(void) { return ""; }
const char *rf_cs_null(void) { return NULL; }
const char *rf_cs_tail(void) { return rf_tail_data; }

unsigned int rf_u32max(void) { return 4294967295U; }

void rf_setv(int v) { rf_last = v; }
long rf_lastv(void) { return rf_last; }

const void *rf_nz_null(void) { return NULL; }
const void *rf_nz_ptr(void) { return &rf_anchor; }

long rf_add(long a, long b) { return a + b; }

long rf_sum3(const int *v, int n, int bias) {
    long s = 0;
    for (int i = 0; i < n; i++) s += v[i];
    return s + bias;
}

long rf_nullconst(const char *p, int v) { return p == NULL ? v : -1; }

long rf_tagged(const char *s, int tag) { return tag + (long)strlen(s); }

long rf_bytes_arg(const void *p, int n) {
    (void)p;
    return (long)n;
}

long rf_prefix(const char *p, int v) { return p == NULL ? v : -1; }

long rf_lenfirst(int n, const int *v) {
    long s = 0;
    for (int i = 0; i < n; i++) s += v[i];
    return s;
}

/* 16 MiB, the cstr_out bound: one byte short of the cap plus a NUL is accepted; no NUL within the
 * bound exits 11. Allocated with libc so the fixture does not add a 16 MiB static slot to the
 * hosted image's data areas. */
#define RF_BIG (16u << 20)
static unsigned char *rf_big;

static unsigned char *rf_big_buffer(void) {
    if (rf_big == NULL) rf_big = (unsigned char *)malloc((size_t)RF_BIG);
    return rf_big;
}

const char *rf_cs_big_ok(void) {
    unsigned char *b = rf_big_buffer();
    if (b == NULL) return NULL;
    memset(b, 'x', (size_t)RF_BIG - 1);
    b[RF_BIG - 1] = 0;
    return (const char *)b;
}

const char *rf_cs_big_bad(void) {
    unsigned char *b = rf_big_buffer();
    if (b == NULL) return NULL;
    memset(b, 'x', (size_t)RF_BIG);
    return (const char *)b;
}
