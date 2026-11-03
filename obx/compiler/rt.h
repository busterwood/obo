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
#include <sys/epoll.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <sys/eventfd.h>
#include <sys/signalfd.h>
#include <sys/wait.h>
#include <sys/prctl.h>
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
    int why;                        /* why it was woken: RT_WOK, or RT_WTIMEOUT by its timer */
    int64_t deadline;               /* task.within's deadline (monotonic ns), or RT_NEVER */
    int64_t wake_at, hidx;          /* while parked with a timer: when, and its heap index + 1 */
    uint64_t tseq;                  /* the order of timers with one wake_at */
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
#define RT_NEVER INT64_MAX
static rt_task rt_root = {.refs = 1, .deadline = RT_NEVER};
static rt_task* rt_cur = &rt_root;

/* Stack overflow (SPEC 14, exit 4): a SIGSEGV near the stack, or in the guard page of the running
   task's stack, caught on an alternate stack. */
static char* rt_stack_top;
static size_t rt_stack_max;
static pthread_t rt_main_thr;       /* a fault on a helper thread (C code, stage C1c) is not ours */
static void rt_segv(int sig, siginfo_t* si, void* uc) {
    char* a = si->si_addr;
    (void)uc;
    if (pthread_equal(pthread_self(), rt_main_thr) &&
        ((a <= rt_stack_top && (size_t)(rt_stack_top - a) <= rt_stack_max + (1 << 20)) ||
         (rt_cur->stk && a >= rt_cur->stk && a < rt_cur->stk + RT_GUARD))) {
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
    rt_main_thr = pthread_self();
    rt_stack_max = getrlimit(RLIMIT_STACK, &rl) == 0 && rl.rlim_cur != RLIM_INFINITY ? rl.rlim_cur : (size_t)1 << 30;
    ss.ss_sp = alt; ss.ss_size = sizeof alt;
    sigaltstack(&ss, 0);
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = rt_segv;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, 0);
}
static rt_dfn rt_end_fn;                   /* the program's global drops (task.workers) */
static void rt_on_end(rt_dfn f) { rt_end_fn = f; }
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
static int64_t rt_ntasks;                  /* started tasks not yet finished (task.workers refuses to fork) */

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
static void rt_wake(rt_task* t) {
    if (t->state == RT_PARKED) { t->state = RT_RUN; rt_enqueue(t); }
}

/* ---- the event loop (stage C1b): timers and sockets
   A task that parks with a deadline (task.sleep, or a cancellable wait inside task.within) has a
   timer: an entry in a binary heap ordered by (deadline, order of parking). A task waiting for a
   socket is in rt_fds[fd]; every socket is registered with epoll once, edge-triggered, when it is
   made, and an operation parks only after EAGAIN, so an edge always follows. When no task is
   runnable, rt_poll wakes the tasks whose timers have expired and waits in epoll_wait (or, with
   no socket waited for, nanosleep) until the nearest deadline or a ready socket. With no timer
   and no task waiting for a socket, nothing can wake any task: a deadlock (exit 14). Every 64
   switches it also looks without waiting, so tasks that keep each other runnable do not starve
   the sockets and timers. */
enum { RT_WOK = 0, RT_WCANCEL = 1, RT_WTIMEOUT = 3 };
/* reg: 0 not in the epoll set, 1 a socket of lib/net (counted in rt_nreg), 2 a C library's
   descriptor (obxlib_fd_wait; registered until the library closes it). lazy: a listener, put in
   the set at its first wait, so that one made before task.workers is in no set in the parent
   and each worker registers it in its own. */
typedef struct { rt_task *rd, *wr; uint8_t reg, lazy; } rt_fdw;
static rt_fdw* rt_fds;                     /* by descriptor: the tasks waiting to read and to write */
static int64_t rt_nfds;
static int rt_epfd = -1;
static int64_t rt_nreg;                    /* sockets of lib/net in the epoll set */
#define RT_SIG_TAG (~(uint64_t)1)           /* the epoll data of the signal watch's signalfd */
static void rt_sig_read(void);
static int64_t rt_nio;                     /* tasks waiting for a socket */
static int64_t rt_njobs;                   /* tasks parked in a blocking C call on the helper pool */
#define RT_POOL_TAG (~(uint64_t)0)          /* the epoll data of the helper pool's eventfd */
static void rt_pool_drain(bool rd);
static int rt_pool_check(int ms);
static int rt_pool_asleep;
static rt_task** rt_heap;                  /* the timers */
static int64_t rt_nheap, rt_cheap;
static uint64_t rt_tseq;
static unsigned rt_ticks;

