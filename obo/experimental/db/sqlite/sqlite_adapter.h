/* Scalar-only SQLite adapter for the experimental db/sqlite package.
 *
 * libsqlite3.so.0 is present on the probe host but its development header is
 * not installed. The adapter therefore uses the reviewed manual declaration
 * subset in sqlite_decls.h and the cinterop resource registry. Obo receives
 * only integer tokens; sqlite3*, sqlite3_stmt* and char* stay in C.
 */
#ifndef OBO_DB_SQLITE_ADAPTER_H
#define OBO_DB_SQLITE_ADAPTER_H

long sq_open(long path_buf);
long sq_open_error_buf(void);
long sq_open_error_code(void);

long sq_conn_live(long conn_token);
long sq_conn_close(long conn_token);
long sq_exec(long conn_token, long sql_buf);
long sq_changes(long conn_token);
long sq_error_buf(long conn_token);
long sq_error_code(long conn_token);
long sq_errstr_buf(long code);

long sq_prepare(long conn_token, long sql_buf);
long sq_stmt_live(long stmt_token);
long sq_stmt_close(long stmt_token);
long sq_stmt_reset(long stmt_token);
long sq_stmt_bind_null(long stmt_token, long index);
long sq_stmt_bind_int(long stmt_token, long index, long value);
long sq_stmt_bind_text(long stmt_token, long index, long value_buf);
long sq_stmt_bind_blob(long stmt_token, long index, long value_buf);
long sq_stmt_step(long stmt_token);
long sq_stmt_column_count(long stmt_token);
long sq_stmt_column_type(long stmt_token, long col);
long sq_stmt_column_text_buf(long stmt_token, long col);
long sq_stmt_column_blob_buf(long stmt_token, long col);
long sq_stmt_column_int(long stmt_token, long col);
long sq_stmt_error_buf(long stmt_token);
long sq_stmt_error_code(long stmt_token);

#endif
