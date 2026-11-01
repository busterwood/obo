/* P3 Linux process, signal and terminal backend.
 *
 * Scalar-only boundary: a spawn plan is an internal registry object holding
 * copied executable/argv/environment bytes and duplicated directory/source
 * descriptors. No pointer, pid, fd, termios struct or signal number crosses to
 * obo. Every operation is one kernel attempt unless a named blocking wait is
 * requested; EINTR is returned, never retried.
 *
 * fork/exec runs only trusted C after fork: no allocation, no obo code, no
 * stdio. All descriptors and argv/env storage are allocated before fork. A
 * CLOEXEC error pipe distinguishes exec failure from a genuine child exit 127.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"
#include "sysnet.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

extern char **environ;

typedef struct sys_spawn_plan {
    char *exec;
    unsigned char *argv_blob;
    size_t argv_blob_len;
    char **argv;
    int argc;
    unsigned char *env_blob;
    size_t env_blob_len;
    char **envp;
    int envc;
    int env_mode;          /* 0 inherit, 1 explicit */
    int dir_fd;
    int redir[3];          /* SYS_REDIRECT_* */
    int src_fd[3];
    int new_group;
} sys_spawn_plan;

typedef struct sys_child {
    long token;
    pid_t pid;
    int pidfd;
    int pipe_fd[3];
    int group_created;
    int reaped;
    int detached;
    long status;           /* encoded terminal status */
    struct sys_child *next;
} sys_child;

static sys_child *child_head;

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static int release_fdobj_p3(void *p) {
    sys_fdobj *f = (sys_fdobj *)p;
    int e = 0;
    if (f == NULL) return EINVAL;
    if (f->fd >= 0) {
        if (close(f->fd) != 0) e = errno;
        f->fd = -1;
    }
    free(f);
    return e;
}

static int lib_signal(int sig) {
    switch (sig) {
        case SIGINT: return 1;
        case SIGTERM: return 2;
        case SIGHUP: return 3;
        case SIGCHLD: return 4;
        case SIGWINCH: return 5;
        default: return 6; /* OTHER */
    }
}

static int linux_signal(long id) {
    switch (id) {
        case 1: return SIGINT;
        case 2: return SIGTERM;
        case 3: return SIGHUP;
        case 4: return SIGCHLD;
        case 5: return SIGWINCH;
        default: return 0;
    }
}

static int set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) return errno;
    if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) return errno;
    return 0;
}

static long encode_status(int status) {
    if (WIFEXITED(status)) {
        return ((long)(WEXITSTATUS(status) & 0xffffff) << 8) | SYS_EXIT_EXITED;
    }
    if (WIFSIGNALED(status)) {
        long flags = SYS_EXIT_SIGNALLED;
        if (WCOREDUMP(status)) flags |= SYS_EXIT_CORE;
        return ((long)(lib_signal(WTERMSIG(status)) & 0xff) << 8) | flags;
    }
    return 0;
}

static void child_unlink(sys_child *victim) {
    sys_child **pp = &child_head;
    while (*pp != NULL) {
        if (*pp == victim) {
            *pp = victim->next;
            return;
        }
        pp = &(*pp)->next;
    }
}

static int release_child(void *p) {
    sys_child *c = (sys_child *)p;
    int i;
    if (c == NULL) return EINVAL;
    for (i = 0; i < 3; i++) {
        if (c->pipe_fd[i] >= 0) {
            (void)close(c->pipe_fd[i]);
            c->pipe_fd[i] = -1;
        }
    }
    if (c->pidfd >= 0) {
        (void)close(c->pidfd);
        c->pidfd = -1;
    }
    if (!c->reaped) {
        /* A running child is not killed and not waited for in close/defer. The
         * single-threaded reaper keeps the obligation. */
        c->token = 0;
        c->detached = 1;
        return 0;
    }
    child_unlink(c);
    free(c);
    return 0;
}

static int release_plan(void *p) {
    sys_spawn_plan *plan = (sys_spawn_plan *)p;
    int i;
    if (plan == NULL) return EINVAL;
    free(plan->exec);
    free(plan->argv_blob);
    free(plan->argv);
    free(plan->env_blob);
    free(plan->envp);
    if (plan->dir_fd >= 0) (void)close(plan->dir_fd);
    for (i = 0; i < 3; i++) {
        if (plan->src_fd[i] >= 0) (void)close(plan->src_fd[i]);
    }
    free(plan);
    return 0;
}

