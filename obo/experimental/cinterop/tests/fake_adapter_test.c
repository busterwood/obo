/* Deterministic C-side tests for the resource registry and libpq adapter.
 * They link the fake libpq implementation, never a real server or libpq. */
#include "../toolkit/cres.h"
#include "../toolkit/libpq_adapter.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern long fake_get_conn_live(void);
extern long fake_get_result_live(void);
extern long fake_get_finish_count(void);
extern long fake_get_clear_count(void);
extern long fake_get_exec_count(void);
extern long fake_get_make_empty_count(void);
extern void fake_set_fail_alloc(long v);
extern long fake_last_param_count(void);
extern long fake_last_param_isnull(long i);
extern long fake_last_param_len(long i);
extern long fake_last_param_format(long i);
extern long fake_last_param_byte(long i, long j);
extern long fake_last_sql_len(void);
extern long fake_last_sql_byte(long i);

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", msg); failures++; } \
} while (0)

static long make_buf(const unsigned char *p, size_t n) {
    long t = cres_buf_new((long)n);
    size_t i;
    if (t == 0) return 0;
    for (i = 0; i < n; i++) {
        if (cres_buf_set(t, (long)i, p[i]) != 1) {
            cres_buf_free(t);
            return 0;
        }
    }
    return t;
}

static long make_cstr(const char *s) {
    return make_buf((const unsigned char *)s, strlen(s));
}

typedef struct {
    unsigned char *p;
    size_t len;
    size_t cap;
} packet_builder;

static void pb_init(packet_builder *pb, size_t cap) {
    pb->p = (unsigned char *)malloc(cap);
    pb->len = 0;
    pb->cap = cap;
}

static void pb_need(packet_builder *pb, size_t n) {
    if (pb->len + n > pb->cap) {
        fprintf(stderr, "FAIL: packet builder overflow\n");
        exit(2);
    }
}

static void pb_u8(packet_builder *pb, unsigned int v) {
    pb_need(pb, 1);
    pb->p[pb->len++] = (unsigned char)v;
}

static void pb_u32(packet_builder *pb, unsigned long v) {
    pb_need(pb, 4);
    pb->p[pb->len++] = (unsigned char)(v & 255);
    pb->p[pb->len++] = (unsigned char)((v >> 8) & 255);
    pb->p[pb->len++] = (unsigned char)((v >> 16) & 255);
    pb->p[pb->len++] = (unsigned char)((v >> 24) & 255);
}

static void pb_bytes(packet_builder *pb, const unsigned char *p, size_t n) {
    pb_need(pb, n);
    if (n != 0) memcpy(pb->p + pb->len, p, n);
    pb->len += n;
}

static long pb_finish(packet_builder *pb) {
    long t = make_buf(pb->p, pb->len);
    free(pb->p);
    pb->p = NULL;
    return t;
}

static long packet(const char *sql, int result_format,
                   int nparams, const int *kinds,
                   const unsigned char **values, const int *lens) {
    packet_builder pb;
    int i;
    pb_init(&pb, 4096);
    pb_u32(&pb, (unsigned long)strlen(sql));
    pb_bytes(&pb, (const unsigned char *)sql, strlen(sql));
    pb_u8(&pb, (unsigned int)result_format);
    pb_u32(&pb, (unsigned long)nparams);
    for (i = 0; i < nparams; i++) {
        pb_u8(&pb, (unsigned int)kinds[i]);
        pb_u32(&pb, (unsigned long)(kinds[i] == 0 ? 0 : lens[i]));
        if (kinds[i] != 0) pb_bytes(&pb, values[i], (size_t)lens[i]);
    }
    return pb_finish(&pb);
}

static long packet_sql_bytes(const unsigned char *sql, size_t sql_len,
                             int result_format, int nparams,
                             const int *kinds, const unsigned char **values,
                             const int *lens) {
    packet_builder pb;
    int i;
    pb_init(&pb, 4096);
    pb_u32(&pb, (unsigned long)sql_len);
    pb_bytes(&pb, sql, sql_len);
    pb_u8(&pb, (unsigned int)result_format);
    pb_u32(&pb, (unsigned long)nparams);
    for (i = 0; i < nparams; i++) {
        pb_u8(&pb, (unsigned int)kinds[i]);
        pb_u32(&pb, (unsigned long)(kinds[i] == 0 ? 0 : lens[i]));
        if (kinds[i] != 0) pb_bytes(&pb, values[i], (size_t)lens[i]);
    }
    return pb_finish(&pb);
}

