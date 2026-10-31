#ifndef OBO_ZLIB_SYSTEM_H
#define OBO_ZLIB_SYSTEM_H

/* The system zlib header is not copied into the repository. This stable
 * repository-relative entry point lets the committed binding avoid an
 * absolute /usr/include path; the compiler's normal system include path
 * resolves <zlib.h> and the dependency scan hashes it. */
#include <zlib.h>

#endif