long sys_proc_plan_new(void) {
    sys_spawn_plan *plan;
    long token;
    sys_reg_bump_call(SYS_CALL_PROC_PLAN_NEW);
    plan = (sys_spawn_plan *)calloc(1, sizeof(*plan));
    if (plan == NULL) return err(ENOMEM);
    plan->dir_fd = -1;
    plan->src_fd[0] = -1;
    plan->src_fd[1] = -1;
    plan->src_fd[2] = -1;
    plan->redir[0] = SYS_REDIRECT_INHERIT;
    plan->redir[1] = SYS_REDIRECT_INHERIT;
    plan->redir[2] = SYS_REDIRECT_INHERIT;
    token = sys_reg_new_object(SYS_KIND_SPAWN_PLAN, plan, release_plan, 0);
    if (token < 0) {
        release_plan(plan);
        return token;
    }
    return token;
}

static sys_spawn_plan *plan_of(long token) {
    return (sys_spawn_plan *)sys_reg_peek(token, SYS_KIND_SPAWN_PLAN);
}

static int split_nul_blob(unsigned char *blob, size_t len, int require_equals,
                          char ***out, int *out_count) {
    int count = 0;
    size_t i = 0;
    size_t start = 0;
    char **list;
    if (blob == NULL || len == 0 || blob[len - 1] != 0) return EINVAL;
    while (i < len) {
        if (blob[i] == 0) count++;
        i++;
    }
    if (count < 1) return EINVAL;
    list = (char **)calloc((size_t)count + 1, sizeof(char *));
    if (list == NULL) return ENOMEM;
    count = 0;
    for (i = 0; i < len; i++) {
        if (blob[i] != 0) continue;
        if (i == start && require_equals) {
            free(list);
            return EINVAL; /* empty environment name */
        }
        if (require_equals) {
            size_t j;
            int seen = 0;
            for (j = start; j < i; j++) {
                if (blob[j] == '=') { seen = j > start; break; }
            }
            if (!seen) {
                free(list);
                return EINVAL;
            }
        }
        list[count++] = (char *)(blob + start);
        start = i + 1;
    }
    list[count] = NULL;
    *out = list;
    *out_count = count;
    return 0;
}

static int copy_buffer(long buf_token, unsigned char **out, size_t *outlen) {
    unsigned char *data = NULL;
    size_t len = 0;
    long r = sys_reg_buffer_data(buf_token, &data, &len);
    if (r < 0) return (int)(0 - r);
    if (data == NULL) return EINVAL;
    *out = data;
    *outlen = len;
    return 0;
}

long sys_proc_plan_set(long plan_token, long field, long a, long b) {
    sys_spawn_plan *plan = plan_of(plan_token);
    unsigned char *data = NULL;
    size_t len = 0;
    int e;
    sys_reg_bump_call(SYS_CALL_PROC_PLAN_SET);
    if (plan == NULL) return err(EINVAL);
    if (field == SYS_OP_PLAN_EXEC) {
        char *copy;
        e = copy_buffer(a, &data, &len);
        if (e != 0) return err(e);
        if (len < 1 || len > 4095 || memchr(data, 0, len) != NULL) return err(EINVAL);
        copy = (char *)malloc(len + 1);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, data, len);
        copy[len] = '\0';
        free(plan->exec);
        plan->exec = copy;
        return 1;
    }
    if (field == SYS_OP_PLAN_ARGV) {
        unsigned char *copy;
        char **list = NULL;
        int count = 0;
        e = copy_buffer(a, &data, &len);
        if (e != 0) return err(e);
        if (len < 1 || len > (16L * 1024L * 1024L)) return err(E2BIG);
        copy = (unsigned char *)malloc(len);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, data, len);
        e = split_nul_blob(copy, len, 0, &list, &count);
        if (e != 0) {
            free(copy);
            return err(e);
        }
        if (count < 1 || list[0][0] == '\0') {
            free(copy);
            free(list);
            return err(EINVAL);
        }
        free(plan->argv_blob);
        free(plan->argv);
        plan->argv_blob = copy;
        plan->argv_blob_len = len;
        plan->argv = list;
        plan->argc = count;
        return 1;
    }
    if (field == SYS_OP_PLAN_ENV) {
        unsigned char *copy;
        char **list = NULL;
        int count = 0;
        e = copy_buffer(a, &data, &len);
        if (e != 0) return err(e);
        if (len == 0) {
            /* Explicit empty environment: no entries, not one empty name. */
            free(plan->env_blob);
            free(plan->envp);
            plan->env_blob = NULL;
            plan->env_blob_len = 0;
            plan->envp = NULL;
            plan->envc = 0;
            return 1;
        }
        copy = (unsigned char *)malloc(len);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, data, len);
        e = split_nul_blob(copy, len, 1, &list, &count);
        if (e != 0) {
            free(copy);
            return err(e);
        }
        free(plan->env_blob);
        free(plan->envp);
        plan->env_blob = copy;
        plan->env_blob_len = len;
        plan->envp = list;
        plan->envc = count;
        return 1;
    }
    if (field == SYS_OP_PLAN_ENV_MODE) {
        if (a != 0 && a != 1) return err(EINVAL);
        plan->env_mode = (int)a;
        return 1;
    }
    if (field == SYS_OP_PLAN_DIRECTORY) {
        int fd = -1;
        if (a != 0) {
            sys_fdobj *d = (sys_fdobj *)sys_reg_peek(a, SYS_KIND_DIRECTORY);
            if (d == NULL || d->fd < 0) return err(EBADF);
            fd = fcntl(d->fd, F_DUPFD_CLOEXEC, 3);
            if (fd < 0) return err(errno);
        }
        if (plan->dir_fd >= 0) (void)close(plan->dir_fd);
        plan->dir_fd = fd;
        return 1;
    }
    if (field == SYS_OP_PLAN_INPUT || field == SYS_OP_PLAN_OUTPUT ||
        field == SYS_OP_PLAN_ERROR) {
        int which = (int)(field - SYS_OP_PLAN_INPUT);
        int src = -1;
        if (a < SYS_REDIRECT_INHERIT || a > SYS_REDIRECT_MERGE_ERROR) return err(EINVAL);
        if (a == SYS_REDIRECT_MERGE_ERROR && which != 2) return err(EINVAL);
        if (a == SYS_REDIRECT_STREAM) {
            sys_fdobj *s = (sys_fdobj *)sys_reg_peek(b, SYS_KIND_STREAM);
            if (s == NULL || s->fd < 0) return err(EBADF);
            src = fcntl(s->fd, F_DUPFD_CLOEXEC, 3);
            if (src < 0) return err(errno);
        } else if (b != 0) {
            return err(EINVAL);
        }
        if (plan->src_fd[which] >= 0) (void)close(plan->src_fd[which]);
        plan->src_fd[which] = src;
        plan->redir[which] = (int)a;
        return 1;
    }
    if (field == SYS_OP_PLAN_GROUP) {
        if (a != 0 && a != 1) return err(EINVAL);
        plan->new_group = (int)a;
        return 1;
    }
    return err(EINVAL);
}

