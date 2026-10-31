/* Scripted P2 fake backend. Selected at link time only; there is no runtime
 * production toggle and no stored obo callback. Sockets are logical in-memory
 * endpoints, timers use the fake monotonic clock, and readiness is derived from
 * queues plus explicit spurious-readiness controls. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"
#include "sysnet_fake.h"
#include "sysfake_internal.h"

#include <arpa/inet.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define FAKE_MAX_BYTES (16L * 1024L * 1024L)

typedef struct {
    int family;
    int port;
    long scope;
    int length;
    unsigned char data[108];
} fake_addr;

typedef struct fake_msg {
    unsigned char *data;
    size_t len;
    fake_addr peer;
    int has_peer;
    struct fake_msg *next;
} fake_msg;

struct fake_sock {
    int kind;
    int refs;
    fake_addr addr;
    int bound;
    int connecting;
    int failed;
    long failure;
    int read_closed;
    int read_shutdown;
    int write_closed;
    int force_readable;
    int force_writable;
    struct fake_sock *peer;
    int peer_closed;
    fake_addr peer_addr;
    int has_peer_addr;
    unsigned char *in;
    size_t in_len;
    size_t in_cap;
    fake_msg *msgs_head;
    fake_msg *msgs_tail;
    struct fake_sock *pending_head;
    struct fake_sock *pending_tail;
    struct fake_sock *next_pending;
    struct fake_sock *next_global;
};

typedef struct fake_sock fake_sock;

typedef struct fake_reactor fake_reactor;

typedef struct fake_watch {
    long token;
    fake_reactor *reactor;
    long source;
    int source_kind;
    int readable;
    int writable;
    long tag;
    struct fake_watch *next;
} fake_watch;

typedef struct fake_timer {
    long token;
    fake_reactor *reactor;
    long at_ns;
    long period_ns;
    long tag;
    struct fake_timer *next;
} fake_timer;

struct fake_reactor {
    int state;
    long max_watches;
    long max_timers;
    long watch_count;
    long timer_count;
    long turn;
    int wake_pending;
    fake_watch *watches;
    fake_timer *timers;
    struct fake_reactor *next;
};

typedef struct {
    int requested;
} fake_cancel;

static fake_sock *fake_socks;
static fake_reactor *fake_reactors;
static long fake_next_port = 40000;

static long err(long e) {
    return -(e > 0 ? e : EIO);
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

static int addr_equal(const fake_addr *a, const fake_addr *b) {
    return a->family == b->family && a->port == b->port &&
           a->scope == b->scope && a->length == b->length &&
           memcmp(a->data, b->data, (size_t)a->length) == 0;
}

static long addr_from_packet(long token, fake_addr *out) {
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
    if (port < 0 || port > 65535) return err(ERANGE);
    if (scope < 0 || scope > 4294967295LL) return err(ERANGE);
    if (length < 0 || length > SYS_ADDR_DATA_MAX) return err(ERANGE);
    memset(out, 0, sizeof(*out));
    out->family = (int)family;
    out->port = (int)port;
    out->scope = (long)scope;
    out->length = (int)length;
    memcpy(out->data, p + SYS_ADDR_DATA_OFFSET, (size_t)length);
    if (family == SYS_ADDR_FAMILY_IPV4) {
        if (length != 4) return err(EINVAL);
    } else if (family == SYS_ADDR_FAMILY_IPV6) {
        if (length != 16) return err(EINVAL);
    } else if (family == SYS_ADDR_FAMILY_UNIX_PATH) {
        if (length < 1 || length > 107) return err(EINVAL);
        if (memchr(out->data, 0, (size_t)length) != NULL) return err(EINVAL);
    } else if (family == SYS_ADDR_FAMILY_UNIX_ABSTRACT) {
        if (length < 1 || length > 107) return err(EINVAL);
    } else {
        return err(EINVAL);
    }
    return 0;
}

static long addr_to_packet(const fake_addr *a, long token) {
    unsigned char *out = NULL;
    size_t outlen = 0;
    long r = buffer_span(token, &out, &outlen);
    if (r < 0) return r;
    if (outlen < (size_t)SYS_ADDR_PACKET_BYTES) return err(EINVAL);
    memset(out, 0, (size_t)SYS_ADDR_PACKET_BYTES);
    if (a == NULL || a->family == SYS_ADDR_FAMILY_NONE) return 0;
    put64(out, 0, a->family);
    put64(out, 1, a->port);
    put64(out, 2, a->scope);
    put64(out, 3, a->length);
    memcpy(out + SYS_ADDR_DATA_OFFSET, a->data, (size_t)a->length);
    return 1;
}

static fake_addr make_loopback(int family, int port) {
    fake_addr a;
    memset(&a, 0, sizeof(a));
    a.family = family;
    a.port = port;
    if (family == SYS_ADDR_FAMILY_IPV4) {
        a.length = 4;
        a.data[0] = 127;
        a.data[3] = 1;
    } else {
        a.length = 16;
        a.data[15] = 1;
    }
    return a;
}

static int is_ip(const fake_addr *a) {
    return a->family == SYS_ADDR_FAMILY_IPV4 ||
           a->family == SYS_ADDR_FAMILY_IPV6;
}

static fake_sock *find_bound(const fake_addr *a, int kind) {
    fake_sock *s = fake_socks;
    while (s != NULL) {
        if (s->kind == kind && s->bound && addr_equal(&s->addr, a)) return s;
        s = s->next_global;
    }
    return NULL;
}

static void unlink_sock(fake_sock *s) {
    fake_sock **link = &fake_socks;
    while (*link != NULL) {
        if (*link == s) {
            *link = s->next_global;
            return;
        }
        link = &(*link)->next_global;
    }
}

static void free_msgs(fake_sock *s) {
    fake_msg *m = s->msgs_head;
    while (m != NULL) {
        fake_msg *next = m->next;
        free(m->data);
        free(m);
        m = next;
    }
    s->msgs_head = NULL;
    s->msgs_tail = NULL;
}

static long queue_bytes(fake_sock *s, const unsigned char *data, size_t len) {
    size_t need;
    if (len == 0) return 0;
    if (s->in_len > (size_t)FAKE_MAX_BYTES || len > (size_t)FAKE_MAX_BYTES - s->in_len) {
        return err(ENOSPC);
    }
    need = s->in_len + len;
    if (need > s->in_cap) {
        size_t cap = s->in_cap ? s->in_cap : 64;
        unsigned char *p;
        while (cap < need) {
            if (cap > (size_t)FAKE_MAX_BYTES / 2) { cap = need; break; }
            cap *= 2;
        }
        p = (unsigned char *)realloc(s->in, cap);
        if (p == NULL) return err(ENOMEM);
        s->in = p;
        s->in_cap = cap;
    }
    memcpy(s->in + s->in_len, data, len);
    s->in_len += len;
    return 0;
}

static int release_fake_handle(void *p);

/* Called with the fake handle's reference. */
void fake_sock_release(struct fake_sock *s) {
    if (s == NULL) return;
    if (s->refs > 1) {
        s->refs--;
        return;
    }
    if (s->kind == SYS_KIND_LISTENER) {
        while (s->pending_head != NULL) {
            fake_sock *server = s->pending_head;
            long token;
            void *obj = NULL;
            int (*release)(void *) = NULL;
            s->pending_head = server->next_pending;
            server->next_pending = NULL;
            token = (long)(intptr_t)server->next_global;
            server->next_global = NULL;
            if (token != 0 && sys_reg_retire(token, SYS_KIND_CONNECTION,
                                             &obj, &release)) {
                if (release != NULL) (void)release(obj);
            } else {
                fake_sock_release(server);
            }
        }
        s->pending_tail = NULL;
    }
    if (s->peer != NULL) {
        s->peer->peer = NULL;
        s->peer->peer_closed = 1;
        s->peer = NULL;
    }
    unlink_sock(s);
    free_msgs(s);
    free(s->in);
    free(s);
}

