/* The native runtime: ref/rt.h without libc. Freestanding C (raw Linux syscalls), compiled
   by gen_rtx.py with gcc at fixed addresses (rt.ld) and embedded in the compiler as data
   (rtx.obx). Every rt_* function has the observable behaviour of its rt.h namesake: the
   same output buffering, trap messages and exit codes, reference counts, drop actions and
   OBX_CHECK=1 live-object counting. It also provides the small part of libc that lib/
   declares as externs (close, opendir, mkdir, getenv, ...) and the obx_* helpers of rt.h.
   See design/native.md, "Runtime". */

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "obxrt.h"

typedef long i64;
typedef unsigned long u64;

/* ---- syscalls */

static i64 sys6(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f) {
    i64 r;
    register i64 r10 __asm__("r10") = d;
    register i64 r8 __asm__("r8") = e;
    register i64 r9 __asm__("r9") = f;
    __asm__ volatile ("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c), "r"(r10), "r"(r8), "r"(r9)
                      : "rcx", "r11", "memory");
    return r;
}
#define SYS(n, a, b, c) sys6(n, (i64)(a), (i64)(b), (i64)(c), 0, 0, 0)
#define SYS4(n, a, b, c, d) sys6(n, (i64)(a), (i64)(b), (i64)(c), (i64)(d), 0, 0)

enum { S_READ = 0, S_WRITE = 1, S_OPEN = 2, S_CLOSE = 3, S_MMAP = 9, S_MUNMAP = 11, S_RT_SIGACTION = 13,
       S_NANOSLEEP = 35, S_GETPID = 39, S_FORK = 57, S_EXECVE = 59, S_WAIT4 = 61, S_GETCWD = 79,
       S_CHDIR = 80, S_RENAME = 82, S_MKDIR = 83, S_RMDIR = 84, S_UNLINK = 87, S_SYMLINK = 88,
       S_CHMOD = 90, S_GETRLIMIT = 97, S_SIGALTSTACK = 131, S_GETDENTS64 = 217,
       S_CLOCK_GETTIME = 228, S_EXIT_GROUP = 231, S_NEWFSTATAT = 262, S_GETRANDOM = 318 };

static int rt_errno;
/* a syscall result as libc returns it: -1 with errno set on failure */
static i64 sysret(i64 r) {
    if (r < 0 && r > -4096) { rt_errno = (int)-r; return -1; }
    return r;
}
__attribute__((noreturn)) static void sys_exit(int code) {
    for (;;) SYS(S_EXIT_GROUP, code, 0, 0);
}

/* ---- what gcc may call even in freestanding code, and the string basics */

void* memcpy(void* d, const void* s, size_t n) {
    unsigned char* p = d;
    const unsigned char* q = s;
    while (n--) *p++ = *q++;
    return d;
}
void* memmove(void* d, const void* s, size_t n) {
    unsigned char* p = d;
    const unsigned char* q = s;
    if (p < q) { while (n--) *p++ = *q++; }
    else { p += n; q += n; while (n--) *--p = *--q; }
    return d;
}
void* memset(void* d, int c, size_t n) {
    unsigned char* p = d;
    while (n--) *p++ = (unsigned char)c;
    return d;
}
int memcmp(const void* a, const void* b, size_t n) {
    const unsigned char* p = a;
    const unsigned char* q = b;
    for (; n; n--, p++, q++)
        if (*p != *q) return *p < *q ? -1 : 1;
    return 0;
}
void* memchr(const void* s, int c, size_t n) {
    const unsigned char* p = s;
    for (; n; n--, p++)
        if (*p == (unsigned char)c) return (void*)p;
    return 0;
}
size_t strlen(const char* s) {
    size_t n = 0;
    while (s[n]) n++;
    return n;
}
static int str_eqn(const char* a, const char* b, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (a[i] != b[i]) return 0;
    return 1;
}

/* ---- the heap: size classes on mmap'd chunks, one free list per class; a large block is
   its own mapping. Every block has a 16-byte header (its class, or its mapping size), so
   blocks are 16-byte aligned like malloc's. Memory of the small classes is reused, never
   unmapped. */

#define NCLASS 40
static size_t class_size[NCLASS];
static void* free_list[NCLASS];
static char* chunk_at;
static size_t chunk_left;
#define CHUNK (1 << 20)
#define LARGE (1 << 16)

