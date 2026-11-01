/* ASan reproduction of the old SQLite adapter's release_stmt use-after-free.
 *
 * OLD_SHAPE frees the connection structure before the statement's release reads
 * it (`s->owner->stmt_live`, sqlite_adapter.c:136-143), so ASan reports a
 * heap-use-after-free. NEW_SHAPE finalizes the statement and closes the
 * connection independently, as the v2 Stmt (a bare sqlite3_stmt* with no owner)
 * does, and passes.
 *
 * Run: ./sqlite_uaf_fixture old|new
 */
#include <stdlib.h>
#include <string.h>
#include "sqlite_decls.h"

typedef struct old_conn {
    sqlite3 *db;
    long stmt_live;
} old_conn;

typedef struct old_stmt {
    sqlite3_stmt *stmt;
    old_conn *owner;
} old_stmt;

static void old_release_stmt(old_stmt *s) {
    if (s->stmt) sqlite3_finalize(s->stmt);
    if (s->owner && s->owner->stmt_live > 0) s->owner->stmt_live -= 1;
    free(s);
}

int main(int argc, char **argv) {
    sqlite3 *db = 0;
    sqlite3_stmt *st = 0;
    old_conn *c;
    old_stmt *s;
    if (argc != 2 || (strcmp(argv[1], "old") != 0 && strcmp(argv[1], "new") != 0)) {
        return 2;
    }
    if (sqlite3_open_v2(":memory:", &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, 0) != 0) {
        return 3;
    }
    if (sqlite3_prepare_v2(db, "SELECT 1", -1, &st, 0) != 0) {
        return 4;
    }
    c = (old_conn *)calloc(1, sizeof(*c));
    s = (old_stmt *)calloc(1, sizeof(*s));
    if (!c || !s) return 5;
    c->db = db;
    c->stmt_live = 1;
    s->stmt = st;
    s->owner = c;
    if (strcmp(argv[1], "old") == 0) {
        (void)sqlite3_close_v2(c->db);      /* old adapter's close shape */
        free(c);                            /* the owner is gone ... */
        old_release_stmt(s);                /* ... and release_stmt reads it */
    } else {
        sqlite3_finalize(s->stmt);          /* NEW_SHAPE: no owner pointer */
        free(s);
        (void)sqlite3_close_v2(c->db);
        free(c);
    }
    return 0;
}
