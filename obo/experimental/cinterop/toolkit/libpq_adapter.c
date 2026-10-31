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

static char *with_connect_timeout(const char *cs, size_t n) {
    const char *suffix = "connect_timeout=5";
    size_t slen = strlen(suffix);
    char *out;
    char sep = ' ';
    int uri = (n >= 11 && memcmp(cs, "postgres://", 11) == 0) ||
              (n >= 13 && memcmp(cs, "postgresql://", 13) == 0);
    if (strstr(cs, "connect_timeout=") != NULL) {
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
    PGconn *conn;
    long token;
    if (!cres_buf_data(dsn_token, &data, &len)) return -1;
    if (len > CRES_MAX_BUFFER) return -2;
    cs = copy_cstr(data, len);
    if (cs == NULL) return -2;
    bounded = with_connect_timeout(cs, len);
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
