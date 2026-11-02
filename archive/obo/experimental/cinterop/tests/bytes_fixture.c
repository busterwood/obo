/* Native fixture for the stage (a) argument kinds. Each function checks one
 * contract of the kinds thunk; the obo wrapper names them in its descriptors. */
#include <stdint.h>
#include <string.h>
#include "bytes_fixture.h"

/* Provided by the hosted runtime (host.c), linked into the same executable. */
long obo_ci_scratch_live(void);

static uint64_t fnv1a(const unsigned char *p, int n) {
    uint64_t h = 1469598103934665603ULL;
    for (int i = 0; i < n; i++) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }
    return h;
}

/* A non-negative result, so obo's signed int holds it exactly. */
long bytes_hash(const void *p, int n) {
    return (long)(fnv1a((const unsigned char *)p, n) & 0x7fffffffffffffffULL);
}

long str_hash(const void *p, int n) {
    return bytes_hash(p, n);
}

/* 1 if the pointer is not NULL, plus twice the length: a nil string or an
 * empty array must give 1 (non-NULL, length 0). */
long bytes_probe(const void *p, int n) {
    return (p != NULL ? 1 : 0) + 2L * (long)n;
}

long str_probe(const void *p, int n) {
    return bytes_probe(p, n);
}

long cstr_len(const char *s) {
    return (long)strlen(s);
}

static void *last_const;

/* Records the pointer it was given; 1 when it is the const:-1 value. */
long rec_const(void *p) {
    last_const = p;
    return p == (void *)-1 ? 1 : 0;
}

long ui_echo(unsigned int v) {
    return (long)v;
}

long i_echo(int v) {
    return (long)v;
}

long ints_sum(const int *v, int n) {
    long s = 0;
    for (int i = 0; i < n; i++) s += v[i];
    return s;
}

long ints_u_last(const unsigned int *v, int n) {
    return n > 0 ? (long)v[n - 1] : -1L;
}

/* Each element: NULL codes 1, otherwise 2 + its length. Folded in base 16. */
long list_probe(const char *const *p, const int *l, int n) {
    long acc = 0;
    for (int i = 0; i < n; i++) {
        long code = p[i] == NULL ? 1 : 2 + (long)l[i];
        acc = acc * 16 + code;
    }
    return acc;
}

/* The native sees the array as it is at the call: n is its length. */
long timing_probe(const void *p, int n, long g) {
    (void)p;
    return (long)n * 1000 + g;
}

long scratch_live(void) {
    return obo_ci_scratch_live();
}
