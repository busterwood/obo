/* Statement shims for the mssql binding.
 *
 * The foreign word points at the ms_stmt state (see ms_internal.h), so the
 * owner connection and the closed flag are always available. The connection
 * close marks children dead and frees their HSTMTs before disconnecting the
 * DBC; every stmt shim checks the flag and returns a clean diagnostic instead
 * of touching a freed driver handle.
 *
 * Cell reads use SQLGetData. The Phase 1 probe measured that:
 *  - SQL_C_BINARY reports the remaining byte count in the indicator;
 *  - SQL_C_CHAR reports SQL_NO_TOTAL on a truncated chunk, so the chunk length
 *    is recovered by pre-filling the buffer with 0xFF (never valid UTF-8) and
 *    finding the driver's NUL terminator. This also keeps embedded NULs exact;
 *  - a C_CHAR buffer of one byte makes no progress, so the buffer is always at
 *    least two bytes and grows by doubling to MS_MAX_VALUE + 4.
 *
 * Columns are consumed in ascending order by SQLGetData. ms_read_cell reads and
 * caches any skipped columns first, so text(1) before text(0) is well defined
 * and a later text(0) is served from the cache.
 */
#include <stdlib.h>
#include <string.h>
#include "ms_internal.h"

/* ---------------------------------------------------------------- state basics */

static void state_set_error(ms_stmt *s, const char *msg, int code) {
    char *p;
    if (!s) return;
    free(s->synthetic);
    s->synthetic = NULL;
    p = (char *)malloc(strlen(msg) + 1);
    if (p) {
        strcpy(p, msg);
        s->synthetic = p;
    }
    s->synthetic_code = code;
}

static void state_clear_error(ms_stmt *s) {
    if (!s) return;
    free(s->synthetic);
    s->synthetic = NULL;
    s->synthetic_code = 0;
}

static int stmt_ok(ms_stmt *s) {
    if (!s) return 0;
    if (s->closed || s->h == SQL_NULL_HSTMT) {
        state_set_error(s, "statement's connection is closed", 2);
        return 0;
    }
    return 1;
}

int ms_stmt_live(ms_stmt *s) {
    return (s && !s->closed && s->h != SQL_NULL_HSTMT) ? 1 : 0;
}

/* ------------------------------------------------------------------ cell state */

static void cell_free(ms_cell *c) {
    free(c->buf);
    c->buf = NULL;
    c->cap = 0;
    c->len = 0;
    c->status = 0;
    c->loaded = 0;
}

static void cells_free_all(ms_stmt *s) {
    int i;
    for (i = 0; i < s->ncells; i++) cell_free(&s->cells[i]);
    free(s->cells);
    s->cells = NULL;
    s->ncells = 0;
    s->row_valid = 0;
    s->read_next = 0;
}

static int cells_reset(ms_stmt *s, int n) {
    int i;
    for (i = 0; i < s->ncells; i++) cell_free(&s->cells[i]);
    free(s->cells);
    s->cells = NULL;
    s->ncells = 0;
    s->row_valid = 0;
    s->read_next = 0;
    if (n <= 0) return 0;
    s->cells = (ms_cell *)calloc((size_t)n, sizeof(ms_cell));
    if (!s->cells) {
        state_set_error(s, "out of memory allocating the column cache", 3);
        return -1;
    }
    s->ncells = n;
    return 0;
}

static int cells_init(ms_stmt *s) {
    SQLSMALLINT n = 0;
    SQLRETURN rc = SQLNumResultCols(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) {
        state_set_error(s, "result column count could not be read", 2);
        return -1;
    }
    if (n < 0 || n > MS_MAX_COLUMNS) {
        state_set_error(s, "result set has too many columns", 2);
        return -1;
    }
    return cells_reset(s, (int)n);
}

static void cells_clear_row(ms_stmt *s) {
    int i;
    for (i = 0; i < s->ncells; i++) {
        s->cells[i].loaded = 0;
        s->cells[i].len = 0;
        s->cells[i].status = 0;
    }
    s->row_valid = 0;
    s->read_next = 0;
}

/* ------------------------------------------------------------- statement life */

