/* obx runtime: output, strings, allocation and a mark-sweep garbage collector.
 * Freestanding: no libc, talks to Linux directly through syscalls.
 * Heap scanning is precise (type descriptors); stack and globals are scanned
 * conservatively. The compiler pushes every live register, including the
 * arguments, before any runtime call, so all roots are on the obx stack when
 * a collection runs; the runtime's own frames are deliberately not scanned.
 * No runtime routine may allocate twice while holding the first result only
 * in a C variable. */

typedef unsigned long u64;
typedef long i64;

static i64 sys3(i64 n, i64 a, i64 b, i64 c) {
    i64 r;
    __asm__ volatile ("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory");
    return r;
}
/* Four arguments: the fourth goes in r10, which "syscall" does not take from
 * rcx (it clobbers rcx with the return address). Only newfstatat needs this. */
static i64 sys4(i64 n, i64 a, i64 b, i64 c, i64 d) {
    i64 r;
    register i64 r10 __asm__("r10") = d;
    __asm__ volatile ("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c), "r"(r10) : "rcx", "r11", "memory");
    return r;
}
static void *sys_mmap(u64 len) {
    i64 r;
    register i64 r10 __asm__("r10") = 0x22 | 0x4000; /* MAP_PRIVATE|MAP_ANONYMOUS|MAP_NORESERVE */
    register i64 r8 __asm__("r8") = -1;
    register i64 r9 __asm__("r9") = 0;
    __asm__ volatile ("syscall" : "=a"(r) : "a"(9), "D"(0), "S"(len), "d"(3), "r"(r10), "r"(r8), "r"(r9)
                      : "rcx", "r11", "memory");
    return (void *)r;
}

void *memset(void *d, int c, u64 n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
/* Zero whole 8-byte words; object sizes are always multiples of 16. */
static void zero_words(u64 *p, u64 n) {
    if (n >= 32) { __asm__ volatile ("rep stosq" : "+D"(p), "+c"(n) : "a"(0) : "memory"); return; }
    while (n--) *p++ = 0;
}
void *memcpy(void *d, const void *s, u64 n) { unsigned char *p = d; const unsigned char *q = s; while (n--) *p++ = *q++; return d; }

/* ---------- output ---------- */
static char obuf[65536];
static u64 olen;
static i64 out_fd = 1;
static void flush(void) { u64 off = 0; while (off < olen) { i64 w = sys3(1, out_fd, (i64)(obuf + off), (i64)(olen - off)); if (w <= 0) break; off += w; } olen = 0; }
static void putb(const char *s, u64 n) { while (n--) { if (olen == sizeof obuf) flush(); obuf[olen++] = *s++; } }
static void puts_(const char *s) { u64 n = 0; while (s[n]) n++; putb(s, n); }
static void put_int(i64 v) {
    char b[24]; int i = 24; u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) b[--i] = '-';
    putb(b + i, 24 - i);
}
static void die_code(const char *msg, i64 code) { flush(); u64 n = 0; while (msg[n]) n++; sys3(1, 2, (i64)msg, n); sys3(60, code, 0, 0); for (;;); }
static void die(const char *msg) { die_code(msg, 2); }

/* ---------- heap ---------- */
/* Object: 16-byte header [kind-or-descriptor][size|mark], payload follows.
 * kind 1 = block of 8-byte scalars, 2 = block of pointers, 3 = string,
 * 4 = block of bytes, 5 = array header, otherwise a pointer to a record
 * descriptor: [payload size][pointer count][offsets...].
 *
 * An array is a header {len, cap, data, element size} whose data points to a
 * block [count][elements...] holding cap elements. Growing replaces the block,
 * so every reference to the array sees the change. Elements past len are kept
 * zero. Strings are a single object [len][bytes].
 *
 * A Result cell (the I/O builtins build these) is a record whose header's low
 * 32 bits are the variant descriptor: the ok variant has the payload word at
 * +0; the shared err variant has the message (an owned string, 0 = "") at +0
 * and the positive errno at +8. The compiler passes both descriptors to each
 * I/O helper, and match compares the header tag against the cached variants,
 * so the helpers must use exactly the descriptor they are given. */
#define HEAP_SIZE (1UL << 32)
#define GRAN 16
#define NCLASS 65
#define MARK 1UL

static u64 heap_start, heap_top, heap_end;
static unsigned char *starts;          /* one bit per granule: an object header starts here */
static u64 *free_small[NCLASS];
static u64 *free_large;
static u64 *mstack; static u64 msp, mcap;
static u64 stack_base, glob_start, glob_end;
u64 env_base;                          /* the initial stack's envp, for getenv */
u64 rt_entry_sp;                       /* set by the entry stubs in entry.S */
static int gc_poison;                  /* debugging modes, from OBX_GC=poison|stress */
#define POISON 0xDEADBEEFDEADBEEFUL
static u64 since_gc, live_bytes, threshold = 8UL << 20;
static u64 tmp_root[2];                /* objects held by a runtime routine across an allocation */
static u64 n_collections, n_freed_total, total_alloc;
/* obo: explicit ownership. Every non-string object is counted while live. */
static u64 live_owned, live_bytes_now, peak_bytes;
static int leak_check;
static int stats;                  /* OBO_STATS=1: report memory at exit */

/* Incremental I/O: the fd table and the test-only fault hook. fdstate is 0 for a
 * descriptor openFile never saw (0/1/2, inherited, negative or >= 1024), 1 for one
 * it opened, and 2 once a close was attempted. The fault hook is inert unless
 * OBO_CHECK=1, so a normal program cannot see it. */
static unsigned char fdstate[1024];
static int io_fault_eintr, io_fault_short;
static int fault_read, fault_write, fault_close;
/* The metadata fault hook is the same kind of test-only injection: one synthetic
 * EINTR per call kind, inert unless OBO_CHECK=1. */
static int meta_fault_eintr;
static int fault_dir, fault_stat, fault_lstat, fault_link, fault_tty;

static inline u64 gidx(u64 h) { return (h - heap_start) / GRAN; }
static inline int  is_start(u64 h) { u64 g = gidx(h); return starts[g >> 3] >> (g & 7) & 1; }
static inline void set_start(u64 h) { u64 g = gidx(h); starts[g >> 3] |= (unsigned char)(1 << (g & 7)); }
static inline void clr_start(u64 h) { u64 g = gidx(h); starts[g >> 3] &= (unsigned char)~(1 << (g & 7)); }

static int starts_with(const char *s, const char *p) { while (*p) if (*s++ != *p++) return 0; return 1; }

void rt_init(u64 sbase, u64 gstart, u64 gend) {
    stack_base = sbase; glob_start = gstart; glob_end = gend;
    /* the initial stack holds argc, argv[], NULL, envp[], NULL */
    u64 argc = *(u64 *)sbase;
    env_base = sbase + 8 * (argc + 2);
    for (char **env = (char **)env_base; *env; env++) {
        if (starts_with(*env, "OBX_GC=poison")) gc_poison = 1;
        if (starts_with(*env, "OBX_GC=stress")) gc_poison = 1;
        if (starts_with(*env, "OBO_CHECK=1")) leak_check = 1;
        if (starts_with(*env, "OBO_STATS=1")) stats = 1;
        if (starts_with(*env, "OBO_IO_FAULT=eintr")) io_fault_eintr = 1;
        if (starts_with(*env, "OBO_IO_FAULT=short")) io_fault_short = 1;
        if (starts_with(*env, "OBO_META_FAULT=eintr")) meta_fault_eintr = 1;
    }
    /* the environment order is arbitrary: gate the hook after the whole loop */
    if (!leak_check) { io_fault_eintr = 0; io_fault_short = 0; meta_fault_eintr = 0; }
    if (io_fault_eintr) { fault_read = fault_write = fault_close = 1; }
    if (meta_fault_eintr) { fault_dir = fault_stat = fault_lstat = fault_link = fault_tty = 1; }
    heap_start = (u64)sys_mmap(HEAP_SIZE);
    if ((i64)heap_start < 0) die("obx: cannot reserve heap\n");
    heap_top = heap_start; heap_end = heap_start + HEAP_SIZE;
    starts = sys_mmap(HEAP_SIZE / GRAN / 8);
    mcap = 1UL << 26; mstack = sys_mmap(mcap * 8);
}

static void mark_ptr(u64 w) {
    if (w < heap_start + GRAN || w >= heap_top || (w & (GRAN - 1))) return;
    u64 h = w - GRAN;
    if (!is_start(h)) return;
    u64 *hdr = (u64 *)h;
    if (hdr[1] & MARK) return;
    hdr[1] |= MARK;
    if (msp == mcap) die("obx: mark stack overflow\n");
    mstack[msp++] = h;
}

static void scan_range(u64 lo, u64 hi) {
    for (u64 p = lo & ~7UL; p + 8 <= hi; p += 8) mark_ptr(*(u64 *)p);
}

static void scan_object(u64 h) {
    u64 *hdr = (u64 *)h, kind = hdr[0];
    u64 *body = (u64 *)(h + GRAN);
    if (kind == 1 || kind == 3 || kind == 4) return;
    if (kind == 2) { u64 n = body[0]; for (u64 i = 0; i < n; i++) mark_ptr(body[1 + i]); return; }
    if (kind == 5) { mark_ptr(body[2]); return; }
    u64 *desc = (u64 *)kind;
    for (u64 i = 0; i < desc[1]; i++) mark_ptr(*(u64 *)((u64)body + desc[2 + i]));
}

static void sweep(void) {
    for (int i = 0; i < NCLASS; i++) free_small[i] = 0;
    free_large = 0;
    live_bytes = 0;
    for (u64 h = heap_start; h < heap_top; ) {
        u64 *hdr = (u64 *)h, size = hdr[1] & ~(GRAN - 1);
        if (is_start(h)) {
            if (hdr[1] & MARK) { hdr[1] &= ~MARK; live_bytes += size; h += size; continue; }
            clr_start(h); n_freed_total++;
            if (gc_poison) for (u64 *w = (u64 *)(h + 2 * GRAN - 8); (u64)w < h + size; w++) *w = POISON;
        }
        u64 g = size / GRAN;
        u64 *link = (u64 *)(h + GRAN);
        if (g < NCLASS) { link[0] = (u64)free_small[g]; free_small[g] = (u64 *)h; }
        else            { link[0] = (u64)free_large;    free_large = (u64 *)h; }
        h += size;
    }
}

void rt_collect(void) {
    return;                            /* obo: no collector */
    u64 sp = rt_entry_sp;
    msp = 0;
    scan_range(sp, stack_base);
    scan_range(glob_start, glob_end);
    mark_ptr(tmp_root[0]); mark_ptr(tmp_root[1]);
    while (msp) scan_object(mstack[--msp]);
    sweep();
    n_collections++;
    since_gc = 0;
    threshold = live_bytes * 2 > (8UL << 20) ? live_bytes * 2 : (8UL << 20);
}

static u64 alloc_raw(u64 kind, u64 payload) {
    u64 size = (GRAN + payload + GRAN - 1) & ~(GRAN - 1);
    /* obo has no collector: memory comes back through rt_free */
    u64 g = size / GRAN, h = 0;
    if (g < NCLASS && free_small[g]) { h = (u64)free_small[g]; free_small[g] = (u64 *)((u64 *)(h + GRAN))[0]; }
    else if (g >= NCLASS) {
        u64 **pp = &free_large;
        while (*pp) {
            u64 *b = *pp;
            if ((b[1] & ~(GRAN - 1)) == size) { *pp = (u64 *)b[2]; h = (u64)b; break; }
            pp = (u64 **)&b[2];
        }
    }
    u64 gen = 0;
    if (!h) {
        if (heap_top + size > heap_end) die("obx: out of memory\n");
        h = heap_top; heap_top += size;
    } else {
        gen = ((u64 *)h)[0] >> 32;                         /* a reused block keeps counting */
    }
    zero_words((u64 *)h, size / 8);
    u64 *hdr = (u64 *)h;
    hdr[0] = kind | gen << 32; hdr[1] = size;
    set_start(h);
    total_alloc += size;
    live_owned++;
    live_bytes_now += size;
    if (live_bytes_now > peak_bytes) peak_bytes = live_bytes_now;
    return h + GRAN;
}

u64 rt_alloc_record(u64 *desc) { return alloc_raw((u64)desc, desc[0] ? desc[0] : 8); }

typedef struct { u64 len, cap, data, esize; } Array;

/* Returns one block to its free list, poisoning it in debug mode. */
#define KIND(h) (((u64 *)(h))[0] & 0xFFFFFFFFUL)   /* the high half holds the block's generation */

static void free_block(u64 h) {
    u64 *hdr = (u64 *)h, size = hdr[1] & 0xFFFFFFF0UL;      /* high bits: a string's reference count */
    clr_start(h);
    hdr[0] = ((hdr[0] >> 32) + 1) << 32;                    /* a new generation: old handles go stale */
    live_owned--;
    live_bytes_now -= size;
    n_freed_total++;
    hdr[1] = size;
    if (gc_poison) for (u64 *w = (u64 *)(h + 2 * GRAN - 8); (u64)w < h + size; w++) *w = POISON;
    u64 g = size / GRAN;
    u64 *link = (u64 *)(h + GRAN);
    if (g < NCLASS) { link[0] = (u64)free_small[g]; free_small[g] = (u64 *)h; }
    else            { link[0] = (u64)free_large;    free_large = (u64 *)h; }
}

/* Strings are reference counted: the high 32 bits of the header's size word
 * hold the count minus one, so a fresh string (count 1) needs no update.
 * Literals live in read-only data and null is "", so both are ignored. */
static int heap_string(u64 s) { return s >= heap_start + GRAN && s < heap_top; }

u64 rt_str_inc(u64 s) {
    if (heap_string(s)) ((u64 *)(s - GRAN))[1] += 1UL << 32;
    return s;
}

void rt_str_dec(u64 s) {
    if (!heap_string(s)) return;
    u64 h = s - GRAN, *hdr = (u64 *)h;
    if (!is_start(h) || KIND(h) != 3) die_code("obo: double free or invalid free (string)\n", 6);
    if (hdr[1] >> 32) hdr[1] -= 1UL << 32;
    else free_block(h);
}

/* Frees an object and everything it owns. Iterative, so long lists don't
 * overflow the stack. Every pointer stored in an owned object's owning fields,
 * or in a block of references, is owning (strings are not freed in M1). */
void rt_free(u64 p) {
    if (!p) return;
    msp = 0;
    mstack[msp++] = p;
    while (msp) {
        u64 q = mstack[--msp];
        if (q < heap_start + GRAN || q >= heap_top || (q & (GRAN - 1)) || !is_start(q - GRAN))
            die_code("obo: double free or invalid free\n", 6);
        u64 h = q - GRAN, kind = KIND(h);
        u64 *body = (u64 *)q;
        if (kind == 3) die_code("obo: internal error: freeing a string\n", 6);
        if (kind == 5) {
            if (body[2]) mstack[msp++] = body[2];
        } else if (kind == 2) {
            for (u64 i = 0; i < body[0]; i++) if (body[1 + i]) mstack[msp++] = body[1 + i];
        } else if (kind == 6) {
            for (u64 i = 0; i < body[0]; i++) rt_str_dec(body[1 + i]);
        } else if (kind != 1 && kind != 4) {
            /* descriptor: [size][n owned][offsets...][n strings][offsets...] */
            u64 *desc = (u64 *)kind, n = desc[1];
            for (u64 i = 0; i < n; i++) {
                u64 v = *(u64 *)((u64)body + desc[2 + i]);
                if (v) mstack[msp++] = v;
            }
            for (u64 i = 0; i < desc[2 + n]; i++) rt_str_dec(*(u64 *)((u64)body + desc[3 + n + i]));
        }
        if (msp + 1024 >= mcap) die_code("obo: object graph too deep to free\n", 6);
        free_block(h);
    }
}

/* Handles: the object's offset in the heap (in 16-byte units) and, in the
 * high half, the generation of its block when the handle was taken. 0 is nil. */
u64 rt_handle(u64 p) {
    if (!p) return 0;
    return ((p - heap_start) / GRAN) | (((u64 *)(p - GRAN))[0] >> 32) << 32;
}

static u64 handle_target(u64 hd) {
    u64 off = hd & 0xFFFFFFFFUL, p = heap_start + off * GRAN;
    if (!hd || p < heap_start + GRAN || p >= heap_top || !is_start(p - GRAN)) return 0;
    if ((((u64 *)(p - GRAN))[0] >> 32) != hd >> 32) return 0;
    return p;
}

/* the object a handle names, or a trap if it has been freed */
u64 rt_deref(u64 hd) {
    u64 p = handle_target(hd);
    if (!p) die_code("obo: the object this handle names has been freed\n", 7);
    return p;
}

i64 rt_alive(u64 hd) { return handle_target(hd) != 0; }

/* Normal end of the program: with OBO_CHECK=1, anything still live is a leak. */
void rt_finish(void) {
    flush();
    if (stats) {                       /* to stderr, leaving the program's output alone */
        i64 was = out_fd;
        out_fd = 2;
        puts_("obo: "); put_int((i64)peak_bytes); puts_(" peak bytes, ");
        put_int((i64)total_alloc); puts_(" allocated\n");
        flush();
        out_fd = was;
    }
    if (leak_check) {
        u64 nfd = 0;
        for (int i = 0; i < 1024; i++) if (fdstate[i] == 1) nfd++;
        if (nfd) {
            char msg[80]; const char *p = "obo: file descriptors leaked: "; int n = 0;
            while (*p) msg[n++] = *p++;
            char b[24]; int i = 24; u64 u = nfd; do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
            while (i < 24) msg[n++] = b[i++];
            msg[n++] = '\n';
            sys3(1, 2, (i64)msg, n); sys3(60, 8, 0, 0);
        }
    }
    if (leak_check && live_owned) {
        char msg[64]; const char *p = "obo: objects leaked: "; int n = 0;
        while (*p) msg[n++] = *p++;
        char b[24]; int i = 24; u64 u = live_owned; do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
        while (i < 24) msg[n++] = b[i++];
        msg[n++] = '\n';
        sys3(1, 2, (i64)msg, n); sys3(60, 5, 0, 0);
    }
    sys3(60, 0, 0, 0);
    for (;;);
}

static u64 new_block(u64 kind, u64 esize, u64 cap) {
    u64 b = alloc_raw(kind, 8 + esize * cap);
    *(u64 *)b = cap;
    return b;
}

/* kind: 1 = ints and bools, 2 = owned references, 4 = bytes, 6 = strings */
u64 rt_alloc_array(i64 n, u64 kind) {
    if (n < 0) die("obx: negative array length\n");
    u64 esize = kind == 4 ? 1 : 8;
    u64 b = new_block(kind, esize, (u64)n);
    tmp_root[0] = b;
    Array *a = (Array *)alloc_raw(5, sizeof(Array));
    tmp_root[0] = 0;
    a->len = a->cap = (u64)n; a->data = b; a->esize = esize;
    return (u64)a;
}

/* Appends v, growing the data block when full. v is on the obx stack, so it
 * survives a collection during the growth. */
void rt_append(Array *a, u64 v) {
    if (a->len == a->cap) {
        u64 cap = a->cap < 4 ? 8 : a->cap * 2;
        u64 kind = KIND(a->data - GRAN);
        u64 b = new_block(kind, a->esize, cap);
        memcpy((char *)b + 8, (char *)a->data + 8, a->len * a->esize);
        free_block(a->data - GRAN);
        a->data = b; a->cap = cap;
    }
    char *slot = (char *)a->data + 8 + a->len * a->esize;
    if (a->esize == 1) *slot = (char)v; else *(u64 *)slot = v;
    a->len++;
}

void rt_panic_bounds(i64 line);

/* Shrinks to n elements, clearing the rest so nothing stale stays reachable. */
void rt_truncate(Array *a, i64 n, i64 line) {
    if (n < 0 || (u64)n > a->len) rt_panic_bounds(line);
    u64 bk = KIND(a->data - GRAN);
    if (bk == 2) for (u64 i = (u64)n; i < a->len; i++) rt_free(((u64 *)((char *)a->data + 8))[i]);
    if (bk == 6) for (u64 i = (u64)n; i < a->len; i++) rt_str_dec(((u64 *)((char *)a->data + 8))[i]);
    memset((char *)a->data + 8 + (u64)n * a->esize, 0, (a->len - (u64)n) * a->esize);
    a->len = (u64)n;
}

/* ---------- strings: pointer to [len][bytes]; 0 means "" ---------- */
static u64 slen(u64 s) { return s ? *(u64 *)s : 0; }
static const char *sdata(u64 s) { return (const char *)(s + 8); }

u64 rt_concat(u64 a, u64 b) {
    u64 la = slen(a), lb = slen(b);
    if (!la) return rt_str_inc(b);
    if (!lb) return rt_str_inc(a);
    u64 p = alloc_raw(3, 8 + la + lb);
    *(u64 *)p = la + lb;
    memcpy((char *)p + 8, sdata(a), la);
    memcpy((char *)p + 8 + la, sdata(b), lb);
    return p;
}

i64 rt_streq(u64 a, u64 b) {
    u64 la = slen(a);
    if (la != slen(b)) return 0;
    for (u64 i = 0; i < la; i++) if (sdata(a)[i] != sdata(b)[i]) return 0;
    return 1;
}

u64 rt_int_to_str(i64 v) {
    char b[24]; int i = 24; u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) b[--i] = '-';
    u64 p = alloc_raw(3, 8 + (24 - i));
    *(u64 *)p = 24 - i;
    memcpy((char *)p + 8, b + i, 24 - i);
    return p;
}