static void* sys_mmap(size_t n) {
    i64 r = sys6(S_MMAP, 0, (i64)n, 3, 0x22, -1, 0);       /* PROT_READ|WRITE, MAP_PRIVATE|ANONYMOUS */
    if (r < 0 && r > -4096) return 0;
    return (void*)r;
}
static void heap_init(void) {
    size_t s = 16;
    for (int i = 0; i < NCLASS; i++) {
        class_size[i] = s;
        s = s < 128 ? s + 16 : s + s / 4;
        s = (s + 15) & ~(size_t)15;
    }
}
static int class_of(size_t n) {
    for (int i = 0; i < NCLASS; i++)
        if (class_size[i] >= n) return i;
    return -1;
}
void* malloc(size_t n) {
    if (!class_size[0]) heap_init();
    size_t need = n ? n : 1;
    if (need > LARGE) {
        size_t m = (need + 16 + 4095) & ~(size_t)4095;
        if (m < need) return 0;
        char* p = sys_mmap(m);
        if (!p) return 0;
        ((size_t*)p)[0] = m;
        ((size_t*)p)[1] = 1;        /* large */
        return p + 16;
    }
    int c = class_of(need);
    void* f = free_list[c];
    if (f) {
        free_list[c] = *(void**)f;
        return f;
    }
    size_t bs = class_size[c] + 16;
    if (chunk_left < bs) {
        char* p = sys_mmap(CHUNK);
        if (!p) return 0;
        chunk_at = p;
        chunk_left = CHUNK;
    }
    char* b = chunk_at;
    chunk_at += bs;
    chunk_left -= bs;
    ((size_t*)b)[0] = (size_t)c;
    ((size_t*)b)[1] = 0;
    return b + 16;
}
static size_t block_size(void* p) {
    size_t* h = (size_t*)((char*)p - 16);
    return h[1] ? h[0] - 16 : class_size[h[0]];
}
void free(void* p) {
    if (!p) return;
    size_t* h = (size_t*)((char*)p - 16);
    if (h[1]) { SYS(S_MUNMAP, h, h[0], 0); return; }
    *(void**)p = free_list[h[0]];
    free_list[h[0]] = p;
}
void* realloc(void* p, size_t n) {
    if (!p) return malloc(n);
    size_t have = block_size(p);
    if (n <= have) return p;
    void* q = malloc(n);
    if (!q) return 0;
    memcpy(q, p, have);
    free(p);
    return q;
}

/* ---- process state, output, traps */

static int64_t rt_live;             /* live objects, counted only when rt_check */
static bool rt_check;               /* OBX_CHECK=1: a leak at exit stops the program (exit 5) */
static int rt_argc;
static char** rt_argv;
static char** rt_environ;           /* the initial environment, or the copy setenv made */
static char rt_obuf[1 << 16];
static size_t rt_olen;

static void write_all(int fd, const char* p, size_t n) {
    while (n > 0) {
        i64 w = SYS(S_WRITE, fd, p, n);
        if (w <= 0) return;
        p += w;
        n -= (size_t)w;
    }
}

void rt_flush(void) {
    size_t off = 0;
    while (off < rt_olen) {
        i64 n = SYS(S_WRITE, 1, rt_obuf + off, rt_olen - off);
        if (n <= 0) break;
        off += (size_t)n;
    }
    rt_olen = 0;
}

/* stderr text, built in one buffer and written at once (as fprintf to unbuffered stderr) */
static char ebuf[4096];
static size_t elen;
static void eput(const char* s, size_t n) {
    for (size_t i = 0; i < n && elen < sizeof ebuf; i++) ebuf[elen++] = s[i];
}
static void eputs(const char* s) { eput(s, strlen(s)); }
static void eputd(int64_t v) {
    char b[24];
    int i = 24;
    uint64_t u = v < 0 ? 0 - (uint64_t)v : (uint64_t)v;
    do b[--i] = (char)('0' + u % 10); while (u /= 10);
    if (v < 0) b[--i] = '-';
    eput(b + i, (size_t)(24 - i));
}
static void eflush(void) { write_all(2, ebuf, elen); elen = 0; }

__attribute__((noreturn)) void rt_trap(int code, const char* msg, const char* where) {
    rt_flush();
    eputs("obx: "); eputs(msg); eputs(" at "); eputs(where); eputs("\n");
    eflush();
    sys_exit(code);
}

/* Not zeroed: every caller stores each byte it later reads. */
void* rt_alloc(size_t n) {
    void* p = malloc(n ? n : 1);
    if (!p) rt_trap(2, "out of memory", "runtime");
    if (rt_check) rt_live++;
    return p;
}
void rt_free(void* p) { free(p); if (rt_check) rt_live--; }

