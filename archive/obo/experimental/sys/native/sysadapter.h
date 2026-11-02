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
    SYS_KIND_CHILD = 12,
    SYS_KIND_SIGNAL_SOURCE = 13,
    SYS_KIND_TERMINAL = 14,
    SYS_KIND_LOGGER = 15,
    SYS_KIND_CANCELLATION = 17,
    SYS_KIND_TEST_RESOURCE = 90,
    SYS_KIND_TEST_RESOURCE_ALT = 91,
    SYS_KIND_BYTE_BUFFER = 92,
    /* Internal P3 spawn aggregate; never placed in a public record. */
    SYS_KIND_SPAWN_PLAN = 93
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
    SYS_OP_BUF_SET64 = 17,
    /* Spawn-plan fields, applied through sys_res_op on SYS_KIND_SPAWN_PLAN. */
    SYS_OP_PLAN_EXEC = 20,
    SYS_OP_PLAN_ARGV = 21,
    SYS_OP_PLAN_ENV = 22,
    SYS_OP_PLAN_ENV_MODE = 23,
    SYS_OP_PLAN_DIRECTORY = 24,
    SYS_OP_PLAN_INPUT = 25,
    SYS_OP_PLAN_OUTPUT = 26,
    SYS_OP_PLAN_ERROR = 27,
    SYS_OP_PLAN_GROUP = 28
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
    SYS_CALL_CANCEL = 40,
    SYS_CALL_DIR_MKDIR = 41,
    SYS_CALL_DIR_UNLINK = 42,
    SYS_CALL_DIR_RMDIR = 43,
    SYS_CALL_DIR_READLINK = 44,
    SYS_CALL_DIR_SYNC = 45,
    SYS_CALL_OPEN_BENEATH = 46,
    SYS_CALL_CHMOD = 47,
    SYS_CALL_SYNC = 48,
    SYS_CALL_RENAME = 49,
    SYS_CALL_LINK = 50,
    SYS_CALL_SYMLINK = 51,
    SYS_CALL_LOG_OPEN = 52,
    SYS_CALL_LOG_EMIT = 53,
    SYS_CALL_LOG_DRAIN = 54,
    SYS_CALL_LOG_FLUSH = 55,
    SYS_CALL_PROC_PLAN_NEW = 56,
    SYS_CALL_PROC_PLAN_SET = 57,
    SYS_CALL_PROC_SPAWN = 58,
    SYS_CALL_PROC_PIPE = 59,
    SYS_CALL_PROC_DUP = 60,
    SYS_CALL_PROC_EXEC = 61,
    SYS_CALL_PROC_TAKE = 62,
    SYS_CALL_PROC_WAIT = 63,
    SYS_CALL_PROC_KILL = 64,
    SYS_CALL_PROC_DETACH = 65,
    SYS_CALL_PROC_REAP = 66,
    SYS_CALL_PROC_CWD = 67,
    SYS_CALL_PROC_CHDIR = 68,
    SYS_CALL_SIGNAL_INIT = 69,
    SYS_CALL_SIGNAL_SUBSCRIBE = 70,
    SYS_CALL_SIGNAL_NEXT = 71,
    SYS_CALL_SIGNAL_CLOSE = 72,
    SYS_CALL_TERMINAL_OPEN = 73,
    SYS_CALL_TERMINAL_SIZE = 74,
    SYS_CALL_TERMINAL_RAW = 75,
    SYS_CALL_TERMINAL_RESTORE = 76,
    SYS_CALL_TERMINAL_DUP = 77
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

/* P1b practical-filesystem operations. Names are anchored single components;
 * sys_file_open_beneath accepts a multi-component path and uses openat2. */
long sys_dir_mkdir(long dir_token, long name_token, long mode);
long sys_dir_unlink(long dir_token, long name_token);
long sys_dir_remove_empty(long dir_token, long name_token);
long sys_dir_readlink(long dir_token, long name_token, long buf_token, long cap);
long sys_dir_sync(long dir_token);
long sys_file_open_beneath(long dir_token, long path_token, long flags, long mode);
long sys_file_open_beneath_supported(void);
long sys_file_chmod(long token, long kind, long mode);
long sys_file_sync(long token, long kind, long data_only);
long sys_file_rename(long from_dir, long old_name, long to_dir, long new_name,
                     long replace);
long sys_file_link(long from_dir, long old_name, long to_dir, long new_name);
long sys_file_symlink(long target_token, long to_dir, long name_token);

/* P1b bounded logger. The adapter owns a CLOEXEC standard-stream duplicate and
 * a bounded queue. sys_log_emit returns 1 accepted, 0 dropped, or -errno. */
long sys_log_open(long which, long capacity);
long sys_log_emit(long token, long buf_token, long offset, long count,
                  long policy);
