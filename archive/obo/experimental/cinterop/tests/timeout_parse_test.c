/* Exercise the real libpq conninfo parser used to decide whether a bounded
 * connect_timeout is already present. It calls only PQconninfoParse, so this
 * never opens a socket. */
#include "../toolkit/libpq_conninfo.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

static void check(int cond, const char *name) {
    if (!cond) {
        fprintf(stderr, "FAIL: %s\n", name);
        failures++;
    }
}

static void expect(const char *dsn, int want, const char *name) {
    int got = pq_conninfo_has_timeout(dsn);
    if (got != want) {
        fprintf(stderr, "FAIL: %s (got %d, want %d)\n", name, got, want);
        failures++;
    }
}

int main(void) {
    expect("dbname=postgres", 0, "plain conninfo has no timeout");
    expect("dbname=postgres connect_timeout=3", 1, "explicit timeout preserved");
    expect("dbname=postgres connect_timeout=0", 1,
           "explicit connect_timeout=0 counts as present");
    expect("dbname=postgres application_name='connect_timeout='", 0,
           "quoted application value is not a timeout keyword");
    expect("dbname=postgres password='connect_timeout='", 0,
           "quoted password value is not a timeout keyword");
    expect("postgresql:///postgres?user=chris", 0, "URI conninfo has no timeout");
    expect("postgresql:///postgres?user=chris&connect_timeout=3", 1,
           "URI conninfo keeps an explicit timeout");
    expect("host", -1, "invalid conninfo is rejected");
    expect("host='unterminated", -1, "unterminated quote is rejected");
    check(strstr(pq_conninfo_error(), "unterminated") != NULL,
          "invalid parse copies libpq's diagnostic");

    if (failures != 0) {
        fprintf(stderr, "timeout parse tests: %d failure(s)\n", failures);
        return 1;
    }
    printf("timeout parse tests ok\n");
    return 0;
}
