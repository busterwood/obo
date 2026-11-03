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
#include <dirent.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <time.h>
#include <sys/mman.h>
#if defined(__SANITIZE_ADDRESS__)
#include <sanitizer/common_interface_defs.h>
#include <sanitizer/asan_interface.h>
#include <sanitizer/lsan_interface.h>
#endif

typedef struct { int64_t rc; char data[]; } obx_sblk;
typedef struct { obx_sblk* b; uint32_t off, len; } obx_str;
#define RT_SP(s) ((const char*)(s).b->data + (s).off)
typedef struct { void* data; int64_t len, cap; } obx_arr;
typedef struct { void* data; int64_t len; } obx_slice;
typedef struct { void* fn; void* env; } obx_clo;     // a closure: code and environment
typedef __int128 i128;
typedef unsigned __int128 u128;

static int64_t rt_live;             /* live objects, counted only when rt_check */
static bool rt_check;               /* OBX_CHECK=1: a leak at exit stops the program (exit 5) */
static int rt_argc;
static char** rt_argv;
static char rt_obuf[1 << 16];
static size_t rt_olen;
/* Set as the program ends (main returned, exit, a trap): output from then on, by a callback C
   calls at exit (SPEC 12), is written at once, since nothing flushes the buffer later. */
static bool rt_ended;

static int rt_oerr;                 /* the first failed write of rt_obuf (obxlib_flush_out) */
static void rt_flush(void) {
    size_t off = 0;
    while (off < rt_olen) {
        ssize_t n = write(1, rt_obuf + off, rt_olen - off);
        if (n <= 0) { if (!rt_oerr) rt_oerr = n < 0 ? errno : EIO; break; }
        off += (size_t)n;
    }
    rt_olen = 0;
}

__attribute__((noreturn)) static void rt_trap(int code, const char* msg, const char* where) {
    rt_flush();
    rt_ended = true;
    fprintf(stderr, "obx: %s at %s\n", msg, where);
    exit(code);
}
/* A trap about a Shared.with that is open: "obx: <msg> (the with at <with>) at <where>". */
__attribute__((noreturn)) static void rt_trap_with(int code, const char* msg, const char* with, const char* where) {
    rt_flush();
    rt_ended = true;
    fprintf(stderr, "obx: %s (the with at %s) at %s\n", msg, with, where);
    exit(code);
}

/* Not zeroed: every caller stores each byte it later reads (all the fields of a record or of the
   variant built, every array element, a str's bytes and its NUL). */
static void* rt_alloc(size_t n) {
    void* p = malloc(n ? n : 1);
    if (!p) rt_trap(2, "out of memory", "runtime");
    if (rt_check) rt_live++;
    return p;
}
static void rt_free(void* p) { free(p); if (rt_check) rt_live--; }

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
/* A drop method's own values, dropped as it returns, go onto the worklist (in a heap copy) to be
   dropped right after it, so that a method that detaches a child does not recurse (SPEC 9.4). */
static void rt_ddefer(rt_dfn f, void* p, size_t n) {
    void* q = rt_alloc(n);
    memcpy(q, p, n);
    rt_dpush(rt_free_act, q);
    rt_dpush(f, q);
}

/* Tasks (lib/task.obx, design/concurrency.md stage C1a): stackful coroutines on one thread. A
   task runs on its own mmap'd stack (RT_STACK bytes) with a guard page (RT_GUARD) below it; the
   root task is main's stack. The functions are at the end of this file. */
#define RT_STACK (256 * 1024)
#define RT_GUARD 4096
typedef struct rt_task rt_task;
typedef struct rt_scope rt_scope;
struct rt_scope {
    rt_task* owner;                 /* the task that opened it (and closes it) */
    rt_scope* outer;                /* the owner's scope opened before it */
    rt_task *tasks, *last;          /* its unfinished tasks, in start order */
    int64_t live;                   /* how many */
    bool closing;                   /* the owner is parked until live is 0 */
};
enum { RT_RUN, RT_PARKED, RT_DONE };
struct rt_task {
    void* sp;                       /* the saved stack pointer while switched out */
    char* stk;                      /* the stack's mapping, guard page first; 0 for the root and promises */
    void* (*run)(void*);            /* the entry (a callback of lib/task) and its argument box */
    void* job;
    void* res;                      /* the result box once done, until taken or dropped */
    void (*resdrop)(void*);         /* drops a result box */
    rt_task* waiter;                /* the task parked waiting for this one */
    rt_task* next;                  /* the run queue */
    rt_scope* scope;                /* while unfinished: its scope, and its links in the scope's list */
    rt_task *snext, *sprev;
    rt_scope* scopes;               /* the innermost scope this task has open */
    int state, refs;                /* refs: the future, the promise, and the running task itself */
    bool cancelled, cancellable, promise, abandoned, forgotten;
    const char* pw;                 /* while parked: where (the user's call, "file:line") */
    int shdrop;                     /* inside a Shared value's drop: more go onto the worklist */
    rt_dact* ds;                    /* its drop-action stack and C string copies while switched out */
    int64_t dn, dcap;
    char** ctmp;
    int64_t nctmp, cctmp;
#if defined(__SANITIZE_ADDRESS__)
    void* fake;                     /* ASan's fake stack while switched out, and the stack's bounds */
    const void* sbot;
    size_t ssz;
#endif
};
static rt_task rt_root = {.refs = 1};
static rt_task* rt_cur = &rt_root;