/* Pending server sockets carry their registry token in next_global while
 * queued; helper accessors keep the intent clear. */
static long pending_token(fake_sock *server) {
    return (long)(intptr_t)server->next_global;
}

static void set_pending_token(fake_sock *server, long token) {
    server->next_global = (fake_sock *)(intptr_t)token;
}

static int release_fake_handle(void *p) {
    fake_file *f = (fake_file *)p;
    if (f == NULL) return EINVAL;
    if (f->sock != NULL) {
        fake_sock_release(f->sock);
        f->sock = NULL;
    }
    free(f);
    return 0;
}

static long new_sock_handle(int kind, fake_sock *s) {
    fake_file *f = (fake_file *)calloc(1, sizeof(*f));
    long token;
    if (f == NULL) return err(ENOMEM);
    f->sock = s;
    s->refs++;
    token = sys_reg_new_object(kind, f, release_fake_handle, 0);
    if (token < 0) {
        s->refs--;
        free(f);
    }
    return token;
}

static fake_sock *new_sock(int kind) {
    fake_sock *s = (fake_sock *)calloc(1, sizeof(*s));
    if (s == NULL) return NULL;
    s->kind = kind;
    s->refs = 0;
    return s;
}

static fake_file *sock_handle(long token, long kind) {
    fake_file *f = (fake_file *)sys_reg_peek(token, (int)kind);
    if (f == NULL || f->sock == NULL) return NULL;
    return f;
}

static long fake_assign_port(fake_addr *a) {
    if (!is_ip(a)) return 0;
    if (a->port != 0) return 0;
    if (fake_next_port > 60000) fake_next_port = 40000;
    a->port = (int)fake_next_port++;
    return 0;
}

/* ---- listen / accept / connect ----------------------------------------- */

long sys_net_listen(long addr_token, long backlog, long flags) {
    fake_addr a;
    fake_sock *s;
    long r;
    long token;
    long scripted = 0;
    sys_reg_bump_call(SYS_CALL_NET_LISTEN);
    if (backlog < 1 || backlog > 65535) return err(EINVAL);
    if (flags < 0 || (flags & ~(SYS_LISTEN_REUSE_ADDRESS | SYS_LISTEN_REUSE_PORT |
                               SYS_LISTEN_IPV6_ONLY)) != 0) {
        return err(EINVAL);
    }
    r = addr_from_packet(addr_token, &a);
    if (r < 0) return r;
    if (fake_pop_script(SYS_CALL_NET_LISTEN, &scripted) && scripted < 0) {
        return scripted;
    }
    fake_assign_port(&a);
    if (find_bound(&a, SYS_KIND_LISTENER) != NULL) return err(EADDRINUSE);
    s = new_sock(SYS_KIND_LISTENER);
    if (s == NULL) return err(ENOMEM);
    s->addr = a;
    s->bound = 1;
    token = new_sock_handle(SYS_KIND_LISTENER, s);
    if (token < 0) {
        fake_sock_release(s);
        return token;
    }
    s->next_global = fake_socks;
    fake_socks = s;
    return token;
}

