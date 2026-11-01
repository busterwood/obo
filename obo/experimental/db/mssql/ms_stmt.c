/* Statement shims for the mssql binding.
 *
 * ODBC statements are opaque SQLHSTMT handles, so this file keeps a small
 * process-wide side table keyed by the handle. The table holds the per-column
 * cell cache (one row at a time, reused across fetches) and the parameter
 * slots that must stay alive between SQLBindParameter and SQLExecute. The
 * binding is single-threaded (cinterop design C10), so no locking is used.
 *
 * Cell reads use SQLGetData. The Phase 1 probe measured that:
 *  - SQL_C_BINARY reports the remaining byte count in the indicator;
 *  - SQL_C_CHAR reports SQL_NO_TOTAL on a truncated chunk, so the chunk length
 *    is recovered by pre-filling the buffer with 0xFF (never valid UTF-8) and
 *    finding the driver's NUL terminator. This also keeps embedded NULs exact;
 *  - a C_CHAR buffer of one byte makes no progress, so the buffer is always at
 *    least two bytes and grows by doubling up to MS_MAX_VALUE.
 */
#include <stdlib.h>
#include <string.h>
#include "ms_internal.h"

typedef struct ms_bind_slot {
    void *ptr;
    SQLLEN *ind;
    struct ms_bind_slot *next;
} ms_bind_slot;

typedef struct ms_cell {
    unsigned char *buf;
    int cap;
    int len;
    int status;   /* 0 value, 1 SQL NULL, -1 driver error, -2 too large */
    int mode;     /* 0 text (UTF-8), 1 binary (raw bytes) */
    int loaded;   /* valid for the current row and column */
} ms_cell;

typedef struct ms_state {
    SQLHSTMT h;
    ms_cell *cells;
    int ncells;
    ms_bind_slot *binds;
    char *synthetic;
    int synthetic_code;
    struct ms_state *next;
} ms_state;

static ms_state *g_states;

static ms_state *state_get(SQLHSTMT h) {
    ms_state *s;
    for (s = g_states; s; s = s->next) {
        if (s->h == h) return s;
    }
    return NULL;
}