/* Drop actions: drop glue pushes the parts of a value instead of recursing (SPEC 9.4). */
typedef struct { rt_dfn f; void* p; } rt_dact;
static rt_dact* rt_ds;
static int64_t rt_dn, rt_dcap;
void rt_dpush(rt_dfn f, void* p) {
    if (rt_dn == rt_dcap) {
        rt_dcap = rt_dcap ? rt_dcap * 2 : 256;
        rt_ds = realloc(rt_ds, (size_t)rt_dcap * sizeof *rt_ds);
        if (!rt_ds) rt_trap(2, "out of memory", "runtime");
    }
    rt_ds[rt_dn].f = f; rt_ds[rt_dn++].p = p;
}
void rt_drun(rt_dfn f, void* p) {
    int64_t base = rt_dn;
    f(p);
    while (rt_dn > base) { rt_dact a = rt_ds[--rt_dn]; a.f(a.p); }
}
void rt_free_act(void* p) { rt_free(p); }
void rt_ddefer(rt_dfn f, void* p, size_t n) {
    void* q = rt_alloc(n);
    memcpy(q, p, n);
    rt_dpush(rt_free_act, q);
    rt_dpush(f, q);
}

/* ---- environment */

char* getenv(const char* name) {
    size_t n = strlen(name);
    if (!rt_environ) return 0;
    for (char** e = rt_environ; *e; e++)
        if (str_eqn(*e, name, n) && (*e)[n] == '=') return *e + n + 1;
    return 0;
}

static int env_owned;           /* rt_environ is a malloc'd copy */
static void env_own(void) {
    if (env_owned) return;
    size_t n = 0;
    if (rt_environ) while (rt_environ[n]) n++;
    char** e = malloc((n + 1) * sizeof *e);
    if (!e) rt_trap(2, "out of memory", "runtime");
    for (size_t i = 0; i < n; i++) e[i] = rt_environ[i];
    e[n] = 0;
    rt_environ = e;
    env_owned = 1;
}
static int env_name_ok(const char* name) {
    if (!name || !*name) return 0;
    for (const char* p = name; *p; p++)
        if (*p == '=') return 0;
    return 1;
}
int unsetenv(const char* name) {
    if (!env_name_ok(name)) { rt_errno = 22; return -1; }
    env_own();
    size_t n = strlen(name), j = 0;
    for (size_t i = 0; rt_environ[i]; i++)
        if (!(str_eqn(rt_environ[i], name, n) && rt_environ[i][n] == '=')) rt_environ[j++] = rt_environ[i];
    rt_environ[j] = 0;
    return 0;
}
int setenv(const char* name, const char* value, int overwrite) {
    if (!env_name_ok(name)) { rt_errno = 22; return -1; }
    if (getenv(name) && !overwrite) return 0;
    unsetenv(name);
    size_t n = strlen(name), m = strlen(value), k = 0;
    char* s = malloc(n + m + 2);
    if (!s) { rt_errno = 12; return -1; }
    memcpy(s, name, n); s[n] = '='; memcpy(s + n + 1, value, m + 1);
    while (rt_environ[k]) k++;
    char** e = realloc(rt_environ, (k + 2) * sizeof *e);
    if (!e) { rt_errno = 12; return -1; }
    e[k] = s; e[k + 1] = 0;
    rt_environ = e;
    return 0;
}

/* ---- start, stack overflow (SPEC 14, exit 4), finish */

struct k_sigaction { void (*handler)(int, void*, void*); unsigned long flags; void (*restorer)(void);
                     unsigned long mask; };
void rt_n_restore(void);
__asm__(".text\n.globl rt_n_restore\nrt_n_restore:\n\tmov $15, %eax\n\tsyscall\n\thlt\n");

static char* rt_stack_top;
static size_t rt_stack_max;
static void rt_segv(int sig, void* si, void* uc) {
    char* a = *(char**)((char*)si + 16);        /* si_addr */
    (void)uc;
    if (a <= rt_stack_top && (size_t)(rt_stack_top - a) <= rt_stack_max + (1 << 20)) {
        static const char m[] = "obx: stack overflow\n";
        rt_flush();
        SYS(S_WRITE, 2, m, sizeof m - 1);
        sys_exit(4);
    }
    struct k_sigaction sa = {0};         /* any other fault: the default action when it repeats */
    SYS4(S_RT_SIGACTION, sig, &sa, 0, 8);
}