long sys_net_bind_datagram(long addr_token) {
    fake_addr a;
    fake_sock *s;
    long r;
    long token;
    long scripted = 0;
    sys_reg_bump_call(SYS_CALL_NET_LISTEN);
    r = addr_from_packet(addr_token, &a);
    if (r < 0) return r;
    if (fake_pop_script(SYS_CALL_NET_LISTEN, &scripted) && scripted < 0) {
        return scripted;
    }
    fake_assign_port(&a);
    if (find_bound(&a, SYS_KIND_DATAGRAM) != NULL) return err(EADDRINUSE);
    s = new_sock(SYS_KIND_DATAGRAM);
    if (s == NULL) return err(ENOMEM);
    s->addr = a;
    s->bound = 1;
    token = new_sock_handle(SYS_KIND_DATAGRAM, s);
    if (token < 0) {
        fake_sock_release(s);
        return token;
    }
    s->next_global = fake_socks;
    fake_socks = s;
    return token;
}

static fake_sock *find_listener(const fake_addr *a) {
    return find_bound(a, SYS_KIND_LISTENER);
}

long sys_net_connect(long addr_token) {
    fake_addr a;
    fake_sock *listener;
    fake_sock *client;
    fake_sock *server;
    long r;
    long ctoken;
    long stoken;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_CONNECT);
    r = addr_from_packet(addr_token, &a);
    if (r < 0) return r;
    have_script = fake_pop_script(SYS_CALL_CONNECT, &scripted);
    if (have_script && scripted < 0 && scripted != -EINPROGRESS &&
        scripted != -EAGAIN && scripted != -EINTR) return scripted;
    listener = find_listener(&a);
    if (listener == NULL) return err(ECONNREFUSED);
    client = new_sock(SYS_KIND_CONNECTION);
    server = new_sock(SYS_KIND_CONNECTION);
    if (client == NULL || server == NULL) {
        free(client);
        free(server);
        return err(ENOMEM);
    }
    client->peer = server;
    server->peer = client;
    server->bound = 1;
    server->addr = listener->addr;
    server->peer_addr = make_loopback(a.family, (int)fake_next_port++);
    server->has_peer_addr = 1;
    if (is_ip(&a)) {
        client->bound = 1;
        client->addr = make_loopback(a.family, (int)fake_next_port++);
        client->peer_addr = a;
        client->has_peer_addr = 1;
    } else {
        client->bound = 0;
        client->peer_addr = a;
        client->has_peer_addr = 1;
    }
    client->connecting = (have_script &&
                          (scripted == -EINPROGRESS || scripted == -EAGAIN ||
                           scripted == -EINTR)) ? 1 : 0;
    stoken = new_sock_handle(SYS_KIND_CONNECTION, server);
    if (stoken < 0) {
        fake_sock_release(client);
        fake_sock_release(server);
        return stoken;
    }
    ctoken = new_sock_handle(SYS_KIND_CONNECTION, client);
    if (ctoken < 0) {
        void *obj = NULL;
        int (*release)(void *) = NULL;
        (void)sys_reg_retire(stoken, SYS_KIND_CONNECTION, &obj, &release);
        if (release != NULL) (void)release(obj);
        fake_sock_release(client);
        return ctoken;
    }
    /* Queue the server side without putting it on the global list. */
    set_pending_token(server, stoken);
    server->next_pending = NULL;
    if (listener->pending_tail == NULL) listener->pending_head = server;
    else listener->pending_tail->next_pending = server;
    listener->pending_tail = server;
    return ctoken;
}

long sys_net_accept(long listener_token) {
    fake_file *lf = sock_handle(listener_token, SYS_KIND_LISTENER);
    fake_sock *server;
    long token;
    long scripted = 0;
    sys_reg_bump_call(SYS_CALL_ACCEPT);
    if (lf == NULL) return err(EBADF);
    if (fake_pop_script(SYS_CALL_ACCEPT, &scripted) && scripted < 0) {
        return scripted;
    }
    server = lf->sock->pending_head;
    if (server == NULL) return err(EAGAIN);
    lf->sock->pending_head = server->next_pending;
    if (lf->sock->pending_head == NULL) lf->sock->pending_tail = NULL;
    server->next_pending = NULL;
    token = pending_token(server);
    set_pending_token(server, 0);
    return token;
}

long sys_net_finish_connect(long conn_token) {
    fake_file *f = sock_handle(conn_token, SYS_KIND_CONNECTION);
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_FINISH_CONNECT);
    if (f == NULL) return err(EBADF);
    if (f->sock->failed) return err(f->sock->failure);
    if (!f->sock->connecting) return 1;
    have_script = fake_pop_script(SYS_CALL_FINISH_CONNECT, &scripted);
    if (have_script && scripted < 0) {
        if (scripted == -EINTR) return 0;   /* pending; never poison */
        f->sock->failed = 1;
        f->sock->failure = -scripted;
        f->sock->connecting = 0;
        return scripted;
    }
    if (have_script && scripted == 0) return 0;
    f->sock->connecting = 0;
    return 1;
}

long sys_net_state(long conn_token) {
    fake_file *f = sock_handle(conn_token, SYS_KIND_CONNECTION);
    if (f == NULL) return 0;
    if (f->sock->failed) return err(f->sock->failure);
    if (f->sock->connecting) return 2;
    return 1;
}

/* ---- stream I/O --------------------------------------------------------- */

