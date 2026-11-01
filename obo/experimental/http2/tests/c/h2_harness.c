/* H2a C shim harness.
 *
 * Drives the shim with a real nghttp2 client session: the client generates
 * preface/SETTINGS/HEADERS/DATA/RST wire bytes, the server shim consumes them,
 * and server output is fed back into the client session and checked. No socket,
 * TLS, reactor or obo runtime is involved.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "h2_adapter.h"

#include <nghttp2/nghttp2.h>

#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#include "sysadapter.h"
#include "sysregistry.h"

#define MAX_CAP (2u * 1024u * 1024u)
#define OPT_FIELDS 44
#define OPT_BYTES (OPT_FIELDS * 8)

static nghttp2_session *g_client;
static unsigned char client_out[MAX_CAP];
static size_t client_out_len;
static unsigned char server_out[MAX_CAP];
static size_t server_out_len;
static unsigned char rx_body[MAX_CAP];
static size_t rx_body_len;
static char rx_headers[64][256];
static char rx_values[64][4096];
static size_t rx_header_len;
static int client_closed;
static long input_token;
static long wire_token;
static long g_max_events = 256;

static int wire_has_type(uint8_t type);

static void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}

static void check(int cond, const char *what) {
    if (!cond) die("FAIL: %s", what);
}

static long buf_new(size_t n) {
    long t = sys_reg_new_buffer((long)n);
    if (t < 0) die("sys_reg_new_buffer failed: %ld", t);
    return t;
}

static void buf_put(long token, const void *src, size_t n) {
    unsigned char *p = NULL;
    size_t cap = 0;
    if (sys_reg_buffer_data(token, &p, &cap) != 0 || n > cap)
        die("buffer put out of range");
    memcpy(p, src, n);
}

/* ------------------------------------------------------------------ */
/* nghttp2 client                                                      */
/* ------------------------------------------------------------------ */

static ssize_t client_send_cb(nghttp2_session *s, const uint8_t *data,
                              size_t length, int flags, void *ud) {
    (void)s;
    (void)flags;
    (void)ud;
    if (client_out_len + length > MAX_CAP) die("client output overflow");
    memcpy(client_out + client_out_len, data, length);
    client_out_len += length;
    return (ssize_t)length;
}

