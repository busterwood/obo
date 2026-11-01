/* P3 descriptor-inheritance regression harness.
 *
 * Deliberately closes 0/1/2 in the parent, reports through a saved descriptor,
 * and checks the child's view through /proc/self/fd plus a non-CLOEXEC marker.
 * Covers prepared-fd collisions (B1) and unintentional-fd inheritance (B2) on
 * both the posix_spawn and anchored fork/exec paths.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static int report_fd = -1;

static void report(const char *fmt, ...) {
    va_list ap;
    char buf[4096];
    int n;
    va_start(ap, fmt);
    n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        ssize_t ignored = write(report_fd, buf, (size_t)n);
        (void)ignored;
    }
}

static long mkdata(const unsigned char *data, size_t len) {
    long t = sys_reg_new_buffer((long)len);
    unsigned char *p = NULL;
    size_t l = 0;
    if (t < 0) return t;
    if (sys_reg_buffer_data(t, &p, &l) < 0 || l < len) return -EIO;
    if (len > 0) memcpy(p, data, len);
    return t;
}

static long mktext(const char *text) {
    return mkdata((const unsigned char *)text, strlen(text));
}

static long mkargv(int count, const char *const *args) {
    size_t total = 0;
    int i;
    size_t at = 0;
    unsigned char *blob;
    long t;
    for (i = 0; i < count; i++) total += strlen(args[i]) + 1;
    blob = (unsigned char *)malloc(total);
    if (blob == NULL) return -ENOMEM;
    for (i = 0; i < count; i++) {
        size_t n = strlen(args[i]) + 1;
        memcpy(blob + at, args[i], n);
        at += n;
    }
    t = mkdata(blob, total);
    free(blob);
    return t;
}

static long plan_new(const char *exec, int argc, const char *const *argv) {
    long plan = sys_proc_plan_new();
    long b;
    if (plan < 0) return plan;
    b = mktext(exec);
    if (b < 0 || sys_proc_plan_set(plan, SYS_OP_PLAN_EXEC, b, 0) < 0) return -EIO;
    (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
    b = mkargv(argc, argv);
    if (b < 0 || sys_proc_plan_set(plan, SYS_OP_PLAN_ARGV, b, 0) < 0) return -EIO;
    (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
    if (sys_proc_plan_set(plan, SYS_OP_PLAN_ENV_MODE, 0, 0) < 0) return -EIO;
    return plan;
}

static char *read_file(const char *path) {
    int fd = open(path, O_RDONLY);
    char *out;
    ssize_t n;
    if (fd < 0) return NULL;
    out = (char *)calloc(1, 4096);
    if (out == NULL) { (void)close(fd); return NULL; }
    n = read(fd, out, 4095);
    if (n < 0) n = 0;
    out[n] = '\0';
    (void)close(fd);
    return out;
}

static int wait_child(long child) {
    long raw;
    do {
        raw = sys_proc_wait(child, 1);
    } while (raw == -EINTR);
    return raw < 0 ? -1 : 0;
}

static long open_blocking_source(const char *path) {
    long pb = mktext(path);
    long f;
    if (pb < 0) return -EIO;
    f = sys_file_open(0, pb, SYS_OPEN_READ | SYS_OPEN_WRITE | SYS_OPEN_CREATE |
                             SYS_OPEN_TRUNCATE, 0600);
    (void)sys_res_close(pb, SYS_KIND_BYTE_BUFFER);
    if (f < 0) return f;
    if (sys_file_retag(f, SYS_KIND_FILE, SYS_KIND_STREAM) != 1) {
        (void)sys_res_close(f, SYS_KIND_FILE);
        return -EIO;
    }
    return f;
}

static int run_case(const char *label, long dir_token, int in_kind,
                    int out_kind, long src_token, int marker_fd,
                    const char *report_path) {
    char script[2048];
    const char *args[3];
    long plan, child, b;
    char *got;
    int ok = 0;

    snprintf(script, sizeof(script),
             "if [ -e /proc/self/fd/0 ]; then a=open; else a=closed; fi; "
             "if [ -e /proc/self/fd/1 ]; then c=open; else c=closed; fi; "
             "if [ -e /proc/self/fd/2 ]; then d=open; else d=closed; fi; "
             "if [ -e /proc/self/fd/%d ]; then m=leaked; else m=clean; fi; "
             "printf 'stdin=%%s stdout=%%s stderr=%%s marker=%%s\\n' "
             "\"$a\" \"$c\" \"$d\" \"$m\" > %s",
             marker_fd, report_path);
    args[0] = "/bin/sh";
    args[1] = "-c";
    args[2] = script;
    (void)unlink(report_path);
    plan = plan_new("/bin/sh", 3, args);
    if (plan < 0) { report("%s=plan_err\n", label); return -1; }
    if (sys_proc_plan_set(plan, SYS_OP_PLAN_INPUT, in_kind,
                          in_kind == SYS_REDIRECT_STREAM ? src_token : 0) < 0 ||
        sys_proc_plan_set(plan, SYS_OP_PLAN_OUTPUT, out_kind, 0) < 0 ||
        sys_proc_plan_set(plan, SYS_OP_PLAN_ERROR, SYS_REDIRECT_INHERIT, 0) < 0) {
        report("%s=set_err\n", label);
        (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
        return -1;
    }
    if (dir_token != 0) {
        if (sys_proc_plan_set(plan, SYS_OP_PLAN_DIRECTORY, dir_token, 0) < 0) {
            report("%s=dir_err\n", label);
            (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
            return -1;
        }
    }
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    if (child < 0) {
        report("%s=spawn:%ld\n", label, child);
        return -1;
    }
    if (wait_child(child) != 0) report("%s=wait_err\n", label);
    (void)sys_res_close(child, SYS_KIND_CHILD);
    got = read_file(report_path);
    if (got == NULL) {
        report("%s=no_report\n", label);
    } else {
        report("%s=%s", label, got);
        if (strstr(got, "marker=clean") != NULL) ok = 1;
        free(got);
    }
    (void)unlink(report_path);
    (void)b;
    return ok;
}

int main(void) {
    char report_path[128];
    char input_path[128];
    char cwd[4096];
    long dir_token = 0;
    long src;
    long marker;
    int marker_fd;
    long pb_token;

    report_fd = dup(1);
    if (report_fd < 3) {
        /* Keep a reporting descriptor even if std fds were already sparse. */
        int moved = fcntl(report_fd, F_DUPFD, 3);
        if (moved >= 0) { (void)close(report_fd); report_fd = moved; }
    }
    if (sys_adapter_init(256, 1 << 20) < 0) return 2;
    sys_signal_init(1);
    if (getcwd(cwd, sizeof(cwd)) == NULL) return 2;
    snprintf(report_path, sizeof(report_path), "%s/p3-fd-report-%ld", "/tmp",
             (long)getpid());
    snprintf(input_path, sizeof(input_path), "%s/p3-fd-input-%ld", "/tmp",
             (long)getpid());

    /* A non-CLOEXEC marker above the standard descriptors. */
    marker = open("/dev/null", O_RDONLY);
    if (marker >= 0 && marker <= 2) {
        int moved = fcntl(marker, F_DUPFD, 3);
        (void)close(marker);
        marker = moved;
    }
    if (marker < 0) { report("marker_open=err\n"); return 2; }
    marker_fd = (int)marker;

    src = open_blocking_source(input_path);
    if (src < 0) { report("source_open=err\n"); return 2; }
    pb_token = mktext("/tmp");
    if (pb_token >= 0) dir_token = sys_dir_open(0, pb_token, 1);
    if (pb_token >= 0) (void)sys_res_close(pb_token, SYS_KIND_BYTE_BUFFER);

    /* Close the standard descriptors only after the marker/report/file setup. */
    (void)close(0);
    (void)close(1);
    (void)close(2);

    /* B1: NULL and STREAM input must survive closed fd 0; PIPE output closed fd 1. */
    run_case("posix_null_pipe", 0, SYS_REDIRECT_NULL, SYS_REDIRECT_PIPE,
             src, marker_fd, report_path);
    run_case("fork_null_pipe", dir_token, SYS_REDIRECT_NULL, SYS_REDIRECT_PIPE,
             src, marker_fd, report_path);
    run_case("posix_stream_pipe", 0, SYS_REDIRECT_STREAM, SYS_REDIRECT_PIPE,
             src, marker_fd, report_path);
    run_case("fork_stream_pipe", dir_token, SYS_REDIRECT_STREAM,
             SYS_REDIRECT_PIPE, src, marker_fd, report_path);
    run_case("posix_inherit_pipe", 0, SYS_REDIRECT_INHERIT, SYS_REDIRECT_PIPE,
             src, marker_fd, report_path);
    run_case("fork_inherit_pipe", dir_token, SYS_REDIRECT_INHERIT,
             SYS_REDIRECT_PIPE, src, marker_fd, report_path);
    run_case("posix_pipe_inherit", 0, SYS_REDIRECT_PIPE, SYS_REDIRECT_INHERIT,
             src, marker_fd, report_path);
    run_case("fork_pipe_inherit", dir_token, SYS_REDIRECT_PIPE,
             SYS_REDIRECT_INHERIT, src, marker_fd, report_path);

    (void)sys_res_close(src, SYS_KIND_STREAM);
    if (dir_token > 0) (void)sys_res_close(dir_token, SYS_KIND_DIRECTORY);
    (void)close(marker_fd);
    (void)unlink(input_path);
    report("fd_shutdown=%ld\n", sys_adapter_shutdown());
    (void)close(report_fd);
    return 0;
}
