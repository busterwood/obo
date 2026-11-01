/* Deterministic fake libsqlite3 for the cinterop v2 binding tests.
 *
 * It implements only the declarations in sqlite_decls.h, with knobs selected by
 * the SQL text: "bad ..." fails prepare with a non-NULL *ppStmt, "SELECT
 * resetfail" fails step and reports the prior error from reset, "SELECT nomem"
 * makes column_text return NULL with SQLITE_NOMEM, and the SELECT counters read
 * this file's close/finalize/destructor counters. close_v2 marks a zombie while
 * statements live, exactly as SQLite documents (design 6.2).
 */
#include <stdlib.h>
#include <string.h>
#include "fake_sqlite.h"

struct sqlite3_stmt;
struct sqlite3 {
    int failed;
    int zombie;
    int live;
    struct sqlite3_stmt *first;
    int last_code;
};

struct sqlite3_stmt {
    sqlite3 *db;
    int kind;
    int done;
    int reset_err;
    int dtor;
    int len;
    unsigned char bytes[64];
    struct sqlite3_stmt *next;
};

enum {
    Q_SELECT = 0,
    Q_RESETFAIL,
    Q_NOMEM,
    Q_LAST_DTOR,
    Q_LAST_LEN,
    Q_LIVE,
    Q_CLOSED,
    Q_FINALIZED,
    Q_ZOMBIES
};

static long fake_live;
static long fake_closed;
static long fake_finalized;
static long fake_zombies;
static long fake_last_dtor;
static long fake_last_len;

static int starts(const void *p, int n, const char *s) {
    int m = (int)strlen(s);
    if (n < m) return 0;
    return memcmp(p, s, (size_t)m) == 0;
}

static int qkind(const void *p, int n) {
    if (starts(p, n, "SELECT resetfail")) return Q_RESETFAIL;
    if (starts(p, n, "SELECT nomem")) return Q_NOMEM;
    if (starts(p, n, "SELECT last_dtor")) return Q_LAST_DTOR;
    if (starts(p, n, "SELECT last_len")) return Q_LAST_LEN;
    if (starts(p, n, "SELECT live")) return Q_LIVE;
    if (starts(p, n, "SELECT closed")) return Q_CLOSED;
    if (starts(p, n, "SELECT finalized")) return Q_FINALIZED;
    if (starts(p, n, "SELECT zombies")) return Q_ZOMBIES;
    return Q_SELECT;
}

static void db_free(sqlite3 *db) {
    if (!db) return;
    free(db);
    fake_closed += 1;
}

int sqlite3_open_v2(const char *filename, sqlite3 **ppDb, int flags,
                    const char *zVfs) {
    sqlite3 *db;
    (void)flags;
    (void)zVfs;
    db = (sqlite3 *)calloc(1, sizeof(*db));
    if (!db) { *ppDb = 0; return 7; }
    if (filename && strstr(filename, "fail")) {
        db->failed = 1;
        db->last_code = 1;
        *ppDb = db;
        fake_live += 1;
        return 1;
    }
    *ppDb = db;
    fake_live += 1;
    return 0;
}

int sqlite3_close_v2(sqlite3 *db) {
    if (!db) return 0;
    if (db->live > 0) {
        db->zombie = 1;
        fake_zombies += 1;
        return 0;
    }
    db_free(db);
    return 0;
}

int sqlite3_finalize(sqlite3_stmt *stmt) {
    sqlite3 *db;
    if (!stmt) return 0;
    db = stmt->db;
    if (db) {
        sqlite3_stmt **pp = &db->first;
        while (*pp && *pp != stmt) pp = &(*pp)->next;
        if (*pp == stmt) *pp = stmt->next;
        if (db->live > 0) db->live -= 1;
        if (db->live == 0 && db->zombie) {
            fake_zombies -= 1;
            db_free(db);
        }
    }
    free(stmt);
    fake_finalized += 1;
    return 0;
}

int sqlite3_prepare_v2(sqlite3 *db, const void *zSql, int nByte,
                       sqlite3_stmt **ppStmt, const char **pzTail) {
    sqlite3_stmt *st;
    if (!db) return 21;
    if (nByte <= 0) {
        *ppStmt = 0;
        if (pzTail) *pzTail = (const char *)zSql;
        return 0;
    }
    if (starts(zSql, nByte, "bad")) {
        st = (sqlite3_stmt *)calloc(1, sizeof(*st));
        st->db = db;
        st->kind = Q_SELECT;
        *ppStmt = st;                       /* non-NULL on an error */
        if (pzTail) *pzTail = (const char *)zSql + 1;
        db->last_code = 1;
        db->live += 1;
        st->next = db->first;
        db->first = st;
        return 1;
    }
    st = (sqlite3_stmt *)calloc(1, sizeof(*st));
    if (!st) { *ppStmt = 0; return 7; }
    st->db = db;
    st->kind = qkind(zSql, nByte);
    if (nByte > (int)sizeof(st->bytes)) nByte = (int)sizeof(st->bytes);
    memcpy(st->bytes, zSql, (size_t)nByte);
    st->len = nByte;
    *ppStmt = st;
    if (pzTail) *pzTail = (const char *)zSql + nByte;
    db->live += 1;
    st->next = db->first;
    db->first = st;
    return 0;
}