/* Prepare one child-side descriptor. Returns 0, or -errno. `parent_fd` receives
 * the parent pipe end or -1. Stream sources are duplicated again so the plan
 * remains the owner and the command's FromStream endpoint is never consumed. */
static long prepare_redirect(sys_spawn_plan *plan, int which, int *child_fd,
                             int *parent_fd) {
    int fds[2];
    int fd;
    *child_fd = -1;
    *parent_fd = -1;
    switch (plan->redir[which]) {
        case SYS_REDIRECT_INHERIT:
            return 0;
        case SYS_REDIRECT_NULL:
            fd = open("/dev/null", O_RDWR | O_CLOEXEC);
            if (fd < 0) return err(errno);
            *child_fd = fd;
            return 0;
        case SYS_REDIRECT_PIPE:
            sys_reg_bump_call(SYS_CALL_PROC_PIPE);
            if (pipe2(fds, O_CLOEXEC) != 0) return err(errno);
            if (which == SYS_TAKE_INPUT) {
                *child_fd = fds[0];
                *parent_fd = fds[1];
                fd = set_nonblocking(*parent_fd);
            } else {
                *child_fd = fds[1];
                *parent_fd = fds[0];
                fd = set_nonblocking(*parent_fd);
            }
            if (fd != 0) {
                (void)close(fds[0]);
                (void)close(fds[1]);
                *child_fd = -1;
                *parent_fd = -1;
                return err(fd);
            }
            return 0;
        case SYS_REDIRECT_STREAM:
            sys_reg_bump_call(SYS_CALL_PROC_DUP);
            if (plan->src_fd[which] < 0) return err(EBADF);
            fd = fcntl(plan->src_fd[which], F_DUPFD_CLOEXEC, 3);
            if (fd < 0) return err(errno);
            *child_fd = fd;
            return 0;
        case SYS_REDIRECT_MERGE_ERROR:
            if (which != SYS_TAKE_ERROR) return err(EINVAL);
            return 0;
        default:
            return err(EINVAL);
    }
}

static int reset_child_signals(void) {
    sigset_t empty;
    int sig;
    sigemptyset(&empty);
    if (sigprocmask(SIG_SETMASK, &empty, NULL) != 0) return errno;
    for (sig = 1; sig < NSIG; sig++) {
        if (sig == SIGKILL || sig == SIGSTOP) continue;
        (void)signal(sig, SIG_DFL);
    }
    return 0;
}

static int child_apply_redirect(int target, int kind, int child_fd) {
    if (kind == SYS_REDIRECT_INHERIT) return 0;
    if (kind == SYS_REDIRECT_MERGE_ERROR) {
        if (dup2(1, 2) < 0) return errno;
        return 0;
    }
    if (kind == SYS_REDIRECT_NULL) {
        int fd = open("/dev/null", O_RDWR);
        if (fd < 0) return errno;
        if (dup2(fd, target) < 0) {
            int e = errno;
            (void)close(fd);
            return e;
        }
        (void)close(fd);
        return 0;
    }
    if (child_fd < 0) return EBADF;
    /* All prepared sources are >= 3, so target 0/1/2 never collides with a
     * not-yet-duplicated source. */
    if (child_fd != target && dup2(child_fd, target) < 0) return errno;
    if (child_fd != target) (void)close(child_fd);
    return 0;
}