/* string(bytes): copies the array's bytes into a new string */
u64 rt_bytes_to_string(Array *a) {
    if (!a->len) return 0;
    u64 p = alloc_raw(3, 8 + a->len);
    *(u64 *)p = a->len;
    memcpy((char *)p + 8, (char *)a->data + 8, a->len);
    return p;
}

/* substr(s, from, to): the bytes from..to-1 */
u64 rt_substr(u64 s, i64 from, i64 to, i64 line) {
    if (from < 0 || to < from || (u64)to > slen(s)) rt_panic_bounds(line);
    if (to == from) return 0;
    u64 p = alloc_raw(3, 8 + (u64)(to - from));
    *(u64 *)p = (u64)(to - from);
    memcpy((char *)p + 8, sdata(s) + from, (u64)(to - from));
    return p;
}

/* ---------- files, arguments, stderr ---------- */
static int cpath(u64 s, char *buf) {          /* obx string -> NUL-terminated path */
    u64 n = slen(s);
    if (n == 0 || n >= 4096) return 0;
    memcpy(buf, sdata(s), n); buf[n] = 0;
    for (u64 i = 0; i < n; i++) if (!buf[i]) return 0;
    return 1;
}

/* readFile(path): the whole file as []byte, or nil */
u64 rt_read_file(u64 path) {
    char p[4096];
    if (!cpath(path, p)) return 0;
    i64 fd = sys3(2, (i64)p, 0, 0);                         /* open O_RDONLY */
    if (fd < 0) return 0;
    i64 size = sys3(8, fd, 0, 2);                           /* lseek to the end */
    if (size < 0 || sys3(8, fd, 0, 0) != 0) { sys3(3, fd, 0, 0); return 0; }
    Array *a = (Array *)rt_alloc_array(size, 4);
    u64 got = 0;
    while (got < (u64)size) {
        i64 r = sys3(0, fd, (i64)((char *)a->data + 8 + got), size - (i64)got);
        if (r <= 0) break;
        got += (u64)r;
    }
    sys3(3, fd, 0, 0);
    if (got != (u64)size) return 0;
    return (u64)a;
}

