/* Minimal real adapter template for the cinterop v2 boundary.
 *
 * Replace demo_* with your library's operations. The important parts are:
 * demo_item * is the real C pointer and stays on the C side (it becomes the
 * foreign handle's private word), demo_open returns it for an `own` result,
 * demo_make reports an error through rc for an out record, and
 * demo_close frees exactly once. There is no token registry and no cres. */
#include "adapter.h"

#include <stdlib.h>

struct demo_item {
    unsigned char *data;
    long size;
};

static demo_item *alloc_item(long size) {
    demo_item *item;
    if (size < 0 || size > 1024 * 1024) return NULL;
    item = (demo_item *)calloc(1, sizeof(*item));
    if (item == NULL) return NULL;
    item->size = size;
    item->data = (unsigned char *)calloc((size_t)size + 1, 1);
    if (item->data == NULL) {
        free(item);
        return NULL;
    }
    return item;
}

demo_item *demo_open(long size) {
    /* own<demo_item>: NULL is a native contract violation (exit 11), so the
     * wrapper rejects an out-of-range size before calling. This NULL is
     * allocation failure only. */
    return alloc_item(size);
}

long demo_read_byte(const demo_item *item, long index) {
    if (item == NULL || index < 0 || index >= item->size) return -1;
    return (long)item->data[index];
}

int demo_make(long size, demo_item **out) {
    demo_item *item;
    if (out == NULL) return -1;
    *out = NULL;
    item = alloc_item(size);
    if (item == NULL) return -3;
    *out = item;
    return 0;
}

void demo_close(demo_item *item) {
    if (item == NULL) return;
    free(item->data);
    free(item);
}
