/* P0 bridge microbenchmark: scalar byte ops versus packed 7-byte groups.
 * This times the exact C adapter path, excluding obo call overhead. */
#define _POSIX_C_SOURCE 200809L
#include "sysadapter.h"
#include "sysregistry.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define N (1024L * 1024L)

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static long pack7(const unsigned char *p, long count) {
    long v = 0;
    long i;
    for (i = 0; i < count; i++) v |= ((long)p[i]) << (8 * i);
    return v;
}

int main(void) {
    long t;
    unsigned char *src;
    unsigned char *dst;
    long i;
    double a, b;
    double scalar_in, packed_in, scalar_out, packed_out;
    long sum_scalar = 0;
    long sum_packed = 0;

    src = (unsigned char *)malloc((size_t)N);
    dst = (unsigned char *)malloc((size_t)N);
    if (src == NULL || dst == NULL) return 2;
    for (i = 0; i < N; i++) src[i] = (unsigned char)(i % 251);

    if (sys_adapter_init(8, 2L * N) != 0) return 2;
    t = sys_res_new(SYS_KIND_BYTE_BUFFER, N, 0);
    if (t <= 0) return 2;

    a = now_seconds();
    for (i = 0; i < N; i++) {
        if (sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET, i, src[i], 0) != 1) return 3;
    }
    b = now_seconds();
    scalar_in = (double)N / (b - a) / (1024.0 * 1024.0);

    a = now_seconds();
    for (i = 0; i < N; ) {
        long count = N - i;
        long packed;
        if (count > 7) count = 7;
        packed = pack7(src + i, count);
        if (sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, i, packed, count) != count) return 4;
        i += count;
    }
    b = now_seconds();
    packed_in = (double)N / (b - a) / (1024.0 * 1024.0);

    a = now_seconds();
    for (i = 0; i < N; i++) {
        long v = sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET, i, 0, 0);
        if (v < 0) return 5;
        sum_scalar = (sum_scalar + v) % 1000000007L;
    }
    b = now_seconds();
    scalar_out = (double)N / (b - a) / (1024.0 * 1024.0);

    a = now_seconds();
    for (i = 0; i < N; ) {
        long count = N - i;
        long packed;
        long j;
        if (count > 7) count = 7;
        packed = sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET_PACKED, i, 0, count);
        if (packed < 0) return 6;
        for (j = 0; j < count; j++) {
            dst[i + j] = (unsigned char)((packed >> (8 * j)) & 0xff);
            sum_packed = (sum_packed + dst[i + j]) % 1000000007L;
        }
        i += count;
    }
    b = now_seconds();
    packed_out = (double)N / (b - a) / (1024.0 * 1024.0);

    printf("bytes=%ld scalar_in=%.1f MiB/s packed_in=%.1f MiB/s scalar_out=%.1f MiB/s packed_out=%.1f MiB/s sums=%ld,%ld\n",
           N, scalar_in, packed_in, scalar_out, packed_out, sum_scalar, sum_packed);
    if (sum_scalar != sum_packed) {
        fprintf(stderr, "benchmark checksum mismatch\n");
        return 7;
    }

    if (sys_res_close(t, SYS_KIND_BYTE_BUFFER) != 1) return 8;
    if (sys_adapter_shutdown() != 0) return 9;
    free(src);
    free(dst);
    return 0;
}
