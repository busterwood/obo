/* Fake libpq ABI for deterministic adapter/resource tests. It implements only
 * the declarations in toolkit/libpq_decls.h and never opens a socket. */
#include "../toolkit/libpq_decls.h"

#include <stdlib.h>
#include <string.h>

#define FAKE_MAX_ROWS 4
#define FAKE_MAX_COLS 4
#define FAKE_MAX_PARAMS 32

struct pg_conn {
    int status;
    char error[256];
};

struct pg_result {
    int status;
    int ntuples;
    int nfields;
    int isnull[FAKE_MAX_ROWS][FAKE_MAX_COLS];
    int lengths[FAKE_MAX_ROWS][FAKE_MAX_COLS];
    char *values[FAKE_MAX_ROWS][FAKE_MAX_COLS];
    char *names[FAKE_MAX_COLS];
    Oid types[FAKE_MAX_COLS];
    int mods[FAKE_MAX_COLS];
    char error[256];
};

static long fake_conn_live = 0;
static long fake_result_live = 0;
static long fake_finish_count = 0;
static long fake_clear_count = 0;
static long fake_exec_count = 0;
static long fake_make_empty_count = 0;
static int fake_fail_alloc = 0;
static int fake_last_nparams = 0;
static int fake_last_isnull[FAKE_MAX_PARAMS];
static int fake_last_len[FAKE_MAX_PARAMS];
static int fake_last_format[FAKE_MAX_PARAMS];
static char *fake_last_values[FAKE_MAX_PARAMS];
static char fake_last_sql[256];
static Oid fake_last_types[FAKE_MAX_PARAMS];
static int fake_last_result_format_value = 0;

long fake_get_conn_live(void) { return fake_conn_live; }
long fake_get_result_live(void) { return fake_result_live; }
long fake_get_finish_count(void) { return fake_finish_count; }
long fake_get_clear_count(void) { return fake_clear_count; }
long fake_get_exec_count(void) { return fake_exec_count; }
long fake_get_make_empty_count(void) { return fake_make_empty_count; }
void fake_set_fail_alloc(long v) { fake_fail_alloc = (int)v; }
long fake_last_param_count(void) { return fake_last_nparams; }
long fake_last_param_isnull(long i) { return (i >= 0 && i < FAKE_MAX_PARAMS) ? fake_last_isnull[i] : -1; }
long fake_last_param_len(long i) { return (i >= 0 && i < FAKE_MAX_PARAMS) ? fake_last_len[i] : -1; }
long fake_last_param_format(long i) { return (i >= 0 && i < FAKE_MAX_PARAMS) ? fake_last_format[i] : -1; }
long fake_last_param_type(long i) { return (i >= 0 && i < FAKE_MAX_PARAMS) ? (long)fake_last_types[i] : -1; }
long fake_last_result_format(void) { return (long)fake_last_result_format_value; }
long fake_last_param_byte(long i, long j) {
    if (i < 0 || i >= FAKE_MAX_PARAMS || fake_last_values[i] == NULL) return -1;
    if (j < 0 || j >= fake_last_len[i]) return -1;
    return (unsigned char)fake_last_values[i][j];
}
long fake_last_sql_len(void) { return (long)strlen(fake_last_sql); }
long fake_last_sql_byte(long i) {
    if (i < 0 || i >= (long)strlen(fake_last_sql)) return -1;
    return (unsigned char)fake_last_sql[i];
}

static void fake_clear_last(void) {
    int i;
    for (i = 0; i < FAKE_MAX_PARAMS; i++) {
        free(fake_last_values[i]);
        fake_last_values[i] = NULL;
        fake_last_isnull[i] = 0;
        fake_last_len[i] = 0;
        fake_last_format[i] = 0;
        fake_last_types[i] = 0;
    }
    fake_last_result_format_value = 0;
    fake_last_nparams = 0;
    fake_last_sql[0] = 0;
}

static PGresult *fake_new_result(int status, int ntuples, int nfields) {
    PGresult *r;
    int i, j;
    if (fake_fail_alloc) return NULL;
    r = (PGresult *)calloc(1, sizeof(*r));
    if (r == NULL) return NULL;
    r->status = status;
    r->ntuples = ntuples;
    r->nfields = nfields;
    for (i = 0; i < FAKE_MAX_ROWS; i++) {
        for (j = 0; j < FAKE_MAX_COLS; j++) {
            r->isnull[i][j] = 1;
            r->lengths[i][j] = 0;
        }
    }
    fake_result_live++;
    return r;
}

