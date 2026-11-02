/* rt_meta_test: the direct oracle for the runtime's metadata contract (design
 * section 6, P3, P5a and P6a). It links runtime.c into its own image (so it may
 * use sys3/sys4, set env_base, which is how the crafted-envp case pins
 * getenv's scan rules, and open a pty for the terminalWidth cases). It reads the
 * test-only fault ints directly.
 * alloc_raw and the string helpers) and has its own _entry, which passes the
 * kernel stack to rt_init; the environment therefore reaches the runtime
 * exactly as it does for a compiled program.
 *
 * Each case runs in a process of its own: the test driver runs this binary once
 * per case, with the case name in argv[1] and a scratch directory path in argv[2].
 * A case that should end normally calls rt_finish (exit 0, the leak oracle); the
 * cases that check exits 9 end in the runtime's own traps. An assertion failure
 * exits 1. Every fixture is created here, through raw syscalls, so the expected
 * stat values (size, mode, mtime, mtime_ns, uid, gid, nlink) are pinning the
 * runtime's 14-int array against the kernel, not against itself. Each case also
 * compares every array element against the same field of a fresh raw
 * newfstatat, so a shifted field offset or a swapped pair is caught even when
 * the hardcoded expectation would coincide.
 *
 *     python3 obo/tests/meta_test.py --rt      (builds and runs every case)
 *
 * The 4096-byte readlink target is not creatable on Linux (symlink(2) refuses a
 * target of PATH_MAX bytes), so the runtime's r == 4096 -> ENAMETOOLONG branch is
 * defence in depth; a 4095-byte target (the kernel maximum) is checked instead.
 */
#include "../runtime/runtime.c"

/* the descriptors the compiler passes: err [16, 0, 1, 0], owned ok [8, 1, 0, 0].
 * They live in .data, as generated descriptors do. */
static u64 desc_err[4] = {16, 0, 1, 0};
static u64 desc_ok_int[3] = {8, 0, 0};
static u64 desc_ok_owned[4] = {8, 1, 0, 0};

static void fail(i64 code) { sys3(60, code, 0, 0); for (;;); }
#define CHECK(c) do { if (!(c)) fail(1); } while (0)

/* one arena for every path and obo string the harness builds; obo strings are
 * 8-byte aligned, paths are NUL-terminated. The arena is below the heap, so
 * rt_str_dec ignores the strings. */
static char arena[1 << 20];
static u64 arena_used;
static u64 harness_sp, harness_argc;      /* the initial stack and argc, for env_base */
static u64 arena_alloc(u64 n) {
    u64 off = (arena_used + 7) & ~7UL;
    arena_used = off + n;
    if (arena_used > sizeof arena) fail(2);
    return off;
}
static char *ajoin(const char *a, const char *b) {
    u64 la = 0, lb = 0; while (a[la]) la++; while (b[lb]) lb++;
    char *p = (char *)&arena[arena_alloc(la + lb + 1)];
    memcpy(p, a, la); memcpy(p + la, b, lb); p[la + lb] = 0;
    return p;
}
static u64 ostrn(const char *s, u64 n) {
    u64 off = arena_alloc(8 + n);
    u64 p = (u64)&arena[off];
    *(u64 *)p = n;
    memcpy((char *)p + 8, s, n);
    return p;
}
static u64 ostr(const char *s) { u64 n = 0; while (s[n]) n++; return ostrn(s, n); }

/* A legal path of exactly `total` bytes with short components: base, then a
 * missing first component, then 1..200-byte components. A single 4095-byte
 * component is ENAMETOOLONG from the kernel (NAME_MAX), which would confuse the
 * cpath boundary test; this shape asks the kernel for the full-path limit. */
static char *long_path(const char *base, int total) {
    char *p = (char *)&arena[arena_alloc((u64)total + 1)];
    int n = 0;
    while (base[n]) { p[n] = base[n]; n++; }
    const char *miss = "/missing";
    for (int i = 0; miss[i] && n < total; i++) p[n++] = miss[i];
    while (n < total) {
        p[n++] = '/';
        for (int k = 0; k < 200 && n < total; k++) p[n++] = 'x';
    }
    p[total] = 0;
    return p;
}

static int c_eq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}
static int str_eq_c(u64 s, const char *t) {
    u64 n = s ? *(u64 *)s : 0, i = 0;
    if (!s) return t[0] == 0;
    while (i < n && t[i] && ((const char *)(s + 8))[i] == t[i]) i++;
    return i == n && t[i] == 0;
}
static int str_prefix_c(u64 s, const char *t) {
    u64 n = s ? *(u64 *)s : 0, i = 0;
    if (!s) return t[0] == 0;
    while (t[i]) {
        if (i >= n || ((const char *)(s + 8))[i] != t[i]) return 0;
        i++;
    }
    return 1;
}
static u64 cell_tag(u64 c) { return (*(u64 *)(c - GRAN)) & 0xFFFFFFFFUL; }
static u64 cell_payload(u64 c) { return *(u64 *)c; }
static u64 cell_msg(u64 c) { return *(u64 *)c; }
static i64 cell_code(u64 c) { return (i64)((u64 *)c)[1]; }

