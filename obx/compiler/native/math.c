/* The libm of the native runtime: obxprim.inc (the single instructions) and obxmath.inc (musl's
   src/math, MIT; see its header), the text the C backend compiles too, so that both give the
   same bits. The functions of std's libm externs are these wrappers; a program that calls them
   links no libm and no libc.

   gen_rtx.py compiles this file apart from rt.c, with -ffixed-xmm8 to -ffixed-xmm15: no code
   here touches xmm8-xmm15, and it calls nothing outside itself (gen_rtx.py checks that it has no
   undefined symbol). So the native backend keeps doubles in xmm8-xmm14 across calls of these
   functions (rtx.FP_KEEP lists them; design/native.md, section 4, "doubles in xmm registers"). */

#include "../obxprim.inc"
#include "../obxmath.inc"
/* the global names, aliases of the functions (a wrapper would add a jump to each call) */
#define M1(n) double n(double x) __attribute__((alias("obx_" #n)));
#define M2(n) double n(double x, double y) __attribute__((alias("obx_" #n)));
M1(sqrt) M1(fabs) M1(floor) M1(ceil) M1(trunc) M1(rint) M1(round)
M1(exp) M1(exp2) M1(log) M1(log2) M1(log10) M1(log1p) M1(expm1) M1(cbrt)
M1(sin) M1(cos) M1(tan) M1(asin) M1(acos) M1(atan) M1(sinh) M1(cosh) M1(tanh)
M2(pow) M2(atan2) M2(hypot) M2(fmod) M2(copysign)
double fma(double x, double y, double z) __attribute__((alias("obx_fma")));