static void fake_set_cell(PGresult *r, int row, int col, const char *name,
                          Oid type, int mod, const char *data, int len, int isnull) {
    if (r == NULL || row < 0 || row >= FAKE_MAX_ROWS || col < 0 || col >= FAKE_MAX_COLS) return;
    if (col < FAKE_MAX_COLS) {
        if (r->names[col] == NULL && name != NULL) {
            r->names[col] = (char *)malloc(strlen(name) + 1);
            if (r->names[col] != NULL) strcpy(r->names[col], name);
        }
        r->types[col] = type;
        r->mods[col] = mod;
    }
    if (isnull) {
        r->isnull[row][col] = 1;
        r->lengths[row][col] = 0;
        return;
    }
    r->isnull[row][col] = 0;
    r->lengths[row][col] = len;
    if (len < 0) len = 0;
    r->values[row][col] = (char *)malloc((size_t)len + 1);
    if (r->values[row][col] == NULL) return;
    if (len != 0 && data != NULL) memcpy(r->values[row][col], data, (size_t)len);
    r->values[row][col][len] = 0;
}

int PQlibVersion(void) { return 160015; }

PGconn *PQconnectdb(const char *conninfo) {
    PGconn *c;
    if (conninfo == NULL) return NULL;
    if (strstr(conninfo, "fake-null") != NULL) return NULL;
    c = (PGconn *)calloc(1, sizeof(*c));
    if (c == NULL) return NULL;
    if (strstr(conninfo, "fake-fail") != NULL) {
        c->status = CONNECTION_BAD;
        strcpy(c->error, "fake connection failure");
    } else {
        c->status = CONNECTION_OK;
    }
    fake_conn_live++;
    return c;
}

int PQstatus(const PGconn *conn) { return conn == NULL ? CONNECTION_BAD : conn->status; }

char *PQerrorMessage(const PGconn *conn) {
    static char empty[] = "";
    return conn == NULL ? empty : (char *)conn->error;
}

void PQfinish(PGconn *conn) {
    if (conn != NULL) {
        fake_finish_count++;
        fake_conn_live--;
        free(conn);
    }
}

PGresult *PQexecParams(PGconn *conn, const char *command, int nParams,
                       const Oid *paramTypes, const char *const *paramValues,
                       const int *paramLengths, const int *paramFormats,
                       int resultFormat) {
    PGresult *r;
    int i;
    fake_exec_count++;
    fake_clear_last();
    fake_last_result_format_value = resultFormat;
    if (conn == NULL || command == NULL) return NULL;
    if (fake_fail_alloc || strstr(command, "FAKE_ALLOC") != NULL) return NULL;
    if (strlen(command) >= sizeof(fake_last_sql)) return NULL;
    strcpy(fake_last_sql, command);
    fake_last_nparams = nParams;
    for (i = 0; i < nParams && i < FAKE_MAX_PARAMS; i++) {
        fake_last_isnull[i] = paramValues != NULL && paramValues[i] == NULL;
        fake_last_len[i] = (paramValues != NULL && paramValues[i] != NULL) ? paramLengths[i] : 0;
        fake_last_format[i] = (paramValues != NULL && paramValues[i] != NULL) ? paramFormats[i] : 0;
        fake_last_types[i] = (paramTypes != NULL) ? paramTypes[i] : 0;
        if (paramValues != NULL && paramValues[i] != NULL) {
            fake_last_values[i] = (char *)malloc((size_t)fake_last_len[i] + 1);
            if (fake_last_values[i] != NULL) {
                if (fake_last_len[i] != 0) memcpy(fake_last_values[i], paramValues[i], (size_t)fake_last_len[i]);
                fake_last_values[i][fake_last_len[i]] = 0;
            }
        }
    }
    if (strstr(command, "FAKE_ERROR") != NULL) {
        r = fake_new_result(PGRES_FATAL_ERROR, 0, 0);
        if (r != NULL) strcpy(r->error, "fake query failed");
        return r;
    }
    if (strstr(command, "FAKE_NULL") != NULL) return NULL;
    if (strstr(command, "FAKE_EMPTY") != NULL) {
        return fake_new_result(PGRES_EMPTY_QUERY, 0, 0);
    }
    r = fake_new_result(PGRES_TUPLES_OK, 1, 4);
    if (r == NULL) return NULL;
    if (nParams >= 1 && paramValues != NULL && paramValues[0] != NULL) {
        fake_set_cell(r, 0, 0, "t", 25, -1, paramValues[0], paramLengths[0], 0);
    } else {
        fake_set_cell(r, 0, 0, "t", 25, -1, NULL, 0, 1);
    }
    if (nParams >= 2 && paramValues != NULL && paramValues[1] != NULL) {
        fake_set_cell(r, 0, 1, "n", 23, -1, paramValues[1], paramLengths[1], 0);
    } else {
        fake_set_cell(r, 0, 1, "n", 23, -1, NULL, 0, 1);
    }
    if (nParams >= 3 && paramValues != NULL && paramValues[2] != NULL) {
        fake_set_cell(r, 0, 2, "b", 17, -1, paramValues[2], paramLengths[2], 0);
    } else {
        fake_set_cell(r, 0, 2, "b", 17, -1, NULL, 0, 1);
    }
    fake_set_cell(r, 0, 3, "num", 1700, -1, "1.234567890123456789", 20, 0);
    return r;
}

