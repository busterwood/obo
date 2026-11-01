/* The one trusted C helper for the SQLite v2 binding (design 6.1/6.2).
 *
 * sqlite3_column_text returns NULL both for SQL NULL and for out-of-memory,
 * so after an empty text copy the wrapper needs the connection's error code.
 * sqlite3_errcode takes a sqlite3*, and the statement's connection pointer must
 * not become an obo value; this helper keeps it entirely in C. */
#include "sqlite_decls.h"

int sq_stmt_errcode(sqlite3_stmt *stmt) {
    if (stmt == 0) return 0;
    return sqlite3_errcode(sqlite3_db_handle(stmt));
}
