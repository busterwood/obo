/* Exercise the real libpq conninfo parser used to decide whether a bounded
 * connect_timeout is already present. PQconnectdb is wrapped and returns NULL,
 * so this never opens a socket. */
#include "../toolkit/cres.h"
#include "../toolkit/libpq_adapter.h"

#include <stdio.h>
#include <string.h>

extern void *__real_PQconnectdb(const char *conninfo);
static char captured[8192];
static int failures = 0;

void *__wrap_PQconnectdb(const char *conninfo) {
    snprintf(captured, sizeof(captured), "%s", conninfo);
    return NULL; /* never connect */
}

static void check(int cond, const char *name) {
    if (!cond) {
        fprintf(stderr, "FAIL: %s (captured=%s)\n", name, captured);
        failures++;
    }
}

static void try_dsn(const char *dsn) {
    long t = cres_buf_from_bytes(dsn, strlen(dsn));
    long c;
    captured[0] = 0;
    c = pq_connect(t);
    cres_buf_free(t);
    if (c > 0) pq_conn_close(c);
}

int main(void) {
    try_dsn("dbname=postgres");
    check(strstr(captured, "connect_timeout=5") != NULL,
          "plain conninfo gets default timeout");

    try_dsn("dbname=postgres connect_timeout=3");
    check(strstr(captured, "connect_timeout=3") != NULL,
          "explicit timeout preserved");
    check(strstr(captured, "connect_timeout=5") == NULL,
          "default timeout not appended");

    try_dsn("dbname=postgres application_name='connect_timeout='");
    check(strstr(captured, "connect_timeout=5") != NULL,
          "quoted application value is not a timeout keyword");

    try_dsn("dbname=postgres password='connect_timeout='");
    check(strstr(captured, "connect_timeout=5") != NULL,
          "quoted password value is not a timeout keyword");

    try_dsn("postgresql:///postgres?user=chris");
    check(strstr(captured, "connect_timeout=5") != NULL &&
          strstr(captured, "&connect_timeout=5") != NULL,
          "URI conninfo gets an appended default timeout");

    captured[0] = 0;
    try_dsn("host");
    check(captured[0] == 0, "invalid conninfo rejected before PQconnectdb");

    if (failures != 0) {
        fprintf(stderr, "timeout parse tests: %d failure(s)\n", failures);
        return 1;
    }
    printf("timeout parse tests ok\n");
    return 0;
}
