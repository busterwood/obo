/* mathulp.c: the largest error, in ULP, of obx's libm (compiler/obxprim.inc and obxmath.inc,
   included here as shipped) and of the C library's, against the 113-bit libquadmath result, over
   random inputs; and whether the special values (NaN, +-0, +-inf, subnormals, huge arguments,
   pow's edge cases) come out as the C library's do, bit for bit.

       gcc -O2 -o /tmp/mathulp obx/tools/mathulp.c -lm -lquadmath
       /tmp/mathulp [count]          # random inputs per function, default 1000000

   The ULP is |got - exact| / ulp(exact) for the double nearest the exact value, so a correctly
   rounded function scores at most 0.5. */
#include <math.h>
#include <quadmath.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../compiler/obxprim.inc"
#include "../compiler/obxmath.inc"
#define INFINITY __builtin_inf()
#define NAN __builtin_nan("")
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)

static uint64_t rs = 88172645463325252ULL;
static uint64_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static double unif(double lo, double hi) { return lo + (hi - lo) * ((rnd() >> 11) * (1.0 / 9007199254740992.0)); }
/* log-uniform magnitude in [lo, hi] (lo > 0) */
static double logu(double lo, double hi) { return exp(unif(log(lo), log(hi))); }
static uint64_t bits(double d) { uint64_t u; memcpy(&u, &d, 8); return u; }
static double dbl(uint64_t u) { double d; memcpy(&d, &u, 8); return d; }

typedef double (*f1)(double);
typedef double (*f2)(double, double);
typedef __float128 (*q1)(__float128);
typedef __float128 (*q2)(__float128, __float128);

static double ulps(double got, __float128 ex) {
    if (isnan(got) || isinf(got)) return isnan((double)ex) || isinf((double)ex) ? 0 : 1e30;
    double e = (double)ex;
    if (isinf(e)) return 1e30;
    double a = fabs(e), u = nextafter(a, INFINITY) - a;
    if (a == 0) u = 4.9406564584124654e-324;
    return (double)(fabsq((__float128)got - ex) / (__float128)u);
}

struct fn1 { const char* name; f1 ours; f1 libc; q1 ref; double lo, hi; int shape; };
/* shape: 0 uniform in [lo, hi], 1 log-uniform magnitude with a random sign, 2 log-uniform positive */
static void run1(const struct fn1* f, long n) {
    double mo = 0, ml = 0, wo = 0, wl = 0;
    long same = 0;
    for (long i = 0; i < n; i++) {
        double x;
        if (f->shape == 0) x = unif(f->lo, f->hi);
        else if (f->shape == 1) x = (rnd() & 1 ? -1 : 1) * logu(f->lo, f->hi);
        else x = logu(f->lo, f->hi);
        __float128 ex = f->ref((__float128)x);
        double go = f->ours(x), gl = f->libc(x);
        double uo = ulps(go, ex), ul = ulps(gl, ex);
        if (uo > mo) { mo = uo; wo = x; }
        if (ul > ml) { ml = ul; wl = x; }
        same += bits(go) == bits(gl);
    }
    printf("%-6s obx %.3f (at %.17g)   glibc %.3f (at %.17g)   same bits %.2f%%\n", f->name, mo, wo, ml, wl, 100.0 * same / n);
}

struct fn2 { const char* name; f2 ours; f2 libc; q2 ref; };
static void run2(const struct fn2* f, long n, int kind) {
    double mo = 0, ml = 0, wox = 0, woy = 0;
    long same = 0;
    for (long i = 0; i < n; i++) {
        double x, y;
        if (kind == 0) { x = logu(1e-3, 1e3); y = unif(-60, 60); if (i % 5 == 0) y = unif(-1, 1); }
        else if (kind == 1) { x = unif(-1e3, 1e3); y = unif(-1e3, 1e3); }
        else { x = logu(1e-100, 1e100) * (rnd() & 1 ? -1 : 1); y = logu(1e-100, 1e100) * (rnd() & 1 ? -1 : 1); }
        __float128 ex = f->ref((__float128)x, (__float128)y);
        double go = f->ours(x, y), gl = f->libc(x, y);
        double uo = ulps(go, ex), ul = ulps(gl, ex);
        if (uo > mo) { mo = uo; wox = x; woy = y; }
        if (ul > ml) ml = ul;
        same += bits(go) == bits(gl);
    }
    printf("%-6s obx %.3f (at %.17g, %.17g)   glibc %.3f   same bits %.2f%%\n", f->name, mo, wox, woy, ml, 100.0 * same / n);
}

