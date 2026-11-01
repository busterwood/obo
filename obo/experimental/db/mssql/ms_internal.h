/* Internal declarations shared by the mssql shim sources. */
#ifndef OBO_DB_MSSQL_INTERNAL_H
#define OBO_DB_MSSQL_INTERNAL_H

#include <stddef.h>
#include "mssql_decls.h"

/* UTF-8 <-> UTF-16 (BMP and surrogate pairs). Both return 0 on success and
 * allocate the output with malloc; the caller frees it. A malformed input
 * returns -1 and a failed allocation returns -2. */
int ms_utf8_to_utf16(const void *src, int n, SQLWCHAR **out, int *units);
int ms_utf16_to_utf8(const SQLWCHAR *src, int units, char **out, int *bytes);

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

#endif