static long spawn_fork(sys_spawn_plan *plan, int child_fd[3], pid_t *out_pid,
                       int *group_created) {
    int errpipe[2];
    pid_t pid;
    int e;
    int i;
    size_t got = 0;
    int child_err = 0;

    if (pipe2(errpipe, O_CLOEXEC) != 0) return err(errno);
    pid = fork();
    if (pid < 0) {
        e = errno;
        (void)close(errpipe[0]);
        (void)close(errpipe[1]);
        return err(e);
    }
    if (pid == 0) {
        /* Trusted C only. No allocation, stdio or obo code here. */
        (void)close(errpipe[0]);
        if (plan->new_group && setpgid(0, 0) != 0) {
            child_err = errno;
        } else if (plan->dir_fd >= 0 && fchdir(plan->dir_fd) != 0) {
            child_err = errno;
        } else {
            child_err = reset_child_signals();
        }
        if (child_err == 0) {
            child_err = child_apply_redirect(0, plan->redir[0], child_fd[0]);
        }
        if (child_err == 0) {
            child_err = child_apply_redirect(1, plan->redir[1], child_fd[1]);
        }
        if (child_err == 0) {
            child_err = child_apply_redirect(2, plan->redir[2], child_fd[2]);
        }
        for (i = 0; i < 3; i++) {
            if (child_fd[i] >= 0) (void)close(child_fd[i]);
        }
        if (child_err == 0) {
            char *empty_envp[1] = { NULL };
            char **envp = plan->env_mode == 1
                              ? (plan->envp != NULL ? plan->envp : empty_envp)
                              : environ;
            execve(plan->exec, plan->argv, envp);
            child_err = errno;
        }
        {
            int saved = child_err;
            ssize_t n;
            do {
                n = write(errpipe[1], &saved, sizeof(saved));
            } while (n < 0 && errno == EINTR);
        }
        _exit(127);
    }
    (void)close(errpipe[1]);
    if (plan->new_group) {
        (void)setpgid(pid, pid);
        *group_created = 1;
    }
    for (;;) {
        ssize_t n = read(errpipe[0], (unsigned char *)&e + got, sizeof(e) - got);
        if (n < 0) {
            if (errno == EINTR) continue;
            got = 0;
            e = errno;
            break;
        }
        if (n == 0) break;
        got += (size_t)n;
        if (got == sizeof(e)) break;
    }
    (void)close(errpipe[0]);
    if (got == sizeof(e)) {
        int status = 0;
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) { }
        (void)status;
        return err(e);
    }
    *out_pid = pid;
    return 0;
}

static long spawn_posix(sys_spawn_plan *plan, int child_fd[3], pid_t *out_pid,
                        int *group_created) {
    posix_spawn_file_actions_t fa;
    posix_spawnattr_t attr;
    sigset_t empty;
    sigset_t defaults;
    short flags = POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF;
    char *empty_envp[1] = { NULL };
    char **envp = plan->env_mode == 1
                      ? (plan->envp != NULL ? plan->envp : empty_envp)
                      : environ;
    int rc;
    int i;

    rc = posix_spawn_file_actions_init(&fa);
    if (rc != 0) return err(rc);
    rc = posix_spawnattr_init(&attr);
    if (rc != 0) {
        posix_spawn_file_actions_destroy(&fa);
        return err(rc);
    }
    for (i = 0; i < 3; i++) {
        int kind = plan->redir[i];
        if (kind == SYS_REDIRECT_INHERIT) continue;
        if (kind == SYS_REDIRECT_MERGE_ERROR) {
            rc = posix_spawn_file_actions_adddup2(&fa, 1, 2);
        } else if (kind == SYS_REDIRECT_NULL) {
            rc = posix_spawn_file_actions_addopen(&fa, i, "/dev/null",
                                                  O_RDWR, 0);
        } else {
            if (child_fd[i] < 0) {
                rc = EINVAL;
            } else {
                rc = posix_spawn_file_actions_adddup2(&fa, child_fd[i], i);
                if (rc == 0) {
                    rc = posix_spawn_file_actions_addclose(&fa, child_fd[i]);
                }
            }
        }
        if (rc != 0) goto done;
    }
    sigemptyset(&empty);
    sigfillset(&defaults);
    rc = posix_spawnattr_setsigmask(&attr, &empty);
    if (rc == 0) rc = posix_spawnattr_setsigdefault(&attr, &defaults);
    if (plan->new_group) {
        if (rc == 0) rc = posix_spawnattr_setpgroup(&attr, 0);
        if (rc == 0) flags |= POSIX_SPAWN_SETPGROUP;
    }
    if (rc == 0) rc = posix_spawnattr_setflags(&attr, flags);
    if (rc == 0) {
        rc = posix_spawn(out_pid, plan->exec, &fa, &attr, plan->argv, envp);
    }
    if (rc == 0 && plan->new_group) {
        (void)setpgid(*out_pid, *out_pid);
        *group_created = 1;
    }
done:
    posix_spawn_file_actions_destroy(&fa);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) return err(rc);
    return 0;
}

