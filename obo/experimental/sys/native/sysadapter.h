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
    SYS_KIND_REACTOR = 6,
    SYS_KIND_WATCH = 7,
    SYS_KIND_TIMER = 8,
    SYS_KIND_LISTENER = 9,
    SYS_KIND_CONNECTION = 10,
    SYS_KIND_DATAGRAM = 11,
    SYS_KIND_CANCELLATION = 17,
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
    SYS_CALL_STANDARD = 19,
    SYS_CALL_NET_LISTEN = 20,
    SYS_CALL_ACCEPT = 21,
    SYS_CALL_CONNECT = 22,
    SYS_CALL_FINISH_CONNECT = 23,
    SYS_CALL_CONN_READ = 24,
    SYS_CALL_CONN_WRITE = 25,
    SYS_CALL_SHUTDOWN = 26,
    SYS_CALL_NET_DUP = 27,
    SYS_CALL_ADDRESS = 28,
    SYS_CALL_SOCKOPT = 29,
    SYS_CALL_REACTOR_NEW = 30,
    SYS_CALL_WATCH = 31,
    SYS_CALL_MODIFY = 32,
    SYS_CALL_REMOVE = 33,
    SYS_CALL_TIMER = 34,
    SYS_CALL_CANCEL_TIMER = 35,
    SYS_CALL_WAIT = 36,
    SYS_CALL_WAKE = 37,
    SYS_CALL_STOP = 38,
    SYS_CALL_RESOLVE = 39,
    SYS_CALL_CANCEL = 40
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

/* P2 address packet: four 64-bit fields then up to 108 address bytes. */
#define SYS_ADDR_PACKET_BYTES 160L
#define SYS_ADDR_DATA_OFFSET 32L
#define SYS_ADDR_DATA_MAX 108L
enum {
    SYS_ADDR_FAMILY_NONE = 0,
    SYS_ADDR_FAMILY_IPV4 = 1,
    SYS_ADDR_FAMILY_IPV6 = 2,
    SYS_ADDR_FAMILY_UNIX_PATH = 3,
    SYS_ADDR_FAMILY_UNIX_ABSTRACT = 4
};

/* Listen option bits (library bits, not a Linux flag word). */
enum {
    SYS_LISTEN_REUSE_ADDRESS = 1,
    SYS_LISTEN_REUSE_PORT = 2,
    SYS_LISTEN_IPV6_ONLY = 4
};

/* Datagram metadata packet: originalSize, truncated. */
#define SYS_DGRAM_META_BYTES 16L

/* Resolver family/kind selectors. */
enum {
    SYS_FAMILY_ANY = 0,
    SYS_FAMILY_IPV4 = 4,
    SYS_FAMILY_IPV6 = 6
};
enum {
    SYS_SOCKET_STREAM = 1,
    SYS_SOCKET_DATAGRAM = 2
};

/* Resolver result packet: 16-byte header (count then reserved) then fixed
 * address records. Error encoding keeps EAI codes and errno distinct. */
#define SYS_RESOLVE_HEADER 16L
#define SYS_RESOLVE_MAX_RESULTS 64L
#define SYS_RESOLVE_EAI_BASE 100000L
#define SYS_RESOLVE_SYSERR_BASE 200000L

/* Reactor event packet: eight 64-bit fields. */
#define SYS_EVENT_PACKET_BYTES 64L
#define SYS_REACTOR_MAX_BATCH 64L

long sys_net_listen(long addr_token, long backlog, long flags);
long sys_net_accept(long listener_token);
long sys_net_connect(long addr_token);
long sys_net_finish_connect(long conn_token);
long sys_net_read(long token, long kind, long buf_token, long offset, long count);
long sys_net_write(long token, long kind, long buf_token, long offset, long count);
long sys_net_shutdown(long token, long kind, long how);
long sys_net_dup(long token, long kind, long to_kind);
long sys_net_address(long token, long kind, long which, long out_token);
long sys_net_set_no_delay(long token, long enabled);
long sys_net_set_keepalive(long token, long enabled, long idle, long interval,
                           long probes);
long sys_net_set_buffer(long token, long kind, long which, long size);
long sys_net_set_broadcast(long token, long enabled);
long sys_net_bind_datagram(long addr_token);
long sys_net_receive(long token, long buf_token, long max, long peer_token,
                     long meta_token);
long sys_net_send(long token, long addr_token, long buf_token, long offset,
                  long count);
long sys_net_state(long conn_token);

long sys_net_reactor_new(long max_watches, long max_timers);
long sys_net_cancel_new(void);
long sys_net_watch(long reactor_token, long source_token, long source_kind,
                   long readable, long writable, long tag);
long sys_net_modify(long watch_token, long readable, long writable);
long sys_net_remove(long watch_token);
long sys_net_timer(long reactor_token, long at_ns, long period_ns, long tag);
long sys_net_cancel_timer(long timer_token);
long sys_net_wait(long reactor_token, long finite, long deadline_ns,
                  long max_events, long out_token);
long sys_net_wake(long reactor_token);
long sys_net_stop(long reactor_token);
long sys_net_cancel_request(long cancellation_token);
long sys_net_cancel_pending(long cancellation_token);

long sys_resolve(long host_token, long service_token, long family, long kind,
                 long flags, long out_token, long max_results);

/* Called by sys_res_close before retiring any watchable source. */
void sys_net_forget_source(long token);

/* Fake-backend P2 controls. */
long sysfake_net_ready(long source_token);
long sysfake_net_advance(long nanos);
long sysfake_net_pending(long source_token, long readable, long writable);

/* Fake-backend controls. These symbols exist only in the fake link unit and
 * are never called by production wrappers. Tests drive them directly. */
long sysfake_reset(void);
long sysfake_push(long call_class, long result);
long sysfake_set_time(long value);
long sysfake_now(void);
long sysfake_set_realtime(long seconds, long nanos);
long sysfake_set_seed(long value);

#endif
