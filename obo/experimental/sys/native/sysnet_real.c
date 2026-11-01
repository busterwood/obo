/* Linux P2 backend: numeric addresses, TCP/UDP/Unix sockets, epoll reactor,
 * timers, eventfd cancellation and blocking getaddrinfo.
 *
 * Scalar-only boundary: address and event packets live in C-owned byte-buffer
 * resources reached by token. No pointer, struct-by-value or callback crosses.
 * Every read/write/accept/send/receive/connect is one kernel attempt; EINTR is
 * reported, never hidden. All sockets are nonblocking and CLOEXEC at creation.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"
#include "sysnet.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

typedef struct real_reactor real_reactor;

typedef struct real_watch {
    long token;
    real_reactor *reactor;
    long source;
    int source_kind;
    int fd;
    int readable;
    int writable;
    long tag;
    struct real_watch *next;
} real_watch;

typedef struct real_timer {
    long token;
    real_reactor *reactor;
    long at_ns;
    long period_ns;
    long tag;
    struct real_timer *next;
} real_timer;

struct real_reactor {
    int epfd;
    int wakefd;
    int state;                 /* 0 open, 1 stopping, 2 closed */
    long max_watches;
    long max_timers;
    long watch_count;
    long timer_count;
    long turn;
    int wake_pending;
    int wake_first;
    real_watch *watches;
    real_timer *timers;
    struct real_reactor *next;
};

typedef struct {
    int fd;
    int requested;
} real_cancel;

static real_reactor *reactors;

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static long real_now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return err(errno);
    if (ts.tv_sec < 0 || ts.tv_sec > (time_t)9223372036LL) return err(EOVERFLOW);
    return (long)((int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec);
}

static long long get64(const unsigned char *p, long index) {
    int64_t v = 0;
    memcpy(&v, p + (size_t)index * 8, sizeof(v));
    return (long long)v;
}

static void put64(unsigned char *p, long index, long long v) {
    int64_t x = (int64_t)v;
    memcpy(p + (size_t)index * 8, &x, sizeof(x));
}

static long buffer_span(long token, unsigned char **data, size_t *len) {
    long r = sys_reg_buffer_data(token, data, len);
    if (r < 0) return r;
    if (*data == NULL) return err(EINVAL);
    return 0;
}

/* ---- address packets ---------------------------------------------------- */

static long addr_from_packet(long token, struct sockaddr_storage *ss,
                             socklen_t *salen) {
    unsigned char *p = NULL;
    size_t n = 0;
    long long family;
    long long port;
    long long scope;
    long long length;
    long r = buffer_span(token, &p, &n);
    if (r < 0) return r;
    if (n < (size_t)SYS_ADDR_PACKET_BYTES) return err(EINVAL);
    family = get64(p, 0);
    port = get64(p, 1);
    scope = get64(p, 2);
    length = get64(p, 3);
    if (length < 0 || length > SYS_ADDR_DATA_MAX) return err(ERANGE);
    memset(ss, 0, sizeof(*ss));
    if (family == SYS_ADDR_FAMILY_IPV4) {
        struct sockaddr_in *sin = (struct sockaddr_in *)ss;
        if (length != 4) return err(EINVAL);
        if (port < 0 || port > 65535) return err(ERANGE);
        sin->sin_family = AF_INET;
        sin->sin_port = htons((uint16_t)port);
        memcpy(&sin->sin_addr, p + SYS_ADDR_DATA_OFFSET, 4);
        *salen = sizeof(*sin);
        return 0;
    }
    if (family == SYS_ADDR_FAMILY_IPV6) {
        struct sockaddr_in6 *sin6 = (struct sockaddr_in6 *)ss;
        if (length != 16) return err(EINVAL);
        if (port < 0 || port > 65535) return err(ERANGE);
        if (scope < 0 || scope > 4294967295LL) return err(ERANGE);
        sin6->sin6_family = AF_INET6;
        sin6->sin6_port = htons((uint16_t)port);
        sin6->sin6_scope_id = (uint32_t)scope;
        memcpy(&sin6->sin6_addr, p + SYS_ADDR_DATA_OFFSET, 16);
        *salen = sizeof(*sin6);
        return 0;
    }
    if (family == SYS_ADDR_FAMILY_UNIX_PATH ||
        family == SYS_ADDR_FAMILY_UNIX_ABSTRACT) {
        struct sockaddr_un *un = (struct sockaddr_un *)ss;
        if (length < 1 || length > 107) return err(ERANGE);
        if (family == SYS_ADDR_FAMILY_UNIX_PATH) {
            if (memchr(p + SYS_ADDR_DATA_OFFSET, 0, (size_t)length) != NULL) {
                return err(EINVAL);
            }
            un->sun_family = AF_UNIX;
            memcpy(un->sun_path, p + SYS_ADDR_DATA_OFFSET, (size_t)length);
            un->sun_path[length] = '\0';
            *salen = (socklen_t)(offsetof(struct sockaddr_un, sun_path) +
                                 (size_t)length + 1);
            return 0;
        }
        un->sun_family = AF_UNIX;
        un->sun_path[0] = '\0';
        memcpy(un->sun_path + 1, p + SYS_ADDR_DATA_OFFSET, (size_t)length);
        *salen = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 +
                             (size_t)length);
        return 0;
    }
    return err(EINVAL);
}

/* Write an address packet at `out`; returns 1 for a written address, 0 for
 * "none". The caller has already checked the 160-byte span. */
static long addr_write_packet(const struct sockaddr *sa, socklen_t salen,
                              unsigned char *out) {
    memset(out, 0, (size_t)SYS_ADDR_PACKET_BYTES);
    if (sa == NULL || salen < sizeof(sa_family_t)) return 0;
    if (sa->sa_family == AF_INET) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)sa;
        if (salen < sizeof(*sin)) return err(EINVAL);
        put64(out, 0, SYS_ADDR_FAMILY_IPV4);
        put64(out, 1, (long long)ntohs(sin->sin_port));
        put64(out, 2, 0);
        put64(out, 3, 4);
        memcpy(out + SYS_ADDR_DATA_OFFSET, &sin->sin_addr, 4);
        return 1;
    }
    if (sa->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 = (const struct sockaddr_in6 *)sa;
        if (salen < sizeof(*sin6)) return err(EINVAL);
        put64(out, 0, SYS_ADDR_FAMILY_IPV6);
        put64(out, 1, (long long)ntohs(sin6->sin6_port));
        put64(out, 2, (long long)sin6->sin6_scope_id);
        put64(out, 3, 16);
        memcpy(out + SYS_ADDR_DATA_OFFSET, &sin6->sin6_addr, 16);
        return 1;
    }
    if (sa->sa_family == AF_UNIX) {
        const struct sockaddr_un *un = (const struct sockaddr_un *)sa;
        size_t base = offsetof(struct sockaddr_un, sun_path);
        size_t raw;
        if (salen <= base) return 0;
        raw = (size_t)salen - base;
        if (raw > sizeof(un->sun_path)) raw = sizeof(un->sun_path);
        if (un->sun_path[0] == '\0') {
            size_t length = raw > 0 ? raw - 1 : 0;
            if (length == 0 || length > 107) return 0;
            put64(out, 0, SYS_ADDR_FAMILY_UNIX_ABSTRACT);
            put64(out, 3, (long long)length);
            memcpy(out + SYS_ADDR_DATA_OFFSET, un->sun_path + 1, length);
            return 1;
        }
        {
            size_t length = 0;
            while (length < raw && un->sun_path[length] != '\0') length++;
            if (length == 0 || length > 107) return 0;
            put64(out, 0, SYS_ADDR_FAMILY_UNIX_PATH);
            put64(out, 3, (long long)length);
            memcpy(out + SYS_ADDR_DATA_OFFSET, un->sun_path, length);
            return 1;
        }
    }
    return 0;
}

