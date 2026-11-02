#include <stddef.h>
#include <string.h>
#include "nullconst_fixture.h"

long nc_cstr_null(const char *s) {
    return s == NULL ? 1 : 0;
}

long nc_struct_null(struct foo *f) {
    return f == NULL ? 1 : 0;
}

long nc_destructor(const char *s, void (*d)(void *)) {
    return (long)strlen(s) + (d == (void (*)(void *))-1 ? 100 : 0);
}

long nc_int_const(int v) {
    return v;
}

long nc_bytes_wrong(struct foo *p, int n) {
    (void)p;
    return n;
}

long nc_struct_null_bad(struct foo *f) {
    (void)f;
    return 0;
}

long nc_two(void *a, void *b) {
    return (a == NULL) * 10 + (b != NULL);
}

long nc_int_long(long x) {
    return x;
}

long nc_uint(unsigned int x) {
    return (long)x;
}
