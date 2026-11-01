/* P1b C harness for the real and scripted-fake adapters.
 *
 * Trusted C: drives the scalar adapter directly for anchored directory
 * mutation, links, rename, chmod/sync, openat2 containment, the logger queue
 * and environment snapshots. It leaves no live native resources.
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
    if (!(cond)) { fprintf(stderr, "p1b_c: %s failed: %s\n", what, #cond); return 1; } \
} while (0)

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

static void close_buf(long t) {
    if (t > 0) sys_res_close(t, SYS_KIND_BYTE_BUFFER);
}

int main(int argc, char **argv) {
    int fake = argc > 1 && strcmp(argv[1], "fake") == 0;
    char realdir[] = "/tmp/opencode/p1b-c-XXXXXX";
    char base[256];
    char path[320];
    long dt, n1, n2, n3, ft, bt, lb, sb, lt, logt;
    unsigned char *p = NULL;
    size_t n = 0;
    long raw;
    long readn;
    long envsize;

    if (fake) {
        snprintf(base, sizeof(base), "/p1b-c-dir");
    } else {
        CHECK(mkdtemp(realdir) != NULL, "mkdtemp");
        snprintf(base, sizeof(base), "%s", realdir);
    }

    CHECK(sys_adapter_init(4096, 64L * 1024L * 1024L) == 0, "init");
    if (fake && sysfake_reset != NULL) sysfake_reset();

    bt = make_buf(base, strlen(base));
    CHECK(bt > 0, "dir path buffer");
    dt = sys_dir_open(0, bt, 1);
    CHECK(dt > 0, "dir open");
    close_buf(bt);

    n1 = make_buf("sub1", 4);
    CHECK(n1 > 0, "mkdir name");
    CHECK(sys_dir_mkdir(dt, n1, 0755) == 1, "mkdir");
    CHECK(sys_dir_mkdir(dt, n1, 0755) == -EEXIST, "mkdir EEXIST");
    close_buf(n1);

    ft = make_buf("config", 6);
    CHECK(ft > 0, "file name");
    raw = sys_file_open(dt, ft, SYS_OPEN_READ | SYS_OPEN_WRITE | SYS_OPEN_CREATE |
                        SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE, 0600);
    CHECK(raw > 0, "file create");
    {
        long f = raw;
        sb = make_buf("value", 5);
        CHECK(sb > 0, "write buffer");
        CHECK(sys_io_write(f, SYS_KIND_FILE, sb, 0, 5, -1) == 5, "write");
        close_buf(sb);
        CHECK(sys_file_chmod(f, SYS_KIND_FILE, 0640) == 1, "chmod");
        CHECK(sys_file_sync(f, SYS_KIND_FILE, 1) == 1, "fdatasync");
        CHECK(sys_file_sync(f, SYS_KIND_FILE, 0) == 1, "fsync");
        CHECK(sys_res_close(f, SYS_KIND_FILE) == 1, "file close");

        n2 = make_buf("config2", 7);
        CHECK(n2 > 0, "rename target");
        CHECK(sys_file_rename(dt, ft, dt, n2, 0) == 1, "rename no-replace");
        CHECK(sys_file_rename(dt, ft, dt, n2, 0) == -ENOENT,
              "renamed source is gone");
        {
            long again = sys_file_open(dt, ft, SYS_OPEN_READ | SYS_OPEN_WRITE |
                                       SYS_OPEN_CREATE, 0600);
            CHECK(again > 0, "recreate replace target");
            CHECK(sys_res_close(again, SYS_KIND_FILE) == 1, "replace target close");
        }
        CHECK(sys_file_rename(dt, n2, dt, ft, 1) == 1, "rename replace");
        close_buf(n2);
        CHECK(sys_file_link(dt, ft, dt, n3 = make_buf("configlink", 10)) == 1,
              "link");
        close_buf(n3);
        n3 = make_buf("conflink", 8);
        CHECK(n3 > 0, "symlink name");
        sb = make_buf("config", 6);
        CHECK(sb > 0, "symlink target");
        CHECK(sys_file_symlink(sb, dt, n3) == 1, "symlink");
        lb = sys_res_new(SYS_KIND_BYTE_BUFFER, 64, 0);
        CHECK(lb > 0, "readlink buffer");
        readn = sys_dir_readlink(dt, n3, lb, 64);
        CHECK(readn == 6, "readlink length");
        CHECK(sys_reg_buffer_data(lb, &p, &n) == 0 && n >= 6, "readlink data");
        CHECK(memcmp(p, "config", 6) == 0, "readlink target");
        CHECK(sys_dir_readlink(dt, ft, lb, 64) == -EINVAL, "readlink non-link");
        close_buf(lb);
        close_buf(sb);
        CHECK(sys_dir_unlink(dt, n3) == 1, "unlink symlink");
        CHECK(sys_dir_unlink(dt, n3) == -ENOENT, "unlink ENOENT");
        close_buf(n3);

        n3 = make_buf("config", 6);
        CHECK(n3 > 0, "config reopen name");
        p = NULL;
        raw = sys_file_open(dt, n3, SYS_OPEN_READ, 0);
        CHECK(raw > 0, "open rooted file");
        close_buf(n3);
        if (sys_file_open_beneath_supported() == 1) {
            long beneath;
            n3 = make_buf("config", 6);
            beneath = sys_file_open_beneath(dt, n3, SYS_OPEN_READ, 0);
            CHECK(beneath > 0, "openBeneath relative");
            CHECK(sys_res_close(beneath, SYS_KIND_FILE) == 1, "beneath close");
            close_buf(n3);
            n3 = make_buf("../config", 9);
            CHECK(sys_file_open_beneath(dt, n3, SYS_OPEN_READ, 0) == -EXDEV,
                  "openBeneath parent escape");
            close_buf(n3);
            n3 = make_buf("/etc/passwd", 11);
            CHECK(sys_file_open_beneath(dt, n3, SYS_OPEN_READ, 0) == -EXDEV,
                  "openBeneath absolute escape");
            close_buf(n3);
        } else {
            /* Old kernels must report Unsupported, not fall back to a lexical
             * prefix check. */
            n3 = make_buf("config", 6);
            CHECK(sys_file_open_beneath(dt, n3, SYS_OPEN_READ, 0) == -ENOSYS,
                  "openat2 absent returns ENOSYS");
            close_buf(n3);
        }
        CHECK(sys_res_close(raw, SYS_KIND_FILE) == 1, "rooted file close");
    }

    n1 = make_buf("sub1", 4);
    CHECK(n1 > 0, "rmdir name");
    CHECK(sys_dir_remove_empty(dt, n1) == 1, "removeEmpty");
    CHECK(sys_dir_remove_empty(dt, n1) == -ENOENT, "removeEmpty ENOENT");
    CHECK(sys_dir_unlink(dt, n1) == -ENOENT, "unlink missing");
    close_buf(n1);
    CHECK(sys_dir_sync(dt) == 1, "directory sync");
    CHECK(sys_res_close(dt, SYS_KIND_DIRECTORY) == 1, "dir close");
    close_buf(ft);

    envsize = sys_env_size();
    CHECK(envsize > 0, "environment size");
    lt = sys_res_new(SYS_KIND_BYTE_BUFFER, envsize, 0);
    CHECK(lt > 0, "environment buffer");
    CHECK(sys_env_snapshot(lt, envsize) == envsize, "environment snapshot");
    CHECK(sys_res_close(lt, SYS_KIND_BYTE_BUFFER) == 1, "environment close");

    logt = sys_log_open(2, 256);
    CHECK(logt > 0, "log open");
    bt = make_buf("p1b-c-log\n", 10);
    CHECK(sys_log_emit(logt, bt, 0, 10, 0) == 1, "log emit");
    CHECK(sys_log_queued(logt) == 10, "log queued");
    CHECK(sys_log_flush(logt) == 1, "log flush");
    CHECK(sys_log_queued(logt) == 0, "log drained");
    CHECK(sys_log_dropped(logt) == 0, "log dropped zero");
    close_buf(bt);
    CHECK(sys_res_close(logt, SYS_KIND_LOGGER) == 1, "log close");

    CHECK(sys_res_live_count() == 0, "no live native resources");
    CHECK(sys_adapter_shutdown() == 0, "shutdown");
    if (!fake) {
        snprintf(path, sizeof(path), "%s/config", realdir);
        unlink(path);
        snprintf(path, sizeof(path), "%s/config2", realdir);
        unlink(path);
        snprintf(path, sizeof(path), "%s/configlink", realdir);
        unlink(path);
        snprintf(path, sizeof(path), "%s/sub1", realdir);
        rmdir(path);
        rmdir(realdir);
    }
    return 0;
}