long sys_proc_spawn(long plan_token) {
    sys_spawn_plan *plan = plan_of(plan_token);
    sys_child *child;
    int child_fd[3];
    int parent_fd[3];
    int i;
    long e;
    pid_t pid = -1;
    int group_created = 0;
    long token;
    sys_reg_bump_call(SYS_CALL_PROC_SPAWN);
    if (plan == NULL) return err(EINVAL);
    if (plan->exec == NULL || plan->argc < 1 || plan->argv == NULL) return err(EINVAL);
    if (plan->env_mode == 1 && plan->envp == NULL && plan->envc != 0) return err(EINVAL);

    child = (sys_child *)calloc(1, sizeof(*child));
    if (child == NULL) return err(ENOMEM);
    child->pidfd = -1;
    child->pipe_fd[0] = -1;
    child->pipe_fd[1] = -1;
    child->pipe_fd[2] = -1;
    for (i = 0; i < 3; i++) {
        child_fd[i] = -1;
        parent_fd[i] = -1;
        e = prepare_redirect(plan, i, &child_fd[i], &parent_fd[i]);
        if (e != 0) goto fail;
    }
    if (plan->dir_fd >= 0) {
        e = spawn_fork(plan, child_fd, &pid, &group_created);
    } else {
        e = spawn_posix(plan, child_fd, &pid, &group_created);
    }
    if (e != 0) goto fail;
    for (i = 0; i < 3; i++) {
        if (child_fd[i] >= 0) {
            (void)close(child_fd[i]);
            child_fd[i] = -1;
        }
    }
#ifdef SYS_pidfd_open
    {
        long pfd = syscall(SYS_pidfd_open, pid, 0);
        if (pfd >= 0) child->pidfd = (int)pfd;
    }
#endif
    child->pid = pid;
    child->group_created = group_created;
    child->token = 0;
    child->next = child_head;
    child_head = child;
    token = sys_reg_new_object(SYS_KIND_CHILD, child, release_child, 0);
    if (token < 0) {
        /* Registry exhaustion after a successful spawn: retire our own child
         * rather than leaking an unsupervised process. */
        int status = 0;
        (void)kill(pid, SIGKILL);
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) { }
        child_unlink(child);
        for (i = 0; i < 3; i++) {
            if (parent_fd[i] >= 0) {
                (void)close(parent_fd[i]);
                parent_fd[i] = -1;
            }
        }
        child->reaped = 1;
        if (child->pidfd >= 0) {
            (void)close(child->pidfd);
            child->pidfd = -1;
        }
        free(child);
        return token;
    }
    child->token = token;
    for (i = 0; i < 3; i++) child->pipe_fd[i] = parent_fd[i];
    return token;

fail:
    for (i = 0; i < 3; i++) {
        if (child_fd[i] >= 0) (void)close(child_fd[i]);
        if (parent_fd[i] >= 0) (void)close(parent_fd[i]);
    }
    free(child);
    return e;
}

long sys_proc_take(long child_token, long which, long to_kind) {
    sys_child *c = (sys_child *)sys_reg_peek(child_token, SYS_KIND_CHILD);
    sys_fdobj *f;
    int fd;
    long token;
    sys_reg_bump_call(SYS_CALL_PROC_TAKE);
    if (c == NULL) return err(EINVAL);
    if (which < 0 || which > 2) return err(EINVAL);
    if (to_kind != SYS_KIND_STREAM) return err(EINVAL);
    if (c->detached) return err(ECHILD);
    fd = c->pipe_fd[which];
    if (fd < 0) return err(EINVAL);
    f = (sys_fdobj *)calloc(1, sizeof(*f));
    if (f == NULL) return err(ENOMEM);
    f->fd = fd;
    f->append = 0;
    f->mode = 0;
    f->sock = 0;
    f->state = NULL;
    token = sys_reg_new_object((int)to_kind, f, release_fdobj_p3, 0);
    if (token < 0) {
        free(f);
        return token;
    }
    c->pipe_fd[which] = -1;
    return token;
}

