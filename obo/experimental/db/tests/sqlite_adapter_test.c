/* Direct C tests for the experimental SQLite adapter.
 *
 * These tests use the real libsqlite3.so.0 and the declarations-only adapter
 * build. They cover ownership, bounds, NULL vs empty, embedded NUL in BLOB vs
 * text, error copying, statement/connection close ordering, double close and
 * stale tokens. Build with ASAN/UBSAN too; see test_db_adapters.py.
 */
#include "../../cinterop/toolkit/cres.h"
#include "../sqlite/sqlite_adapter.h"
#include "../sqlite/sqlite_decls.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); return 1; } } while (0)

static long mk(const void *data, size_t len) {
    return cres_buf_from_bytes(data, len);
}

static long mkc(const char *s) {
    return mk(s, strlen(s));
}

static int buf_eq_bytes(long tok, const unsigned char *want, size_t n) {
    unsigned char *p = NULL;
    size_t len = 0;
    if (!cres_buf_data(tok, &p, &len)) return 0;
    return len == n && (n == 0 || memcmp(p, want, n) == 0);
}

int main(void) {
    long path, sql, c, st, q, b;
    long live;
    unsigned char blob[3] = {0, 255, 65};
    const unsigned char want_text[] = "alice";
    char filepath[256];

    CHECK(cres_live_count() == 0);

    /* Open failure: the message is copied before the failed handle is closed. */
    path = mkc("/tmp/obo-db-sqlite-no-such-dir/file.db");
    CHECK(path > 0);
    CHECK(sq_open(path) == 0);
    CHECK(cres_buf_free(path) == 1);
    b = sq_open_error_buf();
    CHECK(b > 0);
    CHECK(cres_buf_len(b) > 0);
    CHECK(sq_open_error_code() != 0);
    CHECK(cres_buf_free(b) == 1);

    /* In-memory database and schema. */
    path = mkc(":memory:");
    CHECK(path > 0);
    c = sq_open(path);
    CHECK(c > 0);
    CHECK(cres_buf_free(path) == 1);
    CHECK(sq_conn_live(c) == 1);

    sql = mkc("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT, data BLOB)");
    CHECK(sql > 0);
    CHECK(sq_exec(c, sql) == 0);
    CHECK(cres_buf_free(sql) == 1);

    q = mkc("INSERT INTO t(id,name,note,data) VALUES(?1,?2,?3,?4)");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_stmt_live(st) == 1);

    /* 1-based bind indices; out-of-range is SQLITE_RANGE. */
    CHECK(sq_stmt_bind_int(st, 0, 1) == SQLITE_RANGE);
    CHECK(sq_stmt_bind_text(st, 99, st) == SQLITE_RANGE);

    CHECK(sq_stmt_bind_int(st, 1, 7) == SQLITE_OK);
    b = mkc("alice");
    CHECK(b > 0);
    CHECK(sq_stmt_bind_text(st, 2, b) == SQLITE_OK);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_bind_null(st, 3) == SQLITE_OK);
    b = mk(blob, sizeof(blob));
    CHECK(b > 0);
    CHECK(sq_stmt_bind_blob(st, 4, b) == SQLITE_OK);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_step(st) == SQLITE_DONE);

    /* Reset and insert a second row with an empty (not NULL) note. */
    CHECK(sq_stmt_reset(st) == SQLITE_OK);
    CHECK(sq_stmt_bind_int(st, 1, 8) == SQLITE_OK);
    b = mkc("bob");
    CHECK(b > 0);
    CHECK(sq_stmt_bind_text(st, 2, b) == SQLITE_OK);
    CHECK(cres_buf_free(b) == 1);
    b = mkc("");
    CHECK(b > 0);
    CHECK(sq_stmt_bind_text(st, 3, b) == SQLITE_OK);
    CHECK(cres_buf_free(b) == 1);
    b = mk(blob, sizeof(blob));
    CHECK(b > 0);
    CHECK(sq_stmt_bind_blob(st, 4, b) == SQLITE_OK);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_step(st) == SQLITE_DONE);
    CHECK(sq_stmt_close(st) == 1);
    CHECK(sq_stmt_close(st) == 0);

    /* SELECT: exact i64, NULL vs empty, BLOB with NUL, stale-column bounds. */
    q = mkc("SELECT id,name,note,data FROM t WHERE id=?1");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_stmt_bind_int(st, 1, 7) == SQLITE_OK);
    CHECK(sq_stmt_step(st) == SQLITE_ROW);
    CHECK(sq_stmt_column_count(st) == 4);
    CHECK(sq_stmt_column_type(st, 0) == SQLITE_INTEGER);
    CHECK(sq_stmt_column_int(st, 0) == 7);
    CHECK(sq_stmt_column_type(st, 1) == SQLITE_TEXT);
    b = sq_stmt_column_text_buf(st, 1);
    CHECK(b > 0);
    CHECK(buf_eq_bytes(b, want_text, sizeof(want_text) - 1));
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_column_type(st, 2) == SQLITE_NULL);
    CHECK(sq_stmt_column_text_buf(st, 2) == 0);
    CHECK(sq_stmt_column_type(st, 3) == SQLITE_BLOB);
    b = sq_stmt_column_blob_buf(st, 3);
    CHECK(b > 0);
    CHECK(buf_eq_bytes(b, blob, sizeof(blob)));
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_column_type(st, 99) == -1);
    CHECK(sq_stmt_column_text_buf(st, 99) == -1);
    CHECK(sq_stmt_step(st) == SQLITE_DONE);
    CHECK(sq_stmt_close(st) == 1);

    /* Empty text is non-NULL: cell type TEXT with zero length. */
    q = mkc("SELECT note FROM t WHERE id=8");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_stmt_step(st) == SQLITE_ROW);
    CHECK(sq_stmt_column_type(st, 0) == SQLITE_TEXT);
    b = sq_stmt_column_text_buf(st, 0);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) == 0);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_close(st) == 1);

    /* A TEXT value can contain NUL in storage; exact bytes preserve it even
     * though the string convenience layer will reject it. */
    q = mkc("SELECT 'a'||char(0)||'b'");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_stmt_step(st) == SQLITE_ROW);
    CHECK(sq_stmt_column_type(st, 0) == SQLITE_TEXT);
    b = sq_stmt_column_text_buf(st, 0);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) == 3);
    CHECK(cres_buf_free(b) == 1);
    b = sq_stmt_column_blob_buf(st, 0);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) == 3);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_close(st) == 1);

    /* SQL and TEXT embedded NUL are rejected; BLOB NUL is preserved above. */
    sql = mk("SELECT 1\0x", 10);
    CHECK(sql > 0);
    CHECK(sq_exec(c, sql) == -1);
    CHECK(cres_buf_free(sql) == 1);
    b = sq_error_buf(c);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) > 0);
    CHECK(cres_buf_free(b) == 1);

    q = mkc("UPDATE t SET note=?1 WHERE id=7");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    b = mk("a\0b", 3);
    CHECK(b > 0);
    CHECK(sq_stmt_bind_text(st, 1, b) == -1);
    CHECK(cres_buf_free(b) == 1);
    CHECK(sq_stmt_error_code(st) == -1);
    CHECK(sq_stmt_close(st) == 1);

    /* Constraint failure copies its message/code. */
    sql = mkc("INSERT INTO t(id,name) VALUES(7,'duplicate')");
    CHECK(sql > 0);
    CHECK(sq_exec(c, sql) != SQLITE_OK);
    CHECK(cres_buf_free(sql) == 1);
    CHECK(sq_error_code(c) != 0);
    b = sq_error_buf(c);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) > 0);
    CHECK(cres_buf_free(b) == 1);

    /* prepare accepts exactly one statement. */
    q = mkc("SELECT 1; SELECT 2");
    CHECK(q > 0);
    CHECK(sq_prepare(c, q) == 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_error_code(c) == 1);

    /* Connection close fails while a statement is live, then succeeds. */
    q = mkc("SELECT 1");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_conn_close(c) == 0);
    CHECK(sq_error_code(c) == SQLITE_BUSY);
    CHECK(sq_stmt_close(st) == 1);
    CHECK(sq_conn_close(c) == 1);
    CHECK(sq_conn_close(c) == 0);
    CHECK(sq_conn_live(c) == 0);
    CHECK(sq_stmt_column_count(st) == -1);

    /* Real tempfile under /tmp, then remove it. */
    snprintf(filepath, sizeof(filepath), "/tmp/opencode/obo-db-sqlite-%ld.db",
             (long)getpid());
    unlink(filepath);
    path = mkc(filepath);
    CHECK(path > 0);
    c = sq_open(path);
    CHECK(c > 0);
    CHECK(cres_buf_free(path) == 1);
    sql = mkc("CREATE TABLE f(v INTEGER); INSERT INTO f VALUES(42)");
    CHECK(sql > 0);
    CHECK(sq_exec(c, sql) == 0);
    CHECK(cres_buf_free(sql) == 1);
    CHECK(sq_changes(c) == 1);
    q = mkc("SELECT v FROM f");
    CHECK(q > 0);
    st = sq_prepare(c, q);
    CHECK(st > 0);
    CHECK(cres_buf_free(q) == 1);
    CHECK(sq_stmt_step(st) == SQLITE_ROW);
    CHECK(sq_stmt_column_int(st, 0) == 42);
    CHECK(sq_stmt_step(st) == SQLITE_DONE);
    CHECK(sq_stmt_close(st) == 1);
    CHECK(sq_conn_close(c) == 1);
    unlink(filepath);

    /* Library errstr is available without a connection. */
    b = sq_errstr_buf(SQLITE_BUSY);
    CHECK(b > 0);
    CHECK(cres_buf_len(b) > 0);
    CHECK(cres_buf_free(b) == 1);

    live = cres_live_count();
    CHECK(live == 0);
    CHECK(cres_live_kind(CRES_KIND_CONN) == 0);
    CHECK(cres_live_kind(CRES_KIND_STMT) == 0);
    printf("sqlite adapter tests ok\n");
    return 0;
}
