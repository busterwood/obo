/* rt_io_test: the direct oracle for the runtime's I/O contract (design section 7,
 * N11). It links runtime.c into its own image (so it may use alloc_raw and the
 * string helpers) and has its own _entry, which passes the kernel stack to
 * rt_init; the environment therefore reaches the runtime exactly as it does for
 * a compiled program.
 *
 * Each case runs in a process of its own: the test driver runs this binary once
 * per case, with the case name in argv[1] and a scratch file path in argv[2].
 * A case that should end normally calls rt_finish (exit 0, the leak oracle); the
 * cases that check exits 8 and 9 end in the runtime's own traps. An assertion
 * failure exits 1.
 *
 *     python3 obo/tests/io_test.py --rt      (builds and runs every case)
 */
#include "../runtime/runtime.c"

/* the descriptors the compiler passes: err [16, 0, 1, 0], scalar ok [8, 0, 0],
 * owned ok [8, 1, 0, 0]. They live in .data, as generated descriptors do. */
static u64 desc_err[4] = {16, 0, 1, 0};
static u64 desc_ok_int[3] = {8, 0, 0};
static u64 desc_ok_bool[3] = {8, 0, 0};
static u64 desc_ok_owned[4] = {8, 1, 0, 0};

static void fail(i64 code) { sys3(60, code, 0, 0); for (;;); }
#define CHECK(c) do { if (!(c)) fail(1); } while (0)

/* Argument strings live in .data, like a program's literals (address below the
 * heap, so rt_str_dec ignores them); the harness frees no strings of its own. */