void rt_init(int argc, char** argv) {
    static char alt[1 << 16];
    struct { void* sp; int flags; size_t size; } ss = {alt, 0, sizeof alt};
    struct { unsigned long cur, max; } rl;
    struct k_sigaction sa = {0};
    const char* c = getenv("OBX_CHECK");
    rt_check = c && c[0] == '1';
    rt_argc = argc; rt_argv = argv;
    rt_stack_top = __builtin_frame_address(0);
    rt_stack_max = SYS(S_GETRLIMIT, 3, &rl, 0) == 0 && rl.cur != ~0UL ? rl.cur : (size_t)1 << 30;
    SYS(S_SIGALTSTACK, &ss, 0, 0);
    sa.handler = rt_segv;
    sa.flags = 4 | 0x08000000 | 0x04000000;      /* SA_SIGINFO | SA_ONSTACK | SA_RESTORER */
    sa.restorer = rt_n_restore;
    SYS4(S_RT_SIGACTION, 11, &sa, 0, 8);
}

/* The entry: the native program's _start passes the initial stack pointer and its main. */
void rt_n_start(int64_t* sp, void* mainf) {
    int argc = (int)sp[0];
    char** argv = (char**)(sp + 1);
    rt_environ = argv + argc + 1;
    int r = ((int (*)(int, char**))mainf)(argc, argv);
    sys_exit(r);
}

void rt_finish(void) {
    rt_flush();
    if (rt_check && rt_live != 0) {
        eputs("obx: leak: "); eputd(rt_live); eputs(" live objects at exit at runtime\n");
        eflush();
        sys_exit(5);
    }
}
__attribute__((noreturn)) void rt_exit(int64_t code) { rt_flush(); sys_exit((int)code); }
__attribute__((noreturn)) void exit(int code) { sys_exit(code); }

/* ---- strings (see rt.h) */