/* Stack overflow (SPEC 14, exit 4): a SIGSEGV near the stack, or in the guard page of the running
   task's stack, caught on an alternate stack. */
static char* rt_stack_top;
static size_t rt_stack_max;
static void rt_segv(int sig, siginfo_t* si, void* uc) {
    char* a = si->si_addr;
    (void)uc;
    if ((a <= rt_stack_top && (size_t)(rt_stack_top - a) <= rt_stack_max + (1 << 20)) ||
        (rt_cur->stk && a >= rt_cur->stk && a < rt_cur->stk + RT_GUARD)) {
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
    const char* c = getenv("OBX_CHECK");
    rt_check = c && c[0] == '1';
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
    rt_ended = true;
    if (rt_check && rt_live != 0) {
        fprintf(stderr, "obx: leak: %lld live objects at exit at runtime\n", (long long)rt_live);
        exit(5);
    }
}
__attribute__((noreturn)) static void rt_exit(int64_t code) { rt_flush(); rt_ended = true; exit((int)code); }

/* ---- strings
   A str is {block, offset, len} (16 bytes, so it is passed in registers): the block is a
   reference-counted allocation, or a static one (rc < 0) for literals. A substring shares the
   block, so its bytes are not in general NUL-terminated; but every block ends with a NUL, so
   RT_SP(s)[s.len] can always be read. Offsets and lengths are 32 bits: a str holds < 4 GiB. */

#ifndef RT_STR_MAX
#define RT_STR_MAX UINT32_MAX       /* the longest str; a test lowers it with -DRT_STR_MAX=n */
#endif
static struct { int64_t rc; char data[1]; } rt_empty = {-1, ""};
/* Every str with new bytes is made here; lengths are computed in 64 bits by the callers. */
static obx_str rt_str_new(int64_t len) {
    if (len == 0) return (obx_str){(obx_sblk*)&rt_empty, 0, 0};
    if (len < 0 || len > (int64_t)RT_STR_MAX) rt_trap(2, "string too long (4 GiB or more)", "runtime");
    obx_sblk* b = rt_alloc(sizeof(obx_sblk) + (size_t)len + 1);
    b->rc = 1;
    b->data[len] = 0;
    return (obx_str){b, 0, (uint32_t)len};
}
static obx_str rt_str_of(const char* p, int64_t len) {
    obx_str s = rt_str_new(len);
    if (len) memcpy(s.b->data, p, (size_t)len);
    return s;
}
static obx_str rt_str_dup(obx_str s) { if (s.b && s.b->rc >= 0) s.b->rc++; return s; }
static void rt_str_drop(obx_str s) { if (s.b && s.b->rc > 0 && --s.b->rc == 0) rt_free(s.b); }
static bool rt_str_eq(obx_str a, obx_str b) {
    return a.len == b.len && (RT_SP(a) == RT_SP(b) || memcmp(RT_SP(a), RT_SP(b), (size_t)a.len) == 0);
}
static int rt_str_cmp(obx_str a, obx_str b) {
    int64_t n = a.len < b.len ? a.len : b.len;
    int c = memcmp(RT_SP(a), RT_SP(b), (size_t)n);
    if (c) return c < 0 ? -1 : 1;
    return a.len < b.len ? -1 : a.len > b.len;
}
static obx_str rt_str_concat(obx_str a, obx_str b) {
    if (b.len == 0) return rt_str_dup(a);
    if (a.len == 0) return rt_str_dup(b);
    obx_str s = rt_str_new((int64_t)a.len + b.len);
    memcpy(s.b->data, RT_SP(a), (size_t)a.len);
    memcpy(s.b->data + a.len, RT_SP(b), (size_t)b.len);
    return s;
}
static uint8_t rt_str_byte(obx_str s, int64_t i, const char* w) {
    if (i < 0 || i >= s.len) rt_trap(3, "string index out of range", w);
    return (uint8_t)RT_SP(s)[i];
}
/* s[a..b]: shares the bytes of s (a new reference to its block) */
static obx_str rt_str_sub(obx_str s, int64_t a, int64_t b, const char* w) {
    if (a < 0 || b < a || b > s.len) rt_trap(3, "string slice out of range", w);
    return (obx_str){rt_str_dup(s).b, s.off + (uint32_t)a, (uint32_t)(b - a)};
}
static obx_str rt_str_from_bytes(obx_slice b) { return rt_str_of(b.data, b.len); }
static obx_str rt_str_from_c(void* p) {
    if (!p) return rt_str_new(0);
    return rt_str_of(p, (int64_t)strlen(p));
}
/* A NUL-terminated copy (malloc) or NULL when s contains a NUL byte. */
static char* rt_cdup(obx_str s) {
    if (memchr(RT_SP(s), 0, (size_t)s.len)) return 0;
    char* c = malloc((size_t)s.len + 1);
    if (!c) rt_trap(2, "out of memory", "runtime");
    memcpy(c, RT_SP(s), (size_t)s.len);
    c[s.len] = 0;
    return c;
}
/* A str for a C call: its own bytes when a NUL follows them, else a copy freed by rt_cfree
   after the call. rt_cfree frees the copies made since the call's rt_cmark, so a callback
   (an obx function C calls during the call) that calls C itself frees only its own. */
static char** rt_ctmp;
static int64_t rt_nctmp, rt_cctmp;
static const char* rt_cstr(obx_str s, const char* w) {
    if (memchr(RT_SP(s), 0, (size_t)s.len)) rt_trap(12, "a string passed to C contains a NUL byte", w);
    if (RT_SP(s)[s.len] == 0) return RT_SP(s);
    if (rt_nctmp == rt_cctmp) {
        rt_cctmp = rt_cctmp ? rt_cctmp * 2 : 8;
        rt_ctmp = realloc(rt_ctmp, (size_t)rt_cctmp * sizeof *rt_ctmp);
        if (!rt_ctmp) rt_trap(2, "out of memory", "runtime");
    }
    return rt_ctmp[rt_nctmp++] = rt_cdup(s);
}
static int64_t rt_cmark(void) { return rt_nctmp; }
/* An array's elements for a C call: an empty array (no buffer) passes a pointer to a byte */
static char rt_cempty;
static void* rt_cdata(void* p) { return p ? p : &rt_cempty; }
static void rt_cfree(int64_t m) { while (rt_nctmp > m) free(rt_ctmp[--rt_nctmp]); }
/* The decimal digits of v, written backwards before end; returns the first. 64-bit division as
   soon as v fits (a 128-bit one is a libgcc call). */
static char* rt_digits(char* end, u128 v) {
    for (; v > UINT64_MAX; v /= 10) *--end = (char)('0' + (int)(v % 10));
    uint64_t w = (uint64_t)v;
    do *--end = (char)('0' + (int)(w % 10)); while (w /= 10);
    return end;
}
static obx_str rt_u128_to_str(u128 v) {
    char buf[48], *p = rt_digits(buf + 48, v);
    return rt_str_of(p, buf + 48 - p);
}
static obx_str rt_i128_to_str(i128 v) {
    char buf[48], *p = rt_digits(buf + 48, v < 0 ? (u128)0 - (u128)v : (u128)v);
    if (v < 0) *--p = '-';
    return rt_str_of(p, buf + 48 - p);
}
static obx_str rt_int_to_str(int64_t v) { return rt_i128_to_str(v); }
static obx_str rt_u64_to_str(uint64_t v) { return rt_u128_to_str(v); }
static obx_str rt_float_to_str(double d) {
    char buf[64];
    for (int p = 15; p <= 17; p++) {
        snprintf(buf, sizeof buf, "%.*g", p, d);
        if (strtod(buf, 0) == d || d != d) break;
    }
    return rt_str_of(buf, (int64_t)strlen(buf));
}
static bool rt_parse_float(obx_str s, double* out) {
    char buf[401];
    if (s.len == 0 || s.len > 400 || memchr(RT_SP(s), 0, (size_t)s.len)) return false;
    memcpy(buf, RT_SP(s), (size_t)s.len);
    buf[s.len] = 0;
    char* end;
    errno = 0;
    double d = strtod(buf, &end);
    if (end != buf + s.len) return false;
    *out = d;
    return true;
}
static int64_t rt_write_fd(int64_t fd, obx_str s) {
    if (fd == 1 && !rt_ended) {
        if (rt_olen + (size_t)s.len > sizeof rt_obuf) rt_flush();
        if ((size_t)s.len <= sizeof rt_obuf) {
            memcpy(rt_obuf + rt_olen, RT_SP(s), (size_t)s.len);
            rt_olen += (size_t)s.len;
            return s.len;
        }
    }
    rt_flush();
    int64_t off = 0;
    while (off < s.len) {
        ssize_t n = write((int)fd, RT_SP(s) + off, (size_t)(s.len - off));
        if (n <= 0) return -1;
        off += n;
    }
    return off;
}
static int64_t rt_read_file(obx_str path, obx_str* out) {
    char* cp = rt_cdup(path);
    if (!cp) return EINVAL;
    int fd = open(cp, O_RDONLY);
    free(cp);
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
    obx_str s = rt_str_of(buf, (int64_t)len);
    free(buf);
    rt_str_drop(*out);
    *out = s;
    return 0;
}
static bool rt_getenv(obx_str name, obx_str* out) {
    char* cn = rt_cdup(name);
    if (!cn) return false;
    const char* v = getenv(cn);
    free(cn);
    if (!v) return false;
    rt_str_drop(*out);
    *out = rt_str_of(v, (int64_t)strlen(v));
    return true;
}
__attribute__((noreturn)) static void rt_panic(obx_str msg, const char* w) {
    rt_flush();
    rt_ended = true;
    fprintf(stderr, "obx: panic: %.*s at %s\n", (int)msg.len, RT_SP(msg), w);
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
        if (!a->data && rt_check) rt_live++;
        a->data = d; a->cap = nc;
    }
    return (char*)a->data + (size_t)(a->len++) * size;
}
static void* rt_arr_pop(obx_arr* a, size_t size, const char* w) {
    if (a->len == 0) rt_trap(3, "pop on an empty array", w);
    return (char*)a->data + (size_t)(--a->len) * size;
}
static void rt_swap(obx_slice a, int64_t i, int64_t j, size_t size, const char* w) {
    rt_idx(i, a.len, w); rt_idx(j, a.len, w);
    char tmp[size + 1];
    char* p = (char*)a.data + (size_t)i * size;
    char* q = (char*)a.data + (size_t)j * size;
    memcpy(tmp, p, size); memcpy(p, q, size); memcpy(q, tmp, size);
}
static inline obx_slice rt_as_slice(obx_arr* a) { return (obx_slice){a->data, a->len}; }
/* s[lo..hi] (hi < 0: to the end), checked when it is made */
static obx_slice rt_sub(obx_slice s, int64_t lo, int64_t hi, size_t size, const char* w) {
    if (lo < 0 || hi < lo || hi > s.len) rt_trap(3, "slice range out of bounds", w);
    return (obx_slice){(char*)s.data + (size_t)lo * size, hi - lo};
}
static obx_arr rt_str_to_bytes(obx_str s) {
    obx_arr a = rt_arr_new(s.len, 1);
    if (s.len) memcpy(a.data, RT_SP(s), (size_t)s.len);
    return a;
}
static obx_arr rt_args(void) {
    obx_arr a = rt_arr_new(rt_argc, sizeof(obx_str));
    for (int i = 0; i < rt_argc; i++)
        ((obx_str*)a.data)[i] = rt_str_of(rt_argv[i], (int64_t)strlen(rt_argv[i]));
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

/* ---- C helpers for the library (lib/io, fs, os, time), called through
   `extern "c"` declarations; every name starts with obx_. They exist where a
   libc call needs a struct, errno or a pointer the language cannot express. */

int64_t obx_errno(void) { return errno; }
void* obx_dirent_name(void* d) { return ((struct dirent*)d)->d_name; }
int64_t obx_dirent_type(void* d) { return ((struct dirent*)d)->d_type; }

static struct stat obx_st;
/* stat (follow != 0) or lstat into a buffer read by obx_stat_field; 0 or errno */
int64_t obx_stat(const char* path, int64_t follow) {
    int r = follow ? stat(path, &obx_st) : lstat(path, &obx_st);
    return r == 0 ? 0 : errno;
}
int64_t obx_stat_field(int64_t i) {
    switch (i) {
    case 0: return (int64_t)obx_st.st_size;
    case 1: return (int64_t)obx_st.st_mode;
    case 2: return (int64_t)obx_st.st_mtim.tv_sec;
    case 3: return (int64_t)obx_st.st_mtim.tv_nsec;
    case 4: return (int64_t)obx_st.st_nlink;
    case 5: return (int64_t)obx_st.st_uid;
    case 6: return (int64_t)obx_st.st_gid;
    case 7: return (int64_t)obx_st.st_ino;
    case 8: return (int64_t)obx_st.st_dev;
    case 9: return (int64_t)obx_st.st_atim.tv_sec;
    case 10: return (int64_t)obx_st.st_ctim.tv_sec;
    case 11: return (int64_t)obx_st.st_blocks;
    default: return 0;
    }
}
/* how: 0 read, 1 write (create, truncate), 2 append (create), 3 create new
   (fail if it exists); the fd, or -errno */
int64_t obx_open(const char* path, int64_t how) {
    static const int flags[4] = {O_RDONLY, O_WRONLY | O_CREAT | O_TRUNC,
                                 O_WRONLY | O_CREAT | O_APPEND, O_WRONLY | O_CREAT | O_EXCL};
    if (how < 0 || how > 3) return -EINVAL;
    int fd = open(path, flags[how] | O_CLOEXEC, 0666);
    return fd < 0 ? -(int64_t)errno : fd;
}
/* flush the stdout buffer; the errno of the first write to stdout that failed since the last
   call, or 0 */
int64_t obxlib_flush_out(void) {
    rt_flush();
    int e = rt_oerr;
    rt_oerr = 0;
    return e;
}
/* readlink into a buffer read by obxlib_readlink_text; the length, or -errno */
static char obxlib_link[4097];
int64_t obxlib_readlink(const char* path) {
    ssize_t n = readlink(path, obxlib_link, sizeof obxlib_link - 1);
    if (n < 0) return -(int64_t)errno;
    obxlib_link[n] = 0;
    return n;
}
void* obxlib_readlink_text(void) { return obxlib_link; }
/* 1 when fd is a terminal; its width in columns, or 0 when it reports none */
int64_t obxlib_isatty(int64_t fd) { return isatty((int)fd); }
int64_t obxlib_term_cols(int64_t fd) {
    struct winsize w;
    if (ioctl((int)fd, TIOCGWINSZ, &w) != 0 || w.ws_col == 0 || w.ws_row == 0) return 0;
    return w.ws_col;
}
/* C memory for lib/ffi (and through it the database adapters): a zeroed buffer from the runtime's
   allocator (counted by OBX_CHECK), byte, int32 and pointer access by index, and copies between
   obx byte arrays and C memory */
void* obxlib_alloc(int64_t n) {
    size_t k = n > 0 ? (size_t)n : 1;
    void* p = rt_alloc(k);
    memset(p, 0, k);
    return p;
}
void obxlib_free(void* p) { if (p) rt_free(p); }
int64_t obxlib_peek(const void* p, int64_t i) { return ((const uint8_t*)p)[i]; }
void obxlib_poke(void* p, int64_t i, int64_t v) { ((uint8_t*)p)[i] = (uint8_t)v; }
int64_t obxlib_peek_i32(const void* p, int64_t i) { return ((const int32_t*)p)[i]; }
void obxlib_poke_i32(void* p, int64_t i, int64_t v) { ((int32_t*)p)[i] = (int32_t)v; }
void* obxlib_peek_ptr(const void* p, int64_t i) { return ((void* const*)p)[i]; }
void obxlib_poke_ptr(void* p, int64_t i, void* q) { ((void**)p)[i] = q; }
void obxlib_copy_in(void* dst, int64_t at, const void* src, int64_t n) { memcpy((char*)dst + at, src, (size_t)n); }
void obxlib_copy_out(void* dst, const void* src, int64_t at, int64_t n) { memcpy(dst, (const char*)src + at, (size_t)n); }
/* clock: 0 realtime, 1 monotonic; nanoseconds */
int64_t obx_clock_ns(int64_t which) {
    struct timespec t;
    clock_gettime(which ? CLOCK_MONOTONIC : CLOCK_REALTIME, &t);
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
void* obx_getcwd(void) {
    static char buf[4096];
    return getcwd(buf, sizeof buf);
}

/* ---- tasks (lib/task.obx; design/concurrency.md, stage C1a)
   Stackful coroutines on one thread, scheduled cooperatively from a FIFO run queue. Every
   suspension goes through rt_park; a task that parks with no other task runnable is a deadlock
   (exit 14), since nothing else can wake it. rt_ctx_swap saves the callee-saved registers, the
   SSE and x87 control words and the stack pointer, and resumes another task where it saved its
   own. The drop-action stack and the C string copies are per task (swapped by rt_switch), so a
   task that parks inside a drop or a callback does not mix its actions with another's. With
   ASan the switches are announced (__sanitizer_start/finish_switch_fiber). */

void rt_ctx_swap(void** save, void* to);
__asm__(".text\n.globl rt_ctx_swap\n.type rt_ctx_swap,@function\nrt_ctx_swap:\n"
        "\tpushq %rbp\n\tpushq %rbx\n\tpushq %r12\n\tpushq %r13\n\tpushq %r14\n\tpushq %r15\n"
        "\tsubq $8, %rsp\n\tstmxcsr (%rsp)\n\tfnstcw 4(%rsp)\n\tmovq %rsp, (%rdi)\n\tmovq %rsi, %rsp\n"
        "\tldmxcsr (%rsp)\n\tfldcw 4(%rsp)\n\taddq $8, %rsp\n"
        "\tpopq %r15\n\tpopq %r14\n\tpopq %r13\n\tpopq %r12\n\tpopq %rbx\n\tpopq %rbp\n\tret\n"
        ".size rt_ctx_swap, .-rt_ctx_swap\n");

static rt_task *rt_rq_head, *rt_rq_tail;   /* runnable tasks, first to run first */
static rt_task* rt_dead;                   /* a finished task, released by the next one to run */
static rt_task* rt_from;                   /* the task the running one was resumed from */
typedef struct rt_shared rt_shared;
static rt_shared* rt_with_top;             /* the innermost open Shared.with (no task may park) */
static char* rt_stk_free;                  /* stacks for reuse, linked through their lowest word */
static int rt_nstk_free;

static void rt_enqueue(rt_task* t) {
    t->next = 0;
    if (rt_rq_tail) rt_rq_tail->next = t; else rt_rq_head = t;
    rt_rq_tail = t;
}
static rt_task* rt_dequeue(void) {
    rt_task* t = rt_rq_head;
    if (t) { rt_rq_head = t->next; if (!rt_rq_head) rt_rq_tail = 0; }
    return t;
}
static char* rt_stack_get(void) {
    char* s = rt_stk_free;
    if (s) {
        rt_stk_free = *(char**)(s + RT_GUARD);
        rt_nstk_free--;
    } else {
        s = mmap(0, RT_STACK + RT_GUARD, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
        if (s == MAP_FAILED) rt_trap(2, "out of memory (a task's stack)", "runtime");
        mprotect(s, RT_GUARD, PROT_NONE);
    }
#if defined(__SANITIZE_ADDRESS__)
    ASAN_UNPOISON_MEMORY_REGION(s + RT_GUARD, RT_STACK);
    __lsan_register_root_region(s + RT_GUARD, RT_STACK);   /* a parked task's locals are reachable */
#endif
    return s;
}
static void rt_stack_put(char* s) {
#if defined(__SANITIZE_ADDRESS__)
    __lsan_unregister_root_region(s + RT_GUARD, RT_STACK);
#endif
    if (rt_nstk_free >= 64) { munmap(s, RT_STACK + RT_GUARD); return; }
    *(char**)(s + RT_GUARD) = rt_stk_free;
    rt_stk_free = s;
    rt_nstk_free++;
}
static void rt_release(rt_task* t) { if (--t->refs == 0) rt_free(t); }
/* A finished task's stack and buffers, once another task runs. */
static void rt_reap(rt_task* t) {
    rt_stack_put(t->stk);
    free(t->ds);
    free(t->ctmp);
    rt_release(t);
}
/* What a task does first when it runs again after a switch. */
#if defined(__SANITIZE_ADDRESS__)
static char* rt_root_lo;   /* while another task runs: the low end of main's stack, a root region */
#endif
static void rt_resumed(void) {
#if defined(__SANITIZE_ADDRESS__)
    const void* bot;
    size_t sz;
    __sanitizer_finish_switch_fiber(rt_cur->fake, &bot, &sz);
    if (rt_from && !rt_from->stk) { rt_from->sbot = bot; rt_from->ssz = sz; }
    if (rt_cur == &rt_root && rt_root_lo) {
        __lsan_unregister_root_region(rt_root_lo, (size_t)(rt_stack_top - rt_root_lo));
        rt_root_lo = 0;
    }
#endif
    if (rt_dead) { rt_task* d = rt_dead; rt_dead = 0; rt_reap(d); }
}
static void rt_switch(rt_task* to, bool dying) {
    rt_task* me = rt_cur;
    me->ds = rt_ds; me->dn = rt_dn; me->dcap = rt_dcap;
    me->ctmp = rt_ctmp; me->nctmp = rt_nctmp; me->cctmp = rt_cctmp;
    rt_ds = to->ds; rt_dn = to->dn; rt_dcap = to->dcap;
    rt_ctmp = to->ctmp; rt_nctmp = to->nctmp; rt_cctmp = to->cctmp;
    rt_cur = to;
    rt_from = me;
#if defined(__SANITIZE_ADDRESS__)
    if (me == &rt_root) {   /* main's stack is scanned for leaks only while it runs: register it */
        rt_root_lo = (char*)__builtin_frame_address(0) - 4096;
        __lsan_register_root_region(rt_root_lo, (size_t)(rt_stack_top - rt_root_lo));
    }
    __sanitizer_start_switch_fiber(dying ? 0 : &me->fake, to->stk ? to->stk + RT_GUARD : to->sbot,
                                   to->stk ? (size_t)RT_STACK : to->ssz);
#else
    (void)dying;
#endif
    rt_ctx_swap(&me->sp, to->sp);
    rt_resumed();
}
/* Suspends the running task until rt_wake (or, when cancellable, rt_cancel); true when it was
   cancelled. */
static bool rt_park(bool cancellable, const char* w) {
    rt_task* me = rt_cur;
    rt_task* nx = rt_dequeue();
    if (!nx) rt_trap(14, "deadlock: every task is parked", w);
    me->pw = w;
    me->state = RT_PARKED;
    me->cancellable = cancellable;
    rt_switch(nx, false);
    me->cancellable = false;
    return cancellable && me->cancelled;
}
static void rt_wake(rt_task* t) {
    if (t->state == RT_PARKED) { t->state = RT_RUN; rt_enqueue(t); }
}
/* Every operation that can park checks first that no Shared.with is open (exit 13); w is the
   user's call. Defined with Shared below. */
static void rt_nopark(const char* w);
/* Cancels t and every unfinished task of the scopes it has open. */
static void rt_cancel(rt_task* t) {
    if (t->promise || t->state == RT_DONE || t->cancelled) return;
    t->cancelled = true;
    for (rt_scope* s = t->scopes; s; s = s->outer)
        for (rt_task* u = s->tasks; u; u = u->snext) rt_cancel(u);
    if (t->state == RT_PARKED && t->cancellable) rt_wake(t);
}
__attribute__((noreturn)) static void rt_task_boot(void) {
    rt_resumed();
    rt_task* t = rt_cur;
    void* r = t->run(t->job);
    t->job = 0;
    while (t->forgotten && r) { void* x = r; r = 0; t->resdrop(x); }   /* nobody will wait for it */
    t->res = r;
    t->state = RT_DONE;
    rt_scope* s = t->scope;
    if (t->sprev) t->sprev->snext = t->snext; else s->tasks = t->snext;
    if (t->snext) t->snext->sprev = t->sprev; else s->last = t->sprev;
    t->scope = 0;
    if (--s->live == 0 && s->closing) rt_wake(s->owner);
    if (t->waiter) rt_wake(t->waiter);
    rt_task* nx = rt_dequeue();
    if (!nx) rt_trap(14, "deadlock: every task is parked", s->owner->pw ? s->owner->pw : "runtime");
    rt_dead = t;
    rt_switch(nx, true);
    __builtin_unreachable();
}

void* obxlib_task_scope_open(void) {
    rt_scope* s = rt_alloc(sizeof *s);
    s->owner = rt_cur; s->outer = rt_cur->scopes; s->tasks = s->last = 0; s->live = 0; s->closing = false;
    rt_cur->scopes = s;
    return s;
}
/* Parks (not cancellable) until every task of the scope has finished. */
void obxlib_task_scope_close(void* p, const char* w) {
    rt_scope* s = p;
    rt_nopark(w);
    while (s->live > 0) { s->closing = true; rt_park(false, w); }
    s->closing = false;
    rt_cur->scopes = s->outer;
    rt_free(s);
}
void* obxlib_task_start(void* sp, void* run, void* job, void* resdrop) {
    rt_scope* s = sp;
    rt_task* t = rt_alloc(sizeof *t);
    memset(t, 0, sizeof *t);
    t->stk = rt_stack_get();
    t->run = (void* (*)(void*))run; t->job = job; t->resdrop = (void (*)(void*))resdrop;
    t->refs = 2;
    t->cancelled = rt_cur->cancelled;
    t->scope = s;
    t->sprev = s->last;
    if (s->last) s->last->snext = t; else s->tasks = t;
    s->last = t;
    s->live++;
    uint64_t* top = (uint64_t*)(t->stk + RT_GUARD + RT_STACK);
    uint32_t mx;
    uint16_t cw;
    __asm__ volatile("stmxcsr %0" : "=m"(mx));
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    *--top = 0;                                 /* rt_task_boot's return address: it never returns */
    *--top = (uint64_t)(uintptr_t)rt_task_boot;
    for (int i = 0; i < 6; i++) *--top = 0;     /* rbp rbx r12 r13 r14 r15 */
    *--top = (uint64_t)mx | (uint64_t)cw << 32;
    t->sp = top;
    rt_enqueue(t);
    return t;
}
/* 0 when t is done, 1 when the waiting task was cancelled, 2 when t is an abandoned promise. */
int64_t obxlib_task_wait(void* p, const char* w) {
    rt_task* t = p;
    rt_nopark(w);
    while (t->state != RT_DONE) {
        if (rt_cur->cancelled) return 1;
        t->waiter = rt_cur;
        rt_park(true, w);
        t->waiter = 0;
    }
    return t->abandoned ? 2 : 0;
}
/* The index of a future in ts[0..n) that is done, or -1 when the waiting task was cancelled. */
int64_t obxlib_task_wait_any(void** ts, int64_t n, const char* w) {
    rt_nopark(w);
    for (;;) {
        for (int64_t i = 0; i < n; i++)
            if (((rt_task*)ts[i])->state == RT_DONE) return i;
        if (rt_cur->cancelled) return -1;
        for (int64_t i = 0; i < n; i++) ((rt_task*)ts[i])->waiter = rt_cur;
        rt_park(true, w);
        for (int64_t i = 0; i < n; i++) ((rt_task*)ts[i])->waiter = 0;
    }
}
/* The future t is dropped: its task is cancelled, and its result dropped once there is one. */
void obxlib_task_forget(void* p) {
    rt_task* t = p;
    if (t->state == RT_DONE) {
        if (t->res) { void* x = t->res; t->res = 0; t->resdrop(x); }
    } else {
        t->forgotten = true;
        rt_cancel(t);
    }
    rt_release(t);
}
/* Cancels t's task and parks (not cancellable) until it has finished; then drops the future. */
void obxlib_task_join(void* p, const char* w) {
    rt_task* t = p;
    rt_cancel(t);
    while (t->state != RT_DONE && !t->promise) {
        t->waiter = rt_cur;
        rt_park(false, w);
        t->waiter = 0;
    }
    obxlib_task_forget(t);
}
/* The result box of the finished t; the future is gone. */
void* obxlib_task_take(void* p) {
    rt_task* t = p;
    void* r = t->res;
    t->res = 0;
    rt_release(t);
    return r;
}
int64_t obxlib_task_yield(const char* w) {
    rt_nopark(w);
    if (rt_rq_head) {
        rt_enqueue(rt_cur);
        rt_switch(rt_dequeue(), false);
    }
    return rt_cur->cancelled;
}
int64_t obxlib_task_cancelled(void) { return rt_cur->cancelled; }
/* A promise: a task with no code, done by complete or abandon; refs for the promise and its future. */
void* obxlib_task_promise(void* resdrop) {
    rt_task* t = rt_alloc(sizeof *t);
    memset(t, 0, sizeof *t);
    t->promise = true;
    t->resdrop = (void (*)(void*))resdrop;
    t->refs = 2;
    return t;
}
void obxlib_task_complete(void* p, void* res) {
    rt_task* t = p;
    t->state = RT_DONE;
    if (t->forgotten) t->resdrop(res); else t->res = res;
    if (t->waiter) rt_wake(t->waiter);
    rt_release(t);
}
void obxlib_task_abandon(void* p) {
    rt_task* t = p;
    t->state = RT_DONE;
    t->abandoned = true;
    if (t->waiter) rt_wake(t->waiter);
    rt_release(t);
}

/* Shared[T] (a lang item of lib/task): a counted block holding the value's box. The handle is a
   copy type; its dup and drop glue call rt_sh_dup and rt_sh_drop. */
struct rt_shared {
    int64_t rc;
    bool busy;                      /* its with is open: opened at w, inside the with up */
    const char* w;
    rt_shared* up;
    void* box;
    void (*drop)(void*);
};
static void rt_nopark(const char* w) {
    if (rt_with_top) rt_trap_with(13, "a task parked inside Shared.with", rt_with_top->w, w);
}
void* obxlib_sh_new(void* box, void* drop) {
    rt_shared* s = rt_alloc(sizeof *s);
    s->rc = 1; s->busy = false; s->box = box; s->drop = (void (*)(void*))drop;
    return s;
}
static void rt_sh_dup(void* p) { if (p) ((rt_shared*)p)->rc++; }
/* The value is dropped through the worklist: a value that holds the last handle of another
   (a list of Shared nodes) is dropped by the outermost rt_drun, without nesting (SPEC 9.4). */
static void rt_sh_free(rt_shared* s) {
    void* b = s->box;
    void (*d)(void*) = s->drop;
    rt_free(s);
    if (rt_cur->shdrop) { rt_dpush(d, b); return; }
    rt_cur->shdrop++;
    rt_drun(d, b);
    rt_cur->shdrop--;
}
static void rt_sh_drop(void* p) {
    rt_shared* s = p;
    if (s && --s->rc == 0 && !s->busy) rt_sh_free(s);
}
/* Opens with on the value: its box, lent until obxlib_sh_leave; a nested with on it stops the program. */
void* obxlib_sh_enter(void* p, const char* w) {
    rt_shared* s = p;
    if (s->busy) rt_trap_with(13, "Shared.with on a value whose with is already open", s->w, w);
    s->busy = true;
    s->w = w;
    s->up = rt_with_top;
    rt_with_top = s;
    return s->box;
}
void obxlib_sh_leave(void* p, void* box) {
    rt_shared* s = p;
    s->box = box;
    s->busy = false;
    rt_with_top = s->up;
    if (s->rc == 0) rt_sh_free(s);
}
