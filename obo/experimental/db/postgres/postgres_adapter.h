/* Scalar-only PostgreSQL adapter for the experimental db/postgres package.
 *
 * This builds on the reviewed cinterop libpq toolkit: connection creation,
 * status, result metadata and close still come from toolkit/libpq_adapter.c.
 * The only new C surface is the typed parameter packet used by execute().
 * Obo never sees a PGconn*, PGresult* or char*; it holds generation-checked
 * registry tokens.
 */
#ifndef OBO_DB_POSTGRES_ADAPTER_H
#define OBO_DB_POSTGRES_ADAPTER_H

long pg_open(long dsn_buf);
long pg_exec_typed(long conn_token, long packet_buf);

#endif
