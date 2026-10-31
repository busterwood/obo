/* Guarded hosted stack for the experimental cinterop entry bridge. */
#ifndef OBO_CINTEROP_HOST_STACK_H
#define OBO_CINTEROP_HOST_STACK_H

#include <stddef.h>

#define OBO_GUARD_STACK_DEFAULT (1u << 20)
#define OBO_GUARD_STACK_MIN (256u << 10)
#define OBO_GUARD_STACK_MAX (16u << 20)

typedef struct {
    unsigned char *map;
    size_t map_len;
    unsigned char *lo;   /* first usable byte */
    unsigned char *hi;   /* first byte of the upper guard page */
    size_t usable;
    size_t page;
} obo_guard_stack;

/* Returns 1 on success and 0 on failure with a short message in err. The
 * returned stack has a PROT_NONE page below and above the usable region. */
int obo_guard_stack_init(obo_guard_stack *s, size_t requested,
                         char *err, size_t errcap);
void obo_guard_stack_destroy(obo_guard_stack *s);

#endif
