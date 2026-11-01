/* ASan regression harness for B1: a statement outliving its connection.
 *
 * This links the shim sources directly (no obo image) and reproduces the
 * live-server sequence open -> exec -> fetch -> text -> close connection,
 * then calls stmt shims on the orphaned statement. A correct build returns a
 * clean diagnostic and exits 0; a use-after-free aborts under ASan.
 *
 * The DSN comes from MSSQL_TEST_DSN and is never printed.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mssql_decls.h"

int main(void) {
    const char *dsn = getenv("MSSQL_TEST_DSN");
    const char *sql = "SELECT 1";
    ms_conn *c;
    ms_stmt *s = NULL;
    int rc;

    if (!dsn || !*dsn) {
        fprintf(stderr, "MSSQL_TEST_DSN is not set\n");
        return 77;
    }
    c = ms_open(dsn, (int)strlen(dsn));
    if (!c) {
        fprintf(stderr, "open failed: %s\n", ms_open_error());
        return 1;
    }
    rc = ms_exec_direct(c, sql, (int)strlen(sql), &s);
    if (rc < 0 || !s) {
        fprintf(stderr, "exec failed: %s\n", ms_open_error());
        ms_close_conn(c);
        return 2;
    }
    rc = ms_fetch(s);
    if (rc != SQL_SUCCESS && rc != SQL_SUCCESS_WITH_INFO) {
        fprintf(stderr, "fetch rc=%d\n", rc);
        ms_close_stmt(s);
        ms_close_conn(c);
        return 3;
    }
    rc = ms_read_cell(s, 0, 0);
    if (rc != 0) {
        fprintf(stderr, "read rc=%d diag=%s\n", rc, ms_stmt_diag(s));
        ms_close_stmt(s);
        ms_close_conn(c);
        return 4;
    }
    if (!ms_cell_value(s, 0, 0) || ms_cell_len(s, 0, 0) != 1 ||
        ((const char *)ms_cell_value(s, 0, 0))[0] != '1') {
        fprintf(stderr, "unexpected value\n");
        ms_close_stmt(s);
        ms_close_conn(c);
        return 5;
    }

    rc = ms_close_conn(c);
    if (rc != 0) {
        fprintf(stderr, "close connection rc=%d\n", rc);
        return 6;
    }
    if (ms_stmt_live(s) != 0) {
        fprintf(stderr, "statement reported live after connection close\n");
        return 7;
    }
    if (ms_num_cols(s) != -1) {
        fprintf(stderr, "column count on orphaned statement did not fail\n");
        return 8;
    }
    if (ms_read_cell(s, 0, 0) != -1) {
        fprintf(stderr, "cell read on orphaned statement did not fail\n");
        return 9;
    }
    {
        const char *diag = ms_stmt_diag(s);
        if (!diag || !strstr(diag, "connection is closed")) {
            fprintf(stderr, "missing clean diagnostic: %s\n",
                    diag ? diag : "(null)");
            return 10;
        }
    }
    if (ms_stmt_error_code(s) != 2) {
        fprintf(stderr, "unexpected native code %d\n", ms_stmt_error_code(s));
        return 11;
    }
    if (strcmp(ms_stmt_sqlstate(s), "08003") != 0) {
        fprintf(stderr, "unexpected SQLSTATE %s\n", ms_stmt_sqlstate(s));
        return 12;
    }
    ms_close_stmt(s);
    printf("ms_uaf_test=ok\n");
    return 0;
}