static ms_stmt *stmt_new(SQLHSTMT h, ms_conn *owner) {
    ms_stmt *s = (ms_stmt *)calloc(1, sizeof *s);
    if (!s) return NULL;
    s->h = h;
    s->owner = owner;
    if (owner) {
        s->next_sibling = owner->children;
        owner->children = s;
    }
    return s;
}

static void stmt_detach(ms_stmt *s) {
    ms_conn *c = s->owner;
    ms_stmt **pp;
    if (!c) return;
    pp = &c->children;
    while (*pp) {
        if (*pp == s) {
            *pp = s->next_sibling;
            break;
        }
        pp = &(*pp)->next_sibling;
    }
    s->owner = NULL;
    s->next_sibling = NULL;
}

static void binds_free(ms_stmt *s) {
    while (s->binds) {
        ms_bind_slot *b = s->binds;
        s->binds = b->next;
        free(b->ptr);
        free(b->ind);
        free(b);
    }
}

int ms_stmt_mark_closed(ms_stmt *s) {
    int rc = 0;
    if (!s) return 0;
    if (s->h) {
        /* The connection is still alive here; close the cursor and free the
         * HSTMT before the parent DBC is disconnected. */
        SQLRETURN fr;
        SQLFreeStmt(s->h, SQL_CLOSE);
        fr = SQLFreeHandle(SQL_HANDLE_STMT, s->h);
        if (!SQL_SUCCEEDED(fr)) rc = (int)fr;
        s->h = SQL_NULL_HSTMT;
    }
    s->closed = 1;
    s->owner = NULL;
    s->next_sibling = NULL;
    s->row_valid = 0;
    cells_free_all(s);
    binds_free(s);
    state_set_error(s, "statement's connection is closed", 2);
    return rc;
}

int ms_close_stmt(ms_stmt *s) {
    int rc = 0;
    if (!s) return 0;
    if (!s->closed) {
        if (s->owner) stmt_detach(s);
        if (s->h) {
            SQLRETURN cr = SQLFreeStmt(s->h, SQL_CLOSE);
            if (!SQL_SUCCEEDED(cr) && rc == 0) rc = (int)cr;
            cr = SQLFreeHandle(SQL_HANDLE_STMT, s->h);
            if (!SQL_SUCCEEDED(cr) && rc == 0) rc = (int)cr;
            s->h = SQL_NULL_HSTMT;
        }
        s->closed = 1;
    }
    cells_free_all(s);
    binds_free(s);
    free(s->synthetic);
    free(s);
    return rc;
}

/* ------------------------------------------------------------------ execution */

int ms_exec_direct(ms_conn *c, const void *sql, int n, ms_stmt **out) {
    SQLHSTMT h = SQL_NULL_HSTMT;
    SQLRETURN rc;
    SQLWCHAR *w = NULL;
    int units = 0;
    ms_stmt *s;
    if (!out) return SQL_ERROR;
    *out = NULL;
    if (!c || c->closed || !c->dbc) {
        ms_set_last_error("connection is closed", "", 0);
        return SQL_ERROR;
    }
    if (!sql || n <= 0 || n > MS_MAX_SQL) {
        ms_set_last_error("SQL text is empty or too large", "", 2);
        return SQL_ERROR;
    }
    rc = SQLAllocHandle(SQL_HANDLE_STMT, c->dbc, (SQLHANDLE *)&h);
    if (!SQL_SUCCEEDED(rc) || !h) {
        ms_set_last_error("statement allocation failed", "", 0);
        return SQL_ERROR;
    }
    s = stmt_new(h, c);
    if (!s) {
        SQLFreeHandle(SQL_HANDLE_STMT, h);
        ms_set_last_error("out of memory allocating statement state", "", 3);
        return SQL_ERROR;
    }
    *out = s;
    if (ms_utf8_to_utf16(sql, n, &w, &units) != 0) {
        state_set_error(s, "SQL text is not valid UTF-8", 2);
        return SQL_ERROR;
    }
    rc = SQLExecDirectW(h, w, units);
    free(w);
    if (SQL_SUCCEEDED(rc)) {
        if (cells_init(s) != 0) return SQL_ERROR;
        s->row_valid = 0;
    }
    return (int)rc;
}