/* ---------- fixtures, through raw syscalls ---------- */
static void make_dir(const char *p) { CHECK(sys3(83, (i64)p, 0755, 0) == 0); }
static void make_file_n(const char *p, const char *data, u64 n) {
    i64 fd = sys3(2, (i64)p, 0x241, 0644);            /* O_WRONLY|O_CREAT|O_TRUNC */
    CHECK(fd >= 0);
    u64 done = 0;
    while (done < n) {
        i64 w = sys3(1, fd, (i64)(data + done), (i64)(n - done));
        CHECK(w > 0);
        done += (u64)w;
    }
    CHECK(sys3(3, fd, 0, 0) == 0);
    CHECK(sys3(90, (i64)p, 0644, 0) == 0);            /* chmod: umask-independent */
}
static void make_file(const char *p, const char *data) {
    u64 n = 0; while (data[n]) n++;
    make_file_n(p, data, n);
}
static void make_link(const char *target, const char *link) {
    CHECK(sys3(88, (i64)target, (i64)link, 0) == 0);
}
static void set_times(const char *p, i64 asec, i64 ansec, i64 msec, i64 mnsec) {
    i64 t[4];
    t[0] = asec; t[1] = ansec; t[2] = msec; t[3] = mnsec;
    CHECK(sys4(280, AT_FDCWD, (i64)p, (i64)t, 0) == 0);   /* utimensat */
}
static void raw_stat(const char *p, u64 *st, i64 flags) {
    i64 r;
    do { r = sys4(262, AT_FDCWD, (i64)p, (i64)st, flags); } while (r == -4);
    CHECK(r == 0);
}
static i64 raw_uid(void) { return sys3(102, 0, 0, 0); }
static i64 raw_gid(void) { return sys3(104, 0, 0, 0); }

/* the section 6.6 offsets, read straight from a raw struct stat */
static i64 st_dev(u64 *s)  { return (i64)*(u64 *)((char *)s + 0); }
static i64 st_ino(u64 *s)  { return (i64)*(u64 *)((char *)s + 8); }
static i64 st_nlink(u64 *s){ return (i64)*(u64 *)((char *)s + 16); }
static i64 st_mode(u64 *s) { return (i64)*(unsigned int *)((char *)s + 24); }
static i64 st_uid(u64 *s)  { return (i64)*(unsigned int *)((char *)s + 28); }
static i64 st_gid(u64 *s)  { return (i64)*(unsigned int *)((char *)s + 32); }
static i64 st_rdev(u64 *s) { return (i64)*(u64 *)((char *)s + 40); }
static i64 st_size(u64 *s) { return *(i64 *)((char *)s + 48); }
static i64 st_blksize(u64 *s) { return *(i64 *)((char *)s + 56); }
static i64 st_blocks(u64 *s)  { return *(i64 *)((char *)s + 64); }
static i64 st_atime_s(u64 *s) { return *(i64 *)((char *)s + 72); }
static i64 st_mtime_s(u64 *s) { return *(i64 *)((char *)s + 88); }
static i64 st_mtime_ns(u64 *s){ return *(i64 *)((char *)s + 96); }
static i64 st_ctime_s(u64 *s) { return *(i64 *)((char *)s + 104); }