#define RT_SP(s) ((const char*)(s).b->data + (s).off)
#define RT_STR_MAX UINT32_MAX
static struct { int64_t rc; char data[1]; } rt_empty = {-1, ""};
obx_str rt_str_new(int64_t len) {
    if (len == 0) return (obx_str){(obx_sblk*)&rt_empty, 0, 0};
    if (len < 0 || len > (int64_t)RT_STR_MAX) rt_trap(2, "string too long (4 GiB or more)", "runtime");
    obx_sblk* b = rt_alloc(sizeof(obx_sblk) + (size_t)len + 1);
    b->rc = 1;
    b->data[len] = 0;
    return (obx_str){b, 0, (uint32_t)len};
}
obx_str rt_str_of(const char* p, int64_t len) {
    obx_str s = rt_str_new(len);
    if (len) memcpy(s.b->data, p, (size_t)len);
    return s;
}
obx_str rt_str_dup(obx_str s) { if (s.b && s.b->rc >= 0) s.b->rc++; return s; }
void rt_str_drop(obx_str s) { if (s.b && s.b->rc > 0 && --s.b->rc == 0) rt_free(s.b); }
bool rt_str_eq(obx_str a, obx_str b) {
    return a.len == b.len && (RT_SP(a) == RT_SP(b) || memcmp(RT_SP(a), RT_SP(b), (size_t)a.len) == 0);
}
int rt_str_cmp(obx_str a, obx_str b) {
    int64_t n = a.len < b.len ? a.len : b.len;
    int c = memcmp(RT_SP(a), RT_SP(b), (size_t)n);
    if (c) return c < 0 ? -1 : 1;
    return a.len < b.len ? -1 : a.len > b.len;
}
obx_str rt_str_concat(obx_str a, obx_str b) {
    if (b.len == 0) return rt_str_dup(a);
    if (a.len == 0) return rt_str_dup(b);
    obx_str s = rt_str_new((int64_t)a.len + b.len);
    memcpy(s.b->data, RT_SP(a), (size_t)a.len);
    memcpy(s.b->data + a.len, RT_SP(b), (size_t)b.len);
    return s;
}
uint8_t rt_str_byte(obx_str s, int64_t i, const char* w) {
    if (i < 0 || i >= s.len) rt_trap(3, "string index out of range", w);
    return (uint8_t)RT_SP(s)[i];
}
obx_str rt_str_sub(obx_str s, int64_t a, int64_t b, const char* w) {
    if (a < 0 || b < a || b > s.len) rt_trap(3, "string slice out of range", w);
    return (obx_str){rt_str_dup(s).b, s.off + (uint32_t)a, (uint32_t)(b - a)};
}
obx_str rt_str_from_bytes(obx_slice b) { return rt_str_of(b.data, b.len); }
obx_str rt_str_from_c(void* p) {
    if (!p) return rt_str_new(0);
    return rt_str_of(p, (int64_t)strlen(p));
}
static char* rt_cdup(obx_str s) {
    if (memchr(RT_SP(s), 0, (size_t)s.len)) return 0;
    char* c = malloc((size_t)s.len + 1);
    if (!c) rt_trap(2, "out of memory", "runtime");
    memcpy(c, RT_SP(s), (size_t)s.len);
    c[s.len] = 0;
    return c;
}
static char** rt_ctmp;
static int64_t rt_nctmp, rt_cctmp;
const char* rt_cstr(obx_str s, const char* w) {
    if (memchr(RT_SP(s), 0, (size_t)s.len)) rt_trap(12, "a string passed to C contains a NUL byte", w);
    if (RT_SP(s)[s.len] == 0) return RT_SP(s);
    if (rt_nctmp == rt_cctmp) {
        rt_cctmp = rt_cctmp ? rt_cctmp * 2 : 8;
        rt_ctmp = realloc(rt_ctmp, (size_t)rt_cctmp * sizeof *rt_ctmp);
        if (!rt_ctmp) rt_trap(2, "out of memory", "runtime");
    }
    return rt_ctmp[rt_nctmp++] = rt_cdup(s);
}
void rt_cfree(void) { while (rt_nctmp) free(rt_ctmp[--rt_nctmp]); }
static char* rt_digits(char* end, u128 v) {
    for (; v > UINT64_MAX; v /= 10) *--end = (char)('0' + (int)(v % 10));
    uint64_t w = (uint64_t)v;
    do *--end = (char)('0' + (int)(w % 10)); while (w /= 10);
    return end;
}
obx_str rt_u128_to_str(u128 v) {
    char buf[48], *p = rt_digits(buf + 48, v);
    return rt_str_of(p, buf + 48 - p);
}
obx_str rt_i128_to_str(i128 v) {
    char buf[48], *p = rt_digits(buf + 48, v < 0 ? (u128)0 - (u128)v : (u128)v);
    if (v < 0) *--p = '-';
    return rt_str_of(p, buf + 48 - p);
}
obx_str rt_int_to_str(int64_t v) { return rt_i128_to_str(v); }
obx_str rt_u64_to_str(uint64_t v) { return rt_u128_to_str(v); }
int64_t rt_write_fd(int64_t fd, obx_str s) {
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
        i64 n = sysret(SYS(S_WRITE, fd, RT_SP(s) + off, (size_t)(s.len - off)));
        if (n <= 0) return -1;
        off += n;
    }
    return off;
}
int64_t rt_read_file(obx_str path, obx_str* out) {
    char* cp = rt_cdup(path);
    if (!cp) return 22;                             /* EINVAL */
    int fd = (int)sysret(SYS(S_OPEN, cp, 0, 0));
    free(cp);
    if (fd < 0) return rt_errno;
    size_t cap = 4096, len = 0;
    char* buf = malloc(cap);
    for (;;) {
        if (len == cap) { cap *= 2; buf = realloc(buf, cap); }
        if (!buf) rt_trap(2, "out of memory", "runtime");
        i64 n = sysret(SYS(S_READ, fd, buf + len, cap - len));
        if (n < 0) { int e = rt_errno; free(buf); SYS(S_CLOSE, fd, 0, 0); return e; }
        if (n == 0) break;
        len += (size_t)n;
    }
    SYS(S_CLOSE, fd, 0, 0);
    obx_str s = rt_str_of(buf, (int64_t)len);
    free(buf);
    rt_str_drop(*out);
    *out = s;
    return 0;
}
bool rt_getenv(obx_str name, obx_str* out) {
    char* cn = rt_cdup(name);
    if (!cn) return false;
    const char* v = getenv(cn);
    free(cn);
    if (!v) return false;
    rt_str_drop(*out);
    *out = rt_str_of(v, (int64_t)strlen(v));
    return true;
}
__attribute__((noreturn)) void rt_panic(obx_str msg, const char* w) {
    rt_flush();
    eputs("obx: panic: "); eput(RT_SP(msg), msg.len); eputs(" at "); eputs(w); eputs("\n");
    eflush();
    sys_exit(12);
}

/* ---- arrays */