static void copy_text(char *dst, size_t cap, const uint8_t *src, size_t n) {
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

static int client_header_cb(nghttp2_session *s, const nghttp2_frame *frame,
                            const uint8_t *name, size_t namelen,
                            const uint8_t *value, size_t valuelen,
                            uint8_t flags, void *ud) {
    (void)s;
    (void)frame;
    (void)flags;
    (void)ud;
    if (rx_header_len < 64) {
        copy_text(rx_headers[rx_header_len], sizeof(rx_headers[0]), name, namelen);
        copy_text(rx_values[rx_header_len], sizeof(rx_values[0]), value, valuelen);
        rx_header_len += 1;
    }
    return 0;
}

static int client_data_cb(nghttp2_session *s, uint8_t flags, int32_t id,
                          const uint8_t *data, size_t len, void *ud) {
    (void)s;
    (void)flags;
    (void)id;
    (void)ud;
    if (rx_body_len + len > MAX_CAP) die("client body overflow");
    memcpy(rx_body + rx_body_len, data, len);
    rx_body_len += len;
    return 0;
}

static int client_close_cb(nghttp2_session *s, int32_t id, uint32_t code,
                           void *ud) {
    (void)s;
    (void)id;
    (void)code;
    (void)ud;
    client_closed = 1;
    return 0;
}

typedef struct {
    const unsigned char *data;
    size_t len;
    size_t pos;
    int eof_once;
} read_source;

static ssize_t source_read_cb(nghttp2_session *s, int32_t id, uint8_t *buf,
                              size_t length, uint32_t *flags,
                              nghttp2_data_source *source, void *ud) {
    read_source *src = (read_source *)source->ptr;
    size_t n;
    (void)s;
    (void)id;
    (void)ud;
    n = src->len - src->pos;
    if (n > length) n = length;
    if (n > 0) {
        memcpy(buf, src->data + src->pos, n);
        src->pos += n;
    }
    if (src->pos >= src->len) {
        *flags |= NGHTTP2_DATA_FLAG_EOF;
        src->eof_once = 1;
    }
    return (ssize_t)n;
}

static nghttp2_session *client_new(void) {
    nghttp2_session_callbacks *cb = NULL;
    nghttp2_session *s = NULL;
    client_out_len = 0;
    server_out_len = 0;
    if (nghttp2_session_callbacks_new(&cb) != 0) die("client callbacks");
    nghttp2_session_callbacks_set_send_callback(cb, client_send_cb);
    nghttp2_session_callbacks_set_on_header_callback(cb, client_header_cb);
    nghttp2_session_callbacks_set_on_data_chunk_recv_callback(cb, client_data_cb);
    nghttp2_session_callbacks_set_on_stream_close_callback(cb, client_close_cb);
    if (nghttp2_session_client_new(&s, cb, NULL) != 0) die("client session");
    nghttp2_session_callbacks_del(cb);
    if (nghttp2_submit_settings(s, NGHTTP2_FLAG_NONE, NULL, 0) != 0)
        die("client settings");
    return s;
}

static void client_pump(nghttp2_session *s) {
    int guard = 0;
    int idle = 0;
    while (guard < 64) {
        size_t before = client_out_len;
        guard += 1;
        if (nghttp2_session_send(s) != 0) die("client send failed");
        if (client_out_len == before) {
            idle += 1;
            if (idle >= 2) break;
        } else {
            idle = 0;
        }
        if (!nghttp2_session_want_write(s)) break;
    }
}

/* ------------------------------------------------------------------ */
/* Server shim                                                         */
/* ------------------------------------------------------------------ */

static long make_server(long now) {
    unsigned char opt[OPT_BYTES];
    server_out_len = 0;
    client_out_len = 0;
    int64_t *v = (int64_t *)opt;
    long token;
    memset(opt, 0, sizeof(opt));
    v[0] = 0x48324131LL;
    v[1] = H2_MODE_PRIOR_KNOWLEDGE;
    v[2] = 32;
    v[3] = 1000;
    v[4] = 16384;
    v[5] = 100;
    v[6] = 8192;
    v[7] = 81920;
    v[8] = 4;
    v[9] = 1572864;
    v[10] = 32768;
    v[11] = 32768;
    v[12] = 1048576;
    v[13] = 32768;
    v[14] = 65536;
    v[15] = g_max_events;
    v[16] = 8388608;
    v[17] = 4096;
    v[18] = 32;
    v[19] = 16384;
    v[20] = 4096;
    v[21] = 32;
    v[22] = 16384;
    v[23] = 100;
    v[24] = 20;
    v[25] = 100;
    v[26] = 32;
    v[27] = 4000;
    v[28] = 2000;
    v[29] = 200;
    v[30] = 100;
    v[31] = 10000;
    v[32] = 10000;
    v[33] = 10000;
    v[34] = 30000;
    v[35] = 60000;
    v[36] = 300000;
    v[37] = 300000;
    v[38] = 60000;
    v[39] = 1000;
    v[40] = 30000;
    v[41] = 4009984;
    v[42] = 0;
    v[43] = 0;
    (void)now;
    token = buf_new(sizeof(opt));
    buf_put(token, opt, sizeof(opt));
    return h2_conn_new(token, (long)sizeof(opt));
}

static size_t server_send_all(long conn) {
    size_t before = server_out_len;
    for (;;) {
        long n = h2_conn_send(conn, 65536);
        long pending;
        long got;
        if (n < 0) die("server send failed: %ld", n);
        pending = h2_conn_wire_pending(conn);
        if (pending < 0) die("wire pending failed: %ld", pending);
        if (pending == 0) break;
        got = h2_conn_wire_read(conn, wire_token, pending);
        if (got < 0) die("wire read failed: %ld", got);
        if (got == 0) break;
        if (server_out_len + (size_t)got > MAX_CAP) die("server output overflow");
        {
            unsigned char *p = NULL;
            size_t cap = 0;
            if (sys_reg_buffer_data(wire_token, &p, &cap) != 0) die("wire data");
            memcpy(server_out + server_out_len, p, (size_t)got);
            server_out_len += (size_t)got;
        }
    }
    return server_out_len - before;
}

static long server_feed(long conn, const unsigned char *data, size_t len,
                        long now) {
    long n;
    buf_put(input_token, data, len);
    n = h2_conn_feed(conn, input_token, 0, (long)len, now);
    return n;
}

static void client_to_server(long conn, long now) {
    size_t n;
    client_out_len = 0;
    client_pump(g_client);
    n = client_out_len;
    check(server_feed(conn, client_out, n, now) == (long)n,
          "client bytes consumed");
    client_out_len = 0;
}

static void server_to_client(long conn, long now) {
    size_t n = server_send_all(conn);
    if (n > 0) {
        ssize_t r = nghttp2_session_mem_recv(g_client, server_out, n);
        if (r < 0) die("client mem_recv failed: %zd", r);
        check((size_t)r == n, "client consumed server bytes");
        server_out_len = 0;
        client_out_len = 0;
        client_pump(g_client);
        if (client_out_len > 0) {
            check(server_feed(conn, client_out, client_out_len, now) ==
                  (long)client_out_len, "client ACK consumed");
            client_out_len = 0;
        }
    }
}

static long next_event(long conn, long now) {
    return h2_conn_next_event(conn, now);
}

static void expect_event(long conn, long now, long want, int32_t id) {
    long ev = next_event(conn, now);
    while (ev == H2_EV_SETTINGS_ACKED && want != H2_EV_SETTINGS_ACKED)
        ev = next_event(conn, now);
    if (ev != want) {
        die("expected event %ld, got %ld (stream %ld, err=%ld, aux=%ld)",
            want, ev, h2_conn_event_stream_id(conn),
            h2_conn_event_error(conn), h2_conn_event_aux(conn));
    }
    if (id > 0) {
        check(h2_conn_event_stream_id(conn) == id, "event stream id");
    }
}

static long adopt(long conn, long id) {
    long tok = h2_stream_adopt(conn, id);
    if (tok < 0) die("stream adopt %ld failed: %ld", id, tok);
    check(h2_stream_mark_adopted(tok) == 1, "mark adopted");
    return tok;
}

static void release_stream(long conn, long tok) {
    (void)h2_stream_close(tok);
    (void)sys_res_close(tok, H2_KIND_STREAM);
    (void)server_send_all(conn);
}

static read_source g_source;

static int32_t submit_get(nghttp2_session *s, const char *path, const char *body,
                          size_t blen, int with_body) {
    static const char *names[] = {":method", ":scheme", ":path", ":authority"};
    static const char *values[] = {"POST", "http", "", "example.test"};
    nghttp2_nv nva[4];
    read_source *src = NULL;
    nghttp2_data_provider provider;
    size_t i;
    values[2] = path;
    for (i = 0; i < 4; i++) {
        nva[i].name = (uint8_t *)names[i];
        nva[i].namelen = strlen(names[i]);
        nva[i].value = (uint8_t *)values[i];
        nva[i].valuelen = strlen(values[i]);
        nva[i].flags = NGHTTP2_NV_FLAG_NONE;
    }
    if (with_body) {
        src = &g_source;
        memset(src, 0, sizeof(*src));
        src->data = (const unsigned char *)body;
        src->len = blen;
        provider.source.ptr = src;
        provider.read_callback = source_read_cb;
        int32_t rid = nghttp2_submit_request(s, NULL, nva, 4, &provider, src);
        if (rid <= 0) die("submit_request failed");
        return rid;
    }
    {
        int32_t rid = nghttp2_submit_request(s, NULL, nva, 4, NULL, NULL);
        if (rid <= 0) die("submit_request failed");
        return rid;
    }
}

static int find_header(const char *name, char *out, size_t cap) {
    size_t i;
    for (i = 0; i < rx_header_len; i++) {
        if (strcmp(rx_headers[i], name) == 0) {
            snprintf(out, cap, "%s", rx_values[i]);
            return 1;
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Tests                                                               */
/* ------------------------------------------------------------------ */

static nghttp2_session *g_client;

static void setup(void) {
    long rc = sys_adapter_init(65536, 2147483647L);
    if (rc < 0) die("sys_adapter_init failed: %ld", rc);
    input_token = buf_new(MAX_CAP);
    wire_token = buf_new(MAX_CAP);
    check(h2_init() == 1, "h2_init compatibility");
    check(h2_version_num() >= 0x013b00, "nghttp2 version");
}

static void test_version(void) {
    check(h2_init() == 1, "version probe");
    check(h2_version_num() == 0x013b00, "pinned 1.59.0");
    printf("ok version\n");
}

static void test_bad_preface(void) {
    long c = make_server(0);
    static const unsigned char wrong[24] = "PRI * HTTP/1.1\r\n\r\nXX\r\n\r\n";
    long r = server_feed(c, wrong, 24, 0);
    check(r < 0, "bad preface is rejected");
    check((0 - r) == H2_FAIL(H2_FAIL_BAD_PREFACE, 0), "bad preface code");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    printf("ok preface\n");
}

static void test_partial_preface(void) {
    long c = make_server(0);
    static const unsigned char magic[24] = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";
    check(server_feed(c, magic, 10, 0) == 10, "partial preface consumed");
    check(server_feed(c, magic + 10, 14, 0) == 14, "preface remainder");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    printf("ok partial-preface\n");
}

static void test_settings(void) {
    long c = make_server(0);
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    expect_event(c, 0, H2_EV_SETTINGS_ACKED, 0);
    check(h2_conn_settings_acked(c) == 1, "settings acked");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok settings\n");
}

static void test_request_response(void) {
    long c = make_server(0);
    long tok;
    long ev;
    char status[16];
    check(h2_conn_wire_pending(c) >= 0, "wire");
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);

    rx_header_len = 0;
    rx_body_len = 0;
    client_closed = 0;
    submit_get(g_client, "/hello", NULL, 0, 0);
    client_to_server(c, 0);
    expect_event(c, 0, H2_EV_STREAM_STARTED, 1);
    expect_event(c, 0, H2_EV_STREAM_END, 1);
    tok = adopt(c, 1);
    check(h2_stream_header_count(tok) == 4, "request header count");
    check(h2_stream_begin(tok, 200) == 1, "begin response");
    {
        const char *n = "content-type", *v = "text/plain";
        long p = buf_new(strlen(n) + strlen(v));
        buf_put(p, n, strlen(n));
        {
            unsigned char *ptr = NULL;
            size_t cap = 0;
            (void)sys_reg_buffer_data(p, &ptr, &cap);
            memcpy(ptr + strlen(n), v, strlen(v));
        }
        check(h2_stream_header(tok, p, 0, (long)strlen(n),
                               (long)strlen(n), (long)strlen(v)) == 1,
              "response header");
        (void)sys_res_close(p, SYS_KIND_BYTE_BUFFER);
    }
    check(h2_stream_set_length(tok, 6) == 1, "set length");
    {
        const char *body = "hello\n";
        long p = buf_new(6);
        buf_put(p, body, 6);
        check(h2_stream_write(tok, p, 0, 6) == 6, "write body");
        (void)sys_res_close(p, SYS_KIND_BYTE_BUFFER);
    }
    check(h2_stream_end(tok) == 1, "end response");
    server_to_client(c, 0);
    check(find_header(":status", status, sizeof(status)), "status header present");
    check(strcmp(status, "200") == 0, "status 200");
    check(rx_body_len == 6 && memcmp(rx_body, "hello\n", 6) == 0, "response body");
    ev = next_event(c, 0);
    check(ev == H2_EV_NONE || ev == H2_EV_STREAM_CLOSED, "no stray event");
    release_stream(c, tok);
    server_to_client(c, 0);
    check(client_closed == 1, "client stream closed");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok request-response\n");
}

static void test_body_and_discard(void) {
    long c = make_server(0);
    long tok;
    char body[4];
    long p;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    submit_get(g_client, "/echo", "abcdefghij", 10, 1);
    client_to_server(c, 0);
    expect_event(c, 0, H2_EV_STREAM_STARTED, 1);
    expect_event(c, 0, H2_EV_STREAM_DATA, 1);
    expect_event(c, 0, H2_EV_STREAM_END, 1);
    tok = adopt(c, 1);
    p = buf_new(4);
    check(h2_stream_read(tok, p, 4) == 4, "read four body bytes");
    {
        unsigned char *ptr = NULL;
        size_t cap = 0;
        (void)sys_reg_buffer_data(p, &ptr, &cap);
        memcpy(body, ptr, 4);
    }
    check(memcmp(body, "abcd", 4) == 0, "body prefix");
    check(h2_stream_discard(tok, 100, 0) == 6, "discard remaining body");
    check(h2_stream_read(tok, p, 4) == 0, "body exhausted");
    (void)sys_res_close(p, SYS_KIND_BYTE_BUFFER);
    release_stream(c, tok);
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok body-discard\n");
}

static void append_data_frame(int32_t id, const char *body, size_t len, int end) {
    size_t at = client_out_len;
    if (at + 9 + len > MAX_CAP) die("data frame overflow");
    client_out[at + 0] = (unsigned char)((len >> 16) & 0xff);
    client_out[at + 1] = (unsigned char)((len >> 8) & 0xff);
    client_out[at + 2] = (unsigned char)(len & 0xff);
    client_out[at + 3] = 0;
    client_out[at + 4] = (unsigned char)(end ? 1 : 0);
    client_out[at + 5] = (unsigned char)((id >> 24) & 0x7f);
    client_out[at + 6] = (unsigned char)((id >> 16) & 0xff);
    client_out[at + 7] = (unsigned char)((id >> 8) & 0xff);
    client_out[at + 8] = (unsigned char)(id & 0xff);
    memcpy(client_out + at + 9, body, len);
    client_out_len = at + 9 + len;
}

static void test_reset_restores_window(void) {
    long c = make_server(0);
    long tok;
    long after;
    int i;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    for (i = 0; i < 20; i++) {
        long ev;
        int32_t id;
        while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
        {
            int32_t rid = submit_get(g_client, "/reset", NULL, 0, 0);
            client_out_len = 0;
            client_pump(g_client);
            {
                size_t z = 0;
                while (z + 9 <= client_out_len) {
                    size_t fl = ((size_t)client_out[z]<<16) |
                                ((size_t)client_out[z+1]<<8) | client_out[z+2];
                    if (client_out[z+3] == 1)
                        client_out[z+4] = (unsigned char)(client_out[z+4] & ~1u);
                    z += 9 + fl;
                }
            }
            {
                static char big[16384];
                memset(big, 'z', sizeof(big));
                append_data_frame(rid, big, sizeof(big), 0);
                append_data_frame(rid, big, sizeof(big), 1);
            }
            {
                long fed = server_feed(c, client_out, client_out_len, 0);
                check(fed == (long)client_out_len, "headers+data consumed");
            }
            client_out_len = 0;
        }
        expect_event(c, 0, H2_EV_STREAM_STARTED, -1);
        ev = H2_EV_STREAM_STARTED;
        id = (int32_t)h2_conn_event_stream_id(c);
        tok = adopt(c, id);
        (void)next_event(c, 0); /* data */
        (void)h2_stream_reset(tok, NGHTTP2_CANCEL);
        (void)sys_res_close(tok, H2_KIND_STREAM);
        /* Drain remaining events for this stream. */
        while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
        server_to_client(c, 0);
    }
    {
        long st = buf_new(H2_STATS_BYTES);
        long n = h2_conn_stats(c, st);
        check(n == H2_STATS_BYTES, "stats packet");
        {
            unsigned char *ptr = NULL;
            size_t cap = 0;
            int64_t vals[H2_STATS_FIELDS];
            (void)sys_reg_buffer_data(st, &ptr, &cap);
            memcpy(vals, ptr, sizeof(vals));
            after = (long)vals[H2_ST_CONN_WINDOW];
            check(vals[H2_ST_WINDOW_RESTORED] >= 20 * 32768,
                  "window restored counter");
            check(after >= 524288,
                  "connection window not exhausted after reset storm");
        }
        (void)sys_res_close(st, SYS_KIND_BYTE_BUFFER);
    }
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok reset-window\n");
}

static size_t build_request(unsigned char *out, const char *path, int keep_open) {
    size_t n;
    size_t z;
    client_out_len = 0;
    (void)submit_get(g_client, path, NULL, 0, 0);
    client_pump(g_client);
    z = 0;
    while (z + 9 <= client_out_len) {
        size_t fl = ((size_t)client_out[z]<<16) |
                    ((size_t)client_out[z+1]<<8) | client_out[z+2];
        if (client_out[z+3] == 1) {
            if (keep_open) client_out[z+4] = (unsigned char)(client_out[z+4] & ~1u);
            else client_out[z+4] = (unsigned char)(client_out[z+4] | 1u);
        }
        z += 9 + fl;
    }
    n = client_out_len;
    memcpy(out, client_out, n);
    client_out_len = 0;
    return n;
}

static void append_frame(unsigned char *buf, size_t *len, uint8_t type,
                         uint8_t flags, int32_t id, const char *payload,
                         size_t plen) {
    size_t at = *len;
    if (at + 9 + plen > MAX_CAP) die("frame append overflow");
    buf[at + 0] = (unsigned char)((plen >> 16) & 0xff);
    buf[at + 1] = (unsigned char)((plen >> 8) & 0xff);
    buf[at + 2] = (unsigned char)(plen & 0xff);
    buf[at + 3] = type;
    buf[at + 4] = flags;
    buf[at + 5] = (unsigned char)((id >> 24) & 0x7f);
    buf[at + 6] = (unsigned char)((id >> 16) & 0xff);
    buf[at + 7] = (unsigned char)((id >> 8) & 0xff);
    buf[at + 8] = (unsigned char)(id & 0xff);
    if (plen > 0) memcpy(buf + at + 9, payload, plen);
    *len = at + 9 + plen;
}

static void test_max_concurrent(void) {
    long c = make_server(0);
    static unsigned char req[MAX_CAP];
    static const unsigned char block[] = {
        0x82, 0x86, 0x84,             /* :method GET, :scheme http, :path / */
        0x41, 0x0e, 'e','x','a','m','p','l','e','.','t','e','s','t','.','1'
    };
    size_t blocklen = sizeof(block);
    size_t len = 0;
    int i;
    long ev;
    int started = 0;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    for (i = 0; i < 33; i++)
        append_frame(req, &len, 1, 4, 2 * i + 1, (const char *)block, blocklen);
    check(server_feed(c, req, len, 0) == (long)len, "33 headers consumed");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_STREAM_STARTED) started += 1;
    }
    check(started == 32, "only maxConcurrentStreams started");
    (void)server_send_all(c);
    {
        long st = buf_new(H2_STATS_BYTES);
        unsigned char *ptr = NULL;
        size_t cap = 0;
        int64_t vals[H2_STATS_FIELDS];
        (void)h2_conn_stats(c, st);
        (void)sys_reg_buffer_data(st, &ptr, &cap);
        memcpy(vals, ptr, sizeof(vals));
        check(vals[H2_ST_LIVE_STREAMS] == 32, "table stays at the cap");
        check(vals[H2_ST_STREAMS_OPENED] == 32, "no record beyond the cap");
        (void)sys_res_close(st, SYS_KIND_BYTE_BUFFER);
    }
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok max-concurrent\n");
}

static void test_release_with_unread_data(void) {
    long c = make_server(0);
    static unsigned char req[MAX_CAP];
    size_t len;
    long tok;
    long ev;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    len = build_request(req, "/release", 1);
    {
        static char big[4096];
        memset(big, 'q', sizeof(big));
        append_frame(req, &len, 0, 1, 1, big, sizeof(big));
    }
    check(server_feed(c, req, len, 0) == (long)len, "release request consumed");
    expect_event(c, 0, H2_EV_STREAM_STARTED, 1);
    tok = h2_stream_adopt(c, 1);
    check(tok > 0, "unadopted stream token");
    /* Simulate platform-released-first adoption failure: release must consume
     * buffered bytes, submit one REFUSED_STREAM and defer free. */
    check(sys_res_close(tok, H2_KIND_STREAM) == 1, "native release");
    (void)server_send_all(c);
    {
        long st = buf_new(H2_STATS_BYTES);
        unsigned char *ptr = NULL;
        size_t cap = 0;
        int64_t vals[H2_STATS_FIELDS];
        (void)h2_conn_stats(c, st);
        (void)sys_reg_buffer_data(st, &ptr, &cap);
        memcpy(vals, ptr, sizeof(vals));
        check(vals[H2_ST_WINDOW_RESTORED] >= 4096, "release restored window");
        check(vals[H2_ST_RESETS_SENT] >= 1, "release submitted one RST");
        (void)sys_res_close(st, SYS_KIND_BYTE_BUFFER);
    }
    while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok release-unread\n");
}

static void test_continuation_flood(void) {
    long c = make_server(0);
    static unsigned char req[MAX_CAP];
    size_t len;
    int i;
    long r;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    len = build_request(req, "/continuation", 1);
    /* Clear END_HEADERS on the request HEADERS frame. */
    {
        size_t z = 0;
        while (z + 9 <= len) {
            size_t fl = ((size_t)req[z]<<16) | ((size_t)req[z+1]<<8) | req[z+2];
            if (req[z+3] == 1) req[z+4] = (unsigned char)(req[z+4] & ~4u);
            z += 9 + fl;
        }
    }
    for (i = 0; i < 8; i++)
        append_frame(req, &len, 9, 0, 1, NULL, 0);
    r = server_feed(c, req, len, 0);
    check(r < 0, "continuation flood rejected");
    check(h2_conn_state(c) == H2_STATE_FAILED, "connection failed");
    server_out_len = 0;
    (void)server_send_all(c);
    check(wire_has_type(NGHTTP2_GOAWAY), "failed connection flushed GOAWAY");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok continuation-flood\n");
}

static void test_zero_data_flood(void) {
    long c = make_server(0);
    static unsigned char req[MAX_CAP];
    size_t len;
    int i;
    long r;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    len = build_request(req, "/zero", 1);
    for (i = 0; i < 300; i++)
        append_frame(req, &len, 0, 0, 1, NULL, 0);
    r = server_feed(c, req, len, 0);
    check(r < 0, "zero-DATA flood rejected");
    check(h2_conn_state(c) == H2_STATE_FAILED, "zero-DATA connection failed");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok zero-data-flood\n");
}

static void test_multiplexed(void) {
    long c = make_server(0);
    long t1;
    long t3;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    rx_header_len = 0;
    rx_body_len = 0;
    submit_get(g_client, "/one", NULL, 0, 0);
    submit_get(g_client, "/two", NULL, 0, 0);
    client_to_server(c, 0);
    expect_event(c, 0, H2_EV_STREAM_STARTED, 1);
    expect_event(c, 0, H2_EV_STREAM_END, 1);
    expect_event(c, 0, H2_EV_STREAM_STARTED, 3);
    expect_event(c, 0, H2_EV_STREAM_END, 3);
    t1 = adopt(c, 1);
    t3 = adopt(c, 3);
    check(h2_stream_begin(t1, 200) == 1, "mux begin 1");
    check(h2_stream_set_length(t1, 6) == 1, "mux length 1");
    {
        long p = buf_new(6);
        buf_put(p, "hello\n", 6);
        check(h2_stream_write(t1, p, 0, 6) == 6, "mux write 1");
        (void)sys_res_close(p, SYS_KIND_BYTE_BUFFER);
    }
    check(h2_stream_end(t1) == 1, "mux end 1");
    check(h2_stream_begin(t3, 200) == 1, "mux begin 3");
    check(h2_stream_set_length(t3, 6) == 1, "mux length 3");
    {
        long p = buf_new(6);
        buf_put(p, "hello\n", 6);
        check(h2_stream_write(t3, p, 0, 6) == 6, "mux write 3");
        (void)sys_res_close(p, SYS_KIND_BYTE_BUFFER);
    }
    check(h2_stream_end(t3) == 1, "mux end 3");
    server_to_client(c, 0);
    check(rx_body_len == 12 && memcmp(rx_body, "hello\nhello\n", 12) == 0,
          "both multiplexed bodies");
    check(rx_header_len >= 4, "both responses carried headers");
    release_stream(c, t1);
    release_stream(c, t3);
    (void)server_send_all(c);
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok multiplexed\n");
}

static void test_header_cap(void) {
    long c = make_server(0);
    static unsigned char req[MAX_CAP];
    static unsigned char hb[22000];
    size_t len = 0;
    size_t hblen = 0;
    long ev;
    int started = 0;
    int k;
    int j;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    hb[hblen++] = 0x82; /* :method GET */
    hb[hblen++] = 0x86; /* :scheme http */
    hb[hblen++] = 0x84; /* :path / */
    /* Three 6000-byte fields exceed the 16 KiB list cap. Each field is a
     * literal header with a new name "x-pad"; the HPACK string length 6000
     * uses the 7-bit prefix 0x7f, 0xf1, 0x2d. */
    for (k = 0; k < 3; k++) {
        hb[hblen++] = 0x00; /* literal without indexing */
        hb[hblen++] = 5;
        hb[hblen++] = 'x'; hb[hblen++] = '-'; hb[hblen++] = 'p';
        hb[hblen++] = 'a'; hb[hblen++] = 'd';
        hb[hblen++] = 0x7f;
        hb[hblen++] = 0xf1;
        hb[hblen++] = 0x2d;
        for (j = 0; j < 6000; j++) hb[hblen++] = 'a';
    }
    {
        size_t first = hblen > 16384 ? 16384 : hblen;
        append_frame(req, &len, 1, 1, 1, (const char *)hb, first);
        if (hblen > first)
            append_frame(req, &len, 9, 4, 1, (const char *)hb + first, hblen - first);
    }
    check(server_feed(c, req, len, 0) == (long)len, "oversized header consumed");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_STREAM_STARTED) started += 1;
    }
    check(started == 0, "oversized header stream not delivered");
    (void)server_send_all(c);
    {
        long st = buf_new(H2_STATS_BYTES);
        unsigned char *ptr = NULL;
        size_t cap = 0;
        int64_t vals[H2_STATS_FIELDS];
        (void)h2_conn_stats(c, st);
        (void)sys_reg_buffer_data(st, &ptr, &cap);
        memcpy(vals, ptr, sizeof(vals));
        check(vals[H2_ST_RESETS_SENT] >= 1, "oversized header reset");
        (void)sys_res_close(st, SYS_KIND_BYTE_BUFFER);
    }
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok header-cap\n");
}