int PQresultStatus(const PGresult *res) { return res == NULL ? PGRES_FATAL_ERROR : res->status; }
int PQntuples(const PGresult *res) { return res == NULL ? -1 : res->ntuples; }
int PQnfields(const PGresult *res) { return res == NULL ? -1 : res->nfields; }
int PQgetisnull(const PGresult *res, int row, int col) {
    if (res == NULL || row < 0 || row >= res->ntuples || col < 0 || col >= res->nfields) return -1;
    return res->isnull[row][col];
}
int PQgetlength(const PGresult *res, int row, int col) {
    if (res == NULL || row < 0 || row >= res->ntuples || col < 0 || col >= res->nfields) return -1;
    return res->lengths[row][col];
}
char *PQgetvalue(const PGresult *res, int row, int col) {
    static char empty[] = "";
    if (res == NULL || row < 0 || row >= res->ntuples || col < 0 || col >= res->nfields) return empty;
    if (res->values[row][col] == NULL) return empty;
    return res->values[row][col];
}
char *PQfname(const PGresult *res, int col) {
    static char empty[] = "";
    if (res == NULL || col < 0 || col >= res->nfields || res->names[col] == NULL) return empty;
    return res->names[col];
}
Oid PQftype(const PGresult *res, int col) {
    if (res == NULL || col < 0 || col >= res->nfields) return 0;
    return res->types[col];
}
int PQfmod(const PGresult *res, int col) {
    if (res == NULL || col < 0 || col >= res->nfields) return -1;
    return res->mods[col];
}
char *PQresultErrorMessage(const PGresult *res) {
    static char empty[] = "";
    return res == NULL ? empty : (char *)res->error;
}

void PQclear(PGresult *res) {
    int i, j;
    if (res == NULL) return;
    fake_clear_count++;
    fake_result_live--;
    for (i = 0; i < FAKE_MAX_ROWS; i++) {
        for (j = 0; j < FAKE_MAX_COLS; j++) free(res->values[i][j]);
    }
    for (j = 0; j < FAKE_MAX_COLS; j++) free(res->names[j]);
    free(res);
}

PGresult *PQmakeEmptyPGresult(PGconn *conn, int status) {
    (void)conn;
    fake_make_empty_count++;
    return fake_new_result(status, 0, 0);
}

/* Minimal fake conninfo parser: enough to exercise the adapter's
 * "is connect_timeout present?" query without a hand-rolled production parser.
 * It walks unquoted tokens and ignores quoted text. */
PQconninfoOption *PQconninfoParse(const char *conninfo, char **errmsg) {
    PQconninfoOption *opts;
    const char *p = conninfo;
    int found = 0;
    int quoted = 0;
    if (errmsg != NULL) *errmsg = NULL;
    if (p == NULL) return NULL;
    while (*p != 0) {
        if (*p == '\'') {
            quoted = !quoted;
            p++;
            continue;
        }
        if (!quoted && strncmp(p, "connect_timeout", 15) == 0) {
            const char *q = p + 15;
            while (*q == ' ' || *q == '\t') q++;
            if (*q == '=') {
                found = 1;
                break;
            }
        }
        p++;
    }
    opts = (PQconninfoOption *)calloc(2, sizeof(*opts));
    if (opts == NULL) return NULL;
    if (found) {
        opts[0].keyword = (char *)"connect_timeout";
        opts[0].val = (char *)"";
    }
    return opts;
}

void PQconninfoFree(PQconninfoOption *connOptions) {
    free(connOptions);
}

void PQfreemem(void *ptr) {
    free(ptr);
}
