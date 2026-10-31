#include "scalar.h"

long zero(void) { return 0; }
int neg32(void) { return -123; }
long identity(long a) { return a; }
long add(long a, long b) { return a + b; }
long sum3(long a, long b, long c) { return a + b + c; }
long sum4(long a, long b, long c, long d) { return a + b + c + d; }
long sum5(long a, long b, long c, long d, long e) { return a + b + c + d + e; }
long sum6(long a, long b, long c, long d, long e, long f) {
    return a + b + c + d + e + f;
}
long sum7(long a, long b, long c, long d, long e, long f, long g) {
    return a + b + c + d + e + f + g;
}
long sum8(long a, long b, long c, long d, long e, long f, long g, long h) {
    return a + b + c + d + e + f + g + h;
}
