/* The obx runtime, included at the top of every generated C file. */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <sys/resource.h>

typedef struct { int64_t rc; int64_t len; char data[]; } obx_str;
typedef struct { void* data; int64_t len, cap; } obx_arr;
typedef __int128 i128;
typedef unsigned __int128 u128;

static int64_t rt_live;
static int rt_argc;
static char** rt_argv;
static char rt_obuf[1 << 16];
static size_t rt_olen;

static void rt_flush(void) {
    size_t off = 0;
    while (off < rt_olen) {
        ssize_t n = write(1, rt_obuf + off, rt_olen - off);
        if (n <= 0) break;
        off += (size_t)n;
    }
    rt_olen = 0;
}

__attribute__((noreturn)) static void rt_trap(int code, const char* msg, const char* where) {
    rt_flush();
    fprintf(stderr, "obx: %s at %s\n", msg, where);
    exit(code);
}

static void* rt_alloc(size_t n) {
    void* p = calloc(1, n ? n : 1);
    if (!p) rt_trap(2, "out of memory", "runtime");
    rt_live++;
    return p;
}
static void rt_free(void* p) { free(p); rt_live--; }

/* Drop actions: drop glue pushes the parts of a value instead of recursing (SPEC 9.4). */
typedef void (*rt_dfn)(void*);
typedef struct { rt_dfn f; void* p; } rt_dact;
static rt_dact* rt_ds;
static int64_t rt_dn, rt_dcap;
static void rt_dpush(rt_dfn f, void* p) {
    if (rt_dn == rt_dcap) {
        rt_dcap = rt_dcap ? rt_dcap * 2 : 256;
        rt_ds = realloc(rt_ds, (size_t)rt_dcap * sizeof *rt_ds);
        if (!rt_ds) rt_trap(2, "out of memory", "runtime");
    }
    rt_ds[rt_dn].f = f; rt_ds[rt_dn++].p = p;
}
static void rt_drun(rt_dfn f, void* p) {
    int64_t base = rt_dn;
    f(p);
    while (rt_dn > base) { rt_dact a = rt_ds[--rt_dn]; a.f(a.p); }
}
static void rt_free_act(void* p) { rt_free(p); }

/* Stack overflow (SPEC 14, exit 4): a SIGSEGV near the stack, caught on an alternate stack. */
static char* rt_stack_top;
static size_t rt_stack_max;
static void rt_segv(int sig, siginfo_t* si, void* uc) {
    char* a = si->si_addr;
    (void)uc;
    if (a <= rt_stack_top && (size_t)(rt_stack_top - a) <= rt_stack_max + (1 << 20)) {
        static const char m[] = "obx: stack overflow\n";
        rt_flush();
        if (write(2, m, sizeof m - 1)) {}
        _exit(4);
    }
    signal(sig, SIG_DFL);           /* any other fault: the default action when it repeats */
}
static void rt_init(int argc, char** argv) {
    static char alt[1 << 16];
    stack_t ss = {0};
    struct sigaction sa;
    struct rlimit rl;
    rt_argc = argc; rt_argv = argv;
    rt_stack_top = __builtin_frame_address(0);
    rt_stack_max = getrlimit(RLIMIT_STACK, &rl) == 0 && rl.rlim_cur != RLIM_INFINITY ? rl.rlim_cur : (size_t)1 << 30;
    ss.ss_sp = alt; ss.ss_size = sizeof alt;
    sigaltstack(&ss, 0);
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = rt_segv;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, 0);
}
static void rt_finish(void) {
    rt_flush();
    const char* c = getenv("OBX_CHECK");
    if (c && c[0] == '1' && rt_live != 0) {
        fprintf(stderr, "obx: leak: %lld live objects at exit at runtime\n", (long long)rt_live);
        exit(5);
    }
}
__attribute__((noreturn)) static void rt_exit(int64_t code) { rt_flush(); exit((int)code); }

/* ---- strings */