long fake_sock_read(struct fake_file *f, unsigned char *buf, size_t offset,
                    size_t count) {
    fake_sock *s = f->sock;
    long scripted = 0;
    int have_script;
    size_t n;
    have_script = fake_pop_script(SYS_CALL_CONN_READ, &scripted);
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    if (s->connecting) return err(EINPROGRESS);
    if (s->failed) return err(s->failure);
    if (s->read_shutdown) return 0;
    if (have_script && scripted < 0) return scripted;
    if (s->in_len == 0) {
        if (s->peer_closed || s->read_closed) return 0;
        return err(EAGAIN);
    }
    n = s->in_len;
    if (n > count) n = count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    memcpy(buf + offset, s->in, n);
    if (n < s->in_len) memmove(s->in, s->in + n, s->in_len - n);
    s->in_len -= n;
    return (long)n;
}

long fake_sock_write(struct fake_file *f, const unsigned char *buf,
                     size_t count) {
    fake_sock *s = f->sock;
    long scripted = 0;
    int have_script;
    size_t n;
    long r;
    have_script = fake_pop_script(SYS_CALL_CONN_WRITE, &scripted);
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    if (s->connecting) return err(EINPROGRESS);
    if (s->failed) return err(s->failure);
    if (s->write_closed) return err(EPIPE);
    if (have_script && scripted < 0) return scripted;
    if (count == 0) return 0;
    if (s->peer == NULL) return err(EPIPE);
    n = count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    r = queue_bytes(s->peer, buf, n);
    if (r < 0) return r;
    return (long)n;
}

