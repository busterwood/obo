/* Minimal real adapter template for the cinterop v2 boundary.
 *
 * Replace demo_* with your library's operations. The important parts are:
 * demo_item * is the real C pointer and stays on the C side (it becomes the
 * foreign handle's private word), demo_open returns it for an `own_opt` result,
 * demo_make reports an error through rc for an out record (and can leave a
 * non-NULL handle for the wrapper to close), and
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
    /* The wrapper declares this as own_opt<demo_item>, so NULL becomes nil and
     * the wrapper returns err. A plain own result would exit 11 on NULL. The
     * wrapper rejects an out-of-range size before calling; NULL here is
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
    /* Model sqlite3_open_v2: a failing open can still write a non-NULL handle.
     * The wrapper must read the message (or here the code) and close it exactly
     * once on the error path. Size 7 is the deterministic demo failure. */
    if (size == 7) return 7;
    return 0;
}

void demo_close(demo_item *item) {
    if (item == NULL) return;
    free(item->data);
    free(item);
}