static void test_deadline(void) {
    long c = make_server(0);
    long ev;
    g_client = client_new();
    client_to_server(c, 0);
    while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
    ev = next_event(c, 11000000000LL);
    check(ev == H2_EV_PROTOCOL_ERROR, "settings ACK deadline fired");
    check(h2_conn_state(c) == H2_STATE_FAILED, "deadline failed the connection");
    server_out_len = 0;
    (void)server_send_all(c);
    check(wire_has_type(NGHTTP2_GOAWAY), "settings timeout flushed GOAWAY");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok deadline\n");
}

static int wire_has_type(uint8_t type) {
    size_t z = 0;
    while (z + 9 <= server_out_len) {
        size_t fl = ((size_t)server_out[z]<<16) |
                    ((size_t)server_out[z+1]<<8) | server_out[z+2];
        if (server_out[z+3] == type) return 1;
        z += 9 + fl;
    }
    return 0;
}

static void test_orphan_access(void) {
    long c = make_server(0);
    long tok;
    long out;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    submit_get(g_client, "/orphan", NULL, 0, 0);
    client_to_server(c, 0);
    expect_event(c, 0, H2_EV_STREAM_STARTED, 1);
    tok = adopt(c, 1);
    check(h2_stream_header_count(tok) > 0, "header count before orphan");
    check(sys_res_close(c, H2_KIND_CONNECTION) == 1, "connection closed");
    check(h2_stream_header_count(tok) == -H2_FAIL(H2_FAIL_CLOSED, 0),
          "orphan header count is Closed");
    out = buf_new(64);
    check(h2_stream_header_at(tok, 0, out, 0) == -H2_FAIL(H2_FAIL_CLOSED, 0),
          "orphan header at is Closed");
    check(h2_stream_trailer_count(tok) == -H2_FAIL(H2_FAIL_CLOSED, 0),
          "orphan trailer count is Closed");
    check(h2_stream_trailer_at(tok, 0, out, 0) == -H2_FAIL(H2_FAIL_CLOSED, 0),
          "orphan trailer at is Closed");
    check(sys_res_close(tok, H2_KIND_STREAM) == 1, "orphan shell released");
    (void)sys_res_close(out, SYS_KIND_BYTE_BUFFER);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok orphan\n");
}