/* the 14-int array of an ok stat cell */
static u64 *stat_fields(u64 cell) {
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    Array *a = (Array *)cell_payload(cell);
    CHECK(a != 0);
    CHECK(a->len == 14);
    return (u64 *)((char *)a->data + 8);
}
static u64 stat_call(const char *path, int follow) {
    return follow ? rt_stat_path(ostr(path), (u64)desc_err, (u64)desc_ok_owned)
                  : rt_lstat_path(ostr(path), (u64)desc_err, (u64)desc_ok_owned);
}
/* every array element against the same field of the raw struct at its offset */
static void check_fields(u64 *v, u64 *s) {
    CHECK((i64)v[0] == st_mode(s));
    CHECK((i64)v[1] == st_size(s));
    CHECK((i64)v[2] == st_mtime_s(s));
    CHECK((i64)v[3] == st_mtime_ns(s));
    CHECK((i64)v[4] == st_uid(s));
    CHECK((i64)v[5] == st_gid(s));
    CHECK((i64)v[6] == st_nlink(s));
    CHECK((i64)v[7] == st_ino(s));
    CHECK((i64)v[8] == st_dev(s));
    CHECK((i64)v[9] == st_rdev(s));
    CHECK((i64)v[10] == st_blksize(s));
    CHECK((i64)v[11] == st_blocks(s));
    CHECK((i64)v[12] == st_atime_s(s));
    CHECK((i64)v[13] == st_ctime_s(s));
}
static void expect_msg(u64 cell, const char *pre, const char *path) {
    char buf[4600]; int n = 0;
    while (*pre) buf[n++] = *pre++;
    while (*path) buf[n++] = *path++;
    buf[n] = 0;
    CHECK(str_eq_c(cell_msg(cell), buf));
}
static void expect_num_msg(u64 cell, const char *pre, i64 v) {
    char buf[128]; int n = 0;
    while (*pre) buf[n++] = *pre++;
    char b[24]; int i = 24; u64 u = v < 0 ? (u64)0 - (u64)v : (u64)v;
    do { b[--i] = (char)('0' + u % 10); u /= 10; } while (u);
    if (v < 0) b[--i] = '-';
    while (i < 24) buf[n++] = b[i++];
    buf[n] = 0;
    CHECK(str_eq_c(cell_msg(cell), buf));
}
static u64 open_tracked(const char *path, const char *mode) {
    u64 cell = rt_open_file(ostr(path), ostr(mode), (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    i64 fd = (i64)cell_payload(cell);
    CHECK(fd >= 0);
    rt_free(cell);
    return (u64)fd;
}
static void close_tracked(i64 fd) {
    u64 cell = rt_close_fd(fd, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 1);
    rt_free(cell);
}

/* ---------- readDirFd ---------- */
/* the record walk the fs module does, in C: whole records, a NUL name inside
 * each record; returns true when the chunk holds `name` */
static int dir_has(Array *a, const char *name) {
    unsigned char *b = (unsigned char *)a->data + 8;
    u64 used = a->len, pos = 0;
    while (pos + 19 <= used) {
        u64 reclen = (u64)b[pos + 16] | ((u64)b[pos + 17] << 8);
        CHECK(reclen >= 20);
        CHECK(pos + reclen <= used);
        const char *nm = (const char *)(b + pos + 19);
        u64 maxlen = reclen - 19, i = 0;
        while (i < maxlen && nm[i]) i++;
        CHECK(i < maxlen);
        if (c_eq(nm, name)) return 1;
        pos += reclen;
    }
    return 0;
}
static u64 dir_read(i64 fd, i64 max) {
    u64 cell = rt_read_dir_fd(fd, max, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    return cell;
}

/* ---------- isatty and getenv (P5a) ---------- */
static void expect_bytes(u64 a, const char *want, u64 n) {
    CHECK(a != 0);
    Array *arr = (Array *)a;
    CHECK(arr->len == n);
    for (u64 i = 0; i < n; i++) CHECK(((char *)arr->data + 8)[i] == want[i]);
}

/* /dev/pts/N for a freshly opened /dev/ptmx master, unlocked. The slave is a
 * terminal; the master's TCGETS is not asserted (this host's kernel reports it
 * as a terminal too, but that is not portable). */
static void open_pty(i64 *master, i64 *slave) {
    static char path[32];
    *master = sys3(2, (i64)"/dev/ptmx", 2 | 0x100, 0);      /* O_RDWR|O_NOCTTY */
    CHECK(*master >= 0);
    int unlock = 0;
    CHECK(sys3(16, *master, 0x40045431, (i64)&unlock) == 0); /* TIOCSPTLCK */
    int n = 0;
    CHECK(sys3(16, *master, 0x80045430, (i64)&n) == 0);      /* TIOCGPTN */
    CHECK(n >= 0 && n < 1000000);
    const char *pre = "/dev/pts/";
    int k = 0;
    while (pre[k]) { path[k] = pre[k]; k += 1; }
    char d[8]; int dn = 0;
    do { d[dn++] = (char)('0' + n % 10); n /= 10; } while (n);
    while (dn) path[k++] = d[--dn];
    path[k] = 0;
    *slave = sys3(2, (i64)path, 2 | 0x100, 0);
    CHECK(*slave >= 0);
}

/* ---------- cases ---------- */
static void case_stat_file(const char *dir) {
    char *p = ajoin(dir, "/f");
    make_file(p, "hello");
    set_times(p, 1000000000, 111, 1234567890, 123456789);
    u64 raw[18];
    raw_stat(p, raw, 0);
    u64 cell = stat_call(p, 1);
    u64 *v = stat_fields(cell);
    check_fields(v, raw);
    CHECK((i64)v[0] == st_mode(raw));
    CHECK(((i64)v[0] & 0xF000) == 0x8000);            /* regular */
    CHECK(((i64)v[0] & 0xFFF) == 0644);
    CHECK((i64)v[1] == 5);
    CHECK((i64)v[2] == 1234567890);
    CHECK((i64)v[3] == 123456789);
    CHECK((i64)v[4] == raw_uid());
    CHECK((i64)v[5] == raw_gid());
    CHECK((i64)v[6] == 1);
    CHECK((i64)v[7] != 0);
    CHECK((i64)v[10] > 0);
    CHECK((i64)v[12] == 1000000000);
    rt_free(cell);
    /* lstat on a regular file is the same stat */
    cell = stat_call(p, 0);
    v = stat_fields(cell);
    check_fields(v, raw);
    rt_free(cell);
    rt_finish();
}

static void case_stat_link(const char *dir) {
    char *t = ajoin(dir, "/target");
    char *l = ajoin(dir, "/link");
    char *d = ajoin(dir, "/sub");
    char *dl = ajoin(dir, "/dlink");
    make_file(t, "1234567");
    make_dir(d);
    make_link("target", l);
    make_link("sub", dl);
    u64 raw[18], rawl[18], rawd[18];
    raw_stat(t, raw, 0);
    raw_stat(l, rawl, AT_SYMLINK_NOFOLLOW);
    raw_stat(d, rawd, 0);
    /* stat follows: the link's cells are the target's */
    u64 cell = stat_call(l, 1);
    u64 *v = stat_fields(cell);
    check_fields(v, raw);
    CHECK((i64)v[1] == 7);
    CHECK(((i64)v[0] & 0xF000) == 0x8000);
    rt_free(cell);
    /* lstat does not: the link's own mode and size */
    cell = stat_call(l, 0);
    v = stat_fields(cell);
    check_fields(v, rawl);
    CHECK(((i64)v[0] & 0xF000) == 0xA000);            /* symlink */
    CHECK((i64)v[1] == 6);                            /* len("target") */
    rt_free(cell);
    /* a symlink to a directory: lstat is a link, stat is a directory */
    cell = stat_call(dl, 0);
    v = stat_fields(cell);
    CHECK(((i64)v[0] & 0xF000) == 0xA000);
    CHECK((i64)v[1] == 3);                            /* len("sub") */
    rt_free(cell);
    cell = stat_call(dl, 1);
    v = stat_fields(cell);
    check_fields(v, rawd);
    CHECK(((i64)v[0] & 0xF000) == 0x4000);
    rt_free(cell);
    rt_finish();
}

static void case_stat_dir(const char *dir) {
    char *p = ajoin(dir, "/d");
    make_dir(p);
    u64 raw[18];
    raw_stat(p, raw, 0);
    u64 cell = stat_call(p, 0);
    u64 *v = stat_fields(cell);
    check_fields(v, raw);
    CHECK(((i64)v[0] & 0xF000) == 0x4000);
    rt_free(cell);
    rt_finish();
}

static void case_stat_errors(const char *dir) {
    char *miss = ajoin(dir, "/no-such");
    u64 cell = stat_call(miss, 1);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);                       /* ENOENT */
    expect_msg(cell, "cannot stat ", miss);
    rt_free(cell);
    cell = stat_call(miss, 0);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);
    expect_msg(cell, "cannot lstat ", miss);
    rt_free(cell);
    /* the empty path is EINVAL, not the kernel's ENOENT */
    cell = stat_call("", 1);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    expect_msg(cell, "cannot stat ", "");
    rt_free(cell);
    cell = stat_call("", 0);
    CHECK(cell_code(cell) == 22);
    expect_msg(cell, "cannot lstat ", "");
    rt_free(cell);
    /* an embedded NUL is EINVAL */
    u64 s = ostrn("a\0b", 3);
    cell = rt_stat_path(s, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    rt_free(cell);
    /* 4095 bytes is a legal path for cpath: the kernel decides (ENOENT here);
     * 4096 is our own ENAMETOOLONG before the syscall */
    char *p4095 = long_path(dir, 4095);
    cell = stat_call(p4095, 1);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);                       /* cpath accepted it */
    rt_free(cell);
    char *p4096 = long_path(dir, 4096);
    u64 s2 = ostrn(p4096, 4096);
    cell = rt_stat_path(s2, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 36);                      /* ENAMETOOLONG */
    CHECK(str_prefix_c(cell_msg(cell), "cannot stat "));
    rt_free(cell);
    rt_finish();
}

static void case_stat_pre1970(const char *dir) {
    char *p = ajoin(dir, "/old");
    make_file(p, "x");
    set_times(p, -200, 0, -100, 0);
    u64 raw[18];
    raw_stat(p, raw, 0);
    u64 cell = stat_call(p, 1);
    u64 *v = stat_fields(cell);
    check_fields(v, raw);
    CHECK((i64)v[2] == -100);                          /* signed seconds */
    CHECK((i64)v[3] == 0);
    CHECK((i64)v[12] == -200);
    rt_free(cell);
    rt_finish();
}

/* A host path owned by two different ids is the oracle for a uid/gid swap; a
 * plain user file has uid == gid, where the swap is genuinely unobservable. */
static void case_stat_owner(const char *dir) {
    static const char *cand[] = {"/etc/shadow", "/dev/tty", "/dev/ptmx",
                                 "/run/systemd/journal/socket", "/var/log/auth.log", 0};
    char *p = ajoin(dir, "/f");
    make_file(p, "x");
    for (int i = 0; cand[i]; i++) {
        u64 raw[18];
        i64 r;
        do { r = sys4(262, AT_FDCWD, (i64)cand[i], (i64)raw, 0); } while (r == -4);
        if (r != 0) continue;
        if (st_uid(raw) == st_gid(raw)) continue;
        u64 cell = stat_call(cand[i], 1);
        u64 *v = stat_fields(cell);
        CHECK((i64)v[4] == st_uid(raw));
        CHECK((i64)v[5] == st_gid(raw));
        CHECK((i64)v[4] != (i64)v[5]);
        rt_free(cell);
        break;
    }
    rt_finish();
}

static void readlink_ok_cell(u64 cell, const char *want, u64 n) {
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    Array *a = (Array *)cell_payload(cell);
    CHECK(a != 0);
    CHECK(a->len == n);
    for (u64 i = 0; i < n; i++) CHECK(((char *)a->data + 8)[i] == want[i]);
}

static void case_readlink_ok(const char *dir) {
    char *l = ajoin(dir, "/link");
    make_link("a/b c", l);
    u64 cell = rt_read_link(ostr(l), (u64)desc_err, (u64)desc_ok_owned);
    readlink_ok_cell(cell, "a/b c", 5);                /* raw bytes, no NUL */
    rt_free(cell);
    /* a dangling target still reads; stat fails, lstat succeeds as a link */
    char *d = ajoin(dir, "/dangling");
    make_link("missing", d);
    cell = rt_read_link(ostr(d), (u64)desc_err, (u64)desc_ok_owned);
    readlink_ok_cell(cell, "missing", 7);
    rt_free(cell);
    cell = stat_call(d, 1);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);
    rt_free(cell);
    cell = stat_call(d, 0);
    u64 *v = stat_fields(cell);
    CHECK(((i64)v[0] & 0xF000) == 0xA000);
    CHECK((i64)v[1] == 7);
    rt_free(cell);
    rt_finish();
}

