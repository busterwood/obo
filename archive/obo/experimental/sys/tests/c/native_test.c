/* Real-backend C harness: registry identity, byte bounds, quotas, ordering. */
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>

static int failures;

#define CHECK(cond, text) do { \
    if (!(cond)) { fprintf(stderr, "FAIL %s (%s:%d)\n", text, __FILE__, __LINE__); failures++; } \
} while (0)

static void test_identity(void) {
    long t1, t2, t3;
    long n1, n2;
    CHECK(sys_adapter_init(8, 1024) == 0, "init");
    CHECK(sys_adapter_init(8, 1024) == -EBUSY, "reinit is busy");
    n1 = sys_res_nonce();
    n2 = sys_res_nonce();
    CHECK(n1 >= 2 && n2 >= 2, "nonce is positive and never 0/1");
    CHECK(n1 != n2, "nonce changes");
    t1 = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t1 > 0, "create test resource");
    CHECK(sys_res_validate(t1, SYS_KIND_TEST_RESOURCE) == 1, "validate kind");
    CHECK(sys_res_validate(t1, SYS_KIND_TEST_RESOURCE_ALT) == 0, "wrong kind rejected");
    CHECK(sys_res_kind(t1) == SYS_KIND_TEST_RESOURCE, "kind query");
    CHECK(sys_res_op(t1, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_WRITE, 5, 0, 0) == 1, "write");
    CHECK(sys_res_op(t1, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_READ, 0, 0, 0) == 5, "read");
    CHECK(sys_res_op(t1, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_READ, 0, 0, 0) == -EAGAIN,
          "empty read is EAGAIN, not EINTR and not zero");
    CHECK(sys_res_op(t1, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_WRITE, 0, 0, 0) == -EINVAL,
          "eventfd rejects zero");
    CHECK(sys_res_close(t1, SYS_KIND_TEST_RESOURCE) == 1, "close");
    CHECK(sys_res_validate(t1, SYS_KIND_TEST_RESOURCE) == 0, "closed token invalid");
    CHECK(sys_res_close(t1, SYS_KIND_TEST_RESOURCE) == 0, "double close");
    CHECK(sys_res_live_count() == 0, "no live resource");

    /* Slot reuse with a new generation: the old token stays dead. */
    t1 = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    t2 = sys_res_new(SYS_KIND_TEST_RESOURCE_ALT, 0, 0);
    CHECK(t1 > 0 && t2 > 0 && t1 != t2, "two tokens");
    CHECK(sys_res_close(t1, SYS_KIND_TEST_RESOURCE) == 1, "close first");
    t3 = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t3 > 0 && t3 != t1, "reused slot has a new token");
    CHECK(sys_res_validate(t1, SYS_KIND_TEST_RESOURCE) == 0, "stale token dead");
    CHECK(sys_res_validate(t3, SYS_KIND_TEST_RESOURCE) == 1, "new token live");
    CHECK(sys_res_close(t2, SYS_KIND_TEST_RESOURCE_ALT) == 1, "close alt");
    CHECK(sys_res_close(t3, SYS_KIND_TEST_RESOURCE) == 1, "close third");
    CHECK(sys_adapter_shutdown() == 0, "empty shutdown");
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -EINVAL, "no create after shutdown");

    /* Native table exhaustion and reuse. */
    CHECK(sys_adapter_init(1, 1024) == 0, "init one");
    t1 = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t1 > 0, "one resource");
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -EMFILE, "table full");
    CHECK(sys_res_close(t1, SYS_KIND_TEST_RESOURCE) == 1, "free slot");
    t1 = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t1 > 0, "reuse after close");
    CHECK(sys_res_close(t1, SYS_KIND_TEST_RESOURCE) == 1, "close reused");
    CHECK(sys_adapter_shutdown() == 0, "shutdown one");
}