long sys_proc_wait(long child_token, long block) {
    sys_child *c = (sys_child *)sys_reg_peek(child_token, SYS_KIND_CHILD);
    int status = 0;
    pid_t r;
    sys_reg_bump_call(SYS_CALL_PROC_WAIT);
    if (c == NULL) return err(EINVAL);
    if (block != 0 && block != 1) return err(EINVAL);
    if (c->detached) return err(ECHILD);
    if (c->reaped) return c->status;
    r = waitpid(c->pid, &status, block ? 0 : WNOHANG);
    if (r < 0) return err(errno);
    if (r == 0) return 0;
    c->reaped = 1;
    c->status = encode_status(status);
    return c->status;
}

long sys_proc_terminate(long child_token, long force, long group) {
    sys_child *c = (sys_child *)sys_reg_peek(child_token, SYS_KIND_CHILD);
    int sig;
    int r;
    sys_reg_bump_call(SYS_CALL_PROC_KILL);
    if (c == NULL) return err(EINVAL);
    if ((force != 0 && force != 1) || (group != 0 && group != 1)) return err(EINVAL);
    if (c->detached || c->reaped) return err(ECHILD);
    sig = force ? SIGKILL : SIGTERM;
    if (group) {
        /* Only a process group created by this module may be signalled. Never
         * pass an unchecked negative pid. A pidfd names one process, not its
         * group, so group signalling always uses the owned group id. */
        if (!c->group_created || c->pid <= 0) return err(EINVAL);
        r = kill(-c->pid, sig);
    } else {
#ifdef SYS_pidfd_send_signal
        if (c->pidfd >= 0) {
            r = (int)syscall(SYS_pidfd_send_signal, c->pidfd, sig, NULL, 0);
        } else {
            r = kill(c->pid, sig);
        }
#else
        r = kill(c->pid, sig);
#endif
    }
    if (r == 0) return 1;
    if (errno == ESRCH) return 0;
    return err(errno);
}

long sys_proc_detach(long child_token) {
    sys_child *c = (sys_child *)sys_reg_peek(child_token, SYS_KIND_CHILD);
    sys_reg_bump_call(SYS_CALL_PROC_DETACH);
    if (c == NULL) return err(EINVAL);
    if (c->detached || c->reaped) return 0;
    c->detached = 1;
    return 1;
}

long sys_proc_reap_children(void) {
    sys_child **pp = &child_head;
    long count = 0;
    sys_reg_bump_call(SYS_CALL_PROC_REAP);
    while (*pp != NULL) {
        sys_child *c = *pp;
        if (!c->reaped) {
            int status = 0;
            pid_t r = waitpid(c->pid, &status, WNOHANG);
            if (r == c->pid) {
                c->reaped = 1;
                c->status = encode_status(status);
                count++;
            }
        }
        if (c->reaped && c->token == 0) {
            *pp = c->next;
            if (c->pidfd >= 0) (void)close(c->pidfd);
            free(c);
            continue;
        }
        pp = &c->next;
    }
    return count;
}

long sys_proc_child_count(void) {
    sys_child *c = child_head;
    long n = 0;
    while (c != NULL) {
        if (c->token > 0) n++;
        c = c->next;
    }
    return n;
}

long sys_proc_watch_fd(long child_token) {
    sys_child *c = (sys_child *)sys_reg_peek(child_token, SYS_KIND_CHILD);
    if (c == NULL) return err(EINVAL);
    if (c->pidfd < 0) return err(EOPNOTSUPP);
    return c->pidfd;
}

long sys_proc_cwd(long buf_token, long cap) {
    unsigned char *data = NULL;
    size_t len = 0;
    long r;
    char *out;
    sys_reg_bump_call(SYS_CALL_PROC_CWD);
    if (cap < 1 || cap > 1048576) return err(EINVAL);
    r = sys_reg_buffer_data(buf_token, &data, &len);
    if (r < 0) return r;
    if (data == NULL || (size_t)cap > len) return err(ERANGE);
    out = getcwd((char *)data, (size_t)cap);
    if (out == NULL) return err(errno);
    return (long)strlen((char *)data);
}

long sys_proc_chdir(long dir_token) {
    sys_fdobj *d;
    sys_reg_bump_call(SYS_CALL_PROC_CHDIR);
    if (dir_token == 0) return err(EINVAL);
    d = (sys_fdobj *)sys_reg_peek(dir_token, SYS_KIND_DIRECTORY);
    if (d == NULL || d->fd < 0) return err(EBADF);
    if (fchdir(d->fd) != 0) return err(errno);
    return 1;
}

long sys_proc_arg_max(void) {
    long v = sysconf(_SC_ARG_MAX);
    if (v < 4096) v = 4096;
    if (v > 2147483647L) v = 2147483647L;
    return v;
}

long sys_proc_pidfd_supported(void) {
#ifdef SYS_pidfd_open
    return 1;
#else
    return 0;
#endif
}

/* ---- signals ----------------------------------------------------------- */

