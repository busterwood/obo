/* Experimental libpq adapter over the C resource registry.
 *
 * Every exported function takes and returns only scalar long values. Obo never
 * sees a PGconn*, PGresult* or char*. Pointer lifetimes stay in this trusted C
 * adapter; obo holds generation-checked tokens and explicit close operations.
 *
 * Packet format for pq_exec_packet (all integers little-endian):
 *   u32 sql_len, sql bytes,
 *   u8  result_format (0 text, 1 binary),
 *   u32 nparams,
 *   nparams x { u8 kind (0 NULL, 1 text, 2 binary), u32 len, len bytes }.
 *
 * SQL and text parameters reject an embedded NUL before PQexecParams. Binary
 * parameters preserve their exact length, including NUL. SQL is passed as a
 * parameter string to PQexecParams, never interpolated.
 */
#include "cres.h"
#include "libpq_adapter.h"
#include "libpq_decls.h"

#include <stdlib.h>
#include <string.h>

#define PQ_MAX_PARAMS 32
#define PQ_MAX_SQL (1024L * 1024L)

static void release_conn(void *p) { PQfinish((PGconn *)p); }
static void release_result(void *p) { PQclear((PGresult *)p); }

/* The last conninfo parse diagnostic, copied before libpq frees its string.
 * The adapter is single-threaded and trusted; this is a bounded diagnostic,
 * not a second error channel for connection status. */
static char *pq_parse_error = NULL;

static void clear_parse_error(void) {
    free(pq_parse_error);
    pq_parse_error = NULL;
}

static void set_parse_error(const char *msg) {
    size_t n;
    char *copy;
    clear_parse_error();
    if (msg == NULL) msg = "invalid connection string";
    n = strlen(msg);
    copy = (char *)malloc(n + 1);
    if (copy == NULL) return;
    memcpy(copy, msg, n + 1);
    pq_parse_error = copy;
}

static int packet_u8(const unsigned char **p, const unsigned char *end, unsigned int *v) {
    if (*p >= end) return 0;
    *v = (unsigned int)**p;
    (*p)++;
    return 1;
}

static int packet_u32(const unsigned char **p, const unsigned char *end, unsigned long *v) {
    if ((size_t)(end - *p) < 4) return 0;
    *v = (unsigned long)(*p)[0] | ((unsigned long)(*p)[1] << 8) |
         ((unsigned long)(*p)[2] << 16) | ((unsigned long)(*p)[3] << 24);
    *p += 4;
    return 1;
}

static int has_nul(const unsigned char *p, size_t n) {
    size_t i;
    for (i = 0; i < n; i++) if (p[i] == 0) return 1;
    return 0;
}

static char *copy_bytes_raw(const unsigned char *p, size_t n) {
    char *s = (char *)malloc(n + 1);
    if (s == NULL) return NULL;
    if (n != 0) memcpy(s, p, n);
    s[n] = 0;
    return s;
}

static char *copy_cstr(const unsigned char *p, size_t n) {
    if (has_nul(p, n)) return NULL;
    return copy_bytes_raw(p, n);
}

/* Ask libpq's own conninfo parser whether a bounded timeout is already named.
 * Returns 1 present, 0 absent, -1 invalid conninfo. This avoids mistaking a
 * quoted value such as application_name='connect_timeout=' for a keyword. */
static int conninfo_has_timeout(const char *cs) {
    PQconninfoOption *opts;
    char *err = NULL;
    int found = 0;
    opts = PQconninfoParse(cs, &err);
    if (opts == NULL) {
        set_parse_error(err != NULL ? err : "invalid connection string");
        if (err != NULL) PQfreemem(err);
        return -1;
    }
    for (PQconninfoOption *o = opts; o->keyword != NULL; o++) {
        if (strcmp(o->keyword, "connect_timeout") == 0 && o->val != NULL) {
            found = 1;
            break;
        }
    }
    PQconninfoFree(opts);
    return found;
}

static char *with_connect_timeout(const char *cs, size_t n, int has_timeout) {
    const char *suffix = "connect_timeout=5";
    size_t slen = strlen(suffix);
    char *out;
    char sep = ' ';
    int uri = (n >= 11 && memcmp(cs, "postgres://", 11) == 0) ||
              (n >= 13 && memcmp(cs, "postgresql://", 13) == 0);
    if (has_timeout) {
        out = (char *)malloc(n + 1);
        if (out == NULL) return NULL;
        memcpy(out, cs, n + 1);
        return out;
    }
    if (uri) sep = (strchr(cs, '?') != NULL) ? '&' : '?';
    out = (char *)malloc(n + 1 + slen + 2);
    if (out == NULL) return NULL;
    memcpy(out, cs, n);
    out[n] = sep;
    memcpy(out + n + 1, suffix, slen);
    out[n + 1 + slen] = 0;
    return out;
}