/* writeFile(path, data, executable): true on success */
i64 rt_write_file(u64 path, Array *a, i64 executable) {
    char p[4096];
    if (!cpath(path, p)) return 0;
    i64 fd = sys3(2, (i64)p, 0x241, executable ? 0755 : 0644);   /* O_WRONLY|O_CREAT|O_TRUNC */
    if (fd < 0) return 0;
    u64 done = 0;
    while (done < a->len) {
        i64 w = sys3(1, fd, (i64)((char *)a->data + 8 + done), (i64)(a->len - done));
        if (w <= 0) { sys3(3, fd, 0, 0); return 0; }
        done += (u64)w;
    }
    return sys3(3, fd, 0, 0) == 0;
}

/* ---------- incremental I/O: Result cells and the fd helpers ---------- */

/* Every message is a proper obo string (kind 3) allocated once; the err cell's
 * descriptor frees it. No cell retains a caller's pointer: path bytes are copied.
 * The cell stores and the tmp_root use follow the runtime's two-allocation rule. */
static u64 iomsg_lit(const char *s) {
    u64 n = 0; while (s[n]) n++;
    u64 p = alloc_raw(3, 8 + n);
    *(u64 *)p = n;
    memcpy((char *)p + 8, s, n);
    return p;
}
static u64 iomsg_str(const char *pre, u64 s) {
    u64 pl = 0; while (pre[pl]) pl++;
    u64 sl = slen(s);
    u64 p = alloc_raw(3, 8 + pl + sl);
    *(u64 *)p = pl + sl;
    memcpy((char *)p + 8, pre, pl);
    memcpy((char *)p + 8 + pl, sdata(s), sl);
    return p;
}
static u64 iomsg_num(const char *pre, i64 v) {
    char b[24]; int i = 24; u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) b[--i] = '-';
    u64 pl = 0; while (pre[pl]) pl++;
    u64 p = alloc_raw(3, 8 + pl + (u64)(24 - i));
    *(u64 *)p = pl + (u64)(24 - i);
    memcpy((char *)p + 8, pre, pl);
    memcpy((char *)p + 8 + pl, b + i, 24 - i);
    return p;
}
static u64 err_cell(u64 descErr, u64 msg, i64 code) {
    tmp_root[0] = msg;
    u64 *c = (u64 *)rt_alloc_record((u64 *)descErr);
    c[0] = msg;
    c[1] = (u64)code;
    tmp_root[0] = 0;
    return (u64)c;
}
static u64 ok_cell(u64 descOk, u64 payload) {
    tmp_root[0] = payload;
    u64 *c = (u64 *)rt_alloc_record((u64 *)descOk);
    c[0] = payload;
    tmp_root[0] = 0;
    return (u64)c;
}
/* A trap naming a descriptor, like the other runtime traps: stderr, then exit. */
static void die_num(const char *pre, i64 v, i64 code) {
    flush();
    char msg[80]; int n = 0; while (*pre) msg[n++] = *pre++;
    char b[24]; int i = 24; u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) b[--i] = '-';
    while (i < 24) msg[n++] = b[i++];
    msg[n++] = '\n';
    sys3(1, 2, (i64)msg, n); sys3(60, code, 0, 0); for (;;);
}
static int str_is(u64 s, const char *t) {
    u64 n = slen(s), i = 0;
    while (i < n && t[i] && sdata(s)[i] == t[i]) i++;
    return i == n && t[i] == 0;
}
/* The test-only injections: one synthetic EINTR per syscall kind and a 3-byte cap
 * on each write. Both are inert unless OBO_CHECK=1 (see rt_init). */
