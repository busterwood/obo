/* Minimal manual declarations for the SQLite 3 C API used by the
 * experimental db/sqlite adapter.
 *
 * Provenance: independently written for this project from the public SQLite 3
 * C API documentation (https://sqlite.org/c3ref/intro.html) and the installed
 * library's ABI. The probe host has libsqlite3.so.0 and the sqlite3 3.45.1
 * shell but no /usr/include/sqlite3.h and no pkg-config sqlite3.pc. This file
 * therefore does NOT claim a header check: sqlite.cbind records signature mode
 * "declarations-only". These declarations are trusted build input, not a
 * substitute for the upstream header, and they cover only the functions this
 * adapter calls.
 *
 * Copyright and licence: the declarations below are an ABI description using
 * the public C types and function names documented by SQLite. SQLite itself is
 * in the public domain. No upstream header text is reproduced.
 */
#ifndef OBO_DB_SQLITE_DECLS_H
#define OBO_DB_SQLITE_DECLS_H

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long long sqlite3_int64;
typedef void (*sqlite3_destructor_type)(void *);

/* SQLITE_TRANSIENT: SQLite copies the value before the call returns. */
#define OBO_SQLITE_TRANSIENT ((sqlite3_destructor_type)-1)

/* sqlite3_open_v2 flags. */
#define SQLITE_OPEN_READWRITE 0x00000002
#define SQLITE_OPEN_CREATE    0x00000004
#define SQLITE_OPEN_URI       0x00000040

/* Result codes. */
#define SQLITE_OK     0
#define SQLITE_BUSY   5
#define SQLITE_RANGE  25
#define SQLITE_ROW    100
#define SQLITE_DONE   101

/* Fundamental datatypes. */
#define SQLITE_INTEGER 1
#define SQLITE_FLOAT   2
#define SQLITE_TEXT    3
#define SQLITE_BLOB    4
#define SQLITE_NULL    5

extern int sqlite3_open_v2(const char *filename, sqlite3 **ppDb, int flags,
                           const char *zVfs);
extern int sqlite3_close(sqlite3 *db);
extern int sqlite3_exec(sqlite3 *db, const char *sql,
                        int (*callback)(void *, int, char **, char **),
                        void *arg, char **errmsg);
extern int sqlite3_prepare_v2(sqlite3 *db, const char *zSql, int nByte,
                              sqlite3_stmt **ppStmt, const char **pzTail);
extern int sqlite3_bind_null(sqlite3_stmt *stmt, int index);
extern int sqlite3_bind_int64(sqlite3_stmt *stmt, int index,
                              sqlite3_int64 value);
extern int sqlite3_bind_text(sqlite3_stmt *stmt, int index, const char *value,
                             int n, sqlite3_destructor_type destroy);
extern int sqlite3_bind_blob(sqlite3_stmt *stmt, int index, const void *value,
                             int n, sqlite3_destructor_type destroy);
extern int sqlite3_bind_parameter_count(sqlite3_stmt *stmt);
extern int sqlite3_step(sqlite3_stmt *stmt);
extern int sqlite3_reset(sqlite3_stmt *stmt);
extern const unsigned char *sqlite3_column_text(sqlite3_stmt *stmt, int col);
extern const void *sqlite3_column_blob(sqlite3_stmt *stmt, int col);
extern int sqlite3_column_bytes(sqlite3_stmt *stmt, int col);
extern sqlite3_int64 sqlite3_column_int64(sqlite3_stmt *stmt, int col);
extern int sqlite3_column_type(sqlite3_stmt *stmt, int col);
extern int sqlite3_column_count(sqlite3_stmt *stmt);
extern const char *sqlite3_errmsg(sqlite3 *db);
extern const char *sqlite3_errstr(int code);
extern int sqlite3_changes(sqlite3 *db);
extern int sqlite3_finalize(sqlite3_stmt *stmt);
extern void sqlite3_free(void *ptr);

#endif
