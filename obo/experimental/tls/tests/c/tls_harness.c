/* Experimental P2T-b C harness.
 *
 * Drives the trusted C adapter and a normal OpenSSL client over memory BIOs.
 * It exists to exercise fragmentation, WANT_READ/WANT_WRITE, close_notify,
 * truncation, client certificates, KeyUpdate and the leak census without
 * depending on obo-side scheduling. Not production code.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <openssl/err.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>

#include "sysregistry.h"
#include "tls_adapter.h"

static int failures;
static int g_capture_check;
static int g_capture_seen;
static long g_last_sr;
static long g_last_cr;

#define CHECK(cond, what) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", what); failures++; } \
} while (0)

static long read_file_buffer(const char *path, long *len_out) {
    FILE *f;
    long size;
    unsigned char *data;
    long token;
    size_t got;
    f = fopen(path, "rb");
    if (f == NULL) return -(long)errno;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return -EIO; }
    size = ftell(f);
    if (size < 0) { fclose(f); return -EIO; }
    rewind(f);
    data = (unsigned char *)malloc((size_t)size + 1);
    if (data == NULL) { fclose(f); return -ENOMEM; }
    got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if (got != (size_t)size) { free(data); return -EIO; }
    data[size] = 0;
    token = sys_reg_new_buffer(size);
    if (token > 0) {
        unsigned char *dst = NULL;
        size_t cap = 0;
        if (sys_reg_buffer_data(token, &dst, &cap) < 0 || cap < (size_t)size) {
            sys_res_close(token, 92);
            free(data);
            return -EIO;
        }
        memcpy(dst, data, (size_t)size);
    }
    free(data);
    if (len_out != NULL) *len_out = size;
    return token;
}

static void put_i64(unsigned char *p, long long v) {
    int i;
    unsigned long long u = (unsigned long long)v;
    for (i = 0; i < 8; i++) p[i] = (unsigned char)((u >> (8 * i)) & 0xffu);
}

static long make_options(long role, long minv, long maxv, long verify,
                         long require_sni, long depth, long max_sessions,
                         long max_handshake, long max_context) {
    long token = sys_reg_new_buffer(TLS_OPTION_BYTES);
    unsigned char *p = NULL;
    size_t len = 0;
    if (token <= 0) return token;
    if (sys_reg_buffer_data(token, &p, &len) < 0 || len < TLS_OPTION_BYTES) {
        sys_res_close(token, 92);
        return -EIO;
    }
    put_i64(p + TLS_OPTION_ROLE * 8, role);
    put_i64(p + TLS_OPTION_MIN_VERSION * 8, minv);
    put_i64(p + TLS_OPTION_MAX_VERSION * 8, maxv);
    put_i64(p + TLS_OPTION_VERIFY * 8, verify);
    put_i64(p + TLS_OPTION_REQUIRE_SNI * 8, require_sni);
    put_i64(p + TLS_OPTION_VERIFY_DEPTH * 8, depth);
    put_i64(p + TLS_OPTION_MAX_SESSIONS * 8, max_sessions);
    put_i64(p + TLS_OPTION_MAX_HANDSHAKE * 8, max_handshake);
    put_i64(p + TLS_OPTION_MAX_CONTEXT_BYTES * 8, max_context);
    return token;
}

static long set_alpn(long ctx, long policy, const char *const *protos, long count) {
    long total = 0;
    long i;
    long token;
    unsigned char *p = NULL;
    size_t len = 0;
    long at;
    for (i = 0; i < count; i++) total += (long)strlen(protos[i]);
    token = sys_reg_new_buffer(TLS_ALPN_HEADER_BYTES + count * 2 + total);
    if (token <= 0) return token;
    if (sys_reg_buffer_data(token, &p, &len) < 0) { sys_res_close(token, 92); return -EIO; }
    put_i64(p + TLS_ALPN_POLICY * 8, policy);
    put_i64(p + TLS_ALPN_COUNT * 8, count);
    at = TLS_ALPN_HEADER_BYTES;
    for (i = 0; i < count; i++) {
        size_t n = strlen(protos[i]);
        p[at++] = (unsigned char)((n >> 8) & 0xff);
        p[at++] = (unsigned char)(n & 0xff);
        memcpy(p + at, protos[i], n);
        at += (long)n;
    }
    at = tls_context_set_alpn(ctx, token);
    sys_res_close(token, 92);
    return at;
}

static long load_chain(long ctx, const char *path) {
    long token = read_file_buffer(path, NULL);
    long r;
    if (token <= 0) return token;
    r = tls_context_load_chain(ctx, token);
    sys_res_close(token, 92);
    return r;
}

static long load_key(long ctx, const char *path) {
    long token = read_file_buffer(path, NULL);
    long r;
    if (token <= 0) return token;
    r = tls_context_load_key(ctx, token);
    sys_res_close(token, 92);
    return r;
}

static long load_trust(long ctx, const char *path) {
    long token = read_file_buffer(path, NULL);
    long r;
    if (token <= 0) return token;
    r = tls_context_set_trust(ctx, token);
    sys_res_close(token, 92);
    return r;
}

struct peer {
    SSL_CTX *ctx;
    SSL *ssl;
    BIO *rbio;
    BIO *wbio;
};

static int peer_new(struct peer *p, const char *ca, const char *cert, const char *key,
                    int minver, int maxver, int verify, const char *alpn) {
    memset(p, 0, sizeof(*p));
    p->ctx = SSL_CTX_new(TLS_client_method());
    if (p->ctx == NULL) { fprintf(stderr, "peer_new: ctx\n"); return 0; }
    if (!SSL_CTX_set_min_proto_version(p->ctx, minver) ||
        !SSL_CTX_set_max_proto_version(p->ctx, maxver)) { fprintf(stderr, "peer_new: ver\n"); return 0; }
    if (ca != NULL && SSL_CTX_load_verify_locations(p->ctx, ca, NULL) != 1) { fprintf(stderr, "peer_new: ca %s\n", ca); return 0; }
    SSL_CTX_set_verify(p->ctx, verify ? SSL_VERIFY_PEER : SSL_VERIFY_NONE, NULL);
    if (cert != NULL && SSL_CTX_use_certificate_file(p->ctx, cert, SSL_FILETYPE_PEM) != 1) { fprintf(stderr, "peer_new: cert %s\n", cert); return 0; }
    if (key != NULL && SSL_CTX_use_PrivateKey_file(p->ctx, key, SSL_FILETYPE_PEM) != 1) { fprintf(stderr, "peer_new: key %s\n", key); return 0; }
    if (alpn != NULL) {
        size_t n = strlen(alpn);
        unsigned char wire[256];
        if (n > 250) return 0;
        wire[0] = (unsigned char)n;
        memcpy(wire + 1, alpn, n);
        if (SSL_CTX_set_alpn_protos(p->ctx, wire, (unsigned int)(n + 1)) != 0) return 0;
    }
    p->ssl = SSL_new(p->ctx);
    if (p->ssl == NULL) return 0;
    p->rbio = BIO_new(BIO_s_mem());
    p->wbio = BIO_new(BIO_s_mem());
    if (p->rbio == NULL || p->wbio == NULL) return 0;
    BIO_set_mem_eof_return(p->rbio, -1);
    SSL_set_bio(p->ssl, p->rbio, p->wbio);
    SSL_set_connect_state(p->ssl);
    return 1;
}

static void peer_free(struct peer *p) {
    if (p->ssl != NULL) SSL_free(p->ssl);
    if (p->ctx != NULL) SSL_CTX_free(p->ctx);
    memset(p, 0, sizeof(*p));
}

static int feed_client_output(long st, struct peer *p, size_t chunk) {
    unsigned char buf[65536];
    int n;
    while ((n = BIO_read(p->wbio, buf, (int)(chunk > sizeof(buf) ? sizeof(buf) : chunk))) > 0) {
        long token = sys_reg_new_buffer(n);
        unsigned char *dst = NULL;
        size_t cap = 0;
        long r;
        if (token <= 0) return 0;
        if (sys_reg_buffer_data(token, &dst, &cap) < 0 || cap < (size_t)n) {
            sys_res_close(token, 92);
            return 0;
        }
        memcpy(dst, buf, (size_t)n);
        r = tls_session_feed(st, token, 0, n);
        sys_res_close(token, 92);
        if (r != n) return 0;
    }
    return 1;
}

static int drain_server_output(long st, struct peer *p) {
    unsigned char buf[65536];
    long n;
    for (;;) {
        long token = sys_reg_new_buffer(sizeof(buf));
        unsigned char *dst = NULL;
        size_t cap = 0;
        if (token <= 0) return 0;
        if (sys_reg_buffer_data(token, &dst, &cap) < 0) { sys_res_close(token, 92); return 0; }
        n = tls_session_drain(st, token, (long)sizeof(buf));
        if (n < 0) { sys_res_close(token, 92); return 0; }
        if (n == 0) { sys_res_close(token, 92); break; }
        if (g_capture_check && n >= 4 && memmem(dst, (size_t)n, "pong", 4) != NULL) g_capture_seen = 1;
        if (BIO_write(p->rbio, dst, (int)n) != (int)n) { sys_res_close(token, 92); return 0; }
        if (tls_session_commit(st, n) != 1) { sys_res_close(token, 92); return 0; }
        sys_res_close(token, 92);
    }
    return 1;
}

static int client_handshake(struct peer *p) {
    int r = SSL_do_handshake(p->ssl);
    if (r == 1) return 1;
    {
        int e = SSL_get_error(p->ssl, r);
        if (e == SSL_ERROR_WANT_READ || e == SSL_ERROR_WANT_WRITE) return 0;
    }
    return -1;
}

static int handshake(long st, struct peer *p, size_t chunk, int *server_status_out) {
    int i;
    for (i = 0; i < 512; i++) {
        int sr;
        int cr;
        if (!feed_client_output(st, p, chunk)) return 0;
        if (!drain_server_output(st, p)) return 0;
        sr = (int)tls_session_handshake(st);
        g_last_sr = sr;
        if (server_status_out != NULL && i == 0) *server_status_out = sr;
        if (sr == TLS_STEP_WANT_READ && tls_session_output_pending(st) > 0) {
            fprintf(stderr, "FAIL: bare WantRead with pending output\n");
            failures++;
        }
        cr = client_handshake(p);
        g_last_cr = cr;
        if (sr < 0 || cr < 0) {
            fprintf(stderr, "handshake fail i=%d sr=%d cr=%d ssl=%d\n", i, sr, cr, cr < 0 ? SSL_get_error(p->ssl, cr) : 0);
            return 0;
        }
        if (sr == TLS_STEP_DONE && cr == 1) return 1;
    }
    return 0;
}

static long make_buffer(const void *data, size_t len) {
    long token = sys_reg_new_buffer((long)len);
    unsigned char *dst = NULL;
    size_t cap = 0;
    if (token <= 0) return token;
    if (sys_reg_buffer_data(token, &dst, &cap) < 0 || cap < len) {
        sys_res_close(token, 92);
        return -EIO;
    }
    memcpy(dst, data, len);
    return token;
}

static int adapter_write(long st, const void *data, size_t len) {
    long token = make_buffer(data, len);
    long r;
    if (token <= 0) return 0;
    r = tls_session_write(st, token, 0, (long)len);
    sys_res_close(token, 92);
    return (int)r;
}

static int adapter_read(long st, unsigned char *out, size_t max) {
    long token = sys_reg_new_buffer((long)max);
    unsigned char *dst = NULL;
    size_t cap = 0;
    long n;
    if (token <= 0) return -1;
    if (sys_reg_buffer_data(token, &dst, &cap) < 0) { sys_res_close(token, 92); return -1; }
    n = tls_session_read(st, token, (long)max);
    if (n > 0) memcpy(out, dst, (size_t)n);
    sys_res_close(token, 92);
    return (int)n;
}

static long open_context(const char *dir, long verify, long require_sni,
                         long minv, long maxv, const char *alpn_policy,
                         const char *const *protos, long count) {
    char path[4096];
    long opt;
    long ctx;
    long r;
    snprintf(path, sizeof(path), "%s/server.pem", dir);
    opt = make_options(0, minv, maxv, verify, require_sni, 8, 64, 1048576, 67108864);
    if (opt <= 0) return opt;
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    if (ctx <= 0) return ctx;
    r = load_chain(ctx, path);
    if (r != 1) { fprintf(stderr, "load_chain=%ld\n", r); tls_context_error_copy(ctx, 0, 0); sys_res_close(ctx, 23); return -1000 + r; }
    snprintf(path, sizeof(path), "%s/server.key", dir);
    r = load_key(ctx, path);
    if (r != 1) { fprintf(stderr, "load_key=%ld\n", r); sys_res_close(ctx, 23); return -1000 + r; }
    if (verify != 0) {
        snprintf(path, sizeof(path), "%s/ca.pem", dir);
        r = load_trust(ctx, path);
        if (r != 1) { fprintf(stderr, "load_trust=%ld\n", r); sys_res_close(ctx, 23); return -1000 + r; }
    }
    if (alpn_policy != NULL) {
        long policy = TLS_ALPN_PREFER;
        if (strcmp(alpn_policy, "disabled") == 0) policy = 0;
        if (strcmp(alpn_policy, "require") == 0) policy = 2;
        r = set_alpn(ctx, policy, protos, count);
        if (r != 1) { fprintf(stderr, "set_alpn=%ld\n", r); sys_res_close(ctx, 23); return -1000 + r; }
    }
    if (tls_context_live_sessions(ctx) != 0) return -1001;
    return ctx;
}

static int mode_tls(const char *dir, int minver, int maxver, const char *alpn,
                    const char *want_alpn) {
    long ctx = open_context(dir, 0, 0, minver == TLS1_2_VERSION ? 0 : 0,
                            maxver == TLS1_3_VERSION ? 1 : 0, "prefer",
                            (const char *const[]){ "h2", "http/1.1" }, 2);
    long st;
    struct peer p;
    int okh;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "session new");
    if (st <= 0) return 0;
    CHECK(tls_session_output_pending(st) == 0, "new session has no output");
    CHECK(peer_new(&p, NULL, NULL, NULL, minver, maxver, 0, alpn), "client new");
    okh = handshake(st, &p, 65536, NULL);
    CHECK(okh, "handshake completes");
    if (okh) {
        const unsigned char *sel = NULL;
        unsigned int sellen = 0;
        SSL_get0_alpn_selected(p.ssl, &sel, &sellen);
        if (want_alpn == NULL) {
            CHECK(sellen == 0, "no ALPN selected");
        } else {
            CHECK(sellen == strlen(want_alpn) && memcmp(sel, want_alpn, sellen) == 0,
                  "expected ALPN selected");
        }
        CHECK(tls_session_info_scalar(st, TLS_INFO_PEER_PRESENT) == 0, "no client certificate");
        CHECK(tls_session_info_scalar(st, TLS_INFO_VERIFICATION) == 0, "no verification requested");
        {
            SSL_SESSION *client_session = SSL_get1_session(p.ssl);
            CHECK(client_session != NULL, "client session object");
            if (client_session != NULL) {
                CHECK(SSL_SESSION_has_ticket(client_session) == 0, "session tickets are disabled");
                SSL_SESSION_free(client_session);
            }
        }
    }
    peer_free(&p);
    tls_session_fail(st);
    CHECK(sys_res_close(st, TLS_KIND_SESSION) == 1, "session close");
    CHECK(tls_context_live_sessions(ctx) == 0, "no live sessions after close");
    CHECK(sys_res_close(ctx, TLS_KIND_CONTEXT) == 1, "context close");
    return failures == 0;
}

static int mode_echo(const char *dir, int minver, int maxver) {
    long ctx = open_context(dir, 0, 0, minver == TLS1_2_VERSION ? 0 : 1,
                            maxver == TLS1_3_VERSION ? 1 : 0, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char got[16];
    int n;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "echo session");
    if (st <= 0) return 0;
    CHECK(peer_new(&p, NULL, NULL, NULL, minver, maxver, 0, "http/1.1"), "echo client");
    CHECK(handshake(st, &p, 65536, NULL), "echo handshake");
    CHECK(SSL_write(p.ssl, "ping", 4) == 4, "client write");
    CHECK(feed_client_output(st, &p, 65536), "feed client data");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n == 4 && memcmp(got, "ping", 4) == 0, "adapter read");
    g_capture_check = 1;
    g_capture_seen = 0;
    CHECK(adapter_write(st, "pong", 4) == 4, "adapter write");
    CHECK(drain_server_output(st, &p), "drain server data");
    g_capture_check = 0;
    CHECK(!g_capture_seen, "plaintext sentinel does not appear in ciphertext");
    n = SSL_read(p.ssl, got, sizeof(got));
    CHECK(n == 4 && memcmp(got, "pong", 4) == 0, "client read");
    /* Clean close from the client first. */
    CHECK(SSL_shutdown(p.ssl) >= 0, "client shutdown first");
    CHECK(feed_client_output(st, &p, 65536), "feed close_notify");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n == 0, "adapter EOF after close_notify");
    CHECK(tls_session_shutdown(st) >= 0, "server shutdown");
    CHECK(drain_server_output(st, &p), "drain close_notify");
    CHECK(SSL_shutdown(p.ssl) >= 0, "client consumes server close_notify");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return failures == 0;
}


