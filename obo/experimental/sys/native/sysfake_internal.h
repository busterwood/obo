/* Shared fake-backend handle shape. */
#ifndef OBO_SYS_FAKE_INTERNAL_H
#define OBO_SYS_FAKE_INTERNAL_H

typedef struct fake_desc fake_desc;

struct fake_sock;

typedef struct fake_file {
    fake_desc *d;
    struct fake_sock *sock;
} fake_file;

#endif