int64_t rt_idx(int64_t i, int64_t len, const char* w) {
    if (i < 0 || i >= len) rt_trap(3, "index out of range", w);
    return i;
}
obx_arr rt_arr_new(int64_t n, size_t size) {
    obx_arr a = {0, n, n};
    if (n > 0) a.data = rt_alloc((size_t)n * size);
    return a;
}
obx_arr rt_arr_fill(int64_t n, size_t size, const char* w) {
    if (n < 0) rt_trap(2, "negative array length", w);
    if (n > (int64_t)1 << 40) rt_trap(2, "out of memory", w);
    return rt_arr_new(n, size);
}
void rt_arr_free(obx_arr a) { if (a.data) rt_free(a.data); }
void* rt_arr_push(obx_arr* a, size_t size) {
    if (a->len == a->cap) {
        int64_t nc = a->cap ? a->cap * 2 : 4;
        void* d = realloc(a->data, (size_t)nc * size);
        if (!d) rt_trap(2, "out of memory", "runtime");
        if (!a->data && rt_check) rt_live++;
        a->data = d; a->cap = nc;
    }
    return (char*)a->data + (size_t)(a->len++) * size;
}
void* rt_arr_pop(obx_arr* a, size_t size, const char* w) {
    if (a->len == 0) rt_trap(3, "pop on an empty array", w);
    return (char*)a->data + (size_t)(--a->len) * size;
}
void rt_swap(obx_slice a, int64_t i, int64_t j, size_t size, const char* w) {
    rt_idx(i, a.len, w); rt_idx(j, a.len, w);
    char* p = (char*)a.data + (size_t)i * size;
    char* q = (char*)a.data + (size_t)j * size;
    for (size_t k = 0; k < size; k++) { char t = p[k]; p[k] = q[k]; q[k] = t; }
}
obx_slice rt_as_slice(obx_arr* a) { return (obx_slice){a->data, a->len}; }
obx_slice rt_sub(obx_slice s, int64_t lo, int64_t hi, size_t size, const char* w) {
    if (lo < 0 || hi < lo || hi > s.len) rt_trap(3, "slice range out of bounds", w);
    return (obx_slice){(char*)s.data + (size_t)lo * size, hi - lo};
}
obx_arr rt_str_to_bytes(obx_str s) {
    obx_arr a = rt_arr_new(s.len, 1);
    if (s.len) memcpy(a.data, RT_SP(s), (size_t)s.len);
    return a;
}
obx_arr rt_args(void) {
    obx_arr a = rt_arr_new(rt_argc, sizeof(obx_str));
    for (int i = 0; i < rt_argc; i++)
        ((obx_str*)a.data)[i] = rt_str_of(rt_argv[i], (int64_t)strlen(rt_argv[i]));
    return a;
}

/* ---- integers (the macros of rt.h) */

#define RT_OVF(w) rt_trap(10, "integer overflow", w)
#define RT_INT(N, T, MIN, BITS, SIGNED)                                                     \
T rt_add_##N(T a, T b, const char* w) { T r; if (__builtin_add_overflow(a, b, &r)) RT_OVF(w); return r; } \
T rt_sub_##N(T a, T b, const char* w) { T r; if (__builtin_sub_overflow(a, b, &r)) RT_OVF(w); return r; } \
T rt_mul_##N(T a, T b, const char* w) { T r; if (__builtin_mul_overflow(a, b, &r)) RT_OVF(w); return r; } \
T rt_wadd_##N(T a, T b) { T r; __builtin_add_overflow(a, b, &r); return r; }               \
T rt_wsub_##N(T a, T b) { T r; __builtin_sub_overflow(a, b, &r); return r; }               \
T rt_wmul_##N(T a, T b) { T r; __builtin_mul_overflow(a, b, &r); return r; }               \
T rt_div_##N(T a, T b, const char* w) {                                                     \
    if (b == 0) rt_trap(10, "division by zero", w);                                         \
    if (SIGNED && a == MIN && b == (T)-1) RT_OVF(w);                                        \
    return a / b; }                                                                         \
T rt_rem_##N(T a, T b, const char* w) {                                                     \
    if (b == 0) rt_trap(10, "division by zero", w);                                         \
    if (SIGNED && a == MIN && b == (T)-1) RT_OVF(w);                                        \
    return a % b; }                                                                         \
T rt_neg_##N(T a, const char* w) { T r; if (__builtin_sub_overflow((T)0, a, &r)) RT_OVF(w); return r; } \
T rt_shl_##N(T a, int64_t n, const char* w) {                                               \
    if (n < 0 || n >= BITS) rt_trap(10, "shift count out of range", w);                     \
    return (T)((u128)a << n); }                                                             \
T rt_shr_##N(T a, int64_t n, const char* w) {                                               \
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

