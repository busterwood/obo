/* Experimental SQLite adapter over the cinterop resource registry.
 *
 * Every exported function takes and returns scalar long values. The adapter
 * owns sqlite3*, sqlite3_stmt* and all error-message copies. A Connection
 * token cannot be closed while a statement prepared from it is still live:
 * close returns 0 and records SQLITE_BUSY, so the caller finalizes statements
 * first. Statements are finalized exactly once by their explicit close.
 *
 * SQL and text inputs reject embedded NUL before reaching SQLite. BLOB values
 * are bound and copied with their exact length, including NUL. Error messages
 * are copied before sqlite3_free or before a failed connection is closed.
 */
#include "../../cinterop/toolkit/cres.h"
#include "sqlite_adapter.h"
#include "sqlite_decls.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define SQ_MAX_PATH 4096L
#define SQ_MAX_SQL (1024L * 1024L)
#define SQ_MAX_VALUE (16L * 1024L * 1024L)
#define SQ_OPEN_ERROR_MAX 512

typedef struct sq_conn {
    sqlite3 *db;
    char *last_error;
    int last_code;
    long stmt_live;
} sq_conn;

typedef struct sq_stmt {
    sqlite3_stmt *stmt;
    sq_conn *owner;
    char *last_error;
    int last_code;
} sq_stmt;

static char g_open_error[SQ_OPEN_ERROR_MAX];
static int g_open_code = 0;

static void copy_bounded(char *dst, size_t cap, const char *src) {
    size_t n;
    if (cap == 0) return;
    if (src == NULL) src = "";
    n = strlen(src);
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

static void set_conn_error(sq_conn *c, int code, const char *msg) {
    char *copy = NULL;
    if (c == NULL) return;
    if (msg != NULL && msg[0] != 0) {
        size_t n = strlen(msg);
        copy = (char *)malloc(n + 1);
        if (copy != NULL) memcpy(copy, msg, n + 1);
    }
    free(c->last_error);
    c->last_error = copy;
    c->last_code = code;
}

static void clear_conn_error(sq_conn *c) {
    if (c == NULL) return;
    free(c->last_error);
    c->last_error = NULL;
    c->last_code = 0;
}

static void set_conn_error_from_db(sq_conn *c, int code) {
    set_conn_error(c, code, c != NULL && c->db != NULL ? sqlite3_errmsg(c->db)
                                                       : sqlite3_errstr(code));
}

/* Statement diagnostics are separate from the connection's last error. A
 * successful bind/step on statement B must not erase statement A's diagnosis,
 * and a failed step on A must not appear on B. */
static void set_stmt_error(sq_stmt *s, int code, const char *msg) {
    char *copy = NULL;
    if (s == NULL) return;
    if (msg != NULL && msg[0] != 0) {
        size_t n = strlen(msg);
        copy = (char *)malloc(n + 1);
        if (copy != NULL) memcpy(copy, msg, n + 1);
    }
    free(s->last_error);
    s->last_error = copy;
    s->last_code = code;
}

static void clear_stmt_error(sq_stmt *s) {
    if (s == NULL) return;
    free(s->last_error);
    s->last_error = NULL;
    s->last_code = 0;
}

static void set_stmt_error_from_db(sq_stmt *s, int code) {
    set_stmt_error(s, code, s != NULL && s->owner != NULL && s->owner->db != NULL
                                ? sqlite3_errmsg(s->owner->db)
                                : sqlite3_errstr(code));
}

static long buf_from_cstr(const char *s) {
    if (s == NULL) s = "";
    return cres_buf_from_bytes(s, strlen(s));
}

static int has_nul(const unsigned char *p, size_t n) {
    size_t i;
    for (i = 0; i < n; i++) if (p[i] == 0) return 1;
    return 0;
}

static char *copy_cstr(const unsigned char *p, size_t n) {
    char *s;
    if (n > (size_t)INT_MAX || has_nul(p, n)) return NULL;
    s = (char *)malloc(n + 1);
    if (s == NULL) return NULL;
    if (n != 0) memcpy(s, p, n);
    s[n] = 0;
    return s;
}

static void release_conn(void *p) {
    sq_conn *c = (sq_conn *)p;
    if (c == NULL) return;
    if (c->db != NULL) sqlite3_close(c->db);
    free(c->last_error);
    free(c);
}

static void release_stmt(void *p) {
    sq_stmt *s = (sq_stmt *)p;
    if (s == NULL) return;
    if (s->stmt != NULL) sqlite3_finalize(s->stmt);
    if (s->owner != NULL && s->owner->stmt_live > 0) s->owner->stmt_live--;
    free(s->last_error);
    free(s);
}

static sq_conn *get_conn(long token) {
    return (sq_conn *)cres_peek(token, CRES_KIND_CONN);
}

static sq_stmt *get_stmt(long token) {
    return (sq_stmt *)cres_peek(token, CRES_KIND_STMT);
}

long sq_open(long path_buf) {
    unsigned char *data = NULL;
    size_t len = 0;
    char *path;
    sqlite3 *db = NULL;
    sq_conn *c;
    long token;
    int rc;

    g_open_error[0] = 0;
    g_open_code = 0;
    if (!cres_buf_data(path_buf, &data, &len)) {
        copy_bounded(g_open_error, sizeof(g_open_error), "invalid path buffer");
        g_open_code = -1;
        return 0;
    }
    if (len > (size_t)SQ_MAX_PATH) {
        copy_bounded(g_open_error, sizeof(g_open_error), "database path is too long");
        g_open_code = -1;
        return 0;
    }
    path = copy_cstr(data, len);
    if (path == NULL) {
        copy_bounded(g_open_error, sizeof(g_open_error),
                     "database path contains an embedded NUL or is too long");
        g_open_code = -1;
        return 0;
    }
    rc = sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE |
                         SQLITE_OPEN_URI, NULL);
    free(path);
    if (rc != SQLITE_OK) {
        copy_bounded(g_open_error, sizeof(g_open_error),
                     db != NULL ? sqlite3_errmsg(db) : sqlite3_errstr(rc));
        g_open_code = rc;
        if (db != NULL) sqlite3_close(db);
        return 0;
    }
    if (db == NULL) {
        copy_bounded(g_open_error, sizeof(g_open_error), "sqlite returned no database");
        g_open_code = -1;
        return 0;
    }
    c = (sq_conn *)calloc(1, sizeof(*c));
    if (c == NULL) {
        sqlite3_close(db);
        copy_bounded(g_open_error, sizeof(g_open_error), "adapter allocation failed");
        g_open_code = -3;
        return 0;
    }
    c->db = db;
    token = cres_register(CRES_KIND_CONN, c, release_conn);
    if (token == 0) {
        release_conn(c);
        copy_bounded(g_open_error, sizeof(g_open_error), "resource registry is full");
        g_open_code = -3;
        return 0;
    }
    return token;
}

