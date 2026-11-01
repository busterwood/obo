/* Diagnostic formatting and the process-wide error/text buffers.
 *
 * ODBC diagnostics are fetched with SQLGetDiagRecW and converted to UTF-8 here,
 * because the Phase 1 probe showed the narrow SQLGetDiagRec path is converted
 * through the process locale (a non-ASCII server message came back as Latin-1
 * under the C locale). Every record is formatted as
 *
 *     SQLSTATE/native/message\n
 *
 * so the SQLSTATE and the SQL Server native error number both survive in a
 * single text value, and the wrapper can also expose the native code alone.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ms_internal.h"

static char *g_text;
static size_t g_text_cap;

static char *g_last_error;
static char g_last_state[6];
static int g_last_code;

char *ms_text_reserve(size_t need) {
    char *p;
    size_t cap;
    if (need <= g_text_cap) {
        if (!g_text) {
            g_text = (char *)malloc(1);
            if (!g_text) return NULL;
            g_text[0] = 0;
        }
        g_text[0] = 0;
        return g_text;
    }
    if (need > (size_t)MS_MAX_VALUE + 1) need = (size_t)MS_MAX_VALUE + 1;
    cap = g_text_cap ? g_text_cap : 1024;
    while (cap < need) {
        if (cap >= (size_t)MS_MAX_VALUE + 1) {
            cap = (size_t)MS_MAX_VALUE + 1;
            break;
        }
        cap *= 2;
        if (cap > (size_t)MS_MAX_VALUE + 1) cap = (size_t)MS_MAX_VALUE + 1;
    }
    p = (char *)realloc(g_text, cap);
    if (!p) return NULL;
    g_text = p;
    g_text_cap = cap;
    g_text[0] = 0;
    return g_text;
}

void ms_set_last_error(const char *text, const char *state, int code) {
    size_t n = text ? strlen(text) : 0;
    char *p;
    if (n > (size_t)MS_MAX_VALUE) n = (size_t)MS_MAX_VALUE;
    p = (char *)realloc(g_last_error, n + 1);
    if (p) {
        g_last_error = p;
        if (n) memcpy(g_last_error, text, n);
        g_last_error[n] = 0;
    }
    if (state) {
        strncpy(g_last_state, state, 5);
        g_last_state[5] = 0;
    } else {
        g_last_state[0] = 0;
    }
    g_last_code = code;
}

const char *ms_last_error(void) {
    return g_last_error ? g_last_error : "";
}

const char *ms_last_error_state(void) {
    return g_last_state;
}

int ms_last_error_code(void) {
    return g_last_code;
}

/* Fetch one diagnostic record (1-based), growing the wide message buffer when
 * the driver reports a longer message. Returns 0 on success, 1 on SQL_NO_DATA
 * and -1 on any other failure. msg_out is malloc'd and the caller frees it. */
static int diag_record(SQLSMALLINT type, SQLHANDLE h, SQLSMALLINT n,
                       char state_out[6], int *native_out, char **msg_out) {
    SQLWCHAR state_w[6];
    SQLWCHAR stack[512];
    SQLWCHAR *wb = stack;
    SQLSMALLINT len = 0;
    SQLINTEGER native = 0;
    SQLRETURN rc;
    char *utf8 = NULL;
    int bytes = 0;
    int i;

    *msg_out = NULL;
    memset(state_w, 0, sizeof state_w);
    state_w[0] = 0;
    rc = SQLGetDiagRecW(type, h, n, state_w, &native, stack,
                        (SQLSMALLINT)(sizeof(stack) / sizeof(stack[0])), &len);
    if (rc == SQL_NO_DATA) return 1;
    if (!SQL_SUCCEEDED(rc)) return -1;
    if (len >= (SQLSMALLINT)(sizeof(stack) / sizeof(stack[0]))) {
        wb = (SQLWCHAR *)malloc(((size_t)len + 1) * sizeof(SQLWCHAR));
        if (!wb) return -1;
        rc = SQLGetDiagRecW(type, h, n, state_w, &native, wb,
                            (SQLSMALLINT)(len + 1), &len);
        if (!SQL_SUCCEEDED(rc) && rc != SQL_SUCCESS_WITH_INFO) {
            free(wb);
            return -1;
        }
    }
    for (i = 0; i < 5; i++) {
        state_out[i] = (state_w[i] >= 0x20 && state_w[i] < 0x7f)
                           ? (char)state_w[i] : '?';
    }
    state_out[5] = 0;
    if (ms_utf16_to_utf8(wb, (int)len, &utf8, &bytes) != 0) {
        if (wb != stack) free(wb);
        return -1;
    }
    if (wb != stack) free(wb);
    *native_out = (int)native;
    *msg_out = utf8;
    return 0;
}

int ms_diag(SQLHANDLE h, int handle_type, char *out, int cap) {
    int used = 0;
    int n = 1;
    if (!out || cap <= 0) return 0;
    out[0] = 0;
    if (!h) return 0;
    while (n <= 64) {
        char state[6];
        int native = 0;
        char *msg = NULL;
        int r = diag_record((SQLSMALLINT)handle_type, h, (SQLSMALLINT)n, state,
                            &native, &msg);
        int need;
        if (r != 0) {
            if (msg) free(msg);
            break;
        }
        need = 5 + 1 + 12 + 1 + (int)strlen(msg) + 1;
        if (used + need < cap) {
            used += snprintf(out + used, (size_t)(cap - used), "%s/%d/%s\n",
                             state, native, msg);
        } else if (cap - 1 > used) {
            int space = cap - 1 - used;
            int w = snprintf(out + used, (size_t)(space + 1), "%s/%d/%s\n",
                             state, native, msg);
            used += (w < space) ? w : space;
            out[cap - 1] = 0;
        }
        free(msg);
        n++;
    }
    return used;
}

/* Format all records into the shared text buffer, growing until the output is
 * known not to be truncated. */
static const char *handle_diag(SQLHANDLE h, int handle_type) {
    int cap = 1024;
    while (cap <= MS_MAX_VALUE + 1) {
        char *buf = ms_text_reserve((size_t)cap);
        int n;
        if (!buf) return "";
        n = ms_diag(h, handle_type, buf, cap);
        if (n < cap - 1) return buf;
        cap *= 2;
    }
    return ms_text_reserve(1) ? g_text : "";
}

const char *ms_handle_diag(SQLHANDLE h, int handle_type) {
    return handle_diag(h, handle_type);
}

const char *ms_handle_sqlstate(SQLHANDLE h, int handle_type) {
    static char state[6];
    char *msg = NULL;
    int native = 0;
    state[0] = 0;
    if (diag_record((SQLSMALLINT)handle_type, h, 1, state, &native, &msg) == 0) {
        free(msg);
        return state;
    }
    return "";
}

int ms_handle_error_code(SQLHANDLE h, int handle_type) {
    char state[6];
    char *msg = NULL;
    int native = 0;
    if (diag_record((SQLSMALLINT)handle_type, h, 1, state, &native, &msg) == 0) {
        free(msg);
        return native;
    }
    return 0;
}