#define RT_CV(N, T, LO, HI)                                                                 \
T rt_cvs_##N(i128 v, const char* w) {                                                       \
    if (v < (i128)(LO) || ((HI) >= 0 && (u128)v > (u128)(HI) && v >= 0))                   \
        rt_trap(10, "conversion out of range", w);                                         \
    return (T)v; }                                                                          \
T rt_cvu_##N(u128 v, const char* w) {                                                       \
    if (v > (u128)(HI)) rt_trap(10, "conversion out of range", w);                          \
    return (T)v; }                                                                          \
T rt_cvf_##N(double d, const char* w) {                                                     \
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

/* ---- what the code generator does not inline */

u128 rt_n_udiv128(u128 a, u128 b) { return a / b; }
u128 rt_n_umod128(u128 a, u128 b) { return a % b; }
i128 rt_n_sdiv128(i128 a, i128 b) { return a / b; }
i128 rt_n_smod128(i128 a, i128 b) { return a % b; }
u128 rt_n_mul128(u128 a, u128 b) { return a * b; }
double rt_n_u64_to_double(uint64_t v) { return (double)v; }
double rt_n_i128_to_double(i128 v) { return (double)v; }
double rt_n_u128_to_double(u128 v) { return (double)v; }
i128 rt_n_double_to_i128(double d) { return (i128)d; }
u128 rt_n_double_to_u128(double d) { return (u128)d; }
uint64_t rt_n_double_to_u64(double d) { return (uint64_t)d; }

/* ---- the part of libc that lib/ (and the tests) declare as externs */

