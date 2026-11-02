/* Shared Linux P2 socket descriptor object.
 *
 * A sys_fdobj is used for files, directories, standard streams and
 * sockets. Only objects with state != NULL are sockets; file streams have
 * state == NULL and keep the P1a read/write path. The socket state is
 * reference-counted because duplicateStream shares the socket and its
 * half-close state while owning a separate descriptor.
 */
#ifndef OBO_SYS_NET_H
#define OBO_SYS_NET_H

typedef struct {
    int refs;
    int connecting;
    int failed;
    long failure;          /* positive captured errno when failed != 0 */
    int read_closed;
    int write_closed;
} sys_sockstate;

typedef struct {
    int fd;
    int append;
    long mode;
    int sock;
    sys_sockstate *state;
    int dgram_flags;       /* SYS_DGRAM_* bits enabled at bind time */
    int gso_disabled;      /* set after the kernel rejects UDP_SEGMENT */
} sys_fdobj;

#endif
