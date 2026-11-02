/* Fake-backend C harness: scripting, close consumption, counters, time. */
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdio.h>

static int failures;

#define CHECK(cond, text) do { \
    if (!(cond)) { fprintf(stderr, "FAIL %s (%s:%d)\n", text, __FILE__, __LINE__); failures++; } \
} while (0)

int main(void) {
    long t;
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -EINVAL, "create before init");
    CHECK(sys_adapter_init(8, 1024) == 0, "init");
    CHECK(sys_adapter_init(8, 1024) == -EBUSY, "second init is busy");
    CHECK(sys_res_nonce() >= 2, "nonce after init");
    CHECK(sysfake_reset() == 1, "reset");

    CHECK(sysfake_push(SYS_CALL_NEW, -ENOMEM) == 1, "script create failure");
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -ENOMEM, "scripted create");
    CHECK(sys_res_call_count(SYS_CALL_NEW) == 1, "one create attempt");

    CHECK(sysfake_push(SYS_CALL_NEW, 0) == 1, "script create success");
    t = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t > 0, "created fake resource");

    CHECK(sysfake_push(SYS_CALL_OP, -EINTR) == 1, "script EINTR");
    CHECK(sys_res_op(t, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_WRITE, 5, 0, 0) == -EINTR,
          "EINTR distinct");
    CHECK(sys_res_call_count(SYS_CALL_OP) == 1, "no hidden retry");
    CHECK(sysfake_push(SYS_CALL_OP, -EAGAIN) == 1, "script EAGAIN");
    CHECK(sys_res_op(t, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_READ, 0, 0, 0) == -EAGAIN,
          "EAGAIN distinct");
    CHECK(sys_res_call_count(SYS_CALL_OP) == 2, "one call per operation");
    CHECK(sysfake_push(SYS_CALL_OP, 3) == 1, "script progress");
    CHECK(sys_res_op(t, SYS_KIND_TEST_RESOURCE, SYS_OP_TEST_PROGRESS, 0, 0, 0) == 3,
          "scripted progress");

    CHECK(sysfake_set_time(1234) == 1, "set time");
    CHECK(sysfake_now() == 1234, "fake time");

    CHECK(sysfake_push(SYS_CALL_CLOSE, -EINTR) == 1, "script close EINTR");
    CHECK(sys_res_close(t, SYS_KIND_TEST_RESOURCE) == -EINTR, "close error reported");
    CHECK(sys_res_validate(t, SYS_KIND_TEST_RESOURCE) == 0, "close still consumed token");
    CHECK(sys_res_live_count() == 0, "no manufactured leak");
    CHECK(sys_res_cleanup_failures() == 1, "cleanup failure counted");
    CHECK(sys_res_close(t, SYS_KIND_TEST_RESOURCE) == 0, "double close safe");

    CHECK(sysfake_reset() == 1, "reset counters");
    CHECK(sys_res_call_count(SYS_CALL_OP) == 0, "counters cleared");
    CHECK(sys_res_new(SYS_KIND_BYTE_BUFFER, 4, 0) > 0, "fake byte buffer still shared");
    CHECK(sys_adapter_shutdown() >= 0, "shutdown drains");

    CHECK(sys_adapter_init(1, 1024) == 0, "init one");
    t = sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0);
    CHECK(t > 0, "one fake resource");
    CHECK(sys_res_new(SYS_KIND_TEST_RESOURCE, 0, 0) == -EMFILE, "fake table full");
    CHECK(sys_res_close(t, SYS_KIND_TEST_RESOURCE) == 1, "free fake slot");
    CHECK(sys_adapter_shutdown() == 0, "shutdown one");
    if (failures != 0) {
        fprintf(stderr, "%d fake test failure(s)\n", failures);
        return 1;
    }
    printf("fake_test: ok\n");
    return 0;
}
