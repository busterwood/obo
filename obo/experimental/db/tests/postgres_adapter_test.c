/* Direct C tests for the experimental PostgreSQL typed adapter.
 *
 * The deterministic fake-libpq implements the same eight-argument
 * PQexecParams descriptor and records parameter types/formats/bytes, so these
 * tests prove packet parsing, typed OIDs and ownership without a server.
 */
#include "../../cinterop/toolkit/cres.h"
#include "../../cinterop/toolkit/libpq_adapter.h"
#include "../../cinterop/toolkit/libpq_decls.h"
#include "../postgres/postgres_adapter.h"

#include <stdio.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

extern long fake_get_conn_live(void);
extern long fake_get_result_live(void);
extern long fake_get_finish_count(void);
extern long fake_get_clear_count(void);
extern long fake_get_exec_count(void);
extern void fake_set_fail_alloc(long v);
extern long fake_last_param_count(void);
extern long fake_last_param_isnull(long i);
extern long fake_last_param_len(long i);
extern long fake_last_param_format(long i);
extern long fake_last_param_type(long i);
extern long fake_last_param_byte(long i, long j);
extern long fake_last_result_format(void);
extern long fake_last_sql_len(void);
extern long fake_last_sql_byte(long i);

typedef struct {
    unsigned char b[4096];
    size_t n;
} packet;

static void p8(packet *p, unsigned int v) {
    p->b[p->n++] = (unsigned char)(v & 0xff);
}

static void p32(packet *p, unsigned long v) {
    p8(p, (unsigned int)(v & 0xff));
    p8(p, (unsigned int)((v >> 8) & 0xff));
    p8(p, (unsigned int)((v >> 16) & 0xff));
    p8(p, (unsigned int)((v >> 24) & 0xff));
}

static void pbytes(packet *p, const void *d, size_t n) {
    if (n != 0) memcpy(p->b + p->n, d, n);
    p->n += n;
}

static void pbegin(packet *p, const char *sql, int result_binary, unsigned long np) {
    p->n = 0;
    p32(p, (unsigned long)strlen(sql));
    pbytes(p, sql, strlen(sql));
    p8(p, (unsigned int)result_binary);
    p32(p, np);
}

static void pparam(packet *p, unsigned int kind, const void *d, size_t n) {
    p8(p, kind);
    p32(p, (unsigned long)n);
    pbytes(p, d, n);
}

static long mkc(const char *s) {
    return cres_buf_from_bytes(s, strlen(s));
}

static long wrap(const packet *p) {
    return cres_buf_from_bytes(p->b, p->n);
}

static int buf_eq(long tok, const unsigned char *want, size_t n) {
    unsigned char *p = NULL;
    size_t len = 0;
    if (!cres_buf_data(tok, &p, &len)) return 0;
    return len == n && (n == 0 || memcmp(p, want, n) == 0);
}

/* The reviewed eight-argument PQexecParams descriptor is still what the
 * adapter calls. A changed width or missing parameter is a compile error. */
static PGresult *(*chk_exec_params)(PGconn *, const char *, int, const Oid *,
                                    const char *const *, const int *,
                                    const int *, int) = PQexecParams;

static int run_bad_packet(long conn, packet *p) {
    long b = wrap(p);
    long t;
    CHECK(b > 0);
    t = pg_exec_typed(conn, b);
    CHECK(t == -2);
    CHECK(cres_buf_free(b) == 1);
    CHECK(cres_live_kind(CRES_KIND_RESULT) == 0);
    return 0;
}