typedef struct {
    int fd;
    long bits;
    long pending[6];
} sys_signal_source;

static int signal_mask_valid;
static int signal_active;
static sigset_t signal_saved_mask;

long sys_signal_init(long ignore_pipe) {
    struct sigaction sa;
    sys_reg_bump_call(SYS_CALL_SIGNAL_INIT);
    if (ignore_pipe != 0 && ignore_pipe != 1) return err(EINVAL);
    if (ignore_pipe) {
        if (sigaction(SIGPIPE, NULL, &sa) == 0 && sa.sa_handler != SIG_IGN) {
            struct sigaction ign;
            memset(&ign, 0, sizeof(ign));
            ign.sa_handler = SIG_IGN;
            sigemptyset(&ign.sa_mask);
            if (sigaction(SIGPIPE, &ign, NULL) != 0) return err(errno);
        }
    }
    return 1;
}

static int release_signal(void *p) {
    sys_signal_source *s = (sys_signal_source *)p;
    int e = 0;
    if (s == NULL) return EINVAL;
    sys_reg_bump_call(SYS_CALL_SIGNAL_CLOSE);
    if (s->fd >= 0) {
        if (close(s->fd) != 0) e = errno;
        s->fd = -1;
    }
    if (signal_active && signal_mask_valid) {
        (void)sigprocmask(SIG_SETMASK, &signal_saved_mask, NULL);
        signal_mask_valid = 0;
        signal_active = 0;
    }
    free(s);
    return e;
}

long sys_signal_subscribe(long bits) {
    sys_signal_source *s;
    sigset_t set;
    long token;
    int sig;
    sys_reg_bump_call(SYS_CALL_SIGNAL_SUBSCRIBE);
    if (bits <= 0 || (bits & ~0x1fL) != 0) return err(EINVAL);
    if (signal_active) return err(EBUSY);
    sigemptyset(&set);
    for (sig = 1; sig <= 5; sig++) {
        if ((bits & (1L << (sig - 1))) != 0) {
            int native_sig = linux_signal(sig);
            if (native_sig == 0) return err(EINVAL);
            sigaddset(&set, native_sig);
        }
    }
    if (sigprocmask(SIG_BLOCK, &set, &signal_saved_mask) != 0) return err(errno);
    signal_mask_valid = 1;
    sigemptyset(&set);
    for (sig = 1; sig <= 5; sig++) {
        if ((bits & (1L << (sig - 1))) != 0) sigaddset(&set, linux_signal(sig));
    }
    s = (sys_signal_source *)calloc(1, sizeof(*s));
    if (s == NULL) {
        (void)sigprocmask(SIG_SETMASK, &signal_saved_mask, NULL);
        signal_mask_valid = 0;
        return err(ENOMEM);
    }
    s->fd = signalfd(-1, &set, SFD_NONBLOCK | SFD_CLOEXEC);
    if (s->fd < 0) {
        int e = errno;
        (void)sigprocmask(SIG_SETMASK, &signal_saved_mask, NULL);
        signal_mask_valid = 0;
        free(s);
        return err(e);
    }
    s->bits = bits;
    signal_active = 1;
    token = sys_reg_new_object(SYS_KIND_SIGNAL_SOURCE, s, release_signal, 0);
    if (token < 0) {
        release_signal(s);
        return token;
    }
    return token;
}

long sys_signal_next(long source_token) {
    sys_signal_source *s = (sys_signal_source *)sys_reg_peek(source_token,
                                                             SYS_KIND_SIGNAL_SOURCE);
    int i;
    sys_reg_bump_call(SYS_CALL_SIGNAL_NEXT);
    if (s == NULL) return err(EINVAL);
    for (i = 1; i <= 5; i++) {
        if (s->pending[i] > 0) {
            long count = s->pending[i];
            s->pending[i] = 0;
            return (count << 8) | i;
        }
    }
    for (;;) {
        struct signalfd_siginfo info;
        ssize_t n = read(s->fd, &info, sizeof(info));
        int id;
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            if (errno == EINTR) return err(EINTR);
            return err(errno);
        }
        if (n != (ssize_t)sizeof(info)) return err(EIO);
        id = lib_signal((int)info.ssi_signo);
        if (id < 1 || id > 5) continue;
        if (s->pending[id] < 255) s->pending[id]++;
    }
    for (i = 1; i <= 5; i++) {
        if (s->pending[i] > 0) {
            long count = s->pending[i];
            s->pending[i] = 0;
            return (count << 8) | i;
        }
    }
    return 0;
}

long sys_signal_watch_fd(long source_token) {
    sys_signal_source *s = (sys_signal_source *)sys_reg_peek(source_token,
                                                             SYS_KIND_SIGNAL_SOURCE);
    return s == NULL ? err(EBADF) : s->fd;
}

/* ---- terminal --------------------------------------------------------- */