static i64 io_read(i64 fd, i64 buf, i64 n) {
    for (;;) {
        i64 r;
        if (fault_read) { fault_read = 0; r = -4; } else r = sys3(0, fd, buf, n);
        if (r != -4) return r;
    }
}
static i64 io_write(i64 fd, i64 buf, i64 n) {
    for (;;) {
        i64 r;
        if (io_fault_short && n > 3) n = 3;
        if (fault_write) { fault_write = 0; r = -4; } else r = sys3(1, fd, buf, n);
        if (r != -4) return r;
    }
}
static i64 io_close(i64 fd) {
    if (fault_close) { fault_close = 0; return -4; }
    return sys3(3, fd, 0, 0);
}

/* openFile(path, mode, descErr, descOk): Result[int]. The path is checked before
 * conversion; a 4095-byte path plus the NUL fits cpath's buffer exactly. */
u64 rt_open_file(u64 path, u64 mode, u64 descErr, u64 descOk) {
    char p[4096];
    if (slen(path) >= 4096) return err_cell(descErr, iomsg_str("cannot open ", path), 36);  /* ENAMETOOLONG */
    if (!cpath(path, p)) return err_cell(descErr, iomsg_str("cannot open ", path), 22);     /* EINVAL */
    i64 flags, pmode = 0;
    if (str_is(mode, "r")) { flags = 0; }                                                   /* O_RDONLY */
    else if (str_is(mode, "w")) { flags = 0x241; pmode = 0644; }                            /* O_WRONLY|O_CREAT|O_TRUNC */
    else if (str_is(mode, "a")) { flags = 0x441; pmode = 0644; }                            /* O_WRONLY|O_CREAT|O_APPEND */
    else return err_cell(descErr, iomsg_lit("unknown open mode"), 22);
    i64 fd;
    do { fd = sys3(2, (i64)p, flags, pmode); } while (fd == -4);   /* EINTR: opening a FIFO or device */
    if (fd < 0) return err_cell(descErr, iomsg_str("cannot open ", path), -fd);
    if (fd < 1024) fdstate[fd] = 1;
    return ok_cell(descOk, (u64)fd);
}

