/* Adapter ABI template for the cinterop v2 boundary.
 *
 * The opaque C type is the foreign handle's private word. No pointer is ever
 * returned to obo as an integer. The header is the signature-check source:
 * every demo_* prototype below must match the spec exactly. */
#ifndef OBO_CINTEROP_TEMPLATE_ADAPTER_H
#define OBO_CINTEROP_TEMPLATE_ADAPTER_H

typedef struct demo_item demo_item;

demo_item *demo_open(long size);
long demo_read_byte(const demo_item *item, long index);
void demo_close(demo_item *item);

/* Out-parameter form: reports a failure through the return code instead of a
 * NULL `own` result (NULL would be a native contract violation, exit 11). */
int demo_make(long size, demo_item **out);

#endif