#define W1(n) static double o_##n(double x) { return obx_##n(x); } static double c_##n(double x) { return n(x); }
W1(exp) W1(exp2) W1(log) W1(log2) W1(log10) W1(log1p) W1(expm1) W1(cbrt) W1(sin) W1(cos) W1(tan) W1(asin) W1(acos) W1(atan)
W1(sinh) W1(cosh) W1(tanh) W1(sqrt) W1(floor) W1(ceil) W1(trunc) W1(rint) W1(round)
static double o_pow(double x, double y) { return obx_pow(x, y); } static double c_pow(double x, double y) { return pow(x, y); }
static double o_atan2(double x, double y) { return obx_atan2(x, y); } static double c_atan2(double x, double y) { return atan2(x, y); }
static double o_hypot(double x, double y) { return obx_hypot(x, y); } static double c_hypot(double x, double y) { return hypot(x, y); }
static double o_fmod(double x, double y) { return obx_fmod(x, y); } static double c_fmod(double x, double y) { return fmod(x, y); }
static __float128 q_cbrt(__float128 x) { return cbrtq(x); }
static __float128 q_pow(__float128 x, __float128 y) { return powq(x, y); }
static __float128 q_atan2(__float128 x, __float128 y) { return atan2q(x, y); }
static __float128 q_hypot(__float128 x, __float128 y) { return hypotq(x, y); }
static __float128 q_fmod(__float128 x, __float128 y) { return fmodq(x, y); }

/* special values: the same bits as the C library, except that a NaN may be any NaN */
static long bad = 0, tried = 0, lastbit = 0;
/* a difference that is not a finite neighbour of the other result: a NaN, an infinity, a zero or a sign differs */
static int classdiff(double a, double b) {
    if (isnan(a) || isnan(b)) return isnan(a) != isnan(b);
    if (isinf(a) || isinf(b) || a == 0 || b == 0) return bits(a) != bits(b);
    return (a < 0) != (b < 0) || fabs(a - b) > 4 * (nextafter(fabs(b), INFINITY) - fabs(b));
}
static void chk1(const char* name, f1 o, f1 c, double x) {
    double a = o(x), b = c(x);
    tried++;
    if (bits(a) != bits(b) && !(isnan(a) && isnan(b))) {
        if (classdiff(a, b)) { bad++; printf("  SPECIAL %s(%a): obx %a, glibc %a\n", name, x, a, b); } else lastbit++;
    }
}
static void chk2(const char* name, f2 o, f2 c, double x, double y) {
    double a = o(x, y), b = c(x, y);
    tried++;
    if (bits(a) != bits(b) && !(isnan(a) && isnan(b))) {
        if (classdiff(a, b)) { bad++; printf("  SPECIAL %s(%a, %a): obx %a, glibc %a\n", name, x, y, a, b); } else lastbit++;
    }
}