/* readFd(fd, max, descErr, descOk): Result[?[]byte]. Flush first, so a prompt
 * written with print appears before the program blocks; then the checks run
 * before the max-sized array allocation (N2), in the order max, fd, table. */
u64 rt_read_fd(i64 fd, i64 max, u64 descErr, u64 descOk) {
    flush();
    if (max <= 0) return err_cell(descErr, iomsg_lit("readFd needs a positive count"), 22);
    if (fd < 0) return err_cell(descErr, iomsg_num("cannot read fd ", fd), 9);             /* EBADF */
    if (fd < 1024 && fdstate[fd] == 2) {
        if (leak_check) die_num("obo: use of a closed file descriptor ", fd, 9);
        return err_cell(descErr, iomsg_num("cannot read fd ", fd), 9);
    }
    Array *a = (Array *)rt_alloc_array(max, 4);
    i64 r = io_read(fd, (i64)((char *)a->data + 8), max);
    if (r < 0) { rt_free((u64)a); return err_cell(descErr, iomsg_num("cannot read fd ", fd), -r); }
    if (r == 0) { rt_free((u64)a); return ok_cell(descOk, 0); }    /* EOF is nil, never an empty array */
    if ((u64)r < (u64)max) a->len = (u64)r;                        /* a short read is data, not EOF */
    return ok_cell(descOk, (u64)a);
}

