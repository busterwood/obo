/* Connection shims: one process-wide ODBC environment, a UTF-8 DSN converted to
 * UTF-16, and the W connect entry point.
 *
 * The environment handle is allocated lazily and never freed: the driver keeps
 * process-wide state behind it, and the binding has no shutdown hook that could
 * safely order a free after every connection. This matches the one-process,
 * single-threaded contract of the cinterop seam (design C10).
 *
 * The foreign word points at the ms_conn state, not at the raw SQLHDBC. That
 * lets close mark every child statement dead and free its HSTMT before the DBC
 * is disconnected, so a Rows that outlives its Connection cannot reach a stale
 * driver handle.
 */
#include <stdlib.h>
#include <string.h>
#include "ms_internal.h"

static SQLHENV g_env;
static int g_env_tried;

static int ensure_env(void) {
    SQLRETURN rc;
    if (g_env_tried) return g_env ? 0 : -1;
    g_env_tried = 1;
    rc = SQLAllocHandle(SQL_HANDLE_ENV, SQL_NULL_HANDLE, (SQLHANDLE *)&g_env);
    if (!SQL_SUCCEEDED(rc) || !g_env) {
        g_env = SQL_NULL_HANDLE;
        ms_set_last_error("ODBC environment allocation failed", "", 0);
        return -1;
    }
    rc = SQLSetEnvAttr(g_env, SQL_ATTR_ODBC_VERSION, (SQLPOINTER)SQL_OV_ODBC3, 0);
    if (!SQL_SUCCEEDED(rc)) {
        ms_set_last_error("ODBC environment could not select ODBC 3 behaviour", "", 0);
        return -1;
    }
    return 0;
}

ms_conn *ms_open(const void *dsn, int n) {
    SQLWCHAR *wdsn = NULL;
    int units = 0;
    SQLHDBC dbc = SQL_NULL_HDBC;
    SQLRETURN rc;

    if (!dsn || n <= 0 || n > MS_MAX_VALUE) {
        ms_set_last_error("connection string is empty or too large", "", 0);
        return NULL;
    }
    if (ensure_env() != 0) return NULL;
    if (ms_utf8_to_utf16(dsn, n, &wdsn, &units) != 0) {
        ms_set_last_error("connection string is not valid UTF-8", "", 0);
        return NULL;
    }
    rc = SQLAllocHandle(SQL_HANDLE_DBC, g_env, (SQLHANDLE *)&dbc);
    if (!SQL_SUCCEEDED(rc) || !dbc) {
        free(wdsn);
        ms_set_last_error("ODBC connection allocation failed", "", 0);
        return NULL;
    }
    rc = SQLDriverConnectW(dbc, NULL, wdsn, SQL_NTS, NULL, 0, NULL,
                           SQL_DRIVER_NOPROMPT);
    free(wdsn);
    if (SQL_SUCCEEDED(rc)) {
        /* SQL_SUCCESS_WITH_INFO here means informational records such as
         * "Changed database context"; the connection is usable. They are not
         * an error and are not drained (reading a diagnostic does not clear
         * it); conn_diag exposes them if a caller asks. */
        ms_conn *c = (ms_conn *)calloc(1, sizeof *c);
        if (!c) {
            SQLDisconnect(dbc);
            SQLFreeHandle(SQL_HANDLE_DBC, dbc);
            ms_set_last_error("out of memory allocating connection state", "", 3);
            return NULL;
        }
        c->dbc = dbc;
        return c;
    }
    {
        char *buf = ms_text_reserve(1024);
        char state[6];
        int code = 0;
        if (buf) {
            int cap = 1024;
            for (;;) {
                int used;
                if (cap > MS_MAX_VALUE + 1) cap = MS_MAX_VALUE + 1;
                buf = ms_text_reserve((size_t)cap);
                if (!buf) break;
                used = ms_diag(dbc, SQL_HANDLE_DBC, buf, cap);
                if (used < cap - 1 || cap == MS_MAX_VALUE + 1) break;
                cap *= 2;
            }
        }
        strcpy(state, ms_handle_sqlstate(dbc, SQL_HANDLE_DBC));
        code = ms_handle_error_code(dbc, SQL_HANDLE_DBC);
        if (buf && buf[0]) {
            ms_set_last_error(buf, state, code);
        } else {
            ms_set_last_error("ODBC connection failed without a diagnostic",
                              state, code);
        }
    }
    SQLFreeHandle(SQL_HANDLE_DBC, dbc);
    return NULL;
}

const char *ms_open_error(void) {
    return ms_last_error();
}

const char *ms_open_sqlstate(void) {
    return ms_last_error_state();
}

int ms_open_error_code(void) {
    return ms_last_error_code();
}

int ms_close_conn(ms_conn *c) {
    int rc = 0;
    if (!c) return 0;
    if (c->closed) return 0;
    c->closed = 1;
    {
        ms_stmt *s = c->children;
        c->children = NULL;
        while (s) {
            ms_stmt *next = s->next_sibling;
            s->next_sibling = NULL;
            /* Frees the HSTMT, marks the state closed and leaves the clean
             * diagnostic that a later stmt shim returns. A driver failure is
             * reported by Connection.close() through the release code. */
            {
                int cr = ms_stmt_mark_closed(s);
                if (cr != 0 && rc == 0) rc = cr;
            }
            s = next;
        }
    }
    if (c->dbc) {
        SQLRETURN dr = SQLDisconnect(c->dbc);
        SQLRETURN fr;
        if (!SQL_SUCCEEDED(dr) && rc == 0) rc = (int)dr;
        fr = SQLFreeHandle(SQL_HANDLE_DBC, c->dbc);
        if (!SQL_SUCCEEDED(fr) && rc == 0) rc = (int)fr;
        c->dbc = SQL_NULL_HDBC;
    }
    free(c);
    return rc;
}

/* CONTRACT: c must be live. ms_close_conn frees the state and the obo wrapper
 * zeroes the foreign word before calling it, so these are unreachable after a
 * close from obo. See ms_internal.h. */
const char *ms_conn_diag(const ms_conn *c) {
    if (!c || c->closed || !c->dbc) return "";
    return ms_handle_diag((SQLHDBC)(void *)c->dbc, SQL_HANDLE_DBC);
}

const char *ms_conn_sqlstate(const ms_conn *c) {
    if (!c || c->closed || !c->dbc) return "";
    return ms_handle_sqlstate((SQLHDBC)(void *)c->dbc, SQL_HANDLE_DBC);
}

int ms_conn_error_code(const ms_conn *c) {
    if (!c || c->closed || !c->dbc) return 0;
    return ms_handle_error_code((SQLHDBC)(void *)c->dbc, SQL_HANDLE_DBC);
}
