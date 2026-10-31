/* Experimental typed-parameter PostgreSQL adapter.
 *
 * Packet format (all integers little-endian, built by postgres.obx):
 *   u32 sql_len, sql bytes,
 *   u8  result_format (0 text, 1 binary),
 *   u32 nparams,
 *   nparams x { u8 kind, u32 len, len bytes }.
 *
 * Kinds are 0 NULL, 1 text, 2 blob (bytea), 3 int (i64 decimal text) and
 * 4 bool ("t"/"f"). The adapter sets the PostgreSQL parameter OID explicitly:
 * 0 (unspecified), 25 (text), 17 (bytea), 20 (int8) and 16 (bool). It keeps
 * the eight-argument PQexecParams call from the reviewed libpq declarations.
 *
 * SQL and text parameters reject embedded NUL. Binary parameters preserve
 * their exact length, including NUL. There is no SQL interpolation: the SQL
 * bytes are passed to PQexecParams as the command string and every value is a
 * bound parameter.
 */
#include "../../cinterop/toolkit/cres.h"
#include "../../cinterop/toolkit/libpq_adapter.h"
#include "../../cinterop/toolkit/libpq_decls.h"
#include "postgres_adapter.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define PG_MAX_PARAMS 32
#define PG_MAX_SQL (1024L * 1024L)
#define PG_MAX_VALUE (16L * 1024L * 1024L)

enum {
    PG_PARAM_NULL = 0,
    PG_PARAM_TEXT = 1,
    PG_PARAM_BLOB = 2,
    PG_PARAM_INT = 3,
    PG_PARAM_BOOL = 4
};

static void release_result(void *p) {
    PQclear((PGresult *)p);
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

static int valid_int_text(const unsigned char *p, size_t n) {
    size_t i = 0;
    if (n == 0 || n > 32) return 0;
    if (p[0] == '-') {
        i = 1;
        if (i == n) return 0;
    }
    for (; i < n; i++) {
        if (p[i] < '0' || p[i] > '9') return 0;
    }
    return 1;
}

long pg_open(long dsn_buf) {
    return pq_connect(dsn_buf);
}

long pg_exec_typed(long conn_token, long packet_buf) {
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
    Oid *types = NULL;
    unsigned long i;
    PGresult *res;
    long token;

    if (conn == NULL) return -1;
    if (!cres_buf_data(packet_buf, &packet, &packet_len)) return -2;
    p = packet;
    end = packet + packet_len;
    if (!packet_u32(&p, end, &sql_len)) return -2;
    if (sql_len > (unsigned long)PG_MAX_SQL || (size_t)(end - p) < (size_t)sql_len) return -2;
    sql = copy_cstr(p, (size_t)sql_len);
    if (sql == NULL) return -2;
    p += sql_len;
    if (!packet_u8(&p, end, &result_format) || result_format > 1) {
        free(sql);
        return -2;
    }
    if (!packet_u32(&p, end, &nparams) || nparams > PG_MAX_PARAMS) {
        free(sql);
        return -2;
    }

    if (nparams != 0) {
        values = (const char **)calloc((size_t)nparams, sizeof(*values));
        lengths = (int *)calloc((size_t)nparams, sizeof(*lengths));
        formats = (int *)calloc((size_t)nparams, sizeof(*formats));
        types = (Oid *)calloc((size_t)nparams, sizeof(*types));
        if (values == NULL || lengths == NULL || formats == NULL || types == NULL) {
            free(sql);
            free(values);
            free(lengths);
            free(formats);
            free(types);
            return -3;
        }
    }

    for (i = 0; i < nparams; i++) {
        unsigned int kind = 0;
        unsigned long len = 0;
        if (!packet_u8(&p, end, &kind) || kind > PG_PARAM_BOOL) goto bad_packet;
        if (!packet_u32(&p, end, &len)) goto bad_packet;
        if (len > (unsigned long)PG_MAX_VALUE || (size_t)(end - p) < (size_t)len) goto bad_packet;
        switch (kind) {
        case PG_PARAM_NULL:
            types[i] = 0;
            values[i] = NULL;
            lengths[i] = 0;
            formats[i] = 0;
            break;
        case PG_PARAM_TEXT:
            if (has_nul(p, (size_t)len)) goto bad_packet;
            types[i] = 25; /* text */
            values[i] = copy_cstr(p, (size_t)len);
            if (values[i] == NULL) goto nomem;
            lengths[i] = (int)len;
            formats[i] = 0;
            break;
        case PG_PARAM_BLOB:
            types[i] = 17; /* bytea */
            values[i] = copy_bytes_raw(p, (size_t)len);
            if (values[i] == NULL) goto nomem;
            lengths[i] = (int)len;
            formats[i] = 1;
            break;
        case PG_PARAM_INT:
            if (!valid_int_text(p, (size_t)len)) goto bad_packet;
            types[i] = 20; /* int8 */
            values[i] = copy_bytes_raw(p, (size_t)len);
            if (values[i] == NULL) goto nomem;
            lengths[i] = (int)len;
            formats[i] = 0;
            break;
        case PG_PARAM_BOOL:
            if (len != 1 || (p[0] != 't' && p[0] != 'f')) goto bad_packet;
            types[i] = 16; /* bool */
            values[i] = copy_bytes_raw(p, (size_t)len);
            if (values[i] == NULL) goto nomem;
            lengths[i] = 1;
            formats[i] = 0;
            break;
        default:
            goto bad_packet;
        }
        p += len;
    }
    if (p != end) goto bad_packet;

    res = PQexecParams(conn, sql, (int)nparams, types, values, lengths, formats,
                       (int)result_format);
    free(sql);
    for (i = 0; i < nparams; i++) free((void *)values[i]);
    free(values);
    free(lengths);
    free(formats);
    free(types);
    if (res == NULL) return 0;
    token = cres_register(CRES_KIND_RESULT, res, release_result);
    if (token == 0) {
        PQclear(res);
        return -3;
    }
    return token;

nomem:
    free(sql);
    for (i = 0; i < nparams; i++) free((void *)values[i]);
    free(values);
    free(lengths);
    free(formats);
    free(types);
    return -3;

bad_packet:
    free(sql);
    for (i = 0; i < nparams; i++) free((void *)values[i]);
    free(values);
    free(lengths);
    free(formats);
    free(types);
    return -2;
}
