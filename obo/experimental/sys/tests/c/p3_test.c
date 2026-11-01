/* P3 adapter harness: spawn plans, redirects, pipes, wait/reap, exec failure,
 * process groups, signalfd, cwd/chdir and terminal basics. Built once against
 * the Linux adapter and once against the scripted fake, normally and under
 * ASan/UBSan/LSan. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

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
    unsigned char *blob;
    size_t at = 0;
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

static long plan_for(const char *exec, int argc, const char *const *argv,
                     long flags) {
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
    if ((flags & 1) && sys_proc_plan_set(plan, SYS_OP_PLAN_OUTPUT,
                                         SYS_REDIRECT_PIPE, 0) < 0) return -EIO;
    if ((flags & 2) && sys_proc_plan_set(plan, SYS_OP_PLAN_ERROR,
                                         SYS_REDIRECT_PIPE, 0) < 0) return -EIO;
    if ((flags & 4) && sys_proc_plan_set(plan, SYS_OP_PLAN_GROUP, 1, 0) < 0) return -EIO;
    return plan;
}

static long wait_block(long child) {
    long raw;
    int guard = 0;
    while (guard < 2000) {
        raw = sys_proc_wait(child, 1);
        if (raw != -EINTR) return raw;
        guard++;
    }
    return -EINTR;
}

static int read_all(long stream_token, char *out, size_t cap) {
    size_t at = 0;
    int guard = 0;
    while (at + 1 < cap && guard < 2000) {
        long b = sys_reg_new_buffer(64);
        long n;
        unsigned char *p = NULL;
        size_t l = 0;
        if (b < 0) return -1;
        n = sys_io_read(stream_token, SYS_KIND_STREAM, b, 0, 64,
                        -1 /* SYS_POSITION_CURRENT */);
        if (n == -EAGAIN) {
            (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
            usleep(1000);
            guard++;
            continue;
        }
        if (n <= 0) {
            (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
            out[at] = '\0';
            return 0;
        }
        if (sys_reg_buffer_data(b, &p, &l) < 0 || (size_t)n > l) {
            (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
            return -1;
        }
        if ((size_t)n > cap - 1 - at) n = (long)(cap - 1 - at);
        memcpy(out + at, p, (size_t)n);
        at += (size_t)n;
        (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
    }
    out[at] = '\0';
    return 0;
}

static void show_status(const char *label, long raw) {
    long flags = raw & 0xff;
    long payload = raw >> 8;
    if (flags & SYS_EXIT_SIGNALLED) printf("%s=signal:%ld\n", label, payload);
    else printf("%s=exit:%ld\n", label, payload);
}

#ifdef P3_FAKE
static int fake_mode(int argc, char **argv) {
    long plan, child, stream, b, raw, last;
    const char *a1[] = {"fake"};
    char out[32];
    (void)argc;
    (void)argv;
    if (sys_adapter_init(256, 1 << 20) < 0) return 2;
    sys_signal_init(1);
    sysfake_reset();

    plan = plan_for("/fake", 1, a1, 1);
    if (plan < 0) return 2;
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    if (child < 0) return 2;
    stream = sys_proc_take(child, 1, SYS_KIND_STREAM);
    if (stream < 0) return 2;
    last = sysfake_proc_last();
    b = mktext("hi");
    printf("fake_write=%ld\n", sysfake_proc_write(last, 1, b, 0, 2));
    (void)sys_res_close(b, SYS_KIND_BYTE_BUFFER);
    printf("fake_exit=%ld\n", sysfake_proc_exit(last, SYS_EXIT_EXITED, 7));
    if (read_all(stream, out, sizeof(out)) < 0) return 2;
    printf("fake_out=%s\n", out);
    (void)sys_res_close(stream, SYS_KIND_STREAM);
    raw = wait_block(child);
    show_status("fake_status", raw);
    (void)sys_res_close(child, SYS_KIND_CHILD);

    plan = plan_for("/fake", 1, a1, 4);
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    printf("fake_group=%ld\n", sys_proc_terminate(child, 0, 1));
    raw = wait_block(child);
    show_status("fake_group_status", raw);
    (void)sys_res_close(child, SYS_KIND_CHILD);

    b = (long)sys_signal_subscribe(2);
    if (b < 0) return 2;
    (void)sysfake_signal_push(2, 3);
    printf("fake_signal=%ld\n", sys_signal_next(b));
    (void)sys_res_close(b, SYS_KIND_SIGNAL_SOURCE);

    b = sys_terminal_open(0);
    printf("fake_term_size=%ld\n", sys_terminal_size(b));
    printf("fake_term_raw=%ld\n", sys_terminal_raw(b));
    printf("fake_term_restore=%ld\n", sys_terminal_restore(b));
    (void)sys_res_close(b, SYS_KIND_TERMINAL);

    printf("fake_shutdown=%ld\n", sys_adapter_shutdown());
    return 0;
}
#endif

#ifndef P3_FAKE
static int real_mode(void) {
    const char *a1[] = {"/bin/sh", "-c", "printf hi; exit 7"};
    const char *a2[] = {"/bin/sh", "-c", "exit 127"};
    const char *a3[] = {"/bin/sh", "-c", "sleep 5"};
    const char *pwd[] = {"/bin/pwd"};
    const char *bad[] = {"/no/such/p3-executable"};
    char out[512];
    char saved[4096];
    long plan, child, stream, b, raw, dir;
    sigset_t cur;

    if (sys_adapter_init(256, 1 << 20) < 0) return 2;
    sys_signal_init(1);
    if (getcwd(saved, sizeof(saved)) == NULL) return 2;

    /* Output pipe + genuine exit 7. */
    plan = plan_for("/bin/sh", 3, a1, 1);
    if (plan < 0) return 2;
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    if (child < 0) return 2;
    stream = sys_proc_take(child, 1, SYS_KIND_STREAM);
    if (stream < 0) return 2;
    if (read_all(stream, out, sizeof(out)) < 0) return 2;
    printf("real_out=%s\n", out);
    (void)sys_res_close(stream, SYS_KIND_STREAM);
    raw = wait_block(child);
    show_status("real_status", raw);
    (void)sys_res_close(child, SYS_KIND_CHILD);

    /* Genuine exit 127 versus exec failure. */
    plan = plan_for("/bin/sh", 3, a2, 0);
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    raw = wait_block(child);
    show_status("real_127", raw);
    (void)sys_res_close(child, SYS_KIND_CHILD);
    plan = plan_for("/no/such/p3-executable", 1, bad, 0);
    child = sys_proc_spawn(plan);
    printf("real_execfail=%ld\n", child);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    if (child >= 0) (void)sys_res_close(child, SYS_KIND_CHILD);

    /* Anchored child cwd via Command.directory. */
    dir = mktext("/tmp");
    b = sys_dir_open(0, dir, 1);
    (void)sys_res_close(dir, SYS_KIND_BYTE_BUFFER);
    if (b < 0) return 2;
    plan = plan_for("/bin/pwd", 1, pwd, 1);
    if (plan < 0) return 2;
    if (sys_proc_plan_set(plan, SYS_OP_PLAN_DIRECTORY, b, 0) < 0) return 2;
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    stream = sys_proc_take(child, 1, SYS_KIND_STREAM);
    if (read_all(stream, out, sizeof(out)) < 0) return 2;
    printf("real_child_cwd=%s", out);
    (void)sys_res_close(stream, SYS_KIND_STREAM);
    raw = wait_block(child);
    (void)sys_res_close(child, SYS_KIND_CHILD);
    (void)sys_res_close(b, SYS_KIND_DIRECTORY);

    /* Created process group termination. */
    plan = plan_for("/bin/sh", 3, a3, 4);
    child = sys_proc_spawn(plan);
    (void)sys_res_close(plan, SYS_KIND_SPAWN_PLAN);
    printf("real_group_term=%ld\n", sys_proc_terminate(child, 0, 1));
    raw = wait_block(child);
    show_status("real_group_status", raw);
    (void)sys_res_close(child, SYS_KIND_CHILD);

    /* signalfd delivery and mask restoration. */
    b = sys_signal_subscribe(2);
    if (b < 0) return 2;
    printf("real_overlap=%ld\n", sys_signal_subscribe(2));
    if (fork() == 0) {
        usleep(50000);
        kill(getppid(), SIGTERM);
        _exit(0);
    }
    {
        struct pollfd pfd;
        pfd.fd = (int)sys_signal_watch_fd(b);
        pfd.events = POLLIN;
        pfd.revents = 0;
        printf("real_signal_poll=%d\n", poll(&pfd, 1, 3000));
    }
    raw = sys_signal_next(b);
    printf("real_signal=%ld\n", raw);
    (void)sys_res_close(b, SYS_KIND_SIGNAL_SOURCE);
    sigprocmask(SIG_SETMASK, NULL, &cur);
    printf("real_mask_restored=%d\n", sigismember(&cur, SIGTERM) == 0);

    /* Terminal basics on the (non-tty) harness stdin. */
    b = sys_terminal_open(0);
    if (b < 0) return 2;
    printf("real_term_size=%ld\n", sys_terminal_size(b));
    printf("real_term_raw=%ld\n", sys_terminal_raw(b));
    (void)sys_terminal_restore(b);
    dir = sys_terminal_dup(b);
    printf("real_term_dup=%d\n", dir >= 0);
    if (dir >= 0) (void)sys_res_close(dir, SYS_KIND_STREAM);
    (void)sys_res_close(b, SYS_KIND_TERMINAL);

    /* cwd/chdir through an anchored directory. */
    {
        long cb = sys_reg_new_buffer(4096);
        unsigned char *p = NULL;
        size_t l = 0;
        long d = mktext("/tmp");
        long dt = sys_dir_open(0, d, 1);
        (void)sys_res_close(d, SYS_KIND_BYTE_BUFFER);
        printf("real_chdir=%ld\n", sys_proc_chdir(dt));
        printf("real_cwd_len_ok=%d\n",
               sys_proc_cwd(cb, 4096) > 0 &&
               sys_reg_buffer_data(cb, &p, &l) == 0 && p != NULL &&
               memcmp(p, "/tmp", 4) == 0);
        (void)sys_res_close(dt, SYS_KIND_DIRECTORY);
        (void)sys_res_close(cb, SYS_KIND_BYTE_BUFFER);
    }
    if (chdir(saved) != 0) return 2;

    printf("real_shutdown=%ld\n", sys_adapter_shutdown());
    return 0;
}
#endif

int main(int argc, char **argv) {
#ifdef P3_FAKE
    (void)argc;
    (void)argc;
    (void)argv;
    return fake_mode(argc, argv);
#else
    (void)argc;
    (void)argc;
    (void)argv;
    return real_mode();
#endif
}
