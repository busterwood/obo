#include "host_stack.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static int fail(char *err, size_t errcap, const char *msg) {
    if (err != NULL && errcap != 0) snprintf(err, errcap, "%s", msg);
    return 0;
}

int obo_guard_stack_init(obo_guard_stack *s, size_t requested,
                         char *err, size_t errcap) {
    long p;
    size_t page, usable, map_len;
    void *map;

    if (s == NULL) return fail(err, errcap, "obo: internal null guard-stack descriptor\n");
    memset(s, 0, sizeof(*s));
    if (requested < OBO_GUARD_STACK_MIN || requested > OBO_GUARD_STACK_MAX) {
        return fail(err, errcap, "obo: cinterop invalid stack size\n");
    }
    p = sysconf(_SC_PAGESIZE);
    if (p <= 0) return fail(err, errcap, "obo: cinterop cannot read page size\n");
    page = (size_t)p;
    if ((page & (page - 1)) != 0) {
        return fail(err, errcap, "obo: cinterop page size is not a power of two\n");
    }
    if (requested > SIZE_MAX - (page - 1)) {
        return fail(err, errcap, "obo: cinterop stack size overflow\n");
    }
    usable = ((requested + page - 1) / page) * page;
    if (usable > OBO_GUARD_STACK_MAX || usable > SIZE_MAX - 2 * page) {
        return fail(err, errcap, "obo: cinterop stack size overflow\n");
    }
    map_len = usable + 2 * page;
    map = mmap(NULL, map_len, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS
#ifdef MAP_STACK
               | MAP_STACK
#endif
               , -1, 0);
    if (map == MAP_FAILED) {
        return fail(err, errcap, "obo: cinterop cannot map guarded stack\n");
    }
    if (mprotect(map, page, PROT_NONE) != 0 ||
        mprotect((unsigned char *)map + page + usable, page, PROT_NONE) != 0) {
        (void)munmap(map, map_len);
        return fail(err, errcap, "obo: cinterop cannot protect stack guards\n");
    }
    s->map = (unsigned char *)map;
    s->map_len = map_len;
    s->lo = (unsigned char *)map + page;
    s->hi = s->lo + usable;
    s->usable = usable;
    s->page = page;
    return 1;
}

void obo_guard_stack_destroy(obo_guard_stack *s) {
    if (s == NULL) return;
    if (s->map != NULL) (void)munmap(s->map, s->map_len);
    memset(s, 0, sizeof(*s));
}