static void test_idle(void) {
    long c = make_server(0);
    long ev;
    long d;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
    d = h2_conn_deadline(c);
    check(d > 0 && d <= 60000000000LL, "idle deadline armed");
    ev = next_event(c, 61000000000LL);
    check(ev == H2_EV_TIMEOUT, "idle timeout event");
    check(h2_conn_state(c) == H2_STATE_CLOSING, "idle timeout closes");
    server_out_len = 0;
    (void)server_send_all(c);
    check(wire_has_type(NGHTTP2_GOAWAY), "idle timeout queued GOAWAY");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok idle\n");
}

static void test_data_stream_zero(void) {
    long c = make_server(0);
    static unsigned char req[24 + 9 + 10];
    long in;
    long fed;
    long ev;
    int saw = 0;
    memset(req, 0, sizeof(req));
    memcpy(req, "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n", 24);
    req[27] = 4; /* SETTINGS, empty */
    req[35] = 1; /* DATA length 1 */
    req[36] = 0; /* DATA */
    req[37] = 0;
    req[41] = 0; /* stream 0 */
    req[42] = 'x';
    in = buf_new(sizeof(req));
    buf_put(in, req, sizeof(req));
    fed = h2_conn_feed(c, in, 0, (long)sizeof(req), 0);
    check(fed >= 0, "DATA on stream 0 is reported as a connection error");
    server_out_len = 0;
    (void)server_send_all(c);
    check(h2_conn_state(c) == H2_STATE_FAILED, "stream-0 DATA failed the connection");
    check(wire_has_type(NGHTTP2_GOAWAY), "stream-0 DATA queued GOAWAY");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_PROTOCOL_ERROR) saw = 1;
    }
    check(saw == 1, "stream-0 DATA surfaced one protocol error");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    printf("ok data-stream-zero\n");
}