int ms_prepare(ms_conn *c, const void *sql, int n, ms_stmt **out) {
    SQLHSTMT h = SQL_NULL_HSTMT;
    SQLRETURN rc;
    SQLWCHAR *w = NULL;
    int units = 0;
    ms_stmt *s;
    if (!out) return SQL_ERROR;
    *out = NULL;
    if (!c || c->closed || !c->dbc) {
        ms_set_last_error("connection is closed", "", 0);
        return SQL_ERROR;
    }
    if (!sql || n <= 0 || n > MS_MAX_SQL) {
        ms_set_last_error("SQL text is empty or too large", "", 2);
        return SQL_ERROR;
    }
    rc = SQLAllocHandle(SQL_HANDLE_STMT, c->dbc, (SQLHANDLE *)&h);
    if (!SQL_SUCCEEDED(rc) || !h) {
        ms_set_last_error("statement allocation failed", "", 0);
        return SQL_ERROR;
    }
    s = stmt_new(h, c);
    if (!s) {
        SQLFreeHandle(SQL_HANDLE_STMT, h);
        ms_set_last_error("out of memory allocating statement state", "", 3);
        return SQL_ERROR;
    }
    *out = s;
    if (ms_utf8_to_utf16(sql, n, &w, &units) != 0) {
        state_set_error(s, "SQL text is not valid UTF-8", 2);
        return SQL_ERROR;
    }
    rc = SQLPrepareW(h, w, units);
    free(w);
    return (int)rc;
}

int ms_execute(ms_stmt *s) {
    SQLRETURN rc;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    /* A prepared statement can be executed again only after its previous
     * cursor is closed; otherwise the driver returns 24000. */
    SQLFreeStmt(s->h, SQL_CLOSE);
    rc = SQLExecute(s->h);
    if (SQL_SUCCEEDED(rc)) {
        if (cells_init(s) != 0) return SQL_ERROR;
        s->row_valid = 0;
        state_clear_error(s);
    } else {
        cells_clear_row(s);
    }
    return (int)rc;
}

int ms_fetch(ms_stmt *s) {
    SQLRETURN rc;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    rc = SQLFetch(s->h);
    if (SQL_SUCCEEDED(rc)) {
        cells_clear_row(s);
        s->row_valid = 1;
        s->read_next = 0;
        state_clear_error(s);
    } else {
        cells_clear_row(s);
    }
    return (int)rc;
}