int main(int argc, char** argv) {
    long n = argc > 1 ? atol(argv[1]) : 1000000;
    const struct fn1 t1[] = {
        {"exp", o_exp, c_exp, expq, -745, 709.78, 0},
        {"exp2", o_exp2, c_exp2, exp2q, -1074, 1023.99, 0},
        {"expm1", o_expm1, c_expm1, expm1q, -40, 709.78, 0},
        {"log", o_log, c_log, logq, 4.9e-324, 1.79e308, 2},
        {"log2", o_log2, c_log2, log2q, 4.9e-324, 1.79e308, 2},
        {"log10", o_log10, c_log10, log10q, 4.9e-324, 1.79e308, 2},
        {"log1p", o_log1p, c_log1p, log1pq, -0.999, 1e10, 0},
        {"cbrt", o_cbrt, c_cbrt, q_cbrt, 4.9e-324, 1.79e308, 1},
        {"sin", o_sin, c_sin, sinq, -100, 100, 0},
        {"sin*", o_sin, c_sin, sinq, 1e-3, 1e22, 1},
        {"cos", o_cos, c_cos, cosq, -100, 100, 0},
        {"cos*", o_cos, c_cos, cosq, 1e-3, 1e22, 1},
        {"tan", o_tan, c_tan, tanq, -100, 100, 0},
        {"tan*", o_tan, c_tan, tanq, 1e-3, 1e22, 1},
        {"asin", o_asin, c_asin, asinq, -1, 1, 0},
        {"acos", o_acos, c_acos, acosq, -1, 1, 0},
        {"atan", o_atan, c_atan, atanq, 1e-10, 1e10, 1},
        {"sinh", o_sinh, c_sinh, sinhq, -710, 710, 0},
        {"cosh", o_cosh, c_cosh, coshq, -710, 710, 0},
        {"tanh", o_tanh, c_tanh, tanhq, -20, 20, 0},
    };
    printf("max ULP over %ld random inputs per function (sin*, cos*, tan*: magnitudes 1e-3 to 1e22)\n", n);
    for (unsigned i = 0; i < sizeof t1 / sizeof t1[0]; i++) run1(&t1[i], n);
    const struct fn2 t2[] = {{"pow", o_pow, c_pow, q_pow}, {"atan2", o_atan2, c_atan2, q_atan2}, {"hypot", o_hypot, c_hypot, q_hypot}, {"fmod", o_fmod, c_fmod, q_fmod}};
    run2(&t2[0], n, 0);
    run2(&t2[1], n, 1);
    run2(&t2[2], n, 2);
    {   /* fmod is exact */
        long diff = 0;
        for (long i = 0; i < n; i++) {
            double x = logu(1e-300, 1e300) * (rnd() & 1 ? -1 : 1), y = logu(1e-300, 1e300);
            diff += bits(obx_fmod(x, y)) != bits(fmod(x, y));
        }
        printf("fmod   differs from glibc in %ld of %ld (exact: must be 0)\n", diff, n);
    }
    {   /* the single instructions: bit for bit with glibc, over random bit patterns */
        long diff = 0;
        for (long i = 0; i < n * 4; i++) {
            double x = dbl(rnd());
            if (i % 4 == 1) x = unif(-1e6, 1e6);
            if (i % 4 == 2) x = (double)(long)unif(-1e6, 1e6) + 0.5;
            if (i % 4 == 3) x = unif(-4.5e15, 4.5e15);
            if (!isnan(x)) {
                diff += bits(obx_sqrt(x)) != bits(sqrt(x)) && !isnan(sqrt(x));
                diff += bits(obx_floor(x)) != bits(floor(x));
                diff += bits(obx_ceil(x)) != bits(ceil(x));
                diff += bits(obx_trunc(x)) != bits(trunc(x));
                diff += bits(obx_rint(x)) != bits(rint(x));
                diff += bits(obx_round(x)) != bits(round(x));
                diff += bits(obx_fabs(x)) != bits(fabs(x));
                diff += bits(obx_copysign(x, -1.5)) != bits(copysign(x, -1.5));
                diff += bits(obx_fma(x, 1.1, 3.0)) != bits(fma(x, 1.1, 3.0));
            }
        }
        printf("sqrt floor ceil trunc rint round fabs copysign fma: %ld differences from glibc in %ld inputs\n", diff, n * 4);
    }
    const double sp[] = {0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, 0.1, INFINITY, -INFINITY, NAN, 4.9406564584124654e-324, -4.9406564584124654e-324,
        2.2250738585072009e-308, 2.2250738585072014e-308, 1.7976931348623157e308, -1.7976931348623157e308, 709.782712893384, 709.79, -745.13, -745.14, 1e22, 1e300, 9.007199254740992e15,
        6.283185307179586, 1.5707963267948966, 3.141592653589793, 1e-300, 2.5, 3.5, -2.5, 710.5, -37, 38, 1024, -1075, 0.9999999999999999, 1.0000000000000002};
    const int ns = sizeof sp / sizeof sp[0];
    for (int i = 0; i < ns; i++) {
        double x = sp[i];
#define S1(n) chk1(#n, o_##n, c_##n, x);
        S1(exp) S1(exp2) S1(expm1) S1(log) S1(log2) S1(log10) S1(log1p) S1(cbrt) S1(sin) S1(cos) S1(tan) S1(asin) S1(acos) S1(atan)
        S1(sinh) S1(cosh) S1(tanh) S1(sqrt) S1(floor) S1(ceil) S1(trunc) S1(rint) S1(round)
        for (int j = 0; j < ns; j++) {
            double y = sp[j];
            chk2("pow", o_pow, c_pow, x, y); chk2("atan2", o_atan2, c_atan2, x, y); chk2("hypot", o_hypot, c_hypot, x, y); chk2("fmod", o_fmod, c_fmod, x, y);
        }
    }
    printf("special values: %ld results of %ld differ from glibc in kind (NaN, inf, zero, sign, more than a few ULP); %ld differ in the last bits\n", bad, tried, lastbit);
    return 0;
}
