/* Test-only probe declarations for tests/fake_libpq.c.
 *
 * The fake implements the libpq subset in toolkit/libpq_decls.h plus these
 * counters and last-call readers. A cbind that uses the fake in header mode
 * includes this file so the spec functions are checked against real
 * declarations, not hand-written ones.
 */
#ifndef OBO_CINTEROP_FAKE_LIBPQ_PROBE_H
#define OBO_CINTEROP_FAKE_LIBPQ_PROBE_H

long fake_get_conn_live(void);
long fake_get_result_live(void);
long fake_get_finish_count(void);
long fake_get_clear_count(void);
long fake_get_exec_count(void);
long fake_get_make_empty_count(void);
void fake_set_fail_alloc(long v);
long fake_last_param_count(void);
long fake_last_param_isnull(long i);
long fake_last_param_len(long i);
long fake_last_param_format(long i);
long fake_last_param_type(long i);
long fake_last_param_byte(long i, long j);
long fake_last_result_format(void);
long fake_last_sql_len(void);
long fake_last_sql_byte(long i);

#endif