static long addr_to_packet(const struct sockaddr *sa, socklen_t salen,
                           long token) {
    unsigned char *out = NULL;
    size_t outlen = 0;
    long r = buffer_span(token, &out, &outlen);
    if (r < 0) return r;
    if (outlen < (size_t)SYS_ADDR_PACKET_BYTES) return err(EINVAL);
    return addr_write_packet(sa, salen, out);
}

/* ---- socket objects ----------------------------------------------------- */

static int release_sockobj(void *p) {
    sys_fdobj *f = (sys_fdobj *)p;
    int e = 0;
    if (f == NULL) return EINVAL;
    if (f->fd >= 0) {
        if (close(f->fd) != 0) e = errno;
        f->fd = -1;
    }
    if (f->state != NULL) {
        f->state->refs--;
        if (f->state->refs <= 0) free(f->state);
        f->state = NULL;
    }
    free(f);
    return e;
}

static sys_fdobj *sock_of(long token, long kind) {
    sys_fdobj *f = (sys_fdobj *)sys_reg_peek(token, (int)kind);
    if (f == NULL || !f->sock || f->state == NULL) return NULL;
    return f;
}

static long register_sock(int fd, int kind, int connecting) {
    sys_fdobj *f = (sys_fdobj *)calloc(1, sizeof(*f));
    sys_sockstate *st = (sys_sockstate *)calloc(1, sizeof(*st));
    long token;
    if (f == NULL || st == NULL) {
        free(f);
        free(st);
        (void)close(fd);
        return err(ENOMEM);
    }
    st->refs = 1;
    st->connecting = connecting;
    f->fd = fd;
    f->sock = 1;
    f->state = st;
    token = sys_reg_new_object(kind, f, release_sockobj, 0);
    if (token < 0) release_sockobj(f);
    return token;
}

/* ---- listen / accept / connect ----------------------------------------- */