long pq_lib_version(void) {
    return (long)PQlibVersion();
}

long pq_make_empty_result(long status) {
    PGresult *res;
    long token;
    if (status < 0 || status > PGRES_TUPLES_CHUNK) return -2;
    res = PQmakeEmptyPGresult(NULL, (int)status);
    if (res == NULL) return 0;
    token = cres_register(CRES_KIND_RESULT, res, release_result);
    if (token == 0) {
        PQclear(res);
        return -3;
    }
    return token;
}

long pq_connect(long dsn_token) {
    unsigned char *data = NULL;
    size_t len = 0;
    char *cs;
    char *bounded;
    int has_timeout;
    PGconn *conn;
    long token;
    clear_parse_error();
    if (!cres_buf_data(dsn_token, &data, &len)) return -1;
    if (len > CRES_MAX_BUFFER) return -2;
    cs = copy_cstr(data, len);
    if (cs == NULL) return -2;
    has_timeout = conninfo_has_timeout(cs);
    if (has_timeout < 0) {
        free(cs);
        return -4;
    }
    bounded = with_connect_timeout(cs, len, has_timeout);
    free(cs);
    if (bounded == NULL) return -3;
    conn = PQconnectdb(bounded);
    free(bounded);
    if (conn == NULL) return 0;
    token = cres_register(CRES_KIND_CONN, conn, release_conn);
    if (token == 0) {
        PQfinish(conn);
        return -3;
    }
    return token;
}

/* Copy of the last invalid-conninfo diagnostic from libpq. Valid only
 * immediately after pq_connect returned -4; the wrapper copies it before any
 * other adapter call. Returns a buffer token, or 0 on allocation failure. */
long pq_conninfo_error_buf(void) {
    if (pq_parse_error == NULL) return cres_buf_from_bytes("", 0);
    return cres_buf_from_bytes(pq_parse_error, strlen(pq_parse_error));
}

long pq_conn_status(long conn_token) {
    PGconn *conn = (PGconn *)cres_peek(conn_token, CRES_KIND_CONN);
    if (conn == NULL) return -1;
    return (long)PQstatus(conn);
}

long pq_conn_live(long conn_token) {
    return cres_peek(conn_token, CRES_KIND_CONN) != NULL;
}

long pq_conn_close(long conn_token) {
    return cres_close_kind(conn_token, CRES_KIND_CONN);
}

long pq_result_live(long res_token) {
    return cres_peek(res_token, CRES_KIND_RESULT) != NULL;
}

long pq_result_clear(long res_token) {
    return cres_close_kind(res_token, CRES_KIND_RESULT);
}

long pq_error_buf(long token) {
    const char *msg = NULL;
    if (cres_peek(token, CRES_KIND_CONN) != NULL) {
        msg = PQerrorMessage((PGconn *)cres_peek(token, CRES_KIND_CONN));
    } else if (cres_peek(token, CRES_KIND_RESULT) != NULL) {
        msg = PQresultErrorMessage((PGresult *)cres_peek(token, CRES_KIND_RESULT));
    } else {
        return -1;
    }
    if (msg == NULL) msg = "";
    return cres_buf_from_bytes(msg, strlen(msg));
}

long pq_result_error_buf(long res_token) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    const char *msg;
    if (res == NULL) return -1;
    msg = PQresultErrorMessage(res);
    if (msg == NULL) msg = "";
    return cres_buf_from_bytes(msg, strlen(msg));
}

long pq_result_status(long res_token) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    if (res == NULL) return -1;
    return (long)PQresultStatus(res);
}

long pq_ntuples(long res_token) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    int n;
    if (res == NULL) return -1;
    n = PQntuples(res);
    if (n < 0) return -1;
    return (long)n;
}

long pq_nfields(long res_token) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    int n;
    if (res == NULL) return -1;
    n = PQnfields(res);
    if (n < 0) return -1;
    return (long)n;
}

static int valid_cell(PGresult *res, long row, long col) {
    int nt = PQntuples(res);
    int nf = PQnfields(res);
    if (nt < 0 || nf < 0) return 0;
    if (row < 0 || col < 0 || row >= nt || col >= nf) return 0;
    return 1;
}

long pq_getisnull(long res_token, long row, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    if (res == NULL || !valid_cell(res, row, col)) return -1;
    return (long)PQgetisnull(res, (int)row, (int)col);
}

long pq_getlength(long res_token, long row, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    int n;
    if (res == NULL || !valid_cell(res, row, col)) return -1;
    if (PQgetisnull(res, (int)row, (int)col)) return 0;
    n = PQgetlength(res, (int)row, (int)col);
    if (n < 0) return -1;
    return (long)n;
}

