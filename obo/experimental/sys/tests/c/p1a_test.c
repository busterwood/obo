/* P1a C harness for the real and scripted-fake adapters.
 *
 * Trusted C: it drives the scalar adapter directly, checks file/dir/stat/
 * clock/random behavior, and leaves no live native resources. The same source
 * builds against either adapter; argv[1] "fake" only selects the fixture paths
 * and clears the fake in-memory store before shutdown.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define CHECK(cond, what) do { \
    if (!(cond)) { fprintf(stderr, "p1a_c: %s failed: %s\n", what, #cond); return 1; } \
} while (0)

/* Present only in the fake link unit; the real adapter has no such symbol. */
extern long sysfake_reset(void) __attribute__((weak));

static long make_buf(const char *s, size_t len) {
    long t = sys_res_new(SYS_KIND_BYTE_BUFFER, (long)len, 0);
    unsigned char *p = NULL;
    size_t n = 0;
    size_t copy;
    if (t < 0) return t;
    if (sys_reg_buffer_data(t, &p, &n) < 0 || n < len) return -EIO;
    copy = strlen(s);
    if (copy > len) copy = len;
    memcpy(p, s, copy);
    return t;
}

static int64_t packet_field(long buf, long index) {
    unsigned char *p = NULL;
    size_t n = 0;
    int64_t v = 0;
    if (sys_reg_buffer_data(buf, &p, &n) < 0) return 0;
    if (n < (size_t)(index + 1) * 8) return 0;
    memcpy(&v, p + index * 8, sizeof(v));
    return v;
}