long sys_net_read(long token, long kind, long buf_token, long offset,
                  long count) {
    fake_file *f = sock_handle(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    long r;
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    if (f == NULL) return err(EBADF);
    if (offset < 0 || count <= 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    return fake_sock_read(f, buf, (size_t)offset, (size_t)count);
}

long sys_net_write(long token, long kind, long buf_token, long offset,
                   long count) {
    fake_file *f = sock_handle(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    long r;
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    if (f == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    return fake_sock_write(f, buf + offset, (size_t)count);
}

long sys_net_shutdown(long token, long kind, long how) {
    fake_file *f = (fake_file *)sys_reg_peek(token, (int)kind);
    sys_reg_bump_call(SYS_CALL_SHUTDOWN);
    if (f == NULL) return err(EBADF);
    if (f->sock == NULL) return err(EOPNOTSUPP);
    if (how != 0 && how != 1) return err(EINVAL);
    if (f->sock->connecting) return err(EINPROGRESS);
    if (f->sock->failed) return err(f->sock->failure);
    if (how == 0) {
        /* SHUT_RD closes only the local read direction. It does not notify the
         * peer or make the peer's writes fail; queued/new data is not
         * delivered to this reader. */
        f->sock->read_shutdown = 1;
    } else {
        /* SHUT_WR is visible to the peer as EOF on its read side. */
        f->sock->write_closed = 1;
        if (f->sock->peer != NULL) f->sock->peer->read_closed = 1;
    }
    return 1;
}

long sys_net_dup(long token, long kind, long to_kind) {
    fake_file *src = (fake_file *)sys_reg_peek(token, (int)kind);
    fake_file *copy;
    long out;
    sys_reg_bump_call(SYS_CALL_NET_DUP);
    if (src == NULL || src->sock == NULL) return err(EBADF);
    if (to_kind <= 0) return err(EINVAL);
    if (src->sock->connecting) return err(EINPROGRESS);
    if (src->sock->failed) return err(src->sock->failure);
    copy = (fake_file *)calloc(1, sizeof(*copy));
    if (copy == NULL) return err(ENOMEM);
    copy->sock = src->sock;
    copy->sock->refs++;
    out = sys_reg_new_object((int)to_kind, copy, release_fake_handle, 0);
    if (out < 0) {
        copy->sock->refs--;
        free(copy);
    }
    return out;
}

/* ---- addresses and options --------------------------------------------- */

long sys_net_address(long token, long kind, long which, long out_token) {
    fake_file *f = (fake_file *)sys_reg_peek(token, (int)kind);
    sys_reg_bump_call(SYS_CALL_ADDRESS);
    if (f == NULL || f->sock == NULL) return err(EBADF);
    if (which != 0 && which != 1) return err(EINVAL);
    if (which == 1) {
        if (f->sock->connecting) return err(ENOTCONN);
        if (!f->sock->has_peer_addr) return addr_to_packet(NULL, out_token);
        return addr_to_packet(&f->sock->peer_addr, out_token);
    }
    if (!f->sock->bound) return addr_to_packet(NULL, out_token);
    return addr_to_packet(&f->sock->addr, out_token);
}

long sys_net_set_no_delay(long token, long enabled) {
    fake_file *f = sock_handle(token, SYS_KIND_CONNECTION);
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    if (!is_ip(&f->sock->addr) && !is_ip(&f->sock->peer_addr)) {
        return err(EOPNOTSUPP);
    }
    return 1;
}

long sys_net_set_keepalive(long token, long enabled, long idle, long interval,
                           long probes) {
    fake_file *f = sock_handle(token, SYS_KIND_CONNECTION);
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    if (idle < 0 || interval < 0 || probes < 0) return err(EINVAL);
    if (enabled && (idle < 1 || interval < 1 || probes < 1)) return err(ERANGE);
    return 1;
}

long sys_net_set_buffer(long token, long kind, long which, long size) {
    fake_file *f = (fake_file *)sys_reg_peek(token, (int)kind);
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL || f->sock == NULL) return err(EBADF);
    if (which != 0 && which != 1) return err(EINVAL);
    if (size < 1 || size > 1073741824L) return err(ERANGE);
    return 1;
}

long sys_net_set_broadcast(long token, long enabled) {
    fake_file *f = sock_handle(token, SYS_KIND_DATAGRAM);
    sys_reg_bump_call(SYS_CALL_SOCKOPT);
    if (f == NULL) return err(EBADF);
    if (enabled != 0 && enabled != 1) return err(EINVAL);
    return 1;
}

/* ---- datagrams ---------------------------------------------------------- */

long sys_net_receive(long token, long buf_token, long max, long peer_token,
                     long meta_token) {
    fake_file *f = sock_handle(token, SYS_KIND_DATAGRAM);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    unsigned char *peer = NULL;
    size_t peerlen = 0;
    unsigned char *meta = NULL;
    size_t metalen = 0;
    fake_msg *m;
    long r;
    long copied;
    long original;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_CONN_READ);
    if (f == NULL) return err(EBADF);
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
    have_script = fake_pop_script(SYS_CALL_CONN_READ, &scripted);
    if (have_script && scripted < 0) return scripted;
    m = f->sock->msgs_head;
    if (m == NULL) return err(EAGAIN);
    f->sock->msgs_head = m->next;
    if (f->sock->msgs_head == NULL) f->sock->msgs_tail = NULL;
    original = (long)m->len;
    copied = original;
    if (copied > max) copied = max;
    if (copied > 0) memcpy(buf, m->data, (size_t)copied);
    memset(meta, 0, (size_t)SYS_DGRAM_META_BYTES);
    put64(meta, 0, original);
    put64(meta, 1, original > max ? 1 : 0);
    if (m->has_peer) (void)addr_to_packet(&m->peer, peer_token);
    else (void)addr_to_packet(NULL, peer_token);
    free(m->data);
    free(m);
    return copied;
}

long sys_net_send(long token, long addr_token, long buf_token, long offset,
                  long count) {
    fake_file *f = sock_handle(token, SYS_KIND_DATAGRAM);
    fake_addr to;
    unsigned char *buf = NULL;
    size_t buflen = 0;
    fake_sock *receiver;
    fake_msg *m;
    long r;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_CONN_WRITE);
    if (f == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    if (count > 1048576) return err(EMSGSIZE);
    r = addr_from_packet(addr_token, &to);
    if (r < 0) return r;
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    have_script = fake_pop_script(SYS_CALL_CONN_WRITE, &scripted);
    if (have_script && scripted < 0) return scripted;
    receiver = find_bound(&to, SYS_KIND_DATAGRAM);
    if (receiver == NULL) return count;
    m = (fake_msg *)calloc(1, sizeof(*m));
    if (m == NULL) return err(ENOMEM);
    if (count > 0) {
        m->data = (unsigned char *)malloc((size_t)count);
        if (m->data == NULL) {
            free(m);
            return err(ENOMEM);
        }
        memcpy(m->data, buf + offset, (size_t)count);
    }
    m->len = (size_t)count;
    if (f->sock->bound) {
        m->peer = f->sock->addr;
        m->has_peer = 1;
    }
    if (receiver->msgs_tail == NULL) receiver->msgs_head = m;
    else receiver->msgs_tail->next = m;
    receiver->msgs_tail = m;
    return count;
}

/* ---- cancellation ------------------------------------------------------- */

static int release_fake_cancel(void *p) {
    free(p);
    return 0;
}

long fake_cancel_new(void) {
    fake_cancel *c = (fake_cancel *)calloc(1, sizeof(*c));
    long token;
    sys_reg_bump_call(SYS_CALL_CANCEL);
    if (c == NULL) return err(ENOMEM);
    token = sys_reg_new_object(SYS_KIND_CANCELLATION, c, release_fake_cancel, 0);
    if (token < 0) free(c);
    return token;
}

long sys_net_reactor_new(long max_watches, long max_timers) {
    return fake_reactor_new(max_watches, max_timers);
}

long sys_net_cancel_new(void) {
    return fake_cancel_new();
}

long sys_net_cancel_request(long token) {
    fake_cancel *c = (fake_cancel *)sys_reg_peek(token, SYS_KIND_CANCELLATION);
    sys_reg_bump_call(SYS_CALL_CANCEL);
    if (c == NULL) return err(EBADF);
    c->requested = 1;
    return 1;
}

long sys_net_cancel_pending(long token) {
    fake_cancel *c = (fake_cancel *)sys_reg_peek(token, SYS_KIND_CANCELLATION);
    if (c == NULL) return err(EBADF);
    return c->requested ? 1 : 0;
}

/* ---- reactor ------------------------------------------------------------ */

static int release_fake_watch(void *p) {
    fake_watch *w = (fake_watch *)p;
    fake_reactor *r = w->reactor;
    fake_watch **link;
    if (r != NULL) {
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

static int release_fake_timer(void *p) {
    fake_timer *t = (fake_timer *)p;
    fake_reactor *r = t->reactor;
    fake_timer **link;
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

static int release_fake_reactor(void *p) {
    fake_reactor *r = (fake_reactor *)p;
    fake_reactor **link;
    void *obj = NULL;
    int (*release)(void *) = NULL;
    if (r == NULL) return EINVAL;
    r->state = 2;
    while (r->watches != NULL) {
        fake_watch *w = r->watches;
        if (sys_reg_retire(w->token, SYS_KIND_WATCH, &obj, &release)) {
            if (release != NULL) (void)release(obj);
        } else {
            r->watches = w->next;
            if (r->watch_count > 0) r->watch_count--;
        }
    }
    while (r->timers != NULL) {
        fake_timer *t = r->timers;
        if (sys_reg_retire(t->token, SYS_KIND_TIMER, &obj, &release)) {
            if (release != NULL) (void)release(obj);
        } else {
            r->timers = t->next;
            if (r->timer_count > 0) r->timer_count--;
        }
    }
    link = &fake_reactors;
    while (*link != NULL) {
        if (*link == r) {
            *link = r->next;
            break;
        }
        link = &(*link)->next;
    }
    free(r);
    return 0;
}

long fake_reactor_new(long max_watches, long max_timers) {
    fake_reactor *r;
    long token;
    sys_reg_bump_call(SYS_CALL_REACTOR_NEW);
    if (max_watches < 1 || max_watches > 65536) return err(EINVAL);
    if (max_timers < 1 || max_timers > 65536) return err(EINVAL);
    r = (fake_reactor *)calloc(1, sizeof(*r));
    if (r == NULL) return err(ENOMEM);
    r->max_watches = max_watches;
    r->max_timers = max_timers;
    r->next = fake_reactors;
    fake_reactors = r;
    token = sys_reg_new_object(SYS_KIND_REACTOR, r, release_fake_reactor, 0);
    if (token < 0) {
        fake_reactors = r->next;
        free(r);
    }
    return token;
}

static fake_reactor *fake_reactor_of(long token) {
    return (fake_reactor *)sys_reg_peek(token, SYS_KIND_REACTOR);
}

static int watchable_kind(long kind) {
    return kind == SYS_KIND_STREAM || kind == SYS_KIND_LISTENER ||
           kind == SYS_KIND_CONNECTION || kind == SYS_KIND_DATAGRAM ||
           kind == SYS_KIND_CANCELLATION ||
           kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT;
}

static long source_ready(long token, long kind, int *readable, int *writable,
                         int *hangup, int *failed) {
    *readable = 0; *writable = 0; *hangup = 0; *failed = 0;
    if (kind == SYS_KIND_CANCELLATION) {
        fake_cancel *c = (fake_cancel *)sys_reg_peek(token, (int)kind);
        if (c == NULL) return err(EBADF);
        if (c->requested) *readable = 1;
        return 0;
    }
    {
        fake_file *f = (fake_file *)sys_reg_peek(token, (int)kind);
        fake_sock *s;
        if (f == NULL) return err(EBADF);
        s = f->sock;
        if (s == NULL) return err(EOPNOTSUPP);
        if (s->failed) { *failed = 1; return 0; }
        if (s->kind == SYS_KIND_LISTENER) {
            if (s->pending_head != NULL) *readable = 1;
        } else if (s->kind == SYS_KIND_DATAGRAM) {
            if (s->msgs_head != NULL) *readable = 1;
        } else {
            if (s->in_len > 0) *readable = 1;
            if (s->read_shutdown || s->read_closed ||
                (s->peer_closed && s->in_len == 0)) {
                /* EOF is readable (a zero-byte read) and a hangup in the
                 * level-triggered contract, matching epoll EPOLLIN|RDHUP. */
                *readable = 1;
                *hangup = 1;
            }
            if (s->connecting) *writable = 1;
            else if (!s->write_closed) *writable = 1;
        }
        if (s->force_readable) *readable = 1;
        if (s->force_writable) *writable = 1;
        if (*failed) { *readable = 0; *writable = 0; *hangup = 0; }
        return 0;
    }
}

long sys_net_watch(long reactor_token, long source_token, long source_kind,
                   long readable, long writable, long tag) {
    fake_reactor *r = fake_reactor_of(reactor_token);
    fake_watch *w;
    long token;
    long probe;
    sys_reg_bump_call(SYS_CALL_WATCH);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(EBUSY);
    if (!watchable_kind(source_kind)) return err(EOPNOTSUPP);
    if ((readable != 0 && readable != 1) || (writable != 0 && writable != 1)) {
        return err(EINVAL);
    }
    if (!readable && !writable) return err(EINVAL);
    probe = source_ready(source_token, source_kind, &(int){0}, &(int){0},
                         &(int){0}, &(int){0});
    if (probe < 0) return probe;
    if (r->watch_count >= r->max_watches) return err(EMFILE);
    w = (fake_watch *)calloc(1, sizeof(*w));
    if (w == NULL) return err(ENOMEM);
    w->reactor = r;
    w->source = source_token;
    w->source_kind = (int)source_kind;
    w->readable = (int)readable;
    w->writable = (int)writable;
    w->tag = tag;
    token = sys_reg_new_object(SYS_KIND_WATCH, w, release_fake_watch, 0);
    if (token < 0) {
        free(w);
        return token;
    }
    w->token = token;
    w->next = r->watches;
    r->watches = w;
    r->watch_count++;
    return token;
}

long sys_net_modify(long watch_token, long readable, long writable) {
    fake_watch *w = (fake_watch *)sys_reg_peek(watch_token, SYS_KIND_WATCH);
    sys_reg_bump_call(SYS_CALL_MODIFY);
    if (w == NULL || w->reactor == NULL || w->reactor->state == 2) {
        return err(EBADF);
    }
    if ((readable != 0 && readable != 1) || (writable != 0 && writable != 1)) {
        return err(EINVAL);
    }
    if (!readable && !writable) return err(EINVAL);
    w->readable = (int)readable;
    w->writable = (int)writable;
    return 1;
}

long sys_net_remove(long watch_token) {
    fake_watch *w = (fake_watch *)sys_reg_peek(watch_token, SYS_KIND_WATCH);
    void *obj = NULL;
    int (*release)(void *) = NULL;
    sys_reg_bump_call(SYS_CALL_REMOVE);
    if (w == NULL) return 0;
    if (!sys_reg_retire(watch_token, SYS_KIND_WATCH, &obj, &release)) return 0;
    if (release != NULL) (void)release(obj);
    return 1;
}

long sys_net_timer(long reactor_token, long at_ns, long period_ns, long tag) {
    fake_reactor *r = fake_reactor_of(reactor_token);
    fake_timer *t;
    long token;
    sys_reg_bump_call(SYS_CALL_TIMER);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(EBUSY);
    if (at_ns < 0 || period_ns < 0) return err(ERANGE);
    if (r->timer_count >= r->max_timers) return err(EMFILE);
    t = (fake_timer *)calloc(1, sizeof(*t));
    if (t == NULL) return err(ENOMEM);
    t->reactor = r;
    t->at_ns = at_ns;
    t->period_ns = period_ns;
    t->tag = tag;
    token = sys_reg_new_object(SYS_KIND_TIMER, t, release_fake_timer, 0);
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
    fake_timer *t = (fake_timer *)sys_reg_peek(timer_token, SYS_KIND_TIMER);
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

static void collect_timers(fake_reactor *r, long now, unsigned char *out,
                           long cap, long *count) {
    fake_timer *t = r->timers;
    while (t != NULL) {
        fake_timer *next = t->next;
        if (*count >= cap) return;
        if (now >= t->at_ns) {
            long ticks = 1;
            long nextAt = t->at_ns;
            if (t->period_ns > 0) {
                long elapsed = now - t->at_ns;
                ticks = 1 + elapsed / t->period_ns;
                if (ticks < 1) ticks = 1;
                if (ticks > (INT64_MAX - t->at_ns) / t->period_ns) nextAt = -1;
                else nextAt = t->at_ns + ticks * t->period_ns;
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

static void collect_io(fake_reactor *r, unsigned char *out, long cap,
                       long *count) {
    fake_watch *w;
    long total = 0;
    long pos = 0;
    long start;
    for (w = r->watches; w != NULL; w = w->next) {
        int rd, wr, hu, fa;
        if (source_ready(w->source, w->source_kind, &rd, &wr, &hu, &fa) < 0) continue;
        if ((w->readable && rd) || (w->writable && wr) || hu || fa) total++;
    }
    if (total == 0) return;
    start = r->turn % total;
    r->turn++;
    for (w = r->watches; w != NULL; w = w->next) {
        int rd, wr, hu, fa;
        long rel;
        if (source_ready(w->source, w->source_kind, &rd, &wr, &hu, &fa) < 0) continue;
        if (!((w->readable && rd) || (w->writable && wr) || hu || fa)) continue;
        rel = (pos - start + total) % total;
        pos++;
        if (rel >= cap) continue;
        put_event(out, *count, w->tag, w->readable && rd, w->writable && wr,
                  hu, fa, 0, 0, 0);
        *count += 1;
    }
}

static long fake_next_due(fake_reactor *r) {
    fake_timer *t = r->timers;
    long best = -1;
    while (t != NULL) {
        if (best < 0 || t->at_ns < best) best = t->at_ns;
        t = t->next;
    }
    return best;
}

long sys_net_wait(long reactor_token, long finite, long deadline_ns,
                  long max_events, long out_token) {
    fake_reactor *r = fake_reactor_of(reactor_token);
    unsigned char *out = NULL;
    size_t outlen = 0;
    long cap;
    long rr;
    sys_reg_bump_call(SYS_CALL_WAIT);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return err(ECANCELED);
    if (finite != 0 && finite != 1) return err(EINVAL);
    if (finite && deadline_ns < 0) return err(ERANGE);
    if (max_events < 1 || max_events > 4096) return err(EINVAL);
    rr = buffer_span(out_token, &out, &outlen);
    if (rr < 0) return rr;
    cap = max_events;
    if (cap > SYS_REACTOR_MAX_BATCH) cap = SYS_REACTOR_MAX_BATCH;
    if (outlen < (size_t)cap * (size_t)SYS_EVENT_PACKET_BYTES) {
        return err(ERANGE);
    }
    for (;;) {
        long now = fake_clock_now();
        long count = 0;
        long due;
        collect_io(r, out, cap, &count);
        collect_timers(r, now, out, cap, &count);
        if (r->wake_pending && count < cap) {
            put_event(out, count, 0, 0, 0, 0, 0, 0, 0, 1);
            count++;
            r->wake_pending = 0;
        }
        if (count > 0) return count;
        due = fake_next_due(r);
        if (finite) {
            if (now >= deadline_ns) return 0;
            if (due >= 0 && due <= deadline_ns) {
                fake_clock_set(due);
                continue;
            }
            fake_clock_set(deadline_ns);
            return 0;
        }
        if (due >= 0) {
            fake_clock_set(due);
            continue;
        }
        /* An infinite fake wait with no readiness or timer returns an empty
         * batch instead of hanging the single-threaded test process. */
        return 0;
    }
}

long sys_net_wake(long reactor_token) {
    fake_reactor *r = fake_reactor_of(reactor_token);
    sys_reg_bump_call(SYS_CALL_WAKE);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return 0;
    r->wake_pending = 1;
    return 1;
}

long sys_net_stop(long reactor_token) {
    fake_reactor *r = fake_reactor_of(reactor_token);
    sys_reg_bump_call(SYS_CALL_STOP);
    if (r == NULL) return err(EBADF);
    if (r->state != 0) return 0;
    r->state = 1;
    return 1;
}

void fake_net_forget_source(long token) {
    fake_reactor *r = fake_reactors;
    while (r != NULL) {
        fake_watch *w = r->watches;
        while (w != NULL) {
            fake_watch *next = w->next;
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

/* ---- fake resolver ------------------------------------------------------ */

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

static long fake_service_port(const char *service, int *port) {
    int value = 0;
    size_t i;
    if (service == NULL || service[0] == '\0') { *port = 0; return 0; }
    for (i = 0; service[i] != '\0'; i++) {
        if (service[i] < '0' || service[i] > '9') return -(SYS_RESOLVE_EAI_BASE + 8);
        value = value * 10 + (service[i] - '0');
        if (value > 65535) return -(SYS_RESOLVE_EAI_BASE + 8);
    }
    if (i == 0 || i > 5) return -(SYS_RESOLVE_EAI_BASE + 8);
    *port = value;
    return 0;
}

static long put_result(unsigned char *out, long index, int family,
                       const unsigned char *octets, int port) {
    unsigned char *rec = out + SYS_RESOLVE_HEADER +
                         (size_t)index * (size_t)SYS_ADDR_PACKET_BYTES;
    memset(rec, 0, (size_t)SYS_ADDR_PACKET_BYTES);
    put64(rec, 0, family);
    put64(rec, 1, port);
    put64(rec, 2, 0);
    put64(rec, 3, family == SYS_ADDR_FAMILY_IPV4 ? 4 : 16);
    memcpy(rec + SYS_ADDR_DATA_OFFSET, octets,
           family == SYS_ADDR_FAMILY_IPV4 ? 4 : 16);
    return 1;
}

long sys_resolve(long host_token, long service_token, long family, long kind,
                 long flags, long out_token, long max_results) {
    const char *host = NULL;
    const char *service = NULL;
    size_t hostlen = 0;
    size_t servicelen = 0;
    unsigned char *out = NULL;
    size_t outlen = 0;
    unsigned char v4[4];
    unsigned char v6[16];
    long r;
    long count = 0;
    long scripted = 0;
    int port = 0;
    int numeric;
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
    if (fake_pop_script(SYS_CALL_RESOLVE, &scripted) && scripted < 0) {
        return scripted;
    }
    r = fake_service_port(service, &port);
    if (r < 0) return r;
    numeric = (flags & 1) != 0;
    memset(v4, 0, sizeof(v4));
    memset(v6, 0, sizeof(v6));
    if (strcmp(host, "localhost") == 0) {
        if (numeric) return -(SYS_RESOLVE_EAI_BASE + 2);
        if (family == SYS_FAMILY_ANY || family == SYS_FAMILY_IPV4) {
            v4[0] = 127; v4[3] = 1;
            count += put_result(out, count, SYS_ADDR_FAMILY_IPV4, v4, port);
        }
        if ((family == SYS_FAMILY_ANY || family == SYS_FAMILY_IPV6) &&
            count < max_results) {
            v6[15] = 1;
            count += put_result(out, count, SYS_ADDR_FAMILY_IPV6, v6, port);
        }
    } else if (strcmp(host, "example.test") == 0) {
        if (numeric) return -(SYS_RESOLVE_EAI_BASE + 2);
        if (family == SYS_FAMILY_ANY || family == SYS_FAMILY_IPV4) {
            v4[0] = 192; v4[1] = 0; v4[2] = 2; v4[3] = 10;
            count += put_result(out, count, SYS_ADDR_FAMILY_IPV4, v4, port);
        }
    } else if (strcmp(host, "resolve-again.test") == 0) {
        return -(SYS_RESOLVE_EAI_BASE + 3);
    } else if (strcmp(host, "notfound.test") == 0) {
        return -(SYS_RESOLVE_EAI_BASE + 2);
    } else if (strcmp(host, "system.test") == 0) {
        return -(SYS_RESOLVE_SYSERR_BASE + 5);
    } else if (inet_pton(AF_INET, host, v4) == 1) {
        if (family == SYS_FAMILY_ANY || family == SYS_FAMILY_IPV4) {
            count += put_result(out, count, SYS_ADDR_FAMILY_IPV4, v4, port);
        }
    } else if (inet_pton(AF_INET6, host, v6) == 1) {
        if (family == SYS_FAMILY_ANY || family == SYS_FAMILY_IPV6) {
            count += put_result(out, count, SYS_ADDR_FAMILY_IPV6, v6, port);
        }
    } else {
        return -(SYS_RESOLVE_EAI_BASE + 2);
    }
    if (count == 0) return -(SYS_RESOLVE_EAI_BASE + 2);
    put64(out, 0, count);
    put64(out, 1, 0);
    return count;
}

long sysfake_net_ready(long source_token) {
    fake_file *f = (fake_file *)sys_reg_peek(source_token, SYS_KIND_CONNECTION);
    if (f == NULL || f->sock == NULL) return 0;
    f->sock->force_readable = 1;
    return 1;
}

long sysfake_net_advance(long nanos) {
    long now = fake_clock_now();
    if (nanos < 0 || now > INT64_MAX - nanos) return -ERANGE;
    fake_clock_set(now + nanos);
    return 1;
}

long sysfake_net_pending(long source_token, long readable, long writable) {
    fake_file *f = (fake_file *)sys_reg_peek(source_token, SYS_KIND_CONNECTION);
    if (f == NULL || f->sock == NULL) return 0;
    f->sock->force_readable = readable != 0;
    f->sock->force_writable = writable != 0;
    return 1;
}