/* writeFd(fd, data, descErr, descOk): Result[int]. Borrows data. The fd-state
 * check is first, so a closed fd is never ok(0) for empty data (N1). */
u64 rt_write_fd(i64 fd, Array *data, u64 descErr, u64 descOk) {
    flush();
    if (fd >= 0 && fd < 1024 && fdstate[fd] == 2) {
        if (leak_check) die_num("obo: use of a closed file descriptor ", fd, 9);
        return err_cell(descErr, iomsg_num("cannot write fd ", fd), 9);
    }
    i64 r = io_write(fd, (i64)((char *)data->data + 8), (i64)data->len);
    if (r < 0) return err_cell(descErr, iomsg_num("cannot write fd ", fd), -r);
    if (r == 0 && data->len > 0) return err_cell(descErr, iomsg_num("cannot write fd ", fd), 5);  /* EIO */
    return ok_cell(descOk, (u64)r);                                /* a short write is success */
}

/* closeFile(fd, descErr, descOk): Result[bool]. A table-state-0 0/1/2 is
 * refused; a tracked number follows the open/closed cases; any other number
 * goes to the kernel and is never entered in the table (N3). A tracked fd is
 * marked closed on any attempt, even a failed one (N4). */
u64 rt_close_fd(i64 fd, u64 descErr, u64 descOk) {
    flush();
    int tracked = fd >= 0 && fd < 1024 && fdstate[fd] != 0;
    if (fd >= 0 && fd < 3 && !tracked)
        return err_cell(descErr, iomsg_num("cannot close standard stream fd ", fd), 22);   /* EINVAL */
    if (tracked && fdstate[fd] == 2) {
        if (leak_check) die_num("obo: double close of file descriptor ", fd, 9);
        return err_cell(descErr, iomsg_num("cannot close fd ", fd), 9);
    }
    i64 r = io_close(fd);
    if (tracked) fdstate[fd] = 2;
    if (r == 0 || r == -4) return ok_cell(descOk, 1);      /* EINTR: Linux already closed it */
    return err_cell(descErr, iomsg_num("cannot close fd ", fd), -r);
}