long sq_open_error_buf(void) {
    return buf_from_cstr(g_open_error);
}

long sq_open_error_code(void) {
    return (long)g_open_code;
}

long sq_conn_live(long conn_token) {
    return get_conn(conn_token) != NULL;
}

long sq_conn_close(long conn_token) {
    sq_conn *c = get_conn(conn_token);
    if (c == NULL) return 0;
    if (c->stmt_live != 0) {
        set_conn_error(c, SQLITE_BUSY,
                       "connection has live statements; close them first");
        return 0;
    }
    return cres_close_kind(conn_token, CRES_KIND_CONN);
}

long sq_exec(long conn_token, long sql_buf) {
    sq_conn *c = get_conn(conn_token);
    unsigned char *data = NULL;
    size_t len = 0;
    char *sql;
    char *errmsg = NULL;
    int rc;
    if (c == NULL) return -1;
    if (!cres_buf_data(sql_buf, &data, &len)) return -1;
    if (len > (size_t)SQ_MAX_SQL) {
        set_conn_error(c, -1, "SQL text is too large");
        return -1;
    }
    sql = copy_cstr(data, len);
    if (sql == NULL) {
        set_conn_error(c, -1, "SQL text contains an embedded NUL");
        return -1;
    }
    rc = sqlite3_exec(c->db, sql, NULL, NULL, &errmsg);
    free(sql);
    if (rc != SQLITE_OK) {
        if (errmsg != NULL) {
            set_conn_error(c, rc, errmsg);
            sqlite3_free(errmsg);
        } else {
            set_conn_error_from_db(c, rc);
        }
        return (long)rc;
    }
    if (errmsg != NULL) sqlite3_free(errmsg);
    clear_conn_error(c);
    return SQLITE_OK;
}

long sq_changes(long conn_token) {
    sq_conn *c = get_conn(conn_token);
    if (c == NULL) return -1;
    return (long)sqlite3_changes(c->db);
}

long sq_error_buf(long conn_token) {
    sq_conn *c = get_conn(conn_token);
    if (c == NULL) return -1;
    return buf_from_cstr(c->last_error != NULL ? c->last_error : "");
}

