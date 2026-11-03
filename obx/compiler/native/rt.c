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
       S_MREMAP = 25,
       S_NANOSLEEP = 35, S_GETPID = 39, S_FORK = 57, S_EXECVE = 59, S_WAIT4 = 61, S_KILL = 62, S_GETCWD = 79, S_READLINK = 89, S_IOCTL = 16,
       S_CHDIR = 80, S_RENAME = 82, S_MKDIR = 83, S_RMDIR = 84, S_UNLINK = 87, S_SYMLINK = 88,
       S_CHMOD = 90, S_GETRLIMIT = 97, S_SIGALTSTACK = 131, S_GETDENTS64 = 217,
       S_CLOCK_GETTIME = 228, S_EXIT_GROUP = 231, S_NEWFSTATAT = 262, S_GETRANDOM = 318 };

/* In a dynamically linked program (design/native.md, "Dynamic linking") the dynamic linker
   fills these with libc's exit, getenv and __errno_location, so that the runtime ends the
   process, reads the environment and reports errors through libc, as the C backend does; in
   a static program they stay zero and the runtime's own versions serve. */
void* rt_n_libc[3];
static int rt_errno_v;
__attribute__((noinline)) static int* rt_errno_at(void) { return rt_n_libc[2] ? ((int* (*)(void))rt_n_libc[2])() : &rt_errno_v; }
#define rt_errno (*rt_errno_at())
/* a syscall result as libc returns it: -1 with errno set on failure */
static i64 sysret(i64 r) {
    if (r < 0 && r > -4096) { rt_errno = (int)-r; return -1; }
    return r;
}
__attribute__((noreturn)) static void sys_exit(int code) {
    for (;;) SYS(S_EXIT_GROUP, code, 0, 0);
}
/* Set as the program ends: output from then on, by a callback libc calls at exit (SPEC 12), is
   written at once, since nothing flushes the buffer later. */
static int rt_ended;
/* The end of the program (rt.h's exit): with libc, its exit runs the atexit handlers and
   flushes stdio, after the runtime has flushed its own buffer. */
__attribute__((noreturn, noinline)) static void rt_quit(int code) {
    rt_ended = 1;
    if (rt_n_libc[0]) ((void (*)(int))rt_n_libc[0])(code);
    sys_exit(code);
}

/* ---- what gcc may call even in freestanding code, and the string basics */

