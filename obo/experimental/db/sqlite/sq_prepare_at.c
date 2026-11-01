/* Trusted helper: prepare from an offset in the caller's SQL bytes.
 *
 * exec() walks a multi-statement script by advancing an absolute offset. Copying
 * the remaining tail each time would make it O(n^2); this helper calls
 * sqlite3_prepare_v2(db, sql + off, n - off, ...) and returns the tail as an
 * absolute pointer, which the compiler's out_offset@sql turns into an absolute
 * offset. The pointer arithmetic never leaves C. */
#include "sqlite_decls.h"

int sq_prepare_at(sqlite3 *db, const void *sql, int n, int off,
                  sqlite3_stmt **st, const char **tail) {
    const char *p = 0;
    int rc;
    if (!st) return SQLITE_MISUSE;
    *st = 0;
    if (tail) *tail = 0;
    if (off < 0 || off > n) return SQLITE_MISUSE;
    rc = sqlite3_prepare_v2(db, (const char *)sql + off, n - off, st, &p);
    if (tail) *tail = p;
    return rc;
}
