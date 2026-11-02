/* The one libpq C helper kept by the v2 binding: ask libpq's own conninfo parser
 * whether connect_timeout is present. This avoids hand-rolling a parser and
 * mistaking a quoted value such as application_name='connect_timeout=' for a
 * keyword. */
#ifndef OBO_CINTEROP_LIBPQ_CONNINFO_H
#define OBO_CINTEROP_LIBPQ_CONNINFO_H

/* 1: connect_timeout is present, 0: absent, -1: invalid conninfo. */
int pq_conninfo_has_timeout(const char *conninfo);

/* The diagnostic of the last invalid parse, or "" when none. Static storage;
 * valid until the next pq_conninfo_has_timeout call. */
const char *pq_conninfo_error(void);

#endif