long sys_log_drain(long token, long finite, long deadline_ns);
long sys_log_flush(long token);
long sys_log_dropped(long token);
long sys_log_queued(long token);

/* P1b process-environment snapshot. Mutation has no exported adapter symbol. */
long sys_env_size(void);
long sys_env_snapshot(long buf_token, long cap);

/* P3 process, signal and terminal operations. Every argument and result is a
 * scalar; no pid, fd or termios struct reaches obo. */
enum {
    SYS_REDIRECT_INHERIT = 0,
    SYS_REDIRECT_NULL = 1,
    SYS_REDIRECT_PIPE = 2,
    SYS_REDIRECT_STREAM = 3,
    SYS_REDIRECT_MERGE_ERROR = 4
};
enum {
    SYS_TAKE_INPUT = 0,
    SYS_TAKE_OUTPUT = 1,
    SYS_TAKE_ERROR = 2
};
#define SYS_EXIT_EXITED 1L
#define SYS_EXIT_SIGNALLED 2L
#define SYS_EXIT_CORE 4L

long sys_proc_plan_set(long plan_token, long field, long a, long b);
long sys_proc_plan_new(void);
long sys_proc_spawn(long plan_token);
long sys_proc_take(long child_token, long which, long to_kind);
/* block != 0 waits; returns 0 while running, an encoded terminal status, or
 * -errno. The encoded status is ((payload & 0xffffff) << 8) | flags. */
long sys_proc_wait(long child_token, long block);
long sys_proc_terminate(long child_token, long force, long group);
long sys_proc_detach(long child_token);
long sys_proc_reap_children(void);
long sys_proc_child_count(void);
long sys_proc_cwd(long buf_token, long cap);
long sys_proc_chdir(long dir_token);
long sys_proc_arg_max(void);
long sys_proc_pidfd_supported(void);
/* Reactor integration: -EOPNOTSUPP when no pidfd exists. */
long sys_proc_watch_fd(long child_token);
/* Called by sys_adapter_shutdown after every registry release. */
void sys_proc_shutdown(void);

long sys_signal_init(long ignore_pipe);
long sys_signal_subscribe(long bits);
long sys_signal_next(long source_token);
/* Reactor integration for a signalfd source. */
long sys_signal_watch_fd(long source_token);

long sys_terminal_open(long which);
long sys_terminal_size(long terminal_token);
long sys_terminal_raw(long terminal_token);
long sys_terminal_restore(long terminal_token);
long sys_terminal_dup(long terminal_token);

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

/* Datagram metadata packet: originalSize, truncated, segmentSize (P2q adds the
 * third field; the packet is 24 bytes and remains bounded). */
#define SYS_DGRAM_META_BYTES 24L

/* P2q datagram batch metadata: one 40-byte entry per received datagram:
 * dataOffset, length, originalSize, truncated, segmentSize. */
#define SYS_DGRAM_BATCH_ENTRY 40L
#define SYS_DGRAM_MAX_BATCH 64L
#define SYS_DGRAM_MAX_BATCH_BYTES 65507L

/* Datagram bind option bits (library bits, not Linux flags). */
enum {
    SYS_DGRAM_REUSE_PORT = 1,
    SYS_DGRAM_GRO = 2,
    SYS_DGRAM_GSO = 4,
    SYS_DGRAM_DONT_FRAGMENT = 8
};

/* sys_net_datagram_caps bit mask. */
enum { SYS_DGRAM_CAP_GSO = 1, SYS_DGRAM_CAP_GRO = 2 };

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
long sys_net_bind_datagram_options(long addr_token, long rcvbuf, long sndbuf,
                                   long flags);
long sys_net_datagram_caps(long token);
long sys_net_receive(long token, long buf_token, long max, long peer_token,
                     long meta_token);
long sys_net_receive_batch(long token, long data_token, long meta_token,
                           long peer_token, long max_packets, long max_bytes);
long sys_net_send(long token, long addr_token, long buf_token, long offset,
                  long count);
long sys_net_send_batch(long token, long addr_token, long buf_token,
                        long offset, long count, long segment_size);
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
long sysfake_disable_open_beneath(long disabled);
long sysfake_log_short(long bytes);
long sysfake_store_count(void);

/* Fake P3 controls. */
long sysfake_set_arg_max(long value);
long sysfake_proc_exit(long ordinal, long flags, long payload);
long sysfake_proc_last(void);
long sysfake_proc_write(long ordinal, long which, long buf_token,
                        long offset, long count);
long sysfake_proc_read(long ordinal, long which, long buf_token,
                       long offset, long count);
long sysfake_proc_ready(long child_token);
long sysfake_signal_ready(long source_token);
long sysfake_signal_push(long signal, long count);
long sysfake_terminal_size(long rows, long columns);
long sysfake_terminal_raw(long raw);

#endif
