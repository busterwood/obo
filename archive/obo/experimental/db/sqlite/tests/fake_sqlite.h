/* Fake libsqlite3 for the cinterop v2 binding tests.
 *
 * The SQL text selects the knobs: "bad ..." fails prepare with a non-NULL
 * *ppStmt, "SELECT resetfail" fails step and returns that error from reset,
 * "SELECT nomem" makes column_text return NULL with SQLITE_NOMEM, and the
 * "SELECT live/closed/finalized/zombies/last_dtor/last_len" counters expose the
 * fake's bookkeeping. close_v2 keeps a zombie while statements are live. */
#ifndef OBO_DB_SQLITE_FAKE_H
#define OBO_DB_SQLITE_FAKE_H

#include "sqlite_decls.h"

#endif