static long buf_string(long token, char *out, size_t cap) {
    unsigned char *p = NULL;
    size_t n = 0, i;
    if (!cres_buf_data(token, &p, &n)) return 0;
    if (n + 1 > cap) return 0;
    for (i = 0; i < n; i++) out[i] = (char)p[i];
    out[n] = 0;
    return (long)n;
}

static void test_registry(void) {
    long before = cres_live_count();
    long t = cres_buf_new(4);
    long t2;
    CHECK(t > 0, "buffer allocation");
    CHECK(cres_buf_len(t) == 4, "buffer length");
    CHECK(cres_buf_set(t, 0, 65) == 1, "buffer set");
    CHECK(cres_buf_get(t, 0) == 65, "buffer get");
    CHECK(cres_buf_set(t, 4, 1) == 0, "buffer set bound");
    CHECK(cres_buf_get(t, 4) == -1, "buffer get bound");
    CHECK(cres_buf_set(t, 0, 256) == 0, "buffer byte bound");
    t2 = cres_buf_new(4);
    CHECK(t2 > t, "token generation/index changes");
    CHECK(cres_buf_free(t) == 1, "buffer close");
    CHECK(cres_buf_free(t) == 0, "double close");
    CHECK(cres_buf_get(t, 0) == -1, "stale token rejected");
    CHECK(cres_buf_set(t2, 0, 66) == 1, "live token remains valid");
    CHECK(cres_buf_free(t2) == 1, "second buffer close");
    CHECK(cres_buf_new(CRES_MAX_BUFFER + 1) == 0, "oversized buffer rejected");
    CHECK(cres_buf_new(-1) == 0, "negative buffer rejected");
    CHECK(cres_live_count() == before, "registry clean after buffer tests");
}

static void test_connect_and_result(void) {
    char out[256];
    const char *sql = "FAKE_OK";
    const unsigned char *vals[3];
    int kinds[3] = {1, 0, 2};
    int lens[3] = {5, 0, 2};
    unsigned char text[] = {'h', 'e', 'l', 'l', 'o'};
    unsigned char binary[] = {0, 255};
    long dsn, conn, pkt, res, v;
    long finish0 = fake_get_finish_count();
    long clear0 = fake_get_clear_count();

    vals[0] = text;
    vals[1] = NULL;
    vals[2] = binary;
    dsn = make_cstr("fake-ok");
    CHECK(dsn > 0, "dsn buffer");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    CHECK(conn > 0, "fake connect");
    CHECK(pq_conn_status(conn) == 0, "connection status ok");
    CHECK(pq_conn_live(conn) == 1, "connection live");

    pkt = packet(sql, 0, 3, kinds, vals, lens);
    CHECK(pkt > 0, "query packet");
    res = pq_exec_packet(conn, pkt);
    cres_buf_free(pkt);
    CHECK(res > 0, "fake query result");
    CHECK(pq_result_status(res) == 2, "tuples status");
    CHECK(pq_ntuples(res) == 1, "one tuple");
    CHECK(pq_nfields(res) == 4, "four fields");
    CHECK(fake_last_param_count() == 3, "fake saw three params");
    CHECK(fake_last_param_isnull(0) == 0, "text param not null");
    CHECK(fake_last_param_len(0) == 5, "text param length");
    CHECK(fake_last_param_format(0) == 0, "text param format");
    CHECK(fake_last_param_isnull(1) == 1, "NULL param distinct");
    CHECK(fake_last_param_isnull(2) == 0, "binary param not null");
    CHECK(fake_last_param_len(2) == 2, "binary length preserved");
    CHECK(fake_last_param_format(2) == 1, "binary format");
    CHECK(fake_last_param_byte(2, 0) == 0, "binary NUL preserved");
    CHECK(fake_last_param_byte(2, 1) == 255, "binary 0xff preserved");

    CHECK(pq_getisnull(res, 0, 0) == 0, "text value not null");
    v = pq_value_buf(res, 0, 0);
    CHECK(v > 0, "text value buffer");
    CHECK(buf_string(v, out, sizeof(out)) == 5 && strcmp(out, "hello") == 0, "text value");
    cres_buf_free(v);
    CHECK(pq_getisnull(res, 0, 1) == 1, "NULL cell");
    CHECK(pq_getlength(res, 0, 1) == 0, "NULL length zero");
    CHECK(pq_value_buf(res, 0, 1) == 0, "NULL has no value buffer");
    CHECK(pq_getisnull(res, 0, 2) == 0, "binary cell not null");
    CHECK(pq_getlength(res, 0, 2) == 2, "binary cell length");
    v = pq_value_buf(res, 0, 2);
    CHECK(v > 0, "binary value buffer");
    CHECK(cres_buf_len(v) == 2, "binary buffer length");
    CHECK(cres_buf_get(v, 0) == 0 && cres_buf_get(v, 1) == 255, "binary bytes");
    cres_buf_free(v);
    CHECK(pq_getisnull(res, 9, 0) == -1, "row bound rejected");
    CHECK(pq_getisnull(res, 0, 9) == -1, "column bound rejected");
    CHECK(pq_getlength(res, -1, 0) == -1, "negative row rejected");

    CHECK(pq_ftype(res, 3) == 1700, "numeric OID preserved");
    CHECK(pq_fmod(res, 3) == -1, "numeric typmod preserved");
    v = pq_fname_buf(res, 3);
    CHECK(v > 0, "field name buffer");
    CHECK(buf_string(v, out, sizeof(out)) == 3 && strcmp(out, "num") == 0, "field name");
    cres_buf_free(v);
    v = pq_value_buf(res, 0, 3);
    CHECK(v > 0, "numeric text buffer");
    CHECK(buf_string(v, out, sizeof(out)) == 20 && strcmp(out, "1.234567890123456789") == 0,
          "exact numeric text not a float");
    cres_buf_free(v);

    /* PGresult outlives PGconn. */
    CHECK(pq_conn_close(conn) == 1, "connection close");
    CHECK(pq_conn_close(conn) == 0, "connection double close");
    CHECK(fake_get_finish_count() == finish0 + 1, "PQfinish once");
    CHECK(pq_result_status(res) == 2, "result survives connection close");
    CHECK(pq_result_clear(res) == 1, "result clear");
    CHECK(pq_result_clear(res) == 0, "result double clear");
    CHECK(fake_get_clear_count() == clear0 + 1, "PQclear once");
    CHECK(pq_result_status(res) == -1, "stale result rejected");
    CHECK(cres_live_count() == 0, "no resources after result lifetime test");
}