static void test_closing_refuses_streams(void) {
    long c = make_server(0);
    static const unsigned char block[] = {
        0x82, 0x86, 0x84,
        0x41, 0x0e, 'e','x','a','m','p','l','e','.','t','e','s','t'
    };
    static unsigned char req[MAX_CAP];
    size_t len = 0;
    long ev;
    int started = 0;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    check(h2_conn_goaway(c, -1, NGHTTP2_NO_ERROR) == 1, "explicit GOAWAY");
    check(h2_conn_state(c) == H2_STATE_CLOSING, "closing state");
    (void)server_send_all(c);
    append_frame(req, &len, 1, 5, 1, (const char *)block, sizeof(block));
    check(server_feed(c, req, len, 0) == (long)len, "closing feed consumed");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_STREAM_STARTED) started += 1;
    }
    check(started == 0, "closing connection refused a new stream");
    {
        long st = buf_new(H2_STATS_BYTES);
        unsigned char *ptr = NULL;
        size_t cap = 0;
        int64_t vals[H2_STATS_FIELDS];
        (void)h2_conn_stats(c, st);
        (void)sys_reg_buffer_data(st, &ptr, &cap);
        memcpy(vals, ptr, sizeof(vals));
        check(vals[H2_ST_STREAMS_OPENED] == 0, "no stream record after closing");
        (void)sys_res_close(st, SYS_KIND_BYTE_BUFFER);
    }
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok closing-refuses\n");
}