static obx_str* rt_str_new(int64_t len) {
    obx_str* s = rt_alloc(sizeof(obx_str) + (size_t)len + 1);
    s->rc = 1; s->len = len;
    return s;
}
static obx_str* rt_str_of(const char* p, int64_t len) {
    obx_str* s = rt_str_new(len);
    memcpy(s->data, p, (size_t)len);
    return s;
}
static obx_str* rt_str_dup(obx_str* s) { if (s->rc > 0) s->rc++; return s; }
static void rt_str_drop(obx_str* s) { if (s->rc > 0 && --s->rc == 0) rt_free(s); }
static bool rt_str_eq(obx_str* a, obx_str* b) {
    return a->len == b->len && memcmp(a->data, b->data, (size_t)a->len) == 0;
}
static int rt_str_cmp(obx_str* a, obx_str* b) {
    int64_t n = a->len < b->len ? a->len : b->len;
    int c = memcmp(a->data, b->data, (size_t)n);
    if (c) return c < 0 ? -1 : 1;
    return a->len < b->len ? -1 : a->len > b->len;
}
static obx_str* rt_str_concat(obx_str* a, obx_str* b) {
    obx_str* s = rt_str_new(a->len + b->len);
    memcpy(s->data, a->data, (size_t)a->len);
    memcpy(s->data + a->len, b->data, (size_t)b->len);
    return s;
}
static uint8_t rt_str_byte(obx_str* s, int64_t i, const char* w) {
    if (i < 0 || i >= s->len) rt_trap(3, "string index out of range", w);
    return (uint8_t)s->data[i];
}
static obx_str* rt_str_slice(obx_str* s, int64_t a, int64_t b, const char* w) {
    if (a < 0 || b < a || b > s->len) rt_trap(3, "string slice out of range", w);
    return rt_str_of(s->data + a, b - a);
}
static obx_str* rt_str_from_bytes(obx_arr* b) { return rt_str_of(b->data, b->len); }
static obx_str* rt_str_from_c(void* p) {
    if (!p) return rt_str_new(0);
    return rt_str_of(p, (int64_t)strlen(p));
}
static const char* rt_cstr(obx_str* s, const char* w) {
    if (memchr(s->data, 0, (size_t)s->len)) rt_trap(12, "a string passed to C contains a NUL byte", w);
    return s->data;
}
static obx_str* rt_u128_to_str(u128 v) {
    char buf[48]; int n = 48;
    do { buf[--n] = (char)('0' + (int)(v % 10)); v /= 10; } while (v);
    return rt_str_of(buf + n, 48 - n);
}
static obx_str* rt_i128_to_str(i128 v) {
    if (v >= 0) return rt_u128_to_str((u128)v);
    obx_str* m = rt_u128_to_str((u128)0 - (u128)v);
    obx_str* s = rt_str_new(m->len + 1);
    s->data[0] = '-';
    memcpy(s->data + 1, m->data, (size_t)m->len);
    rt_str_drop(m);
    return s;
}
static obx_str* rt_int_to_str(int64_t v) { return rt_i128_to_str(v); }
static obx_str* rt_u64_to_str(uint64_t v) { return rt_u128_to_str(v); }
static obx_str* rt_float_to_str(double d) {
    char buf[64];
    for (int p = 15; p <= 17; p++) {
        snprintf(buf, sizeof buf, "%.*g", p, d);
        if (strtod(buf, 0) == d || d != d) break;
    }
    return rt_str_of(buf, (int64_t)strlen(buf));
}
static bool rt_parse_float(obx_str* s, double* out) {
    if (s->len == 0 || s->len > 400 || memchr(s->data, 0, (size_t)s->len)) return false;
    char* end;
    errno = 0;
    double d = strtod(s->data, &end);
    if (end != s->data + s->len) return false;
    *out = d;
    return true;
}
static int64_t rt_write_fd(int64_t fd, obx_str* s) {
    if (fd == 1) {
        if (rt_olen + (size_t)s->len > sizeof rt_obuf) rt_flush();
        if ((size_t)s->len <= sizeof rt_obuf) {
            memcpy(rt_obuf + rt_olen, s->data, (size_t)s->len);
            rt_olen += (size_t)s->len;
            return s->len;
        }
    }
    rt_flush();
    int64_t off = 0;
    while (off < s->len) {
        ssize_t n = write((int)fd, s->data + off, (size_t)(s->len - off));
        if (n <= 0) return -1;
        off += n;
    }
    return off;
}
static int64_t rt_read_file(obx_str* path, obx_str** out) {
    if (memchr(path->data, 0, (size_t)path->len)) return EINVAL;
    int fd = open(path->data, O_RDONLY);
    if (fd < 0) return errno;
    size_t cap = 4096, len = 0;
    char* buf = malloc(cap);
    for (;;) {
        if (len == cap) { cap *= 2; buf = realloc(buf, cap); }
        if (!buf) rt_trap(2, "out of memory", "runtime");
        ssize_t n = read(fd, buf + len, cap - len);
        if (n < 0) { int e = errno; free(buf); close(fd); return e; }
        if (n == 0) break;
        len += (size_t)n;
    }
    close(fd);
    obx_str* s = rt_str_of(buf, (int64_t)len);
    free(buf);
    rt_str_drop(*out);
    *out = s;
    return 0;
}
static bool rt_getenv(obx_str* name, obx_str** out) {
    if (memchr(name->data, 0, (size_t)name->len)) return false;
    const char* v = getenv(name->data);
    if (!v) return false;
    rt_str_drop(*out);
    *out = rt_str_of(v, (int64_t)strlen(v));
    return true;
}
__attribute__((noreturn)) static void rt_panic(obx_str* msg, const char* w) {
    rt_flush();
    fprintf(stderr, "obx: panic: %.*s at %s\n", (int)msg->len, msg->data, w);
    exit(12);
}