static char cmstr[2048];
static u64 cmstr_used;
static u64 mkstr(const char *s) {
    u64 n = 0; while (s[n]) n++;
    u64 off = (cmstr_used + 7) & ~7UL;
    u64 p = (u64)&cmstr[off];
    *(u64 *)p = n;
    memcpy((char *)p + 8, s, n);
    cmstr_used = off + 8 + ((n + 7) & ~7UL);
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
static u64 cell_tag(u64 c) { return (*(u64 *)(c - GRAN)) & 0xFFFFFFFFUL; }
static u64 cell_payload(u64 c) { return *(u64 *)c; }
static u64 cell_msg(u64 c) { return *(u64 *)c; }
static i64 cell_code(u64 c) { return (i64)((u64 *)c)[1]; }
static Array *bytes(u64 n, int fill) {
    Array *a = (Array *)rt_alloc_array((i64)n, 4);
    for (u64 i = 0; i < n; i++) ((unsigned char *)(a->data + 8))[i] = (unsigned char)(fill + i);
    return a;
}
static int bytes_are(Array *a, u64 n, int fill) {
    if (a->len != n) return 0;
    for (u64 i = 0; i < n; i++) if (((unsigned char *)(a->data + 8))[i] != (unsigned char)(fill + i)) return 0;
    return 1;
}
static u64 open_ok(const char *path, const char *mode) {
    u64 cell = rt_open_file(mkstr(path), mkstr(mode), (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    i64 fd = (i64)cell_payload(cell);
    CHECK(fd >= 0);
    rt_free(cell);
    return (u64)fd;
}
static void close_ok(u64 fd) {
    u64 cell = rt_close_fd((i64)fd, (u64)desc_err, (u64)desc_ok_bool);
    CHECK(cell_tag(cell) == (u64)desc_ok_bool);
    CHECK(cell_payload(cell) == 1);
    rt_free(cell);
}
static i64 now_ms(void) {
    struct { i64 sec, nsec; } ts;
    CHECK(sys3(228, 1, (i64)&ts, 0) == 0);          /* clock_gettime(CLOCK_MONOTONIC) */
    return ts.sec * 1000 + ts.nsec / 1000000;
}

/* the cell layout: the tag is the descriptor passed, the ok payload is at +0,
 * the err message at +0 and the code at +8; the descriptors free what they own. */
static void case_layout(const char *path) {
    (void)path;
    u64 cell = rt_open_file(mkstr("/dev/null"), mkstr("r"), (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    u64 fd = cell_payload(cell);
    CHECK(fd < 1024);
    rt_free(cell);
    close_ok(fd);
    cell = rt_open_file(mkstr("/nonexistent-obo-rt-io"), mkstr("r"), (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_msg(cell) != 0);
    CHECK(str_eq_c(cell_msg(cell), "cannot open /nonexistent-obo-rt-io"));
    CHECK(cell_code(cell) == 2);                       /* ENOENT */
    rt_free(cell);                                     /* the err descriptor frees the message */
    rt_finish();
}

/* readFd's success cells: EOF is nil with an ok tag, not an empty array; a
 * short read is the truncated array; freeing the cell frees the array. */
static void case_read_eof(const char *path) {
    (void)path;
    u64 fd = open_ok("/dev/null", "r");
    u64 cell = rt_read_fd((i64)fd, 16, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    CHECK(cell_payload(cell) == 0);
    rt_free(cell);
    close_ok(fd);
    rt_finish();
}

/* max <= 0 is EINVAL; it is checked before fd (a computed zero with fd -1 is
 * EINVAL, not EBADF), and no max-sized array is allocated. */
static void case_max_zero(const char *path) {
    (void)path;
    u64 cell = rt_read_fd(0, 0, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "readFd needs a positive count"));
    rt_free(cell);
    cell = rt_read_fd(-1, 0, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    rt_free(cell);
    rt_finish();
}

/* fd < 0 with a huge max is EBADF with no allocation: an OOM here would exit 2. */
static void case_neg_big(const char *path) {
    (void)path;
    u64 cell = rt_read_fd(-1, (i64)1 << 40, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);
    CHECK(str_eq_c(cell_msg(cell), "cannot read fd -1"));
    rt_free(cell);
    rt_finish();
}

/* the standard descriptors are untracked and cannot be closed through the API */
static void case_std_close(const char *path) {
    (void)path;
    for (i64 fd = 0; fd < 3; fd++) {
        u64 cell = rt_close_fd(fd, (u64)desc_err, (u64)desc_ok_bool);
        CHECK(cell_tag(cell) == (u64)desc_err);
        CHECK(cell_code(cell) == 22);
        rt_free(cell);
    }
    rt_finish();
}

/* write one buffer, read it back through a larger max (a short read is data,
 * truncated into the array), and free the cell: the owned descriptor frees it. */
static void case_write_read(const char *path) {
    u64 fd = open_ok(path, "w");
    Array *a = bytes(10, 0);
    u64 cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 10);
    rt_free(cell);
    rt_free((u64)a);
    close_ok(fd);
    fd = open_ok(path, "r");
    cell = rt_read_fd((i64)fd, 100, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    Array *got = (Array *)cell_payload(cell);
    CHECK(got != 0);
    CHECK(bytes_are(got, 10, 0));
    rt_free(cell);                                     /* frees the array through the descriptor */
    close_ok(fd);
    rt_finish();
}

/* a tracked, open fd that the program forgets: exit 8 before the object check */
static void case_leak(const char *path) {
    u64 fd = open_ok(path, "w");
    (void)fd;
    rt_finish();
}

/* open, close, close: the second attempt traps under check; without check it
 * is an err cell with EBADF and the program ends normally. */
static void case_double_close(const char *path) {
    u64 fd = open_ok(path, "w");
    close_ok(fd);
    u64 cell = rt_close_fd((i64)fd, (u64)desc_err, (u64)desc_ok_bool);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);
    rt_free(cell);
    rt_finish();
}

/* N1: an empty write to a tracked closed fd is the fd error, never ok(0) */
static void case_empty_write_closed(const char *path) {
    u64 fd = open_ok(path, "w");
    close_ok(fd);
    Array *a = bytes(0, 0);
    u64 cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);
    rt_free(cell);
    rt_free((u64)a);
    rt_finish();
}

/* N2: a huge read from a tracked closed fd is the fd error, not an OOM */
static void case_closed_big_read(const char *path) {
    u64 fd = open_ok(path, "w");
    close_ok(fd);
    u64 cell = rt_read_fd((i64)fd, (i64)1 << 40, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);
    rt_free(cell);
    rt_finish();
}

/* a close that fails with EBADF (the kernel fd was closed behind the table's
 * back) still marks the tracked number closed (N4): rt_finish reports no leak,
 * and without OBO_CHECK a second close is the table's EBADF, never a syscall on
 * a number the kernel may have reused. */
static void case_close_ebadf(const char *path) {
    u64 fd = open_ok(path, "w");
    sys3(3, (i64)fd, 0, 0);             /* the kernel closes it; the table still says open */
    u64 cell = rt_close_fd((i64)fd, (u64)desc_err, (u64)desc_ok_bool);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 9);        /* the failed close is itself reported */
    rt_free(cell);
    if (!leak_check) {
        cell = rt_close_fd((i64)fd, (u64)desc_err, (u64)desc_ok_bool);
        CHECK(cell_tag(cell) == (u64)desc_err);
        CHECK(cell_code(cell) == 9);
        rt_free(cell);
    }
    rt_finish();                        /* a table left open would exit 8 */
}

/* "w" is O_TRUNC: a 3-byte rewrite of an 8-byte file leaves exactly 3 bytes */
static void case_trunc(const char *path) {
    u64 fd = open_ok(path, "w");
    Array *a = bytes(8, 0);
    u64 cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 8);
    rt_free(cell);
    rt_free((u64)a);
    close_ok(fd);
    fd = open_ok(path, "w");             /* truncates the 8 bytes away */
    a = bytes(3, 7);
    cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 3);
    rt_free(cell);
    rt_free((u64)a);
    close_ok(fd);
    fd = open_ok(path, "r");
    cell = rt_read_fd((i64)fd, 16, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    CHECK(bytes_are((Array *)cell_payload(cell), 3, 7));
    rt_free(cell);
    close_ok(fd);
    rt_finish();
}

/* a computed mode that is none of "r", "w" and "a" is EINVAL, before any open */
static void case_unknown_mode(const char *path) {
    (void)path;
    u64 cell = rt_open_file(mkstr("/nonexistent-obo-rt-io"), mkstr("z"),
                            (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "unknown open mode"));
    rt_free(cell);
    rt_finish();
}

/* one synthetic EINTR per read, write and close: all three complete; the close
 * is ok(true) and marks the fd closed, so rt_finish reports no leak */
static void case_eintr(const char *path) {
    u64 fd = open_ok(path, "w");
    Array *a = bytes(5, 1);
    u64 cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 5);
    rt_free(cell);
    rt_free((u64)a);
    close_ok(fd);                       /* synthetic EINTR: ok(true) */
    fd = open_ok(path, "r");
    cell = rt_read_fd((i64)fd, 16, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    CHECK(bytes_are((Array *)cell_payload(cell), 5, 1));
    rt_free(cell);
    close_ok(fd);
    rt_finish();
}

/* io_fault_short caps each write at 3 bytes: the helper returns the short count
 * and the caller (here the harness) loops; the full content must arrive. */
static void case_short(const char *path) {
    u64 fd = open_ok(path, "w");
    Array *a = bytes(10, 3);
    u64 done = 0;
    while (done < 10) {
        Array *part = (Array *)rt_alloc_array((i64)(10 - done), 4);
        for (u64 i = 0; i < 10 - done; i++)
            ((unsigned char *)(part->data + 8))[i] = ((unsigned char *)(a->data + 8))[done + i];
        u64 cell = rt_write_fd((i64)fd, part, (u64)desc_err, (u64)desc_ok_int);
        CHECK(cell_tag(cell) == (u64)desc_ok_int);
        u64 n = cell_payload(cell);
        CHECK(n > 0 && n <= 3);
        done += n;
        rt_free(cell);
        rt_free((u64)part);
    }
    rt_free((u64)a);
    close_ok(fd);
    fd = open_ok(path, "r");
    u64 cell = rt_read_fd((i64)fd, 16, (u64)desc_err, (u64)desc_ok_owned);
    CHECK(cell_tag(cell) == (u64)desc_ok_owned);
    CHECK(bytes_are((Array *)cell_payload(cell), 10, 3));
    rt_free(cell);
    close_ok(fd);
    rt_finish();
}

/* without OBO_CHECK the hook is inert: one write of 10 bytes returns 10 */
static void case_inert(const char *path) {
    u64 fd = open_ok(path, "w");
    Array *a = bytes(10, 0);
    u64 cell = rt_write_fd((i64)fd, a, (u64)desc_err, (u64)desc_ok_int);
    CHECK(cell_tag(cell) == (u64)desc_ok_int);
    CHECK(cell_payload(cell) == 10);
    rt_free(cell);
    rt_free((u64)a);
    close_ok(fd);
    rt_finish();
}

/* sleepMs: zero returns at once, a positive count does not return early, and
 * the invalid counts are Result errors with no wait. The lower bound is below
 * the requested time so scheduler/syscall overhead cannot make it flaky. */
static void case_sleep(const char *path) {
    (void)path;
    i64 t0 = now_ms();
    u64 cell = rt_sleep(0, (u64)desc_err, (u64)desc_ok_bool);
    i64 t1 = now_ms();
    CHECK(cell_tag(cell) == (u64)desc_ok_bool);
    CHECK(cell_payload(cell) == 1);
    CHECK(t1 - t0 < 1000);
    rt_free(cell);

    t0 = now_ms();
    cell = rt_sleep(30, (u64)desc_err, (u64)desc_ok_bool);
    t1 = now_ms();
    CHECK(cell_tag(cell) == (u64)desc_ok_bool);
    CHECK(cell_payload(cell) == 1);
    CHECK(t1 - t0 >= 20);
    rt_free(cell);

    cell = rt_sleep(-1, (u64)desc_err, (u64)desc_ok_bool);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 22);
    CHECK(str_eq_c(cell_msg(cell), "sleepMs needs a non-negative count"));
    rt_free(cell);
    cell = rt_sleep(MAX_SLEEP_MS + 1, (u64)desc_err, (u64)desc_ok_bool);
    CHECK(cell_tag(cell) == (u64)desc_err);
    CHECK(cell_code(cell) == 34);
    CHECK(str_eq_c(cell_msg(cell), "sleepMs count is too large"));
    rt_free(cell);
    rt_finish();
}

/* the OBO_META_FAULT=eintr hook injects one EINTR before the real nanosleep;
 * the retry must still wait and return ok(true), with no leak */
static void case_sleep_eintr(const char *path) {
    (void)path;
    i64 t0 = now_ms();
    u64 cell = rt_sleep(20, (u64)desc_err, (u64)desc_ok_bool);
    i64 t1 = now_ms();
    CHECK(cell_tag(cell) == (u64)desc_ok_bool);
    CHECK(cell_payload(cell) == 1);
    CHECK(t1 - t0 >= 10);
    rt_free(cell);
    rt_finish();
}

void test_main(u64 *sp);
__asm__(".globl _entry\n"
        ".section .text\n"
        "_entry:\n"
        "\tmov %rsp, %rdi\n"
        "\tcall test_main\n");

void test_main(u64 *sp) {
    rt_init((u64)sp, 0, 0);
    u64 argc = *(u64 *)sp;
    char **argv = (char **)(sp + 1);
    if (argc < 2) fail(2);
    const char *c = argv[1];
    const char *path = argc > 2 ? argv[2] : "";
    if (c_eq(c, "layout")) case_layout(path);
    else if (c_eq(c, "read_eof")) case_read_eof(path);
    else if (c_eq(c, "max_zero")) case_max_zero(path);
    else if (c_eq(c, "neg_big")) case_neg_big(path);
    else if (c_eq(c, "std_close")) case_std_close(path);
    else if (c_eq(c, "write_read")) case_write_read(path);
    else if (c_eq(c, "leak")) case_leak(path);
    else if (c_eq(c, "double_close")) case_double_close(path);
    else if (c_eq(c, "empty_write_closed")) case_empty_write_closed(path);
    else if (c_eq(c, "closed_big_read")) case_closed_big_read(path);
    else if (c_eq(c, "close_ebadf")) case_close_ebadf(path);
    else if (c_eq(c, "trunc")) case_trunc(path);
    else if (c_eq(c, "unknown_mode")) case_unknown_mode(path);
    else if (c_eq(c, "eintr")) case_eintr(path);
    else if (c_eq(c, "short")) case_short(path);
    else if (c_eq(c, "inert")) case_inert(path);
    else if (c_eq(c, "sleep")) case_sleep(path);
    else if (c_eq(c, "sleep_eintr")) case_sleep_eintr(path);
    fail(3);
}