static void test_window_update_zero(void) {
    long c = make_server(0);
    static unsigned char req[24 + 9 + 13];
    long in;
    long fed;
    long ev;
    int saw = 0;
    memset(req, 0, sizeof(req));
    memcpy(req, "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n", 24);
    req[27] = 4; /* SETTINGS, empty */
    req[35] = 4; /* WINDOW_UPDATE length */
    req[36] = 8;
    req[41] = 0; /* stream 0 */
    /* increment remains zero -> PROTOCOL_ERROR */
    in = buf_new(sizeof(req));
    buf_put(in, req, sizeof(req));
    fed = h2_conn_feed(c, in, 0, (long)sizeof(req), 0);
    check(fed >= 0, "zero WINDOW_UPDATE reported by nghttp2");
    server_out_len = 0;
    (void)server_send_all(c);
    check(h2_conn_state(c) == H2_STATE_FAILED, "zero WINDOW_UPDATE failed the connection");
    check(wire_has_type(NGHTTP2_GOAWAY), "zero WINDOW_UPDATE queued GOAWAY");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_PROTOCOL_ERROR) saw = 1;
    }
    check(saw == 1, "zero WINDOW_UPDATE surfaced one protocol error");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    printf("ok window-update-zero\n");
}

static void test_event_overflow(void) {
    long c;
    long ev;
    int saw = 0;
    g_max_events = 1;
    c = make_server(0);
    g_max_events = 256;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    submit_get(g_client, "/overflow", NULL, 0, 0);
    client_to_server(c, 0);
    check(h2_conn_state(c) == H2_STATE_FAILED, "event overflow failed the connection");
    server_out_len = 0;
    (void)server_send_all(c);
    check(wire_has_type(NGHTTP2_GOAWAY), "event overflow queued GOAWAY");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_PROTOCOL_ERROR) saw = 1;
    }
    check(saw == 1, "event overflow surfaced one protocol error");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok event-overflow\n");
}