static void test_errors_and_allocation(void) {
    char out[256];
    long dsn, conn, pkt, res, v;
    const char *sql = "FAKE_ERROR";
    long clear0 = fake_get_clear_count();

    dsn = make_cstr("fake-ok");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    CHECK(conn > 0, "connect for error");
    pkt = packet(sql, 0, 0, NULL, NULL, NULL);
    res = pq_exec_packet(conn, pkt);
    cres_buf_free(pkt);
    CHECK(res > 0, "error result token");
    CHECK(pq_result_status(res) == 7, "error status");
    v = pq_result_error_buf(res);
    CHECK(v > 0, "error message buffer");
    CHECK(buf_string(v, out, sizeof(out)) > 0 && strstr(out, "fake query failed") != NULL,
          "error message copied");
    cres_buf_free(v);
    CHECK(pq_result_clear(res) == 1, "error result clear");
    CHECK(fake_get_clear_count() == clear0 + 1, "error result PQclear once");
    CHECK(pq_conn_close(conn) == 1, "error connection close");

    /* NULL connection. */
    dsn = make_cstr("fake-null");
    CHECK(pq_connect(dsn) == 0, "NULL connection rejected");
    cres_buf_free(dsn);
    CHECK(cres_live_count() == 0, "no leak after NULL connection");

    /* BAD connection still owns a PGconn and closes exactly once. */
    dsn = make_cstr("fake-fail");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    CHECK(conn > 0, "bad connection token");
    CHECK(pq_conn_status(conn) == 1, "bad connection status");
    v = pq_error_buf(conn);
    CHECK(v > 0, "bad connection error buffer");
    CHECK(buf_string(v, out, sizeof(out)) > 0 && strstr(out, "fake connection failure") != NULL,
          "bad connection message");
    cres_buf_free(v);
    CHECK(pq_conn_close(conn) == 1, "bad connection close");

    /* Allocation failure and malformed packets fail closed. */
    dsn = make_cstr("fake-ok");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    fake_set_fail_alloc(1);
    pkt = packet("FAKE_OK", 0, 0, NULL, NULL, NULL);
    res = pq_exec_packet(conn, pkt);
    cres_buf_free(pkt);
    fake_set_fail_alloc(0);
    CHECK(res == 0, "PQexecParams allocation failure returns no token");
    CHECK(cres_live_count() == 1, "only the connection is live after allocation failure");

    pkt = packet("FAKE_OK", 2, 0, NULL, NULL, NULL);
    CHECK(pq_exec_packet(conn, pkt) == -2, "bad result format rejected");
    cres_buf_free(pkt);
    {
        const unsigned char nul_sql[] = {'F', 'A', 'K', 'E', 0, 'O', 'K'};
        pkt = packet_sql_bytes(nul_sql, sizeof(nul_sql), 0, 0, NULL, NULL, NULL);
        CHECK(pq_exec_packet(conn, pkt) == -2, "SQL NUL rejected");
        cres_buf_free(pkt);
    }
    CHECK(pq_conn_close(conn) == 1, "cleanup after malformed packets");
    CHECK(cres_live_count() == 0, "no leak after error paths");
}

