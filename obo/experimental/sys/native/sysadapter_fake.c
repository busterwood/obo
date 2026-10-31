/* Scripted P0 fake backend. Selected at link time only; there is no runtime
 * production toggle and no stored obo callback. Tests drive the sysfake_*
 * controls directly. */
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdlib.h>

#define FAKE_QUEUE_MAX 64

typedef struct {
    long call_class;
    long result;
} fake_script;

typedef struct {
    long value;
    int signaled;
} fake_res;

static fake_script queue[FAKE_QUEUE_MAX];
static int qhead;
static int qtail;
static long fake_time;

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static int pop_script(long call_class, long *result) {
    if (qhead == qtail) return 0;
    if (queue[qhead].call_class != call_class) return 0;
    *result = queue[qhead].result;
    qhead = (qhead + 1) % FAKE_QUEUE_MAX;
    return 1;
}

static int release_fake(void *p) {
    free(p);
    return 0;
}

long sysfake_reset(void) {
    qhead = 0;
    qtail = 0;
    fake_time = 0;
    sys_reg_reset_counts();
    return 1;
}

long sysfake_push(long call_class, long result) {
    int next = (qtail + 1) % FAKE_QUEUE_MAX;
    if (next == qhead) return -ENOSPC;
    queue[qtail].call_class = call_class;
    queue[qtail].result = result;
    qtail = next;
    return 1;
}

long sysfake_set_time(long value) {
    fake_time = value;
    return 1;
}

long sysfake_now(void) {
    return fake_time;
}

long sys_res_new(long kind, long a, long b) {
    long scripted = 0;
    int have_script;
    (void)b;
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        return sys_reg_new_buffer(a);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        fake_res *f;
        long token;
        have_script = pop_script(SYS_CALL_NEW, &scripted);
        if (have_script && scripted < 0) return scripted;
        f = (fake_res *)malloc(sizeof(*f));
        if (f == NULL) return err(ENOMEM);
        f->value = have_script ? scripted : 0;
        f->signaled = 0;
        token = sys_reg_new_object((int)kind, f, release_fake, 0);
        if (token < 0) free(f);
        return token;
    }
    return err(EINVAL);
}

long sys_res_op(long token, long kind, long op, long a, long b, long c) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_OP);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        void *ptr = sys_reg_peek(token, SYS_KIND_BYTE_BUFFER);
        if (ptr == NULL) return err(EINVAL);
        return sys_reg_buffer_op(ptr, op, a, b, c);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        fake_res *f = (fake_res *)sys_reg_peek(token, (int)kind);
        if (f == NULL) return err(EINVAL);
        have_script = pop_script(SYS_CALL_OP, &scripted);
        if (have_script && scripted < 0) return scripted;
        if (op == SYS_OP_TEST_WRITE) {
            if (a <= 0) return err(EINVAL);
            f->value = a;
            f->signaled = 1;
            return 1;
        }
        if (op == SYS_OP_TEST_READ) {
            if (have_script) {
                f->signaled = 0;
                return scripted;
            }
            if (!f->signaled) return err(EAGAIN);
            f->signaled = 0;
            return f->value;
        }
        if (op == SYS_OP_TEST_PROGRESS) {
            return have_script ? scripted : 0;
        }
        return err(EOPNOTSUPP);
    }
    return err(EINVAL);
}

long sys_res_close(long token, long kind) {
    long scripted = 0;
    int have_script;
    void *ptr = NULL;
    int (*release)(void *) = NULL;
    int e;
    sys_reg_bump_call(SYS_CALL_CLOSE);
    if (sys_reg_peek(token, (int)kind) == NULL) return 0;
    have_script = pop_script(SYS_CALL_CLOSE, &scripted);
    if (!sys_reg_retire(token, kind, &ptr, &release)) return 0;
    e = release != NULL ? release(ptr) : 0;
    /* A scripted close error still consumes the fake resource, matching Linux
     * close semantics instead of manufacturing a leak. */
    if (have_script && scripted < 0) {
        sys_reg_add_cleanup_failure();
        return scripted;
    }
    if (e != 0) {
        sys_reg_add_cleanup_failure();
        return err(e);
    }
    return 1;
}