long pq_value_buf(long res_token, long row, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    char *value;
    int len;
    if (res == NULL || !valid_cell(res, row, col)) return -1;
    if (PQgetisnull(res, (int)row, (int)col)) return 0;
    value = PQgetvalue(res, (int)row, (int)col);
    len = PQgetlength(res, (int)row, (int)col);
    if (value == NULL || len < 0) return -1;
    return cres_buf_from_bytes(value, (size_t)len);
}

long pq_fname_buf(long res_token, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    char *name;
    if (res == NULL || col < 0 || col >= PQnfields(res)) return -1;
    name = PQfname(res, (int)col);
    if (name == NULL) name = "";
    return cres_buf_from_bytes(name, strlen(name));
}

long pq_ftype(long res_token, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    if (res == NULL || col < 0 || col >= PQnfields(res)) return -1;
    return (long)PQftype(res, (int)col);
}

long pq_fmod(long res_token, long col) {
    PGresult *res = (PGresult *)cres_peek(res_token, CRES_KIND_RESULT);
    if (res == NULL || col < 0 || col >= PQnfields(res)) return -1;
    return (long)PQfmod(res, (int)col);
}

long pq_exec_packet(long conn_token, long packet_token) {
    PGconn *conn = (PGconn *)cres_peek(conn_token, CRES_KIND_CONN);
    unsigned char *packet = NULL;
    size_t packet_len = 0;
    const unsigned char *p;
    const unsigned char *end;
    unsigned long sql_len = 0;
    unsigned int result_format = 0;
    unsigned long nparams = 0;
    char *sql = NULL;
    const char **values = NULL;
    int *lengths = NULL;
    int *formats = NULL;
    unsigned long i;
    PGresult *res;
    long token;

    if (conn == NULL) return -1;
    if (!cres_buf_data(packet_token, &packet, &packet_len)) return -2;
    p = packet;
    end = packet + packet_len;
    if (!packet_u32(&p, end, &sql_len)) return -2;
    if (sql_len > (unsigned long)PQ_MAX_SQL || (size_t)(end - p) < sql_len) return -2;
    if (has_nul(p, (size_t)sql_len)) return -2;
    sql = copy_cstr(p, (size_t)sql_len);
    if (sql == NULL) return -3;
    p += sql_len;
    if (!packet_u8(&p, end, &result_format) || result_format > 1) {
        free(sql);
        return -2;
    }
    if (!packet_u32(&p, end, &nparams) || nparams > PQ_MAX_PARAMS) {
        free(sql);
        return -2;
    }

    if (nparams != 0) {
        values = (const char **)calloc((size_t)nparams, sizeof(*values));
        lengths = (int *)calloc((size_t)nparams, sizeof(*lengths));
        formats = (int *)calloc((size_t)nparams, sizeof(*formats));
        if (values == NULL || lengths == NULL || formats == NULL) {
            free(sql);
            free(values);
            free(lengths);
            free(formats);
            return -3;
        }
    }

    for (i = 0; i < nparams; i++) {
        unsigned int kind = 0;
        unsigned long len = 0;
        if (!packet_u8(&p, end, &kind) || kind > 2) goto bad_packet;
        if (!packet_u32(&p, end, &len)) goto bad_packet;
        if ((size_t)(end - p) < (size_t)len) goto bad_packet;
        if (kind == 0) {
            values[i] = NULL;
            lengths[i] = 0;
            formats[i] = 0;
        } else {
            if (kind == 1 && has_nul(p, (size_t)len)) goto bad_packet;
            /* Binary values may contain NUL; only text values are C strings. */
            values[i] = (kind == 1) ? copy_cstr(p, (size_t)len) : copy_bytes_raw(p, (size_t)len);
            if (values[i] == NULL) {
                free(sql);
                for (i = 0; i < nparams; i++) free((void *)values[i]);
                free(values);
                free(lengths);
                free(formats);
                return -3;
            }
            lengths[i] = (int)len;
            formats[i] = (kind == 2) ? 1 : 0;
        }
        p += len;
    }
    if (p != end) goto bad_packet;

    res = PQexecParams(conn, sql, (int)nparams, NULL, values, lengths, formats,
                       (int)result_format);
    free(sql);
    for (i = 0; i < nparams; i++) free((void *)values[i]);
    free(values);
    free(lengths);
    free(formats);
    if (res == NULL) return 0;
    token = cres_register(CRES_KIND_RESULT, res, release_result);
    if (token == 0) {
        PQclear(res);
        return -3;
    }
    return token;

bad_packet:
    free(sql);
    if (values != NULL) {
        for (i = 0; i < nparams; i++) free((void *)values[i]);
    }
    free(values);
    free(lengths);
    free(formats);
    return -2;
}