int ms_num_cols(ms_stmt *s) {
    SQLSMALLINT n = 0;
    SQLRETURN rc;
    if (!stmt_ok(s)) return -1;
    rc = SQLNumResultCols(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (int)n;
}

int ms_num_params(ms_stmt *s) {
    SQLSMALLINT n = 0;
    SQLRETURN rc;
    if (!stmt_ok(s)) return -1;
    rc = SQLNumParams(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (int)n;
}

long ms_row_count(ms_stmt *s) {
    SQLLEN n = 0;
    SQLRETURN rc;
    if (!stmt_ok(s)) return -1;
    rc = SQLRowCount(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (long)n;
}

/* --------------------------------------------------------------- column metadata */

static int describe(ms_stmt *s, int col, SQLWCHAR *name, SQLSMALLINT ncap,
                    SQLSMALLINT *nlen, SQLSMALLINT *type, SQLULEN *size,
                    SQLSMALLINT *digits, SQLSMALLINT *nullable) {
    SQLSMALLINT t = 0, d = 0, nl = 0, nu = 0;
    SQLULEN sz = 0;
    SQLRETURN rc;
    SQLWCHAR dummy[256];
    if (!s || col < 0) return -1;
    if (!name) {
        /* SQL Server identifiers are at most 128 units. A one-unit buffer made
         * SQLDescribeColW truncate the name and leave 01004 on the statement,
         * which then leaked into error_message() after a successful metadata
         * read. A 256-unit buffer avoids the truncation. */
        name = dummy;
        ncap = (SQLSMALLINT)(sizeof(dummy) / sizeof(dummy[0]));
    }
    rc = SQLDescribeColW(s->h, (SQLUSMALLINT)(col + 1), name, ncap, &nl, &t,
                         &sz, &d, &nu);
    if (!SQL_SUCCEEDED(rc)) return -1;
    if (nlen) *nlen = nl;
    if (type) *type = t;
    if (size) *size = sz;
    if (digits) *digits = d;
    if (nullable) *nullable = nu;
    return 0;
}

const char *ms_col_name(ms_stmt *s, int col) {
    SQLWCHAR stack[256];
    SQLWCHAR *w = stack;
    SQLWCHAR *heap = NULL;
    SQLSMALLINT nl = 0;
    char *utf8 = NULL;
    int bytes = 0;
    char *out;
    if (!stmt_ok(s)) return "";
    if (describe(s, col, stack, (SQLSMALLINT)(sizeof(stack) / sizeof(stack[0])),
                 &nl, NULL, NULL, NULL, NULL) != 0) {
        return "";
    }
    if (nl >= (SQLSMALLINT)(sizeof(stack) / sizeof(stack[0]))) {
        heap = (SQLWCHAR *)malloc(((size_t)nl + 1) * sizeof(SQLWCHAR));
        if (!heap) return "";
        if (describe(s, col, heap, (SQLSMALLINT)(nl + 1), &nl, NULL, NULL, NULL,
                     NULL) != 0) {
            free(heap);
            return "";
        }
        w = heap;
    }
    if (ms_utf16_to_utf8(w, (int)nl, &utf8, &bytes) != 0) {
        if (heap) free(heap);
        return "";
    }
    if (heap) free(heap);
    out = ms_text_reserve((size_t)bytes + 1);
    if (!out) {
        free(utf8);
        return "";
    }
    memcpy(out, utf8, (size_t)bytes + 1);
    free(utf8);
    return out;
}

int ms_col_type(ms_stmt *s, int col) {
    SQLSMALLINT t = -1;
    if (!stmt_ok(s)) return -1;
    if (describe(s, col, NULL, 0, NULL, &t, NULL, NULL, NULL) != 0) return -1;
    return (int)t;
}

long ms_col_size(ms_stmt *s, int col) {
    SQLULEN sz = 0;
    if (!stmt_ok(s)) return -1;
    if (describe(s, col, NULL, 0, NULL, NULL, &sz, NULL, NULL) != 0) return -1;
    return (long)sz;
}

int ms_col_digits(ms_stmt *s, int col) {
    SQLSMALLINT d = -1;
    if (!stmt_ok(s)) return -1;
    if (describe(s, col, NULL, 0, NULL, NULL, NULL, &d, NULL) != 0) return -1;
    return (int)d;
}

int ms_col_nullable(ms_stmt *s, int col) {
    SQLSMALLINT n = 0;
    if (!stmt_ok(s)) return -1;
    if (describe(s, col, NULL, 0, NULL, NULL, NULL, NULL, &n) != 0) return -1;
    return (int)n;
}

/* ------------------------------------------------------------------ cell reads */

static int column_binary_mode(ms_stmt *s, int col) {
    SQLSMALLINT t = 0;
    if (describe(s, col, NULL, 0, NULL, &t, NULL, NULL, NULL) != 0) return 0;
    return (t == SQL_BINARY || t == SQL_VARBINARY || t == SQL_LONGVARBINARY)
               ? 1 : 0;
}

static int cell_grow(ms_cell *c) {
    unsigned char *p;
    int cap;
    if (c->cap >= MS_MAX_VALUE + 4) return -1;
    cap = c->cap ? c->cap * 2 : 4096;
    if (cap > MS_MAX_VALUE + 4) cap = MS_MAX_VALUE + 4;
    p = (unsigned char *)realloc(c->buf, (size_t)cap);
    if (!p) return -1;
    c->buf = p;
    c->cap = cap;
    return 0;
}

/* Consume the rest of a column with a scratch buffer. Used when a skipped
 * column exceeded the 16 MiB cell cap: the requested later column must remain
 * readable, so the over-cap value is drained and discarded. */
static int discard_column(ms_stmt *s, int col, int mode) {
    unsigned char scratch[4096];
    for (;;) {
        SQLLEN ind = 0;
        SQLRETURN rc;
        if (!mode) memset(scratch, 0xFF, sizeof scratch);
        rc = SQLGetData(s->h, (SQLUSMALLINT)(col + 1),
                        mode ? SQL_C_BINARY : SQL_C_CHAR,
                        scratch, (SQLLEN)sizeof scratch, &ind);
        if (rc == SQL_SUCCESS || rc == SQL_NO_DATA) return 0;
        if (!SQL_SUCCEEDED(rc)) return -1;
        /* No-progress guard: a conforming driver writes data on a truncated
         * chunk, but a nonconforming one could return WITH_INFO with zero
         * bytes forever. For text the driver NUL-terminates the chunk, so the
         * 0xFF prefill locates the terminator: pos 0 means nothing was written
         * and pos 1 with a NUL first byte means only the terminator (a single
         * embedded-NUL byte is pos 2 and still progress). For binary the 18
         * driver reports the remaining length, so a zero indicator is no
         * progress; SQL_NO_TOTAL is treated as a filled buffer. Return a clean
         * error instead of spinning. */
        if (mode) {
            if (ind == 0) return -1;
        } else {
            int pos = 0;
            while (pos < (int)sizeof scratch && scratch[pos] != 0xFF) pos++;
            if (pos == 0) return -1;
            if (pos == 1 && scratch[0] == 0) return -1;
        }
    }
}

/* Read one column into its cell. The caller has checked stmt_ok, row_valid and
 * the column bounds. */
static int read_cell_at(ms_stmt *s, int col, int mode) {
    ms_cell *c = &s->cells[col];
    if (c->loaded && c->mode == mode) return c->status;
    c->loaded = 0;
    c->len = 0;
    c->status = 0;
    c->mode = mode;
    if (!c->buf && cell_grow(c) != 0) {
        state_set_error(s, "out of memory reading the column", 3);
        c->status = -1;
        c->loaded = 1;
        return -1;
    }
    for (;;) {
        int remain = c->cap - c->len;
        SQLLEN ind = SQL_NO_TOTAL;
        SQLRETURN rc;
        int chunk;
        if (remain < 2) {
            if (cell_grow(c) != 0) {
                state_set_error(s, "value exceeds the 16 MiB cap", 2);
                c->status = -2;
                break;
            }
            continue;
        }
        if (!mode) memset(c->buf + c->len, 0xFF, (size_t)remain);
        rc = SQLGetData(s->h, (SQLUSMALLINT)(col + 1),
                        mode ? SQL_C_BINARY : SQL_C_CHAR,
                        c->buf + c->len, (SQLLEN)remain, &ind);
        if (rc == SQL_NO_DATA) {
            c->status = -1;
            state_set_error(s, "column value was already consumed", 2);
            break;
        }
        if (!SQL_SUCCEEDED(rc)) {
            c->status = -1;
            break;
        }
        if (ind == SQL_NULL_DATA) {
            c->status = 1;
            c->len = 0;
            break;
        }
        if (rc == SQL_SUCCESS) {
            if (ind < 0 || ind > (SQLLEN)(mode ? remain : remain - 1)) {
                /* Fall back to the terminator scan rather than trusting an
                 * implausible indicator. */
                int pos = 0;
                while (pos < remain && c->buf[c->len + pos] != 0xFF) pos++;
                chunk = pos > 0 ? pos - 1 : 0;
            } else {
                chunk = (int)ind;
            }
            c->len += chunk;
            if (c->len > MS_MAX_VALUE) {
                state_set_error(s, "value exceeds the 16 MiB cap", 2);
                c->status = -2;
                break;
            }
            c->status = 0;
            break;
        }
        /* SQL_SUCCESS_WITH_INFO: a truncated chunk. */
        if (mode) {
            /* The binary indicator is the remaining total; a zero or negative
             * value on a truncation means the buffer was filled. */
            chunk = (ind > 0 && ind < (SQLLEN)remain) ? (int)ind : remain;
        } else {
            int pos = 0;
            while (pos < remain && c->buf[c->len + pos] != 0xFF) pos++;
            /* pos == 0 means the driver wrote no data (a character did not
             * fit); the guard below grows the buffer and retries. */
            chunk = pos > 0 ? pos - 1 : 0;
        }
        if (chunk <= 0) {
            /* Some drivers refuse to split a multi-byte character at a small
             * buffer boundary. Grow and retry; at the cap this is an error. */
            if (cell_grow(c) != 0) {
                state_set_error(s, "value exceeds the 16 MiB cap", 2);
                c->status = -2;
                break;
            }
            continue;
        }
        c->len += chunk;
        if (c->len > MS_MAX_VALUE) {
            state_set_error(s, "value exceeds the 16 MiB cap", 2);
            c->status = -2;
            break;
        }
        if (c->len + 2 > c->cap && cell_grow(c) != 0) {
            state_set_error(s, "value exceeds the 16 MiB cap", 2);
            c->status = -2;
            break;
        }
    }
    c->loaded = 1;
    if (c->status == 0) state_clear_error(s);
    return c->status;
}

int ms_read_cell(ms_stmt *s, int col, int binary) {
    int mode = binary ? 1 : 0;
    int k;
    int r;
    if (!stmt_ok(s)) return -1;
    if (!s->row_valid) {
        state_set_error(s, "no current row", 2);
        return -1;
    }
    if (col < 0 || col >= s->ncells) {
        state_set_error(s, "column out of range", 2);
        return -1;
    }
    if (col < s->read_next) {
        ms_cell *c = &s->cells[col];
        if (c->loaded && c->mode == mode) {
            if (c->status == -2) {
                state_set_error(s, "value exceeds the 16 MiB cap", 2);
            } else if (c->status == -1) {
                state_set_error(s, "column value could not be read", 2);
            }
            return c->status;
        }
        state_set_error(s, "columns must be read in ascending order", 2);
        return -1;
    }
    /* SQLGetData consumes columns in ascending order. Read and cache the
     * skipped ones so a later read of an earlier column is served from the
     * cache rather than failing with 07009. */
    for (k = s->read_next; k < col; k++) {
        int sk_mode = column_binary_mode(s, k);
        ms_cell *sk;
        r = read_cell_at(s, k, sk_mode);
        if (r == -2) {
            /* The skipped value was too large to cache. Drain it so the
             * requested column is still readable, and remember that reading
             * this column itself is the cap error. */
            if (discard_column(s, k, sk_mode) != 0) {
                state_set_error(s, "skipped column could not be consumed", 2);
                return -1;
            }
            sk = &s->cells[k];
            cell_free(sk);
            sk->status = -2;
            sk->mode = sk_mode;
            sk->loaded = 1;
            continue;
        }
        if (r < 0) return r;
    }
    r = read_cell_at(s, col, mode);
    if (r >= 0) s->read_next = col + 1;
    return r;
}

const void *ms_cell_value(ms_stmt *s, int col, int binary) {
    ms_cell *c;
    if (!s || !s->row_valid || col < 0 || col >= s->ncells) return NULL;
    c = &s->cells[col];
    if (!c->loaded || c->mode != (binary ? 1 : 0) || c->status != 0) return NULL;
    return c->buf;
}

int ms_cell_len(ms_stmt *s, int col, int binary) {
    ms_cell *c;
    if (!s || !s->row_valid || col < 0 || col >= s->ncells) return 0;
    c = &s->cells[col];
    if (!c->loaded || c->mode != (binary ? 1 : 0) || c->status != 0) return 0;
    return c->len;
}

/* ------------------------------------------------------------------ diagnostics */

const char *ms_stmt_diag(ms_stmt *s) {
    if (!s) return "";
    if (s->synthetic) return s->synthetic;
    if (s->closed || s->h == SQL_NULL_HSTMT) {
        state_set_error(s, "statement's connection is closed", 2);
        return s->synthetic ? s->synthetic : "";
    }
    return ms_handle_diag(s->h, SQL_HANDLE_STMT);
}

const char *ms_stmt_sqlstate(ms_stmt *s) {
    if (!s) return "";
    if (s->closed || s->h == SQL_NULL_HSTMT) return "08003";
    if (s->synthetic) return "HY000";
    return ms_handle_sqlstate(s->h, SQL_HANDLE_STMT);
}

int ms_stmt_error_code(ms_stmt *s) {
    if (!s) return 0;
    if (s->closed || s->h == SQL_NULL_HSTMT) return 2;
    if (s->synthetic) return s->synthetic_code;
    return ms_handle_error_code(s->h, SQL_HANDLE_STMT);
}

/* ---------------------------------------------------------------- parameters */

static int bind_push(ms_stmt *s, void *ptr, SQLLEN *ind) {
    ms_bind_slot *b = (ms_bind_slot *)malloc(sizeof *b);
    if (!b) return -1;
    b->ptr = ptr;
    b->ind = ind;
    b->next = s->binds;
    s->binds = b;
    return 0;
}

static int bind_error(ms_stmt *s, const char *msg, int code) {
    state_set_error(s, msg, code);
    return SQL_ERROR;
}

int ms_bind_null(ms_stmt *s, int index) {
    SQLLEN *ind;
    SQLRETURN rc;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    if (index < 1 || index > MS_MAX_PARAMS) {
        return bind_error(s, "parameter index out of range", 2);
    }
    ind = (SQLLEN *)malloc(sizeof *ind);
    if (!ind) return bind_error(s, "out of memory binding a parameter", 3);
    *ind = SQL_NULL_DATA;
    if (bind_push(s, NULL, ind) != 0) {
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT, SQL_C_CHAR,
                          SQL_WVARCHAR, 0, 0, NULL, 0, ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_int(ms_stmt *s, int index, long value) {
    long *v;
    SQLLEN *ind;
    SQLRETURN rc;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    if (index < 1 || index > MS_MAX_PARAMS) {
        return bind_error(s, "parameter index out of range", 2);
    }
    v = (long *)malloc(sizeof *v);
    ind = (SQLLEN *)malloc(sizeof *ind);
    if (!v || !ind) {
        free(v);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    *v = value;
    *ind = (SQLLEN)sizeof *v;
    if (bind_push(s, v, ind) != 0) {
        free(v);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT,
                          SQL_C_SBIGINT, SQL_BIGINT, 19, 0, v, 8, ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_text(ms_stmt *s, int index, const void *utf8, int n) {
    char *copy;
    SQLLEN *ind;
    SQLRETURN rc;
    SQLULEN colsize;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    if (index < 1 || index > MS_MAX_PARAMS) {
        return bind_error(s, "parameter index out of range", 2);
    }
    if (n < 0 || n > MS_MAX_VALUE || (n > 0 && !utf8)) {
        return bind_error(s, "text parameter is too large", 2);
    }
    if (!ms_utf8_check(utf8, n)) {
        return bind_error(s, "text parameter is not valid UTF-8", 2);
    }
    copy = (char *)malloc((size_t)n + 1);
    ind = (SQLLEN *)malloc(sizeof *ind);
    if (!copy || !ind) {
        free(copy);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    if (n) memcpy(copy, utf8, (size_t)n);
    copy[n] = 0;
    *ind = (SQLLEN)n;
    if (bind_push(s, copy, ind) != 0) {
        free(copy);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    /* The driver rejects a declared precision above 4000 for a wide varchar
     * parameter; 0 means "unspecified" and is required for the 16 MiB range. */
    colsize = (n > 4000) ? 0 : (SQLULEN)(n > 0 ? n : 1);
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT, SQL_C_CHAR,
                          SQL_WVARCHAR, colsize, 0, copy, (SQLLEN)(n + 1), ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_binary(ms_stmt *s, int index, const void *data, int n) {
    unsigned char *copy;
    SQLLEN *ind;
    SQLRETURN rc;
    SQLULEN colsize;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    if (index < 1 || index > MS_MAX_PARAMS) {
        return bind_error(s, "parameter index out of range", 2);
    }
    if (n < 0 || n > MS_MAX_VALUE || (n > 0 && !data)) {
        return bind_error(s, "binary parameter is too large", 2);
    }
    copy = (unsigned char *)malloc(n > 0 ? (size_t)n : 1);
    ind = (SQLLEN *)malloc(sizeof *ind);
    if (!copy || !ind) {
        free(copy);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    if (n) memcpy(copy, data, (size_t)n);
    *ind = (SQLLEN)n;
    if (bind_push(s, copy, ind) != 0) {
        free(copy);
        free(ind);
        return bind_error(s, "out of memory binding a parameter", 3);
    }
    /* Same precision rule for binary: above 8000 bytes use "unspecified". */
    colsize = (n > 8000) ? 0 : (SQLULEN)(n > 0 ? n : 1);
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT, SQL_C_BINARY,
                          SQL_VARBINARY, colsize, 0, copy, (SQLLEN)n, ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_clear(ms_stmt *s) {
    SQLRETURN rc;
    if (!stmt_ok(s)) return SQL_ERROR;
    state_clear_error(s);
    rc = SQLFreeStmt(s->h, SQL_RESET_PARAMS);
    binds_free(s);
    if (SQL_SUCCEEDED(rc)) state_clear_error(s);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}