static long failure_kind(long r) {
    long code = -r;
    return code / 16777216;
}


static int info_text(long st, long field, char *out, size_t cap) {
    long token = sys_reg_new_buffer((long)cap);
    unsigned char *dst = NULL;
    size_t len = 0;
    long n;
    if (token <= 0) return 0;
    if (sys_reg_buffer_data(token, &dst, &len) < 0) { sys_res_close(token, 92); return 0; }
    n = tls_session_info_copy(st, field, token, (long)cap);
    if (n > 0 && (size_t)n < cap) {
        memcpy(out, dst, (size_t)n);
        out[n] = 0;
    } else if (n == 0) {
        out[0] = 0;
    }
    sys_res_close(token, 92);
    return n >= 0;
}

static long new_raw_context(long verify, long require_sni, long minv, long maxv) {
    long opt = make_options(0, minv, maxv, verify, require_sni, 8, 16, 1048576, 67108864);
    long ctx;
    if (opt <= 0) return opt;
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    return ctx;
}

static int mode_alpn_require_missing(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "require",
                            (const char *const[]){ "h2", "http/1.1" }, 2);
    long st;
    struct peer p;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "require-missing session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, NULL), "require-missing client");
    CHECK(!handshake(st, &p, 65536, NULL), "no-ALPN client is rejected");
    CHECK(failure_kind(g_last_sr) == TLS_FAIL_ALPN_REQUIRED, "rejection is AlpnRequired");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_alpn_require_overlap(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "require",
                            (const char *const[]){ "h2" }, 1);
    long st;
    struct peer p;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "require-overlap session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "require-overlap client");
    CHECK(!handshake(st, &p, 65536, NULL), "no-overlap client is rejected");
    CHECK(failure_kind(g_last_sr) == TLS_FAIL_NO_APPLICATION_PROTOCOL,
          "rejection is NoApplicationProtocol");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_alpn_prefer_none(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "h2" }, 1);
    long st;
    struct peer p;
    const unsigned char *sel = NULL;
    unsigned int sellen = 0;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "prefer-none session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "prefer-none client");
    CHECK(handshake(st, &p, 65536, NULL), "prefer-no-overlap handshake succeeds");
    SSL_get0_alpn_selected(p.ssl, &sel, &sellen);
    CHECK(sellen == 0, "no ALPN selected on prefer-no-overlap");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_sni(const char *dir) {
    long ctx = open_context(dir, 0, 1, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    char text[256];
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "sni session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "sni client");
    CHECK(!handshake(st, &p, 65536, NULL), "missing SNI is rejected");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    st = tls_session_new(ctx);
    CHECK(st > 0, "sni session 2");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "sni client 2");
    CHECK(SSL_set_tlsext_host_name(p.ssl, "localhost") == 1, "set SNI");
    CHECK(handshake(st, &p, 65536, NULL), "SNI handshake succeeds");
    CHECK(info_text(st, TLS_INFO_SNI, text, sizeof(text)), "read SNI");
    CHECK(strcmp(text, "localhost") == 0, "SNI copied");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_clientcert(const char *dir) {
    char capath[4096];
    snprintf(capath, sizeof(capath), "%s/ca.pem", dir);
    long ctx = open_context(dir, 2, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    char text[512];
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "cert-required session");
    CHECK(peer_new(&p, capath, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 1, "http/1.1"), "cert-required client");
    CHECK(!handshake(st, &p, 65536, NULL), "missing client cert is rejected");
    CHECK(failure_kind(g_last_sr) == TLS_FAIL_NO_PEER_CERTIFICATE,
          "missing client cert is NoPeerCertificate");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);

    st = tls_session_new(ctx);
    CHECK(st > 0, "valid client cert session");
    {
        char certpath[4096];
        char keypath[4096];
        snprintf(certpath, sizeof(certpath), "%s/client.pem", dir);
        snprintf(keypath, sizeof(keypath), "%s/client.key", dir);
        CHECK(peer_new(&p, capath, certpath, keypath, TLS1_3_VERSION, TLS1_3_VERSION, 1, "http/1.1"), "valid client");
    }
    CHECK(handshake(st, &p, 65536, NULL), "valid client cert handshake");
    CHECK(tls_session_info_scalar(st, TLS_INFO_PEER_PRESENT) == 1, "peer present");
    CHECK(tls_session_info_scalar(st, TLS_INFO_VERIFICATION) == 1, "peer verified");
    CHECK(info_text(st, TLS_INFO_SUBJECT, text, sizeof(text)), "read subject");
    CHECK(strstr(text, "obo test client") != NULL, "subject copied");
    CHECK(info_text(st, TLS_INFO_ISSUER, text, sizeof(text)), "read issuer");
    CHECK(strstr(text, "obo TLS test CA") != NULL, "issuer copied");
    CHECK(tls_session_peer_count(st, TLS_PEER_DNS) == 0, "client has no DNS SAN");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);

    st = tls_session_new(ctx);
    CHECK(st > 0, "untrusted client session");
    {
        char certpath[4096];
        char keypath[4096];
        snprintf(certpath, sizeof(certpath), "%s/untrusted-client.pem", dir);
        snprintf(keypath, sizeof(keypath), "%s/untrusted-client.key", dir);
        CHECK(peer_new(&p, capath, certpath, keypath, TLS1_3_VERSION, TLS1_3_VERSION, 1, "http/1.1"), "untrusted client");
    }
    CHECK(!handshake(st, &p, 65536, NULL), "untrusted client cert is rejected");
    CHECK(failure_kind(g_last_sr) == TLS_FAIL_VERIFY_FAILED, "untrusted client is VerifyFailed");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);

    st = tls_session_new(ctx);
    CHECK(st > 0, "expired client session");
    {
        char certpath[4096];
        char keypath[4096];
        snprintf(certpath, sizeof(certpath), "%s/expired-client.pem", dir);
        snprintf(keypath, sizeof(keypath), "%s/client.key", dir);
        CHECK(peer_new(&p, capath, certpath, keypath, TLS1_3_VERSION, TLS1_3_VERSION, 1, "http/1.1"), "expired client");
    }
    CHECK(!handshake(st, &p, 65536, NULL), "expired client cert is rejected");
    CHECK(failure_kind(g_last_sr) == TLS_FAIL_VERIFY_FAILED, "expired client is VerifyFailed");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_truncate(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char got[8];
    int n;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "truncate session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "truncate client");
    CHECK(handshake(st, &p, 65536, NULL), "truncate handshake");
    CHECK(SSL_write(p.ssl, "abc", 3) == 3, "truncate write");
    CHECK(feed_client_output(st, &p, 65536), "truncate feed");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n == 3 && memcmp(got, "abc", 3) == 0, "truncate data received");
    peer_free(&p);
    CHECK(tls_session_eof(st) == 1, "mark transport EOF");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n < 0 && failure_kind(n) == TLS_FAIL_TRUNCATED, "EOF without close_notify is Truncated");
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_truncate_mid(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char wire[4096];
    unsigned char got[16];
    int n;
    int half;
    long token;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "mid-truncate session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "mid-truncate client");
    CHECK(handshake(st, &p, 65536, NULL), "mid-truncate handshake");
    CHECK(SSL_write(p.ssl, "partial", 7) == 7, "mid-truncate write");
    n = BIO_read(p.wbio, wire, (int)sizeof(wire));
    CHECK(n > 4, "mid-truncate ciphertext available");
    half = n / 2;
    token = make_buffer(wire, (size_t)half);
    CHECK(token > 0, "mid-truncate feed buffer");
    CHECK(tls_session_feed(st, token, 0, half) == half, "mid-truncate feed half");
    sys_res_close(token, 92);
    n = adapter_read(st, got, sizeof(got));
    CHECK(n == TLS_STEP_WANT_READ || n == TLS_STEP_WANT_WRITE, "partial record is not data");
    CHECK(tls_session_eof(st) == 1, "mid-truncate mark EOF");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n < 0 && failure_kind(n) == TLS_FAIL_TRUNCATED, "mid-record EOF is Truncated");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_keyupdate(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char got[64];
    int n;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "keyupdate session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "keyupdate client");
    CHECK(handshake(st, &p, 65536, NULL), "keyupdate handshake");
    CHECK(SSL_key_update(p.ssl, SSL_KEY_UPDATE_REQUESTED) == 1, "request KeyUpdate");
    CHECK(SSL_do_handshake(p.ssl) >= 0, "client keyupdate handshake");
    CHECK(SSL_write(p.ssl, "after", 5) == 5, "write after KeyUpdate");
    CHECK(feed_client_output(st, &p, 65536), "feed after KeyUpdate");
    n = adapter_read(st, got, sizeof(got));
    CHECK(n == 5 && memcmp(got, "after", 5) == 0, "data after KeyUpdate");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int adapter_write_all(long st, struct peer *p, const unsigned char *data, size_t len) {
    size_t at = 0;
    int guard = 0;
    while (at < len && guard < 1024) {
        int r = adapter_write(st, data + at, len - at);
        guard++;
        if (r > 0) { at += (size_t)r; continue; }
        if (r == TLS_STEP_WANT_WRITE) {
            if (!drain_server_output(st, p)) return 0;
            continue;
        }
        if (r == TLS_STEP_WANT_READ) {
            if (!drain_server_output(st, p)) return 0;
            continue;
        }
        return 0;
    }
    return at == len;
}

static int mode_large(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char *big;
    unsigned char *got;
    size_t total;
    int n;
    if (ctx <= 0) return 0;
    big = (unsigned char *)malloc(65536);
    got = (unsigned char *)malloc(65536);
    if (big == NULL || got == NULL) return 0;
    memset(big, 0x5a, 65536);
    st = tls_session_new(ctx);
    CHECK(st > 0, "large session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "large client");
    CHECK(handshake(st, &p, 65536, NULL), "large handshake");
    CHECK(SSL_write(p.ssl, big, 65536) == 65536, "large client write");
    CHECK(feed_client_output(st, &p, 65536), "large feed");
    total = 0;
    while (total < 65536) {
        n = adapter_read(st, got + total, 65536 - total);
        if (n <= 0) break;
        total += (size_t)n;
    }
    CHECK(total == 65536, "large read length");
    CHECK(memcmp(big, got, 65536) == 0, "large read contents");
    CHECK(adapter_write_all(st, &p, big, 65536), "large adapter write");
    CHECK(drain_server_output(st, &p), "large drain");
    total = 0;
    while (total < 65536) {
        n = SSL_read(p.ssl, got + total, (int)(65536 - total));
        if (n <= 0) break;
        total += (size_t)n;
    }
    CHECK(total == 65536, "large client read length");
    CHECK(memcmp(big, got, 65536) == 0, "large client read contents");
    free(big);
    free(got);
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_prefix(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char big[65536];
    unsigned char got[65536];
    size_t sent = 0;
    size_t received = 0;
    int i;
    if (ctx <= 0) return 0;
    for (i = 0; i < 65536; i++) big[i] = (unsigned char)((i * 17) & 0xff);
    st = tls_session_new(ctx);
    CHECK(st > 0, "prefix session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "prefix client");
    CHECK(handshake(st, &p, 65536, NULL), "prefix handshake");
    while (sent < 65536) {
        long token = make_buffer(big + sent, 65536 - sent);
        long n;
        CHECK(token > 0, "prefix plaintext buffer");
        n = tls_session_write(st, token, 0, (long)(65536 - sent));
        sys_res_close(token, 92);
        CHECK(n > 0, "prefix write accepted a prefix");
        if (n <= 0) break;
        sent += (size_t)n;
        /* Simulate a transport that accepts one ciphertext byte per call. */
        for (;;) {
            long out = sys_reg_new_buffer(1);
            unsigned char *dst = NULL;
            size_t cap = 0;
            long d;
            CHECK(out > 0, "prefix drain buffer");
            if (sys_reg_buffer_data(out, &dst, &cap) < 0) return 0;
            d = tls_session_drain(st, out, 1);
            if (d < 0) { sys_res_close(out, 92); return 0; }
            if (d == 0) { sys_res_close(out, 92); break; }
            CHECK(d == 1, "prefix drain is one byte");
            CHECK(BIO_write(p.rbio, dst, 1) == 1, "prefix feed one byte");
            CHECK(tls_session_commit(st, 1) == 1, "prefix commit one byte");
            sys_res_close(out, 92);
        }
    }
    while (received < 65536) {
        int n = SSL_read(p.ssl, got + received, (int)(65536 - received));
        if (n <= 0) break;
        received += (size_t)n;
    }
    CHECK(received == 65536, "prefix client read length");
    CHECK(memcmp(big, got, 65536) == 0, "prefix no replay or loss");
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_fragment(const char *dir) {
    long ctx = open_context(dir, 0, 0, 0, 1, "prefer",
                            (const char *const[]){ "http/1.1" }, 1);
    long st;
    struct peer p;
    unsigned char b;
    int i;
    int done = 0;
    if (ctx <= 0) return 0;
    st = tls_session_new(ctx);
    CHECK(st > 0, "fragment session");
    CHECK(peer_new(&p, NULL, NULL, NULL, TLS1_3_VERSION, TLS1_3_VERSION, 0, "http/1.1"), "fragment client");
    for (i = 0; i < 4096 && !done; i++) {
        long sr;
        int cr;
        if (BIO_read(p.wbio, &b, 1) == 1) {
            long token = make_buffer(&b, 1);
            long fed;
            CHECK(token > 0, "fragment byte buffer");
            fed = tls_session_feed(st, token, 0, 1);
            sys_res_close(token, 92);
            CHECK(fed == 1, "fragment feed one byte");
        }
        sr = tls_session_handshake(st);
        if (sr == TLS_STEP_WANT_READ && tls_session_output_pending(st) > 0) {
            CHECK(0, "fragment bare WantRead with output");
        }
        CHECK(drain_server_output(st, &p), "fragment drain");
        cr = client_handshake(&p);
        if (sr < 0 || cr < 0) break;
        if (sr == TLS_STEP_DONE && cr == 1) done = 1;
    }
    CHECK(done, "fragmented handshake finishes");
    if (done) {
        unsigned char got[8];
        CHECK(SSL_write(p.ssl, "frag", 4) == 4, "fragment write");
        CHECK(feed_client_output(st, &p, 1), "fragment data feed");
        CHECK(adapter_read(st, got, sizeof(got)) == 4, "fragment data read");
        CHECK(memcmp(got, "frag", 4) == 0, "fragment data");
    }
    peer_free(&p);
    sys_res_close(st, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int apply_identity(long ctx, const char *dir) {
    char path[4096];
    long token;
    snprintf(path, sizeof(path), "%s/server.pem", dir);
    token = read_file_buffer(path, NULL);
    if (token <= 0) return 0;
    if (tls_context_load_chain(ctx, token) != 1) { sys_res_close(token, 92); return 0; }
    sys_res_close(token, 92);
    snprintf(path, sizeof(path), "%s/server.key", dir);
    token = read_file_buffer(path, NULL);
    if (token <= 0) return 0;
    if (tls_context_load_key(ctx, token) != 1) { sys_res_close(token, 92); return 0; }
    sys_res_close(token, 92);
    return 1;
}

static int mode_limits(const char *dir) {
    long opt;
    long ctx;
    long st1;
    long st2;
    long token;
    long created[16];
    int count = 0;
    int i;

    opt = make_options(0, 0, 1, 0, 0, 8, 1, 1048576, 67108864);
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    CHECK(ctx > 0, "limit context");
    CHECK(apply_identity(ctx, dir), "limit identity");
    st1 = tls_session_new(ctx);
    CHECK(st1 > 0, "first session within maxSessions");
    st2 = tls_session_new(ctx);
    CHECK(st2 == -TLS_FAIL(TLS_FAIL_RESOURCE_LIMIT, 0), "maxSessions is ResourceLimit");
    sys_res_close(st1, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    opt = make_options(0, 0, 1, 0, 0, 8, 16, 16384, 67108864);
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    CHECK(ctx > 0, "handshake cap context");
    CHECK(apply_identity(ctx, dir), "handshake cap identity");
    st1 = tls_session_new(ctx);
    CHECK(st1 > 0, "handshake cap session");
    token = sys_reg_new_buffer(16385);
    CHECK(token > 0, "handshake cap buffer");
    CHECK(tls_session_feed(st1, token, 0, 16385) == -TLS_FAIL(TLS_FAIL_RESOURCE_LIMIT, 0),
          "handshake ciphertext cap is ResourceLimit");
    sys_res_close(token, 92);
    sys_res_close(st1, TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    opt = make_options(0, 0, 1, 0, 0, 8, 64, 1048576, 1048576);
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    CHECK(ctx > 0, "budget context");
    CHECK(apply_identity(ctx, dir), "budget identity");
    for (i = 0; i < 16; i++) {
        long s = tls_session_new(ctx);
        if (s < 0) {
            CHECK(s == -TLS_FAIL(TLS_FAIL_RESOURCE_LIMIT, 0), "aggregate budget is ResourceLimit");
            break;
        }
        created[count++] = s;
    }
    CHECK(count > 0 && count < 16, "aggregate budget admits a bounded number of sessions");
    for (i = 0; i < count; i++) sys_res_close(created[i], TLS_KIND_SESSION);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_validate(const char *dir) {
    long opt;
    long ctx;
    long st;
    long r;

    opt = make_options(0, 1, 0, 0, 0, 8, 16, 1048576, 67108864);
    ctx = tls_context_new(opt);
    sys_res_close(opt, 92);
    CHECK(ctx == -TLS_FAIL(TLS_FAIL_CONFIGURATION, 0), "min > max is Configuration");

    ctx = new_raw_context(0, 0, 0, 1);
    CHECK(ctx > 0, "validate context");
    CHECK(tls_context_ready(ctx) == 0, "context is not ready before cert/key");
    CHECK(tls_session_new(ctx) == -TLS_FAIL(TLS_FAIL_NOT_CONFIGURED, 0),
          "accept before configuration is NotConfigured");
    {
        char path[4096];
        long token;
        snprintf(path, sizeof(path), "%s/server.key", dir);
        token = read_file_buffer(path, NULL);
        r = tls_context_load_key(ctx, token);
        CHECK(r == -TLS_FAIL(TLS_FAIL_NOT_CONFIGURED, 0), "key before chain is NotConfigured");
        sys_res_close(token, 92);
    }
    {
        char path[4096];
        long token;
        snprintf(path, sizeof(path), "%s/server.pem", dir);
        token = read_file_buffer(path, NULL);
        CHECK(tls_context_load_chain(ctx, token) == 1, "validate chain");
        r = tls_context_load_chain(ctx, token);
        CHECK(r == -TLS_FAIL(TLS_FAIL_CONFIGURATION, 0), "second chain load is Configuration");
        sys_res_close(token, 92);
    }
    {
        char path[4096];
        long token;
        snprintf(path, sizeof(path), "%s/server.key", dir);
        token = read_file_buffer(path, NULL);
        CHECK(tls_context_load_key(ctx, token) == 1, "validate key");
        sys_res_close(token, 92);
    }
    CHECK(tls_context_ready(ctx) == 1, "context ready after chain/key");
    CHECK(set_alpn(ctx, TLS_ALPN_PREFER, (const char *const[]){ "h2", "h2" }, 2)
          == -TLS_FAIL(TLS_FAIL_INVALID_ARGUMENT, 0), "duplicate ALPN is InvalidArgument");
    {
        long v12 = 0;
        long v13 = 1;
        CHECK(tls_context_set_version(ctx, 1, v12) == 1, "set max TLS 1.2");
        CHECK(tls_context_set_version(ctx, 0, v13) == -TLS_FAIL(TLS_FAIL_CONFIGURATION, 0),
              "min > max setter is Configuration");
    }
    st = tls_session_new(ctx);
    CHECK(st > 0, "validate session");
    if (st > 0) {
        r = tls_context_set_alpn(ctx, 0);
        CHECK(r == -TLS_FAIL(TLS_FAIL_BUSY, 0), "setter after session is Busy");
        sys_res_close(st, TLS_KIND_SESSION);
    }
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    ctx = new_raw_context(0, 0, 0, 1);
    CHECK(ctx > 0, "poison context");
    {
        char path[4096];
        long token;
        snprintf(path, sizeof(path), "%s/server.pem", dir);
        token = read_file_buffer(path, NULL);
        CHECK(tls_context_load_chain(ctx, token) == 1, "poison chain");
        sys_res_close(token, 92);
        snprintf(path, sizeof(path), "%s/server.key", dir);
        token = read_file_buffer(path, NULL);
        CHECK(tls_context_load_key(ctx, token) == 1, "poison key");
        sys_res_close(token, 92);
    }
    {
        long token = sys_reg_new_buffer(5);
        unsigned char *dst = NULL;
        size_t cap = 0;
        if (token > 0 && sys_reg_buffer_data(token, &dst, &cap) == 0) {
            memcpy(dst, "BOGUS", 5);
            r = tls_context_set_groups(ctx, token);
            CHECK(r == -TLS_FAIL(TLS_FAIL_CONFIGURATION, 0), "bad group list is Configuration");
            CHECK(tls_session_new(ctx) == -TLS_FAIL(TLS_FAIL_CONFIGURATION, 0),
                  "poisoned context refuses sessions");
        }
        sys_res_close(token, 92);
    }
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

static int mode_cert_errors(const char *dir) {
    char path[4096];
    long ctx;
    long token;
    long r;

    snprintf(path, sizeof(path), "%s/weak.pem", dir);
    ctx = new_raw_context(0, 0, 0, 1);
    token = read_file_buffer(path, NULL);
    CHECK(ctx > 0 && token > 0, "weak context/read");
    r = tls_context_load_chain(ctx, token);
    CHECK(r == -TLS_FAIL(TLS_FAIL_CERTIFICATE_REJECTED, 0), "weak certificate is rejected");
    sys_res_close(token, 92);
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    ctx = new_raw_context(0, 0, 0, 1);
    CHECK(ctx > 0, "mismatch context");
    snprintf(path, sizeof(path), "%s/server.pem", dir);
    token = read_file_buffer(path, NULL);
    CHECK(tls_context_load_chain(ctx, token) == 1, "mismatch chain loads");
    sys_res_close(token, 92);
    snprintf(path, sizeof(path), "%s/client.key", dir);
    token = read_file_buffer(path, NULL);
    r = tls_context_load_key(ctx, token);
    CHECK(r == -TLS_FAIL(TLS_FAIL_KEY_REJECTED, 0), "mismatched key is rejected");
    sys_res_close(token, 92);
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    ctx = new_raw_context(0, 0, 0, 1);
    CHECK(ctx > 0, "encrypted context");
    snprintf(path, sizeof(path), "%s/server.pem", dir);
    token = read_file_buffer(path, NULL);
    CHECK(tls_context_load_chain(ctx, token) == 1, "encrypted chain loads");
    sys_res_close(token, 92);
    snprintf(path, sizeof(path), "%s/encrypted.key", dir);
    token = read_file_buffer(path, NULL);
    r = tls_context_load_key(ctx, token);
    CHECK(r == -TLS_FAIL(TLS_FAIL_KEY_REJECTED, 0), "encrypted key is rejected cleanly");
    sys_res_close(token, 92);
    sys_res_close(ctx, TLS_KIND_CONTEXT);

    ctx = new_raw_context(0, 0, 0, 1);
    CHECK(ctx > 0, "malformed context");
    snprintf(path, sizeof(path), "%s/malformed.pem", dir);
    token = read_file_buffer(path, NULL);
    r = tls_context_load_chain(ctx, token);
    CHECK(r == -TLS_FAIL(TLS_FAIL_CERTIFICATE_REJECTED, 0), "malformed chain is rejected");
    sys_res_close(token, 92);
    sys_res_close(ctx, TLS_KIND_CONTEXT);
    return 1;
}

int main(int argc, char **argv) {
    const char *mode;
    const char *dir;
    int rc;
    if (argc < 3) {
        fprintf(stderr, "usage: %s MODE FIXTURE_DIR\n", argv[0]);
        return 2;
    }
    mode = argv[1];
    dir = argv[2];
    if (sys_adapter_init(4096, 67108864) < 0) {
        fprintf(stderr, "sys_adapter_init failed\n");
        return 2;
    }
    if (tls_init() < 0) {
        fprintf(stderr, "tls_init failed\n");
        return 2;
    }
    if (strcmp(mode, "tls13") == 0) rc = mode_tls(dir, TLS1_3_VERSION, TLS1_3_VERSION, "http/1.1", "http/1.1");
    else if (strcmp(mode, "tls12") == 0) rc = mode_tls(dir, TLS1_2_VERSION, TLS1_2_VERSION, "http/1.1", "http/1.1");
    else if (strcmp(mode, "alpn-h2") == 0) rc = mode_tls(dir, TLS1_3_VERSION, TLS1_3_VERSION, "h2", "h2");
    else if (strcmp(mode, "alpn-none") == 0) rc = mode_tls(dir, TLS1_3_VERSION, TLS1_3_VERSION, NULL, NULL);
    else if (strcmp(mode, "echo13") == 0) rc = mode_echo(dir, TLS1_3_VERSION, TLS1_3_VERSION);
    else if (strcmp(mode, "echo12") == 0) rc = mode_echo(dir, TLS1_2_VERSION, TLS1_2_VERSION);
    else if (strcmp(mode, "require-missing") == 0) rc = mode_alpn_require_missing(dir);
    else if (strcmp(mode, "require-overlap") == 0) rc = mode_alpn_require_overlap(dir);
    else if (strcmp(mode, "prefer-none") == 0) rc = mode_alpn_prefer_none(dir);
    else if (strcmp(mode, "sni") == 0) rc = mode_sni(dir);
    else if (strcmp(mode, "clientcert") == 0) rc = mode_clientcert(dir);
    else if (strcmp(mode, "truncate") == 0) rc = mode_truncate(dir);
    else if (strcmp(mode, "keyupdate") == 0) rc = mode_keyupdate(dir);
    else if (strcmp(mode, "truncate-mid") == 0) rc = mode_truncate_mid(dir);
    else if (strcmp(mode, "large") == 0) rc = mode_large(dir);
    else if (strcmp(mode, "fragment") == 0) rc = mode_fragment(dir);
    else if (strcmp(mode, "prefix") == 0) rc = mode_prefix(dir);
    else if (strcmp(mode, "cert-errors") == 0) rc = mode_cert_errors(dir);
    else if (strcmp(mode, "validate") == 0) rc = mode_validate(dir);
    else if (strcmp(mode, "limits") == 0) rc = mode_limits(dir);
    else { fprintf(stderr, "unknown mode %s\n", mode); rc = 0; }
    if (rc) rc = failures == 0 ? 0 : 1;
    else rc = 1;
    {
        long live = sys_res_live_count();
        long sessions = sys_res_live_kind(TLS_KIND_SESSION);
        long contexts = sys_res_live_kind(TLS_KIND_CONTEXT);
        if (live != 0 || sessions != 0 || contexts != 0) {
            fprintf(stderr, "leak: live=%ld sessions=%ld contexts=%ld\n", live, sessions, contexts);
            rc = 1;
        }
    }
    sys_adapter_shutdown();
    if (failures != 0) fprintf(stderr, "%d check(s) failed\n", failures);
    else printf("ok %s\n", mode);
    return rc;
}