static void test_empty_and_forgery(void) {
    long empty0 = fake_get_make_empty_count();
    long r = pq_make_empty_result(2);
    long conn, dsn, pkt;
    CHECK(r > 0, "empty result token");
    CHECK(fake_get_make_empty_count() == empty0 + 1, "PQmakeEmptyPGresult once");
    CHECK(pq_result_status(r) == 2, "empty result status");
    CHECK(pq_ntuples(r) == 0 && pq_nfields(r) == 0, "empty result shape");
    CHECK(pq_result_clear(r) == 1, "empty result clear");

    dsn = make_cstr("fake-ok");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    pkt = packet("FAKE_OK", 0, 0, NULL, NULL, NULL);
    r = pq_exec_packet(conn, pkt);
    cres_buf_free(pkt);
    CHECK(r > 0, "result for forgery checks");
    CHECK(pq_conn_close(conn) == 1, "forgery connection close");
    /* Kind confusion: a result token cannot be closed as a connection. */
    CHECK(pq_conn_close(r) == 0, "wrong-kind close rejected");
    CHECK(pq_conn_status(r) == -1, "wrong-kind status rejected");
    CHECK(pq_result_live(r) == 1, "wrong-kind probe left result live");
    CHECK(pq_result_clear(r) == 1, "real result still closes");
    CHECK(pq_result_clear(r) == 0, "result poison");
    CHECK(cres_live_count() == 0, "no leak after forgery checks");
}

static void test_injection_is_data(void) {
    const char *sql = "FAKE_OK";
    const char *attack = "x'); DROP TABLE nope; --";
    const unsigned char *vals[1];
    int kinds[1] = {1};
    int lens[1];
    long dsn, conn, pkt, res, v;
    char out[256];
    vals[0] = (const unsigned char *)attack;
    lens[0] = (int)strlen(attack);
    dsn = make_cstr("fake-ok");
    conn = pq_connect(dsn);
    cres_buf_free(dsn);
    pkt = packet(sql, 0, 1, kinds, vals, lens);
    res = pq_exec_packet(conn, pkt);
    cres_buf_free(pkt);
    CHECK(res > 0, "injection query result");
    {
        int i;
        int same = fake_last_sql_len() == 7;
        for (i = 0; i < 7 && same; i++) {
            if (fake_last_sql_byte(i) != (long)(unsigned char)sql[i]) same = 0;
        }
        CHECK(same, "SQL text is the fixed statement");
    }
    CHECK(fake_last_param_len(0) == lens[0], "attack param length");
    v = pq_value_buf(res, 0, 0);
    CHECK(v > 0, "attack value copied back");
    CHECK(buf_string(v, out, sizeof(out)) == lens[0] && strcmp(out, attack) == 0,
          "attack string is data, not SQL");
    cres_buf_free(v);
    CHECK(pq_result_clear(res) == 1, "injection result close");
    CHECK(pq_conn_close(conn) == 1, "injection connection close");
    CHECK(cres_live_count() == 0, "no leak after injection test");
}

int main(void) {
    test_registry();
    test_connect_and_result();
    test_errors_and_allocation();
    test_empty_and_forgery();
    test_injection_is_data();
    if (failures != 0) {
        fprintf(stderr, "fake adapter tests: %d failure(s)\n", failures);
        return 1;
    }
    printf("fake adapter tests ok\n");
    return 0;
}