int main(int argc, char **argv) {
    int fake = argc > 1 && strcmp(argv[1], "fake") == 0;
    char realdir[] = "/tmp/opencode/p1a-c-XXXXXX";
    char workdir[] = "/tmp/opencode/p1a-w-XXXXXX";
    char work[256];
    char dirpath[256];
    long pt, ft, dt, it, bt, sb;
    long dup, retag;
    unsigned char *p = NULL;
    size_t n = 0;
    long raw;
    long nread;
    long count;
    int saw_alpha = 0;
    int saw_beta = 0;
    int64_t id1;
    int64_t id2;

    if (fake) {
        snprintf(work, sizeof(work), "/p1a-c-work");
        snprintf(dirpath, sizeof(dirpath), "/p1a-c-dir");
    } else {
        char alpha[320];
        char beta[320];
        int fd;
        CHECK(mkdtemp(realdir) != NULL, "mkdtemp");
        CHECK(mkdtemp(workdir) != NULL, "mkdtemp work");
        snprintf(work, sizeof(work), "%s/work", workdir);
        snprintf(dirpath, sizeof(dirpath), "%s", realdir);
        snprintf(alpha, sizeof(alpha), "%s/alpha", realdir);
        snprintf(beta, sizeof(beta), "%s/beta", realdir);
        fd = open(alpha, O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
        CHECK(fd >= 0, "create alpha");
        close(fd);
        fd = open(beta, O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
        CHECK(fd >= 0, "create beta");
        close(fd);
        /* Enough long names to force several 8 KiB getdents64 refills. */
        {
            int i;
            char extra[512];
            for (i = 0; i < 300; i++) {
                snprintf(extra, sizeof(extra),
                         "%s/e%03d-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                         "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                         "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                         "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                         realdir, i);
                fd = open(extra, O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
                CHECK(fd >= 0, "create refill file");
                close(fd);
            }
        }
    }

    CHECK(sys_adapter_init(4096, 64L * 1024L * 1024L) == 0, "init");

    pt = make_buf(work, strlen(work));
    CHECK(pt > 0, "path buffer");
    raw = sys_file_open(0, pt, SYS_OPEN_READ | SYS_OPEN_WRITE | SYS_OPEN_CREATE |
                        SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE, 0600);
    CHECK(raw > 0, "file open create");
    ft = raw;

    bt = make_buf("hello", 5);
    CHECK(bt > 0, "write buffer");
    CHECK(sys_io_write(ft, SYS_KIND_FILE, bt, 0, 5, -1) == 5, "write 5");
    CHECK(sys_res_close(bt, SYS_KIND_BYTE_BUFFER) == 1, "write buffer close");
    CHECK(sys_io_seek(ft, SYS_KIND_FILE, 0, SYS_SEEK_START) == 0, "seek 0");
    bt = make_buf("", 16);
    CHECK(bt > 0, "read buffer");
    nread = sys_io_read(ft, SYS_KIND_FILE, bt, 0, 5, -1);
    CHECK(nread == 5, "read 5");
    CHECK(sys_reg_buffer_data(bt, &p, &n) == 0, "read buffer data");
    CHECK(memcmp(p, "hello", 5) == 0, "read contents");

    sb = sys_res_new(SYS_KIND_BYTE_BUFFER, SYS_STAT_PACKET_BYTES, 0);
    CHECK(sb > 0, "stat packet");
    CHECK(sys_stat_get(ft, SYS_KIND_FILE, 0, 0, 0, sb) == 0, "fstat");
    CHECK(packet_field(sb, 0) == 8, "regular kind");
    CHECK(packet_field(sb, 1) == 0600, "mode 0600");
    CHECK(packet_field(sb, 2) == 5, "size 5");
    id1 = packet_field(sb, 15);
    CHECK(id1 > 0, "identity bits");
    CHECK(sys_stat_get(ft, SYS_KIND_FILE, 0, 0, 0, sb) == 0, "fstat 2");
    id2 = packet_field(sb, 15);
    CHECK(id2 == id1, "identity stable");
    CHECK(sys_io_truncate(ft, SYS_KIND_FILE, 3) == 1, "truncate");
    CHECK(sys_stat_get(ft, SYS_KIND_FILE, 0, 0, 0, sb) == 0, "fstat 3");
    CHECK(packet_field(sb, 2) == 3, "size 3");

    dup = sys_file_dup(ft, SYS_KIND_FILE, SYS_KIND_STREAM);
    CHECK(dup > 0, "dup stream");
    CHECK(sys_io_seek(dup, SYS_KIND_STREAM, 0, SYS_SEEK_START) == 0, "dup seek");
    retag = sys_file_retag(dup, SYS_KIND_STREAM, SYS_KIND_STREAM);
    CHECK(retag == 1, "retag same kind");
    CHECK(sys_res_close(dup, SYS_KIND_STREAM) == 1, "dup close");
    CHECK(sys_res_close(ft, SYS_KIND_FILE) == 1, "file close");
    CHECK(sys_res_close(bt, SYS_KIND_BYTE_BUFFER) == 1, "read buffer close");
    CHECK(sys_res_close(sb, SYS_KIND_BYTE_BUFFER) == 1, "stat buffer close");
    CHECK(sys_res_close(pt, SYS_KIND_BYTE_BUFFER) == 1, "path buffer close");

    /* Reopen and transfer File -> Stream by re-tagging. */
    pt = make_buf(work, strlen(work));
    CHECK(pt > 0, "reopen path buffer");
    ft = sys_file_open(0, pt, SYS_OPEN_READ, 0);
    CHECK(ft > 0, "reopen read");
    CHECK(sys_file_retag(ft, SYS_KIND_FILE, SYS_KIND_STREAM) == 1, "retag file");
    bt = make_buf("", 8);
    CHECK(bt > 0, "stream read buffer");
    CHECK(sys_io_read(ft, SYS_KIND_STREAM, bt, 0, 3, 0) == 3, "stream positional read");
    CHECK(sys_res_close(ft, SYS_KIND_STREAM) == 1, "stream close");
    CHECK(sys_res_close(bt, SYS_KIND_BYTE_BUFFER) == 1, "stream buf close");
    CHECK(sys_res_close(pt, SYS_KIND_BYTE_BUFFER) == 1, "reopen path close");

    /* Directory iteration. */
    pt = make_buf(dirpath, strlen(dirpath));
    CHECK(pt > 0, "dir path buffer");
    dt = sys_dir_open(pt, 1);
    CHECK(dt > 0, "dir open");
    it = sys_dir_iterate(dt);
    CHECK(it > 0, "dir iterate");
    bt = make_buf("", 256);
    CHECK(bt > 0, "name buffer");
    count = 0;
    for (;;) {
        raw = sys_dir_next(it, bt, 256);
        CHECK(raw >= 0, "dir next");
        if (raw == 0) break;
        CHECK((raw / 256) > 0, "name length");
        CHECK(sys_reg_buffer_data(bt, &p, &n) == 0, "name data");
        if ((raw / 256) == 5 && memcmp(p, "alpha", 5) == 0) saw_alpha = 1;
        if ((raw / 256) == 4 && memcmp(p, "beta", 4) == 0) saw_beta = 1;
        count++;
    }
    CHECK(count == (fake ? 3 : 302), "dir entry count");
    CHECK(saw_alpha && saw_beta, "dir names");
    CHECK(sys_res_close(it, SYS_KIND_ITERATOR) == 1, "iterator close");
    CHECK(sys_res_close(dt, SYS_KIND_DIRECTORY) == 1, "dir close");
    CHECK(sys_res_close(bt, SYS_KIND_BYTE_BUFFER) == 1, "name buffer close");
    CHECK(sys_res_close(pt, SYS_KIND_BYTE_BUFFER) == 1, "dir path close");

    /* Clocks, sleep and randomness. */
    {
        long mono = sys_clock_monotonic_ns();
        long sec = sys_clock_realtime_sec();
        long nsec = sys_clock_realtime_nsec();
        CHECK(mono >= 0, "monotonic");
        CHECK(nsec >= 0 && nsec <= 999999999L, "realtime nanos");
        (void)sec;
        CHECK(sys_sleep_until(mono) == 1, "sleep until now");
    }
    bt = make_buf("", 32);
    CHECK(bt > 0, "random buffer");
    CHECK(sys_random(bt, 0, 32, 1) == 32, "random 32");
    CHECK(sys_res_close(bt, SYS_KIND_BYTE_BUFFER) == 1, "random buffer close");

    CHECK(sys_res_live_count() == 0, "no live resources");
    if (fake && sysfake_reset != NULL) (void)sysfake_reset();
    raw = sys_adapter_shutdown();
    CHECK(raw == 0, "shutdown clean");

    if (!fake) {
        char alpha[320];
        char beta[320];
        snprintf(alpha, sizeof(alpha), "%s/alpha", realdir);
        snprintf(beta, sizeof(beta), "%s/beta", realdir);
        int i;
        char extra[512];
        for (i = 0; i < 300; i++) {
            snprintf(extra, sizeof(extra),
                     "%s/e%03d-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                     "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                     "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                     "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                     realdir, i);
            unlink(extra);
        }
        unlink(alpha);
        unlink(beta);
        unlink(work);
        rmdir(workdir);
        rmdir(realdir);
    }
    printf("p1a_c: %s ok\n", fake ? "fake" : "real");
    return 0;
}