/* ---------- metadata: directories, stat and readlink ---------- */

/* newfstatat's flags, and the largest directory buffer readDirFd accepts (a
 * bigger constant is refused before the allocation, so it cannot OOM). */
#define AT_FDCWD (-100)
#define AT_SYMLINK_NOFOLLOW 0x100
#define MAX_DIR_BUF 1048576

/* readDirFd(fd, max, descErr, descOk): Result[?[]byte]. The checks run before
 * the max-sized allocation in the design's order: max, its cap, fd, the table;
 * then one getdents64 (retrying EINTR). A short buffer is data; only 0 is EOF,
 * and EOF is nil, never an empty array. */
u64 rt_read_dir_fd(i64 fd, i64 max, u64 descErr, u64 descOk) {
    flush();
    if (max <= 0) return err_cell(descErr, iomsg_lit("readDirFd needs a positive count"), 22);
    if (max > MAX_DIR_BUF) return err_cell(descErr, iomsg_lit("readDirFd count is too large"), 22);
    if (fd < 0) return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), 9);     /* EBADF */
    if (fd >= 0 && fd < 1024 && fdstate[fd] == 2) {
        if (leak_check) die_num("obo: use of a closed file descriptor ", fd, 9);
        return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), 9);
    }
    Array *a = (Array *)rt_alloc_array(max, 4);
    i64 r;
    for (;;) {
        if (fault_dir) { fault_dir = 0; r = -4; } else r = sys3(217, fd, (i64)((char *)a->data + 8), max);
        if (r != -4) break;                                        /* EINTR retried */
    }
    if (r < 0) { rt_free((u64)a); return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), -r); }
    if (r == 0) { rt_free((u64)a); return ok_cell(descOk, 0); }    /* EOF is nil, never an empty array */
    a->len = (u64)r;                                               /* whole records; the caller validates */
    return ok_cell(descOk, (u64)a);
}

/* statPath/lstatPath share do_stat. cpath rejects empty paths and embedded NULs
 * (EINVAL); a 4096-byte path is ENAMETOOLONG before conversion. The 144-byte
 * struct stat is filled at the section 6.6 offsets and copied into the owned
 * 14-int array in the design's field order; nothing of the raw struct is kept. */
static u64 do_stat(u64 path, i64 flags, const char *what, int *fault, u64 descErr, u64 descOk) {
    char p[4096];
    if (slen(path) >= 4096) return err_cell(descErr, iomsg_str(what, path), 36);   /* ENAMETOOLONG */
    if (!cpath(path, p)) return err_cell(descErr, iomsg_str(what, path), 22);      /* EINVAL */
    u64 st[18];                                                    /* 144 bytes, as the kernel expects */
    i64 r;
    for (;;) {
        if (*fault) { *fault = 0; r = -4; } else r = sys4(262, AT_FDCWD, (i64)p, (i64)st, flags);
        if (r != -4) break;                                        /* EINTR retried */
    }
    if (r < 0) return err_cell(descErr, iomsg_str(what, path), -r);
    Array *a = (Array *)rt_alloc_array(14, 1);
    u64 *v = (u64 *)((char *)a->data + 8);
    v[0]  = (u64)(*(unsigned int *)((char *)st + 24));             /* mode (st_mode, u32) */
    v[1]  = (u64)(*(i64 *)((char *)st + 48));                      /* size (signed) */
    v[2]  = (u64)(*(i64 *)((char *)st + 88));                      /* mtime seconds */
    v[3]  = (u64)(*(i64 *)((char *)st + 96));                      /* mtime nanoseconds */
    v[4]  = (u64)(*(unsigned int *)((char *)st + 28));             /* uid */
    v[5]  = (u64)(*(unsigned int *)((char *)st + 32));             /* gid */
    v[6]  = *(u64 *)((char *)st + 16);                             /* nlink */
    v[7]  = *(u64 *)((char *)st + 8);                              /* ino */
    v[8]  = *(u64 *)((char *)st + 0);                              /* dev */
    v[9]  = *(u64 *)((char *)st + 40);                             /* rdev */
    v[10] = (u64)(*(i64 *)((char *)st + 56));                      /* blksize */
    v[11] = (u64)(*(i64 *)((char *)st + 64));                      /* blocks */
    v[12] = (u64)(*(i64 *)((char *)st + 72));                      /* atime seconds */
    v[13] = (u64)(*(i64 *)((char *)st + 104));                     /* ctime seconds */
    return ok_cell(descOk, (u64)a);
}
u64 rt_stat_path(u64 path, u64 descErr, u64 descOk) {
    return do_stat(path, 0, "cannot stat ", &fault_stat, descErr, descOk);
}
u64 rt_lstat_path(u64 path, u64 descErr, u64 descOk) {
    return do_stat(path, AT_SYMLINK_NOFOLLOW, "cannot lstat ", &fault_lstat, descErr, descOk);
}

/* readLinkPath(path): Result[[]byte]. Raw target bytes with no NUL; the API
 * limit is 4095 bytes, so a full 4096-byte read is ENAMETOOLONG rather than a
 * silent truncation. A dangling target still succeeds; a non-symlink is EINVAL. */