long sq_error_code(long conn_token) {
    sq_conn *c = get_conn(conn_token);
    if (c == NULL) return -1;
    return (long)c->last_code;
}

long sq_errstr_buf(long code) {
    return buf_from_cstr(sqlite3_errstr((int)code));
}

static int only_one_statement(const char *tail) {
    const char *p = tail;
    if (p == NULL) return 1;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n' || *p == ';') p++;
    return *p == 0;
}

long sq_prepare(long conn_token, long sql_buf) {
    sq_conn *c = get_conn(conn_token);
    unsigned char *data = NULL;
    size_t len = 0;
    char *sql;
    sqlite3_stmt *stmt = NULL;
    const char *tail = NULL;
    sq_stmt *s;
    long token;
    int rc;

    if (c == NULL) return -1;
    if (!cres_buf_data(sql_buf, &data, &len)) return -1;
    if (len > (size_t)SQ_MAX_SQL || len >= (size_t)INT_MAX) {
        set_conn_error(c, -1, "SQL text is too large");
        return -1;
    }
    sql = copy_cstr(data, len);
    if (sql == NULL) {
        set_conn_error(c, -1, "SQL text contains an embedded NUL");
        return -1;
    }
    rc = sqlite3_prepare_v2(c->db, sql, (int)len + 1, &stmt, &tail);
    if (rc != SQLITE_OK) {
        set_conn_error_from_db(c, rc);
        if (stmt != NULL) sqlite3_finalize(stmt);
        free(sql);
        return (long)rc;
    }
    if (stmt == NULL) {
        set_conn_error(c, 1, "SQL text contains no statement");
        free(sql);
        return 0;
    }
    if (!only_one_statement(tail)) {
        sqlite3_finalize(stmt);
        set_conn_error(c, 1, "prepare accepts exactly one SQL statement");
        free(sql);
        return 0;
    }
    free(sql);
    s = (sq_stmt *)calloc(1, sizeof(*s));
    if (s == NULL) {
        sqlite3_finalize(stmt);
        set_conn_error(c, -3, "adapter allocation failed");
        return -3;
    }
    s->stmt = stmt;
    s->owner = c;
    c->stmt_live++;
    token = cres_register(CRES_KIND_STMT, s, release_stmt);
    if (token == 0) {
        release_stmt(s);
        set_conn_error(c, -3, "resource registry is full");
        return -3;
    }
    clear_conn_error(c);
    return token;
}

long sq_stmt_live(long stmt_token) {
    return get_stmt(stmt_token) != NULL;
}

long sq_stmt_close(long stmt_token) {
    return cres_close_kind(stmt_token, CRES_KIND_STMT);
}

long sq_stmt_reset(long stmt_token) {
    sq_stmt *s = get_stmt(stmt_token);
    if (s == NULL) return -1;
    /* sqlite3_reset returns the previous step error as a convenience, but it
     * has already reset the statement. That prior error must not be reported as
     * a reset failure: reset is successful and the statement is reusable. */
    (void)sqlite3_reset(s->stmt);
    clear_stmt_error(s);
    return SQLITE_OK;
}

static int check_index(sq_stmt *s, long index) {
    int n;
    if (s == NULL) return 0;
    n = sqlite3_bind_parameter_count(s->stmt);
    if (index < 1 || index > (long)n) {
        set_stmt_error(s, SQLITE_RANGE, "parameter index out of range");
        return 0;
    }
    return 1;
}

long sq_stmt_bind_null(long stmt_token, long index) {
    sq_stmt *s = get_stmt(stmt_token);
    int rc;
    if (!check_index(s, index)) return s == NULL ? -1 : SQLITE_RANGE;
    rc = sqlite3_bind_null(s->stmt, (int)index);
    if (rc != SQLITE_OK) set_stmt_error_from_db(s, rc);
    else clear_stmt_error(s);
    return (long)rc;
}

long sq_stmt_bind_int(long stmt_token, long index, long value) {
    sq_stmt *s = get_stmt(stmt_token);
    int rc;
    if (!check_index(s, index)) return s == NULL ? -1 : SQLITE_RANGE;
    rc = sqlite3_bind_int64(s->stmt, (int)index, (sqlite3_int64)value);
    if (rc != SQLITE_OK) set_stmt_error_from_db(s, rc);
    else clear_stmt_error(s);
    return (long)rc;
}

