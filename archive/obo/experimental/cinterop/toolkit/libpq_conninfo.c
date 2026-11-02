/* The one libpq C helper kept by the v2 binding. */
#include "libpq_conninfo.h"
#include "libpq_decls.h"

#include <stdio.h>
#include <string.h>

static char last_error[256];

int pq_conninfo_has_timeout(const char *conninfo) {
    PQconninfoOption *opts;
    char *err = NULL;
    int found = 0;
    last_error[0] = '\0';
    if (conninfo == NULL) {
        snprintf(last_error, sizeof last_error, "%s", "invalid connection string");
        return -1;
    }
    opts = PQconninfoParse(conninfo, &err);
    if (opts == NULL) {
        snprintf(last_error, sizeof last_error, "%s",
                 err != NULL ? err : "invalid connection string");
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

const char *pq_conninfo_error(void) {
    return last_error;
}