static int64_t rt_mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
/* The monotonic time ms milliseconds from now, short of RT_NEVER. */
static int64_t rt_after(int64_t ms) {
    int64_t now = rt_mono();
    if (ms <= 0) return now;
    return ms >= (RT_NEVER - 1 - now) / 1000000 ? RT_NEVER - 1 : now + ms * 1000000;
}
static bool rt_expired(void) { return rt_cur->deadline != RT_NEVER && rt_mono() >= rt_cur->deadline; }
static bool rt_tless(rt_task* a, rt_task* b) {
    return a->wake_at < b->wake_at || (a->wake_at == b->wake_at && a->tseq < b->tseq);
}
static void rt_hset(int64_t i, rt_task* t) { rt_heap[i] = t; t->hidx = i + 1; }
static void rt_hup(int64_t i) {
    rt_task* t = rt_heap[i];
    while (i > 0 && rt_tless(t, rt_heap[(i - 1) / 2])) { rt_hset(i, rt_heap[(i - 1) / 2]); i = (i - 1) / 2; }
    rt_hset(i, t);
}
static void rt_hdown(int64_t i) {
    rt_task* t = rt_heap[i];
    for (;;) {
        int64_t c = 2 * i + 1;
        if (c >= rt_nheap) break;
        if (c + 1 < rt_nheap && rt_tless(rt_heap[c + 1], rt_heap[c])) c++;
        if (!rt_tless(rt_heap[c], t)) break;
        rt_hset(i, rt_heap[c]);
        i = c;
    }
    rt_hset(i, t);
}
static void rt_timer_add(rt_task* t, int64_t at) {
    if (rt_nheap == rt_cheap) {
        rt_cheap = rt_cheap ? 2 * rt_cheap : 64;
        rt_heap = realloc(rt_heap, (size_t)rt_cheap * sizeof *rt_heap);
        if (!rt_heap) rt_trap(2, "out of memory", "runtime");
    }
    t->wake_at = at;
    t->tseq = rt_tseq++;
    rt_heap[rt_nheap++] = t;
    rt_hup(rt_nheap - 1);
}
static void rt_timer_del(rt_task* t) {
    int64_t i = t->hidx - 1;
    rt_task* last = rt_heap[--rt_nheap];
    t->hidx = 0;
    if (i < rt_nheap) { rt_hset(i, last); rt_hup(i); rt_hdown(last->hidx - 1); }
}
/* Makes runnable the tasks whose timers have expired and whose sockets are ready. With block,
   waits until there is a runnable task; when nothing can make one, stops with a deadlock at w. */
static void rt_poll(bool block, const char* w) {
    struct epoll_event ev[256];
    for (;;) {
        int64_t wait = block && !rt_rq_head ? -1 : 0;   /* ns; -1: until a socket is ready */
        if (rt_nheap) {
            int64_t now = rt_mono();
            while (rt_nheap && rt_heap[0]->wake_at <= now) {
                rt_task* t = rt_heap[0];
                rt_timer_del(t);
                if (t->state == RT_PARKED) t->why = RT_WTIMEOUT;
                rt_wake(t);
            }
            if (rt_rq_head) wait = 0;
            else if (rt_nheap && wait) wait = rt_heap[0]->wake_at - now;
        }
        if (wait < 0 && !rt_nio && !rt_njobs) rt_trap(14, "deadlock: every task is parked", w);
        if (rt_nio || rt_njobs) {
            int ms = wait < 0 ? -1 : wait > (int64_t)INT32_MAX * 1000000 ? INT32_MAX
                   : (int)(wait / 1000000 + (wait % 1000000 != 0));
            if (rt_njobs) ms = rt_pool_check(ms);
            int n = rt_nio || ms ? epoll_wait(rt_epfd, ev, 256, ms) : 0;
            __atomic_store_n(&rt_pool_asleep, 0, __ATOMIC_RELAXED);
            for (int i = 0; i < n; i++) {
                if (ev[i].data.u64 == RT_POOL_TAG) { rt_pool_drain(true); continue; }
                if (ev[i].data.u64 == RT_SIG_TAG) { rt_sig_read(); continue; }
                int64_t fd = (int64_t)ev[i].data.u64;
                uint32_t e = ev[i].events;
                if (fd >= rt_nfds) continue;
                if (rt_fds[fd].rd && (e & (EPOLLIN | EPOLLRDHUP | EPOLLHUP | EPOLLERR))) rt_wake(rt_fds[fd].rd);
                if (rt_fds[fd].wr && (e & (EPOLLOUT | EPOLLHUP | EPOLLERR))) rt_wake(rt_fds[fd].wr);
            }
        } else if (wait > 0) {
            struct timespec d = {wait / 1000000000, wait % 1000000000};
            nanosleep(&d, 0);
        }
        if (!block || rt_rq_head) return;
    }
}
/* The task to run next: the run queue's first, after a look at the timers and sockets every 64
   switches; when the queue is empty, after waiting for one. */
static rt_task* rt_next(const char* w) {
    if ((rt_nio | rt_nheap | rt_njobs) && (++rt_ticks & 63) == 0) rt_poll(false, w);
    rt_task* t = rt_dequeue();
    if (!t) { rt_poll(true, w); t = rt_dequeue(); }
    return t;
}
/* Suspends the running task until rt_wake, its timer at `at` (RT_NEVER: none) or, when
   cancellable, rt_cancel: RT_WOK, RT_WTIMEOUT or RT_WCANCEL. */
static int rt_park_until(bool cancellable, int64_t at, const char* w) {
    rt_task* me = rt_cur;
    me->pw = w;
    me->state = RT_PARKED;
    me->cancellable = cancellable;
    me->why = RT_WOK;
    if (at != RT_NEVER) rt_timer_add(me, at);
    rt_task* nx = rt_next(w);
    if (nx != me) rt_switch(nx, false);
    if (me->hidx) rt_timer_del(me);
    me->cancellable = false;
    return cancellable && me->cancelled ? RT_WCANCEL : me->why;
}
static void rt_park(bool cancellable, const char* w) { rt_park_until(cancellable, RT_NEVER, w); }
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
    rt_ntasks--;
    rt_scope* s = t->scope;
    if (t->sprev) t->sprev->snext = t->snext; else s->tasks = t->snext;
    if (t->snext) t->snext->sprev = t->sprev; else s->last = t->sprev;
    t->scope = 0;
    if (--s->live == 0 && s->closing) rt_wake(s->owner);
    if (t->waiter) rt_wake(t->waiter);
    rt_task* nx = rt_next(s->owner->pw ? s->owner->pw : "runtime");
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
    t->deadline = rt_cur->deadline;
    t->scope = s;
    t->sprev = s->last;
    if (s->last) s->last->snext = t; else s->tasks = t;
    s->last = t;
    s->live++;
    rt_ntasks++;
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
/* 0 when t is done, 1 when the waiting task was cancelled, 2 when t is an abandoned promise, 3
   when the waiting task's deadline passed. */
