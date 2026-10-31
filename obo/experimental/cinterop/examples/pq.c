/* The libpq development header was not installed on the probe host; this is the
 * documented prototype fallback: declare only the version function. No
 * connection, query or database access is performed. */
extern int PQlibVersion(void);
