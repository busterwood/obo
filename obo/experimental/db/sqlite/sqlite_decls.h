/* Minimal manual declarations for the SQLite 3 C API used by the experimental
 * db/sqlite package on cinterop v2.
 *
 * Provenance: independently written for this project from the public SQLite 3
 * C API documentation (https://sqlite.org/c3ref/intro.html) and the installed
 * library's ABI. The probe host has libsqlite3.so.0 and the sqlite3 3.45.1
 * shell but no /usr/include/sqlite3.h and no pkg-config sqlite3.pc. This file
 * therefore does NOT claim a header check: sqlite.cbind records signature mode
 * "declarations-only". These declarations are trusted build input, not a
 * substitute for the upstream header, and cover only the functions this
 * binding calls.
 *
 * ABI-identical declarations-only spellings: a byte argument is declared
 * `const void *` (the v2 borrow_bytes C parameter) and sqlite3_int64 is
 * declared `long` (64-bit on x86_64-linux-gnu). The declaration cannot check
 * the installed header; tests/real_sqlite_abi_test.c checks every symbol and
 * signature against a compiled probe, and design section 6.2 records the
 * compromise.
 *
 * Copyright and licence: the declarations below are an ABI description using
 * the public C types and function names documented by SQLite. SQLite itself is
 * in the public domain. No upstream header text is reproduced.
 */
#ifndef OBO_DB_SQLITE_DECLS_H
#define OBO_DB_SQLITE_DECLS_H

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long sqlite3_int64;
typedef void (*sqlite3_destructor_type)(void *);

/* sqlite3_open_v2 flags. */
#define SQLITE_OPEN_READWRITE 0x00000002
#define SQLITE_OPEN_CREATE    0x00000004
#define SQLITE_OPEN_URI       0x00000040

/* Result codes. */
#define SQLITE_OK     0
#define SQLITE_ERROR  1
#define SQLITE_BUSY   5
#define SQLITE_NOMEM  7
#define SQLITE_CONSTRAINT 19
#define SQLITE_MISUSE 21
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
extern int sqlite3_close_v2(sqlite3 *db);
extern int sqlite3_prepare_v2(sqlite3 *db, const void *zSql, int nByte,
                              sqlite3_stmt **ppStmt, const char **pzTail);
extern int sqlite3_bind_null(sqlite3_stmt *stmt, int index);
extern int sqlite3_bind_int64(sqlite3_stmt *stmt, int index,
                              sqlite3_int64 value);
extern int sqlite3_bind_text(sqlite3_stmt *stmt, int index, const void *value,
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
extern int sqlite3_errcode(sqlite3 *db);
extern sqlite3 *sqlite3_db_handle(sqlite3_stmt *stmt);
extern sqlite3_stmt *sqlite3_next_stmt(sqlite3 *db, sqlite3_stmt *stmt);
extern int sqlite3_changes(sqlite3 *db);
extern int sqlite3_finalize(sqlite3_stmt *stmt);

/* The one trusted C helper: inside one native call it reads the connection's
 * error code for a statement. The sqlite3* from sqlite3_db_handle never leaves
 * C, so obo holds no pointer. */
extern int sq_stmt_errcode(sqlite3_stmt *stmt);

/* Trusted helper for exec(): prepare from an absolute offset in the caller's
 * SQL bytes and return the tail as an absolute pointer (see sq_prepare_at.c). */
extern int sq_prepare_at(sqlite3 *db, const void *sql, int n, int off,
                         sqlite3_stmt **st, const char **tail);

#endif
