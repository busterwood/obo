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
    fprintf(stderr, "obx: %s at %s\n", msg, where);
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
    if (rt_check && rt_live != 0) {
        fprintf(stderr, "obx: leak: %lld live objects at exit at runtime\n", (long long)rt_live);
        exit(5);
    }
}
__attribute__((noreturn)) static void rt_exit(int64_t code) { rt_flush(); exit((int)code); }

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
    if (fd == 1) {
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
/* C memory for the database adapters (lib/sqlite, lib/postgres): a buffer from the runtime's
   allocator (counted by OBX_CHECK), a NUL-terminated copy of a str, NULL, and byte, int32 and
   pointer access by index, for what a C API takes or returns as a pointer to memory */
void* obxlib_null(void) { return 0; }
void* obxlib_alloc(int64_t n) { return rt_alloc(n > 0 ? (size_t)n : 1); }
void obxlib_free(void* p) { if (p) rt_free(p); }
void* obxlib_cstr(const char* s) {
    size_t n = strlen(s);
    char* p = rt_alloc(n + 1);
    memcpy(p, s, n + 1);
    return p;
}
int64_t obxlib_peek(const void* p, int64_t i) { return ((const uint8_t*)p)[i]; }
void obxlib_poke(void* p, int64_t i, int64_t v) { ((uint8_t*)p)[i] = (uint8_t)v; }
void obxlib_poke_i32(void* p, int64_t i, int64_t v) { ((int32_t*)p)[i] = (int32_t)v; }
void obxlib_poke_ptr(void* p, int64_t i, void* q) { ((void**)p)[i] = q; }
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
