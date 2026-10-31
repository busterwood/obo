/* Experimental systems platform adapter ABI (P0).
 *
 * Scalar-only boundary: every exported symbol takes `long` arguments and
 * returns `long`. No pointer, struct-by-value, callback, variadic, float or C
 * token crosses to obo. A negative return is -errno captured by the adapter
 * before any other call; the obo wrapper immediately maps it to a library code.
 *
 * The registry token lives only in this C adapter and in sysplatform's private
 * slot table. It is never part of a public obo record.
 */
#ifndef OBO_SYS_ADAPTER_H
#define OBO_SYS_ADAPTER_H

#include <stddef.h>

/* Must match sysplatform::KIND_* P0 fixtures. */
enum {
    SYS_KIND_TEST_RESOURCE = 90,
    SYS_KIND_TEST_RESOURCE_ALT = 91,
    SYS_KIND_BYTE_BUFFER = 92
};

/* Must match sysplatform::OP_*. */
enum {
    SYS_OP_TEST_WRITE = 1,
    SYS_OP_TEST_READ = 2,
    SYS_OP_TEST_PROGRESS = 3,
    SYS_OP_BUF_LEN = 10,
    SYS_OP_BUF_SET = 11,
    SYS_OP_BUF_GET = 12,
    SYS_OP_BUF_SET_PACKED = 13,
    SYS_OP_BUF_GET_PACKED = 14,
    SYS_OP_BUF_FILL = 15
};

/* Fake call classes (diagnostics only; no production caller). */
enum {
    SYS_CALL_NEW = 1,
    SYS_CALL_OP = 2,
    SYS_CALL_CLOSE = 3,
    SYS_CALL_VALIDATE = 4
};

#define SYS_MAX_BYTE_BUFFER (16L * 1024L * 1024L)

long sys_adapter_init(long max_resources, long max_bytes);
long sys_adapter_shutdown(void);
long sys_res_new(long kind, long a, long b);
long sys_res_validate(long token, long kind);
long sys_res_kind(long token);
long sys_res_close(long token, long kind);
long sys_res_op(long token, long kind, long op, long a, long b, long c);
long sys_res_live_count(void);
long sys_res_live_kind(long kind);
long sys_res_bytes(void);
long sys_res_cleanup_failures(void);
long sys_res_call_count(long call_class);

/* Fake-backend controls. These symbols exist only in the fake link unit and
 * are never called by production wrappers. Tests drive them directly. */
long sysfake_reset(void);
long sysfake_push(long call_class, long result);
long sysfake_set_time(long value);
long sysfake_now(void);

#endif