/* ---- arrays */

static int64_t rt_idx(int64_t i, int64_t len, const char* w) {
    if (i < 0 || i >= len) rt_trap(3, "index out of range", w);
    return i;
}
static obx_arr rt_arr_new(int64_t n, size_t size) {
    obx_arr a = {0, n, n};
    if (n > 0) a.data = rt_alloc((size_t)n * size);
    return a;
}
static obx_arr rt_arr_fill(int64_t n, size_t size, const char* w) {
    if (n < 0) rt_trap(2, "negative array length", w);
    if (n > (int64_t)1 << 40) rt_trap(2, "out of memory", w);
    return rt_arr_new(n, size);
}
static void rt_arr_free(obx_arr a) { if (a.data) rt_free(a.data); }
static void* rt_arr_push(obx_arr* a, size_t size) {
    if (a->len == a->cap) {
        int64_t nc = a->cap ? a->cap * 2 : 4;
        void* d = realloc(a->data, (size_t)nc * size);
        if (!d) rt_trap(2, "out of memory", "runtime");
        if (!a->data) rt_live++;
        a->data = d; a->cap = nc;
    }
    return (char*)a->data + (size_t)(a->len++) * size;
}
static void* rt_arr_pop(obx_arr* a, size_t size, const char* w) {
    if (a->len == 0) rt_trap(3, "pop on an empty array", w);
    return (char*)a->data + (size_t)(--a->len) * size;
}
static void rt_arr_swap(obx_arr* a, int64_t i, int64_t j, size_t size, const char* w) {
    rt_idx(i, a->len, w); rt_idx(j, a->len, w);
    char tmp[size];
    char* p = (char*)a->data + (size_t)i * size;
    char* q = (char*)a->data + (size_t)j * size;
    memcpy(tmp, p, size); memcpy(p, q, size); memcpy(q, tmp, size);
}
static obx_arr rt_str_to_bytes(obx_str* s) {
    obx_arr a = rt_arr_new(s->len, 1);
    if (s->len) memcpy(a.data, s->data, (size_t)s->len);
    return a;
}
static obx_arr rt_args(void) {
    obx_arr a = rt_arr_new(rt_argc, sizeof(obx_str*));
    for (int i = 0; i < rt_argc; i++)
        ((obx_str**)a.data)[i] = rt_str_of(rt_argv[i], (int64_t)strlen(rt_argv[i]));
    return a;
}

/* ---- integers */