long sys_net_listen(long addr_token, long backlog, long flags) {
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    int fd;
    int domain;
    int on = 1;
    long r;
    long token;
    sys_reg_bump_call(SYS_CALL_NET_LISTEN);
    if (backlog < 1 || backlog > 65535) return err(EINVAL);
    if (flags < 0 || (flags & ~(SYS_LISTEN_REUSE_ADDRESS | SYS_LISTEN_REUSE_PORT |
                               SYS_LISTEN_IPV6_ONLY)) != 0) {
        return err(EINVAL);
    }
    r = addr_from_packet(addr_token, &ss, &salen);
    if (r < 0) return r;
    domain = ss.ss_family;
    fd = socket(domain, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    if ((flags & SYS_LISTEN_REUSE_ADDRESS) != 0) {
        if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    if ((flags & SYS_LISTEN_REUSE_PORT) != 0) {
        if (setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &on, sizeof(on)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    if (domain == AF_INET6) {
        int only = (flags & SYS_LISTEN_IPV6_ONLY) != 0;
        if (setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &only, sizeof(only)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    if (bind(fd, (struct sockaddr *)&ss, salen) != 0) {
        int e = errno; (void)close(fd); return err(e);
    }
    if (listen(fd, (int)backlog) != 0) {
        int e = errno; (void)close(fd); return err(e);
    }
    token = register_sock(fd, SYS_KIND_LISTENER, 0);
    if (token < 0) return token;
    return token;
}

long sys_net_accept(long listener_token) {
    sys_fdobj *l = sock_of(listener_token, SYS_KIND_LISTENER);
    int fd;
    long token;
    sys_reg_bump_call(SYS_CALL_ACCEPT);
    if (l == NULL) return err(EBADF);
    fd = accept4(l->fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (fd < 0) return err(errno);
    token = register_sock(fd, SYS_KIND_CONNECTION, 0);
    return token;
}

long sys_net_connect(long addr_token) {
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    int fd;
    int rc;
    long r;
    long token;
    int connecting = 0;
    sys_reg_bump_call(SYS_CALL_CONNECT);
    r = addr_from_packet(addr_token, &ss, &salen);
    if (r < 0) return r;
    fd = socket(ss.ss_family, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    rc = connect(fd, (struct sockaddr *)&ss, salen);
    if (rc != 0) {
        int e = errno;
        if (e == EINPROGRESS || e == EALREADY || e == EAGAIN || e == EINTR) {
            connecting = 1;
        } else {
            (void)close(fd);
            return err(e);
        }
    }
    token = register_sock(fd, SYS_KIND_CONNECTION, connecting);
    return token;
}

long sys_net_finish_connect(long conn_token) {
    sys_fdobj *c = sock_of(conn_token, SYS_KIND_CONNECTION);
    struct sockaddr_storage peer;
    socklen_t peerlen = sizeof(peer);
    int soerr = 0;
    socklen_t solen = sizeof(soerr);
    sys_reg_bump_call(SYS_CALL_FINISH_CONNECT);
    if (c == NULL) return err(EBADF);
    if (c->state->failed) return err(c->state->failure);
    if (!c->state->connecting) return 1;
    /* getpeername can succeed while a nonblocking connect is still in
     * SYN-SENT, and SO_ERROR is zero then too. Poll for completion with a
     * zero timeout, then check SO_ERROR; writability alone is not accepted as
     * success, and this call never blocks. */
    {
        struct pollfd pfd;
        int prc;
        memset(&pfd, 0, sizeof(pfd));
        pfd.fd = c->fd;
        pfd.events = POLLOUT;
        prc = poll(&pfd, 1, 0);
        if (prc < 0) {
            int e = errno;
            /* EINTR is not a connect failure: the connection may still be in
             * progress. Leave the state pending for the caller to retry. */
            if (e == EINTR) return 0;
            c->state->failed = 1;
            c->state->failure = e;
            c->state->connecting = 0;
            return err(e);
        }
        if (prc == 0) return 0;
    }
    if (getsockopt(c->fd, SOL_SOCKET, SO_ERROR, &soerr, &solen) != 0) {
        int e = errno;
        if (e == EINTR) return 0;
        c->state->failed = 1;
        c->state->failure = e;
        c->state->connecting = 0;
        return err(e);
    }
    if (soerr != 0) {
        c->state->failed = 1;
        c->state->failure = soerr;
        c->state->connecting = 0;
        return err(soerr);
    }
    /* Confirm the peer is reachable; a writable socket with SO_ERROR zero is
     * connected on Linux. */
    (void)peer;
    (void)peerlen;
    c->state->connecting = 0;
    return 1;
}

long sys_net_state(long conn_token) {
    sys_fdobj *c = sock_of(conn_token, SYS_KIND_CONNECTION);
    if (c == NULL) return 0;
    if (c->state->failed) return err(c->state->failure);
    if (c->state->connecting) return 2;
    return 1;
}

/* ---- stream I/O --------------------------------------------------------- */

static long checked_span(long token, long offset, long count, int read,
                         unsigned char **data, size_t *len) {
    long r = buffer_span(token, data, len);
    if (r < 0) return r;
    if (offset < 0) return err(EINVAL);
    if (read && count <= 0) return err(EINVAL);
    if (!read && count < 0) return err(EINVAL);
    if ((size_t)offset > *len || (size_t)count > *len - (size_t)offset) {
        return err(ERANGE);
    }
    return 0;
}

static long net_read_write(long token, long kind, long buf_token, long offset,
                           long count, int reading) {
    sys_fdobj *f = sock_of(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    ssize_t n;
    long r;
    if (f == NULL) return err(EBADF);
    r = checked_span(buf_token, offset, count, reading, &buf, &buflen);
    if (r < 0) return r;
    if (f->state->connecting) return err(EINPROGRESS);
    if (f->state->failed) return err(f->state->failure);
    if (reading) {
        if (f->state->read_closed) return 0;
        n = recv(f->fd, buf + offset, (size_t)count, 0);
        if (n == 0) {
            f->state->read_closed = 1;
            return 0;
        }
        if (n < 0) {
            int e = errno;
            if (e == ECONNRESET) f->state->read_closed = 1;
            return err(e);
        }
        return (long)n;
    }
    if (f->state->write_closed) return err(EPIPE);
    if (count == 0) return 0;
    n = send(f->fd, buf + offset, (size_t)count, MSG_NOSIGNAL);
    if (n < 0) {
        int e = errno;
        if (e == EPIPE) f->state->write_closed = 1;
        return err(e);
    }
    return (long)n;
}

long sys_net_read(long token, long kind, long buf_token, long offset,
                  long count) {
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    return net_read_write(token, kind, buf_token, offset, count, 1);
}

long sys_net_write(long token, long kind, long buf_token, long offset,
                   long count) {
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    return net_read_write(token, kind, buf_token, offset, count, 0);
}

long sys_net_shutdown(long token, long kind, long how) {
    sys_fdobj *f = (sys_fdobj *)sys_reg_peek(token, (int)kind);
    sys_reg_bump_call(SYS_CALL_SHUTDOWN);
    if (f == NULL || !f->sock || f->state == NULL) return err(EOPNOTSUPP);
    if (how != 0 && how != 1) return err(EINVAL);
    if (f->state->connecting) return err(EINPROGRESS);
    if (f->state->failed) return err(f->state->failure);
    if (how == 0 && f->state->read_closed) return 1;
    if (how == 1 && f->state->write_closed) return 1;
    if (shutdown(f->fd, how == 0 ? SHUT_RD : SHUT_WR) != 0) {
        int e = errno;
        if (e == ENOTSOCK) return err(EOPNOTSUPP);
        return err(e);
    }
    if (how == 0) f->state->read_closed = 1;
    else f->state->write_closed = 1;
    return 1;
}

long sys_net_dup(long token, long kind, long to_kind) {
    sys_fdobj *src = sock_of(token, kind);
    sys_fdobj *f;
    int fd;
    long out;
    sys_reg_bump_call(SYS_CALL_NET_DUP);
    if (src == NULL) return err(EBADF);
    if (to_kind <= 0) return err(EINVAL);
    if (src->state->connecting) return err(EINPROGRESS);
    if (src->state->failed) return err(src->state->failure);
    fd = fcntl(src->fd, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    f = (sys_fdobj *)calloc(1, sizeof(*f));
    if (f == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    f->fd = fd;
    f->sock = 1;
    f->state = src->state;
    f->state->refs++;
    out = sys_reg_new_object((int)to_kind, f, release_sockobj, 0);
    if (out < 0) {
        f->state->refs--;
        (void)close(fd);
        free(f);
    }
    return out;
}

/* ---- addresses and socket options -------------------------------------- */

long sys_net_address(long token, long kind, long which, long out_token) {
    struct sockaddr_storage ss;
    socklen_t salen = sizeof(ss);
    sys_fdobj *f = (sys_fdobj *)sys_reg_peek(token, (int)kind);
    long r;
    sys_reg_bump_call(SYS_CALL_ADDRESS);
    if (f == NULL || !f->sock) return err(EBADF);
    if (which != 0 && which != 1) return err(EINVAL);
    memset(&ss, 0, sizeof(ss));
    if (which == 0) {
        if (getsockname(f->fd, (struct sockaddr *)&ss, &salen) != 0) {
            return err(errno);
        }
    } else {
        if (getpeername(f->fd, (struct sockaddr *)&ss, &salen) != 0) {
            return err(errno);
        }
    }
    r = addr_to_packet((struct sockaddr *)&ss, salen, out_token);
    return r;
}

long sys_net_set_no_delay(long token, long enabled) {
    sys_fdobj *f = sock_of(token, SYS_KIND_CONNECTION);
    int on;
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    on = (int)enabled;
    if (setsockopt(f->fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on)) != 0) {
        int e = errno;
        if (e == ENOPROTOOPT || e == EOPNOTSUPP || e == EINVAL) {
            return err(EOPNOTSUPP);
        }
        return err(e);
    }
    return 1;
}

long sys_net_set_keepalive(long token, long enabled, long idle, long interval,
                           long probes) {
    sys_fdobj *f = sock_of(token, SYS_KIND_CONNECTION);
    int on = (int)enabled;
    int v;
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    if (idle < 0 || interval < 0 || probes < 0) return err(EINVAL);
    if (setsockopt(f->fd, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof(on)) != 0) {
        return err(errno);
    }
    if (!enabled) return 1;
    if (idle < 1 || interval < 1 || probes < 1 ||
        idle > 2147483 || interval > 2147483 || probes > 2147483) {
        return err(ERANGE);
    }
    v = (int)idle;
    if (setsockopt(f->fd, IPPROTO_TCP, TCP_KEEPIDLE, &v, sizeof(v)) != 0) {
        int e = errno;
        if (e == ENOPROTOOPT || e == EOPNOTSUPP) return err(EOPNOTSUPP);
        return err(e);
    }
    v = (int)interval;
    if (setsockopt(f->fd, IPPROTO_TCP, TCP_KEEPINTVL, &v, sizeof(v)) != 0) {
        int e = errno;
        if (e == ENOPROTOOPT || e == EOPNOTSUPP) return err(EOPNOTSUPP);
        return err(e);
    }
    v = (int)probes;
    if (setsockopt(f->fd, IPPROTO_TCP, TCP_KEEPCNT, &v, sizeof(v)) != 0) {
        int e = errno;
        if (e == ENOPROTOOPT || e == EOPNOTSUPP) return err(EOPNOTSUPP);
        return err(e);
    }
    return 1;
}

long sys_net_set_buffer(long token, long kind, long which, long size) {
    sys_fdobj *f = (sys_fdobj *)sys_reg_peek(token, (int)kind);
    int v;
    int opt;
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL || !f->sock) return err(EBADF);
    if (which != 0 && which != 1) return err(EINVAL);
    if (size < 1 || size > 1073741824L) return err(ERANGE);
    opt = which == 0 ? SO_RCVBUF : SO_SNDBUF;
    v = (int)size;
    if (setsockopt(f->fd, SOL_SOCKET, opt, &v, sizeof(v)) != 0) {
        return err(errno);
    }
    return 1;
}

long sys_net_set_broadcast(long token, long enabled) {
    sys_fdobj *f = sock_of(token, SYS_KIND_DATAGRAM);
    int on;
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    on = (int)enabled;
    if (setsockopt(f->fd, SOL_SOCKET, SO_BROADCAST, &on, sizeof(on)) != 0) {
        return err(errno);
    }
    return 1;
}

/* ---- datagrams ---------------------------------------------------------- */

#define DGRAM_DEFAULT_BUFFER 4194304L
#define DGRAM_MAX_BUFFER 1073741824L

#ifndef IP_MTU_DISCOVER
#define IP_MTU_DISCOVER 10
#endif
#ifndef IP_PMTUDISC_DO
#define IP_PMTUDISC_DO 2
#endif
#ifndef IPV6_MTU_DISCOVER
#define IPV6_MTU_DISCOVER 23
#endif
#ifndef IPV6_PMTUDISC_DO
#define IPV6_PMTUDISC_DO 2
#endif
#ifndef UDP_SEGMENT
#define UDP_SEGMENT 103
#endif
#ifndef UDP_GRO
#define UDP_GRO 104
#endif

static int ignore_unsupported_option(int e) {
    return e == ENOPROTOOPT || e == EOPNOTSUPP || e == EINVAL;
}

/* Bind one nonblocking CLOEXEC UDP socket with the P2q option profile. The
 * socket is closed on every post-bind failure. Flags the kernel rejects are
 * cleared rather than treated as correctness failures. */
static long bind_datagram_common(long addr_token, long rcvbuf, long sndbuf,
                                 long flags) {
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    int fd;
    long r;
    long token;
    sys_fdobj *d;
    sys_reg_bump_call(SYS_CALL_NET_LISTEN);
    if (rcvbuf < 1 || rcvbuf > DGRAM_MAX_BUFFER) return err(ERANGE);
    if (sndbuf < 1 || sndbuf > DGRAM_MAX_BUFFER) return err(ERANGE);
    if (flags < 0 || (flags & ~(SYS_DGRAM_REUSE_PORT | SYS_DGRAM_GRO |
                               SYS_DGRAM_GSO | SYS_DGRAM_DONT_FRAGMENT)) != 0) {
        return err(EINVAL);
    }
    r = addr_from_packet(addr_token, &ss, &salen);
    if (r < 0) return r;
    fd = socket(ss.ss_family, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    if (ss.ss_family == AF_INET6) {
        /* Default a datagram socket to IPv6-only, matching the stream
         * listen default. Otherwise :: can reserve the same IPv4 port and
         * silently create a dual-stack endpoint. */
        int only = 1;
        if (setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &only, sizeof(only)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    if ((flags & SYS_DGRAM_REUSE_PORT) != 0) {
        int on = 1;
        if (setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &on, sizeof(on)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    {
        int rb = (int)rcvbuf;
        int sb = (int)sndbuf;
        if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rb, sizeof(rb)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
        if (setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sb, sizeof(sb)) != 0) {
            int e = errno; (void)close(fd); return err(e);
        }
    }
    if ((flags & SYS_DGRAM_GRO) != 0) {
        int on = 1;
        if (setsockopt(fd, IPPROTO_UDP, UDP_GRO, &on, sizeof(on)) != 0) {
            if (!ignore_unsupported_option(errno)) {
                int e = errno; (void)close(fd); return err(e);
            }
            flags &= ~(long)SYS_DGRAM_GRO;
        }
    }
    if ((flags & SYS_DGRAM_DONT_FRAGMENT) != 0) {
        if (ss.ss_family == AF_INET) {
            int mode = IP_PMTUDISC_DO;
            if (setsockopt(fd, IPPROTO_IP, IP_MTU_DISCOVER, &mode,
                           sizeof(mode)) != 0 &&
                !ignore_unsupported_option(errno)) {
                int e = errno; (void)close(fd); return err(e);
            }
        } else if (ss.ss_family == AF_INET6) {
            int mode = IPV6_PMTUDISC_DO;
            if (setsockopt(fd, IPPROTO_IPV6, IPV6_MTU_DISCOVER, &mode,
                           sizeof(mode)) != 0 &&
                !ignore_unsupported_option(errno)) {
                int e = errno; (void)close(fd); return err(e);
            }
        }
    }
    if (bind(fd, (struct sockaddr *)&ss, salen) != 0) {
        int e = errno; (void)close(fd); return err(e);
    }
    token = register_sock(fd, SYS_KIND_DATAGRAM, 0);
    if (token < 0) return token;
    d = sock_of(token, SYS_KIND_DATAGRAM);
    if (d == NULL) return err(EIO);
    d->dgram_flags = (int)flags;
    return token;
}

long sys_net_bind_datagram(long addr_token) {
    /* Legacy P2 profile: no GRO coalescing and no forced DF. GSO is only a
     * capability bit and does not change one-datagram receive behavior. */
    return bind_datagram_common(addr_token, DGRAM_DEFAULT_BUFFER,
                                DGRAM_DEFAULT_BUFFER, SYS_DGRAM_GSO);
}

long sys_net_bind_datagram_options(long addr_token, long rcvbuf, long sndbuf,
                                   long flags) {
    return bind_datagram_common(addr_token, rcvbuf, sndbuf, flags);
}

long sys_net_datagram_caps(long token) {
    sys_fdobj *d = sock_of(token, SYS_KIND_DATAGRAM);
    long caps = 0;
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (d == NULL) return err(EBADF);
    if (!d->gso_disabled && (d->dgram_flags & SYS_DGRAM_GSO) != 0) {
        caps |= SYS_DGRAM_CAP_GSO;
    }
    if ((d->dgram_flags & SYS_DGRAM_GRO) != 0) caps |= SYS_DGRAM_CAP_GRO;
    return caps;
}

/* One recvmsg with a UDP_GRO ancillary read. `segment` is 0 when the kernel
 * did not coalesce the datagram or the option is unavailable. */
static ssize_t dgram_recvmsg(int fd, unsigned char *buf, size_t cap,
                             struct sockaddr_storage *ss, socklen_t *salen,
                             long *segment) {
    struct iovec iov;
    struct msghdr msg;
    char cbuf[CMSG_SPACE(sizeof(int))];
    struct cmsghdr *cmsg;
    ssize_t n;
    *segment = 0;
    memset(ss, 0, sizeof(*ss));
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = buf;
    iov.iov_len = cap;
    msg.msg_name = ss;
    msg.msg_namelen = sizeof(*ss);
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = cbuf;
    msg.msg_controllen = sizeof(cbuf);
    n = recvmsg(fd, &msg, MSG_TRUNC);
    if (n < 0) return n;
    *salen = msg.msg_namelen;
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg != NULL;
         cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == IPPROTO_UDP && cmsg->cmsg_type == UDP_GRO &&
            cmsg->cmsg_len >= CMSG_LEN(sizeof(int))) {
            int value = 0;
            memcpy(&value, CMSG_DATA(cmsg), sizeof(value));
            if (value > 0 && value <= 65535) *segment = (long)value;
        }
    }
    return n;
}

long sys_net_receive(long token, long buf_token, long max, long peer_token,
                     long meta_token) {
    sys_fdobj *d = sock_of(token, SYS_KIND_DATAGRAM);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    unsigned char *peer = NULL;
    size_t peerlen = 0;
    unsigned char *meta = NULL;
    size_t metalen = 0;
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    ssize_t n;
    long r;
    long copied;
    long original;
    long segment = 0;
    int truncated;
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    if (d == NULL) return err(EBADF);
    if (max < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)max > buflen) return err(ERANGE);
    r = buffer_span(peer_token, &peer, &peerlen);
    if (r < 0) return r;
    if (peerlen < (size_t)SYS_ADDR_PACKET_BYTES) return err(ERANGE);
    r = buffer_span(meta_token, &meta, &metalen);
    if (r < 0) return r;
    if (metalen < (size_t)SYS_DGRAM_META_BYTES) return err(ERANGE);
    n = dgram_recvmsg(d->fd, buf, (size_t)max, &ss, &salen, &segment);
    if (n < 0) return err(errno);
    original = (long)n;
    copied = original;
    if (copied > max) copied = max;
    truncated = original > max ? 1 : 0;
    memset(meta, 0, (size_t)SYS_DGRAM_META_BYTES);
    put64(meta, 0, original);
    put64(meta, 1, truncated);
    put64(meta, 2, segment);
    if (salen == 0) {
        memset(peer, 0, (size_t)SYS_ADDR_PACKET_BYTES);
    } else {
        r = addr_write_packet((struct sockaddr *)&ss, salen, peer);
        if (r < 0) return r;
    }
    return copied;
}

long sys_net_receive_batch(long token, long data_token, long meta_token,
                           long peer_token, long max_packets,
                           long max_bytes) {
    sys_fdobj *d = sock_of(token, SYS_KIND_DATAGRAM);
    unsigned char *data = NULL;
    size_t datalen = 0;
    unsigned char *meta = NULL;
    size_t metalen = 0;
    unsigned char *peer = NULL;
    size_t peerlen = 0;
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    ssize_t n;
    long r;
    long i;
    size_t used = 0;
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    if (d == NULL) return err(EBADF);
    if (max_packets < 1 || max_packets > SYS_DGRAM_MAX_BATCH) return err(ERANGE);
    if (max_bytes < 1 || max_bytes > SYS_DGRAM_MAX_BATCH_BYTES) return err(ERANGE);
    r = buffer_span(data_token, &data, &datalen);
    if (r < 0) return r;
    if ((size_t)max_bytes > datalen) return err(ERANGE);
    r = buffer_span(meta_token, &meta, &metalen);
    if (r < 0) return r;
    if (metalen < (size_t)max_packets * SYS_DGRAM_BATCH_ENTRY) return err(ERANGE);
    r = buffer_span(peer_token, &peer, &peerlen);
    if (r < 0) return r;
    if (peerlen < (size_t)max_packets * SYS_ADDR_PACKET_BYTES) return err(ERANGE);
    memset(meta, 0, (size_t)max_packets * SYS_DGRAM_BATCH_ENTRY);
    for (i = 0; i < max_packets; i++) {
        size_t remaining = (size_t)max_bytes - used;
        long segment = 0;
        unsigned char *entry = meta + (size_t)i * SYS_DGRAM_BATCH_ENTRY;
        unsigned char *pentry = peer + (size_t)i * SYS_ADDR_PACKET_BYTES;
        if (remaining == 0) break;
        n = dgram_recvmsg(d->fd, data + used, remaining, &ss, &salen, &segment);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                if (i == 0) return err(EAGAIN);
                break;
            }
            if (errno == EINTR) {
                if (i == 0) return err(EINTR);
                break;
            }
            return err(errno);
        }
        if ((size_t)n > remaining) {
            /* The datagram does not fit the remaining batch budget. It has
             * already been consumed; the caller retries with a larger budget. */
            return err(ERANGE);
        }
        put64(entry, 0, (long long)used);
        put64(entry, 1, (long long)n);
        put64(entry, 2, (long long)n);
        put64(entry, 3, 0);
        put64(entry, 4, segment);
        if (salen == 0) {
            memset(pentry, 0, (size_t)SYS_ADDR_PACKET_BYTES);
        } else {
            r = addr_write_packet((struct sockaddr *)&ss, salen, pentry);
            if (r < 0) return r;
        }
        used += (size_t)n;
    }
    return i;
}

long sys_net_send(long token, long addr_token, long buf_token, long offset,
                  long count) {
    sys_fdobj *d = sock_of(token, SYS_KIND_DATAGRAM);
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    unsigned char *buf = NULL;
    size_t buflen = 0;
    ssize_t n;
    long r;
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    if (d == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    if (count > 1048576) return err(EMSGSIZE);
    r = addr_from_packet(addr_token, &ss, &salen);
    if (r < 0) return r;
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    n = sendto(d->fd, buf + offset, (size_t)count, MSG_NOSIGNAL,
               (struct sockaddr *)&ss, salen);
    if (n < 0) return err(errno);
    if ((long)n != count) return err(EIO);
    return (long)n;
}

long sys_net_send_batch(long token, long addr_token, long buf_token,
                        long offset, long count, long segment_size) {
    sys_fdobj *d = sock_of(token, SYS_KIND_DATAGRAM);
    struct sockaddr_storage ss;
    socklen_t salen = 0;
    unsigned char *buf = NULL;
    size_t buflen = 0;
    struct iovec iov;
    struct msghdr msg;
    char cbuf[CMSG_SPACE(sizeof(uint16_t))];
    ssize_t n;
    long r;
    long segments;
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    if (d == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    if (segment_size < 0 || segment_size > 65507) return err(ERANGE);
    if (count > SYS_DGRAM_MAX_BATCH_BYTES) return err(EMSGSIZE);
    if (segment_size == 0) {
        if (count > 65507) return err(EMSGSIZE);
    } else {
        if (count < segment_size) return err(ERANGE);
        segments = (count + segment_size - 1) / segment_size;
        if (segments < 1 || segments > SYS_DGRAM_MAX_BATCH) return err(ERANGE);
        if (d->gso_disabled || (d->dgram_flags & SYS_DGRAM_GSO) == 0) {
            return err(EOPNOTSUPP);
        }
    }
    r = addr_from_packet(addr_token, &ss, &salen);
    if (r < 0) return r;
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    memset(&msg, 0, sizeof(msg));
    iov.iov_base = buf + offset;
    iov.iov_len = (size_t)count;
    msg.msg_name = &ss;
    msg.msg_namelen = salen;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    if (segment_size > 0) {
        struct cmsghdr *cmsg;
        uint16_t value = (uint16_t)segment_size;
        memset(cbuf, 0, sizeof(cbuf));
        msg.msg_control = cbuf;
        msg.msg_controllen = sizeof(cbuf);
        cmsg = CMSG_FIRSTHDR(&msg);
        cmsg->cmsg_level = IPPROTO_UDP;
        cmsg->cmsg_type = UDP_SEGMENT;
        cmsg->cmsg_len = CMSG_LEN(sizeof(uint16_t));
        memcpy(CMSG_DATA(cmsg), &value, sizeof(value));
    }
    n = sendmsg(d->fd, &msg, MSG_NOSIGNAL);
    if (n < 0) {
        int e = errno;
        if (segment_size > 0 &&
            (e == EIO || e == EINVAL || e == ENOPROTOOPT || e == EOPNOTSUPP)) {
            d->gso_disabled = 1;
            return err(EOPNOTSUPP);
        }
        return err(e);
    }
    if ((long)n != count) return err(EIO);
    return (long)n;
}

/* ---- cancellation ------------------------------------------------------- */

static int release_cancel(void *p) {
    real_cancel *c = (real_cancel *)p;
    int e = 0;
    if (c == NULL) return EINVAL;
    if (c->fd >= 0) {
        if (close(c->fd) != 0) e = errno;
        c->fd = -1;
    }
    free(c);
    return e;
}

long sys_net_cancel_new(void) {
    real_cancel *c;
    int fd;
    long token;
    sys_reg_bump_call(SYS_CALL_CANCEL);
    fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) return err(errno);
    c = (real_cancel *)calloc(1, sizeof(*c));
    if (c == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    c->fd = fd;
    token = sys_reg_new_object(SYS_KIND_CANCELLATION, c, release_cancel, 0);
    if (token < 0) release_cancel(c);
    return token;
}

long sys_net_cancel_request(long token) {
    real_cancel *c = (real_cancel *)sys_reg_peek(token, SYS_KIND_CANCELLATION);
    uint64_t one = 1;
    ssize_t n;
    sys_reg_bump_call(SYS_CALL_CANCEL);
    if (c == NULL) return err(EBADF);
    c->requested = 1;
    n = write(c->fd, &one, sizeof(one));
    if (n < 0 && errno != EAGAIN) return err(errno);
    return 1;
}

long sys_net_cancel_pending(long token) {
    real_cancel *c = (real_cancel *)sys_reg_peek(token, SYS_KIND_CANCELLATION);
    if (c == NULL) return err(EBADF);
    return c->requested ? 1 : 0;
}

/* ---- reactor ------------------------------------------------------------ */

static int release_watch(void *p) {
    real_watch *w = (real_watch *)p;
    real_reactor *r;
    real_watch **link;
    if (w == NULL) return EINVAL;
    r = w->reactor;
    if (r != NULL) {
        if (r->epfd >= 0 && w->fd >= 0) {
            (void)epoll_ctl(r->epfd, EPOLL_CTL_DEL, w->fd, NULL);
        }
        link = &r->watches;
        while (*link != NULL) {
            if (*link == w) {
                *link = w->next;
                if (r->watch_count > 0) r->watch_count--;
                break;
            }
            link = &(*link)->next;
        }
    }
    free(w);
    return 0;
}

static int release_timer(void *p) {
    real_timer *t = (real_timer *)p;
    real_reactor *r;
    real_timer **link;
    if (t == NULL) return EINVAL;
    r = t->reactor;
    if (r != NULL) {
        link = &r->timers;
        while (*link != NULL) {
            if (*link == t) {
                *link = t->next;
                if (r->timer_count > 0) r->timer_count--;
                break;
            }
            link = &(*link)->next;
        }
    }
    free(t);
    return 0;
}

static real_reactor *reactor_of(long token) {
    return (real_reactor *)sys_reg_peek(token, SYS_KIND_REACTOR);
}

static int release_reactor(void *p) {
    real_reactor *r = (real_reactor *)p;
    real_reactor **link;
    void *obj = NULL;
    int (*release)(void *) = NULL;
    int e = 0;
    if (r == NULL) return EINVAL;
    r->state = 2;
    while (r->watches != NULL) {
        real_watch *w = r->watches;
        if (sys_reg_retire(w->token, SYS_KIND_WATCH, &obj, &release)) {
            if (release != NULL) (void)release(obj);
        } else {
            r->watches = w->next;
            if (r->watch_count > 0) r->watch_count--;
        }
    }
    while (r->timers != NULL) {
        real_timer *t = r->timers;
        if (sys_reg_retire(t->token, SYS_KIND_TIMER, &obj, &release)) {
            if (release != NULL) (void)release(obj);
        } else {
            r->timers = t->next;
            if (r->timer_count > 0) r->timer_count--;
        }
    }
    if (r->epfd >= 0) {
        if (close(r->epfd) != 0) e = errno;
        r->epfd = -1;
    }
    if (r->wakefd >= 0) {
        if (close(r->wakefd) != 0) e = errno;
        r->wakefd = -1;
    }
    link = &reactors;
    while (*link != NULL) {
        if (*link == r) {
            *link = r->next;
            break;
        }
        link = &(*link)->next;
    }
    free(r);
    return e;
}

long sys_net_reactor_new(long max_watches, long max_timers) {
    real_reactor *r;
    long token;
    sys_reg_bump_call(SYS_CALL_REACTOR_NEW);
    if (max_watches < 1 || max_watches > 65536) return err(EINVAL);
    if (max_timers < 1 || max_timers > 65536) return err(EINVAL);
    r = (real_reactor *)calloc(1, sizeof(*r));
    if (r == NULL) return err(ENOMEM);
    r->epfd = epoll_create1(EPOLL_CLOEXEC);
    if (r->epfd < 0) {
        int e = errno; free(r); return err(e);
    }
    r->wakefd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (r->wakefd < 0) {
        int e = errno; (void)close(r->epfd); free(r); return err(e);
    }
    {
        struct epoll_event ev;
        memset(&ev, 0, sizeof(ev));
        ev.events = EPOLLIN;
        ev.data.u64 = 0;
        if (epoll_ctl(r->epfd, EPOLL_CTL_ADD, r->wakefd, &ev) != 0) {
            int e = errno;
            (void)close(r->epfd);
            (void)close(r->wakefd);
            free(r);
            return err(e);
        }
    }
    r->max_watches = max_watches;
    r->max_timers = max_timers;
    r->state = 0;
    r->wake_first = 1;
    r->next = reactors;
    reactors = r;
    token = sys_reg_new_object(SYS_KIND_REACTOR, r, release_reactor, 0);
    if (token < 0) {
        reactors = r->next;
        (void)close(r->epfd);
        (void)close(r->wakefd);
        free(r);
    }
    return token;
}

static long source_fd(long token, long kind) {
    if (kind == SYS_KIND_CANCELLATION) {
        real_cancel *c = (real_cancel *)sys_reg_peek(token, SYS_KIND_CANCELLATION);
        return c == NULL ? err(EBADF) : c->fd;
    }
    if (kind == SYS_KIND_STREAM || kind == SYS_KIND_LISTENER ||
        kind == SYS_KIND_CONNECTION || kind == SYS_KIND_DATAGRAM ||
        kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        sys_fdobj *f = (sys_fdobj *)sys_reg_peek(token, (int)kind);
        if (f == NULL || f->fd < 0) return err(EBADF);
        return f->fd;
    }
    return err(EOPNOTSUPP);
}

static int watchable_kind(long kind) {
    return kind == SYS_KIND_STREAM || kind == SYS_KIND_LISTENER ||
           kind == SYS_KIND_CONNECTION || kind == SYS_KIND_DATAGRAM ||
           kind == SYS_KIND_CANCELLATION ||
           kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT;
}

long sys_net_watch(long reactor_token, long source_token, long source_kind,
                   long readable, long writable, long tag) {
    real_reactor *r = reactor_of(reactor_token);
    real_watch *w;
    struct epoll_event ev;
    long fd;
    long token;
    sys_reg_bump_call(SYS_CALL_WATCH);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(EBUSY);
    if (!watchable_kind(source_kind)) return err(EOPNOTSUPP);
    if ((readable != 0 && readable != 1) || (writable != 0 && writable != 1)) {
        return err(EINVAL);
    }
    if (!readable && !writable) return err(EINVAL);
    if (r->watch_count >= r->max_watches) return err(EMFILE);
    fd = source_fd(source_token, source_kind);
    if (fd < 0) return fd;
    w = (real_watch *)calloc(1, sizeof(*w));
    if (w == NULL) return err(ENOMEM);
    w->reactor = r;
    w->source = source_token;
    w->source_kind = (int)source_kind;
    w->fd = (int)fd;
    w->readable = (int)readable;
    w->writable = (int)writable;
    w->tag = tag;
    token = sys_reg_new_object(SYS_KIND_WATCH, w, release_watch, 0);
    if (token < 0) {
        free(w);
        return token;
    }
    w->token = token;
    w->next = r->watches;
    r->watches = w;
    r->watch_count++;
    memset(&ev, 0, sizeof(ev));
    ev.events = (readable ? EPOLLIN : 0) | (writable ? EPOLLOUT : 0);
    ev.data.u64 = (uint64_t)token;
    if (epoll_ctl(r->epfd, EPOLL_CTL_ADD, (int)fd, &ev) != 0) {
        int e = errno;
        void *obj = NULL;
        int (*release)(void *) = NULL;
        (void)sys_reg_retire(token, SYS_KIND_WATCH, &obj, &release);
        if (release != NULL) (void)release(obj);
        if (e == EPERM) return err(EOPNOTSUPP);
        return err(e);
    }
    return token;
}

long sys_net_modify(long watch_token, long readable, long writable) {
    real_watch *w = (real_watch *)sys_reg_peek(watch_token, SYS_KIND_WATCH);
    struct epoll_event ev;
    sys_reg_bump_call(SYS_CALL_MODIFY);
    if (w == NULL || w->reactor == NULL || w->reactor->state == 2) return err(EBADF);
    if ((readable != 0 && readable != 1) || (writable != 0 && writable != 1)) {
        return err(EINVAL);
    }
    if (!readable && !writable) return err(EINVAL);
    w->readable = (int)readable;
    w->writable = (int)writable;
    memset(&ev, 0, sizeof(ev));
    ev.events = (readable ? EPOLLIN : 0) | (writable ? EPOLLOUT : 0);
    ev.data.u64 = (uint64_t)watch_token;
    if (epoll_ctl(w->reactor->epfd, EPOLL_CTL_MOD, w->fd, &ev) != 0) {
        int e = errno;
        if (e == ENOENT || e == EBADF) return err(EBADF);
        return err(e);
    }
    return 1;
}

long sys_net_remove(long watch_token) {
    real_watch *w = (real_watch *)sys_reg_peek(watch_token, SYS_KIND_WATCH);
    void *obj = NULL;
    int (*release)(void *) = NULL;
    sys_reg_bump_call(SYS_CALL_REMOVE);
    if (w == NULL) return 0;
    if (!sys_reg_retire(watch_token, SYS_KIND_WATCH, &obj, &release)) return 0;
    if (release != NULL) (void)release(obj);
    return 1;
}

long sys_net_timer(long reactor_token, long at_ns, long period_ns, long tag) {
    real_reactor *r = reactor_of(reactor_token);
    real_timer *t;
    long token;
    sys_reg_bump_call(SYS_CALL_TIMER);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(EBUSY);
    if (at_ns < 0 || period_ns < 0) return err(ERANGE);
    if (r->timer_count >= r->max_timers) return err(EMFILE);
    t = (real_timer *)calloc(1, sizeof(*t));
    if (t == NULL) return err(ENOMEM);
    t->reactor = r;
    t->at_ns = at_ns;
    t->period_ns = period_ns;
    t->tag = tag;
    token = sys_reg_new_object(SYS_KIND_TIMER, t, release_timer, 0);
    if (token < 0) {
        free(t);
        return token;
    }
    t->token = token;
    t->next = r->timers;
    r->timers = t;
    r->timer_count++;
    return token;
}

long sys_net_cancel_timer(long timer_token) {
    real_timer *t = (real_timer *)sys_reg_peek(timer_token, SYS_KIND_TIMER);
    void *obj = NULL;
    int (*release)(void *) = NULL;
    sys_reg_bump_call(SYS_CALL_CANCEL_TIMER);
    if (t == NULL) return 0;
    if (!sys_reg_retire(timer_token, SYS_KIND_TIMER, &obj, &release)) return 0;
    if (release != NULL) (void)release(obj);
    return 1;
}

static void put_event(unsigned char *out, long index, long tag, int readable,
                      int writable, int hangup, int failed, int timer,
                      long ticks, int wake) {
    unsigned char *p = out + (size_t)index * (size_t)SYS_EVENT_PACKET_BYTES;
    put64(p, 0, tag);
    put64(p, 1, readable ? 1 : 0);
    put64(p, 2, writable ? 1 : 0);
    put64(p, 3, hangup ? 1 : 0);
    put64(p, 4, failed ? 1 : 0);
    put64(p, 5, timer ? 1 : 0);
    put64(p, 6, ticks);
    put64(p, 7, wake ? 1 : 0);
}

static void collect_due_timers(real_reactor *r, long now, unsigned char *out,
                               long cap, long *count) {
    real_timer *t = r->timers;
    while (t != NULL) {
        real_timer *next = t->next;
        if (*count >= cap) return;
        if (now >= t->at_ns) {
            long ticks = 1;
            long nextAt = t->at_ns;
            if (t->period_ns > 0) {
                long elapsed = now - t->at_ns;
                ticks = 1 + elapsed / t->period_ns;
                if (ticks < 1) ticks = 1;
                if (ticks > (INT64_MAX - t->at_ns) / t->period_ns) {
                    nextAt = -1;
                } else {
                    nextAt = t->at_ns + ticks * t->period_ns;
                }
            }
            put_event(out, *count, t->tag, 0, 0, 0, 0, 1, ticks, 0);
            *count += 1;
            if (t->period_ns == 0 || nextAt < 0) {
                void *obj = NULL;
                int (*release)(void *) = NULL;
                long token = t->token;
                if (sys_reg_retire(token, SYS_KIND_TIMER, &obj, &release)) {
                    if (release != NULL) (void)release(obj);
                } else {
                    /* Should not happen; unlink defensively. */
                    r->timers = next;
                    if (r->timer_count > 0) r->timer_count--;
                    free(t);
                }
            } else {
                t->at_ns = nextAt;
            }
        }
        t = next;
    }
}

static long next_timer_due(real_reactor *r) {
    real_timer *t = r->timers;
    long best = -1;
    while (t != NULL) {
        if (best < 0 || t->at_ns < best) best = t->at_ns;
        t = t->next;
    }
    return best;
}

static int drain_wake_once(int fd) {
    uint64_t value;
    ssize_t n = read(fd, &value, sizeof(value));
    return n == (ssize_t)sizeof(value) ? 1 : 0;
}

static void drain_wake(int fd) {
    uint64_t value;
    while (read(fd, &value, sizeof(value)) == (ssize_t)sizeof(value)) {
        /* eventfd reads reset the counter; one read is normally enough. */
    }
}

static void write_wake(int fd) {
    uint64_t one = 1;
    ssize_t n = write(fd, &one, sizeof(one));
    (void)n;
}

long sys_net_wait(long reactor_token, long finite, long deadline_ns,
                  long max_events, long out_token) {
    real_reactor *r = reactor_of(reactor_token);
    unsigned char *out = NULL;
    size_t outlen = 0;
    long cap;
    long rraw;
    struct epoll_event evs[SYS_REACTOR_MAX_BATCH];
    sys_reg_bump_call(SYS_CALL_WAIT);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(ECANCELED);
    if (finite != 0 && finite != 1) return err(EINVAL);
    if (finite && deadline_ns < 0) return err(ERANGE);
    if (max_events < 1 || max_events > 4096) return err(EINVAL);
    rraw = buffer_span(out_token, &out, &outlen);
    if (rraw < 0) return rraw;
    cap = max_events;
    if (cap > SYS_REACTOR_MAX_BATCH) cap = SYS_REACTOR_MAX_BATCH;
    if (outlen < (size_t)cap * (size_t)SYS_EVENT_PACKET_BYTES) {
        return err(ERANGE);
    }
    for (;;) {
        long now = real_now();
        long timeout = -1;
        long due;
        long count = 0;
        int n;
        int i;
        int sawWake = 0;
        if (now < 0) return now;
        if (finite && now >= deadline_ns) return 0;
        /* Peek the eventfd before timer priority: an always-due timer must not
         * cause the wait loop to skip epoll forever and hide a fresh wake. */
        {
            int freshWake = drain_wake_once(r->wakefd);
            if (freshWake) r->wake_pending = 1;
            due = next_timer_due(r);
            if (r->wake_pending) {
                int timerDue = due >= 0 && due <= now;
                /* A freshly drained eventfd wake always wins before timer
                 * priority, so an always-due timer cannot hide it. A wake
                 * already coalesced from a full batch alternates with timers
                 * so a wake storm cannot starve them either. */
                if (freshWake || !timerDue || r->wake_first) {
                    put_event(out, 0, 0, 0, 0, 0, 0, 0, 0, 1);
                    r->wake_pending = 0;
                    r->wake_first = 0;
                    return 1;
                }
                r->wake_first = 1;
            }
        }
        /* Due timers are checked before blocking and fill the batch before
         * socket readiness, so a continuously ready watch cannot starve a
         * timer when max_events is small. */
        collect_due_timers(r, now, out, cap, &count);
        if (count > 0) return count;
        if (r->wake_pending) {
            put_event(out, 0, 0, 0, 0, 0, 0, 0, 0, 1);
            r->wake_pending = 0;
            return 1;
        }
        due = next_timer_due(r);
        if (due >= 0) {
            long delta = due - now;
            if (delta < 0) delta = 0;
            timeout = (delta + 999999L) / 1000000L;
        }
        if (finite) {
            long delta = deadline_ns - now;
            long ms = delta <= 0 ? 0 : (delta + 999999L) / 1000000L;
            if (timeout < 0 || ms < timeout) timeout = ms;
        }
        if (timeout > INT_MAX) timeout = INT_MAX;
        n = epoll_wait(r->epfd, evs, (int)cap, (int)timeout);
        if (n < 0) {
            if (errno == EINTR) continue;
            return err(errno);
        }
        now = real_now();
        if (now < 0) return now;
        /* Timers that became due during the wait get the batch before I/O. */
        collect_due_timers(r, now, out, cap, &count);
        {
            int reverse = (r->turn & 1) != 0;
            r->turn++;
            for (i = 0; i < n; i++) {
                int j = reverse ? (n - 1 - i) : i;
                struct epoll_event *ev = &evs[j];
                real_watch *w;
                int readable = 0, writable = 0, hangup = 0, failed = 0;
                if (ev->data.u64 == 0) {
                    sawWake = 1;
                    continue;
                }
                w = (real_watch *)sys_reg_peek((long)ev->data.u64, SYS_KIND_WATCH);
                if (w == NULL || w->reactor != r) continue;
                if (sys_reg_peek(w->source, w->source_kind) == NULL) {
                    long token = w->token;
                    void *obj = NULL;
                    int (*release)(void *) = NULL;
                    if (sys_reg_retire(token, SYS_KIND_WATCH, &obj, &release)) {
                        if (release != NULL) (void)release(obj);
                    }
                    continue;
                }
                if ((ev->events & EPOLLERR) != 0) {
                    failed = 1;
                } else {
                    readable = (ev->events & EPOLLIN) != 0;
                    writable = (ev->events & EPOLLOUT) != 0;
                    hangup = (ev->events & (EPOLLHUP | EPOLLRDHUP)) != 0;
                }
                if (count < cap) {
                    put_event(out, count, w->tag, readable, writable, hangup,
                              failed, 0, 0, 0);
                    count++;
                }
            }
        }
        if (sawWake) {
            drain_wake(r->wakefd);
            if (r->state != 0) return err(ECANCELED);
            if (count < cap) {
                put_event(out, count, 0, 0, 0, 0, 0, 0, 0, 1);
                count++;
            } else {
                r->wake_pending = 1;
            }
        } else if (r->wake_pending && count < cap) {
            put_event(out, count, 0, 0, 0, 0, 0, 0, 0, 1);
            count++;
            r->wake_pending = 0;
        }
        if (count > 0) return count;
        if (n == 0) {
            if (finite && now >= deadline_ns) return 0;
        }
    }
}

long sys_net_wake(long reactor_token) {
    real_reactor *r = reactor_of(reactor_token);
    sys_reg_bump_call(SYS_CALL_WAKE);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return 0;
    write_wake(r->wakefd);
    return 1;
}

long sys_net_stop(long reactor_token) {
    real_reactor *r = reactor_of(reactor_token);
    sys_reg_bump_call(SYS_CALL_STOP);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return 0;
    r->state = 1;
    write_wake(r->wakefd);
    return 1;
}

void sys_net_forget_source(long token) {
    real_reactor *r = reactors;
    while (r != NULL) {
        real_watch *w = r->watches;
        while (w != NULL) {
            real_watch *next = w->next;
            if (w->source == token) {
                void *obj = NULL;
                int (*release)(void *) = NULL;
                if (sys_reg_retire(w->token, SYS_KIND_WATCH, &obj, &release)) {
                    if (release != NULL) (void)release(obj);
                } else {
                    r->watches = next;
                    if (r->watch_count > 0) r->watch_count--;
                    free(w);
                }
            }
            w = next;
        }
        r = r->next;
    }
}

/* ---- resolver ----------------------------------------------------------- */

static long text_of(long token, size_t max, const char **out, size_t *outlen,
                    int required) {
    unsigned char *data = NULL;
    size_t len = 0;
    long r;
    if (token == 0) {
        *out = NULL;
        *outlen = 0;
        return required ? err(EINVAL) : 0;
    }
    r = buffer_span(token, &data, &len);
    if (r < 0) return r;
    if (len > max) return err(ENAMETOOLONG);
    if (memchr(data, 0, len) != NULL) return err(EINVAL);
    if (required && len == 0) return err(EINVAL);
    *out = (const char *)data;
    *outlen = len;
    return 0;
}

long sys_resolve(long host_token, long service_token, long family, long kind,
                 long flags, long out_token, long max_results) {
    const char *host = NULL;
    const char *service = NULL;
    size_t hostlen = 0;
    size_t servicelen = 0;
    struct addrinfo hints;
    struct addrinfo *res = NULL;
    struct addrinfo *ai;
    unsigned char *out = NULL;
    size_t outlen = 0;
    long count = 0;
    int rc;
    int af;
    int st;
    long r;
    sys_reg_bump_call(SYS_CALL_RESOLVE);
    if (family != SYS_FAMILY_ANY && family != SYS_FAMILY_IPV4 &&
        family != SYS_FAMILY_IPV6) return err(EINVAL);
    if (kind != SYS_SOCKET_STREAM && kind != SYS_SOCKET_DATAGRAM) {
        return err(EINVAL);
    }
    if (flags < 0 || (flags & ~1L) != 0) return err(EINVAL);
    if (max_results < 1 || max_results > SYS_RESOLVE_MAX_RESULTS) {
        return err(ERANGE);
    }
    r = text_of(host_token, 255, &host, &hostlen, 1);
    if (r < 0) return r;
    r = text_of(service_token, 31, &service, &servicelen, 0);
    if (r < 0) return r;
    (void)hostlen;
    (void)servicelen;
    r = buffer_span(out_token, &out, &outlen);
    if (r < 0) return r;
    if (outlen < (size_t)SYS_RESOLVE_HEADER +
                 (size_t)max_results * (size_t)SYS_ADDR_PACKET_BYTES) {
        return err(ERANGE);
    }
    memset(&hints, 0, sizeof(hints));
    af = family == SYS_FAMILY_IPV4 ? AF_INET :
         family == SYS_FAMILY_IPV6 ? AF_INET6 : AF_UNSPEC;
    st = kind == SYS_SOCKET_STREAM ? SOCK_STREAM : SOCK_DGRAM;
    hints.ai_family = af;
    hints.ai_socktype = st;
    hints.ai_flags = 0;
    if ((flags & 1) != 0) hints.ai_flags |= AI_NUMERICHOST | AI_NUMERICSERV;
    rc = getaddrinfo(host, service != NULL && service[0] != '\0' ? service : NULL,
                     &hints, &res);
    if (rc != 0) {
        int e = errno;
        if (res != NULL) freeaddrinfo(res);
        if (rc == EAI_SYSTEM) {
            return -(SYS_RESOLVE_SYSERR_BASE + (e > 0 ? e : EIO));
        }
        {
            int code = rc < 0 ? -rc : rc;
            return -(SYS_RESOLVE_EAI_BASE + code);
        }
    }
    for (ai = res; ai != NULL && count < max_results; ai = ai->ai_next) {
        unsigned char *rec;
        long written = 0;
        if (ai->ai_family != AF_INET && ai->ai_family != AF_INET6) continue;
        rec = out + SYS_RESOLVE_HEADER +
              (size_t)count * (size_t)SYS_ADDR_PACKET_BYTES;
        memset(rec, 0, (size_t)SYS_ADDR_PACKET_BYTES);
        {
            /* addr_to_packet writes through a registry buffer, so write the
             * record directly instead of creating a second buffer. */
            if (ai->ai_family == AF_INET) {
                const struct sockaddr_in *sin =
                    (const struct sockaddr_in *)ai->ai_addr;
                put64(rec, 0, SYS_ADDR_FAMILY_IPV4);
                put64(rec, 1, (long long)ntohs(sin->sin_port));
                put64(rec, 2, 0);
                put64(rec, 3, 4);
                memcpy(rec + SYS_ADDR_DATA_OFFSET, &sin->sin_addr, 4);
                written = 1;
            } else {
                const struct sockaddr_in6 *sin6 =
                    (const struct sockaddr_in6 *)ai->ai_addr;
                put64(rec, 0, SYS_ADDR_FAMILY_IPV6);
                put64(rec, 1, (long long)ntohs(sin6->sin6_port));
                put64(rec, 2, (long long)sin6->sin6_scope_id);
                put64(rec, 3, 16);
                memcpy(rec + SYS_ADDR_DATA_OFFSET, &sin6->sin6_addr, 16);
                written = 1;
            }
        }
        if (written > 0) count++;
    }
    freeaddrinfo(res);
    put64(out, 0, count);
    put64(out, 1, 0);
    return count;
}

/* The real adapter refuses the fake-only control symbols. */
long sysfake_net_ready(long source_token) {
    (void)source_token;
    return err(ENOSYS);
}
long sysfake_net_advance(long nanos) {
    (void)nanos;
    return err(ENOSYS);
}
long sysfake_net_pending(long source_token, long readable, long writable) {
    (void)source_token;
    (void)readable;
    (void)writable;
    return err(ENOSYS);
}
