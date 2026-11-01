/* ABI between host.c and the generated per-binding foreign table (foreign_table.c).
 *
 * The table is generated from the spec `types` in sidecar order, so its index is the compiler's
 * foreign type id. host.c keeps one live-handle counter per entry and calls the typed release
 * wrapper only from close. */
#ifndef OBO_CI_FOREIGN_ABI_H
#define OBO_CI_FOREIGN_ABI_H

#define OBO_CI_FOREIGN_MAX 64

/* A typed wrapper generated for each release symbol: it calls the real release with the correct
 * prototype and returns 0 for a void release or the int result for an int release. */
typedef long (*obo_ci_foreign_release_fn)(void *p);

typedef struct {
    const char *name;                     /* the obo type name, for the census message */
    obo_ci_foreign_release_fn release;
    int release_result;                   /* 0: void or discard (result ignored),
                                           * 1: int (nonzero is an error) */
} obo_ci_foreign_type;

extern const obo_ci_foreign_type obo_ci_foreign_types[];
extern const long obo_ci_foreign_type_count;

#endif
