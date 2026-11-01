/* Hosted-entry ABI for the experimental cinterop bridge.
 *
 * The baseline freestanding runtime is unchanged. The cinterop build compiles a
 * separate hosted runtime with -DOBO_HOSTED_EXIT; that variant appends one
 * jump-table slot after every existing slot. The compiler's RT_* constants and
 * the freestanding runtime blobs do not change.
 *
 * The cinterop build validates that entry.S still has exactly this many slots
 * before the hosted exit slot (59 existing slots -> index 59). If a future
 * runtime adds a slot, update this constant and the build check together.
 */
#ifndef OBO_RUNTIME_HOST_ABI_H
#define OBO_RUNTIME_HOST_ABI_H

#include <stdint.h>

#define RT_TEXT_BASE 0x10000000ULL
#define RT_HOST_EXIT_SLOT 59
#define RT_HOST_EXIT_ADDR (RT_TEXT_BASE + 8ULL * RT_HOST_EXIT_SLOT)

/* The raw runtime blobs carry no symbols, so hosted helpers reach the fixed jump table directly.
 * rt_alloc_record is slot 2 and rt_alloc_array slot 3 in runtime/entry.S (rt_init 0, rt_exit 1).
 * They allocate normal obo objects, so live_owned counts them and OBO_CHECK=1 leak checking applies
 * as usual. */
#define RT_ALLOC_RECORD_SLOT 2
#define RT_ALLOC_RECORD_ADDR (RT_TEXT_BASE + 8ULL * RT_ALLOC_RECORD_SLOT)
#define RT_ALLOC_ARRAY_SLOT 3
#define RT_ALLOC_ARRAY_ADDR (RT_TEXT_BASE + 8ULL * RT_ALLOC_ARRAY_SLOT)

/* The hosted runtime appends a foreign-census callback slot after the last existing slot (94), only
 * under OBO_HOSTED_EXIT. The baseline build has no slot 95 at all, so its bytes are unchanged. */
#define RT_FOREIGN_CENSUS_SLOT 95
#define RT_FOREIGN_CENSUS_ADDR (RT_TEXT_BASE + 8ULL * RT_FOREIGN_CENSUS_SLOT)

typedef void (*rt_host_exit_fn)(long code);
typedef uint64_t (*rt_alloc_record_fn)(const uint64_t *desc);
typedef uint64_t (*rt_alloc_array_fn)(int64_t n, uint64_t kind);
typedef void (*rt_foreign_census_fn)(void);
typedef void (*rt_set_foreign_census_fn)(rt_foreign_census_fn fn);

#endif