int64_t obxlib_task_wait(void* p, const char* w) {
    rt_task* t = p;
    rt_nopark(w);
    while (t->state != RT_DONE) {
        if (rt_cur->cancelled) return 1;
        if (rt_expired()) return 3;
        t->waiter = rt_cur;
        rt_park_until(true, rt_cur->deadline, w);
        t->waiter = 0;
    }
    return t->abandoned ? 2 : 0;
}
/* The index of a future in ts[0..n) that is done; -1 when the waiting task was cancelled, -2 when
   its deadline passed. */
int64_t obxlib_task_wait_any(void** ts, int64_t n, const char* w) {
    rt_nopark(w);
    for (;;) {
        for (int64_t i = 0; i < n; i++)
            if (((rt_task*)ts[i])->state == RT_DONE) return i;
        if (rt_cur->cancelled) return -1;
        if (rt_expired()) return -2;
        for (int64_t i = 0; i < n; i++) ((rt_task*)ts[i])->waiter = rt_cur;
        rt_park_until(true, rt_cur->deadline, w);
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
/* Scope.spawn: the future is given up without cancelling its task; its result is dropped when
   there is one. The scope still waits for the task. */
void obxlib_task_detach(void* p) {
    rt_task* t = p;
    if (t->state == RT_DONE) {
        if (t->res) { void* x = t->res; t->res = 0; t->resdrop(x); }
    } else {
        t->forgotten = true;
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
/* 0, or 1 when the task is cancelled, 3 when its deadline has passed. */
int64_t obxlib_task_yield(const char* w) {
    rt_nopark(w);
    if ((rt_nio | rt_nheap | rt_njobs) && (++rt_ticks & 63) == 0) rt_poll(false, w);
    if (rt_rq_head) {
        rt_enqueue(rt_cur);
        rt_switch(rt_dequeue(), false);
    }
    return rt_cur->cancelled ? 1 : rt_expired() ? 3 : 0;
}
int64_t obxlib_task_cancelled(void) { return rt_cur->cancelled; }
/* What every library call that can park checks first, whether or not it parks: exit 13 inside
   Shared.with; then 1 when the task is cancelled, 3 when its deadline has passed, else 0. */
int64_t obxlib_task_check(const char* w) {
    rt_nopark(w);
    return rt_cur->cancelled ? 1 : rt_expired() ? 3 : 0;
}
/* Parks for ms milliseconds: 0, or 1 when cancelled, 3 when the task's deadline comes first. */
int64_t obxlib_task_sleep(int64_t ms, const char* w) {
    int64_t c = obxlib_task_check(w);
    if (c || ms <= 0) return c;
    int64_t at = rt_after(ms), dl = rt_cur->deadline;
    int r = rt_park_until(true, at < dl ? at : dl, w);
    return r == RT_WTIMEOUT && at <= dl ? 0 : r;
}
/* task.within: the deadline ms milliseconds from now, if it is earlier than the task's; the old
   one, which obxlib_task_restore puts back. Tasks started meanwhile inherit it. */
int64_t obxlib_task_within(int64_t ms) {
    int64_t old = rt_cur->deadline, at = rt_after(ms);
    if (at < old) rt_cur->deadline = at;
    return old;
}
void obxlib_task_restore(int64_t old) { rt_cur->deadline = old; }

/* ---- sockets (lib/net.obx): non-blocking TCP sockets, registered with epoll when made. An
   address is a family (4 or 6), 16 bytes (an IPv4 address in the first 4) and a port. Results
   are a count or a descriptor, or -errno. Writes use MSG_NOSIGNAL: a closed peer is EPIPE, never
   SIGPIPE. */
/* rt_fds has room for fd. */
static void rt_fd_table(int64_t fd) {
    if (fd >= rt_nfds) {
        int64_t n = rt_nfds ? rt_nfds : 64;
        while (n <= fd) n *= 2;
        rt_fdw* f = realloc(rt_fds, (size_t)n * sizeof *rt_fds);
        if (!f) rt_trap(2, "out of memory", "runtime");
        memset(f + rt_nfds, 0, (size_t)(n - rt_nfds) * sizeof *f);
        rt_fds = f;
        rt_nfds = n;
    }
}
/* The epoll set exists and rt_fds has room for fd: 0, or -errno. */
static int rt_fd_room(int64_t fd) {
    if (rt_epfd < 0 && (rt_epfd = epoll_create1(EPOLL_CLOEXEC)) < 0) return -errno;
    rt_fd_table(fd);
    return 0;
}
static int64_t rt_net_new(int fd) {
    int r = rt_fd_room(fd);
    if (r < 0) { close(fd); return r; }
    struct epoll_event ev = {.events = EPOLLIN | EPOLLOUT | EPOLLRDHUP | EPOLLET, .data.u64 = (uint64_t)fd};
    if (epoll_ctl(rt_epfd, EPOLL_CTL_ADD, fd, &ev) != 0) { int e = errno; close(fd); return -e; }
    rt_fds[fd].reg = 1;
    rt_fds[fd].lazy = 0;
    rt_nreg++;
    return fd;
}
static socklen_t rt_net_sa(int64_t fam, const uint8_t* a, int64_t port, struct sockaddr_storage* ss) {
    memset(ss, 0, sizeof *ss);
    if (fam == 6) {
        struct sockaddr_in6* s = (struct sockaddr_in6*)ss;
        s->sin6_family = AF_INET6;
        s->sin6_port = htons((uint16_t)port);
        memcpy(&s->sin6_addr, a, 16);
        return sizeof *s;
    }
    struct sockaddr_in* s = (struct sockaddr_in*)ss;
    s->sin_family = AF_INET;
    s->sin_port = htons((uint16_t)port);
    memcpy(&s->sin_addr, a, 4);
    return sizeof *s;
}
/* A listening socket (SO_REUSEADDR; with reuseport also SO_REUSEPORT) bound to the address. It
   is put in the epoll set at its first wait (lazy), not here. */
int64_t obxlib_net_listen(int64_t fam, const uint8_t* a, int64_t port, int64_t backlog, int64_t reuseport) {
    struct sockaddr_storage ss;
    socklen_t n = rt_net_sa(fam, a, port, &ss);
    int fd = socket(ss.ss_family, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return -errno;
    int one = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0 ||
        (reuseport && setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one) != 0) ||
        bind(fd, (struct sockaddr*)&ss, n) != 0 || listen(fd, (int)backlog) != 0) {
        int e = errno;
        close(fd);
        return -e;
    }
    rt_fd_table(fd);
    rt_fds[fd].reg = 0;
    rt_fds[fd].lazy = 1;
    return fd;
}
/* A socket connecting to the address; *pending is 1 while the connection is in progress (the
   socket becomes writable when it is done, and obxlib_net_soerror tells how it went). */
int64_t obxlib_net_connect(int64_t fam, const uint8_t* a, int64_t port, int64_t* pending) {
    struct sockaddr_storage ss;
    socklen_t n = rt_net_sa(fam, a, port, &ss);
    int fd = socket(ss.ss_family, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return -errno;
    *pending = 0;
    if (connect(fd, (struct sockaddr*)&ss, n) != 0) {
        int e = errno;
        if (e != EINPROGRESS) { close(fd); return -e; }
        *pending = 1;
    }
    return rt_net_new(fd);
}
int accept4(int fd, struct sockaddr* a, socklen_t* n, int flags);   /* _GNU_SOURCE in <sys/socket.h> */
int sched_getaffinity(pid_t pid, size_t n, void* mask);              /* _GNU_SOURCE in <sched.h> */
int64_t obxlib_net_accept(int64_t fd) {
    int c = accept4((int)fd, 0, 0, SOCK_NONBLOCK | SOCK_CLOEXEC);
    return c < 0 ? -errno : rt_net_new(c);
}
int64_t obxlib_net_read(int64_t fd, void* b, int64_t n) {
    ssize_t r = read((int)fd, b, (size_t)n);
    return r < 0 ? -errno : r;
}
int64_t obxlib_net_write(int64_t fd, const void* b, int64_t n) {
    ssize_t r = send((int)fd, b, (size_t)n, MSG_NOSIGNAL);
    return r < 0 ? -errno : r;
}
/* The local (peer 0) or remote address into out[0..19): the family, the port (big-endian), 16
   address bytes. */
int64_t obxlib_net_addr(int64_t fd, int64_t peer, uint8_t* out) {
    struct sockaddr_storage ss;
    socklen_t n = sizeof ss;
    if ((peer ? getpeername((int)fd, (struct sockaddr*)&ss, &n) : getsockname((int)fd, (struct sockaddr*)&ss, &n)) != 0)
        return -errno;
    memset(out, 0, 19);
    if (ss.ss_family == AF_INET6) {
        struct sockaddr_in6* s = (struct sockaddr_in6*)&ss;
        out[0] = 6;
        memcpy(out + 1, &s->sin6_port, 2);
        memcpy(out + 3, &s->sin6_addr, 16);
    } else {
        struct sockaddr_in* s = (struct sockaddr_in*)&ss;
        out[0] = 4;
        memcpy(out + 1, &s->sin_port, 2);
        memcpy(out + 3, &s->sin_addr, 4);
    }
    return 0;
}
int64_t obxlib_net_nodelay(int64_t fd, int64_t on) {
    int v = on != 0;
    return setsockopt((int)fd, IPPROTO_TCP, TCP_NODELAY, &v, sizeof v) != 0 ? -errno : 0;
}
/* The outcome of a connect in progress (SO_ERROR): 0 or an errno. */
int64_t obxlib_net_soerror(int64_t fd) {
    int v = 0;
    socklen_t n = sizeof v;
    return getsockopt((int)fd, SOL_SOCKET, SO_ERROR, &v, &n) != 0 ? errno : v;
}
int64_t obxlib_net_shutdown(int64_t fd, int64_t how) { return shutdown((int)fd, (int)how) != 0 ? -errno : 0; }
/* Closing never parks (a socket is closed by its drop, also inside Shared.with). */
void obxlib_net_close(int64_t fd) {
    if (fd < rt_nfds) {
        rt_fds[fd].rd = rt_fds[fd].wr = 0;
        if (rt_fds[fd].reg == 1) rt_nreg--;
        rt_fds[fd].reg = rt_fds[fd].lazy = 0;
    }
    close((int)fd);
}
/* Parks until the socket is readable (dir 0) or writable (1): 0, or 1 when the task is
   cancelled, 3 when its deadline passes (both checked before parking too). */
int64_t obxlib_net_wait(int64_t fd, int64_t dir, const char* w) {
    rt_nopark(w);
    if (rt_cur->cancelled) return 1;
    if (rt_expired()) return 3;
    if (!rt_fds[fd].reg) {
        /* a listener's first wait. EPOLLEXCLUSIVE: of the processes waiting on one listener
           (made before task.workers), one is woken for a connection, not all */
        int r = rt_fd_room(fd);
        if (r < 0) return r;
        struct epoll_event ev = {.events = EPOLLIN | EPOLLET | EPOLLEXCLUSIVE, .data.u64 = (uint64_t)fd};
        if (epoll_ctl(rt_epfd, EPOLL_CTL_ADD, (int)fd, &ev) != 0) {
            ev.events = EPOLLIN | EPOLLET;      /* a kernel without it (before 4.5) */
            if (epoll_ctl(rt_epfd, EPOLL_CTL_ADD, (int)fd, &ev) != 0) return -errno;
        }
        rt_fds[fd].reg = 1;
        rt_nreg++;
    }
    if (dir) rt_fds[fd].wr = rt_cur; else rt_fds[fd].rd = rt_cur;
    rt_nio++;
    int r = rt_park_until(true, rt_cur->deadline, w);
    rt_nio--;
    if (dir) rt_fds[fd].wr = 0; else rt_fds[fd].rd = 0;   /* by index: rt_fds may have moved */
    return r;
}
static int64_t rt_fd_until(int64_t ms, bool cancellable) {
    int64_t at = cancellable ? rt_cur->deadline : RT_NEVER;
    if (ms > 0 && rt_after(ms) < at) at = rt_after(ms);
    return at;
}
/* Parks until the descriptor fd, which a C library made and reads itself (libpq's socket, stage
   C1c), is readable (dir 0), writable (1) or either (2). It is armed one-shot in the epoll set
   each time, so readiness the library left unread is seen, and an idle one is never reported.
   With cancellable, the task's cancellation and deadline end the wait too; with ms >= 0, so do
   ms milliseconds. 0 ready, 1 cancelled, 3 a deadline or ms passed (as obxlib_net_wait), or
   -errno when epoll refuses fd. */
int64_t obxlib_fd_wait(int64_t fd, int64_t dir, int64_t ms, bool cancellable, const char* w) {
    rt_nopark(w);
    if (cancellable && rt_cur->cancelled) return 1;
    if ((cancellable && rt_expired()) || ms == 0) return 3;
    int r = rt_fd_room(fd);
    if (r < 0) return r;
    struct epoll_event ev = {.events = (dir == 1 ? 0 : EPOLLIN | EPOLLRDHUP) | (dir ? EPOLLOUT : 0) | EPOLLONESHOT,
                             .data.u64 = (uint64_t)fd};
    if (epoll_ctl(rt_epfd, EPOLL_CTL_MOD, (int)fd, &ev) != 0 &&
        (errno != ENOENT || epoll_ctl(rt_epfd, EPOLL_CTL_ADD, (int)fd, &ev) != 0)) return -errno;
    if (rt_fds[fd].reg != 1) rt_fds[fd].reg = 2;
    if (dir != 1) rt_fds[fd].rd = rt_cur;
    if (dir != 0) rt_fds[fd].wr = rt_cur;
    rt_nio++;
    r = rt_park_until(cancellable, rt_fd_until(ms, cancellable), w);
    rt_nio--;
    if (rt_fds[fd].rd == rt_cur) rt_fds[fd].rd = 0;
    if (rt_fds[fd].wr == rt_cur) rt_fds[fd].wr = 0;
    return r;
}

/* ---- the helper pool (stage C1c): blocking C calls on helper threads
   A call of a blocking extern (SPEC 12) stores its arguments in slots on the calling task's
   stack and calls rt_blocking with the extern's thunk (bk1_<C name>, generated: it reads the
   slots, calls the C function and stores the result in the last slot). When nothing else could
   run meanwhile (no runnable task, no task waiting for a socket, a timer or another blocking
   call) the thunk runs inline. Otherwise the task parks, not cancellable, and a helper thread
   runs the thunk: the job (on the parked task's stack) is queued under a mutex, a helper takes
   it, runs the thunk with the task's errno, captures errno, puts the job on the done list and
   writes the pool's eventfd, which is in the event loop's epoll set; the scheduler then wakes
   the tasks of the done jobs, and each takes its errno back. A helper runs only C (the thunk
   and the C function): never obx code, the obx allocator, reference counts or task structures.
   The helpers start with the first call that needs one, OBX_HELPERS of them (default 4, at
   most 64; 0: every blocking call runs inline), with every signal blocked, and wait for work
   until the process ends. */
typedef struct rt_job rt_job;
struct rt_job {
    void (*fn)(void*);              /* the thunk and its slots */
    void* a;
    int err;                        /* errno: the task's before the call, the helper's after */
    bool done;
    rt_task* t;
    rt_job* next;
};
static pthread_mutex_t rt_pool_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t rt_pool_cv = PTHREAD_COND_INITIALIZER;
static rt_job *rt_jq, *rt_jq_tail;         /* queued, first in first out (under rt_pool_mu) */
static rt_job* rt_jdone;                   /* finished, the latest first (under rt_pool_mu) */
static int rt_pool_idle;                   /* helpers waiting for a job (under rt_pool_mu) */
static int rt_pool_efd = -1;
static int rt_npool;                       /* helpers started; -1: none (OBX_HELPERS=0) */
/* Spinning (stage C1c): a short C call is done in a few microseconds, while putting a thread
   to sleep and waking it costs tens (55 us a round trip measured on WSL2). So a helper that
   finds no job looks again for RT_SPIN_NS before it sleeps, and the event loop, with no task to
   run but jobs out, looks at the done list for as long before it sleeps in epoll_wait. Neither
   spins when the process may use only one CPU (sched_getaffinity at the pool's start), where it
   would only delay the thread it waits for. A queued job wakes a sleeping helper only when the
   spinning ones are fewer than the jobs queued. */
#define RT_SPIN_NS 50000
static bool rt_pool_spins;
static int rt_pool_spinning;                /* helpers spinning for a job (atomic) */
static int rt_jqn;                          /* jobs queued (under the lock) */
static bool rt_pool_spin(void) {
    if (!rt_pool_spins) return false;
    int64_t t0 = rt_mono();
    for (int k = 1;; k++) {
        if (__atomic_load_n(&rt_jdone, __ATOMIC_ACQUIRE)) return true;
        __builtin_ia32_pause();
        if ((k & 63) == 0 && rt_mono() - t0 > RT_SPIN_NS) return false;
    }
}
static void rt_helper_spin(void) {
    if (!rt_pool_spins) return;
    __atomic_add_fetch(&rt_pool_spinning, 1, __ATOMIC_RELAXED);
    int64_t t0 = rt_mono();
    for (int k = 1;; k++) {
        if (__atomic_load_n(&rt_jq, __ATOMIC_RELAXED)) break;
        __builtin_ia32_pause();
        if ((k & 63) == 0 && rt_mono() - t0 > RT_SPIN_NS) break;
    }
    __atomic_sub_fetch(&rt_pool_spinning, 1, __ATOMIC_RELAXED);
}
static void* rt_helper(void* unused) {
    (void)unused;
    for (;;) {
        rt_helper_spin();
        pthread_mutex_lock(&rt_pool_mu);
        while (!rt_jq) {
            rt_pool_idle++;
            pthread_cond_wait(&rt_pool_cv, &rt_pool_mu);
            rt_pool_idle--;
        }
        rt_job* j = rt_jq;
        __atomic_store_n(&rt_jq, j->next, __ATOMIC_RELAXED);
        if (!rt_jq) rt_jq_tail = 0;
        rt_jqn--;
        pthread_mutex_unlock(&rt_pool_mu);
        errno = j->err;
        j->fn(j->a);
        j->err = errno;
        pthread_mutex_lock(&rt_pool_mu);
        j->next = rt_jdone;                 /* j belongs to its task again from here */
        __atomic_store_n(&rt_jdone, j, __ATOMIC_RELEASE);
        pthread_mutex_unlock(&rt_pool_mu);
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        if (__atomic_load_n(&rt_pool_asleep, __ATOMIC_RELAXED)) {
            uint64_t one = 1;
            if (write(rt_pool_efd, &one, sizeof one)) {}
        }
    }
    return 0;
}
static void rt_pool_start(void) {
    const char* e = getenv("OBX_HELPERS");
    int n = e && *e ? atoi(e) : 4;
    rt_npool = -1;
    if (n <= 0) return;
    if (n > 64) n = 64;
    uint64_t cpus[16] = {0};
    int ncpu = 0;
    if (sched_getaffinity(0, sizeof cpus, (void*)cpus) == 0)
        for (int i = 0; i < 16; i++) ncpu += __builtin_popcountll(cpus[i]);
    rt_pool_spins = ncpu > 1;
    if (rt_fd_room(0) < 0 || (rt_pool_efd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC)) < 0)
        rt_trap(2, "out of memory (the helper pool)", "runtime");
    struct epoll_event ev = {.events = EPOLLIN | EPOLLET, .data.u64 = RT_POOL_TAG};
    if (epoll_ctl(rt_epfd, EPOLL_CTL_ADD, rt_pool_efd, &ev) != 0) rt_trap(2, "out of memory (the helper pool)", "runtime");
    sigset_t all, old;
    sigfillset(&all);
    pthread_sigmask(SIG_SETMASK, &all, &old);
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    int k = 0;
    for (; k < n; k++) {
        pthread_t t;
        if (pthread_create(&t, &at, rt_helper, 0) != 0) break;
    }
    pthread_attr_destroy(&at);
    pthread_sigmask(SIG_SETMASK, &old, 0);
    if (k == 0) rt_trap(2, "out of memory (a helper thread)", "runtime");
    rt_npool = k;
}
/* The event loop saw the eventfd: wake the tasks of the finished jobs, in the order they
   finished. The eventfd is read before the list is taken, so a job finished after that writes
   it again. */
static void rt_pool_drain(bool rd) {
    uint64_t v;
    if (rd && read(rt_pool_efd, &v, sizeof v)) {}
    pthread_mutex_lock(&rt_pool_mu);
    rt_job* d = rt_jdone;
    __atomic_store_n(&rt_jdone, (rt_job*)0, __ATOMIC_RELAXED);
    pthread_mutex_unlock(&rt_pool_mu);
    rt_job* r = 0;
    while (d) { rt_job* nx = d->next; d->next = r; r = d; d = nx; }
    while (r) {
        rt_job* nx = r->next;
        r->done = true;
        rt_wake(r->t);
        r = nx;
    }
}
/* The event loop's look at the pool before epoll_wait for ms (rt_poll, while tasks are parked in
   blocking calls): when it would sleep, it spins for a while first; when it still would, it marks
   itself asleep, so that a helper finishing a job writes the eventfd (and only then: a store of
   the done list and a load of rt_pool_asleep on the helper, a store of rt_pool_asleep and a load
   of the done list here, each pair with a full fence between, so one side sees the other). Then
   wakes the tasks of the jobs done. The new ms: 0 when there were any. */
static int rt_pool_check(int ms) {
    if (ms != 0 && !rt_pool_spin()) {
        __atomic_store_n(&rt_pool_asleep, 1, __ATOMIC_SEQ_CST);
        if (!__atomic_load_n(&rt_jdone, __ATOMIC_SEQ_CST)) return ms;
        __atomic_store_n(&rt_pool_asleep, 0, __ATOMIC_RELAXED);
    }
    if (!__atomic_load_n(&rt_jdone, __ATOMIC_ACQUIRE)) return ms;
    rt_pool_drain(false);
    return 0;
}
/* A call of a blocking extern: the thunk fn on its slots a. w: the call ("file:line"). */
static void rt_blocking(rt_dfn fn, void* a, const char* w) {
    rt_nopark(w);
    if (!rt_rq_head && !rt_nio && !rt_nheap && !rt_njobs) { fn(a); return; }   /* nothing else could run */
    if (!rt_npool) rt_pool_start();
    if (rt_npool < 0) { fn(a); return; }
    rt_job j = {.fn = fn, .a = a, .err = errno, .done = false, .t = rt_cur, .next = 0};
    pthread_mutex_lock(&rt_pool_mu);
    if (rt_jq_tail) rt_jq_tail->next = &j; else __atomic_store_n(&rt_jq, &j, __ATOMIC_RELAXED);
    rt_jq_tail = &j;
    rt_jqn++;
    if (rt_pool_idle && rt_jqn > __atomic_load_n(&rt_pool_spinning, __ATOMIC_RELAXED)) pthread_cond_signal(&rt_pool_cv);
    pthread_mutex_unlock(&rt_pool_mu);
    rt_njobs++;
    while (!j.done) rt_park(false, w);
    rt_njobs--;
    errno = j.err;
}
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

/* ---- signals (stage C2): a task parks until a signal arrives (lib/task.obx's waitSignal)
   A watched signal is blocked on the program's thread (the helper threads block every signal)
   and read from a signalfd in the epoll set, so it arrives as an event of the loop: no handler
   runs in the middle of obx code, and no system call fails with EINTR. Each arrival is given to
   every task waiting for that signal then; with none waiting it is kept (one of each signal, as
   the kernel keeps them) for the next task that waits. Watching cannot be undone. */
static int rt_sigfd = -1;
static uint64_t rt_sig_watched;            /* bit s-1: signal s is watched */
static uint64_t rt_sig_pending;            /* arrived, and given to no task yet */
typedef struct rt_sigw rt_sigw;
struct rt_sigw { rt_task* t; uint64_t mask; int got; rt_sigw* next; };
static rt_sigw* rt_sigws;                  /* the tasks waiting for a signal (on their stacks) */
static void rt_sigset_of(uint64_t m, sigset_t* s) {
    sigemptyset(s);
    for (int i = 0; i < 64; i++)
        if (m >> i & 1) sigaddset(s, i + 1);
}
/* Watches the signals of mask (bit s-1 for signal s): 0, or -errno. */
int64_t obxlib_sig_watch(int64_t mask) {
    uint64_t m = rt_sig_watched | (uint64_t)mask;
    if (m == rt_sig_watched) return 0;
    int r = rt_fd_room(0);
    if (r < 0) return r;
    sigset_t s, add;
    rt_sigset_of(m, &s);
    rt_sigset_of(m & ~rt_sig_watched, &add);
    sigprocmask(SIG_BLOCK, &add, 0);       /* first, so that none arrives unblocked and unread */
    int fd = signalfd(rt_sigfd, &s, SFD_NONBLOCK | SFD_CLOEXEC);
    if (fd >= 0 && rt_sigfd < 0) {
        struct epoll_event ev = {.events = EPOLLIN, .data.u64 = RT_SIG_TAG};
        if (epoll_ctl(rt_epfd, EPOLL_CTL_ADD, fd, &ev) != 0) { int e = errno; close(fd); fd = -1; errno = e; }
        else rt_sigfd = fd;
    }
    if (fd < 0) {
        int e = errno;
        sigprocmask(SIG_UNBLOCK, &add, 0);
        return -e;
    }
    rt_sig_watched = m;
    return 0;
}
/* The event loop saw the signalfd: give each signal read to the tasks waiting for it, or keep it. */
static void rt_sig_read(void) {
    struct signalfd_siginfo si;
    while (read(rt_sigfd, &si, sizeof si) == (ssize_t)sizeof si) {
        uint64_t b = (uint64_t)1 << (si.ssi_signo - 1);
        bool given = false;
        for (rt_sigw* x = rt_sigws; x; x = x->next)
            if ((x->mask & b) && !x->got) { x->got = (int)si.ssi_signo; rt_wake(x->t); given = true; }
        if (!given) rt_sig_pending |= b;
    }
}
/* Parks until one of the watched signals of mask arrives: the signal; -1 when the task is
   cancelled, -3 when its deadline passes. */
int64_t obxlib_sig_wait(int64_t mask, const char* w) {
    rt_nopark(w);
    for (;;) {
        uint64_t hit = rt_sig_pending & (uint64_t)mask;
        if (hit) {
            int s = __builtin_ctzll(hit) + 1;
            rt_sig_pending &= ~((uint64_t)1 << (s - 1));
            return s;
        }
        if (rt_cur->cancelled) return -1;
        if (rt_expired()) return -3;
        rt_sigw me = {.t = rt_cur, .mask = (uint64_t)mask, .got = 0, .next = rt_sigws};
        rt_sigws = &me;
        rt_nio++;
        int r = rt_park_until(true, rt_cur->deadline, w);
        rt_nio--;
        for (rt_sigw** p = &rt_sigws; *p; p = &(*p)->next)
            if (*p == &me) { *p = me.next; break; }
        if (me.got) return me.got;
        if (r == RT_WCANCEL) return -1;
        if (r == RT_WTIMEOUT) return -3;
    }
}

/* ---- worker processes (stage C2: lib/task.obx's workers, a supervisor around fork)
   task.workers forks only from a process with one thread and nothing in its event loop: no
   helper thread (a lock another thread holds at the fork stays held in the child forever), no
   other task (the child would have a copy of each, and the parent blocks while it supervises),
   no descriptor in the epoll set (the child shares the parent's epoll instance; it closes it and
   makes its own) and no signal watch. Otherwise it stops the program with exit 15 at the call.
   The supervisor blocks SIGTERM, SIGINT and SIGCHLD and waits for them with sigtimedwait; each
   child restores the mask it had, asks for SIGTERM when the supervisor dies (PR_SET_PDEATHSIG),
   and checks under OBX_CHECK=1 that the objects it made are freed: the parent's, made before the
   fork, are still there and are not the child's to free. */
static sigset_t rt_sup_old;                /* the supervisor's signal mask before workers */
static int64_t rt_worker_base;             /* in a worker: the live objects at its fork */
static pid_t rt_worker_parent;             /* in a worker: the supervisor's pid; 0 elsewhere */
static void rt_sup_set(sigset_t* s) {
    sigemptyset(s);
    sigaddset(s, SIGTERM);
    sigaddset(s, SIGINT);
    sigaddset(s, SIGCHLD);
}
/* The descriptors in the epoll set: lib/net's sockets, the C libraries' (each probed, since a
   library that closed its descriptor took it out of the set unseen) and the signal watch. */
static int64_t rt_regs(void) {
    int64_t n = rt_nreg + (rt_sigfd >= 0);
    for (int64_t fd = 0; fd < rt_nfds; fd++) {
        if (rt_fds[fd].reg != 2) continue;
        struct epoll_event ev = {.events = 0, .data.u64 = (uint64_t)fd};
        if (rt_epfd >= 0 && epoll_ctl(rt_epfd, EPOLL_CTL_MOD, (int)fd, &ev) == 0) n++;
        else rt_fds[fd].reg = 0;
    }
    return n;
}
/* The checks before the first fork (exit 15 at w), then the supervisor's signal mask. */
void obxlib_workers_begin(const char* w) {
    rt_nopark(w);
    if (rt_npool > 0) rt_trap(15, "task.workers after the helper pool has started (fork is unsafe once threads exist)", w);
    if (rt_ntasks) rt_trap(15, "task.workers while other tasks exist", w);
    if (rt_regs()) rt_trap(15, "task.workers while the event loop holds registrations (a socket in use, or a signal watch)", w);
    sigset_t s;
    rt_sup_set(&s);
    sigprocmask(SIG_BLOCK, &s, &rt_sup_old);
}
/* After the last worker: signals still pending for the supervisor are dropped (they asked for
   the drain that has happened), and the mask is put back. */
void obxlib_workers_end(void) {
    sigset_t s;
    rt_sup_set(&s);
    struct timespec z = {0, 0};
    siginfo_t si;
    while (sigtimedwait(&s, &si, &z) > 0) {}
    sigprocmask(SIG_SETMASK, &rt_sup_old, 0);
}
/* Forks a worker: its pid in the parent, 0 in the child, or -errno. Both output buffers are
   flushed first (the runtime's and C's stdio), so nothing is written twice. */
int64_t obxlib_proc_fork(void) {
    rt_flush();
    fflush(0);
    pid_t parent = getpid();
    pid_t p = fork();
    if (p < 0) return -errno;
    if (p == 0) {
        rt_worker_parent = parent;
        rt_worker_base = rt_live;
        if (rt_epfd >= 0) { close(rt_epfd); rt_epfd = -1; }
        sigprocmask(SIG_SETMASK, &rt_sup_old, 0);
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        if (getppid() != parent) kill(getpid(), SIGTERM);   /* the supervisor died already */
    }
    return p;
}
/* The supervisor's wait, ms milliseconds at most (ms < 0: no limit): a child that ended (its
   pid, and its wait status in *status), -s for SIGTERM or SIGINT (s; the sender's pid in
   *status), or 0 when ms has passed. */
int64_t obxlib_proc_wait(int64_t ms, int64_t* status) {
    int64_t end = ms < 0 ? RT_NEVER : rt_after(ms);
    sigset_t s;
    rt_sup_set(&s);
    for (;;) {
        int st = 0;
        pid_t p = waitpid(-1, &st, WNOHANG);
        if (p > 0) { *status = st; return p; }
        siginfo_t si;
        int r;
        if (end == RT_NEVER) {
            r = sigwaitinfo(&s, &si);
        } else {
            int64_t left = end - rt_mono();
            if (left <= 0) return 0;
            struct timespec ts = {left / 1000000000, left % 1000000000};
            r = sigtimedwait(&s, &si, &ts);
        }
        if (r == SIGTERM || r == SIGINT) { *status = si.si_pid; return -r; }
        if (r < 0 && errno == EAGAIN) return 0;
        /* SIGCHLD, or EINTR: look again */
    }
}
int64_t obxlib_proc_kill(int64_t pid, int64_t sig) { return kill((pid_t)pid, (int)sig) == 0 ? 0 : -errno; }
/* The end of a worker: its output flushed, and under OBX_CHECK=1 its own objects counted. */
void obxlib_worker_exit(int64_t code) {
    if (rt_end_fn) rt_end_fn(0);       /* the globals' drops, as at the end of main */
    rt_flush();
    rt_ended = true;
    if (rt_check && rt_live > rt_worker_base) {
        fprintf(stderr, "obx: leak: %lld live objects at exit at runtime\n", (long long)(rt_live - rt_worker_base));
        exit(5);
    }
    exit((int)code);
}
/* In a worker: SIGTERM to the supervisor, which forwards it to every worker. 0, or -1 when this
   process is not a worker (or its supervisor is gone). */
int64_t obxlib_worker_stop(void) {
    if (!rt_worker_parent || getppid() != rt_worker_parent) return -1;
    return kill(rt_worker_parent, SIGTERM) == 0 ? 0 : -1;
}