static void case_readlink_errors(const char *dir) {
    char *p = ajoin(dir, "/file");
    make_file(p, "x");
    u64 cell = rt_read_link(ostr(p), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);                      /* EINVAL: not a symlink */
    expect_msg(cell, "cannot readlink ", p);
    rt_free(cell);
    char *miss = ajoin(dir, "/no-such");
    cell = rt_read_link(ostr(miss), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);                       /* ENOENT */
    expect_msg(cell, "cannot readlink ", miss);
    rt_free(cell);
    cell = rt_read_link(ostr(""), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    expect_msg(cell, "cannot readlink ", "");
    rt_free(cell);
    rt_finish();
}

static void case_readlink_long(const char *dir) {
    /* the kernel's maximum target: 4095 bytes; readlink returns all of it */
    static char target[4096];
    for (int i = 0; i < 4095; i++) target[i] = (char)('a' + i % 26);
    target[4095] = 0;
    char *l = ajoin(dir, "/long");
    make_link(target, l);
    u64 cell = rt_read_link(ostr(l), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    Array *a = (Array *)cell_payload(cell);
    CHECK(a != 0);
    CHECK(a->len == 4095);
    CHECK(((char *)a->data + 8)[0] == 'a');
    CHECK(((char *)a->data + 8)[4094] == (char)('a' + 4094 % 26));
    rt_free(cell);
    /* a 4095-byte path is legal for cpath (the kernel says ENOENT); 4096 is
     * ENAMETOOLONG before the syscall */
    char *p4095 = long_path(dir, 4095);
    cell = rt_read_link(ostr(p4095), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 2);
    rt_free(cell);
    char *p4096 = long_path(dir, 4096);
    cell = rt_read_link(ostrn(p4096, 4096), (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 36);
    CHECK(str_prefix_c(cell_msg(cell), "cannot readlink "));
    rt_free(cell);
    rt_finish();
}

static void case_readdir_ok(const char *dir) {
    char *d = ajoin(dir, "/list");
    make_dir(d);
    make_file(ajoin(d, "/a"), "1");
    make_file(ajoin(d, "/b"), "22");
    make_dir(ajoin(d, "/sub"));
    make_link("a", ajoin(d, "/s"));
    u64 fd = open_tracked(d, "r");
    int saw_dot = 0, saw_dotdot = 0, saw_a = 0, saw_b = 0, saw_sub = 0, saw_s = 0;
    int first = 1;
    for (;;) {
        u64 cell = dir_read((i64)fd, 4096);
        if (cell_payload(cell) == 0) {                 /* EOF is nil, never an empty array */
            CHECK(!first);
            rt_free(cell);
            break;
        }
        Array *a = (Array *)cell_payload(cell);
        CHECK(a->len > 0);
        /* the raw records are whole and NUL-terminated; the kernel supplies . and .. */
        saw_dot |= dir_has(a, ".");
        saw_dotdot |= dir_has(a, "..");
        saw_a |= dir_has(a, "a");
        saw_b |= dir_has(a, "b");
        saw_sub |= dir_has(a, "sub");
        saw_s |= dir_has(a, "s");
        first = 0;
        rt_free(cell);
    }
    CHECK(saw_dot && saw_dotdot && saw_a && saw_b && saw_sub && saw_s);
    /* a second read at EOF is still nil */
    u64 cell = dir_read((i64)fd, 4096);
    CHECK(cell_payload(cell) == 0);
    rt_free(cell);
    close_tracked((i64)fd);
    rt_finish();
}

static void case_readdir_empty(const char *dir) {
    char *d = ajoin(dir, "/empty");
    make_dir(d);
    u64 fd = open_tracked(d, "r");
    /* an empty directory still holds . and .., so the first read is data; the
     * case instead checks that a second read after draining is nil (above) and
     * that a 1 MiB request is accepted */
    u64 cell = dir_read((i64)fd, 1048576);
    CHECK(cell_payload(cell) != 0);
    rt_free(cell);
    for (;;) {
        cell = dir_read((i64)fd, 4096);
        if (cell_payload(cell) == 0) { rt_free(cell); break; }
        rt_free(cell);
    }
    close_tracked((i64)fd);
    rt_finish();
}

static void case_readdir_checks(const char *dir) {
    u64 cell = rt_read_dir_fd(0, 0, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "readDirFd needs a positive count"));
    rt_free(cell);
    cell = rt_read_dir_fd(0, -5, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "readDirFd needs a positive count"));
    rt_free(cell);
    cell = rt_read_dir_fd(0, 1048577, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "readDirFd count is too large"));
    rt_free(cell);
    /* the order: max, then its cap, then fd, then the table, then the allocation */
    cell = rt_read_dir_fd(-1, 0, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);                      /* EINVAL, not EBADF */
    rt_free(cell);
    cell = rt_read_dir_fd(-1, 1048577, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);                      /* the cap, not EBADF */
    rt_free(cell);
    cell = rt_read_dir_fd(-1, 1048576, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);                       /* EBADF, with no allocation */
    expect_num_msg(cell, "cannot read dir fd ", -1);
    rt_free(cell);
    /* a small max can make getdents64 return EINVAL; that is an error */
    char *d = ajoin(dir, "/small");
    make_dir(d);
    make_file(ajoin(d, "/abc"), "x");
    u64 fd = open_tracked(d, "r");
    cell = rt_read_dir_fd((i64)fd, 20, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    expect_num_msg(cell, "cannot read dir fd ", (i64)fd);
    rt_free(cell);
    close_tracked((i64)fd);
    /* a regular-file fd is ENOTDIR on the first read */
    char *p = ajoin(dir, "/regular");
    make_file(p, "x");
    fd = open_tracked(p, "r");
    cell = rt_read_dir_fd((i64)fd, 4096, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 20);                      /* ENOTDIR */
    rt_free(cell);
    close_tracked((i64)fd);
    rt_finish();
}

/* under OBO_CHECK a tracked closed fd traps (exit 9); without it the call is
 * the table's EBADF and the program ends normally */
static void case_readdir_closed(const char *dir) {
    char *d = ajoin(dir, "/closed");
    make_dir(d);
    u64 fd = open_tracked(d, "r");
    close_tracked((i64)fd);
    u64 cell = rt_read_dir_fd((i64)fd, 4096, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);
    expect_num_msg(cell, "cannot read dir fd ", (i64)fd);
    rt_free(cell);
    rt_finish();
}

/* isatty: negative fds, a regular file, /dev/null, both pipe ends, a directory,
 * a tracked closed fd and a reused number are all false; only the pty slave is
 * true. No fd-table consultation means no exit 9 under OBO_CHECK, and the case
 * allocates nothing, so the leak oracle also passes. */
static void case_isatty_checks(const char *dir) {
    CHECK(rt_isatty(-1) == 0);
    CHECK(rt_isatty(-2) == 0);
    CHECK(rt_isatty(-1000000) == 0);
    char *p = ajoin(dir, "/file");
    make_file(p, "x");
    i64 fd = sys3(2, (i64)p, 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_isatty(fd) == 0);                     /* regular file: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    fd = sys3(2, (i64)"/dev/null", 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_isatty(fd) == 0);                     /* /dev/null: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    i64 pf[2];
    CHECK(sys3(293, (i64)pf, 0, 0) == 0);          /* pipe2(..., 0) */
    CHECK(rt_isatty(pf[0]) == 0);
    CHECK(rt_isatty(pf[1]) == 0);
    CHECK(sys3(3, pf[0], 0, 0) == 0 && sys3(3, pf[1], 0, 0) == 0);
    char *d = ajoin(dir, "/d");
    make_dir(d);
    fd = sys3(2, (i64)d, 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_isatty(fd) == 0);                     /* directory: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    /* a tracked descriptor that was closed is not a fatal use: isatty never
     * consults the fd table, so the kernel's EBADF is simply false */
    fd = (i64)open_tracked(p, "r");
    close_tracked(fd);
    CHECK(rt_isatty(fd) == 0);
    /* the number may have been reused by the next open; the ioctl then observes
     * the reused descriptor, and a regular file is still false, never a trap */
    i64 again = sys3(2, (i64)p, 0, 0);
    CHECK(again >= 0);
    CHECK(rt_isatty(again) == 0);
    CHECK(sys3(3, again, 0, 0) == 0);
    /* the one true answer: the pty slave */
    i64 m, s;
    open_pty(&m, &s);
    CHECK(rt_isatty(s) == 1);
    CHECK(sys3(3, s, 0, 0) == 0 && sys3(3, m, 0, 0) == 0);
    rt_finish();
}

/* terminalWidth: negative fds, a regular file, /dev/null, both pipe ends, a
 * directory, a tracked closed fd and a reused number are 0; a pty reports the
 * column count TIOCSWINSZ set, never the row; a zero column count is 0
 * (unknown). No fd-table consultation means no exit 9 under OBO_CHECK, and the
 * case allocates nothing, so the leak oracle also passes. */
static void set_winsize(i64 fd, unsigned short row, unsigned short col) {
    unsigned short ws[4] = {row, col, 0, 0};
    CHECK(sys3(16, fd, 0x5414, (i64)ws) == 0);     /* TIOCSWINSZ */
}

static void case_termwidth_checks(const char *dir) {
    CHECK(rt_term_width(-1) == 0);
    CHECK(rt_term_width(-2) == 0);
    CHECK(rt_term_width(-1000000) == 0);
    char *p = ajoin(dir, "/file");
    make_file(p, "x");
    i64 fd = sys3(2, (i64)p, 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_term_width(fd) == 0);                 /* regular file: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    fd = sys3(2, (i64)"/dev/null", 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_term_width(fd) == 0);                 /* /dev/null: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    i64 pf[2];
    CHECK(sys3(293, (i64)pf, 0, 0) == 0);          /* pipe2(..., 0) */
    CHECK(rt_term_width(pf[0]) == 0);
    CHECK(rt_term_width(pf[1]) == 0);
    CHECK(sys3(3, pf[0], 0, 0) == 0 && sys3(3, pf[1], 0, 0) == 0);
    char *d = ajoin(dir, "/d");
    make_dir(d);
    fd = sys3(2, (i64)d, 0, 0);
    CHECK(fd >= 0);
    CHECK(rt_term_width(fd) == 0);                 /* directory: ENOTTY */
    CHECK(sys3(3, fd, 0, 0) == 0);
    /* a tracked descriptor that was closed is not a fatal use: terminalWidth
     * never consults the fd table, so the kernel's EBADF is simply 0 */
    fd = (i64)open_tracked(p, "r");
    close_tracked(fd);
    CHECK(rt_term_width(fd) == 0);
    /* the number may have been reused by the next open; the ioctl then observes
     * the reused descriptor, and a regular file is still 0, never a trap */
    i64 again = sys3(2, (i64)p, 0, 0);
    CHECK(again >= 0);
    CHECK(again == fd);                            /* the number really was reused */
    CHECK(rt_term_width(again) == 0);
    CHECK(sys3(3, again, 0, 0) == 0);
    /* the pty: a fresh one may report 0x0, so set a size and read it back; the
     * column is reported, not the row, and either end sets the same terminal */
    i64 m, s;
    open_pty(&m, &s);
    set_winsize(s, 24, 80);
    CHECK(rt_term_width(s) == 80);
    set_winsize(m, 43, 132);
    CHECK(rt_term_width(s) == 132);
    set_winsize(s, 24, 40000);                     /* above 16 bits' signed range: u16 widened */
    CHECK(rt_term_width(s) == 40000);
    set_winsize(s, 7, 0);                          /* a zero column count is unknown */
    CHECK(rt_term_width(s) == 0);
    CHECK(sys3(3, s, 0, 0) == 0 && sys3(3, m, 0, 0) == 0);
    rt_finish();
}

/* rt_init records env_base = sp + 8*(argc+2), and getenv reads the fixed
 * environment tests/meta_test.py sets: present, present-empty and absent, plus
 * the invalid names. Every returned array is owned and freed here. */
static void case_getenv_real(const char *dir) {
    (void)dir;
    CHECK(env_base == harness_sp + 8 * (harness_argc + 2));
    u64 v = rt_getenv(ostr("OBO_META_ENV_PRESENT"));
    expect_bytes(v, "hello", 5);
    rt_free(v);
    v = rt_getenv(ostr("OBO_META_ENV_EMPTY"));
    CHECK(v != 0);                                 /* present-empty is non-nil */
    CHECK(((Array *)v)->len == 0);
    rt_free(v);
    CHECK(rt_getenv(ostr("OBO_META_ENV_ABSENT")) == 0);
    CHECK(rt_getenv(ostr("")) == 0);               /* empty name */
    CHECK(rt_getenv(ostr("A=B")) == 0);            /* '=' in the name */
    CHECK(rt_getenv(ostrn("OBO_META_ENV\0PRESENT", 19)) == 0);   /* embedded NUL */
    rt_finish();
}

/* The crafted envp makes the scan rules observable: first match, the '='
 * terminator, no prefix match, the empty-name and '=' rules, a malformed entry,
 * and a copy the caller owns after the environment bytes change. */
static char c_dup1[] = "OBO_CRAFT_DUP=first";
static char c_dup2[] = "OBO_CRAFT_DUP=second";
static char c_empty[] = "OBO_CRAFT_EMPTY=";
static char c_eqname[] = "CRAFT=NAME=C";
static char c_prefix[] = "OBO_CRAFT_PREFIX=value";
static char c_esoteric[] = "=esoteric";
static char c_malformed[] = "OBO_CRAFT_MALFORMED";
static char c_short[] = "AB";
static char c_own[] = "OBO_CRAFT_OWN=owned";

static void case_getenv_crafted(const char *dir) {
    (void)dir;
    char *envp[10];
    envp[0] = c_dup1; envp[1] = c_dup2; envp[2] = c_empty; envp[3] = c_eqname;
    envp[4] = c_prefix; envp[5] = c_esoteric; envp[6] = c_malformed;
    envp[7] = c_short; envp[8] = c_own; envp[9] = 0;
    u64 saved = env_base;
    env_base = (u64)envp;
    u64 v = rt_getenv(ostr("OBO_CRAFT_DUP"));      /* first match wins */
    expect_bytes(v, "first", 5);
    rt_free(v);
    v = rt_getenv(ostr("OBO_CRAFT_EMPTY"));
    CHECK(v != 0);
    CHECK(((Array *)v)->len == 0);
    rt_free(v);
    CHECK(rt_getenv(ostr("OBO_CRAFT_ABSENT")) == 0);
    CHECK(rt_getenv(ostr("")) == 0);               /* "=esoteric" must not match */
    CHECK(rt_getenv(ostr("CRAFT=NAME")) == 0);     /* the '=' rule */
    CHECK(rt_getenv(ostr("OBO_CRAFT_PRE")) == 0);  /* a prefix is not a name */
    CHECK(rt_getenv(ostr("OBO_CRAFT_MALFORMED")) == 0);   /* entry without '=' */
    CHECK(rt_getenv(ostr("ABCD")) == 0);           /* entry shorter than the name */
    CHECK(rt_getenv(ostrn("OBO_CRAFT_DU\0P", 13)) == 0);  /* embedded NUL */
    u64 a = rt_getenv(ostr("OBO_CRAFT_OWN"));
    u64 b = rt_getenv(ostr("OBO_CRAFT_OWN"));
    expect_bytes(a, "owned", 5);
    CHECK(a != b);                                 /* a fresh copy each call */
    c_own[14] = 'X';                               /* change the environment bytes */
    expect_bytes(a, "owned", 5);                   /* the copies are unaffected */
    expect_bytes(b, "owned", 5);
    c_own[14] = 'o';
    rt_free(a);
    rt_free(b);
    env_base = saved;
    rt_finish();
}

/* one synthetic EINTR per call kind; every call must still succeed. Run with
 * OBO_CHECK=1 and OBO_META_FAULT=eintr (and, for the inert check, without
 * OBO_CHECK). */
static void case_eintr(const char *dir) {
    char *t = ajoin(dir, "/target");
    char *l = ajoin(dir, "/link");
    char *d = ajoin(dir, "/list");
    make_file(t, "hello");
    make_link("target", l);
    make_dir(d);
    make_file(ajoin(d, "/a"), "x");
    u64 cell = stat_call(t, 1);
    u64 *v = stat_fields(cell);
    CHECK((i64)v[1] == 5);
    rt_free(cell);
    cell = stat_call(t, 1);                            /* the fault is consumed once */
    v = stat_fields(cell);
    CHECK((i64)v[1] == 5);
    rt_free(cell);
    cell = stat_call(l, 0);
    v = stat_fields(cell);
    CHECK(((i64)v[0] & 0xF000) == 0xA000);
    rt_free(cell);
    cell = rt_read_link(ostr(l), (u64)desc_err, (u64)desc_ok_owned);
    readlink_ok_cell(cell, "target", 6);
    rt_free(cell);
    u64 fd = open_tracked(d, "r");
    cell = dir_read((i64)fd, 4096);
    CHECK(cell_payload(cell) != 0);
    rt_free(cell);
    for (;;) {
        cell = dir_read((i64)fd, 4096);
        if (cell_payload(cell) == 0) { rt_free(cell); break; }
        rt_free(cell);
    }
    close_tracked((i64)fd);
    /* the ioctl's EINTR retry: one synthetic EINTR, then the true answer */
    i64 m, s;
    open_pty(&m, &s);
    CHECK(rt_isatty(s) == 1);
    set_winsize(s, 24, 80);
    CHECK(rt_term_width(s) == 80);                     /* the win-size ioctl's EINTR retry */
    CHECK(rt_term_width(s) == 80);                     /* the fault is consumed once */
    CHECK(sys3(3, s, 0, 0) == 0 && sys3(3, m, 0, 0) == 0);
    rt_finish();
}

void test_main(u64 *sp);
__asm__(".globl _entry\n"
        ".section .text\n"
        "_entry:\n"
        "\tmov %rsp, %rdi\n"
        "\tcall test_main\n");

void test_main(u64 *sp) {
    harness_sp = (u64)sp;
    harness_argc = *(u64 *)sp;
    rt_init((u64)sp, 0, 0);
    u64 argc = *(u64 *)sp;
    char **argv = (char **)(sp + 1);
    if (argc < 2) fail(2);
    const char *c = argv[1];
    const char *path = argc > 2 ? argv[2] : "";
    if (c_eq(c, "stat_file")) case_stat_file(path);
    else if (c_eq(c, "stat_link")) case_stat_link(path);
    else if (c_eq(c, "stat_dir")) case_stat_dir(path);
    else if (c_eq(c, "stat_errors")) case_stat_errors(path);
    else if (c_eq(c, "stat_pre1970")) case_stat_pre1970(path);
    else if (c_eq(c, "stat_owner")) case_stat_owner(path);
    else if (c_eq(c, "readlink_ok")) case_readlink_ok(path);
    else if (c_eq(c, "readlink_errors")) case_readlink_errors(path);
    else if (c_eq(c, "readlink_long")) case_readlink_long(path);
    else if (c_eq(c, "readdir_ok")) case_readdir_ok(path);
    else if (c_eq(c, "readdir_empty")) case_readdir_empty(path);
    else if (c_eq(c, "readdir_checks")) case_readdir_checks(path);
    else if (c_eq(c, "readdir_closed")) case_readdir_closed(path);
    else if (c_eq(c, "isatty_checks")) case_isatty_checks(path);
    else if (c_eq(c, "termwidth_checks")) case_termwidth_checks(path);
    else if (c_eq(c, "getenv_real")) case_getenv_real(path);
    else if (c_eq(c, "getenv_crafted")) case_getenv_crafted(path);
    else if (c_eq(c, "eintr")) case_eintr(path);
    fail(3);
}