#include "strerror.inc"
static char errbuf[32];
char* strerror(int e) {
    if (e >= 0 && e < (int)(sizeof err_text / sizeof err_text[0])) return (char*)err_text[e];
    elen = 0;
    eputs("Unknown error "); eputd(e);
    memcpy(errbuf, ebuf, elen); errbuf[elen] = 0;
    elen = 0;
    return errbuf;
}
int close(int fd) { return (int)sysret(SYS(S_CLOSE, fd, 0, 0)); }
int mkdir(const char* p, unsigned mode) { return (int)sysret(SYS(S_MKDIR, p, mode, 0)); }
int rmdir(const char* p) { return (int)sysret(SYS(S_RMDIR, p, 0, 0)); }
int unlink(const char* p) { return (int)sysret(SYS(S_UNLINK, p, 0, 0)); }
int rename(const char* a, const char* b) { return (int)sysret(SYS(S_RENAME, a, b, 0)); }
int symlink(const char* a, const char* b) { return (int)sysret(SYS(S_SYMLINK, a, b, 0)); }
int chmod(const char* p, unsigned mode) { return (int)sysret(SYS(S_CHMOD, p, mode, 0)); }
int chdir(const char* p) { return (int)sysret(SYS(S_CHDIR, p, 0, 0)); }
int getpid(void) { return (int)SYS(S_GETPID, 0, 0, 0); }
int usleep(unsigned us) {
    struct { i64 s, ns; } t = {us / 1000000, (i64)(us % 1000000) * 1000};
    return (int)sysret(SYS(S_NANOSLEEP, &t, 0, 0));
}
int64_t time(int64_t* t) {
    struct { i64 s, ns; } ts;
    SYS(S_CLOCK_GETTIME, 0, &ts, 0);
    if (t) *t = ts.s;
    return ts.s;
}
int abs(int n) { return n < 0 ? -n : n; }
long labs(long n) { return n < 0 ? -n : n; }
int toupper(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
int atoi(const char* s) {
    while (*s == ' ' || (*s >= '\t' && *s <= '\r')) s++;
    int neg = *s == '-';
    if (*s == '-' || *s == '+') s++;
    long v = 0;
    while (*s >= '0' && *s <= '9') v = v * 10 + (*s++ - '0');
    return (int)(neg ? -v : v);
}
char* strdup(const char* s) {
    size_t n = strlen(s);
    char* d = malloc(n + 1);
    if (d) memcpy(d, s, n + 1);
    return d;
}

/* directories: a DIR is a descriptor and a getdents64 buffer */
typedef struct { int fd; int pos, len; char buf[8192]; } DIR;
DIR* opendir(const char* path) {
    int fd = (int)sysret(SYS(S_OPEN, path, 0x10000 | 0x800 | 0x80000, 0));  /* O_DIRECTORY|O_NONBLOCK|O_CLOEXEC */
    if (fd < 0) return 0;
    DIR* d = malloc(sizeof(DIR));
    if (!d) { SYS(S_CLOSE, fd, 0, 0); rt_errno = 12; return 0; }
    d->fd = fd; d->pos = d->len = 0;
    return d;
}
void* readdir(DIR* d) {
    if (d->pos >= d->len) {
        i64 n = sysret(SYS(S_GETDENTS64, d->fd, d->buf, sizeof d->buf));
        if (n <= 0) return 0;
        d->len = (int)n; d->pos = 0;
    }
    char* e = d->buf + d->pos;
    d->pos += *(unsigned short*)(e + 16);
    return e;
}
int closedir(DIR* d) {
    int r = (int)sysret(SYS(S_CLOSE, d->fd, 0, 0));
    free(d);
    return r;
}

int system(const char* cmd) {
    if (!cmd) return 1;
    i64 pid = SYS(S_FORK, 0, 0, 0);
    if (pid == 0) {
        const char* argv[] = {"sh", "-c", cmd, 0};
        SYS(S_EXECVE, "/bin/sh", argv, rt_environ);
        sys_exit(127);
    }
    if (pid < 0) return -1;
    int st = 0;
    for (;;) {
        i64 r = SYS4(S_WAIT4, pid, &st, 0, 0);
        if (r == pid) return st;
        if (r != -4) return -1;                     /* EINTR: wait again */
    }
}
char* mkdtemp(char* t) {
    size_t n = strlen(t);
    static const char cs[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    if (n < 6 || !str_eqn(t + n - 6, "XXXXXX", 6)) { rt_errno = 22; return 0; }
    for (int tries = 0; tries < 100; tries++) {
        unsigned char r[6];
        if (SYS(S_GETRANDOM, r, 6, 0) != 6) { uint64_t x = (uint64_t)(size_t)&r ^ (uint64_t)tries * 0x9e3779b97f4a7c15ULL; for (int i = 0; i < 6; i++) r[i] = (unsigned char)(x >> (8 * i)); }
        for (int i = 0; i < 6; i++) t[n - 6 + i] = cs[r[i] % 62];
        if (mkdir(t, 0700) == 0) return t;
        if (rt_errno != 17) return 0;               /* EEXIST: try another name */
    }
    return 0;
}

/* ---- the obx_* helpers of rt.h */

int64_t obx_errno(void) { return rt_errno; }
void* obx_dirent_name(void* d) { return (char*)d + 19; }
int64_t obx_dirent_type(void* d) { return *((unsigned char*)d + 18); }

struct kstat { u64 dev, ino, nlink; unsigned mode, uid, gid, pad0; u64 rdev; i64 size, blksize, blocks;
               i64 atime, atime_ns, mtime, mtime_ns, ctime, ctime_ns; i64 unused[3]; };
static struct kstat obx_st;
int64_t obx_stat(const char* path, int64_t follow) {
    int r = (int)sysret(SYS4(S_NEWFSTATAT, -100, path, &obx_st, follow ? 0 : 0x100));
    return r == 0 ? 0 : rt_errno;
}
int64_t obx_stat_field(int64_t i) {
    switch (i) {
    case 0: return (int64_t)obx_st.size;
    case 1: return (int64_t)obx_st.mode;
    case 2: return (int64_t)obx_st.mtime;
    case 3: return (int64_t)obx_st.mtime_ns;
    case 4: return (int64_t)obx_st.nlink;
    case 5: return (int64_t)obx_st.uid;
    case 6: return (int64_t)obx_st.gid;
    case 7: return (int64_t)obx_st.ino;
    case 8: return (int64_t)obx_st.dev;
    case 9: return (int64_t)obx_st.atime;
    case 10: return (int64_t)obx_st.ctime;
    case 11: return (int64_t)obx_st.blocks;
    default: return 0;
    }
}
int64_t obx_open(const char* path, int64_t how) {
    static const int flags[4] = {0, 1 | 0x40 | 0x200, 1 | 0x40 | 0x400, 1 | 0x40 | 0x80};
    if (how < 0 || how > 3) return -22;
    int fd = (int)sysret(SYS(S_OPEN, path, flags[how] | 0x80000, 0666));
    return fd < 0 ? -(int64_t)rt_errno : fd;
}
int64_t obx_clock_ns(int64_t which) {
    struct { i64 s, ns; } t;
    SYS(S_CLOCK_GETTIME, which ? 1 : 0, &t, 0);
    return (int64_t)t.s * 1000000000 + t.ns;
}
void* obx_getcwd(void) {
    static char buf[4096];
    return sysret(SYS(S_GETCWD, buf, sizeof buf, 0)) < 0 ? 0 : buf;
}