int main(void) {
    long cs, conn, b, res, v;
    packet p;
    unsigned char blob[3] = {0, 255, 65};
    const unsigned char hello[] = "hello";
    const unsigned char minus[] = "-9223372036854775807";

    CHECK(cres_live_count() == 0);
    (void)chk_exec_params;

    cs = mkc("fake-ok");
    CHECK(cs > 0);
    conn = pg_open(cs);
    CHECK(conn > 0);
    CHECK(cres_buf_free(cs) == 1);
    CHECK(pq_conn_status(conn) == CONNECTION_OK);

    /* Typed packet: text, int8, bool, bytea and NULL. */
    pbegin(&p, "SELECT $1::text, $2::int8, $3::bool, $4::bytea, $5::text", 0, 5);
    pparam(&p, 1, "hello", 5);
    pparam(&p, 3, minus, sizeof(minus) - 1);
    pparam(&p, 4, "t", 1);
    pparam(&p, 2, blob, sizeof(blob));
    pparam(&p, 0, NULL, 0);
    b = wrap(&p);
    CHECK(b > 0);
    res = pg_exec_typed(conn, b);
    CHECK(res > 0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(fake_get_exec_count() == 1);
    CHECK(fake_last_sql_len() == 56);
    CHECK(fake_last_param_count() == 5);
    CHECK(fake_last_param_type(0) == 25);
    CHECK(fake_last_param_type(1) == 20);
    CHECK(fake_last_param_type(2) == 16);
    CHECK(fake_last_param_type(3) == 17);
    CHECK(fake_last_param_type(4) == 0);
    CHECK(fake_last_param_format(0) == 0);
    CHECK(fake_last_param_format(2) == 0);
    CHECK(fake_last_param_format(3) == 1);
    CHECK(fake_last_param_isnull(4) == 1);
    CHECK(fake_last_param_len(0) == 5);
    CHECK(fake_last_param_byte(0, 0) == 'h');
    CHECK(fake_last_param_len(1) == (long)(sizeof(minus) - 1));
    CHECK(fake_last_param_byte(1, 0) == '-');
    CHECK(fake_last_param_len(3) == 3);
    CHECK(fake_last_param_byte(3, 0) == 0);
    CHECK(fake_last_param_byte(3, 1) == 255);
    CHECK(fake_last_param_byte(3, 2) == 65);
    CHECK(fake_last_result_format() == 0);
    CHECK(pq_result_status(res) == PGRES_TUPLES_OK);
    CHECK(pq_ntuples(res) == 1);
    CHECK(pq_nfields(res) == 4);
    v = pq_value_buf(res, 0, 0);
    CHECK(v > 0);
    CHECK(buf_eq(v, hello, 5));
    CHECK(cres_buf_free(v) == 1);
    CHECK(pq_result_clear(res) == 1);
    CHECK(pq_result_clear(res) == 0);

    /* Binary result format reaches PQexecParams as format 1. */
    pbegin(&p, "SELECT decode('00ff41','hex')", 1, 0);
    b = wrap(&p);
    CHECK(b > 0);
    v = pg_exec_typed(conn, b);
    CHECK(v > 0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(fake_last_result_format() == 1);
    CHECK(pq_result_clear(v) == 1);
    CHECK(pq_result_clear(v) == 0);

    /* NULL first parameter: the fake result's first cell is NULL. */
    pbegin(&p, "SELECT $1::text, $2::int8", 0, 2);
    pparam(&p, 0, NULL, 0);
    pparam(&p, 3, "42", 2);
    b = wrap(&p);
    CHECK(b > 0);
    v = pg_exec_typed(conn, b);
    CHECK(v > 0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(pq_getisnull(v, 0, 0) == 1);
    CHECK(pq_getlength(v, 0, 0) == 0);
    CHECK(pq_value_buf(v, 0, 0) == 0);
    CHECK(pq_result_clear(v) == 1);

    /* Malformed packets are rejected before PQexecParams. */
    pbegin(&p, "SELECT 1", 0, 0);
    p.n -= 2; /* truncate nparams */
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT 1", 2, 0); /* invalid result format */
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT $1", 0, 1);
    pparam(&p, 9, "x", 1); /* unknown kind */
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT $1", 0, 1);
    pparam(&p, 1, "a\0b", 3); /* text NUL */
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT $1", 0, 1);
    pparam(&p, 3, "12x", 3); /* invalid int text */
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT $1", 0, 1);
    pparam(&p, 4, "x", 1); /* invalid bool */
    if (run_bad_packet(conn, &p)) return 1;

    p.n = 0;
    p32(&p, 9);
    pbytes(&p, "SELECT 1\0", 9);
    p8(&p, 0);
    p32(&p, 0);
    if (run_bad_packet(conn, &p)) return 1;

    pbegin(&p, "SELECT 1", 0, 0);
    p8(&p, 0); /* trailing byte after a complete packet */
    if (run_bad_packet(conn, &p)) return 1;

    /* Allocation failure leaves no live result token. */
    pbegin(&p, "SELECT 1", 0, 0);
    b = wrap(&p);
    CHECK(b > 0);
    fake_set_fail_alloc(1);
    CHECK(pg_exec_typed(conn, b) == 0);
    fake_set_fail_alloc(0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(cres_live_kind(CRES_KIND_RESULT) == 0);

    /* A result outlives its connection in this adapter. */
    pbegin(&p, "SELECT 1", 0, 0);
    b = wrap(&p);
    CHECK(b > 0);
    res = pg_exec_typed(conn, b);
    CHECK(res > 0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(pq_conn_close(conn) == 1);
    CHECK(pq_conn_close(conn) == 0);
    CHECK(pq_conn_live(conn) == 0);
    CHECK(pq_ntuples(res) == 1);
    CHECK(pq_result_clear(res) == 1);
    CHECK(pq_result_clear(res) == 0);

    /* Failed connect still owns a token until explicitly closed. */
    cs = mkc("fake-fail");
    CHECK(cs > 0);
    conn = pg_open(cs);
    CHECK(conn > 0);
    CHECK(cres_buf_free(cs) == 1);
    CHECK(pq_conn_status(conn) == CONNECTION_BAD);
    v = pq_error_buf(conn);
    CHECK(v > 0);
    CHECK(cres_buf_len(v) > 0);
    CHECK(cres_buf_free(v) == 1);
    CHECK(pq_conn_close(conn) == 1);

    /* PQconnectdb returning NULL and PQexecParams returning NULL. */
    cs = mkc("fake-null");
    CHECK(cs > 0);
    CHECK(pg_open(cs) == 0);
    CHECK(cres_buf_free(cs) == 1);

    CHECK(fake_get_conn_live() == 0);
    CHECK(fake_get_result_live() == 0);
    CHECK(fake_get_finish_count() >= 2);
    CHECK(fake_get_clear_count() >= 4);
    CHECK(cres_live_count() == 0);
    printf("postgres typed adapter tests ok\n");
    return 0;
}
