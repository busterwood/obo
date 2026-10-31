/* P0 bridge microbenchmark: scalar byte ops versus packed 7-byte groups.
 * This times the exact C adapter path, excluding obo call overhead. Each figure
 * is the median of three runs after one warmup; it is indicative, not a
 * portable throughput promise. */
#define _POSIX_C_SOURCE 200809L
#include "sysadapter.h"
#include "sysregistry.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define N (1024L * 1024L)
#define REPS 3

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

static double median3(double a, double b, double c) {
    double lo = a < b ? a : b;
    double hi = a > b ? a : b;
    if (c < lo) return lo;
    if (c > hi) return hi;
    return c;
}

static double scalar_in(long t, const unsigned char *src) {
    long i;
    double a = now_seconds();
    for (i = 0; i < N; i++) {
        if (sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET, i, src[i], 0) != 1) {
            return -1.0;
        }
    }
    return now_seconds() - a;
}

static double packed_in(long t, const unsigned char *src) {
    long i;
    double a = now_seconds();
    for (i = 0; i < N; ) {
        long count = N - i;
        long packed;
        if (count > 7) count = 7;
        packed = pack7(src + i, count);
        if (sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, i, packed, count) != count) {
            return -1.0;
        }
        i += count;
    }
    return now_seconds() - a;
}

static double scalar_out(long t, long *sum) {
    long i;
    double a = now_seconds();
    *sum = 0;
    for (i = 0; i < N; i++) {
        long v = sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET, i, 0, 0);
        if (v < 0) return -1.0;
        *sum = (*sum + v) % 1000000007L;
    }
    return now_seconds() - a;
}

static double packed_out(long t, unsigned char *dst, long *sum) {
    long i;
    double a = now_seconds();
    *sum = 0;
    for (i = 0; i < N; ) {
        long count = N - i;
        long packed;
        long j;
        if (count > 7) count = 7;
        packed = sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET_PACKED, i, 0, count);
        if (packed < 0) return -1.0;
        for (j = 0; j < count; j++) {
            dst[i + j] = (unsigned char)((packed >> (8 * j)) & 0xff);
            *sum = (*sum + dst[i + j]) % 1000000007L;
        }
        i += count;
    }
    return now_seconds() - a;
}

static double time3(double (*fn_in)(long, const unsigned char *),
                    long t, const unsigned char *src) {
    double d[REPS];
    int r;
    if (fn_in(t, src) < 0) return -1.0;   /* warmup */
    for (r = 0; r < REPS; r++) d[r] = fn_in(t, src);
    if (d[0] < 0 || d[1] < 0 || d[2] < 0) return -1.0;
    return median3(d[0], d[1], d[2]);
}

static double time3_out(double (*fn)(long, long *), long t) {
    double d[REPS];
    long sum;
    int r;
    if (fn(t, &sum) < 0) return -1.0;     /* warmup */
    for (r = 0; r < REPS; r++) d[r] = fn(t, &sum);
    if (d[0] < 0 || d[1] < 0 || d[2] < 0) return -1.0;
    return median3(d[0], d[1], d[2]);
}

static double time3_packed_out(long t, unsigned char *dst) {
    double d[REPS];
    long sum;
    int r;
    if (packed_out(t, dst, &sum) < 0) return -1.0;   /* warmup */
    for (r = 0; r < REPS; r++) d[r] = packed_out(t, dst, &sum);
    if (d[0] < 0 || d[1] < 0 || d[2] < 0) return -1.0;
    return median3(d[0], d[1], d[2]);
}

int main(void) {
    long t;
    unsigned char *src;
    unsigned char *dst;
    long i;
    double ds, dp, dso, dpo;
    long sum_scalar = 0;
    long sum_packed = 0;

    src = (unsigned char *)malloc((size_t)N);
    dst = (unsigned char *)malloc((size_t)N);
    if (src == NULL || dst == NULL) return 2;
    for (i = 0; i < N; i++) src[i] = (unsigned char)(i % 251);

    if (sys_adapter_init(8, 2L * N) != 0) return 2;
    t = sys_res_new(SYS_KIND_BYTE_BUFFER, N, 0);
    if (t <= 0) return 2;

    ds = time3(scalar_in, t, src);
    dp = time3(packed_in, t, src);
    dso = time3_out(scalar_out, t);
    dpo = time3_packed_out(t, dst);
    if (ds < 0 || dp < 0 || dso < 0 || dpo < 0) return 3;

    if (scalar_out(t, &sum_scalar) < 0) return 4;
    if (packed_out(t, dst, &sum_packed) < 0) return 5;
    if (sum_scalar != sum_packed) {
        fprintf(stderr, "benchmark checksum mismatch\n");
        return 7;
    }

    printf("bytes=%ld reps=%d median scalar_in=%.1f MiB/s packed_in=%.1f MiB/s scalar_out=%.1f MiB/s packed_out=%.1f MiB/s sums=%ld,%ld\n",
           N, REPS,
           (double)N / ds / (1024.0 * 1024.0),
           (double)N / dp / (1024.0 * 1024.0),
           (double)N / dso / (1024.0 * 1024.0),
           (double)N / dpo / (1024.0 * 1024.0),
           sum_scalar, sum_packed);

    if (sys_res_close(t, SYS_KIND_BYTE_BUFFER) != 1) return 8;
    if (sys_adapter_shutdown() != 0) return 9;
    free(src);
    free(dst);
    return 0;
}