static void state_set_error(ms_state *s, const char *msg, int code) {
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

static ms_state *state_new(SQLHSTMT h) {
    ms_state *s = (ms_state *)calloc(1, sizeof *s);
    if (!s) return NULL;
    s->h = h;
    s->next = g_states;
    g_states = s;
    return s;
}

static void cell_free(ms_cell *c) {
    free(c->buf);
    c->buf = NULL;
    c->cap = 0;
    c->len = 0;
    c->status = 0;
    c->loaded = 0;
}

static void state_free(ms_state *s) {
    ms_state **pp;
    int i;
    if (!s) return;
    for (i = 0; i < s->ncells; i++) cell_free(&s->cells[i]);
    free(s->cells);
    while (s->binds) {
        ms_bind_slot *b = s->binds;
        s->binds = b->next;
        free(b->ptr);
        free(b->ind);
        free(b);
    }
    free(s->synthetic);
    for (pp = &g_states; *pp; pp = &(*pp)->next) {
        if (*pp == s) {
            *pp = s->next;
            break;
        }
    }
    free(s);
}

static int cells_reset(ms_state *s, int n) {
    int i;
    for (i = 0; i < s->ncells; i++) cell_free(&s->cells[i]);
    free(s->cells);
    s->cells = NULL;
    s->ncells = 0;
    if (n <= 0) return 0;
    s->cells = (ms_cell *)calloc((size_t)n, sizeof(ms_cell));
    if (!s->cells) {
        state_set_error(s, "out of memory allocating the column cache", 3);
        return -1;
    }
    s->ncells = n;
    return 0;
}

static int cells_init(ms_state *s) {
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

static void cells_clear_row(ms_state *s) {
    int i;
    for (i = 0; i < s->ncells; i++) {
        s->cells[i].loaded = 0;
        s->cells[i].len = 0;
        s->cells[i].status = 0;
    }
}

/* ------------------------------------------------------------------ execution */

static int alloc_stmt(SQLHSTMT h, ms_stmt **out) {
    ms_state *s = state_new(h);
    if (!s) {
        ms_set_last_error("out of memory allocating statement state", "", 3);
        SQLFreeHandle(SQL_HANDLE_STMT, h);
        return SQL_ERROR;
    }
    *out = (ms_stmt *)h;
    return SQL_SUCCESS;
}

int ms_exec_direct(ms_conn *c, const void *sql, int n, ms_stmt **out) {
    SQLHSTMT h = SQL_NULL_HSTMT;
    SQLRETURN rc;
    SQLWCHAR *w = NULL;
    int units = 0;
    ms_state *s;
    if (!out) return SQL_ERROR;
    *out = NULL;
    if (!c) {
        ms_set_last_error("connection is closed", "", 0);
        return SQL_ERROR;
    }
    if (!sql || n <= 0 || n > MS_MAX_SQL) {
        ms_set_last_error("SQL text is empty or too large", "", 2);
        return SQL_ERROR;
    }
    rc = SQLAllocHandle(SQL_HANDLE_STMT, (SQLHDBC)c, (SQLHANDLE *)&h);
    if (!SQL_SUCCEEDED(rc) || !h) {
        ms_set_last_error("statement allocation failed", "", 0);
        return SQL_ERROR;
    }
    if (alloc_stmt(h, out) != SQL_SUCCESS) return SQL_ERROR;
    s = state_get(h);
    if (ms_utf8_to_utf16(sql, n, &w, &units) != 0) {
        state_set_error(s, "SQL text is not valid UTF-8", 2);
        return SQL_ERROR;
    }
    rc = SQLExecDirectW(h, w, units);
    free(w);
    if (SQL_SUCCEEDED(rc) && cells_init(s) != 0) return SQL_ERROR;
    return (int)rc;
}

int ms_prepare(ms_conn *c, const void *sql, int n, ms_stmt **out) {
    SQLHSTMT h = SQL_NULL_HSTMT;
    SQLRETURN rc;
    SQLWCHAR *w = NULL;
    int units = 0;
    ms_state *s;
    if (!out) return SQL_ERROR;
    *out = NULL;
    if (!c) {
        ms_set_last_error("connection is closed", "", 0);
        return SQL_ERROR;
    }
    if (!sql || n <= 0 || n > MS_MAX_SQL) {
        ms_set_last_error("SQL text is empty or too large", "", 2);
        return SQL_ERROR;
    }
    rc = SQLAllocHandle(SQL_HANDLE_STMT, (SQLHDBC)c, (SQLHANDLE *)&h);
    if (!SQL_SUCCEEDED(rc) || !h) {
        ms_set_last_error("statement allocation failed", "", 0);
        return SQL_ERROR;
    }
    if (alloc_stmt(h, out) != SQL_SUCCESS) return SQL_ERROR;
    s = state_get(h);
    if (ms_utf8_to_utf16(sql, n, &w, &units) != 0) {
        state_set_error(s, "SQL text is not valid UTF-8", 2);
        return SQL_ERROR;
    }
    rc = SQLPrepareW(h, w, units);
    free(w);
    return (int)rc;
}

int ms_execute(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
    rc = SQLExecute(s->h);
    if (SQL_SUCCEEDED(rc)) {
        if (cells_init(s) != 0) return SQL_ERROR;
    }
    return (int)rc;
}

int ms_fetch(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
    rc = SQLFetch(s->h);
    if (SQL_SUCCEEDED(rc)) cells_clear_row(s);
    return (int)rc;
}

int ms_num_cols(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLSMALLINT n = 0;
    SQLRETURN rc;
    if (!s) return -1;
    rc = SQLNumResultCols(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (int)n;
}

int ms_num_params(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLSMALLINT n = 0;
    SQLRETURN rc;
    if (!s) return -1;
    rc = SQLNumParams(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (int)n;
}

long ms_row_count(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLLEN n = 0;
    SQLRETURN rc;
    if (!s) return -1;
    rc = SQLRowCount(s->h, &n);
    if (!SQL_SUCCEEDED(rc)) return -1;
    return (long)n;
}

/* --------------------------------------------------------------- column metadata */

static int describe(ms_state *s, int col, SQLWCHAR *name, SQLSMALLINT ncap,
                    SQLSMALLINT *nlen, SQLSMALLINT *type, SQLULEN *size,
                    SQLSMALLINT *digits, SQLSMALLINT *nullable) {
    SQLSMALLINT t = 0, d = 0, nl = 0, nu = 0;
    SQLULEN sz = 0;
    SQLRETURN rc;
    SQLWCHAR dummy[1];
    if (!s || col < 0) return -1;
    if (!name) {
        name = dummy;
        ncap = 1;
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

const char *ms_col_name(ms_stmt *ss, int col) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLWCHAR stack[256];
    SQLWCHAR *w = stack;
    SQLWCHAR *heap = NULL;
    SQLSMALLINT nl = 0;
    char *utf8 = NULL;
    int bytes = 0;
    char *out;
    if (!s) return "";
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

int ms_col_type(ms_stmt *ss, int col) {
    SQLSMALLINT t = -1;
    if (describe(state_get((SQLHSTMT)ss), col, NULL, 0, NULL, &t, NULL, NULL,
                 NULL) != 0) return -1;
    return (int)t;
}

long ms_col_size(ms_stmt *ss, int col) {
    SQLULEN sz = 0;
    if (describe(state_get((SQLHSTMT)ss), col, NULL, 0, NULL, NULL, &sz, NULL,
                 NULL) != 0) return -1;
    return (long)sz;
}

int ms_col_digits(ms_stmt *ss, int col) {
    SQLSMALLINT d = -1;
    if (describe(state_get((SQLHSTMT)ss), col, NULL, 0, NULL, NULL, NULL, &d,
                 NULL) != 0) return -1;
    return (int)d;
}

int ms_col_nullable(ms_stmt *ss, int col) {
    SQLSMALLINT n = 0;
    if (describe(state_get((SQLHSTMT)ss), col, NULL, 0, NULL, NULL, NULL, NULL,
                 &n) != 0) return -1;
    return (int)n;
}

/* ---------------------------------------------------------------- cell reads */

static int cell_grow(ms_cell *c) {
    unsigned char *p;
    int cap;
    if (c->cap >= MS_MAX_VALUE + 1) return -1;
    cap = c->cap ? c->cap * 2 : 4096;
    if (cap > MS_MAX_VALUE + 1) cap = MS_MAX_VALUE + 1;
    p = (unsigned char *)realloc(c->buf, (size_t)cap);
    if (!p) return -1;
    c->buf = p;
    c->cap = cap;
    return 0;
}

int ms_read_cell(ms_stmt *ss, int col, int binary) {
    ms_state *s = state_get((SQLHSTMT)ss);
    ms_cell *c;
    int mode = binary ? 1 : 0;
    if (!s || col < 0 || col >= s->ncells) return -1;
    c = &s->cells[col];
    if (c->loaded && c->mode == mode) return c->status;
    c->loaded = 0;
    c->len = 0;
    c->status = 0;
    c->mode = mode;
    if (!c->buf && cell_grow(c) != 0) {
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
            c->status = 0;
            break;
        }
        /* SQL_SUCCESS_WITH_INFO: a truncated chunk. */
        if (mode) {
            chunk = (ind >= 0 && ind < (SQLLEN)remain) ? (int)ind : remain;
        } else {
            int pos = 0;
            while (pos < remain && c->buf[c->len + pos] != 0xFF) pos++;
            chunk = pos > 0 ? pos - 1 : remain - 1;
        }
        if (chunk <= 0) {
            /* Some drivers refuse to split a multi-byte character at a small
             * buffer boundary. Grow and retry; at the cap this is an error. */
            if (cell_grow(c) != 0) {
                c->status = -2;
                break;
            }
            continue;
        }
        c->len += chunk;
        if (c->len >= MS_MAX_VALUE) {
            c->status = -2;
            break;
        }
        if (c->len + 2 > c->cap && cell_grow(c) != 0) {
            c->status = -1;
            break;
        }
    }
    c->loaded = 1;
    return c->status;
}

const void *ms_cell_value(ms_stmt *ss, int col, int binary) {
    ms_state *s = state_get((SQLHSTMT)ss);
    ms_cell *c;
    if (!s || col < 0 || col >= s->ncells) return NULL;
    c = &s->cells[col];
    if (!c->loaded || c->mode != (binary ? 1 : 0) || c->status != 0) return NULL;
    return c->buf;
}

int ms_cell_len(ms_stmt *ss, int col, int binary) {
    ms_state *s = state_get((SQLHSTMT)ss);
    ms_cell *c;
    if (!s || col < 0 || col >= s->ncells) return 0;
    c = &s->cells[col];
    if (!c->loaded || c->mode != (binary ? 1 : 0) || c->status != 0) return 0;
    return c->len;
}

/* ------------------------------------------------------------------ diagnostics */

const char *ms_stmt_diag(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    if (!s) return "";
    if (s->synthetic) return s->synthetic;
    return ms_handle_diag(s->h, SQL_HANDLE_STMT);
}

const char *ms_stmt_sqlstate(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    if (!s) return "";
    if (s->synthetic) return "HY000";
    return ms_handle_sqlstate(s->h, SQL_HANDLE_STMT);
}

int ms_stmt_error_code(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    if (!s) return 0;
    if (s->synthetic) return s->synthetic_code;
    return ms_handle_error_code(s->h, SQL_HANDLE_STMT);
}

/* ---------------------------------------------------------------- parameters */

static int bind_push(ms_state *s, void *ptr, SQLLEN *ind) {
    ms_bind_slot *b = (ms_bind_slot *)malloc(sizeof *b);
    if (!b) return -1;
    b->ptr = ptr;
    b->ind = ind;
    b->next = s->binds;
    s->binds = b;
    return 0;
}

static int bind_error(ms_state *s, const char *msg, int code) {
    state_set_error(s, msg, code);
    return SQL_ERROR;
}

int ms_bind_null(ms_stmt *ss, int index) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLLEN *ind;
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
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

int ms_bind_int(ms_stmt *ss, int index, long value) {
    ms_state *s = state_get((SQLHSTMT)ss);
    long *v;
    SQLLEN *ind;
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
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

int ms_bind_text(ms_stmt *ss, int index, const void *utf8, int n) {
    ms_state *s = state_get((SQLHSTMT)ss);
    char *copy;
    SQLLEN *ind;
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
    if (index < 1 || index > MS_MAX_PARAMS) {
        return bind_error(s, "parameter index out of range", 2);
    }
    if (n < 0 || n > MS_MAX_VALUE || (n > 0 && !utf8)) {
        return bind_error(s, "text parameter is too large", 2);
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
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT, SQL_C_CHAR,
                          SQL_WVARCHAR, (SQLULEN)(n > 0 ? n : 1), 0, copy,
                          (SQLLEN)(n + 1), ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_binary(ms_stmt *ss, int index, const void *data, int n) {
    ms_state *s = state_get((SQLHSTMT)ss);
    unsigned char *copy;
    SQLLEN *ind;
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
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
    rc = SQLBindParameter(s->h, (SQLUSMALLINT)index, SQL_PARAM_INPUT, SQL_C_BINARY,
                          SQL_VARBINARY, (SQLULEN)(n > 0 ? n : 1), 0, copy,
                          (SQLLEN)n, ind);
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

int ms_bind_clear(ms_stmt *ss) {
    ms_state *s = state_get((SQLHSTMT)ss);
    SQLRETURN rc;
    if (!s) return SQL_ERROR;
    rc = SQLFreeStmt(s->h, SQL_RESET_PARAMS);
    while (s->binds) {
        ms_bind_slot *b = s->binds;
        s->binds = b->next;
        free(b->ptr);
        free(b->ind);
        free(b);
    }
    return SQL_SUCCEEDED(rc) ? 0 : (int)rc;
}

void ms_close_stmt(ms_stmt *ss) {
    SQLHSTMT h = (SQLHSTMT)ss;
    ms_state *s;
    if (!h) return;
    s = state_get(h);
    if (s) state_free(s);
    /* Free the cursor first: some drivers refuse SQLFreeHandle while a cursor
     * is open. The close is explicit; a failure here is not reported. */
    SQLFreeStmt(h, SQL_CLOSE);
    SQLFreeHandle(SQL_HANDLE_STMT, h);
}