/* rep movsb / rep stosb past a few words (fast since Ivy Bridge, ERMSB; slow to start) */
void* memcpy(void* d, const void* s, size_t n) {
    void* r = d;
    if (n < 64) {
        unsigned char* p = d;
        const unsigned char* q = s;
        while (n >= 8) { __builtin_memcpy(p, q, 8); p += 8; q += 8; n -= 8; }
        while (n--) *p++ = *q++;
        return r;
    }
    __asm__ volatile("rep movsb" : "+D"(d), "+S"(s), "+c"(n) : : "memory");
    return r;
}
void* memmove(void* d, const void* s, size_t n) {
    unsigned char* p = d;
    const unsigned char* q = s;
    if (p <= q || p >= q + n) return memcpy(d, s, n);
    p += n; q += n; while (n--) *--p = *--q;
    return d;
}
void* memset(void* d, int c, size_t n) {
    void* r = d;
    if (n < 64) {
        unsigned char* p = d;
        while (n--) *p++ = (unsigned char)c;
        return r;
    }
    __asm__ volatile("rep stosb" : "+D"(d), "+c"(n) : "a"(c) : "memory");
    return r;
}
int memcmp(const void* a, const void* b, size_t n) {
    const unsigned char* p = a;
    const unsigned char* q = b;
    for (; n >= 8; n -= 8, p += 8, q += 8) {       /* equal words first, then the bytes */
        uint64_t x, y;
        __builtin_memcpy(&x, p, 8);
        __builtin_memcpy(&y, q, 8);
        if (x != y) break;
    }
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
    if (n <= 128) return n ? (int)((n + 15) >> 4) - 1 : 0;     /* classes 0-7: 16, 32, ..., 128 */
    for (int i = 8; i < NCLASS; i++)
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
    size_t* h = (size_t*)((char*)p - 16);
    if (h[1]) {                     /* a large block grows by moving its pages, not its bytes */
        size_t m = (n + 16 + 4095) & ~(size_t)4095;
        if (m < n) return 0;
        i64 r = sys6(S_MREMAP, (i64)h, (i64)h[0], (i64)m, 1, 0, 0);    /* MREMAP_MAYMOVE */
        if (r < 0 && r > -4096) return 0;
        ((size_t*)r)[0] = m;
        return (char*)r + 16;
    }
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

static int rt_oerr;                 /* the first failed write of rt_obuf (obxlib_flush_out) */
void rt_flush(void) {
    size_t off = 0;
    while (off < rt_olen) {
        i64 n = SYS(S_WRITE, 1, rt_obuf + off, rt_olen - off);
        if (n <= 0) { if (!rt_oerr) rt_oerr = n < 0 ? (int)-n : 5; break; }
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
    rt_quit(code);
}
/* A trap about a Shared.with that is open: "obx: <msg> (the with at <with>) at <where>". */
__attribute__((noreturn)) static void rt_trap_with(int code, const char* msg, const char* with, const char* where) {
    rt_flush();
    eputs("obx: "); eputs(msg); eputs(" (the with at "); eputs(with); eputs(") at "); eputs(where); eputs("\n");
    eflush();
    rt_quit(code);
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
    if (rt_n_libc[1]) return ((char* (*)(const char*))rt_n_libc[1])(name);
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

/* ---- the clock through the vDSO
   The kernel maps a small shared library into every process, the vDSO, whose clock_gettime
   reads the clock in user space (about 20 ns against 250 for the system call). rt_init finds
   it from the auxiliary vector (AT_SYSINFO_EHDR, after the environment on the initial stack),
   reads its dynamic symbol table and looks up __vdso_clock_gettime of version LINUX_2.6, as
   the kernel's tools/testing/selftests/vDSO/parse_vdso.c does. Anything unexpected (no vDSO,
   as with vdso=0; not a 64-bit x86-64 ELF; no dynamic table; the symbol missing; a first call
   that fails) leaves rt_vdso_cg zero, and every read is the system call; OBX_NO_VDSO=1 forces
   that. The vDSO function follows the SysV ABI and needs a few dozen bytes of stack, also on a
   task's stack (entered 16-byte aligned, as every C function). */
typedef struct { i64 s, ns; } rt_ts;
static int (*rt_vdso_cg)(int, rt_ts*);

typedef struct { unsigned char ident[16]; uint16_t type, machine; uint32_t version; u64 entry, phoff, shoff;
                 uint32_t flags; uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx; } rt_ehdr;
typedef struct { uint32_t type, flags; u64 offset, vaddr, paddr, filesz, memsz, align; } rt_phdr;
typedef struct { i64 tag; u64 val; } rt_dyn;
typedef struct { uint32_t name; unsigned char info, other; uint16_t shndx; u64 value, size; } rt_esym;
typedef struct { uint16_t version, flags, ndx, cnt; uint32_t hash, aux, next; } rt_verdef;

static bool rt_vdso_eq(const char* a, const char* b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}
static void* rt_vdso_sym(const char* base, const char* name, const char* ver) {
    const rt_ehdr* e = (const rt_ehdr*)base;
    if (e->ident[0] != 0x7f || e->ident[1] != 'E' || e->ident[2] != 'L' || e->ident[3] != 'F' ||
        e->ident[4] != 2 || e->ident[5] != 1 || e->machine != 62 || e->phentsize != sizeof(rt_phdr))
        return 0;
    const rt_phdr* ph = (const rt_phdr*)(base + e->phoff);
    const rt_dyn* dyn = 0;
    const char* bias = 0;
    bool load = false;
    for (int i = 0; i < e->phnum; i++) {
        if (ph[i].type == 1 && !load) { bias = base + ph[i].offset - ph[i].vaddr; load = true; }   /* PT_LOAD */
        else if (ph[i].type == 2) dyn = (const rt_dyn*)(base + ph[i].offset);                    /* PT_DYNAMIC */
    }
    if (!load || !dyn) return 0;
    const uint32_t *hash = 0, *gnu = 0;
    const rt_esym* syms = 0;
    const char* strs = 0;
    const uint16_t* versym = 0;
    const rt_verdef* verdef = 0;
    for (; dyn->tag != 0; dyn++) {
        const char* p = bias + dyn->val;
        switch (dyn->tag) {
        case 4: hash = (const uint32_t*)p; break;                 /* DT_HASH */
        case 0x6ffffef5: gnu = (const uint32_t*)p; break;         /* DT_GNU_HASH */
        case 5: strs = p; break;                                  /* DT_STRTAB */
        case 6: syms = (const rt_esym*)p; break;                  /* DT_SYMTAB */
        case 0x6ffffff0: versym = (const uint16_t*)p; break;      /* DT_VERSYM */
        case 0x6ffffffc: verdef = (const rt_verdef*)p; break;     /* DT_VERDEF */
        }
    }
    if (!syms || !strs || (!hash && !gnu)) return 0;
    uint32_t n;
    if (hash) n = hash[1];                                        /* nchain: the symbol count */
    else {                                                        /* past the last chain's end */
        uint32_t nb = gnu[0], off = gnu[1], top = 0;
        const uint32_t* bk = (const uint32_t*)((const u64*)(gnu + 4) + gnu[2]);
        for (uint32_t i = 0; i < nb; i++) if (bk[i] > top) top = bk[i];
        if (top < off) n = off;
        else {
            while (!(bk[nb + top - off] & 1)) top++;
            n = top + 1;
        }
    }
    if (n > 65536) return 0;
    for (uint32_t i = 0; i < n; i++) {
        const rt_esym* s = &syms[i];
        if ((s->info & 0xf) != 2 || ((s->info >> 4) != 1 && (s->info >> 4) != 2) || s->shndx == 0 ||
            !rt_vdso_eq(strs + s->name, name))
            continue;                                             /* a global or weak defined function */
        if (versym && verdef) {
            uint16_t want = versym[i] & 0x7fff;
            const rt_verdef* d = verdef;
            for (;;) {
                if (!(d->flags & 1) && (d->ndx & 0x7fff) == want) break;     /* not VER_FLG_BASE */
                if (!d->next) return 0;
                d = (const rt_verdef*)((const char*)d + d->next);
            }
            if (!rt_vdso_eq(strs + *(const uint32_t*)((const char*)d + d->aux), ver)) continue;
        }
        return (void*)(bias + s->value);
    }
    return 0;
}
static void rt_vdso_init(char** envp) {
    const char* off = getenv("OBX_NO_VDSO");
    if (off && off[0] == '1') return;
    while (*envp) envp++;
    const char* base = 0;
    for (u64* av = (u64*)(envp + 1); av[0] != 0; av += 2)            /* AT_NULL ends the vector */
        if (av[0] == 33) base = (const char*)av[1];                  /* AT_SYSINFO_EHDR */
    if (!base) return;
    int (*f)(int, rt_ts*) = (int (*)(int, rt_ts*))rt_vdso_sym(base, "__vdso_clock_gettime", "LINUX_2.6");
    rt_ts t;
    if (f && f(1, &t) == 0) rt_vdso_cg = f;
}
/* clock_gettime(which): through the vDSO when rt_init found it, else the system call */
static void rt_clock(int which, rt_ts* t) {
    if (rt_vdso_cg && rt_vdso_cg(which, t) == 0) return;
    SYS(S_CLOCK_GETTIME, which, t, 0);
}

/* ---- start, stack overflow (SPEC 14, exit 4), finish */

struct k_sigaction { void (*handler)(int, void*, void*); unsigned long flags; void (*restorer)(void);
                     unsigned long mask; };
void rt_n_restore(void);
__asm__(".text\n.globl rt_n_restore\nrt_n_restore:\n\tmov $15, %eax\n\tsyscall\n\thlt\n");

/* Tasks (see the end of this file and rt.h): the root task is main's stack. */
#define RT_STACK (256 * 1024)
#define RT_GUARD 4096
typedef struct rt_task rt_task;
typedef struct rt_scope rt_scope;
struct rt_scope { rt_task* owner; rt_scope* outer; rt_task *tasks, *last; int64_t live; bool closing; };
enum { RT_RUN, RT_PARKED, RT_DONE };
struct rt_task {
    void* sp;
    char* stk;
    void* (*run)(void*);
    void* job;
    void* res;
    void (*resdrop)(void*);
    rt_task* waiter;
    rt_task* next;
    rt_scope* scope;
    rt_task *snext, *sprev;
    rt_scope* scopes;
    int state, refs;
    bool cancelled, cancellable, promise, abandoned, forgotten;
    const char* pw;                 /* while parked: where (the user's call, "file:line") */
    int why;                        /* the event loop's fields: see rt.h */
    int64_t deadline;
    int64_t wake_at, hidx;
    uint64_t tseq;
    int shdrop;
    rt_dact* ds;
    int64_t dn, dcap;
    char** ctmp;
    int64_t nctmp, cctmp;
};
#define RT_NEVER INT64_MAX
static rt_task rt_root = {.refs = 1, .deadline = RT_NEVER};
static rt_task* rt_cur = &rt_root;

static char* rt_stack_top;
static size_t rt_stack_max;
static void rt_segv(int sig, void* si, void* uc) {
    char* a = *(char**)((char*)si + 16);        /* si_addr */
    (void)uc;
    if ((a <= rt_stack_top && (size_t)(rt_stack_top - a) <= rt_stack_max + (1 << 20)) ||
        (rt_cur->stk && a >= rt_cur->stk && a < rt_cur->stk + RT_GUARD)) {
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
    rt_vdso_init(argv + argc + 1);
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
    rt_quit(r);
}

void rt_finish(void) {
    rt_flush();
    rt_ended = 1;
    if (rt_check && rt_live != 0) {
        eputs("obx: leak: "); eputd(rt_live); eputs(" live objects at exit at runtime\n");
        eflush();
        rt_quit(5);
    }
}
__attribute__((noreturn)) void rt_exit(int64_t code) { rt_flush(); rt_quit((int)code); }
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
int64_t rt_cmark(void) { return rt_nctmp; }
static char rt_cempty;
void* rt_cdata(void* p) { return p ? p : &rt_cempty; }
void rt_cfree(int64_t m) { while (rt_nctmp > m) free(rt_ctmp[--rt_nctmp]); }
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

/* ------------------------------------------------------------ floats
   Exact conversions between doubles and decimal text, as glibc's printf("%.*g") and
   strtod (round half to even), with big integers of 32-bit words. */
typedef struct { int n; uint32_t w[240]; } rt_big;
static void big_set(rt_big* a, uint64_t v) { a->n = 0; while (v) { a->w[a->n++] = (uint32_t)v; v >>= 32; } }
static void big_trim(rt_big* a) { while (a->n > 0 && a->w[a->n - 1] == 0) a->n--; }
static void big_mul_small(rt_big* a, uint32_t m) {
    uint64_t c = 0;
    for (int i = 0; i < a->n; i++) { uint64_t t = (uint64_t)a->w[i] * m + c; a->w[i] = (uint32_t)t; c = t >> 32; }
    if (c) a->w[a->n++] = (uint32_t)c;
}
static void big_add_small(rt_big* a, uint32_t d) {
    uint64_t c = d;
    for (int i = 0; i < a->n && c; i++) { uint64_t t = (uint64_t)a->w[i] + c; a->w[i] = (uint32_t)t; c = t >> 32; }
    if (c) a->w[a->n++] = (uint32_t)c;
}
static void big_pow5(rt_big* a, int k) {
    for (; k >= 13; k -= 13) big_mul_small(a, 1220703125u);
    uint32_t m = 1;
    while (k-- > 0) m *= 5;
    big_mul_small(a, m);
}
static int big_bits(const rt_big* a) { return a->n ? 32 * (a->n - 1) + 32 - __builtin_clz(a->w[a->n - 1]) : 0; }
static int big_bit(const rt_big* a, int i) { return i / 32 < a->n ? (a->w[i / 32] >> (i % 32)) & 1 : 0; }
static void big_shl(rt_big* out, const rt_big* a, int s) {
    int ws = s / 32, bs = s % 32;
    if (!a->n) { out->n = 0; return; }
    out->n = a->n + ws + 1;
    for (int i = 0; i < out->n; i++) out->w[i] = 0;
    for (int i = 0; i < a->n; i++) {
        uint64_t v = (uint64_t)a->w[i] << bs;
        out->w[i + ws] |= (uint32_t)v;
        out->w[i + ws + 1] |= (uint32_t)(v >> 32);
    }
    big_trim(out);
}
static int big_cmp(const rt_big* a, const rt_big* b) {
    if (a->n != b->n) return a->n < b->n ? -1 : 1;
    for (int i = a->n - 1; i >= 0; i--) if (a->w[i] != b->w[i]) return a->w[i] < b->w[i] ? -1 : 1;
    return 0;
}
static void big_sub(rt_big* a, const rt_big* b) {     /* a -= b, a >= b */
    int64_t br = 0;
    for (int i = 0; i < a->n; i++) {
        int64_t t = (int64_t)a->w[i] - (i < b->n ? b->w[i] : 0) - br;
        br = t < 0;
        a->w[i] = (uint32_t)(t + (br ? ((int64_t)1 << 32) : 0));
    }
    big_trim(a);
}
static uint32_t big_div_small(rt_big* a, uint32_t d) {
    uint64_t r = 0;
    for (int i = a->n - 1; i >= 0; i--) { uint64_t t = (r << 32) | a->w[i]; a->w[i] = (uint32_t)(t / d); r = t % d; }
    big_trim(a);
    return (uint32_t)r;
}
/* The top 64 bits of a as q, with *drop the bits below them and *sticky whether any is set. */
static uint64_t big_top(const rt_big* a, int* drop, int* sticky) {
    int L = big_bits(a), lo = L > 64 ? L - 64 : 0;
    uint64_t q = 0;
    for (int i = L - 1; i >= lo; i--) q = q << 1 | (uint64_t)big_bit(a, i);
    *sticky = 0;
    for (int i = 0; i < lo / 32 && i < a->n; i++) if (a->w[i]) *sticky = 1;
    for (int i = lo / 32 * 32; i < lo; i++) if (big_bit(a, i)) *sticky = 1;
    *drop = lo;
    return q;
}
/* The double nearest (q + a fraction, nonzero when sticky) * 2^e2. */
static uint64_t flt_round(uint64_t q, int sticky, int e2, int neg) {
    uint64_t sign = (uint64_t)neg << 63;
    if (q == 0) return sign;
    int L = 64 - __builtin_clzll(q);
    int exp = L - 1 + e2;
    int drop = exp >= -1022 ? L - 53 : -1074 - e2;
    uint64_t m;
    if (drop <= 0) {
        m = q << -drop;
    } else if (drop > 64) {
        m = 0;
    } else {
        uint64_t rest = drop == 64 ? q : q & (((uint64_t)1 << drop) - 1);
        uint64_t half = (uint64_t)1 << (drop - 1);
        m = drop == 64 ? 0 : q >> drop;
        if (rest > half || (rest == half && (sticky || (m & 1)))) m++;
    }
    int lsb = e2 + drop;
    if (m >= ((uint64_t)1 << 53)) { m >>= 1; lsb++; }
    if (m >= ((uint64_t)1 << 52)) {
        int biased = lsb + 52 + 1023;
        if (biased >= 2047) return sign | 0x7ff0000000000000ULL;
        return sign | (uint64_t)biased << 52 | (m & 0xfffffffffffffULL);
    }
    return sign | m;
}
static double flt_of_bits(uint64_t b) { union { uint64_t b; double d; } u; u.b = b; return u.d; }
static uint64_t flt_bits(double d) { union { uint64_t b; double d; } u; u.d = d; return u.b; }
static int flt_lower(char c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int flt_word(const char* p, const char* e, const char* w) {
    int n = 0;
    for (; w[n]; n++) if (p + n >= e || flt_lower(p[n]) != w[n]) return 0;
    return n;
}
static int flt_hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    c = (char)flt_lower(c);
    return c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}
/* strtod over [p, e): the end of the longest prefix that is a number (p when none). */
static const char* flt_parse(const char* p, const char* e, double* out) {
    const char* s = p;
    while (s < e && (*s == ' ' || (*s >= '\t' && *s <= '\r'))) s++;
    int neg = 0;
    if (s < e && (*s == '+' || *s == '-')) neg = *s++ == '-';
    int n;
    if ((n = flt_word(s, e, "infinity")) || (n = flt_word(s, e, "inf"))) {
        *out = flt_of_bits((uint64_t)neg << 63 | 0x7ff0000000000000ULL);
        return s + n;
    }
    if ((n = flt_word(s, e, "nan"))) {
        s += n;
        const char* t = s;
        if (t < e && *t == '(') {
            t++;
            while (t < e && (flt_hex(*t) >= 0 || (*t >= 'g' && *t <= 'z') || (*t >= 'G' && *t <= 'Z') || *t == '_' ||
                             (*t >= '0' && *t <= '9'))) t++;
            if (t < e && *t == ')') s = t + 1;
        }
        *out = flt_of_bits((uint64_t)neg << 63 | 0x7ff8000000000000ULL);
        return s;
    }
    static rt_big m, d, r, t;
    big_set(&m, 0);
    int digits = 0, k = 0, any = 0;
    if (e - s >= 3 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X') &&
        (flt_hex(s[2]) >= 0 || (s[2] == '.' && e - s >= 4 && flt_hex(s[3]) >= 0))) {
        s += 2;
        int frac = 0;
        for (; s < e; s++) {
            if (*s == '.' && !frac) { frac = 1; continue; }
            int h = flt_hex(*s);
            if (h < 0) break;
            if (m.n || h) { big_mul_small(&m, 16); big_add_small(&m, (uint32_t)h); }
            if (frac) k -= 4;
        }
        if (s < e && (*s == 'p' || *s == 'P')) {
            const char* q = s + 1;
            int en = 0;
            if (q < e && (*q == '+' || *q == '-')) en = *q++ == '-';
            if (q < e && *q >= '0' && *q <= '9') {
                int x = 0;
                for (; q < e && *q >= '0' && *q <= '9'; q++) if (x < 100000) x = x * 10 + (*q - '0');
                k += en ? -x : x;
                s = q;
            }
        }
        int drop, sticky;
        uint64_t q = big_top(&m, &drop, &sticky);
        if (q && drop + k + big_bits(&m) - drop > 1100) { *out = flt_of_bits((uint64_t)neg << 63 | 0x7ff0000000000000ULL); return s; }
        if (q && drop + k + 64 < -1200) { *out = flt_of_bits((uint64_t)neg << 63); return s; }
        *out = flt_of_bits(flt_round(q, sticky, drop + k, neg));
        return s;
    }
    int frac = 0;
    for (; s < e; s++) {
        if (*s == '.' && !frac) { frac = 1; continue; }
        if (*s < '0' || *s > '9') break;
        any = 1;
        if (m.n || *s != '0') { big_mul_small(&m, 10); big_add_small(&m, (uint32_t)(*s - '0')); digits++; }
        if (frac) k--;
    }
    if (!any) return p;
    if (s < e && (*s == 'e' || *s == 'E')) {
        const char* q = s + 1;
        int en = 0;
        if (q < e && (*q == '+' || *q == '-')) en = *q++ == '-';
        if (q < e && *q >= '0' && *q <= '9') {
            int x = 0;
            for (; q < e && *q >= '0' && *q <= '9'; q++) if (x < 100000) x = x * 10 + (*q - '0');
            k += en ? -x : x;
            s = q;
        }
    }
    if (!m.n) { *out = flt_of_bits((uint64_t)neg << 63); return s; }
    if (digits + k > 310) { *out = flt_of_bits((uint64_t)neg << 63 | 0x7ff0000000000000ULL); return s; }
    if (digits + k < -330) { *out = flt_of_bits((uint64_t)neg << 63); return s; }
    int drop, sticky;
    uint64_t q;
    if (k >= 0) {
        big_pow5(&m, k);
        q = big_top(&m, &drop, &sticky);
        *out = flt_of_bits(flt_round(q, sticky, drop + k, neg));
        return s;
    }
    big_set(&d, 1);
    big_pow5(&d, -k);
    int sh = big_bits(&d) - big_bits(&m) + 63;
    if (sh >= 0) { big_shl(&r, &m, sh); } else { r = m; t = d; big_shl(&d, &t, -sh); }
    q = 0;
    for (int i = 63; i >= 0; i--) {
        big_shl(&t, &d, i);
        if (big_cmp(&r, &t) >= 0) { big_sub(&r, &t); q |= (uint64_t)1 << i; }
    }
    *out = flt_of_bits(flt_round(q, r.n != 0, k - sh, neg));
    return s;
}
bool rt_parse_float(obx_str s, double* out) {
    if (s.len == 0 || s.len > 400 || memchr(RT_SP(s), 0, (size_t)s.len)) return false;
    double d;
    if (flt_parse(RT_SP(s), RT_SP(s) + s.len, &d) != RT_SP(s) + s.len) return false;
    *out = d;
    return true;
}
/* printf("%.*g", p, d) into b. */
static int flt_fmt(double d, int p, char* b) {
    uint64_t bits = flt_bits(d);
    int neg = (int)(bits >> 63), n = 0;
    int ex = (int)(bits >> 52 & 0x7ff);
    uint64_t mant = bits & 0xfffffffffffffULL;
    if (neg) b[n++] = '-';
    if (ex == 0x7ff) { const char* w = mant ? "nan" : "inf"; for (int i = 0; i < 3; i++) b[n++] = w[i]; return n; }
    if (ex == 0 && mant == 0) { b[n++] = '0'; return n; }
    uint64_t m = ex ? mant | (uint64_t)1 << 52 : mant;
    int e2 = ex ? ex - 1075 : -1074;
    static rt_big a, t;
    static char dg[800];
    big_set(&a, m);
    int dexp = 0;
    if (e2 >= 0) { t = a; big_shl(&a, &t, e2); } else { big_pow5(&a, -e2); dexp = e2; }
    /* the decimal digits of a, most significant first */
    int nd = 0;
    char rev[800];
    while (a.n) {
        uint32_t c = big_div_small(&a, 1000000000u);
        for (int i = 0; i < 9; i++) { rev[nd++] = (char)('0' + c % 10); c /= 10; }
    }
    while (nd > 1 && rev[nd - 1] == '0') nd--;
    for (int i = 0; i < nd; i++) dg[i] = rev[nd - 1 - i];
    int X = nd - 1 + dexp;
    if (nd > p) {
        int up = 0;
        if (dg[p] > '5') up = 1;
        else if (dg[p] == '5') {
            int rest = 0;
            for (int i = p + 1; i < nd; i++) if (dg[i] != '0') rest = 1;
            up = rest || ((dg[p - 1] - '0') & 1);
        }
        nd = p;
        if (up) {
            int i = p - 1;
            while (i >= 0 && dg[i] == '9') dg[i--] = '0';
            if (i >= 0) dg[i]++;
            else { dg[0] = '1'; for (int j = 1; j < p; j++) dg[j] = '0'; X++; }
        }
    }
    while (nd > 1 && dg[nd - 1] == '0') nd--;
    if (X < -4 || X >= p) {
        b[n++] = dg[0];
        if (nd > 1) { b[n++] = '.'; for (int i = 1; i < nd; i++) b[n++] = dg[i]; }
        b[n++] = 'e';
        b[n++] = X < 0 ? '-' : '+';
        int x = X < 0 ? -X : X;
        if (x >= 100) b[n++] = (char)('0' + x / 100);
        b[n++] = (char)('0' + x / 10 % 10);
        b[n++] = (char)('0' + x % 10);
    } else if (X >= 0) {
        for (int i = 0; i <= X; i++) b[n++] = i < nd ? dg[i] : '0';
        if (nd > X + 1) { b[n++] = '.'; for (int i = X + 1; i < nd; i++) b[n++] = dg[i]; }
    } else {
        b[n++] = '0';
        b[n++] = '.';
        for (int i = 0; i < -X - 1; i++) b[n++] = '0';
        for (int i = 0; i < nd; i++) b[n++] = dg[i];
    }
    return n;
}
obx_str rt_float_to_str(double d) {
    char buf[64];
    int n = 0;
    for (int p = 15; p <= 17; p++) {
        n = flt_fmt(d, p, buf);
        double back = 0;
        flt_parse(buf, buf + n, &back);
        if (back == d || d != d) break;
    }
    return rt_str_of(buf, n);
}
int64_t rt_write_fd(int64_t fd, obx_str s) {
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
    rt_quit(12);
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
    rt_ts ts;
    rt_clock(0, &ts);
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
int64_t obxlib_flush_out(void) {
    rt_flush();
    int e = rt_oerr;
    rt_oerr = 0;
    return e;
}
static char obxlib_link[4097];
int64_t obxlib_readlink(const char* path) {
    i64 n = SYS(S_READLINK, path, obxlib_link, sizeof obxlib_link - 1);
    if (n < 0) return n;
    obxlib_link[n] = 0;
    return n;
}
void* obxlib_readlink_text(void) { return obxlib_link; }
int64_t obxlib_isatty(int64_t fd) {
    char t[64];                                     /* struct termios */
    return SYS(S_IOCTL, fd, 0x5401, t) == 0;        /* TCGETS */
}
int64_t obxlib_term_cols(int64_t fd) {
    unsigned short w[4];                            /* struct winsize: rows, cols, xpixel, ypixel */
    if (SYS(S_IOCTL, fd, 0x5413, w) != 0 || w[0] == 0 || w[1] == 0) return 0;   /* TIOCGWINSZ */
    return w[1];
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
int64_t obx_clock_ns(int64_t which) {
    rt_ts t;
    rt_clock(which ? 1 : 0, &t);
    return (int64_t)t.s * 1000000000 + t.ns;
}
void* obx_getcwd(void) {
    static char buf[4096];
    return sysret(SYS(S_GETCWD, buf, sizeof buf, 0)) < 0 ? 0 : buf;
}

/* ------------------------------------------------------------ the exact part of libm
   The functions of std's libm externs whose result is exact (correctly rounded), so that
   they match glibc bit for bit. The others (pow, exp, log, sin, ...) need the real libm
   (dynamic linking, a later phase). */
static double m_of(uint64_t b) { union { uint64_t b; double d; } u; u.b = b; return u.d; }
static uint64_t m_bits(double d) { union { uint64_t b; double d; } u; u.d = d; return u.b; }
double fabs(double x) { return m_of(m_bits(x) & ~((uint64_t)1 << 63)); }
double copysign(double x, double y) {
    return m_of((m_bits(x) & ~((uint64_t)1 << 63)) | (m_bits(y) & ((uint64_t)1 << 63)));
}
double sqrt(double x) {
    double r;
    __asm__("sqrtsd %1, %0" : "=x"(r) : "x"(x));
    return r;
}
double trunc(double x) {
    uint64_t b = m_bits(x);
    int e = (int)(b >> 52 & 0x7ff) - 1023;
    if (e >= 52) return x + 0.0 == x ? x : x + x;     /* integral, inf, or nan (quieted) */
    if (e < 0) return m_of(b & ((uint64_t)1 << 63));
    return m_of(b & ~(((uint64_t)1 << (52 - e)) - 1));
}
double floor(double x) {
    double t = trunc(x);
    return t > x ? t - 1.0 : t;
}
double ceil(double x) {
    double t = trunc(x);
    if (t < x) t += 1.0;
    return t == 0 ? copysign(0.0, x) : t;
}
double round(double x) {
    double t = trunc(x);
    if (fabs(x - t) >= 0.5) t += copysign(1.0, x);
    return t;
}
double rint(double x) {
    if (!(fabs(x) < 4503599627370496.0)) return x + 0.0 == x ? x : x + x;
    volatile double big = x < 0 ? -4503599627370496.0 : 4503599627370496.0;
    double r = (x + big) - big;
    return r == 0 ? copysign(0.0, x) : r;
}
double fmod(double x, double y) {
    uint64_t ux = m_bits(x), uy = m_bits(y), i;
    int ex = (int)(ux >> 52 & 0x7ff), ey = (int)(uy >> 52 & 0x7ff), sx = (int)(ux >> 63);
    if (uy << 1 == 0 || y != y || ex == 0x7ff) return (x * y) / (x * y);
    if (ux << 1 <= uy << 1) return ux << 1 == uy << 1 ? 0 * x : x;
    if (!ex) {
        for (i = ux << 12; i >> 63 == 0; ex--, i <<= 1) {}
        ux <<= -ex + 1;
    } else {
        ux &= (uint64_t)-1 >> 12;
        ux |= (uint64_t)1 << 52;
    }
    if (!ey) {
        for (i = uy << 12; i >> 63 == 0; ey--, i <<= 1) {}
        uy <<= -ey + 1;
    } else {
        uy &= (uint64_t)-1 >> 12;
        uy |= (uint64_t)1 << 52;
    }
    for (; ex > ey; ex--) {
        i = ux - uy;
        if (i >> 63 == 0) {
            if (i == 0) return 0 * x;
            ux = i;
        }
        ux <<= 1;
    }
    i = ux - uy;
    if (i >> 63 == 0) {
        if (i == 0) return 0 * x;
        ux = i;
    }
    for (; ux >> 52 == 0; ux <<= 1, ex--) {}
    if (ex > 0) {
        ux -= (uint64_t)1 << 52;
        ux |= (uint64_t)ex << 52;
    } else {
        ux >>= -ex + 1;
    }
    return m_of(ux | (uint64_t)sx << 63);
}

/* ------------------------------------------------------------ more of libc
   C functions that programs declare with `extern "c"` and that need no dynamic linking. */
int strcmp(const char* a, const char* b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
int strncmp(const char* a, const char* b, size_t n) {
    for (; n; n--, a++, b++) if (*a != *b || !*a) return (unsigned char)*a - (unsigned char)*b;
    return 0;
}
char* strchr(const char* s, int c) {
    for (;; s++) { if (*s == (char)c) return (char*)s; if (!*s) return 0; }
}
char* strrchr(const char* s, int c) {
    const char* r = 0;
    for (;; s++) { if (*s == (char)c) r = s; if (!*s) return (char*)r; }
}
char* strstr(const char* h, const char* n) {
    size_t k = strlen(n);
    for (; *h; h++) if (!strncmp(h, n, k)) return (char*)h;
    return k ? 0 : (char*)h;
}
__attribute__((noreturn)) void abort(void) {
    struct { void* h; unsigned long flags; void* restorer; unsigned long mask; } sa = {0, 0, 0, 0};
    SYS4(S_RT_SIGACTION, 6, &sa, 0, 8);            /* SIGABRT back to the default: terminate */
    unsigned long set = 1ul << 5;
    SYS4(14, 1, &set, 0, 8);                        /* rt_sigprocmask: unblock it */
    SYS(S_KILL, SYS(S_GETPID, 0, 0, 0), 6, 0);
    sys_exit(127);
}

/* ------------------------------------------------------------ tasks
   rt.h's tasks (lib/task.obx; design/concurrency.md, stage C1a), the same code without ASan:
   stackful coroutines on one thread, a FIFO run queue, every suspension through rt_park, the
   drop-action stack and the C string copies per task, a deadlock when nothing is runnable
   (exit 14), parking inside Shared.with (exit 13). */

void rt_ctx_swap(void** save, void* to);
__asm__(".text\n.globl rt_ctx_swap\n.type rt_ctx_swap,@function\nrt_ctx_swap:\n"
        "\tpushq %rbp\n\tpushq %rbx\n\tpushq %r12\n\tpushq %r13\n\tpushq %r14\n\tpushq %r15\n"
        "\tsubq $8, %rsp\n\tstmxcsr (%rsp)\n\tfnstcw 4(%rsp)\n\tmovq %rsp, (%rdi)\n\tmovq %rsi, %rsp\n"
        "\tldmxcsr (%rsp)\n\tfldcw 4(%rsp)\n\taddq $8, %rsp\n"
        "\tpopq %r15\n\tpopq %r14\n\tpopq %r13\n\tpopq %r12\n\tpopq %rbx\n\tpopq %rbp\n\tret\n"
        ".size rt_ctx_swap, .-rt_ctx_swap\n");

static rt_task *rt_rq_head, *rt_rq_tail;
static rt_task* rt_dead;
typedef struct rt_shared rt_shared;
static rt_shared* rt_with_top;             /* the innermost open Shared.with (no task may park) */
static char* rt_stk_free;
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
        return s;
    }
    i64 r = sys6(S_MMAP, 0, RT_STACK + RT_GUARD, 3, 0x4022, -1, 0);    /* MAP_PRIVATE|ANONYMOUS|NORESERVE */
    if (r < 0 && r > -4096) rt_trap(2, "out of memory (a task's stack)", "runtime");
    s = (char*)r;
    SYS(10, s, RT_GUARD, 0);                    /* mprotect: PROT_NONE */
    return s;
}
static void rt_stack_put(char* s) {
    if (rt_nstk_free >= 64) { SYS(S_MUNMAP, s, RT_STACK + RT_GUARD, 0); return; }
    *(char**)(s + RT_GUARD) = rt_stk_free;
    rt_stk_free = s;
    rt_nstk_free++;
}
static void rt_release(rt_task* t) { if (--t->refs == 0) rt_free(t); }
static void rt_reap(rt_task* t) {
    rt_stack_put(t->stk);
    free(t->ds);
    free(t->ctmp);
    rt_release(t);
}
static void rt_resumed(void) {
    if (rt_dead) { rt_task* d = rt_dead; rt_dead = 0; rt_reap(d); }
}
static void rt_switch(rt_task* to) {
    rt_task* me = rt_cur;
    me->ds = rt_ds; me->dn = rt_dn; me->dcap = rt_dcap;
    me->ctmp = rt_ctmp; me->nctmp = rt_nctmp; me->cctmp = rt_cctmp;
    rt_ds = to->ds; rt_dn = to->dn; rt_dcap = to->dcap;
    rt_ctmp = to->ctmp; rt_nctmp = to->nctmp; rt_cctmp = to->cctmp;
    rt_cur = to;
    rt_ctx_swap(&me->sp, to->sp);
    rt_resumed();
}
static void rt_wake(rt_task* t) {
    if (t->state == RT_PARKED) { t->state = RT_RUN; rt_enqueue(t); }
}

/* ---- the event loop (stage C1b): rt.h's timers (a heap of parked tasks by deadline) and
   sockets (registered with epoll, edge-triggered, when made), with raw system calls */
enum { S_SOCKET = 41, S_CONNECT = 42, S_SENDTO = 44, S_SHUTDOWN = 48, S_BIND = 49, S_LISTEN = 50,
       S_GETSOCKNAME = 51, S_GETPEERNAME = 52, S_SETSOCKOPT = 54, S_GETSOCKOPT = 55, S_EPOLL_WAIT = 232,
       S_EPOLL_CTL = 233, S_ACCEPT4 = 288, S_EPOLL_CREATE1 = 291 };
enum { RT_WOK = 0, RT_WCANCEL = 1, RT_WTIMEOUT = 3 };
struct k_epoll_event { uint32_t events; uint64_t data; } __attribute__((packed));
#define K_EPOLLIN 0x001
#define K_EPOLLOUT 0x004
#define K_EPOLLERR 0x008
#define K_EPOLLHUP 0x010
#define K_EPOLLRDHUP 0x2000
#define K_EPOLLET (1u << 31)
#define K_EAGAIN 11
#define K_EINPROGRESS 115
#define K_O_NONBLOCK 0x800
#define K_O_CLOEXEC 0x80000
typedef struct { rt_task *rd, *wr; } rt_fdw;
static rt_fdw* rt_fds;
static int64_t rt_nfds;
static int rt_epfd = -1;
static int64_t rt_nio;
static rt_task** rt_heap;
static int64_t rt_nheap, rt_cheap;
static uint64_t rt_tseq;
static unsigned rt_ticks;

static int64_t rt_mono(void) {
    rt_ts t;
    rt_clock(1, &t);
    return (int64_t)t.s * 1000000000 + t.ns;
}
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
static void rt_poll(bool block, const char* w) {
    struct k_epoll_event ev[256];
    for (;;) {
        int64_t wait = block && !rt_rq_head ? -1 : 0;
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
        if (wait < 0 && !rt_nio) rt_trap(14, "deadlock: every task is parked", w);
        if (rt_nio) {
            int ms = wait < 0 ? -1 : wait > (int64_t)INT32_MAX * 1000000 ? INT32_MAX
                   : (int)(wait / 1000000 + (wait % 1000000 != 0));
            i64 n = SYS4(S_EPOLL_WAIT, rt_epfd, ev, 256, ms);
            for (i64 i = 0; i < n; i++) {
                int64_t fd = (int64_t)ev[i].data;
                uint32_t e = ev[i].events;
                if (fd >= rt_nfds) continue;
                if (rt_fds[fd].rd && (e & (K_EPOLLIN | K_EPOLLRDHUP | K_EPOLLHUP | K_EPOLLERR))) rt_wake(rt_fds[fd].rd);
                if (rt_fds[fd].wr && (e & (K_EPOLLOUT | K_EPOLLHUP | K_EPOLLERR))) rt_wake(rt_fds[fd].wr);
            }
        } else if (wait > 0) {
            struct { i64 s, ns; } d = {wait / 1000000000, wait % 1000000000};
            SYS(S_NANOSLEEP, &d, 0, 0);
        }
        if (!block || rt_rq_head) return;
    }
}
static rt_task* rt_next(const char* w) {
    if ((rt_nio | rt_nheap) && (++rt_ticks & 63) == 0) rt_poll(false, w);
    rt_task* t = rt_dequeue();
    if (!t) { rt_poll(true, w); t = rt_dequeue(); }
    return t;
}
static int rt_park_until(bool cancellable, int64_t at, const char* w) {
    rt_task* me = rt_cur;
    me->pw = w;
    me->state = RT_PARKED;
    me->cancellable = cancellable;
    me->why = RT_WOK;
    if (at != RT_NEVER) rt_timer_add(me, at);
    rt_task* nx = rt_next(w);
    if (nx != me) rt_switch(nx);
    if (me->hidx) rt_timer_del(me);
    me->cancellable = false;
    return cancellable && me->cancelled ? RT_WCANCEL : me->why;
}
static void rt_park(bool cancellable, const char* w) { rt_park_until(cancellable, RT_NEVER, w); }
static void rt_nopark(const char* w);
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
    while (t->forgotten && r) { void* x = r; r = 0; t->resdrop(x); }
    t->res = r;
    t->state = RT_DONE;
    rt_scope* s = t->scope;
    if (t->sprev) t->sprev->snext = t->snext; else s->tasks = t->snext;
    if (t->snext) t->snext->sprev = t->sprev; else s->last = t->sprev;
    t->scope = 0;
    if (--s->live == 0 && s->closing) rt_wake(s->owner);
    if (t->waiter) rt_wake(t->waiter);
    rt_task* nx = rt_next(s->owner->pw ? s->owner->pw : "runtime");
    rt_dead = t;
    rt_switch(nx);
    __builtin_unreachable();
}

void* obxlib_task_scope_open(void) {
    rt_scope* s = rt_alloc(sizeof *s);
    s->owner = rt_cur; s->outer = rt_cur->scopes; s->tasks = s->last = 0; s->live = 0; s->closing = false;
    rt_cur->scopes = s;
    return s;
}
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
    uint64_t* top = (uint64_t*)(t->stk + RT_GUARD + RT_STACK);
    uint32_t mx;
    uint16_t cw;
    __asm__ volatile("stmxcsr %0" : "=m"(mx));
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    *--top = 0;
    *--top = (uint64_t)(uintptr_t)rt_task_boot;
    for (int i = 0; i < 6; i++) *--top = 0;
    *--top = (uint64_t)mx | (uint64_t)cw << 32;
    t->sp = top;
    rt_enqueue(t);
    return t;
}
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
void* obxlib_task_take(void* p) {
    rt_task* t = p;
    void* r = t->res;
    t->res = 0;
    rt_release(t);
    return r;
}
int64_t obxlib_task_yield(const char* w) {
    rt_nopark(w);
    if ((rt_nio | rt_nheap) && (++rt_ticks & 63) == 0) rt_poll(false, w);
    if (rt_rq_head) {
        rt_enqueue(rt_cur);
        rt_switch(rt_dequeue());
    }
    return rt_cur->cancelled ? 1 : rt_expired() ? 3 : 0;
}
int64_t obxlib_task_cancelled(void) { return rt_cur->cancelled; }
int64_t obxlib_task_check(const char* w) {
    rt_nopark(w);
    return rt_cur->cancelled ? 1 : rt_expired() ? 3 : 0;
}
int64_t obxlib_task_sleep(int64_t ms, const char* w) {
    int64_t c = obxlib_task_check(w);
    if (c || ms <= 0) return c;
    int64_t at = rt_after(ms), dl = rt_cur->deadline;
    int r = rt_park_until(true, at < dl ? at : dl, w);
    return r == RT_WTIMEOUT && at <= dl ? 0 : r;
}
int64_t obxlib_task_within(int64_t ms) {
    int64_t old = rt_cur->deadline, at = rt_after(ms);
    if (at < old) rt_cur->deadline = at;
    return old;
}
void obxlib_task_restore(int64_t old) { rt_cur->deadline = old; }

/* ---- sockets (rt.h's, with raw system calls; results are counts or descriptors, or -errno) */
static int64_t rt_net_new(i64 fd) {
    if (rt_epfd < 0) {
        i64 e = SYS(S_EPOLL_CREATE1, K_O_CLOEXEC, 0, 0);
        if (e < 0) { SYS(S_CLOSE, fd, 0, 0); return e; }
        rt_epfd = (int)e;
    }
    if (fd >= rt_nfds) {
        int64_t n = rt_nfds ? rt_nfds : 64;
        while (n <= fd) n *= 2;
        rt_fdw* f = realloc(rt_fds, (size_t)n * sizeof *rt_fds);
        if (!f) rt_trap(2, "out of memory", "runtime");
        memset(f + rt_nfds, 0, (size_t)(n - rt_nfds) * sizeof *f);
        rt_fds = f;
        rt_nfds = n;
    }
    struct k_epoll_event ev = {K_EPOLLIN | K_EPOLLOUT | K_EPOLLRDHUP | K_EPOLLET, (uint64_t)fd};
    i64 r = SYS4(S_EPOLL_CTL, rt_epfd, 1, fd, &ev);             /* EPOLL_CTL_ADD */
    if (r < 0) { SYS(S_CLOSE, fd, 0, 0); return r; }
    return fd;
}
/* struct sockaddr_in or sockaddr_in6 in ss (28 bytes); its length */
static int rt_net_sa(int64_t fam, const uint8_t* a, int64_t port, unsigned char* ss) {
    memset(ss, 0, 28);
    ss[0] = fam == 6 ? 10 : 2;                                   /* AF_INET6, AF_INET */
    ss[2] = (unsigned char)(port >> 8);
    ss[3] = (unsigned char)port;
    if (fam == 6) { memcpy(ss + 8, a, 16); return 28; }
    memcpy(ss + 4, a, 4);
    return 16;
}
int64_t obxlib_net_listen(int64_t fam, const uint8_t* a, int64_t port, int64_t backlog) {
    unsigned char ss[28];
    int n = rt_net_sa(fam, a, port, ss);
    i64 fd = SYS(S_SOCKET, ss[0], 1 | K_O_NONBLOCK | K_O_CLOEXEC, 0);  /* SOCK_STREAM */
    if (fd < 0) return fd;
    int one = 1;
    i64 r = sys6(S_SETSOCKOPT, fd, 1, 2, (i64)&one, sizeof one, 0);    /* SOL_SOCKET, SO_REUSEADDR */
    if (r >= 0) r = SYS(S_BIND, fd, ss, n);
    if (r >= 0) r = SYS(S_LISTEN, fd, backlog, 0);
    if (r < 0) { SYS(S_CLOSE, fd, 0, 0); return r; }
    return rt_net_new(fd);
}
int64_t obxlib_net_connect(int64_t fam, const uint8_t* a, int64_t port, int64_t* pending) {
    unsigned char ss[28];
    int n = rt_net_sa(fam, a, port, ss);
    i64 fd = SYS(S_SOCKET, ss[0], 1 | K_O_NONBLOCK | K_O_CLOEXEC, 0);
    if (fd < 0) return fd;
    *pending = 0;
    i64 r = SYS(S_CONNECT, fd, ss, n);
    if (r < 0) {
        if (r != -K_EINPROGRESS) { SYS(S_CLOSE, fd, 0, 0); return r; }
        *pending = 1;
    }
    return rt_net_new(fd);
}
int64_t obxlib_net_accept(int64_t fd) {
    i64 c = SYS4(S_ACCEPT4, fd, 0, 0, K_O_NONBLOCK | K_O_CLOEXEC);
    return c < 0 ? c : rt_net_new(c);
}
int64_t obxlib_net_read(int64_t fd, void* b, int64_t n) { return SYS(S_READ, fd, b, n); }
int64_t obxlib_net_write(int64_t fd, const void* b, int64_t n) {
    return sys6(S_SENDTO, fd, (i64)b, n, 0x4000, 0, 0);          /* MSG_NOSIGNAL */
}
int64_t obxlib_net_addr(int64_t fd, int64_t peer, uint8_t* out) {
    unsigned char ss[128];
    int n = sizeof ss;
    i64 r = SYS(peer ? S_GETPEERNAME : S_GETSOCKNAME, fd, ss, &n);
    if (r < 0) return r;
    memset(out, 0, 19);
    out[1] = ss[2];
    out[2] = ss[3];
    if (ss[0] == 10) { out[0] = 6; memcpy(out + 3, ss + 8, 16); }
    else { out[0] = 4; memcpy(out + 3, ss + 4, 4); }
    return 0;
}
int64_t obxlib_net_nodelay(int64_t fd, int64_t on) {
    int v = on != 0;
    i64 r = sys6(S_SETSOCKOPT, fd, 6, 1, (i64)&v, sizeof v, 0);   /* IPPROTO_TCP, TCP_NODELAY */
    return r < 0 ? r : 0;
}
int64_t obxlib_net_soerror(int64_t fd) {
    int v = 0, n = sizeof v;
    i64 r = sys6(S_GETSOCKOPT, fd, 1, 4, (i64)&v, (i64)&n, 0);   /* SOL_SOCKET, SO_ERROR */
    return r < 0 ? -r : v;
}
int64_t obxlib_net_shutdown(int64_t fd, int64_t how) {
    i64 r = SYS(S_SHUTDOWN, fd, how, 0);
    return r < 0 ? r : 0;
}
void obxlib_net_close(int64_t fd) {
    if (fd < rt_nfds) rt_fds[fd].rd = rt_fds[fd].wr = 0;
    SYS(S_CLOSE, fd, 0, 0);
}
int64_t obxlib_net_wait(int64_t fd, int64_t dir, const char* w) {
    rt_nopark(w);
    if (rt_cur->cancelled) return 1;
    if (rt_expired()) return 3;
    if (dir) rt_fds[fd].wr = rt_cur; else rt_fds[fd].rd = rt_cur;
    rt_nio++;
    int r = rt_park_until(true, rt_cur->deadline, w);
    rt_nio--;
    if (dir) rt_fds[fd].wr = 0; else rt_fds[fd].rd = 0;
    return r;
}
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
void rt_sh_dup(void* p) { if (p) ((rt_shared*)p)->rc++; }
static void rt_sh_free(rt_shared* s) {
    void* b = s->box;
    void (*d)(void*) = s->drop;
    rt_free(s);
    if (rt_cur->shdrop) { rt_dpush(d, b); return; }
    rt_cur->shdrop++;
    rt_drun(d, b);
    rt_cur->shdrop--;
}
void rt_sh_drop(void* p) {
    rt_shared* s = p;
    if (s && --s->rc == 0 && !s->busy) rt_sh_free(s);
}
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
