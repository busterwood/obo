/* Minimal real adapter template using the toolkit resource registry.
 *
 * Replace demo_* with your library's scalar operations. The important parts
 * are: every allocation is registered, every call validates kind/generation,
 * every close releases exactly once, and no pointer is returned to obo. */
#include "../../toolkit/cres.h"
#include "adapter.h"

#include <stdlib.h>

typedef struct {
    unsigned char *data;
    long size;
} demo_resource;

static void demo_release(void *p) {
    demo_resource *r = (demo_resource *)p;
    if (r == NULL) return;
    free(r->data);
    free(r);
}

long demo_open(long size) {
    demo_resource *r;
    long token;
    if (size < 0 || size > 1024 * 1024) return -2;
    r = (demo_resource *)calloc(1, sizeof(*r));
    if (r == NULL) return -3;
    r->size = size;
    r->data = (unsigned char *)calloc((size_t)size + 1, 1);
    if (r->data == NULL) {
        free(r);
        return -3;
    }
    token = cres_register(CRES_KIND_CUSTOM, r, demo_release);
    if (token == 0) {
        demo_release(r);
        return -3;
    }
    return token;
}

long demo_read_byte(long token, long index) {
    demo_resource *r = (demo_resource *)cres_peek(token, CRES_KIND_CUSTOM);
    if (r == NULL || index < 0 || index >= r->size) return -1;
    return (long)r->data[index];
}

long demo_close(long token) {
    return cres_close_kind(token, CRES_KIND_CUSTOM);
}

long demo_live(void) {
    return cres_live_count();
}
