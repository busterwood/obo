/* Scalar-only libpq adapter API. Every value crosses the obo boundary as a
 * signed 64-bit integer. Negative returns are adapter errors; positive values
 * are registry tokens. No pointer is exposed to obo. */
#ifndef OBO_CINTEROP_LIBPQ_ADAPTER_H
#define OBO_CINTEROP_LIBPQ_ADAPTER_H

long pq_lib_version(void);
long pq_make_empty_result(long status);

long pq_connect(long dsn_buf);
long pq_conn_status(long conn_token);
long pq_conn_live(long conn_token);
long pq_conn_close(long conn_token);

long pq_exec_packet(long conn_token, long packet_token);
long pq_result_status(long res_token);
long pq_result_live(long res_token);
long pq_result_clear(long res_token);
long pq_ntuples(long res_token);
long pq_nfields(long res_token);
long pq_getisnull(long res_token, long row, long col);
long pq_getlength(long res_token, long row, long col);
long pq_value_buf(long res_token, long row, long col);
long pq_fname_buf(long res_token, long col);
long pq_ftype(long res_token, long col);
long pq_fmod(long res_token, long col);
long pq_error_buf(long token);
long pq_result_error_buf(long res_token);

#endif
