/* Minimal manual declarations for the libpq 16 public C API used by the
 * experimental adapter.
 *
 * Provenance: written for this project from the PostgreSQL 16 libpq manual
 * (https://www.postgresql.org/docs/16/libpq.html). This is NOT a copy of the
 * PostgreSQL header, which is not installed on the probe host. It declares only
 * the opaque types, status values, and functions this adapter calls. The
 * development header is absent, so the cbind records signature mode
 * "declarations-only"; the declarations are trusted build input, not a
 * substitute for a real header check.
 *
 * Copyright and licence: the declarations below are an independently authored
 * ABI description using the public C types and function names documented by
 * PostgreSQL. See the repository NOTICE and PostgreSQL's licence for libpq
 * itself. No PostgreSQL header text is reproduced.
 */
#ifndef OBO_CINTEROP_LIBPQ_DECLS_H
#define OBO_CINTEROP_LIBPQ_DECLS_H

typedef struct pg_conn PGconn;
typedef struct pg_result PGresult;
typedef unsigned int Oid;

/* PQconninfoOption: documented public layout. Used only to ask libpq whether
 * the trusted conninfo already names connect_timeout; this is not a hand-rolled
 * conninfo parser. */
typedef struct PQconninfoOption {
    char *keyword;
    char *envvar;
    char *compiled;
    char *val;
    char *label;
    char *dispchar;
    int dispsize;
} PQconninfoOption;

/* ConnStatusType */
#define CONNECTION_OK 0
#define CONNECTION_BAD 1

/* ExecStatusType (stable public values as of PostgreSQL 16) */
#define PGRES_EMPTY_QUERY 0
#define PGRES_COMMAND_OK 1
#define PGRES_TUPLES_OK 2
#define PGRES_COPY_OUT 3
#define PGRES_COPY_IN 4
#define PGRES_BAD_RESPONSE 5
#define PGRES_NONFATAL_ERROR 6
#define PGRES_FATAL_ERROR 7
#define PGRES_COPY_BOTH 8
#define PGRES_SINGLE_TUPLE 9
#define PGRES_PIPELINE_SYNC 10
#define PGRES_PIPELINE_ABORTED 11
#define PGRES_TUPLES_CHUNK 12

extern int PQlibVersion(void);

extern PQconninfoOption *PQconninfoParse(const char *conninfo, char **errmsg);
extern void PQconninfoFree(PQconninfoOption *connOptions);
extern void PQfreemem(void *ptr);

extern PGconn *PQconnectdb(const char *conninfo);
extern int PQstatus(const PGconn *conn);
extern char *PQerrorMessage(const PGconn *conn);
extern void PQfinish(PGconn *conn);

extern PGresult *PQexecParams(PGconn *conn, const char *command,
                              int nParams, const Oid *paramTypes,
                              const char *const *paramValues,
                              const int *paramLengths,
                              const int *paramFormats,
                              int resultFormat);
extern int PQresultStatus(const PGresult *res);
extern int PQntuples(const PGresult *res);
extern int PQnfields(const PGresult *res);
extern int PQgetisnull(const PGresult *res, int tup_num, int field_num);
extern int PQgetlength(const PGresult *res, int tup_num, int field_num);
extern char *PQgetvalue(const PGresult *res, int tup_num, int field_num);
extern char *PQfname(const PGresult *res, int field_num);
extern Oid PQftype(const PGresult *res, int field_num);
extern int PQfmod(const PGresult *res, int field_num);
extern char *PQresultErrorMessage(const PGresult *res);
extern void PQclear(PGresult *res);

/* Used only for the no-connection ownership probe. libpq documents that conn
 * may be NULL for this internal helper. */
extern PGresult *PQmakeEmptyPGresult(PGconn *conn, int status);

#endif