int sqlite3_bind_null(sqlite3_stmt *stmt, int index) {
    (void)stmt; (void)index; return 0;
}

int sqlite3_bind_int64(sqlite3_stmt *stmt, int index, sqlite3_int64 value) {
    (void)stmt; (void)index; (void)value; return 0;
}

int sqlite3_bind_text(sqlite3_stmt *stmt, int index, const void *value, int n,
                      sqlite3_destructor_type destroy) {
    (void)index;
    if (!stmt) return 21;
    stmt->dtor = (int)(size_t)destroy;
    stmt->len = n;
    if (value && n > 0 && n <= (int)sizeof(stmt->bytes))
        memcpy(stmt->bytes, value, (size_t)n);
    fake_last_dtor = (long)(size_t)destroy;
    fake_last_len = n;
    return 0;
}

int sqlite3_bind_blob(sqlite3_stmt *stmt, int index, const void *value, int n,
                      sqlite3_destructor_type destroy) {
    return sqlite3_bind_text(stmt, index, value, n, destroy);
}

int sqlite3_bind_parameter_count(sqlite3_stmt *stmt) {
    (void)stmt; return 2;
}

int sqlite3_step(sqlite3_stmt *stmt) {
    if (!stmt) return 21;
    if (stmt->kind == Q_RESETFAIL) {
        stmt->reset_err = 19;
        stmt->db->last_code = 19;
        return 19;
    }
    if (stmt->done) return 101;
    stmt->done = 1;
    return 100;
}

int sqlite3_reset(sqlite3_stmt *stmt) {
    int rc;
    if (!stmt) return 21;
    rc = stmt->reset_err;
    stmt->reset_err = 0;
    stmt->done = 0;
    return rc;
}

int sqlite3_column_type(sqlite3_stmt *stmt, int col) {
    (void)col;
    if (!stmt) return 0;
    if (stmt->kind == Q_NOMEM) return 3;
    return 1;
}

int sqlite3_column_count(sqlite3_stmt *stmt) {
    (void)stmt; return 1;
}

const unsigned char *sqlite3_column_text(sqlite3_stmt *stmt, int col) {
    (void)col;
    if (!stmt) return 0;
    if (stmt->kind == Q_NOMEM) {
        stmt->db->last_code = 7;            /* NULL with SQLITE_NOMEM */
        return 0;
    }
    return (const unsigned char *)"x";
}

const void *sqlite3_column_blob(sqlite3_stmt *stmt, int col) {
    (void)col;
    if (!stmt || stmt->kind == Q_NOMEM || stmt->len == 0) return 0;
    return stmt->bytes;
}

int sqlite3_column_bytes(sqlite3_stmt *stmt, int col) {
    (void)col;
    if (!stmt || stmt->kind == Q_NOMEM) return 0;
    return stmt->len;
}

sqlite3_int64 sqlite3_column_int64(sqlite3_stmt *stmt, int col) {
    (void)col;
    if (!stmt) return 0;
    switch (stmt->kind) {
        case Q_LAST_DTOR: return (sqlite3_int64)fake_last_dtor;
        case Q_LAST_LEN: return (sqlite3_int64)fake_last_len;
        case Q_LIVE: return (sqlite3_int64)fake_live;
        case Q_CLOSED: return (sqlite3_int64)fake_closed;
        case Q_FINALIZED: return (sqlite3_int64)fake_finalized;
        case Q_ZOMBIES: return (sqlite3_int64)fake_zombies;
        default: return 1;
    }
}

const char *sqlite3_errmsg(sqlite3 *db) {
    if (db && db->failed) return "fake open failure";
    return "fake error";
}

const char *sqlite3_errstr(int code) {
    if (code == 5) return "database is locked";
    if (code == 7) return "out of memory";
    if (code == 19) return "constraint failed";
    return "fake error";
}

int sqlite3_errcode(sqlite3 *db) {
    if (!db) return 0;
    return db->last_code;
}

sqlite3 *sqlite3_db_handle(sqlite3_stmt *stmt) {
    return stmt ? stmt->db : 0;
}

sqlite3_stmt *sqlite3_next_stmt(sqlite3 *db, sqlite3_stmt *stmt) {
    if (!db) return 0;
    if (!stmt) return db->first;
    return 0;
}

int sqlite3_changes(sqlite3 *db) {
    (void)db; return 1;
}

int sq_stmt_errcode(sqlite3_stmt *stmt) {
    return stmt ? stmt->db->last_code : 0;
}
