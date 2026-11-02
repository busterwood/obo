#include <stdint.h>
#include <stdio.h>
#include "stack_args_fixture.h"

static void ints_out(const int *v, int n) {
    printf("[");
    for (int i = 0; i < n; i++) printf("%s%d", i ? "," : "", v[i]);
    printf("]");
}

static void list_out(const char *const *p, const int *l, int n) {
    printf("[");
    for (int i = 0; i < n; i++) {
        if (i) printf(",");
        if (p[i] == NULL) { printf("nil"); continue; }
        printf("%d:", l[i]);
        for (int j = 0; j < l[i]; j++) printf("%02x", (unsigned char)p[i][j]);
    }
    printf("]");
}

static void bytes_out(const void *p, int n) {
    printf("%s%d:", p ? "" : "NULL", n);
    for (int j = 0; j < n; j++) printf("%02x", ((const unsigned char *)p)[j]);
}

static int aligned(void) { return (uintptr_t)__builtin_frame_address(0) % 16 == 0; }

long f7(long a, const char *s, const int *iv, int n, const char *const *ptrs, const int *lens, int m) {
    printf("f7 al=%d a=%ld s=%s iv=", aligned(), a, s); ints_out(iv, n);
    printf(" m=%d l=", m); list_out(ptrs, lens, m); printf("\n"); fflush(stdout);
    return 7;
}
long f8(long a, const char *s, const int *iv, int n, const char *const *ptrs, const int *lens, int m, int x) {
    printf("f8 al=%d a=%ld s=%s iv=", aligned(), a, s); ints_out(iv, n);
    printf(" m=%d l=", m); list_out(ptrs, lens, m); printf(" x=%d\n", x); fflush(stdout);
    return 8;
}
long f9(long a, const char *s, const int *iv, int n, const char *const *ptrs, const int *lens, int m, const void *bp, int bn) {
    printf("f9 al=%d a=%ld s=%s iv=", aligned(), a, s); ints_out(iv, n);
    printf(" m=%d l=", m); list_out(ptrs, lens, m); printf(" b="); bytes_out(bp, bn); printf("\n"); fflush(stdout);
    return 9;
}
long f10(long a, const char *s, const int *iv, int n, const char *const *ptrs, const int *lens, int m, const void *bp, int bn, int x) {
    printf("f10 al=%d a=%ld s=%s iv=", aligned(), a, s); ints_out(iv, n);
    printf(" m=%d l=", m); list_out(ptrs, lens, m); printf(" b="); bytes_out(bp, bn); printf(" x=%d\n", x); fflush(stdout);
    return 10;
}
long f11(long a, const char *s, const int *iv, int n, const char *const *ptrs, const int *lens, int m, const void *bp, int bn, int x, unsigned int u) {
    printf("f11 al=%d a=%ld s=%s iv=", aligned(), a, s); ints_out(iv, n);
    printf(" m=%d l=", m); list_out(ptrs, lens, m); printf(" b="); bytes_out(bp, bn); printf(" x=%d u=%u\n", x, u); fflush(stdout);
    return 11;
}
