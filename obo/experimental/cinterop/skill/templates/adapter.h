/* Scalar-only adapter ABI template. No pointer crosses into obo. */
#ifndef OBO_CINTEROP_TEMPLATE_ADAPTER_H
#define OBO_CINTEROP_TEMPLATE_ADAPTER_H

long demo_open(long size);
long demo_read_byte(long token, long index);
long demo_close(long token);
long demo_live(void);

#endif