static long bind_bytes(long stmt_token, long index, long value_buf, int is_text) {
    sq_stmt *s = get_stmt(stmt_token);
    unsigned char *data = NULL;
    size_t len = 0;
    int rc;
    if (!check_index(s, index)) return s == NULL ? -1 : SQLITE_RANGE;
    if (!cres_buf_data(value_buf, &data, &len)) {
        set_stmt_error(s, -1, "invalid value buffer");
        return -1;
    }
    if (len > (size_t)SQ_MAX_VALUE || len > (size_t)INT_MAX) {
        set_stmt_error(s, -1, "bound value is too large");
        return -1;
    }
    if (is_text && has_nul(data, len)) {
        set_stmt_error(s, -1, "text value contains an embedded NUL");
        return -1;
    }
    if (is_text) {
        rc = sqlite3_bind_text(s->stmt, (int)index, (const char *)data, (int)len,
                               OBO_SQLITE_TRANSIENT);
    } else {
        rc = sqlite3_bind_blob(s->stmt, (int)index, (const void *)data, (int)len,
                               OBO_SQLITE_TRANSIENT);
    }
    if (rc != SQLITE_OK) set_stmt_error_from_db(s, rc);
    else clear_stmt_error(s);
    return (long)rc;
}

long sq_stmt_bind_text(long stmt_token, long index, long value_buf) {
    return bind_bytes(stmt_token, index, value_buf, 1);
}

long sq_stmt_bind_blob(long stmt_token, long index, long value_buf) {
    return bind_bytes(stmt_token, index, value_buf, 0);
}

long sq_stmt_step(long stmt_token) {
    sq_stmt *s = get_stmt(stmt_token);
    int rc;
    if (s == NULL) return -1;
    rc = sqlite3_step(s->stmt);
    if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
        set_stmt_error_from_db(s, rc);
        return (long)rc;
    }
    clear_stmt_error(s);
    return (long)rc;
}

long sq_stmt_column_count(long stmt_token) {
    sq_stmt *s = get_stmt(stmt_token);
    if (s == NULL) return -1;
    return (long)sqlite3_column_count(s->stmt);
}

static int check_column(sq_stmt *s, long col) {
    int n;
    if (s == NULL) return 0;
    n = sqlite3_column_count(s->stmt);
    return col >= 0 && col < (long)n;
}

long sq_stmt_column_type(long stmt_token, long col) {
    sq_stmt *s = get_stmt(stmt_token);
    if (!check_column(s, col)) return -1;
    return (long)sqlite3_column_type(s->stmt, (int)col);
}

long sq_stmt_column_text_buf(long stmt_token, long col) {
    sq_stmt *s = get_stmt(stmt_token);
    const unsigned char *p;
    int n;
    long t;
    if (!check_column(s, col)) return -1;
    if (sqlite3_column_type(s->stmt, (int)col) == SQLITE_NULL) return 0;
    p = sqlite3_column_text(s->stmt, (int)col);
    n = sqlite3_column_bytes(s->stmt, (int)col);
    if (n < 0) return -1;
    if (p == NULL && n != 0) return -1;
    t = cres_buf_from_bytes(p, (size_t)n);
    /* 0 means SQL NULL to the wrapper, so report a copy/allocation failure
     * distinctly: a non-NULL value must never silently become empty. */
    return t > 0 ? t : -2;
}

long sq_stmt_column_blob_buf(long stmt_token, long col) {
    sq_stmt *s = get_stmt(stmt_token);
    const void *p;
    int n;
    long t;
    if (!check_column(s, col)) return -1;
    if (sqlite3_column_type(s->stmt, (int)col) == SQLITE_NULL) return 0;
    p = sqlite3_column_blob(s->stmt, (int)col);
    n = sqlite3_column_bytes(s->stmt, (int)col);
    if (n < 0) return -1;
    if (p == NULL && n != 0) return -1;
    t = cres_buf_from_bytes(p, (size_t)n);
    return t > 0 ? t : -2;
}

long sq_stmt_column_int(long stmt_token, long col) {
    sq_stmt *s = get_stmt(stmt_token);
    if (!check_column(s, col)) return 0;
    return (long)sqlite3_column_int64(s->stmt, (int)col);
}

long sq_stmt_error_buf(long stmt_token) {
    sq_stmt *s = get_stmt(stmt_token);
    if (s == NULL) return -1;
    return buf_from_cstr(s->last_error != NULL ? s->last_error : "");
}

long sq_stmt_error_code(long stmt_token) {
    sq_stmt *s = get_stmt(stmt_token);
    if (s == NULL) return -1;
    return (long)s->last_code;
}