u64 rt_read_link(u64 path, u64 descErr, u64 descOk) {
    char p[4096];
    char buf[4096];
    if (slen(path) >= 4096) return err_cell(descErr, iomsg_str("cannot readlink ", path), 36);
    if (!cpath(path, p)) return err_cell(descErr, iomsg_str("cannot readlink ", path), 22);
    i64 r;
    for (;;) {
        if (fault_link) { fault_link = 0; r = -4; } else r = sys3(89, (i64)p, (i64)buf, 4096);
        if (r != -4) break;                                        /* EINTR retried */
    }
    if (r < 0) return err_cell(descErr, iomsg_str("cannot readlink ", path), -r);
    if (r == 4096) return err_cell(descErr, iomsg_str("cannot readlink ", path), 36);  /* ENAMETOOLONG */
    Array *a = (Array *)rt_alloc_array(r, 4);
    memcpy((char *)a->data + 8, buf, (u64)r);
    return ok_cell(descOk, (u64)a);                                /* an empty target is a non-nil empty array */
}

/* ---------- environment: isatty and getenv (P5a) ---------- */

/* isatty(fd): true only when ioctl(fd, TCGETS 0x5401) succeeds. fd < 0 is false
 * before any syscall; a pipe, a regular file, /dev/null, a directory or a closed
 * descriptor is false (ENOTTY/EBADF) and never an fd-table trap. A tracked
 * descriptor that was closed may have been reused by the kernel, and then the
 * ioctl simply observes the reused one. The kernel writes 36 bytes of struct
 * termios into the buffer, which is larger. */
i64 rt_isatty(i64 fd) {
    if (fd < 0) return 0;
    u64 t[8];                                                      /* 64 bytes > the 36-byte termios */
    i64 r;
    for (;;) {
        if (fault_tty) { fault_tty = 0; r = -4; } else r = sys3(16, fd, 0x5401, (i64)t);
        if (r != -4) break;                                        /* EINTR retried */
    }
    return r == 0;
}

/* getenv(name): the first `name=...` entry of the environment as an owned byte
 * array; nil when the name is invalid (empty, an embedded NUL, or a '=') or
 * absent. An empty value is a non-nil empty array. env_base is the envp that
 * rt_init recorded from the initial stack; the value is copied, so nothing of
 * envp is retained and the caller owns the array. */
u64 rt_getenv(u64 name) {
    u64 n = slen(name);
    if (n == 0) return 0;
    for (u64 i = 0; i < n; i++) {
        char c = sdata(name)[i];
        if (!c || c == '=') return 0;
    }
    for (char **env = (char **)env_base; *env; env++) {
        char *e = *env;
        u64 i = 0;
        while (i < n && e[i] && e[i] == sdata(name)[i]) i++;
        if (i != n || e[n] != '=') continue;
        u64 vn = 0;
        while (e[n + 1 + vn]) vn++;
        Array *a = (Array *)rt_alloc_array((i64)vn, 4);
        memcpy((char *)a->data + 8, e + n + 1, vn);
        return (u64)a;                                             /* first match wins */
    }
    return 0;
}

/* args(): the command line, program name first */
u64 rt_args(void) {
    u64 argc = *(u64 *)stack_base;
    char **argv = (char **)(stack_base + 8);
    Array *a = (Array *)rt_alloc_array((i64)argc, 6);
    tmp_root[1] = (u64)a;
    for (u64 i = 0; i < argc; i++) {
        u64 n = 0;
        while (argv[i][n]) n++;
        u64 s = 0;
        if (n) {
            s = alloc_raw(3, 8 + n);
            *(u64 *)s = n;
            memcpy((char *)s + 8, argv[i], n);
        }
        ((u64 *)((char *)a->data + 8))[i] = s;
    }
    tmp_root[1] = 0;
    return (u64)a;
}

/* eprint: switch output between stdout (1) and stderr (2) */
void rt_set_output(i64 fd) { flush(); out_fd = fd; }

void rt_print_int(i64 v) { put_int(v); }
void rt_print_str(u64 s) { if (s) putb(sdata(s), slen(s)); }
void rt_print_bool(i64 b) { puts_(b ? "true" : "false"); }
void rt_newline(void) { putb("\n", 1); }

void rt_gc_stats(void) {
    puts_("obo: "); put_int((i64)live_owned); puts_(" live objects, ");
    put_int((i64)peak_bytes); puts_(" peak bytes\n");
    return;
    puts_("gc: "); put_int((i64)n_collections); puts_(" collections, ");
    put_int((i64)n_freed_total); puts_(" objects freed, ");
    put_int((i64)(live_bytes)); puts_(" bytes live after last, ");
    put_int((i64)(total_alloc >> 20)); puts_(" MB allocated in total\n");
}

void rt_panic_bounds(i64 line) {
    flush();
    char msg[64]; const char *p = "obx: index out of range at line "; int n = 0;
    while (*p) msg[n++] = *p++;
    char b[24]; int i = 24; u64 u = (u64)line; do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    while (i < 24) msg[n++] = b[i++];
    msg[n++] = '\n';
    sys3(1, 2, (i64)msg, n); sys3(60, 3, 0, 0); for (;;);
}

void rt_misaligned(void) {
    __asm__ volatile ("and $-16, %rsp");
    die_code("obx: internal error: misaligned stack in a runtime call\n", 4);
}

void rt_exit(i64 code) { flush(); sys3(60, code, 0, 0); for (;;); }