typedef struct {
    int fd;
    int have_saved;
    int raw;
    struct termios saved;
} sys_terminal;

static int release_terminal(void *p) {
    sys_terminal *t = (sys_terminal *)p;
    int e = 0;
    if (t == NULL) return EINVAL;
    if (t->raw && t->have_saved) {
        if (tcsetattr(t->fd, TCSAFLUSH, &t->saved) != 0) e = errno;
        t->raw = 0;
    }
    if (t->fd >= 0) {
        if (close(t->fd) != 0 && e == 0) e = errno;
        t->fd = -1;
    }
    free(t);
    return e;
}

long sys_terminal_open(long which) {
    sys_terminal *t;
    long token;
    sys_reg_bump_call(SYS_CALL_TERMINAL_OPEN);
    if (which < 0 || which > 2) return err(EINVAL);
    t = (sys_terminal *)calloc(1, sizeof(*t));
    if (t == NULL) return err(ENOMEM);
    t->fd = fcntl((int)which, F_DUPFD_CLOEXEC, 0);
    if (t->fd < 0) {
        free(t);
        return err(errno);
    }
    if (tcgetattr(t->fd, &t->saved) == 0) t->have_saved = 1;
    token = sys_reg_new_object(SYS_KIND_TERMINAL, t, release_terminal, 0);
    if (token < 0) {
        release_terminal(t);
        return token;
    }
    return token;
}

long sys_terminal_size(long terminal_token) {
    sys_terminal *t = (sys_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    struct winsize ws;
    sys_reg_bump_call(SYS_CALL_TERMINAL_SIZE);
    if (t == NULL || t->fd < 0) return err(EBADF);
    memset(&ws, 0, sizeof(ws));
    if (ioctl(t->fd, TIOCGWINSZ, &ws) != 0) {
        if (errno == ENOTTY) return err(EOPNOTSUPP);
        return err(errno);
    }
    return ((long)ws.ws_row << 32) | (long)ws.ws_col;
}

long sys_terminal_raw(long terminal_token) {
    sys_terminal *t = (sys_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    struct termios raw;
    sys_reg_bump_call(SYS_CALL_TERMINAL_RAW);
    if (t == NULL || t->fd < 0) return err(EBADF);
    if (t->raw) return 0;
    if (tcgetattr(t->fd, &raw) != 0) {
        if (errno == ENOTTY) return err(EOPNOTSUPP);
        return err(errno);
    }
    t->saved = raw;
    t->have_saved = 1;
    cfmakeraw(&raw);
    if (tcsetattr(t->fd, TCSAFLUSH, &raw) != 0) return err(errno);
    t->raw = 1;
    return 1;
}

long sys_terminal_restore(long terminal_token) {
    sys_terminal *t = (sys_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    sys_reg_bump_call(SYS_CALL_TERMINAL_RESTORE);
    if (t == NULL || t->fd < 0) return err(EBADF);
    if (!t->raw || !t->have_saved) return 0;
    if (tcsetattr(t->fd, TCSAFLUSH, &t->saved) != 0) return err(errno);
    t->raw = 0;
    return 1;
}

long sys_terminal_dup(long terminal_token) {
    sys_terminal *t = (sys_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    sys_fdobj *f;
    long token;
    int fd;
    sys_reg_bump_call(SYS_CALL_TERMINAL_DUP);
    if (t == NULL || t->fd < 0) return err(EBADF);
    fd = fcntl(t->fd, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    f = (sys_fdobj *)calloc(1, sizeof(*f));
    if (f == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    f->fd = fd;
    f->append = 0;
    f->mode = 0;
    f->sock = 0;
    f->state = NULL;
    token = sys_reg_new_object(SYS_KIND_STREAM, f, release_fdobj_p3, 0);
    if (token < 0) {
        (void)close(fd);
        free(f);
    }
    return token;
}

void sys_proc_shutdown(void) {
    sys_child *c = child_head;
    long n = 0;
    while (c != NULL) {
        sys_child *next = c->next;
        if (!c->reaped) {
            int status = 0;
            pid_t r = waitpid(c->pid, &status, WNOHANG);
            if (r == c->pid) {
                c->reaped = 1;
                c->status = encode_status(status);
            } else {
                /* Explicit context shutdown cannot silently block or kill an
                 * application child. Report the outstanding obligation. */
                sys_reg_add_cleanup_failure();
                n++;
            }
        }
        if (c->pidfd >= 0) (void)close(c->pidfd);
        if (c->pipe_fd[0] >= 0) (void)close(c->pipe_fd[0]);
        if (c->pipe_fd[1] >= 0) (void)close(c->pipe_fd[1]);
        if (c->pipe_fd[2] >= 0) (void)close(c->pipe_fd[2]);
        free(c);
        c = next;
    }
    child_head = NULL;
    (void)n;
}