#define RT_OVF(w) rt_trap(10, "integer overflow", w)
#define RT_INT(N, T, MIN, BITS, SIGNED)                                                     \
static T rt_add_##N(T a, T b, const char* w) { T r; if (__builtin_add_overflow(a, b, &r)) RT_OVF(w); return r; } \
static T rt_sub_##N(T a, T b, const char* w) { T r; if (__builtin_sub_overflow(a, b, &r)) RT_OVF(w); return r; } \
static T rt_mul_##N(T a, T b, const char* w) { T r; if (__builtin_mul_overflow(a, b, &r)) RT_OVF(w); return r; } \
static T rt_wadd_##N(T a, T b) { T r; __builtin_add_overflow(a, b, &r); return r; }        \
static T rt_wsub_##N(T a, T b) { T r; __builtin_sub_overflow(a, b, &r); return r; }        \
static T rt_wmul_##N(T a, T b) { T r; __builtin_mul_overflow(a, b, &r); return r; }        \
static T rt_div_##N(T a, T b, const char* w) {                                              \
    if (b == 0) rt_trap(10, "division by zero", w);                                         \
    if (SIGNED && a == MIN && b == (T)-1) RT_OVF(w);                                        \
    return a / b; }                                                                         \
static T rt_rem_##N(T a, T b, const char* w) {                                              \
    if (b == 0) rt_trap(10, "division by zero", w);                                         \
    if (SIGNED && a == MIN && b == (T)-1) RT_OVF(w);                                        \
    return a % b; }                                                                         \
static T rt_neg_##N(T a, const char* w) { T r; if (__builtin_sub_overflow((T)0, a, &r)) RT_OVF(w); return r; } \
static T rt_shl_##N(T a, int64_t n, const char* w) {                                        \
    if (n < 0 || n >= BITS) rt_trap(10, "shift count out of range", w);                     \
    return (T)((u128)a << n); }                                                             \
static T rt_shr_##N(T a, int64_t n, const char* w) {                                        \
    if (n < 0 || n >= BITS) rt_trap(10, "shift count out of range", w);                     \
    return a >> n; }

#define I128_MIN ((i128)((u128)1 << 127))
RT_INT(int, int64_t, INT64_MIN, 64, 1)
RT_INT(i32, int32_t, INT32_MIN, 32, 1)
RT_INT(u32, uint32_t, 0, 32, 0)
RT_INT(u64, uint64_t, 0, 64, 0)
RT_INT(byte, uint8_t, 0, 8, 0)
RT_INT(i128, i128, I128_MIN, 128, 1)
RT_INT(u128, u128, 0, 128, 0)

/* conversions: from a signed value (as i128), an unsigned u128, or a float */
#define RT_CV(N, T, LO, HI)                                                                 \
static T rt_cvs_##N(i128 v, const char* w) {                                                \
    if (v < (i128)(LO) || ((HI) >= 0 && (u128)v > (u128)(HI) && v >= 0))                   \
        rt_trap(10, "conversion out of range", w);                                         \
    return (T)v; }                                                                          \
static T rt_cvu_##N(u128 v, const char* w) {                                                \
    if (v > (u128)(HI)) rt_trap(10, "conversion out of range", w);                          \
    return (T)v; }                                                                          \
static T rt_cvf_##N(double d, const char* w) {                                              \
    if (!((d >= (double)(LO) || d > (double)(LO) - 1.0) && d < (double)(HI) + 1.0))         \
        rt_trap(10, "conversion out of range", w);                                         \
    return (T)d; }

#define U128_MAX (~(u128)0)
#define I128_MAX ((i128)(U128_MAX >> 1))
RT_CV(int, int64_t, INT64_MIN, INT64_MAX)
RT_CV(i32, int32_t, INT32_MIN, INT32_MAX)
RT_CV(u32, uint32_t, 0, UINT32_MAX)
RT_CV(u64, uint64_t, 0, UINT64_MAX)
RT_CV(byte, uint8_t, 0, 255)
RT_CV(i128, i128, I128_MIN, I128_MAX)
RT_CV(u128, u128, 0, U128_MAX)