static void test_buffers(void) {
    long t, r;
    CHECK(sys_adapter_init(8, 64) == 0, "init buffers");
    t = sys_res_new(SYS_KIND_BYTE_BUFFER, 10, 0);
    CHECK(t > 0, "buffer create");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_LEN, 0, 0, 0) == 10, "buffer len");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET, 3, 200, 0) == 1, "set byte");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET, 3, 0, 0) == 200, "get byte");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET, 10, 0, 0) == -ERANGE, "get oob");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET, 10, 1, 0) == -ERANGE, "set oob");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET, 0, 256, 0) == -EINVAL, "bad byte");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 0, 0x01020304050607L, 7) == 7,
          "packed set");
    r = sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET_PACKED, 0, 0, 7);
    CHECK(r == 0x01020304050607L, "packed get");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 0, 255, 1) == 1,
          "1-byte maximum");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 0, 256, 1) == -ERANGE,
          "1-byte overflow rejected");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 0, 65535, 2) == 2,
          "2-byte maximum");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 0, 65536, 2) == -ERANGE,
          "2-byte overflow rejected");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 3, 0x00ffffffffffffffL, 7) == 7,
          "last 7-byte range");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 3, 0x0100000000000000L, 7) == -ERANGE,
          "7-byte overflow rejected");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 4, 0, 7) == -ERANGE,
          "7-byte one past end rejected");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_SET_PACKED, 5, 0x0102, 7) == -ERANGE,
          "packed past end");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET_PACKED, 0, 0, 8) == -ERANGE,
          "packed count limit");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_FILL, 0xAA, 0, 0) == 10, "fill");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_GET, 9, 0, 0) == 0xAA, "filled last");
    CHECK(sys_res_bytes() == 10, "native byte accounting");
    CHECK(sys_res_new(SYS_KIND_BYTE_BUFFER, 65, 0) == -EDQUOT, "byte quota");
    CHECK(sys_res_new(SYS_KIND_BYTE_BUFFER, SYS_MAX_BYTE_BUFFER + 1, 0) == -ERANGE,
          "oversized buffer rejected");
    CHECK(sys_res_new(SYS_KIND_BYTE_BUFFER, -1, 0) == -EINVAL, "negative buffer rejected");
    CHECK(sys_res_close(t, SYS_KIND_BYTE_BUFFER) == 1, "buffer close");
    CHECK(sys_res_bytes() == 0, "byte accounting released");
    t = sys_res_new(SYS_KIND_BYTE_BUFFER, 0, 0);
    CHECK(t > 0, "zero-length buffer");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_LEN, 0, 0, 0) == 0, "zero len");
    CHECK(sys_res_close(t, SYS_KIND_BYTE_BUFFER) == 1, "zero close");
    CHECK(sys_adapter_shutdown() == 0, "shutdown buffers");

    /* Exact 16 MiB boundary and one past it. */
    CHECK(sys_adapter_init(8, 2L * SYS_MAX_BYTE_BUFFER) == 0, "init 16 MiB");
    t = sys_res_new(SYS_KIND_BYTE_BUFFER, SYS_MAX_BYTE_BUFFER, 0);
    CHECK(t > 0, "exact 16 MiB buffer");
    CHECK(sys_res_op(t, SYS_KIND_BYTE_BUFFER, SYS_OP_BUF_LEN, 0, 0, 0) == SYS_MAX_BYTE_BUFFER,
          "exact 16 MiB length");
    CHECK(sys_res_new(SYS_KIND_BYTE_BUFFER, SYS_MAX_BYTE_BUFFER + 1, 0) == -ERANGE,
          "one past 16 MiB rejected");
    CHECK(sys_res_close(t, SYS_KIND_BYTE_BUFFER) == 1, "close 16 MiB");
    CHECK(sys_adapter_shutdown() == 0, "shutdown 16 MiB");
}

static void test_ordering(void) {
    CHECK(sys_adapter_init(0, 8) == -EINVAL, "zero resource cap");
    CHECK(sys_adapter_init(1, 0) == -EINVAL, "zero byte cap");
    CHECK(sys_adapter_init(65537, 8) == -EINVAL, "resource cap ceiling");
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -EINVAL, "create before init");
    CHECK(sys_res_nonce() == -EINVAL, "nonce before init");
    CHECK(sys_adapter_shutdown() == -EINVAL, "shutdown before init");
    CHECK(sys_adapter_init(2, 8) == 0, "final init");
    CHECK(sys_adapter_shutdown() == 0, "final shutdown");
}

int main(void) {
    test_identity();
    test_buffers();
    test_ordering();
    if (failures != 0) {
        fprintf(stderr, "%d native test failure(s)\n", failures);
        return 1;
    }
    printf("native_test: ok\n");
    return 0;
}