static void test_clean_terminate(void) {
    long c = make_server(0);
    long ev;
    int protocol = 0;
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    while ((ev = next_event(c, 0)) != H2_EV_NONE) { (void)ev; }
    check(h2_conn_terminate(c, -1, NGHTTP2_NO_ERROR) == 1, "clean terminate");
    check(h2_conn_state(c) == H2_STATE_CLOSING, "clean terminate is closing");
    server_out_len = 0;
    (void)server_send_all(c);
    check(wire_has_type(NGHTTP2_GOAWAY), "clean terminate queued GOAWAY");
    while ((ev = next_event(c, 0)) != H2_EV_NONE) {
        if (ev == H2_EV_PROTOCOL_ERROR) protocol = 1;
    }
    check(protocol == 0, "clean terminate is not a protocol error");
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok clean-terminate\n");
}

static void test_goaway(void) {
    long c = make_server(0);
    g_client = client_new();
    client_to_server(c, 0);
    server_to_client(c, 0);
    check(h2_conn_drain(c) == 1, "drain submitted");
    server_to_client(c, 0);
    check(h2_conn_drain_poll(c, 1000000) == 1, "barrier ping");
    server_to_client(c, 0);
    check(h2_conn_drain_poll(c, 2000000000LL) == 1, "final goaway");
    server_to_client(c, 0);
    check(h2_conn_drain_complete(c) == 1, "drain complete");
    expect_event(c, 2000000000LL, H2_EV_DRAIN_COMPLETE, 0);
    (void)sys_res_close(c, H2_KIND_CONNECTION);
    nghttp2_session_del(g_client);
    g_client = NULL;
    printf("ok goaway\n");
}

