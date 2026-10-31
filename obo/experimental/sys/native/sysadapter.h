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

/* Must match sysplatform::KIND_* constants. */
enum {
    SYS_KIND_FILE = 1,
    SYS_KIND_DIRECTORY = 2,
    SYS_KIND_ITERATOR = 3,
    SYS_KIND_STREAM = 5,
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
    SYS_OP_BUF_FILL = 15,
    SYS_OP_BUF_GET64 = 16,
    SYS_OP_BUF_SET64 = 17
};

/* Library open-option bits. These are not a Linux flag word; the adapter maps
 * them. Must match the sysfile module's private constants. */
enum {
    SYS_OPEN_READ = 1,
    SYS_OPEN_WRITE = 2,
    SYS_OPEN_CREATE = 4,
    SYS_OPEN_EXCLUSIVE = 8,
    SYS_OPEN_TRUNCATE = 16,
    SYS_OPEN_APPEND = 32,
    SYS_OPEN_NOFOLLOW = 64,
    SYS_OPEN_NONBLOCK = 128
};

/* Library seek origins. */
enum { SYS_SEEK_START = 0, SYS_SEEK_CURRENT = 1, SYS_SEEK_END = 2 };

/* Fake call classes (diagnostics only; no production caller). */
enum {
    SYS_CALL_NEW = 1,
    SYS_CALL_OP = 2,
    SYS_CALL_CLOSE = 3,
    SYS_CALL_VALIDATE = 4,
    SYS_CALL_FILE_OPEN = 5,
    SYS_CALL_DIR_OPEN = 6,
    SYS_CALL_DIR_ITERATE = 7,
    SYS_CALL_DIR_NEXT = 8,
    SYS_CALL_READ = 9,
    SYS_CALL_WRITE = 10,
    SYS_CALL_SEEK = 11,
    SYS_CALL_TRUNCATE = 12,
    SYS_CALL_STAT = 13,
    SYS_CALL_DUP = 14,
    SYS_CALL_RETAG = 15,
    SYS_CALL_CLOCK = 16,
    SYS_CALL_SLEEP = 17,
    SYS_CALL_RANDOM = 18,
    SYS_CALL_STANDARD = 19
};

/* Fixed C-owned metadata packet: 16 signed 64-bit fields (128 bytes). */
#define SYS_STAT_PACKET_BYTES 128L
#define SYS_STAT_FIELDS 16L

#define SYS_MAX_BYTE_BUFFER (16L * 1024L * 1024L)

long sys_adapter_init(long max_resources, long max_bytes);
long sys_adapter_shutdown(void);
long sys_res_new(long kind, long a, long b);
long sys_res_nonce(void);
long sys_res_validate(long token, long kind);
long sys_res_kind(long token);
long sys_res_close(long token, long kind);
long sys_res_op(long token, long kind, long op, long a, long b, long c);
long sys_res_live_count(void);
long sys_res_live_kind(long kind);
long sys_res_bytes(void);
long sys_res_cleanup_failures(void);
long sys_res_call_count(long call_class);

/* P1a scalar operations. Every argument/result is long; path and payload bytes
 * live in C-owned byte-buffer resources reached by token. */
long sys_file_open(long dir_token, long path_token, long flags, long mode);
long sys_dir_open(long dir_token, long path_token, long follow);
long sys_dir_iterate(long dir_token);
long sys_dir_next(long it_token, long buf_token, long cap);
long sys_io_read(long token, long kind, long buf_token, long offset, long count, long position);
long sys_io_write(long token, long kind, long buf_token, long offset, long count, long position);
long sys_io_seek(long token, long kind, long offset, long origin);
long sys_io_truncate(long token, long kind, long length);
long sys_stat_get(long subject_token, long subject_kind, long dir_token,
                  long name_token, long follow, long buf_token);
long sys_file_dup(long token, long kind, long to_kind);
long sys_file_retag(long token, long from_kind, long to_kind);
long sys_clock_monotonic_ns(void);
long sys_clock_realtime_sec(void);
long sys_clock_realtime_nsec(void);
long sys_sleep_until(long monotonic_ns);
long sys_random(long buf_token, long offset, long count, long blocking);
long sys_stream_standard(long which);

/* Fake-backend controls. These symbols exist only in the fake link unit and
 * are never called by production wrappers. Tests drive them directly. */
long sysfake_reset(void);
long sysfake_push(long call_class, long result);
long sysfake_set_time(long value);
long sysfake_now(void);
long sysfake_set_realtime(long seconds, long nanos);
long sysfake_set_seed(long value);

#endif
