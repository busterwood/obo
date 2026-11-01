/* P2q C harness for UDP batch, GSO/GRO and datagram options.
 *
 * The same source builds against the Linux adapter and the link-time fake.
 * Trace-independent assertions only: the kernel may or may not coalesce a
 * UDP_GRO receive, so both a single coalesced message and individual
 * datagrams are accepted when the byte accounting is identical.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CHECK(cond, what) do { \
    if (!(cond)) { fprintf(stderr, "p2q_c: %s failed: %s\n", what, #cond); return 1; } \
} while (0)

static long mkbuf(const unsigned char *p, long n) {
    long t = sys_res_new(SYS_KIND_BYTE_BUFFER, n, 0);
    unsigned char *q = NULL;
    size_t l = 0;
    if (t < 0) return t;
    if (sys_reg_buffer_data(t, &q, &l) < 0 || l < (size_t)n) return -EIO;
    if (n > 0) memcpy(q, p, (size_t)n);
    return t;
}

static long zero_buf(long n) {
    unsigned char *p = (unsigned char *)calloc(1, (size_t)(n > 0 ? n : 1));
    long t;
    if (p == NULL) return -ENOMEM;
    t = mkbuf(p, n);
    free(p);
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

static long mkipv4(const unsigned char ip[4], long port) {
    unsigned char p[160];
    memset(p, 0, sizeof(p));
    put64(p, 0, SYS_ADDR_FAMILY_IPV4);
    put64(p, 1, port);
    put64(p, 3, 4);
    memcpy(p + 32, ip, 4);
    return mkbuf(p, 160);
}

static long local_port(long dgram) {
    long out = zero_buf(160);
    long port;
    if (out < 0) return out;
    if (sys_net_address(dgram, SYS_KIND_DATAGRAM, 0, out) != 1) {
        sys_res_close(out, SYS_KIND_BYTE_BUFFER);
        return -1;
    }
    port = field(out, 1);
    sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    return port;
}

static int batch_account(long meta, long n, long want_total, long want_seg) {
    long i;
    long total = 0;
    long segments = 0;
    for (i = 0; i < n; i++) {
        long base = i * SYS_DGRAM_BATCH_ENTRY;
        long len = field(meta, base / 8 + 1);
        long original = field(meta, base / 8 + 2);
        long trunc = field(meta, base / 8 + 3);
        long seg = field(meta, base / 8 + 4);
        if (len < 0 || original != len || trunc != 0) return 0;
        if (seg != 0) {
            if (want_seg >= 0 && seg != want_seg) return 0;
            segments += (len + seg - 1) / seg;
        } else {
            segments += 1;
        }
        total += len;
    }
    return total == want_total && segments == 3;
}

static long receive_retry(long receiver, long data, long max, long peer,
                          long meta) {
    long r = -EAGAIN;
    int i;
    usleep(20000);
    for (i = 0; i < 500; i++) {
        r = sys_net_receive(receiver, data, max, peer, meta);
        if (r != -EAGAIN && r != -EINTR) return r;
        usleep(20000);
    }
    return r;
}

static long receive_batch_retry(long receiver, long data, long meta, long peer,
                                long max_packets, long max_bytes) {
    long r = -EAGAIN;
    int i;
    usleep(20000);
    for (i = 0; i < 500; i++) {
        r = sys_net_receive_batch(receiver, data, meta, peer, max_packets,
                                  max_bytes);
        if (r != -EAGAIN && r != -EINTR) return r;
        usleep(20000);
    }
    return r;
}

static int run(void) {
    unsigned char loop[4] = {127, 0, 0, 1};
    unsigned char payload[300];
    long aa, ab, sender, receiver, data, meta, peer, buf, caps;
    long r, port;
    int i;
    int gso = 1;
    for (i = 0; i < 300; i++) payload[i] = (unsigned char)i;
    aa = mkipv4(loop, 0);
    CHECK(aa > 0, "addr a");
    ab = mkipv4(loop, 0);
    CHECK(ab > 0, "addr b");
    sender = sys_net_bind_datagram_options(
        aa, 65536, 65536,
        SYS_DGRAM_GRO | SYS_DGRAM_GSO | SYS_DGRAM_DONT_FRAGMENT);
    CHECK(sender > 0, "bind sender options");
    receiver = sys_net_bind_datagram_options(ab, 65536, 65536,
                                             SYS_DGRAM_GRO | SYS_DGRAM_GSO |
                                             SYS_DGRAM_DONT_FRAGMENT);
    CHECK(receiver > 0, "bind receiver options");
    caps = sys_net_datagram_caps(sender);
    CHECK(caps >= 0 && (caps & ~(SYS_DGRAM_CAP_GSO | SYS_DGRAM_CAP_GRO)) == 0,
          "caps mask");
    port = local_port(receiver);
    CHECK(port > 0, "receiver port");
    sys_res_close(ab, SYS_KIND_BYTE_BUFFER);
    ab = mkipv4(loop, port);
    CHECK(ab > 0, "receiver target");

    buf = mkbuf(payload, 300);
    CHECK(buf > 0, "payload buffer");
    data = zero_buf(1024);
    meta = zero_buf(8 * SYS_DGRAM_BATCH_ENTRY);
    peer = zero_buf(8 * SYS_ADDR_PACKET_BYTES);
    CHECK(data > 0 && meta > 0 && peer > 0, "batch buffers");

    r = sys_net_send_batch(sender, ab, buf, 0, 300, 100);
    if (r == -EOPNOTSUPP) {
        gso = 0;
        CHECK(caps == 0 || (caps & SYS_DGRAM_CAP_GSO) != 0,
              "gso caps before fallback");
        CHECK(sys_net_send(sender, ab, buf, 0, 100) == 100, "fallback 1");
        CHECK(sys_net_send(sender, ab, buf, 100, 100) == 100, "fallback 2");
        CHECK(sys_net_send(sender, ab, buf, 200, 100) == 100, "fallback 3");
        caps = sys_net_datagram_caps(sender);
        CHECK(caps >= 0 && (caps & SYS_DGRAM_CAP_GSO) == 0,
              "gso disabled after fallback");
        CHECK(sys_net_send_batch(sender, ab, buf, 0, 300, 100) == -EOPNOTSUPP,
              "gso stays unsupported");
    } else {
        CHECK(r == 300, "gso send");
    }
    r = receive_batch_retry(receiver, data, meta, peer, 8, 1024);
    CHECK(r >= 1 && r <= 8, "batch receive count");
    CHECK(batch_account(meta, r, 300, gso ? 100 : -1),
          "batch accounting/segments");
    CHECK(field(peer, 0) == SYS_ADDR_FAMILY_IPV4, "batch peer");
    CHECK(field(peer, 1) > 0, "batch peer port");

    /* Total budget, not a per-packet bound. */
    r = sys_net_send_batch(sender, ab, buf, 0, 300, 100);
    if (r == -EOPNOTSUPP) {
        CHECK(sys_net_send(sender, ab, buf, 0, 100) == 100, "budget 1");
        CHECK(sys_net_send(sender, ab, buf, 100, 100) == 100, "budget 2");
        CHECK(sys_net_send(sender, ab, buf, 200, 100) == 100, "budget 3");
    } else {
        CHECK(r == 300, "budget gso send");
    }
    r = receive_batch_retry(receiver, data, meta, peer, 8, 300);
    CHECK(r >= 1 && batch_account(meta, r, 300, -1), "budget 300");

    /* A datagram larger than the remaining budget is Range, not silent
     * truncation. Send 150 then 100 with a 200-byte budget. */
    CHECK(sys_net_send(sender, ab, buf, 0, 150) == 150, "range send 150");
    CHECK(sys_net_send(sender, ab, buf, 150, 100) == 100, "range send 100");
    r = receive_batch_retry(receiver, data, meta, peer, 8, 200);
    CHECK(r == -ERANGE, "oversized remaining budget is Range");

    /* Single receive keeps truncation and originalSize. */
    CHECK(sys_net_send(sender, ab, buf, 0, 100) == 100, "single send");
    {
        long small = zero_buf(10);
        long one_meta = zero_buf(SYS_DGRAM_META_BYTES);
        long one_peer = zero_buf(SYS_ADDR_PACKET_BYTES);
        CHECK(small > 0 && one_meta > 0 && one_peer > 0, "single buffers");
        r = receive_retry(receiver, small, 10, one_peer, one_meta);
        CHECK(r == 10, "truncated receive length");
        CHECK(field(one_meta, 0) == 100 && field(one_meta, 1) == 1,
              "truncation metadata");
        CHECK(field(one_meta, 2) >= 0, "single segment metadata");
        sys_res_close(small, SYS_KIND_BYTE_BUFFER);
        sys_res_close(one_meta, SYS_KIND_BYTE_BUFFER);
        sys_res_close(one_peer, SYS_KIND_BYTE_BUFFER);
    }

    /* Argument validation. */
    CHECK(sys_net_receive_batch(receiver, data, meta, peer, 0, 1024) == -ERANGE,
          "max_packets 0");
    CHECK(sys_net_receive_batch(receiver, data, meta, peer, 65, 1024) == -ERANGE,
          "max_packets 65");
    CHECK(sys_net_receive_batch(receiver, data, meta, peer, 8, 0) == -ERANGE,
          "max_bytes 0");
    CHECK(sys_net_receive_batch(receiver, data, meta, peer, 8, 65508) == -ERANGE,
          "max_bytes 65508");
    CHECK(sys_net_send_batch(sender, ab, buf, 0, 300, 65508) == -ERANGE,
          "segment larger than datagram limit");
    CHECK(sys_net_send_batch(sender, ab, buf, 0, 65, 1) == -ERANGE,
          "too many segments");
    CHECK(sys_net_send_batch(sender, ab, buf, 0, 65508, 0) == -EMSGSIZE,
          "batch too large");

    /* The kernel datagram cap is 65507, including a GSO super-packet. */
    {
        unsigned char *bigp = (unsigned char *)calloc(1, 65507);
        long big;
        CHECK(bigp != NULL, "big batch allocation");
        big = mkbuf(bigp, 65507);
        free(bigp);
        CHECK(big > 0, "big batch buffer");
        r = sys_net_send_batch(sender, ab, big, 0, 65507, 1024);
        if (r != -EOPNOTSUPP) CHECK(r == 65507, "gso 65507 accepted");
        CHECK(sys_net_send_batch(sender, ab, big, 0, 65508, 1024) == -EMSGSIZE,
              "gso 65508 rejected");
        sys_res_close(big, SYS_KIND_BYTE_BUFFER);
    }

    /* GSO-off profile rejects a segmented send and caps say so. */
    {
        long off_addr = mkipv4(loop, 0);
        long off = sys_net_bind_datagram_options(off_addr, 65536, 65536,
                                                 SYS_DGRAM_GRO);
        CHECK(off > 0, "bind gso-off");
        caps = sys_net_datagram_caps(off);
        CHECK(caps >= 0 && (caps & SYS_DGRAM_CAP_GSO) == 0, "gso-off caps");
        CHECK(sys_net_send_batch(off, ab, buf, 0, 300, 100) == -EOPNOTSUPP,
              "gso-off segmented send");
        sys_res_close(off, SYS_KIND_DATAGRAM);
        sys_res_close(off_addr, SYS_KIND_BYTE_BUFFER);
    }

    /* GRO-off receiver gets individual datagrams even when the sender uses
     * UDP_SEGMENT. */
    {
        long plain_addr = mkipv4(loop, 0);
        long plain = sys_net_bind_datagram_options(plain_addr, 65536, 65536,
                                                   SYS_DGRAM_GSO);
        long pport;
        long ptarget;
        CHECK(plain > 0, "bind gro-off");
        pport = local_port(plain);
        CHECK(pport > 0, "gro-off port");
        ptarget = mkipv4(loop, pport);
        CHECK(ptarget > 0, "gro-off target");
        r = sys_net_send_batch(sender, ptarget, buf, 0, 300, 100);
        if (r == -EOPNOTSUPP) {
            CHECK(sys_net_send(sender, ptarget, buf, 0, 100) == 100,
                  "gro-off 1");
            CHECK(sys_net_send(sender, ptarget, buf, 100, 100) == 100,
                  "gro-off 2");
            CHECK(sys_net_send(sender, ptarget, buf, 200, 100) == 100,
                  "gro-off 3");
        } else {
            CHECK(r == 300, "gro-off gso send");
        }
        r = receive_batch_retry(plain, data, meta, peer, 8, 1024);
        CHECK(r == 3, "gro-off count");
        CHECK(batch_account(meta, r, 300, 0), "gro-off segments");
        sys_res_close(plain, SYS_KIND_DATAGRAM);
        sys_res_close(plain_addr, SYS_KIND_BYTE_BUFFER);
        sys_res_close(ptarget, SYS_KIND_BYTE_BUFFER);
    }

    /* Legacy sys_net_bind_datagram keeps one-datagram receive and permits a
     * 5000-byte send (no forced DF). */
    {
        long la = mkipv4(loop, 0);
        long lb = mkipv4(loop, 0);
        long legacy_sender;
        long legacy_receiver;
        long lport;
        long ltarget;
        long big;
        unsigned char *bigp;
        CHECK(la > 0 && lb > 0, "legacy addrs");
        legacy_sender = sys_net_bind_datagram(la);
        legacy_receiver = sys_net_bind_datagram(lb);
        CHECK(legacy_sender > 0 && legacy_receiver > 0, "legacy binds");
        lport = local_port(legacy_receiver);
        CHECK(lport > 0, "legacy port");
        ltarget = mkipv4(loop, lport);
        CHECK(ltarget > 0, "legacy target");
        CHECK(sys_net_send(legacy_sender, ltarget, buf, 0, 100) == 100,
              "legacy send 1");
        CHECK(sys_net_send(legacy_sender, ltarget, buf, 100, 100) == 100,
              "legacy send 2");
        CHECK(sys_net_send(legacy_sender, ltarget, buf, 200, 100) == 100,
              "legacy send 3");
        r = receive_retry(legacy_receiver, data, 1024, peer, meta);
        CHECK(r == 100, "legacy receive is one datagram");
        CHECK(field(meta, 0) == 100 && field(meta, 2) == 0,
              "legacy original/segment");
        bigp = (unsigned char *)calloc(1, 5000);
        CHECK(bigp != NULL, "legacy big alloc");
        big = mkbuf(bigp, 5000);
        free(bigp);
        CHECK(big > 0, "legacy big buffer");
        CHECK(sys_net_send(legacy_sender, ltarget, big, 0, 5000) == 5000,
              "legacy 5000 send");
        sys_res_close(big, SYS_KIND_BYTE_BUFFER);
        CHECK(sys_res_close(legacy_receiver, SYS_KIND_DATAGRAM) == 1,
              "legacy receiver close");
        CHECK(sys_res_close(legacy_sender, SYS_KIND_DATAGRAM) == 1,
              "legacy sender close");
        sys_res_close(la, SYS_KIND_BYTE_BUFFER);
        sys_res_close(lb, SYS_KIND_BYTE_BUFFER);
        sys_res_close(ltarget, SYS_KIND_BYTE_BUFFER);
    }

    /* SO_REUSEPORT is explicit; without it a second bind conflicts. */
    {
        long base = mkipv4(loop, 0);
        long first = sys_net_bind_datagram_options(base, 65536, 65536,
                                                   SYS_DGRAM_REUSE_PORT);
        long bport;
        long same;
        long second;
        CHECK(first > 0, "reuse first");
        bport = local_port(first);
        CHECK(bport > 0, "reuse port");
        same = mkipv4(loop, bport);
        CHECK(same > 0, "reuse target");
        second = sys_net_bind_datagram_options(same, 65536, 65536,
                                               SYS_DGRAM_REUSE_PORT);
        CHECK(second > 0, "reuse second");
        CHECK(sys_res_close(second, SYS_KIND_DATAGRAM) == 1, "reuse second close");
        second = sys_net_bind_datagram_options(same, 65536, 65536, 0);
        CHECK(second == -EADDRINUSE, "reuse conflict without flag");
        CHECK(sys_res_close(first, SYS_KIND_DATAGRAM) == 1, "reuse first close");
        sys_res_close(base, SYS_KIND_BYTE_BUFFER);
        sys_res_close(same, SYS_KIND_BYTE_BUFFER);
    }

    CHECK(sys_res_close(receiver, SYS_KIND_DATAGRAM) == 1, "receiver close");
    CHECK(sys_res_close(sender, SYS_KIND_DATAGRAM) == 1, "sender close");
    sys_res_close(aa, SYS_KIND_BYTE_BUFFER);
    sys_res_close(ab, SYS_KIND_BYTE_BUFFER);
    sys_res_close(buf, SYS_KIND_BYTE_BUFFER);
    sys_res_close(data, SYS_KIND_BYTE_BUFFER);
    sys_res_close(meta, SYS_KIND_BYTE_BUFFER);
    sys_res_close(peer, SYS_KIND_BYTE_BUFFER);
    return 0;
}

int main(void) {
    long r;
    if (sys_adapter_init(4096, 64L * 1024L * 1024L) < 0) {
        fprintf(stderr, "p2q_c: init failed\n");
        return 1;
    }
    r = run();
    if (r == 0) {
        if (sys_res_live_count() != 0) {
            fprintf(stderr, "p2q_c: live resources %ld\n", sys_res_live_count());
            r = 1;
        } else if (sys_adapter_shutdown() < 0) {
            fprintf(stderr, "p2q_c: shutdown failed\n");
            r = 1;
        }
    }
    if (r == 0) printf("p2q_c: ok\n");
    return (int)r;
}
