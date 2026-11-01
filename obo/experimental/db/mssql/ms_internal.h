/* Internal declarations shared by the mssql shim sources. */
#ifndef OBO_DB_MSSQL_INTERNAL_H
#define OBO_DB_MSSQL_INTERNAL_H

#include <stddef.h>
#include "mssql_decls.h"

/* A statement slot that must stay alive between SQLBindParameter and
 * SQLExecute. */
typedef struct ms_bind_slot {
    void *ptr;
    SQLLEN *ind;
    struct ms_bind_slot *next;
} ms_bind_slot;

/* One cached cell of the current row. mode 0 is UTF-8 text (SQL_C_CHAR) and
 * mode 1 is raw bytes (SQL_C_BINARY). */
typedef struct ms_cell {
    unsigned char *buf;
    int cap;
    int len;
    int status;   /* 0 value, 1 SQL NULL, -1 driver error, -2 over cap */
    int mode;
    int loaded;
} ms_cell;

/* The connection state. The foreign word points here, not at the raw SQLHDBC.
 * Children are freed and marked dead before the DBC is disconnected, so a
 * Rows that outlives its Connection can only return a clean error. */
struct ms_conn {
    SQLHDBC dbc;
    int closed;
    struct ms_stmt *children;
};

/* The statement state. The foreign word points here. owner is cleared when the
 * connection closes; h is SQL_NULL_HSTMT once the driver handle is gone. */
struct ms_stmt {
    SQLHSTMT h;
    struct ms_conn *owner;
    struct ms_stmt *next_sibling;
    int closed;
    int row_valid;
    int read_next;
    ms_cell *cells;
    int ncells;
    ms_bind_slot *binds;
    char *synthetic;
    int synthetic_code;
};

/* UTF-8 <-> UTF-16 (BMP and surrogate pairs). Both return 0 on success and
 * allocate the output with malloc; the caller frees it. A malformed input
 * returns -1 and a failed allocation returns -2. */
int ms_utf8_to_utf16(const void *src, int n, SQLWCHAR **out, int *units);
int ms_utf16_to_utf8(const SQLWCHAR *src, int units, char **out, int *bytes);

/* Strict UTF-8 validation without allocation: 1 valid, 0 invalid. */
int ms_utf8_check(const void *src, int n);

/* A process-wide growable text buffer used for one cstr_out copy at a time.
 * Returns NULL on allocation failure. The returned pointer is valid until the
 * next call. */
char *ms_text_reserve(size_t need);

/* The last error for an open failure (ms_open) and the generic diagnostic
 * formatter. All state is process-wide; the binding is single-threaded. */
void ms_set_last_error(const char *text, const char *state, int code);
const char *ms_last_error(void);
const char *ms_last_error_state(void);
int ms_last_error_code(void);

/* Format every diagnostic record of one handle into the growable text buffer.
 * Empty string when there are no records. */
const char *ms_handle_diag(SQLHANDLE h, int handle_type);
const char *ms_handle_sqlstate(SQLHANDLE h, int handle_type);
int ms_handle_error_code(SQLHANDLE h, int handle_type);

/* Connection-owned statement teardown: frees the HSTMT, clears the owner and
 * leaves a synthetic "statement's connection is closed" diagnostic in a state
 * the obo word still points at (freed later by ms_close_stmt). */
int ms_stmt_mark_closed(struct ms_stmt *s);

#endif
