#include <stdlib.h>
#include <string.h>
#include "out_fixture.h"

struct Of {
    int v;
    unsigned char bytes[32];
    int n;
};

static long of_live_count;
static long of_closed_count;
static long of_dtor_word;
static long of_last_length;

static Of *of_make(int v) {
    Of *o = (Of *)malloc(sizeof(Of));
    if (!o) return 0;
    o->v = v;
    o->n = 0;
    of_live_count += 1;
    return o;
}

void of_reset(void) {
    of_live_count = 0;
    of_closed_count = 0;
    of_dtor_word = 0;
    of_last_length = 0;
}

long of_live(void) { return of_live_count; }
long of_closed(void) { return of_closed_count; }
long of_last_dtor(void) { return of_dtor_word; }
long of_last_len(void) { return of_last_length; }

int of_new(int v, Of **out) {
    *out = of_make(v);
    return *out ? 0 : 1;
}

int of_zero(Of **out) {
    *out = of_make(0);
    return *out ? 0 : 1;
}

int of_new_fail(int v, Of **out) {
    *out = of_make(v);
    return 3;
}

int of_new_null(int v, Of **out) {
    (void)v;
    *out = 0;
    return 4;
}

int of_wide(int a, int b, int c, int d, int e, int f, Of **out) {
    *out = of_make(a + b + c + d + e + f);
    return 0;
}

int of_open(const char *path, Of **out, int flags, const char *unused) {
    (void)path;
    (void)unused;
    *out = of_make(flags);
    return 0;
}

int of_prepare(Of *db, const void *sql, int n, Of **st, const char **tail) {
    const char *s = (const char *)sql;
    *st = of_make(db ? db->v : -1);
    if (n >= 2) {
        *tail = s + 2;
    } else {
        *tail = s + n;
    }
    return 0;
}

int of_tail(const void *s, int n, int mode, const char **tail) {
    const char *p = (const char *)s;
    if (mode == 0) *tail = p + 2;
    else if (mode == 1) *tail = p;
    else if (mode == 2) *tail = p + n;
    else if (mode == 3) *tail = 0;
    else if (mode == 4) *tail = p - 1;
    else *tail = p + n + 1;
    return 0;
}

int of_bind(const void *s, int n, void (*dtor)(void *), Of **out) {
    Of *o = of_make(n);
    if (!o) { *out = 0; return 1; }
    of_dtor_word = (long)(size_t)dtor;
    of_last_length = n;
    if (n > (int)sizeof(o->bytes)) n = (int)sizeof(o->bytes);
    if (n > 0) memcpy(o->bytes, s, (size_t)n);
    o->n = n;
    *out = o;
    return 0;
}

int of_get(const Of *o) { return o ? o->v : -1; }

void of_release(Of *o) {
    if (!o) return;
    of_closed_count += 1;
    of_live_count -= 1;
    free(o);
}
