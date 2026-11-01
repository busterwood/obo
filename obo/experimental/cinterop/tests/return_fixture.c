/* Native fixture for the stage (b) return kinds and the stage (c) typed native fn form. */
#include <stdint.h>
#include <string.h>

#include "return_fixture.h"

static const char rf_one_data[] = {'A'};
static const char rf_nul_data[] = {'a', 0, 'b'};
static const char rf_over_data[] = "abcdefgh";
static const char rf_exact_data[] = {'x', 'y', 0, 'z'};
static const char rf_tail_data[] = {'o', 'k', 0, 'j', 'u', 'n', 'k'};
static int rf_anchor;
static long rf_last;

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
    return "hi";
}

/* The length depends on the last (stack-passed) argument, so a second call with wrong arguments or
 * alignment would not return 2. */
int rf_cb_many_len(const void *a, int n, const void *b, int m, int x, int y, int z, int w, int v) {
    (void)a; (void)n; (void)b; (void)m; (void)x; (void)y; (void)z; (void)w;
    return v == 9 ? 2 : 0;
}

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
