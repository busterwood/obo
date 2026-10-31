/* Linux P0 backend: one small native eventfd and C-owned byte buffers. */
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/eventfd.h>
#include <unistd.h>

typedef struct {
    int fd;
} sys_fd;

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static int release_fd(void *p) {
    sys_fd *f = (sys_fd *)p;
    int e = 0;
    if (f == NULL) return EINVAL;
    if (f->fd >= 0) {
        /* Linux close consumes the descriptor even when it reports EINTR.
         * Never retry; report the captured error once. */
        if (close(f->fd) != 0) e = errno;
        f->fd = -1;
    }
    free(f);
    return e;
}

long sys_res_new(long kind, long a, long b) {
    (void)b;
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        return sys_reg_new_buffer(a);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        int fd;
        sys_fd *f;
        long token;
        fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (fd < 0) return err(errno);
        f = (sys_fd *)malloc(sizeof(*f));
        if (f == NULL) {
            (void)close(fd);
            return err(ENOMEM);
        }
        f->fd = fd;
        token = sys_reg_new_object((int)kind, f, release_fd, 0);
        if (token < 0) {
            (void)close(fd);
            free(f);
        }
        return token;
    }
    return err(EINVAL);
}

long sys_res_op(long token, long kind, long op, long a, long b, long c) {
    sys_reg_bump_call(SYS_CALL_OP);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        void *ptr = sys_reg_peek(token, SYS_KIND_BYTE_BUFFER);
        if (ptr == NULL) return err(EINVAL);
        return sys_reg_buffer_op(ptr, op, a, b, c);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        sys_fd *f = (sys_fd *)sys_reg_peek(token, (int)kind);
        if (f == NULL) return err(EINVAL);
        if (op == SYS_OP_TEST_WRITE) {
            uint64_t v;
            ssize_t n;
            if (a <= 0) return err(EINVAL);
            v = (uint64_t)a;
            n = write(f->fd, &v, sizeof(v));
            if (n < 0) return err(errno);
            if (n != (ssize_t)sizeof(v)) return err(EIO);
            return 1;
        }
        if (op == SYS_OP_TEST_READ) {
            uint64_t v = 0;
            ssize_t n = read(f->fd, &v, sizeof(v));
            if (n < 0) return err(errno);
            if (n != (ssize_t)sizeof(v)) return err(EIO);
            if (v > (uint64_t)0x7fffffffffffffffULL) return err(EOVERFLOW);
            return (long)v;
        }
        /* Progress is a fake-backend script hook, not a real eventfd op. */
        return err(EOPNOTSUPP);
    }
    return err(EINVAL);
}

long sys_res_close(long token, long kind) {
    void *ptr = NULL;
    int (*release)(void *) = NULL;
    int e;
    sys_reg_bump_call(SYS_CALL_CLOSE);
    if (!sys_reg_retire(token, kind, &ptr, &release)) return 0;
    e = release != NULL ? release(ptr) : 0;
    if (e != 0) {
        sys_reg_add_cleanup_failure();
        return err(e);
    }
    return 1;
}
