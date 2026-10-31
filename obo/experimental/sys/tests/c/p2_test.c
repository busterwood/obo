/* P2 C harness for the real and scripted-fake adapters.
 *
 * Trusted C: drives the scalar adapter directly and checks socket state,
 * bounded address/event packets, reactor timers/wake/stop, datagram
 * truncation, resolver status encoding, descriptor pressure and cleanup. The
 * same source builds against either adapter; argv[1] "fake" only selects the
 * fixture paths and fake-only EMFILE scripting.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define CHECK(cond, what) do { \
    if (!(cond)) { fprintf(stderr, "p2_c: %s failed: %s\n", what, #cond); return 1; } \
} while (0)

extern long sysfake_reset(void) __attribute__((weak));
extern long sysfake_push(long call_class, long result) __attribute__((weak));

static long mkbuf(const unsigned char *p, long n) {
    long t = sys_res_new(SYS_KIND_BYTE_BUFFER, n, 0);
    unsigned char *q = NULL;
    size_t l = 0;
    if (t < 0) return t;
    if (sys_reg_buffer_data(t, &q, &l) < 0 || l < (size_t)n) return -EIO;
    if (n > 0) memcpy(q, p, (size_t)n);
    return t;
}

static long field(long tok, long i) {
    unsigned char *p = NULL;
    size_t n = 0;
    int64_t v = 0;
    if (sys_reg_buffer_data(tok, &p, &n) < 0) return -1;
    if (n < (size_t)(i + 1) * 8) return -2;
    memcpy(&v, p + (size_t)i * 8, sizeof(v));
    return (long)v;
}

static void put64(unsigned char *p, long i, long long v) {
    int64_t x = (int64_t)v;
    memcpy(p + (size_t)i * 8, &x, sizeof(x));
}

static long mkaddr(long family, long port, const unsigned char *data, long len) {
    unsigned char p[160];
    memset(p, 0, sizeof(p));
    put64(p, 0, family);
    put64(p, 1, port);
    put64(p, 2, 0);
    put64(p, 3, len);
    if (len > 0) memcpy(p + 32, data, (size_t)len);
    return mkbuf(p, 160);
}

static long zero_addr(void) {
    unsigned char p[160];
    memset(p, 0, sizeof(p));
    return mkbuf(p, 160);
}

static long deadline_ms(long ms) {
    long now = sys_clock_monotonic_ns();
    if (now < 0) return now;
    return now + ms * 1000000L;
}

static long accept_retry(long listener) {
    long r = -EAGAIN;
    int i;
    for (i = 0; i < 2000; i++) {
        r = sys_net_accept(listener);
        if (r >= 0) return r;
        if (r != -EAGAIN && r != -EINTR) return r;
        usleep(1000);
    }
    return r;
}

static long finish_retry(long conn) {
    long r = 0;
    int i;
    for (i = 0; i < 2000; i++) {
        r = sys_net_finish_connect(conn);
        if (r != 0) return r;
        usleep(1000);
    }
    return r;
}

static long read_retry(long token, long kind, long buf, long max) {
    long r = -EAGAIN;
    int i;
    for (i = 0; i < 2000; i++) {
        r = sys_net_read(token, kind, buf, 0, max);
        if (r != -EAGAIN && r != -EINTR) return r;
        usleep(1000);
    }
    return r;
}

static long event_field(long tok, long index, long field_index) {
    unsigned char *p = NULL;
    size_t n = 0;
    int64_t v = 0;
    if (sys_reg_buffer_data(tok, &p, &n) < 0) return -1;
    if (n < (size_t)(index + 1) * SYS_EVENT_PACKET_BYTES) return -1;
    memcpy(&v, p + (size_t)index * SYS_EVENT_PACKET_BYTES +
              (size_t)field_index * 8, sizeof(v));
    return (long)v;
}

static long address_field(long tok, long index) {
    return field(tok, index);
}

static int tcp_roundtrip(int family, const unsigned char *ip, long iplen) {
    long addr = mkaddr(family == 4 ? SYS_ADDR_FAMILY_IPV4 : SYS_ADDR_FAMILY_IPV6,
                       0, ip, iplen);
    long listener, local, target, conn, accepted, out, in;
    long r;
    unsigned char *p = NULL;
    size_t n = 0;
    long port;
    CHECK(addr > 0, "tcp addr");
    listener = sys_net_listen(addr, 128, SYS_LISTEN_REUSE_ADDRESS | SYS_LISTEN_IPV6_ONLY);
    CHECK(listener > 0, "tcp listen");
    local = zero_addr();
    CHECK(sys_net_address(listener, SYS_KIND_LISTENER, 0, local) == 1, "tcp local");
    port = address_field(local, 1);
    CHECK(port > 0, "tcp port discovered");
    target = mkaddr(family == 4 ? SYS_ADDR_FAMILY_IPV4 : SYS_ADDR_FAMILY_IPV6,
                    port, ip, iplen);
    conn = sys_net_connect(target);
    CHECK(conn > 0, "tcp connect token");
    CHECK(finish_retry(conn) == 1, "tcp finish");
    accepted = accept_retry(listener);
    CHECK(accepted > 0, "tcp accept");
    out = mkbuf((const unsigned char *)"hello", 5);
    CHECK(sys_net_write(conn, SYS_KIND_CONNECTION, out, 0, 5) == 5, "tcp write");
    in = zero_addr();
    r = read_retry(accepted, SYS_KIND_CONNECTION, in, 5);
    CHECK(r == 5, "tcp read");
    CHECK(sys_reg_buffer_data(in, &p, &n) == 0 && n >= 5, "tcp read data");
    CHECK(memcmp(p, "hello", 5) == 0, "tcp data");
    CHECK(sys_net_shutdown(conn, SYS_KIND_CONNECTION, 1) == 1, "tcp shutdown");
    r = read_retry(accepted, SYS_KIND_CONNECTION, in, 5);
    CHECK(r == 0, "tcp eof");
    CHECK(sys_net_shutdown(conn, SYS_KIND_CONNECTION, 1) == 1, "tcp shutdown idempotent");
    CHECK(sys_res_close(accepted, SYS_KIND_CONNECTION) == 1, "tcp accepted close");
    CHECK(sys_res_close(conn, SYS_KIND_CONNECTION) == 1, "tcp conn close");
    CHECK(sys_res_close(listener, SYS_KIND_LISTENER) == 1, "tcp listener close");
    sys_res_close(addr, SYS_KIND_BYTE_BUFFER);
    sys_res_close(local, SYS_KIND_BYTE_BUFFER);
    sys_res_close(target, SYS_KIND_BYTE_BUFFER);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    sys_res_close(in, SYS_KIND_BYTE_BUFFER);
    return 0;
}

static int unix_stream(const char *path) {
    long addr = mkaddr(SYS_ADDR_FAMILY_UNIX_PATH, 0,
                       (const unsigned char *)path, (long)strlen(path));
    long listener, target, conn, accepted, out, in;
    long r;
    unsigned char *p = NULL;
    size_t n = 0;
    unlink(path);
    listener = sys_net_listen(addr, 64, 0);
    CHECK(listener > 0, "unix listen");
    target = mkaddr(SYS_ADDR_FAMILY_UNIX_PATH, 0,
                    (const unsigned char *)path, (long)strlen(path));
    conn = sys_net_connect(target);
    CHECK(conn > 0, "unix connect");
    CHECK(finish_retry(conn) == 1, "unix finish");
    accepted = accept_retry(listener);
    CHECK(accepted > 0, "unix accept");
    out = mkbuf((const unsigned char *)"unix", 4);
    CHECK(sys_net_write(conn, SYS_KIND_CONNECTION, out, 0, 4) == 4, "unix write");
    in = zero_addr();
    r = read_retry(accepted, SYS_KIND_CONNECTION, in, 4);
    CHECK(r == 4, "unix read");
    CHECK(sys_reg_buffer_data(in, &p, &n) == 0 && n >= 4, "unix data span");
    CHECK(memcmp(p, "unix", 4) == 0, "unix data");
    CHECK(sys_res_close(accepted, SYS_KIND_CONNECTION) == 1, "unix accepted close");
    CHECK(sys_res_close(conn, SYS_KIND_CONNECTION) == 1, "unix conn close");
    CHECK(sys_res_close(listener, SYS_KIND_LISTENER) == 1, "unix listener close");
    sys_res_close(addr, SYS_KIND_BYTE_BUFFER);
    sys_res_close(target, SYS_KIND_BYTE_BUFFER);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    sys_res_close(in, SYS_KIND_BYTE_BUFFER);
    unlink(path);
    return 0;
}

static int datagram_checks(const char *patha, const char *pathb) {
    long aa = mkaddr(SYS_ADDR_FAMILY_UNIX_PATH, 0,
                     (const unsigned char *)patha, (long)strlen(patha));
    long ab = mkaddr(SYS_ADDR_FAMILY_UNIX_PATH, 0,
                     (const unsigned char *)pathb, (long)strlen(pathb));
    long sender, receiver, empty, payload, peer, meta, data, out, metaout;
    long r;
    unsigned char *p = NULL;
    size_t n = 0;
    unsigned char big[100];
    unsigned char ten[10];
    int i;
    unlink(patha);
    unlink(pathb);
    sender = sys_net_bind_datagram(aa);
    CHECK(sender > 0, "dgram bind sender");
    receiver = sys_net_bind_datagram(ab);
    CHECK(receiver > 0, "dgram bind receiver");
    empty = mkbuf((const unsigned char *)"", 0);
    CHECK(sys_net_send(sender, ab, empty, 0, 0) == 0, "dgram empty send");
    data = zero_addr();
    peer = zero_addr();
    meta = zero_addr();
    r = sys_net_receive(receiver, data, 16, peer, meta);
    CHECK(r == 0, "dgram empty receive");
    CHECK(field(meta, 0) == 0 && field(meta, 1) == 0, "dgram empty metadata");
    CHECK(field(peer, 0) == SYS_ADDR_FAMILY_UNIX_PATH, "dgram empty peer");
    for (i = 0; i < 100; i++) big[i] = (unsigned char)i;
    payload = mkbuf(big, 100);
    CHECK(sys_net_send(sender, ab, payload, 0, 100) == 100, "dgram send");
    memset(ten, 0, sizeof(ten));
    out = mkbuf(ten, 10);
    metaout = zero_addr();
    r = sys_net_receive(receiver, out, 10, peer, metaout);
    CHECK(r == 10, "dgram truncated receive");
    CHECK(field(metaout, 0) == 100 && field(metaout, 1) == 1, "dgram truncation metadata");
    CHECK(field(peer, 0) == SYS_ADDR_FAMILY_UNIX_PATH, "dgram peer present");
    CHECK(sys_net_set_buffer(receiver, SYS_KIND_DATAGRAM, 0, 65536) == 1, "dgram rcvbuf");
    CHECK(sys_net_set_broadcast(receiver, 1) == 1, "dgram broadcast");
    CHECK(sys_res_close(receiver, SYS_KIND_DATAGRAM) == 1, "dgram receiver close");
    CHECK(sys_res_close(sender, SYS_KIND_DATAGRAM) == 1, "dgram sender close");
    sys_res_close(aa, SYS_KIND_BYTE_BUFFER);
    sys_res_close(ab, SYS_KIND_BYTE_BUFFER);
    sys_res_close(empty, SYS_KIND_BYTE_BUFFER);
    sys_res_close(payload, SYS_KIND_BYTE_BUFFER);
    sys_res_close(data, SYS_KIND_BYTE_BUFFER);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    sys_res_close(peer, SYS_KIND_BYTE_BUFFER);
    sys_res_close(meta, SYS_KIND_BYTE_BUFFER);
    sys_res_close(metaout, SYS_KIND_BYTE_BUFFER);
    (void)p;
    (void)n;
    unlink(patha);
    unlink(pathb);
    return 0;
}

static int reactor_checks(unsigned char ip[4]) {
    long addr = mkaddr(SYS_ADDR_FAMILY_IPV4, 0, ip, 4);
    long listener, local, target, conn, accepted, reactor, watch, timer;
    long out, wake, events, r, d;
    long port;
    unsigned char evbuf[SYS_REACTOR_MAX_BATCH * SYS_EVENT_PACKET_BYTES];
    CHECK(addr > 0, "reactor addr");
    listener = sys_net_listen(addr, 64, SYS_LISTEN_REUSE_ADDRESS);
    CHECK(listener > 0, "reactor listener");
    local = zero_addr();
    CHECK(sys_net_address(listener, SYS_KIND_LISTENER, 0, local) == 1, "reactor local");
    port = address_field(local, 1);
    target = mkaddr(SYS_ADDR_FAMILY_IPV4, port, ip, 4);
    reactor = sys_net_reactor_new(16, 16);
    CHECK(reactor > 0, "reactor new");
    watch = sys_net_watch(reactor, listener, SYS_KIND_LISTENER, 1, 0, 77);
    CHECK(watch > 0, "reactor watch listener");
    conn = sys_net_connect(target);
    CHECK(conn > 0, "reactor connect");
    memset(evbuf, 0, sizeof(evbuf));
    out = mkbuf(evbuf, (long)sizeof(evbuf));
    d = deadline_ms(2000);
    r = sys_net_wait(reactor, 1, d, 8, out);
    CHECK(r >= 1, "reactor wait listener");
    CHECK(event_field(out, 0, 0) == 77 && event_field(out, 0, 1) == 1, "reactor listener event");
    accepted = accept_retry(listener);
    CHECK(accepted > 0, "reactor accept");
    CHECK(sys_net_modify(watch, 0, 0) == -EINVAL, "reactor empty interest rejected");
    CHECK(sys_net_remove(watch) == 1, "reactor remove");
    CHECK(sys_net_remove(watch) == 0, "reactor remove idempotent");
    d = sys_clock_monotonic_ns() + 5000000L;
    timer = sys_net_timer(reactor, d, 5000000L, 88);
    CHECK(timer > 0, "reactor timer");
    sys_sleep_until(sys_clock_monotonic_ns() + 16000000L);
    d = deadline_ms(1000);
    r = sys_net_wait(reactor, 1, d, 8, out);
    CHECK(r >= 1 && event_field(out, 0, 5) == 1 && event_field(out, 0, 6) >= 2,
          "reactor periodic timer");
    CHECK(sys_net_cancel_timer(timer) == 1, "reactor timer cancel");
    CHECK(sys_net_cancel_timer(timer) == 0, "reactor timer cancel idempotent");
    CHECK(sys_net_wake(reactor) == 1, "reactor wake");
    d = deadline_ms(1000);
    r = sys_net_wait(reactor, 1, d, 8, out);
    CHECK(r >= 1 && event_field(out, 0, 7) == 1, "reactor wake event");
    CHECK(sys_net_stop(reactor) == 1, "reactor stop");
    CHECK(sys_net_stop(reactor) == 0, "reactor stop idempotent");
    d = deadline_ms(100);
    r = sys_net_wait(reactor, 1, d, 8, out);
    CHECK(r == -ECANCELED, "reactor wait after stop");
    CHECK(sys_res_close(accepted, SYS_KIND_CONNECTION) == 1, "reactor accepted close");
    CHECK(sys_res_close(conn, SYS_KIND_CONNECTION) == 1, "reactor conn close");
    CHECK(sys_res_close(listener, SYS_KIND_LISTENER) == 1, "reactor listener close");
    CHECK(sys_res_close(reactor, SYS_KIND_REACTOR) == 1, "reactor close");
    events = wake = 0;
    (void)events;
    (void)wake;
    sys_res_close(addr, SYS_KIND_BYTE_BUFFER);
    sys_res_close(local, SYS_KIND_BYTE_BUFFER);
    sys_res_close(target, SYS_KIND_BYTE_BUFFER);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    return 0;
}

static int resolver_checks(void) {
    long host = mkbuf((const unsigned char *)"127.0.0.1", 9);
    long service = mkbuf((const unsigned char *)"80", 2);
    long badhost = mkbuf((const unsigned char *)"not-an-ip", 9);
    unsigned char outbuf[16 + 8 * 160];
    long out;
    memset(outbuf, 0, sizeof(outbuf));
    out = mkbuf(outbuf, (long)sizeof(outbuf));
    long r;
    CHECK(host > 0 && service > 0 && badhost > 0 && out > 0, "resolver buffers");
    r = sys_resolve(host, service, SYS_FAMILY_IPV4, SYS_SOCKET_STREAM, 1, out, 8);
    CHECK(r == 1, "resolver numeric");
    CHECK(field(out, 2) == SYS_ADDR_FAMILY_IPV4 && field(out, 2 + 3) == 4,
          "resolver record");
    r = sys_resolve(host, service, SYS_FAMILY_IPV6, SYS_SOCKET_STREAM, 1, out, 8);
    CHECK(r <= -SYS_RESOLVE_EAI_BASE, "resolver family mismatch is EAI");
    r = sys_resolve(badhost, service, SYS_FAMILY_ANY, SYS_SOCKET_STREAM, 1, out, 8);
    CHECK(r <= -SYS_RESOLVE_EAI_BASE, "resolver numeric error is EAI");
    CHECK(sys_resolve(host, service, 9, SYS_SOCKET_STREAM, 0, out, 8) == -EINVAL,
          "resolver bad family");
    sys_res_close(host, SYS_KIND_BYTE_BUFFER);
    sys_res_close(service, SYS_KIND_BYTE_BUFFER);
    sys_res_close(badhost, SYS_KIND_BYTE_BUFFER);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    return 0;
}

static int descriptor_pressure(int fake, unsigned char ip[4]) {
    long addr = mkaddr(SYS_ADDR_FAMILY_IPV4, 0, ip, 4);
    long listener, local, target, conn, accepted;
    long port;
    struct rlimit oldrl, newrl;
    long r;
    int i;
    int high = 0;
    CHECK(addr > 0, "emfile addr");
    listener = sys_net_listen(addr, 128, SYS_LISTEN_REUSE_ADDRESS);
    CHECK(listener > 0, "emfile listener");
    local = zero_addr();
    CHECK(sys_net_address(listener, SYS_KIND_LISTENER, 0, local) == 1, "emfile local");
    port = address_field(local, 1);
    target = mkaddr(SYS_ADDR_FAMILY_IPV4, port, ip, 4);
    conn = sys_net_connect(target);
    CHECK(conn > 0, "emfile connect");
    CHECK(finish_retry(conn) == 1, "emfile finish");
    if (fake && sysfake_push != NULL) {
        CHECK(sysfake_push(SYS_CALL_ACCEPT, -EMFILE) == 1, "emfile script");
        r = sys_net_accept(listener);
        CHECK(r == -EMFILE, "fake accept EMFILE");
        accepted = accept_retry(listener);
        CHECK(accepted > 0, "fake accept after EMFILE");
    } else {
        CHECK(getrlimit(RLIMIT_NOFILE, &oldrl) == 0, "getrlimit");
        for (i = 0; i < 4096; i++) {
            if (fcntl(i, F_GETFD) >= 0) high = i;
        }
        newrl = oldrl;
        newrl.rlim_cur = (rlim_t)(high + 1);
        if (newrl.rlim_cur > oldrl.rlim_max) newrl.rlim_cur = oldrl.rlim_max;
        CHECK(setrlimit(RLIMIT_NOFILE, &newrl) == 0, "setrlimit low");
        r = sys_net_accept(listener);
        CHECK(r == -EMFILE, "real accept EMFILE");
        CHECK(setrlimit(RLIMIT_NOFILE, &oldrl) == 0, "setrlimit restore");
        accepted = accept_retry(listener);
        CHECK(accepted > 0, "real accept after restore");
    }
    CHECK(sys_res_close(accepted, SYS_KIND_CONNECTION) == 1, "emfile accepted close");
    CHECK(sys_res_close(conn, SYS_KIND_CONNECTION) == 1, "emfile conn close");
    CHECK(sys_res_close(listener, SYS_KIND_LISTENER) == 1, "emfile listener close");
    sys_res_close(addr, SYS_KIND_BYTE_BUFFER);
    sys_res_close(local, SYS_KIND_BYTE_BUFFER);
    sys_res_close(target, SYS_KIND_BYTE_BUFFER);
    return 0;
}

int main(int argc, char **argv) {
    int fake = argc > 1 && strcmp(argv[1], "fake") == 0;
    char realdir[] = "/tmp/opencode/p2-c-XXXXXX";
    char work[512];
    unsigned char ip4[4] = {127, 0, 0, 1};
    unsigned char ip6[16] = {0};
    long r;
    long cnt;
    r = sys_adapter_init(4096, 67108864);
    CHECK(r == 0, "adapter init");
    if (fake && sysfake_reset != NULL) CHECK(sysfake_reset() == 1, "fake reset");
    if (!fake) {
        CHECK(mkdtemp(realdir) != NULL, "mkdtemp");
        snprintf(work, sizeof(work), "%s/work", realdir);
        CHECK(mkdir(work, 0700) == 0, "mkdir work");
    } else {
        snprintf(work, sizeof(work), "/p2-c-work");
    }
    CHECK(tcp_roundtrip(4, ip4, 4) == 0, "tcp ipv4");

    ip6[15] = 1;
    CHECK(tcp_roundtrip(6, ip6, 16) == 0, "tcp ipv6");

    {
        char path[600];
        snprintf(path, sizeof(path), "%s/unix-a", work);
        CHECK(unix_stream(path) == 0, "unix stream");

        snprintf(path, sizeof(path), "%s/unix-b", work);
        {
            char path2[600];
            snprintf(path2, sizeof(path2), "%s/unix-c", work);
            CHECK(datagram_checks(path, path2) == 0, "unix datagram");

        }
    }
    CHECK(reactor_checks(ip4) == 0, "reactor");

    CHECK(resolver_checks() == 0, "resolver");

    CHECK(descriptor_pressure(fake, ip4) == 0, "descriptor pressure");

    cnt = sys_res_live_count();
    CHECK(cnt == 0, "live resource count");
    r = sys_adapter_shutdown();
    CHECK(r == 0, "adapter shutdown leftover");
    printf("p2_c: %s ok\n", fake ? "fake" : "real");
    return 0;
}
