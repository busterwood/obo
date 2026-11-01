/* Real-library ABI test for every SQLite symbol in sqlite_decls.h.
 *
 * The binding is declarations-only (this host has no sqlite3.h), so this test
 * resolves each symbol from libsqlite3.so.0 with independent, public-API
 * prototypes and calls a representative path. That catches a wrong symbol name,
 * a calling-convention/declaration drift, and the two documented
 * declarations-only compromises: `const void *` for byte pointers and `long`
 * for sqlite3_int64 are ABI-identical to `const char *` and `long long` here.
 */
#include <dlfcn.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long long sqlite3_int64;
typedef void (*dtor_fn)(void *);

static void *lib;

static void *sym(const char *name) {
    void *p = dlsym(lib, name);
    if (!p) fprintf(stderr, "missing symbol: %s\n", name);
    return p;
}

#define REQ(name) do { if (!sym(name)) return 1; } while (0)

int main(void) {
    sqlite3 *db = 0;
    sqlite3_stmt *st = 0;
    int (*open_v2)(const char *, sqlite3 **, int, const char *);
    int (*close_v2)(sqlite3 *);
    int (*prepare_v2)(sqlite3 *, const void *, int, sqlite3_stmt **, const char **);
    int (*bind_null)(sqlite3_stmt *, int);
    int (*bind_int64)(sqlite3_stmt *, int, sqlite3_int64);
    int (*bind_text)(sqlite3_stmt *, int, const void *, int, dtor_fn);
    int (*bind_blob)(sqlite3_stmt *, int, const void *, int, dtor_fn);
    int (*parameter_count)(sqlite3_stmt *);
    int (*step)(sqlite3_stmt *);
    int (*reset)(sqlite3_stmt *);
    const unsigned char *(*column_text)(sqlite3_stmt *, int);
    const void *(*column_blob)(sqlite3_stmt *, int);
    int (*column_bytes)(sqlite3_stmt *, int);
    sqlite3_int64 (*column_int64)(sqlite3_stmt *, int);
    int (*column_type)(sqlite3_stmt *, int);
    int (*column_count)(sqlite3_stmt *);
    const char *(*errmsg)(sqlite3 *);
    const char *(*errstr)(int);
    int (*errcode)(sqlite3 *);
    sqlite3 *(*db_handle)(sqlite3_stmt *);
    sqlite3_stmt *(*next_stmt)(sqlite3 *, sqlite3_stmt *);
    int (*changes)(sqlite3 *);
    int (*finalize)(sqlite3_stmt *);
    const char *sql = "SELECT 42";
    const void *blob = "ab";
    sqlite3_int64 v;

    lib = dlopen("libsqlite3.so.0", RTLD_NOW);
    if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    REQ("sqlite3_open_v2"); REQ("sqlite3_close_v2"); REQ("sqlite3_prepare_v2");
    REQ("sqlite3_bind_null"); REQ("sqlite3_bind_int64"); REQ("sqlite3_bind_text");
    REQ("sqlite3_bind_blob"); REQ("sqlite3_bind_parameter_count");
    REQ("sqlite3_step"); REQ("sqlite3_reset"); REQ("sqlite3_column_text");
    REQ("sqlite3_column_blob"); REQ("sqlite3_column_bytes");
    REQ("sqlite3_column_int64"); REQ("sqlite3_column_type");
    REQ("sqlite3_column_count"); REQ("sqlite3_errmsg"); REQ("sqlite3_errstr");
    REQ("sqlite3_errcode"); REQ("sqlite3_db_handle"); REQ("sqlite3_next_stmt");
    REQ("sqlite3_changes"); REQ("sqlite3_finalize");

    open_v2 = (int (*)(const char *, sqlite3 **, int, const char *))sym("sqlite3_open_v2");
    close_v2 = (int (*)(sqlite3 *))sym("sqlite3_close_v2");
    prepare_v2 = (int (*)(sqlite3 *, const void *, int, sqlite3_stmt **, const char **))sym("sqlite3_prepare_v2");
    bind_null = (int (*)(sqlite3_stmt *, int))sym("sqlite3_bind_null");
    bind_int64 = (int (*)(sqlite3_stmt *, int, sqlite3_int64))sym("sqlite3_bind_int64");
    bind_text = (int (*)(sqlite3_stmt *, int, const void *, int, dtor_fn))sym("sqlite3_bind_text");
    bind_blob = (int (*)(sqlite3_stmt *, int, const void *, int, dtor_fn))sym("sqlite3_bind_blob");
    parameter_count = (int (*)(sqlite3_stmt *))sym("sqlite3_bind_parameter_count");
    step = (int (*)(sqlite3_stmt *))sym("sqlite3_step");
    reset = (int (*)(sqlite3_stmt *))sym("sqlite3_reset");
    column_text = (const unsigned char *(*)(sqlite3_stmt *, int))sym("sqlite3_column_text");
    column_blob = (const void *(*)(sqlite3_stmt *, int))sym("sqlite3_column_blob");
    column_bytes = (int (*)(sqlite3_stmt *, int))sym("sqlite3_column_bytes");
    column_int64 = (sqlite3_int64 (*)(sqlite3_stmt *, int))sym("sqlite3_column_int64");
    column_type = (int (*)(sqlite3_stmt *, int))sym("sqlite3_column_type");
    column_count = (int (*)(sqlite3_stmt *))sym("sqlite3_column_count");
    errmsg = (const char *(*)(sqlite3 *))sym("sqlite3_errmsg");
    errstr = (const char *(*)(int))sym("sqlite3_errstr");
    errcode = (int (*)(sqlite3 *))sym("sqlite3_errcode");
    db_handle = (sqlite3 *(*)(sqlite3_stmt *))sym("sqlite3_db_handle");
    next_stmt = (sqlite3_stmt *(*)(sqlite3 *, sqlite3_stmt *))sym("sqlite3_next_stmt");
    changes = (int (*)(sqlite3 *))sym("sqlite3_changes");
    finalize = (int (*)(sqlite3_stmt *))sym("sqlite3_finalize");

    if (open_v2(":memory:", &db, 2 | 4, 0) != 0 || !db) return 2;
    if (prepare_v2(db, sql, (int)strlen(sql), &st, 0) != 0 || !st) return 3;
    if (db_handle(st) != db) return 4;
    if (errcode(db) != 0) return 5;
    if (next_stmt(db, 0) != st) return 6;
    (void)errmsg(db);
    (void)errstr(5);
    (void)changes(db);
    if (step(st) != 100) return 7;
    if (column_type(st, 0) != 1) return 8;
    v = column_int64(st, 0);
    if (v != 42) return 9;
    if (column_count(st) != 1) return 10;
    if (column_bytes(st, 0) <= 0 || !column_text(st, 0)) return 11;
    (void)column_blob(st, 0);
    if (reset(st) != 0) return 12;
    if (step(st) != 100) return 13;
    if (finalize(st) != 0) return 14;
    if (next_stmt(db, 0) != 0) return 15;

    /* Exercise bind variants on a separate statement. */
    if (prepare_v2(db, "SELECT ?1", 9, &st, 0) != 0 || !st) return 17;
    if (parameter_count(st) != 1) return 18;
    if (bind_null(st, 1) != 0) return 19;
    if (bind_int64(st, 1, 7) != 0) return 20;
    if (bind_text(st, 1, "hi", 2, (dtor_fn)(size_t)-1) != 0) return 21;
    if (bind_blob(st, 1, blob, 2, (dtor_fn)(size_t)-1) != 0) return 22;
    if (step(st) != 100) return 23;
    if (column_bytes(st, 0) != 2) return 24;
    if (finalize(st) != 0) return 25;
    if (close_v2(db) != 0) return 26;
    dlclose(lib);
    printf("sqlite3 ABI symbols: ok\n");
    return 0;
}