static int verify_file(const char *path) {
    FILE *f = fopen(path, "rb");
    static unsigned char data[MAX_CAP];
    size_t n;
    ssize_t r;
    char status[16];
    if (f == NULL) die("open %s", path);
    n = fread(data, 1, sizeof(data), f);
    fclose(f);
    g_client = client_new();
    client_out_len = 0;
    client_pump(g_client);
    client_out_len = 0;
    (void)submit_get(g_client, "/hello", NULL, 0, 0);
    client_pump(g_client);
    client_out_len = 0;
    rx_header_len = 0;
    rx_body_len = 0;
    r = nghttp2_session_mem_recv(g_client, data, n);
    if (r < 0) die("verify mem_recv: %zd", r);
    check((size_t)r == n, "verify consumed all bytes");
    check(find_header(":status", status, sizeof(status)), "verify status header");
    check(strcmp(status, "200") == 0, "verify status 200");
    printf("status=%s body=%zu\n", status, rx_body_len);
    {
        char trailer[64];
        if (find_header("x-test", trailer, sizeof(trailer)))
            printf("trailer=%s\n", trailer);
    }
    fwrite(rx_body, 1, rx_body_len, stdout);
    printf("\n");
    nghttp2_session_del(g_client);
    g_client = NULL;
    return 0;
}

int main(int argc, char **argv) {
    setup();
    if (argc == 3 && strcmp(argv[1], "verify") == 0) {
        return verify_file(argv[2]);
    }
    test_version();
    test_bad_preface();
    test_partial_preface();
    test_settings();
    test_request_response();
    test_body_and_discard();
    test_reset_restores_window();
    test_max_concurrent();
    test_release_with_unread_data();
    test_continuation_flood();
    test_zero_data_flood();
    test_multiplexed();
    test_header_cap();
    test_deadline();
    test_orphan_access();
    test_idle();
    test_data_stream_zero();
    test_closing_refuses_streams();
    test_window_update_zero();
    test_event_overflow();
    test_clean_terminate();
    test_goaway();
    printf("ok all\n");
    return 0;
}
