/* Experimental server-side TLS adapter (P2T-b), trusted C implementation.
 *
 * Server-side TLS 1.2/1.3 over memory BIOs. The adapter never reads or writes
 * an fd or an obo buffer directly: obo moves bounded ciphertext/plaintext
 * through registered byte-buffer tokens, and this unit owns SSL*, SSL_CTX*,
 * BIO*, X509* and EVP_PKEY* objects. All callbacks are C-only.
 *
 * This is not a stable ABI and not a security boundary against trusted C.
 * See ../../../../design/tls-adapter-design.md and ../LIMITS.md.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <openssl/bio.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/opensslv.h>
#include <openssl/pem.h>
#include <openssl/provider.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#include <openssl/x509_vfy.h>
#include <openssl/x509v3.h>

#include "../../sys/native/sysregistry.h"
#include "tls_adapter.h"

#define TLS_STATE_NEW 0
#define TLS_STATE_HANDSHAKING 1
#define TLS_STATE_ESTABLISHED 2
#define TLS_STATE_SEND_CLOSED 3
#define TLS_STATE_RECV_CLOSED 4
#define TLS_STATE_BOTH_CLOSED 5
#define TLS_STATE_FAILED 6
#define TLS_STATE_CLOSED 7

#define TLS_ROLE_SERVER 0
#define TLS_ROLE_CLIENT 1
#define TLS_VERSION_12 0
#define TLS_VERSION_13 1
#define TLS_VERIFY_NONE 0
#define TLS_VERIFY_OPTIONAL 1
#define TLS_VERIFY_REQUIRED 2

typedef struct tls_context tls_context;
typedef struct tls_session tls_session;

struct tls_context {
    SSL_CTX *ssl_ctx;
    int role;
    int min_version;
    int max_version;
    int verify;
    int require_sni;
    int verify_depth;
    int max_sessions;
    int max_handshake_bytes;
    long max_context_bytes;
    int alpn_policy;
    int alpn_count;
    unsigned char alpn_wire[TLS_MAX_ALPN_BYTES];
    unsigned int alpn_wire_len;
    int cert_loaded;
    int key_loaded;
    int trust_set;
    int ready;
    int poisoned;
    int live_sessions;
    long long bytes_charged;
    long long extra_charge;
    long long trust_charge;
    long long started;
    long long completed;
    long long failures;
    char last_error[TLS_MAX_MESSAGE];
    tls_context *next;
    tls_context *prev;
};

struct tls_session {
    tls_context *owner;
    SSL *ssl;
    BIO *rbio;
    BIO *wbio;
    unsigned char *stage;
    size_t stage_len;
    size_t stage_off;
    size_t handshake_bytes;
    long long charge;
    int state;
    int transport_eof;
    int verify_mode;
    int alpn_required_missing;
    int received_alert;
    int sent_alert;
    X509 *peer_cert;
    char sni[256];
    char last_error[TLS_MAX_MESSAGE];
    tls_session *next;
    tls_session *prev;
};

static int g_initialized;
static int g_ex_index = -1;
static long long g_live_contexts;
static long long g_live_sessions;
static long long g_started;
static long long g_completed;
static long long g_failures;
static long long g_verify_failures;
static long long g_alpn_rejections;
static long long g_truncations;
static long long g_bytes_charged;
static long long g_max_context_bytes;
static tls_context *g_contexts;
static tls_session *g_sessions;

static long fail(long kind, long payload) {
    return -TLS_FAIL(kind, payload);
}

static long fail_plain(long kind) {
    return fail(kind, 0);
}

static void message(char *dst, size_t cap, const char *fmt, ...) {
    va_list ap;
    if (dst == NULL || cap == 0) return;
    va_start(ap, fmt);
    (void)vsnprintf(dst, cap, fmt, ap);
    va_end(ap);
}

/* Replace control bytes so diagnostics stay single-line and safe. */
static void copy_text(char *dst, size_t cap, const unsigned char *src, size_t len) {
    size_t i;
    size_t at = 0;
    if (dst == NULL || cap == 0) return;
    if (src == NULL) { dst[0] = '\0'; return; }
    for (i = 0; i < len && at + 1 < cap; i++) {
        unsigned char c = src[i];
        if (c < 0x20 || c == 0x7f) {
            dst[at++] = '?';
        } else {
            dst[at++] = (char)c;
        }
    }
    dst[at] = '\0';
}

static void drain_errors(char *dst, size_t cap) {
    size_t at = 0;
    int i;
    if (dst != NULL && cap != 0) dst[0] = '\0';
    for (i = 0; i < 16; i++) {
        unsigned long e = ERR_get_error();
        char line[256];
        size_t n;
        if (e == 0) break;
        if (dst == NULL || cap == 0) continue;
        ERR_error_string_n(e, line, sizeof(line));
        n = strlen(line);
        if (at != 0 && at + 2 < cap) {
            dst[at++] = ';';
            dst[at++] = ' ';
            dst[at] = '\0';
        }
        if (at + n >= cap) n = cap > at ? cap - at - 1 : 0;
        memcpy(dst + at, line, n);
        at += n;
        dst[at] = '\0';
    }
    ERR_clear_error();
}

static long buffer_span(long token, unsigned char **data, size_t *len) {
    long r = sys_reg_buffer_data(token, data, len);
    if (r < 0) return fail(TLS_FAIL_INVALID_RESOURCE, 0);
    if (*data == NULL && *len != 0) return fail(TLS_FAIL_INVALID_RESOURCE, 0);
    return 1;
}

static long buffer_read_i64(long token, long index, long long *out) {
    unsigned char *data = NULL;
    size_t len = 0;
    size_t off;
    long r;
    if (index < 0 || index > 0xffff) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    off = (size_t)index * 8u;
    r = buffer_span(token, &data, &len);
    if (r < 0) return r;
    if (off + 8 > len) return fail_plain(TLS_FAIL_RANGE);
    {
        uint64_t v = 0;
        int i;
        for (i = 0; i < 8; i++) v |= ((uint64_t)data[off + (size_t)i]) << (8 * i);
        *out = (long long)v;
    }
    return 1;
}

static long buffer_write_i64(long token, long index, long long value) {
    unsigned char *data = NULL;
    size_t len = 0;
    size_t off;
    long r;
    if (index < 0 || index > 0xffff) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    off = (size_t)index * 8u;
    r = buffer_span(token, &data, &len);
    if (r < 0) return r;
    if (off + 8 > len) return fail_plain(TLS_FAIL_RANGE);
    {
        uint64_t v = (uint64_t)value;
        int i;
        for (i = 0; i < 8; i++) data[off + (size_t)i] = (unsigned char)((v >> (8 * i)) & 0xffu);
    }
    return 1;
}

static tls_context *context_of(long token) {
    return (tls_context *)sys_reg_peek(token, TLS_KIND_CONTEXT);
}

static tls_session *session_of(long token) {
    return (tls_session *)sys_reg_peek(token, TLS_KIND_SESSION);
}

static void context_clear_error(tls_context *c) {
    if (c != NULL) c->last_error[0] = '\0';
}

static void session_clear_error(tls_session *s) {
    if (s != NULL) s->last_error[0] = '\0';
}

static void context_fail_text(tls_context *c, const char *text) {
    if (c != NULL) message(c->last_error, sizeof(c->last_error), "%s", text);
}

static void session_fail_text(tls_session *s, const char *text) {
    if (s != NULL) message(s->last_error, sizeof(s->last_error), "%s", text);
}

static long session_fail(tls_session *s, long kind, long payload, const char *text) {
    if (s != NULL) {
        s->state = TLS_STATE_FAILED;
        if (s->owner != NULL) {
            s->owner->failures += 1;
        }
        g_failures += 1;
        if (kind == TLS_FAIL_VERIFY_FAILED) g_verify_failures += 1;
        if (kind == TLS_FAIL_ALPN_REQUIRED) g_alpn_rejections += 1;
        if (kind == TLS_FAIL_TRUNCATED) g_truncations += 1;
        if (text != NULL) session_fail_text(s, text);
    }
    return fail(kind, payload);
}

static long session_charge_update(tls_session *s) {
    long long new_charge;
    long long delta;
    long rbio_pending;
    long wbio_pending;
    if (s == NULL || s->owner == NULL) return 1;
    rbio_pending = (long)BIO_ctrl_pending(s->rbio);
    wbio_pending = (long)BIO_ctrl_pending(s->wbio);
    if (rbio_pending < 0) rbio_pending = 0;
    if (wbio_pending < 0) wbio_pending = 0;
    if ((size_t)rbio_pending > TLS_MAX_BIO || (size_t)wbio_pending > TLS_MAX_BIO) {
        return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "TLS BIO pending bytes exceed the per-session cap");
    }
    new_charge = (long long)sizeof(tls_session) + (long long)TLS_MAX_STAGE +
                 (long long)(s->stage_len - s->stage_off) +
                 (long long)rbio_pending + (long long)wbio_pending;
    if (new_charge > s->owner->max_context_bytes) {
        return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "TLS session charge exceeds the context budget");
    }
    if (new_charge > s->owner->max_context_bytes - s->owner->bytes_charged + s->charge) {
        return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "TLS aggregate context budget exceeded");
    }
    delta = new_charge - s->charge;
    s->charge = new_charge;
    s->owner->bytes_charged += delta;
    g_bytes_charged += delta;
    if (g_bytes_charged < 0) g_bytes_charged = 0;
    return 1;
}

static long session_output_bytes(tls_session *s) {
    long pending;
    if (s == NULL) return 0;
    pending = (long)BIO_ctrl_pending(s->wbio);
    if (pending < 0) pending = 0;
    pending += (long)(s->stage_len - s->stage_off);
    return pending;
}

static long context_charge(tls_context *c, long long n) {
    if (c == NULL || n < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (c->bytes_charged > c->max_context_bytes - n) {
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    if (g_bytes_charged > 0x7fffffffffffffffLL - n) {
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    c->extra_charge += n;
    c->bytes_charged += n;
    g_bytes_charged += n;
    return 1;
}

static long context_recharge(tls_context *c, long long old_charge, long long new_charge) {
    long long delta;
    if (c == NULL || old_charge < 0 || new_charge < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    delta = new_charge - old_charge;
    if (delta > 0) {
        if (c->bytes_charged > c->max_context_bytes - delta) {
            return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
        }
        if (g_bytes_charged > 0x7fffffffffffffffLL - delta) {
            return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
        }
    }
    c->extra_charge += delta;
    c->bytes_charged += delta;
    g_bytes_charged += delta;
    if (c->extra_charge < 0) c->extra_charge = 0;
    if (c->bytes_charged < 0) c->bytes_charged = 0;
    if (g_bytes_charged < 0) g_bytes_charged = 0;
    return 1;
}

static void context_uncharge(tls_context *c, long long n) {
    if (c == NULL || n <= 0) return;
    if (c->extra_charge >= n) c->extra_charge -= n; else c->extra_charge = 0;
    if (c->bytes_charged >= n) c->bytes_charged -= n; else c->bytes_charged = 0;
    if (g_bytes_charged >= n) g_bytes_charged -= n; else g_bytes_charged = 0;
}

static long context_set_ready(tls_context *c) {
    if (c == NULL) return 1;
    if (c->role == TLS_ROLE_SERVER) {
        c->ready = c->cert_loaded && c->key_loaded &&
                   (c->verify == TLS_VERIFY_NONE || c->trust_set);
    } else {
        c->ready = c->verify == TLS_VERIFY_NONE || c->trust_set;
    }
    return 1;
}

static int alpn_select_cb(SSL *ssl, const unsigned char **out, unsigned char *outlen,
                          const unsigned char *in, unsigned int inlen, void *arg) {
    tls_context *c = (tls_context *)arg;
    unsigned char *selected = NULL;
    unsigned char selected_len = 0;
    tls_session *s = SSL_get_ex_data(ssl, g_ex_index);
    (void)s;
    if (c == NULL || c->alpn_policy == TLS_ALPN_DISABLED) return SSL_TLSEXT_ERR_NOACK;
    if (SSL_select_next_proto(&selected, &selected_len, c->alpn_wire,
                              c->alpn_wire_len, in, inlen) == OPENSSL_NPN_NEGOTIATED) {
        *out = selected;
        *outlen = (unsigned char)selected_len;
        return SSL_TLSEXT_ERR_OK;
    }
    if (c->alpn_policy == TLS_ALPN_REQUIRE) {
        return SSL_TLSEXT_ERR_ALERT_FATAL;
    }
    return SSL_TLSEXT_ERR_NOACK;
}

static int client_hello_cb(SSL *ssl, int *al, void *arg) {
    tls_context *c = (tls_context *)arg;
    tls_session *s = SSL_get_ex_data(ssl, g_ex_index);
    const unsigned char *ext = NULL;
    size_t extlen = 0;
    if (c == NULL || c->alpn_policy != TLS_ALPN_REQUIRE) {
        return SSL_CLIENT_HELLO_SUCCESS;
    }
    if (SSL_client_hello_get0_ext(ssl, TLSEXT_TYPE_application_layer_protocol_negotiation,
                                  &ext, &extlen) == 0) {
        if (s != NULL) s->alpn_required_missing = 1;
        if (al != NULL) *al = SSL_AD_MISSING_EXTENSION;
        return SSL_CLIENT_HELLO_ERROR;
    }
    (void)ext;
    (void)extlen;
    return SSL_CLIENT_HELLO_SUCCESS;
}

static int sni_cb(SSL *ssl, int *al, void *arg) {
    tls_context *c = (tls_context *)arg;
    tls_session *s = SSL_get_ex_data(ssl, g_ex_index);
    const char *name = SSL_get_servername(ssl, TLSEXT_NAMETYPE_host_name);
    if (s != NULL) {
        if (name != NULL) copy_text(s->sni, sizeof(s->sni), (const unsigned char *)name, strlen(name));
        else s->sni[0] = '\0';
    }
    if (c != NULL && c->require_sni && (name == NULL || name[0] == '\0')) {
        if (al != NULL) *al = SSL_AD_UNRECOGNIZED_NAME;
        return SSL_TLSEXT_ERR_ALERT_FATAL;
    }
    return SSL_TLSEXT_ERR_OK;
}

static void info_cb(const SSL *ssl, int where, int ret) {
    tls_session *s = (tls_session *)SSL_get_ex_data(ssl, g_ex_index);
    if (s == NULL) return;
    if (where & SSL_CB_READ_ALERT) s->received_alert = ret & 0xff;
    if (where & SSL_CB_WRITE_ALERT) s->sent_alert = ret & 0xff;
}

static long map_ssl_error(tls_session *s, int ret, const char *operation) {
    int e;
    int saved_errno;
    unsigned long first = 0;
    int reason = 0;
    char text[TLS_MAX_MESSAGE];
    int have_text = 0;

    if (s == NULL) return fail_plain(TLS_FAIL_INTERNAL);
    e = SSL_get_error(s->ssl, ret);
    saved_errno = errno;
    text[0] = '\0';

    if (e == SSL_ERROR_WANT_READ) {
        ERR_clear_error();
        return TLS_STEP_WANT_READ;
    }
    if (e == SSL_ERROR_WANT_WRITE) {
        ERR_clear_error();
        return TLS_STEP_WANT_WRITE;
    }
    if (e == SSL_ERROR_ZERO_RETURN) {
        ERR_clear_error();
        return 0;
    }
    if (e == SSL_ERROR_WANT_X509_LOOKUP || e == SSL_ERROR_WANT_ASYNC ||
        e == SSL_ERROR_WANT_ASYNC_JOB) {
        ERR_clear_error();
        return session_fail(s, TLS_FAIL_UNSUPPORTED, 0, "OpenSSL requested an unsupported asynchronous operation");
    }
    if (e == SSL_ERROR_WANT_CLIENT_HELLO_CB || e == SSL_ERROR_WANT_RETRY_VERIFY) {
        ERR_clear_error();
        return session_fail(s, TLS_FAIL_INTERNAL, 0, "OpenSSL requested an unsupported retry callback");
    }

    first = ERR_peek_error();
    reason = first != 0 ? ERR_GET_REASON(first) : 0;
    if (first != 0) {
        drain_errors(text, sizeof(text));
        have_text = text[0] != '\0';
    }
    ERR_clear_error();

    if (s->alpn_required_missing) {
        return session_fail(s, TLS_FAIL_ALPN_REQUIRED, 0,
                            have_text ? text : "client sent no ALPN extension while the server requires it");
    }
    if (reason == SSL_R_NO_APPLICATION_PROTOCOL) {
        g_alpn_rejections += 1;
        return session_fail(s, TLS_FAIL_NO_APPLICATION_PROTOCOL, 0,
                            have_text ? text : "no application protocol overlap");
    }
    if (reason == SSL_R_PEER_DID_NOT_RETURN_A_CERTIFICATE ||
        reason == SSL_R_TLSV13_ALERT_CERTIFICATE_REQUIRED) {
        return session_fail(s, TLS_FAIL_NO_PEER_CERTIFICATE, 0,
                            have_text ? text : "peer did not return a certificate");
    }
    if (reason == SSL_R_UNEXPECTED_EOF_WHILE_READING) {
        return session_fail(s, TLS_FAIL_TRUNCATED, 0,
                            have_text ? text : "unexpected EOF while reading TLS records");
    }
    if (e == SSL_ERROR_SSL) {
        long vr = SSL_get_verify_result(s->ssl);
        if (vr != X509_V_OK) {
            return session_fail(s, TLS_FAIL_VERIFY_FAILED, (long)vr,
                                have_text ? text : "peer certificate verification failed");
        }
        if (s->received_alert != 0) {
            return session_fail(s, TLS_FAIL_PROTOCOL_ALERT, (long)s->received_alert,
                                have_text ? text : "peer sent a fatal TLS alert");
        }
        return session_fail(s, TLS_FAIL_PROTOCOL_VIOLATION, 0,
                            have_text ? text : "TLS protocol error");
    }
    if (e == SSL_ERROR_SYSCALL) {
        if (ret == 0 && first == 0) {
            return session_fail(s, TLS_FAIL_TRUNCATED, 0, "transport EOF without close_notify");
        }
        if (saved_errno != 0) {
            return session_fail(s, TLS_FAIL_TRANSPORT, (long)saved_errno,
                                have_text ? text : "TLS transport syscall failed");
        }
        return session_fail(s, TLS_FAIL_TRUNCATED, 0, "TLS transport ended unexpectedly");
    }
    (void)operation;
    return session_fail(s, TLS_FAIL_INTERNAL, 0, have_text ? text : "unexpected OpenSSL error");
}

/* Parse a chain of PEM certificate blocks. The input must contain only
 * whitespace outside certificate blocks; other PEM types and trailing garbage
 * are rejected before any OpenSSL install. */
static int pem_count_cert_blocks(const unsigned char *data, size_t len, long max_count,
                                 long *count_out) {
    const char *begin = "-----BEGIN CERTIFICATE-----";
    const char *end = "-----END CERTIFICATE-----";
    size_t blen = strlen(begin);
    size_t elen = strlen(end);
    size_t i = 0;
    long count = 0;
    while (i < len) {
        unsigned char c = data[i];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') { i++; continue; }
        if (i + blen > len || memcmp(data + i, begin, blen) != 0) return 0;
        if (count >= max_count) return 0;
        i += blen;
        {
            size_t j = i;
            int found = 0;
            while (j + elen <= len) {
                if (memcmp(data + j, end, elen) == 0) {
                    found = 1;
                    j += elen;
                    break;
                }
                j++;
            }
            if (!found) return 0;
            i = j;
        }
        count++;
    }
    *count_out = count;
    return count > 0;
}

static long parse_pem_certs(const unsigned char *data, size_t len, long max_count,
                            X509 ***certs_out, long *count_out) {
    long expected = 0;
    long got = 0;
    X509 **certs;
    BIO *bio;
    long i;
    if (data == NULL || len == 0) return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
    if (!pem_count_cert_blocks(data, len, max_count, &expected)) {
        return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
    }
    if (expected > max_count) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    certs = (X509 **)calloc((size_t)expected, sizeof(*certs));
    if (certs == NULL) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    bio = BIO_new_mem_buf(data, (int)len);
    if (bio == NULL) { free(certs); return fail_plain(TLS_FAIL_RESOURCE_LIMIT); }
    ERR_clear_error();
    while (got < expected) {
        X509 *x = PEM_read_bio_X509(bio, NULL, NULL, NULL);
        int der_len;
        if (x == NULL) break;
        der_len = i2d_X509(x, NULL);
        if (der_len <= 0 || (long)der_len > TLS_MAX_CERT_DER) {
            X509_free(x);
            break;
        }
        certs[got++] = x;
    }
    BIO_free(bio);
    if (got != expected) {
        for (i = 0; i < got; i++) X509_free(certs[i]);
        free(certs);
        drain_errors(NULL, 0);
        return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
    }
    *certs_out = certs;
    *count_out = got;
    return 1;
}

static int name_is(const char *s, size_t len, const char *want) {
    size_t n = strlen(want);
    return n == len && memcmp(s, want, n) == 0;
}

static int allowed_cipher_suite(const char *s, size_t len) {
    return name_is(s, len, "TLS_AES_256_GCM_SHA384") ||
           name_is(s, len, "TLS_CHACHA20_POLY1305_SHA256") ||
           name_is(s, len, "TLS_AES_128_GCM_SHA256");
}

static int allowed_cipher_list(const char *s, size_t len) {
    return name_is(s, len, "ECDHE-ECDSA-AES256-GCM-SHA384") ||
           name_is(s, len, "ECDHE-RSA-AES256-GCM-SHA384") ||
           name_is(s, len, "ECDHE-ECDSA-CHACHA20-POLY1305") ||
           name_is(s, len, "ECDHE-RSA-CHACHA20-POLY1305") ||
           name_is(s, len, "ECDHE-ECDSA-AES128-GCM-SHA256") ||
           name_is(s, len, "ECDHE-RSA-AES128-GCM-SHA256");
}

static int allowed_group(const char *s, size_t len) {
    return name_is(s, len, "X25519") || name_is(s, len, "P-256") ||
           name_is(s, len, "P-384");
}

static int allowed_list_name(const char *s, size_t len, int which) {
    if (which == 0) return allowed_cipher_suite(s, len);
    if (which == 1) return allowed_cipher_list(s, len);
    if (which == 2) return allowed_group(s, len);
    return 0;
}

/* Validate a colon-joined cipher/group list against the exact approved AEAD/
 * group names. OpenSSL expressions, broad keywords (ALL/CBC/SHA/AES), NULL/
 * weak names and CBC suites are rejected before SSL_CTX mutation. */
static long validate_list_string(const unsigned char *data, size_t len,
                                 char *out, size_t cap, long which) {
    size_t at = 0;
    size_t token_start = 0;
    long count = 0;
    if (data == NULL || out == NULL || cap == 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (len == 0 || len > 1024) return fail_plain(TLS_FAIL_RANGE);
    if (len + 1 > cap) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    for (at = 0; at <= len; at++) {
        if (at == len || data[at] == ':') {
            size_t n = at - token_start;
            if (n == 0 || n > 255 || count >= 64) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
            if (!allowed_list_name((const char *)data + token_start, n, (int)which)) {
                return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
            }
            count++;
            token_start = at + 1;
        }
    }
    memcpy(out, data, len);
    out[len] = '\0';
    return 1;
}

static long enforce_security_level(tls_context *c) {
    SSL_CTX_set_security_level(c->ssl_ctx, 2);
    if (SSL_CTX_get_security_level(c->ssl_ctx) != 2) {
        c->poisoned = 1;
        return fail_plain(TLS_FAIL_CONFIGURATION);
    }
    return 1;
}

static int key_acceptable(EVP_PKEY *key) {
    int type;
    int bits;
    if (key == NULL) return 0;
    type = EVP_PKEY_base_id(key);
    bits = EVP_PKEY_bits(key);
    if (type == EVP_PKEY_RSA || type == EVP_PKEY_RSA_PSS) return bits >= 2048;
    if (type == EVP_PKEY_EC) return bits >= 256;
    if (type == EVP_PKEY_ED25519) return 1;
    return 1;
}

static int cert_key_acceptable(X509 *x) {
    EVP_PKEY *key;
    int ok;
    if (x == NULL) return 0;
    key = X509_get0_pubkey(x);
    if (key == NULL) return 0;
    ok = key_acceptable(key);
    return ok;
}

static int no_password_cb(char *buf, int size, int rwflag, void *userdata) {
    (void)buf;
    (void)size;
    (void)rwflag;
    (void)userdata;
    return 0;
}

static long context_mutating_check(tls_context *c) {
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (c->poisoned) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (c->live_sessions > 0) return fail_plain(TLS_FAIL_BUSY);
    return 1;
}

static int context_release(void *p) {
    tls_context *c = (tls_context *)p;
    long long charge;
    if (c == NULL) return EINVAL;
    /* A public res can bypass closeContext's Busy check. Registry retirement
     * happens before release, so release must never fail and strand the
     * context. Detach any live sessions; their SSL objects hold their own
     * SSL_CTX reference and remain valid, and their release will not touch the
     * freed owner. */
    if (c->live_sessions != 0) {
        tls_session *s;
        for (s = g_sessions; s != NULL; s = s->next) {
            if (s->owner == c) {
                s->owner = NULL;
                if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) {
                    s->state = TLS_STATE_FAILED;
                    session_fail_text(s, "TLS context was closed during the handshake");
                }
            }
        }
        c->live_sessions = 0;
    }
    if (c->ssl_ctx != NULL) {
        /* Clear every callback that carries the context pointer before the
         * context is freed. Detached SSL objects retain an SSL_CTX reference,
         * but must never call back into freed adapter state. */
        SSL_CTX_set_alpn_select_cb(c->ssl_ctx, NULL, NULL);
        SSL_CTX_set_client_hello_cb(c->ssl_ctx, NULL, NULL);
        SSL_CTX_set_tlsext_servername_callback(c->ssl_ctx, NULL);
        SSL_CTX_set_tlsext_servername_arg(c->ssl_ctx, NULL);
        SSL_CTX_set_info_callback(c->ssl_ctx, NULL);
    }
    charge = (long long)sizeof(tls_context) + c->extra_charge;
    if (c->ssl_ctx != NULL) SSL_CTX_free(c->ssl_ctx);
    if (c->next != NULL) c->next->prev = c->prev;
    if (c->prev != NULL) c->prev->next = c->next;
    else g_contexts = c->next;
    free(c);
    g_live_contexts -= 1;
    if (g_live_contexts < 0) g_live_contexts = 0;
    g_bytes_charged -= charge;
    if (g_bytes_charged < 0) g_bytes_charged = 0;
    return 0;
}

static int session_release(void *p) {
    tls_session *s = (tls_session *)p;
    if (s == NULL) return EINVAL;
    if (s->owner != NULL) {
        s->owner->live_sessions -= 1;
        if (s->owner->live_sessions < 0) s->owner->live_sessions = 0;
        s->owner->bytes_charged -= s->charge;
        if (s->owner->bytes_charged < 0) s->owner->bytes_charged = 0;
    }
    if (s->next != NULL) s->next->prev = s->prev;
    if (s->prev != NULL) s->prev->next = s->next;
    else g_sessions = s->next;
    if (s->ssl != NULL) SSL_free(s->ssl);
    if (s->peer_cert != NULL) X509_free(s->peer_cert);
    if (s->stage != NULL) {
        OPENSSL_cleanse(s->stage, TLS_MAX_STAGE);
        free(s->stage);
    }
    g_bytes_charged -= s->charge;
    if (g_bytes_charged < 0) g_bytes_charged = 0;
    g_live_sessions -= 1;
    if (g_live_sessions < 0) g_live_sessions = 0;
    OPENSSL_cleanse(s, sizeof(*s));
    free(s);
    return 0;
}

long tls_version_num(void) {
    return (long)OpenSSL_version_num();
}

long tls_init(void) {
    if (g_initialized) return 1;
    if (!OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)) {
        return fail_plain(TLS_FAIL_UNSUPPORTED);
    }
    if (OpenSSL_version_num() < 0x30000000UL) {
        return fail_plain(TLS_FAIL_UNSUPPORTED);
    }
    if (g_ex_index < 0) {
        g_ex_index = SSL_get_ex_new_index(0, NULL, NULL, NULL, NULL);
        if (g_ex_index < 0) return fail_plain(TLS_FAIL_INTERNAL);
    }
    if (OSSL_PROVIDER_available(NULL, "default") != 1) {
        if (OSSL_PROVIDER_load(NULL, "default") == NULL) {
            drain_errors(NULL, 0);
            return fail_plain(TLS_FAIL_UNSUPPORTED);
        }
    }
    ERR_clear_error();
    g_initialized = 1;
    return 1;
}

long tls_context_new(long option_token) {
    unsigned char *data = NULL;
    size_t len = 0;
    long long role, minv, maxv, verify, require_sni, depth, max_sessions;
    long long max_handshake, max_context;
    tls_context *c;
    long init;
    long token;
    long r;

    init = tls_init();
    if (init < 0) return init;
    r = buffer_span(option_token, &data, &len);
    if (r < 0) return r;
    if (len < (size_t)TLS_OPTION_BYTES) return fail_plain(TLS_FAIL_RANGE);
    if (buffer_read_i64(option_token, TLS_OPTION_ROLE, &role) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_MIN_VERSION, &minv) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_MAX_VERSION, &maxv) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_VERIFY, &verify) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_REQUIRE_SNI, &require_sni) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_VERIFY_DEPTH, &depth) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_MAX_SESSIONS, &max_sessions) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_MAX_HANDSHAKE, &max_handshake) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(option_token, TLS_OPTION_MAX_CONTEXT_BYTES, &max_context) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);

    if (role != TLS_ROLE_SERVER && role != TLS_ROLE_CLIENT) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (minv < TLS_VERSION_12 || minv > TLS_VERSION_13) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (maxv < TLS_VERSION_12 || maxv > TLS_VERSION_13) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (minv > maxv) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (verify < TLS_VERIFY_NONE || verify > TLS_VERIFY_REQUIRED) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (role == TLS_ROLE_CLIENT && verify == TLS_VERIFY_OPTIONAL) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (require_sni != 0 && require_sni != 1) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (role == TLS_ROLE_CLIENT && require_sni != 0) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (depth < 0 || depth > 16) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (max_sessions < 1 || max_sessions > 4096) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (max_handshake < 16384 || max_handshake > 8388608) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (max_context < 1048576 || max_context > 1073741824) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if ((long long)sizeof(tls_context) > max_context) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);

    c = (tls_context *)calloc(1, sizeof(*c));
    if (c == NULL) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    c->role = (int)role;
    c->min_version = (int)minv;
    c->max_version = (int)maxv;
    c->verify = (int)verify;
    c->require_sni = (int)require_sni;
    c->verify_depth = (int)depth;
    c->max_sessions = (int)max_sessions;
    c->max_handshake_bytes = (int)max_handshake;
    c->max_context_bytes = (long)max_context;
    c->alpn_policy = TLS_ALPN_DISABLED;
    c->ssl_ctx = SSL_CTX_new(role == TLS_ROLE_SERVER ? TLS_server_method() : TLS_client_method());
    if (c->ssl_ctx == NULL) {
        drain_errors(c->last_error, sizeof(c->last_error));
        free(c);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    ERR_clear_error();
    if (SSL_CTX_set_min_proto_version(c->ssl_ctx, minv == TLS_VERSION_12 ? TLS1_2_VERSION : TLS1_3_VERSION) != 1 ||
        SSL_CTX_set_max_proto_version(c->ssl_ctx, maxv == TLS_VERSION_12 ? TLS1_2_VERSION : TLS1_3_VERSION) != 1) {
        context_fail_text(c, "could not set the TLS version range");
        goto config_fail;
    }
    if (SSL_CTX_set_ciphersuites(c->ssl_ctx,
            "TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256") != 1 ||
        SSL_CTX_set_cipher_list(c->ssl_ctx,
            "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
            "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
            "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256") != 1 ||
        SSL_CTX_set1_groups_list(c->ssl_ctx, "X25519:P-256:P-384") != 1) {
        context_fail_text(c, "could not apply the default cipher/group policy");
        goto config_fail;
    }
    SSL_CTX_set_security_level(c->ssl_ctx, 2);
    if (SSL_CTX_get_security_level(c->ssl_ctx) != 2) {
        context_fail_text(c, "could not establish security level 2");
        goto config_fail;
    }
    SSL_CTX_set_options(c->ssl_ctx,
                        SSL_OP_NO_SSLv3 | SSL_OP_NO_TLSv1 | SSL_OP_NO_TLSv1_1 |
                        SSL_OP_NO_RENEGOTIATION | SSL_OP_NO_COMPRESSION |
                        SSL_OP_NO_TICKET | SSL_OP_CIPHER_SERVER_PREFERENCE);
    SSL_CTX_set_mode(c->ssl_ctx, SSL_MODE_ENABLE_PARTIAL_WRITE |
                                 SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER |
                                 SSL_MODE_RELEASE_BUFFERS);
    SSL_CTX_set_session_cache_mode(c->ssl_ctx, SSL_SESS_CACHE_OFF);
    if (SSL_CTX_set_num_tickets(c->ssl_ctx, 0) != 1 ||
        SSL_CTX_set_max_early_data(c->ssl_ctx, 0) != 1) {
        context_fail_text(c, "could not disable session tickets/early data");
        goto config_fail;
    }
    SSL_CTX_set_post_handshake_auth(c->ssl_ctx, 0);
    SSL_CTX_set_verify_depth(c->ssl_ctx, (int)depth);
    (void)SSL_CTX_set_max_cert_list(c->ssl_ctx, TLS_MAX_CERT_DER);
    if (role == TLS_ROLE_SERVER) {
        int mode = SSL_VERIFY_NONE;
        if (verify == TLS_VERIFY_OPTIONAL) mode = SSL_VERIFY_PEER;
        if (verify == TLS_VERIFY_REQUIRED) mode = SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT;
        SSL_CTX_set_verify(c->ssl_ctx, mode, NULL);
        SSL_CTX_set_client_hello_cb(c->ssl_ctx, NULL, NULL);
        SSL_CTX_set_tlsext_servername_callback(c->ssl_ctx, sni_cb);
        SSL_CTX_set_tlsext_servername_arg(c->ssl_ctx, c);
    }
    SSL_CTX_set_info_callback(c->ssl_ctx, info_cb);
    ERR_clear_error();

    token = sys_reg_new_object(TLS_KIND_CONTEXT, c, context_release, sizeof(*c));
    if (token < 0) {
        SSL_CTX_free(c->ssl_ctx);
        free(c);
        if (token == -EDQUOT) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    c->next = g_contexts;
    if (g_contexts != NULL) g_contexts->prev = c;
    g_contexts = c;
    g_live_contexts += 1;
    c->bytes_charged = (long long)sizeof(*c);
    g_bytes_charged += (long long)sizeof(*c);
    if (g_bytes_charged > g_max_context_bytes) g_max_context_bytes = max_context;
    (void)context_set_ready(c);
    return token;

config_fail:
    drain_errors(c->last_error, sizeof(c->last_error));
    SSL_CTX_free(c->ssl_ctx);
    free(c);
    return fail_plain(TLS_FAIL_CONFIGURATION);
}

long tls_context_load_chain(long context_token, long pem_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    X509 **certs = NULL;
    long count = 0;
    long r;
    long i;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    if (c->cert_loaded) return fail_plain(TLS_FAIL_CONFIGURATION);
    r = buffer_span(pem_token, &data, &len);
    if (r < 0) return r;
    if (len == 0 || len > (size_t)TLS_MAX_CHAIN_PEM) return fail_plain(TLS_FAIL_RANGE);
    r = context_charge(c, (long long)len);
    if (r < 0) return r;
    ERR_clear_error();
    r = parse_pem_certs(data, len, TLS_MAX_CHAIN_CERTS, &certs, &count);
    if (r < 0) {
        context_uncharge(c, (long long)len);
        context_fail_text(c, "certificate chain PEM did not parse");
        return r;
    }
    for (i = 0; i < count; i++) {
        if (!cert_key_acceptable(certs[i])) {
            context_fail_text(c, "certificate public key is below security level 2");
            for (long j = 0; j < count; j++) X509_free(certs[j]);
            free(certs);
            context_uncharge(c, (long long)len);
            return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
        }
    }
    ERR_clear_error();
    if (SSL_CTX_use_certificate(c->ssl_ctx, certs[0]) != 1) {
        context_fail_text(c, "SSL_CTX_use_certificate failed");
        c->poisoned = 1;
        for (i = 0; i < count; i++) X509_free(certs[i]);
        free(certs);
        context_uncharge(c, (long long)len);
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
    }
    X509_free(certs[0]);
    certs[0] = NULL;
    for (i = 1; i < count; i++) {
        if (SSL_CTX_add_extra_chain_cert(c->ssl_ctx, certs[i]) != 1) {
            context_fail_text(c, "could not add an intermediate certificate");
            c->poisoned = 1;
            for (long j = i; j < count; j++) X509_free(certs[j]);
            free(certs);
            context_uncharge(c, (long long)len);
            return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
        }
        certs[i] = NULL;
    }
    free(certs);
    c->cert_loaded = 1;
    context_set_ready(c);
    ERR_clear_error();
    return 1;
}

long tls_context_load_key(long context_token, long pem_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    BIO *bio = NULL;
    EVP_PKEY *key = NULL;
    long r;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    if (!c->cert_loaded) return fail_plain(TLS_FAIL_NOT_CONFIGURED);
    if (c->key_loaded) return fail_plain(TLS_FAIL_CONFIGURATION);
    r = buffer_span(pem_token, &data, &len);
    if (r < 0) return r;
    if (len == 0 || len > (size_t)TLS_MAX_KEY_PEM) {
        OPENSSL_cleanse(data, len);
        return fail_plain(TLS_FAIL_RANGE);
    }
    r = context_charge(c, (long long)len);
    if (r < 0) {
        OPENSSL_cleanse(data, len);
        return r;
    }
    ERR_clear_error();
    bio = BIO_new_mem_buf(data, (int)len);
    if (bio == NULL) {
        OPENSSL_cleanse(data, len);
        context_uncharge(c, (long long)len);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    key = PEM_read_bio_PrivateKey(bio, NULL, no_password_cb, NULL);
    BIO_free(bio);
    if (key == NULL) {
        OPENSSL_cleanse(data, len);
        context_uncharge(c, (long long)len);
        context_fail_text(c, "private key PEM did not parse (encrypted keys are unsupported)");
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_KEY_REJECTED);
    }
    if (!key_acceptable(key)) {
        EVP_PKEY_free(key);
        OPENSSL_cleanse(data, len);
        context_uncharge(c, (long long)len);
        context_fail_text(c, "private key is below security level 2");
        return fail_plain(TLS_FAIL_KEY_REJECTED);
    }
    ERR_clear_error();
    if (SSL_CTX_use_PrivateKey(c->ssl_ctx, key) != 1) {
        EVP_PKEY_free(key);
        OPENSSL_cleanse(data, len);
        context_uncharge(c, (long long)len);
        c->poisoned = 1;
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_KEY_REJECTED);
    }
    EVP_PKEY_free(key);
    OPENSSL_cleanse(data, len);
    ERR_clear_error();
    if (SSL_CTX_check_private_key(c->ssl_ctx) != 1) {
        context_fail_text(c, "private key does not match the loaded certificate");
        c->poisoned = 1;
        context_uncharge(c, (long long)len);
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_KEY_REJECTED);
    }
    c->key_loaded = 1;
    context_set_ready(c);
    ERR_clear_error();
    return 1;
}

long tls_context_set_trust(long context_token, long pem_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    X509 **certs = NULL;
    long count = 0;
    long r;
    long i;
    X509_STORE *store = NULL;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    r = buffer_span(pem_token, &data, &len);
    if (r < 0) return r;
    if (len == 0 || len > (size_t)TLS_MAX_TRUST_PEM) return fail_plain(TLS_FAIL_RANGE);
    ERR_clear_error();
    r = parse_pem_certs(data, len, TLS_MAX_TRUST_CERTS, &certs, &count);
    if (r < 0) {
        context_fail_text(c, "trust bundle PEM did not parse");
        return r;
    }
    store = X509_STORE_new();
    if (store == NULL) {
        for (i = 0; i < count; i++) X509_free(certs[i]);
        free(certs);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    for (i = 0; i < count; i++) {
        if (X509_STORE_add_cert(store, certs[i]) != 1) {
            unsigned long e = ERR_peek_last_error();
            if (ERR_GET_LIB(e) == ERR_LIB_X509 &&
                ERR_GET_REASON(e) == X509_R_CERT_ALREADY_IN_HASH_TABLE) {
                ERR_clear_error();
            } else {
                X509_STORE_free(store);
                for (long j = 0; j < count; j++) X509_free(certs[j]);
                free(certs);
                drain_errors(c->last_error, sizeof(c->last_error));
                context_fail_text(c, "trust bundle certificate could not be added");
                return fail_plain(TLS_FAIL_CERTIFICATE_REJECTED);
            }
        }
    }
    for (i = 0; i < count; i++) X509_free(certs[i]);
    free(certs);
    ERR_clear_error();
    r = context_recharge(c, c->trust_charge, (long long)len);
    if (r < 0) {
        X509_STORE_free(store);
        return r;
    }
    c->trust_charge = (long long)len;
    SSL_CTX_set_cert_store(c->ssl_ctx, store);
    c->trust_set = 1;
    context_set_ready(c);
    return 1;
}

long tls_context_use_system_trust(long context_token) {
    tls_context *c = context_of(context_token);
    X509_STORE *store;
    long check;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (c->role != TLS_ROLE_CLIENT) return fail_plain(TLS_FAIL_UNSUPPORTED);
    check = context_mutating_check(c);
    if (check < 0) return check;
    store = X509_STORE_new();
    if (store == NULL) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    if (X509_STORE_set_default_paths(store) != 1) {
        X509_STORE_free(store);
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_CONFIGURATION);
    }
    SSL_CTX_set_cert_store(c->ssl_ctx, store);
    c->trust_set = 1;
    context_set_ready(c);
    return 1;
}

long tls_context_set_alpn(long context_token, long packet_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    long long policy, count;
    long r;
    long i;
    size_t off;
    size_t wire = 0;
    unsigned char wire_buf[TLS_MAX_ALPN_BYTES];
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    r = buffer_span(packet_token, &data, &len);
    if (r < 0) return r;
    if (len < (size_t)TLS_ALPN_HEADER_BYTES) return fail_plain(TLS_FAIL_RANGE);
    if (buffer_read_i64(packet_token, TLS_ALPN_POLICY, &policy) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (buffer_read_i64(packet_token, TLS_ALPN_COUNT, &count) < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (policy < TLS_ALPN_DISABLED || policy > TLS_ALPN_REQUIRE) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (count < 0 || count > TLS_MAX_ALPN) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (policy != TLS_ALPN_DISABLED && count == 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (policy == TLS_ALPN_DISABLED && count != 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    off = (size_t)TLS_ALPN_HEADER_BYTES;
    for (i = 0; i < count; i++) {
        unsigned int plen;
        long j;
        if (off + 2 > len) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
        plen = ((unsigned int)data[off] << 8) | (unsigned int)data[off + 1];
        off += 2;
        if (plen == 0 || plen > 255 || off + plen > len) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
        for (j = 0; j < (long)plen; j++) {
            if (data[off + (size_t)j] == 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
        }
        if (wire + 1 + plen > TLS_MAX_ALPN_BYTES) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
        wire_buf[wire++] = (unsigned char)plen;
        memcpy(wire_buf + wire, data + off, plen);
        wire += plen;
        off += plen;
    }
    if (off != len) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    /* Duplicate detection over the length-prefixed wire list. */
    {
        size_t a = 0;
        long idx = 0;
        while (a < wire && idx < count) {
            size_t alen = wire_buf[a];
            size_t b = a + 1 + alen;
            size_t d = b;
            long jdx = idx + 1;
            if (a + 1 + alen > wire) return fail_plain(TLS_FAIL_INTERNAL);
            while (d < wire && jdx < count) {
                size_t blen = wire_buf[d];
                size_t e = d + 1 + blen;
                if (e > wire) return fail_plain(TLS_FAIL_INTERNAL);
                if (blen == alen && memcmp(wire_buf + a + 1, wire_buf + d + 1, alen) == 0) {
                    return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
                }
                d = e;
                jdx++;
            }
            a = b;
            idx++;
        }
    }
    memcpy(c->alpn_wire, wire_buf, wire);
    c->alpn_policy = (int)policy;
    c->alpn_count = (int)count;
    c->alpn_wire_len = (unsigned int)wire;
    if (policy == TLS_ALPN_DISABLED) {
        SSL_CTX_set_alpn_select_cb(c->ssl_ctx, NULL, NULL);
        SSL_CTX_set_client_hello_cb(c->ssl_ctx, NULL, NULL);
    } else if (policy == TLS_ALPN_PREFER) {
        SSL_CTX_set_alpn_select_cb(c->ssl_ctx, alpn_select_cb, c);
        SSL_CTX_set_client_hello_cb(c->ssl_ctx, NULL, NULL);
    } else {
        SSL_CTX_set_alpn_select_cb(c->ssl_ctx, alpn_select_cb, c);
        SSL_CTX_set_client_hello_cb(c->ssl_ctx, client_hello_cb, c);
    }
    ERR_clear_error();
    return 1;
}

long tls_context_set_version(long context_token, long which, long version) {
    tls_context *c = context_of(context_token);
    long r;
    int new_min;
    int new_max;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    if (which != 0 && which != 1) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (version < TLS_VERSION_12 || version > TLS_VERSION_13) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    new_min = which == 0 ? (int)version : c->min_version;
    new_max = which == 1 ? (int)version : c->max_version;
    if (new_min > new_max) return fail_plain(TLS_FAIL_CONFIGURATION);
    ERR_clear_error();
    if (SSL_CTX_set_min_proto_version(c->ssl_ctx, new_min == TLS_VERSION_12 ? TLS1_2_VERSION : TLS1_3_VERSION) != 1 ||
        SSL_CTX_set_max_proto_version(c->ssl_ctx, new_max == TLS_VERSION_12 ? TLS1_2_VERSION : TLS1_3_VERSION) != 1) {
        c->poisoned = 1;
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_CONFIGURATION);
    }
    c->min_version = new_min;
    c->max_version = new_max;
    return 1;
}

long tls_context_set_ciphers(long context_token, long which, long list_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    long r;
    char list[1025];
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    if (which != 0 && which != 1) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    r = buffer_span(list_token, &data, &len);
    if (r < 0) return r;
    r = validate_list_string(data, len, list, sizeof(list), which);
    if (r < 0) return r;
    ERR_clear_error();
    r = which == 0 ? SSL_CTX_set_ciphersuites(c->ssl_ctx, list)
                   : SSL_CTX_set_cipher_list(c->ssl_ctx, list);
    if (r != 1) {
        c->poisoned = 1;
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_CONFIGURATION);
    }
    if (enforce_security_level(c) < 0) return fail_plain(TLS_FAIL_CONFIGURATION);
    ERR_clear_error();
    return 1;
}

long tls_context_set_groups(long context_token, long list_token) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    long r;
    char list[1025];
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    context_clear_error(c);
    r = context_mutating_check(c);
    if (r < 0) return r;
    r = buffer_span(list_token, &data, &len);
    if (r < 0) return r;
    r = validate_list_string(data, len, list, sizeof(list), 2);
    if (r < 0) return r;
    ERR_clear_error();
    if (SSL_CTX_set1_groups_list(c->ssl_ctx, list) != 1) {
        c->poisoned = 1;
        drain_errors(c->last_error, sizeof(c->last_error));
        return fail_plain(TLS_FAIL_CONFIGURATION);
    }
    if (enforce_security_level(c) < 0) return fail_plain(TLS_FAIL_CONFIGURATION);
    ERR_clear_error();
    return 1;
}

long tls_context_ready(long context_token) {
    tls_context *c = context_of(context_token);
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    return c->ready ? 1 : 0;
}

long tls_context_role(long context_token) {
    tls_context *c = context_of(context_token);
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    return (long)c->role;
}

long tls_context_max_early_data(long context_token) {
    tls_context *c = context_of(context_token);
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    return (long)SSL_CTX_get_max_early_data(c->ssl_ctx);
}

long tls_context_ticket_count(long context_token) {
    tls_context *c = context_of(context_token);
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    return (long)SSL_CTX_get_num_tickets(c->ssl_ctx);
}

long tls_context_live_sessions(long context_token) {
    tls_context *c = context_of(context_token);
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    return c->live_sessions;
}

long tls_context_counts(long context_token, long out_token) {
    tls_context *c = context_of(context_token);
    long long v[TLS_CONTEXT_COUNT_FIELDS];
    long r;
    long i;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    v[0] = c->live_sessions;
    v[1] = c->max_sessions;
    v[2] = c->started;
    v[3] = c->completed;
    v[4] = c->failures;
    v[5] = c->bytes_charged;
    v[6] = c->max_context_bytes;
    v[7] = c->poisoned ? 1 : 0;
    for (i = 0; i < TLS_CONTEXT_COUNT_FIELDS; i++) {
        r = buffer_write_i64(out_token, i, v[i]);
        if (r < 0) return r;
    }
    return 1;
}

long tls_context_error_copy(long context_token, long out_token, long max) {
    tls_context *c = context_of(context_token);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    n = strlen(c->last_error);
    if (n > (size_t)max) n = (size_t)max;
    if (n > len) n = len;
    if (n != 0) memcpy(data, c->last_error, n);
    return (long)n;
}

long tls_session_new(long context_token) {
    tls_context *c = context_of(context_token);
    tls_session *s;
    long token;
    long long charge;
    if (c == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (c->poisoned) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (c->role != TLS_ROLE_SERVER) return fail_plain(TLS_FAIL_CONFIGURATION);
    if (!c->ready) return fail_plain(TLS_FAIL_NOT_CONFIGURED);
    if (c->live_sessions >= c->max_sessions) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    charge = (long long)sizeof(tls_session) + (long long)TLS_MAX_STAGE;
    if (charge > c->max_context_bytes - c->bytes_charged) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    s = (tls_session *)calloc(1, sizeof(*s));
    if (s == NULL) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    s->owner = c;
    s->ssl = SSL_new(c->ssl_ctx);
    if (s->ssl == NULL) {
        free(s);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    s->rbio = BIO_new(BIO_s_mem());
    s->wbio = BIO_new(BIO_s_mem());
    s->stage = (unsigned char *)malloc(TLS_MAX_STAGE);
    if (s->rbio == NULL || s->wbio == NULL || s->stage == NULL) {
        if (s->rbio != NULL) BIO_free(s->rbio);
        if (s->wbio != NULL) BIO_free(s->wbio);
        SSL_free(s->ssl);
        free(s->stage);
        free(s);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    BIO_set_mem_eof_return(s->rbio, -1);
    SSL_set_bio(s->ssl, s->rbio, s->wbio);
    SSL_set_accept_state(s->ssl);
    SSL_set_mode(s->ssl, SSL_MODE_ENABLE_PARTIAL_WRITE |
                          SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER |
                          SSL_MODE_RELEASE_BUFFERS);
    SSL_set_ex_data(s->ssl, g_ex_index, s);
    s->state = TLS_STATE_HANDSHAKING;
    s->verify_mode = c->verify;
    s->charge = charge;
    token = sys_reg_new_object(TLS_KIND_SESSION, s, session_release, (size_t)charge);
    if (token < 0) {
        SSL_free(s->ssl);
        free(s->stage);
        free(s);
        return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    }
    s->next = g_sessions;
    if (g_sessions != NULL) g_sessions->prev = s;
    g_sessions = s;
    c->live_sessions += 1;
    c->started += 1;
    c->bytes_charged += charge;
    g_live_sessions += 1;
    g_started += 1;
    g_bytes_charged += charge;
    if (g_bytes_charged > g_max_context_bytes) g_max_context_bytes = c->max_context_bytes;
    ERR_clear_error();
    return token;
}

long tls_session_handshake(long session_token) {
    tls_session *s = session_of(session_token);
    int ret;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    session_clear_error(s);
    if (s->state == TLS_STATE_FAILED || s->state == TLS_STATE_CLOSED) return fail_plain(TLS_FAIL_CLOSED);
    if (s->owner == NULL) return fail_plain(TLS_FAIL_CLOSED);
    if (s->state == TLS_STATE_ESTABLISHED || s->state == TLS_STATE_SEND_CLOSED ||
        s->state == TLS_STATE_RECV_CLOSED || s->state == TLS_STATE_BOTH_CLOSED) {
        return TLS_STEP_DONE;
    }
    if (s->transport_eof && BIO_ctrl_pending(s->rbio) == 0 && SSL_pending(s->ssl) == 0) {
        return session_fail(s, TLS_FAIL_TRUNCATED, 0, "transport EOF during the TLS handshake");
    }
    ERR_clear_error();
    ret = SSL_do_handshake(s->ssl);
    if (ret == 1) {
        if (s->state == TLS_STATE_HANDSHAKING) {
            s->state = TLS_STATE_ESTABLISHED;
            if (s->owner != NULL) s->owner->completed += 1;
            g_completed += 1;
            if (s->peer_cert == NULL) s->peer_cert = SSL_get1_peer_certificate(s->ssl);
        }
        r = session_charge_update(s);
        if (r < 0) return r;
        return TLS_STEP_DONE;
    }
    r = map_ssl_error(s, ret, "handshake");
    if (r < 0) return r;
    if (r == TLS_STEP_WANT_READ && s->transport_eof && BIO_ctrl_pending(s->rbio) == 0) {
        return session_fail(s, TLS_FAIL_TRUNCATED, 0, "transport EOF during the TLS handshake");
    }
    if (r == TLS_STEP_WANT_READ && session_output_bytes(s) > 0) r = TLS_STEP_WANT_WRITE;
    if (r == 0) return session_fail(s, TLS_FAIL_HANDSHAKE_FAILED, 0, "handshake closed unexpectedly");
    if (r == TLS_STEP_WANT_READ || r == TLS_STEP_WANT_WRITE) {
        long q = session_charge_update(s);
        if (q < 0) return q;
    }
    return r;
}

long tls_session_read(long session_token, long out_token, long max) {
    tls_session *s = session_of(session_token);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n = 0;
    int ret;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    session_clear_error(s);
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if (max > TLS_MAX_TRANSFER) max = TLS_MAX_TRANSFER;
    if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) return fail_plain(TLS_FAIL_NOT_READY);
    if (s->state == TLS_STATE_FAILED || s->state == TLS_STATE_CLOSED) return fail_plain(TLS_FAIL_CLOSED);
    if (s->state == TLS_STATE_RECV_CLOSED || s->state == TLS_STATE_BOTH_CLOSED) return 0;
    if (s->transport_eof && BIO_ctrl_pending(s->rbio) == 0 && SSL_pending(s->ssl) == 0) {
        if ((SSL_get_shutdown(s->ssl) & SSL_RECEIVED_SHUTDOWN) != 0) return 0;
        return session_fail(s, TLS_FAIL_TRUNCATED, 0, "transport EOF without close_notify");
    }
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)max > len) max = (long)len;
    if (max == 0) return 0;
    ERR_clear_error();
    ret = SSL_read_ex(s->ssl, data, (size_t)max, &n);
    if (ret == 1) {
        if (n == 0) return 0;
        r = session_charge_update(s);
        if (r < 0) return r;
        return (long)n;
    }
    r = map_ssl_error(s, ret, "read");
    if (r == 0) {
        s->state = (s->state == TLS_STATE_SEND_CLOSED) ? TLS_STATE_BOTH_CLOSED : TLS_STATE_RECV_CLOSED;
        return 0;
    }
    if (r < 0) return r;
    if (r == TLS_STEP_WANT_READ && session_output_bytes(s) > 0) r = TLS_STEP_WANT_WRITE;
    if (r == TLS_STEP_WANT_READ || r == TLS_STEP_WANT_WRITE) {
        long q = session_charge_update(s);
        if (q < 0) return q;
        return r == TLS_STEP_WANT_READ ? TLS_IO_WANT_READ : TLS_IO_WANT_WRITE;
    }
    return r;
}

long tls_session_write(long session_token, long in_token, long offset, long count) {
    tls_session *s = session_of(session_token);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n = 0;
    int ret;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    session_clear_error(s);
    if (offset < 0 || count < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (count > TLS_MAX_TRANSFER) return fail_plain(TLS_FAIL_RANGE);
    if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) return fail_plain(TLS_FAIL_NOT_READY);
    if (s->state == TLS_STATE_FAILED || s->state == TLS_STATE_CLOSED ||
        s->state == TLS_STATE_SEND_CLOSED || s->state == TLS_STATE_BOTH_CLOSED) {
        return fail_plain(TLS_FAIL_CLOSED);
    }
    if (count == 0) return 0;
    r = buffer_span(in_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)offset > len || (size_t)count > len - (size_t)offset) return fail_plain(TLS_FAIL_RANGE);
    ERR_clear_error();
    ret = SSL_write_ex(s->ssl, data + offset, (size_t)count, &n);
    if (ret == 1) {
        if (n == 0) return session_fail(s, TLS_FAIL_INTERNAL, 0, "SSL_write_ex accepted no bytes");
        r = session_charge_update(s);
        if (r < 0) return r;
        return (long)n;
    }
    r = map_ssl_error(s, ret, "write");
    if (r == 0) return session_fail(s, TLS_FAIL_TRANSPORT, 10004L, "peer closed the TLS stream");
    if (r < 0) return r;
    if (r == TLS_STEP_WANT_READ && session_output_bytes(s) > 0) r = TLS_STEP_WANT_WRITE;
    if (r == TLS_STEP_WANT_READ || r == TLS_STEP_WANT_WRITE) {
        long q = session_charge_update(s);
        if (q < 0) return q;
        return r == TLS_STEP_WANT_READ ? TLS_IO_WANT_READ : TLS_IO_WANT_WRITE;
    }
    return r;
}

long tls_session_drain(long session_token, long out_token, long max) {
    tls_session *s = session_of(session_token);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t want;
    size_t n;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if (max > TLS_MAX_TRANSFER) max = TLS_MAX_TRANSFER;
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)max > len) max = (long)len;
    if (max == 0) return 0;
    if (s->stage_len == s->stage_off) {
        long pending = (long)BIO_ctrl_pending(s->wbio);
        size_t take;
        if (pending <= 0) return 0;
        if ((size_t)pending > TLS_MAX_STAGE) return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "TLS write BIO exceeded the staging cap");
        take = (size_t)pending;
        if (take > TLS_MAX_STAGE) take = TLS_MAX_STAGE;
        if (BIO_read(s->wbio, s->stage, take) != (int)take) {
            return session_fail(s, TLS_FAIL_INTERNAL, 0, "short read from the TLS write BIO");
        }
        s->stage_len = take;
        s->stage_off = 0;
    }
    want = s->stage_len - s->stage_off;
    if (want == 0) return 0;
    n = want;
    if (n > (size_t)max) n = (size_t)max;
    memcpy(data, s->stage + s->stage_off, n);
    return (long)n;
}

long tls_session_commit(long session_token, long written) {
    tls_session *s = session_of(session_token);
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (written < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if ((size_t)written > s->stage_len - s->stage_off) return fail_plain(TLS_FAIL_INTERNAL);
    s->stage_off += (size_t)written;
    if (s->stage_off == s->stage_len) {
        s->stage_off = 0;
        s->stage_len = 0;
    }
    {
        long r = session_charge_update(s);
        if (r < 0) return r;
    }
    return 1;
}

long tls_session_feed(long session_token, long in_token, long offset, long count) {
    tls_session *s = session_of(session_token);
    unsigned char *data = NULL;
    size_t len = 0;
    long pending;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (offset < 0 || count < 0) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    if (s->state == TLS_STATE_FAILED || s->state == TLS_STATE_CLOSED) return fail_plain(TLS_FAIL_CLOSED);
    if (s->owner == NULL) return fail_plain(TLS_FAIL_CLOSED);
    if (count == 0) return 0;
    r = buffer_span(in_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)offset > len || (size_t)count > len - (size_t)offset) return fail_plain(TLS_FAIL_RANGE);
    pending = (long)BIO_ctrl_pending(s->rbio);
    if (pending < 0) pending = 0;
    if ((size_t)pending + (size_t)count > TLS_MAX_BIO) {
        return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "TLS read BIO pending bytes exceed the cap");
    }
    if (s->state == TLS_STATE_HANDSHAKING) {
        if ((size_t)count > (size_t)s->owner->max_handshake_bytes - s->handshake_bytes) {
            return session_fail(s, TLS_FAIL_RESOURCE_LIMIT, 0, "handshake ciphertext exceeded the configured cap");
        }
        s->handshake_bytes += (size_t)count;
    }
    if (BIO_write(s->rbio, data + offset, (int)count) != (int)count) {
        return session_fail(s, TLS_FAIL_INTERNAL, 0, "could not feed the TLS read BIO");
    }
    r = session_charge_update(s);
    if (r < 0) return r;
    return count;
}

long tls_session_eof(long session_token) {
    tls_session *s = session_of(session_token);
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    s->transport_eof = 1;
    return 1;
}

long tls_session_fail(long session_token) {
    tls_session *s = session_of(session_token);
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (s->state != TLS_STATE_FAILED) {
        s->state = TLS_STATE_FAILED;
        if (s->owner != NULL) s->owner->failures += 1;
        g_failures += 1;
    }
    return 1;
}

long tls_session_output_pending(long session_token) {
    tls_session *s = session_of(session_token);
    long pending;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    pending = session_output_bytes(s);
    if (pending < 0) pending = 0x7fffffffL;
    return pending;
}

long tls_session_ciphertext_pending(long session_token) {
    tls_session *s = session_of(session_token);
    long rbio;
    long wbio;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    rbio = (long)BIO_ctrl_pending(s->rbio);
    wbio = (long)BIO_ctrl_pending(s->wbio);
    if (rbio < 0) rbio = 0;
    if (wbio < 0) wbio = 0;
    return rbio + wbio + (long)(s->stage_len - s->stage_off);
}

long tls_session_plaintext_pending(long session_token) {
    tls_session *s = session_of(session_token);
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) return 0;
    return (long)SSL_pending(s->ssl);
}

long tls_session_shutdown(long session_token) {
    tls_session *s = session_of(session_token);
    int ret;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    session_clear_error(s);
    if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) return fail_plain(TLS_FAIL_NOT_READY);
    if (s->state == TLS_STATE_FAILED || s->state == TLS_STATE_CLOSED) return fail_plain(TLS_FAIL_CLOSED);
    ERR_clear_error();
    ret = SSL_shutdown(s->ssl);
    if (ret == 1) {
        s->state = TLS_STATE_BOTH_CLOSED;
        (void)session_charge_update(s);
        return TLS_STEP_DONE;
    }
    if (ret == 0) {
        s->state = (s->state == TLS_STATE_RECV_CLOSED) ? TLS_STATE_BOTH_CLOSED : TLS_STATE_SEND_CLOSED;
        (void)session_charge_update(s);
        return TLS_SHUTDOWN_SENT;
    }
    r = map_ssl_error(s, ret, "shutdown");
    if (r == 0) {
        s->state = (s->state == TLS_STATE_SEND_CLOSED) ? TLS_STATE_BOTH_CLOSED : TLS_STATE_RECV_CLOSED;
        return TLS_SHUTDOWN_PEER_CLOSED;
    }
    if (r < 0) return r;
    if (r == TLS_STEP_WANT_READ && session_output_bytes(s) > 0) return TLS_SHUTDOWN_WANT_WRITE;
    if (r == TLS_STEP_WANT_READ) return TLS_SHUTDOWN_WANT_READ;
    if (r == TLS_STEP_WANT_WRITE) return TLS_SHUTDOWN_WANT_WRITE;
    return r;
}

static long copy_name(tls_session *s, X509_NAME *name, long out_token, long max) {
    BIO *bio;
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    if (name == NULL) return 0;
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if ((size_t)max > len) max = (long)len;
    if (max == 0) return 0;
    bio = BIO_new(BIO_s_mem());
    if (bio == NULL) return fail_plain(TLS_FAIL_RESOURCE_LIMIT);
    if (X509_NAME_print_ex(bio, name, 0, XN_FLAG_RFC2253) < 0) {
        BIO_free(bio);
        return fail_plain(TLS_FAIL_INTERNAL);
    }
    n = (size_t)BIO_ctrl_pending(bio);
    if (n > (size_t)max) n = (size_t)max;
    if (n > 0 && BIO_read(bio, data, (int)n) != (int)n) {
        BIO_free(bio);
        return fail_plain(TLS_FAIL_INTERNAL);
    }
    BIO_free(bio);
    (void)s;
    return (long)n;
}

static long copy_string(tls_session *s, const char *text, long out_token, long max) {
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    (void)s;
    if (text == NULL) return 0;
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if ((size_t)max > len) max = (long)len;
    n = strlen(text);
    if (n > (size_t)max) n = (size_t)max;
    if (n != 0) memcpy(data, text, n);
    return (long)n;
}

static long copy_bytes(tls_session *s, const unsigned char *bytes, size_t count,
                       long out_token, long max) {
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    (void)s;
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if ((size_t)max > len) max = (long)len;
    n = count;
    if (n > (size_t)max) n = (size_t)max;
    if (n != 0) memcpy(data, bytes, n);
    return (long)n;
}

long tls_session_info_scalar(long session_token, long field) {
    tls_session *s = session_of(session_token);
    long vr;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (field == TLS_INFO_PEER_PRESENT) return s->peer_cert != NULL ? 1 : 0;
    if (field == TLS_INFO_VERIFY_CODE) return (long)SSL_get_verify_result(s->ssl);
    if (field == TLS_INFO_VERIFICATION) {
        if (s->verify_mode == TLS_VERIFY_NONE) return 0;
        if (s->peer_cert == NULL) return 0;
        vr = (long)SSL_get_verify_result(s->ssl);
        return vr == X509_V_OK ? 1 : 2;
    }
    if (field == TLS_INFO_STATE) return (long)s->state;
    if (field == TLS_INFO_SECURE) {
        const SSL_CIPHER *cipher;
        const char *name;
        if (s->state != TLS_STATE_ESTABLISHED && s->state != TLS_STATE_SEND_CLOSED &&
            s->state != TLS_STATE_RECV_CLOSED) return 0;
        if (SSL_is_init_finished(s->ssl) != 1) return 0;
        cipher = SSL_get_current_cipher(s->ssl);
        if (cipher == NULL) return 0;
        name = SSL_CIPHER_get_name(cipher);
        if (name == NULL || strstr(name, "NULL") != NULL) return 0;
        return 1;
    }
    return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
}

long tls_session_info_copy(long session_token, long field, long out_token, long max) {
    tls_session *s = session_of(session_token);
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (s->state == TLS_STATE_NEW || s->state == TLS_STATE_HANDSHAKING) return fail_plain(TLS_FAIL_NOT_READY);
    if (s->state == TLS_STATE_FAILED) return fail_plain(TLS_FAIL_CLOSED);
    if (field == TLS_INFO_VERSION) return copy_string(s, SSL_get_version(s->ssl), out_token, max);
    if (field == TLS_INFO_CIPHER) {
        const SSL_CIPHER *cipher = SSL_get_current_cipher(s->ssl);
        return copy_string(s, cipher != NULL ? SSL_CIPHER_get_name(cipher) : "", out_token, max);
    }
    if (field == TLS_INFO_ALPN) {
        const unsigned char *selected = NULL;
        unsigned int selected_len = 0;
        SSL_get0_alpn_selected(s->ssl, &selected, &selected_len);
        if (selected_len == 0) return 0;
        return copy_bytes(s, selected, selected_len, out_token, max);
    }
    if (field == TLS_INFO_SNI) {
        if (s->sni[0] == '\0') return 0;
        return copy_string(s, s->sni, out_token, max);
    }
    if (field == TLS_INFO_SUBJECT || field == TLS_INFO_ISSUER) {
        X509_NAME *name = NULL;
        if (s->peer_cert != NULL) {
            name = field == TLS_INFO_SUBJECT ? X509_get_subject_name(s->peer_cert)
                                             : X509_get_issuer_name(s->peer_cert);
        }
        return copy_name(s, name, out_token, max);
    }
    if (field == TLS_INFO_SHA256) {
        unsigned char md[EVP_MAX_MD_SIZE];
        unsigned int mdlen = 0;
        if (s->peer_cert == NULL) return 0;
        if (X509_digest(s->peer_cert, EVP_sha256(), md, &mdlen) != 1) {
            return fail_plain(TLS_FAIL_INTERNAL);
        }
        return copy_bytes(s, md, mdlen, out_token, max);
    }
    if (field == TLS_INFO_NOT_BEFORE || field == TLS_INFO_NOT_AFTER) {
        unsigned char *data = NULL;
        size_t len = 0;
        struct tm tm;
        time_t when;
        long r;
        uint64_t sec;
        int i;
        const ASN1_TIME *at = NULL;
        if (s->peer_cert == NULL) return 0;
        at = field == TLS_INFO_NOT_BEFORE ? X509_get0_notBefore(s->peer_cert)
                                          : X509_get0_notAfter(s->peer_cert);
        if (at == NULL) return fail_plain(TLS_FAIL_INTERNAL);
        memset(&tm, 0, sizeof(tm));
        if (ASN1_TIME_to_tm(at, &tm) != 1) return fail_plain(TLS_FAIL_INTERNAL);
        when = timegm(&tm);
        if (when < 0) return fail_plain(TLS_FAIL_INTERNAL);
        r = buffer_span(out_token, &data, &len);
        if (r < 0) return r;
        if (len < 16) return fail_plain(TLS_FAIL_RANGE);
        sec = (uint64_t)when;
        for (i = 0; i < 8; i++) data[i] = (unsigned char)((sec >> (8 * i)) & 0xffu);
        for (i = 0; i < 8; i++) data[8 + i] = 0;
        return 16;
    }
    return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
}

long tls_session_peer_count(long session_token, long which) {
    tls_session *s = session_of(session_token);
    GENERAL_NAMES *names;
    long count = 0;
    int i;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (s->peer_cert == NULL) return 0;
    if (which != TLS_PEER_DNS && which != TLS_PEER_IP) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    names = (GENERAL_NAMES *)X509_get_ext_d2i(s->peer_cert, NID_subject_alt_name, NULL, NULL);
    if (names == NULL) return 0;
    for (i = 0; i < sk_GENERAL_NAME_num(names) && count < 64; i++) {
        const GENERAL_NAME *gn = sk_GENERAL_NAME_value(names, i);
        if (which == TLS_PEER_DNS && gn->type == GEN_DNS) count++;
        if (which == TLS_PEER_IP && gn->type == GEN_IPADD) count++;
    }
    GENERAL_NAMES_free(names);
    return count;
}

long tls_session_peer_item(long session_token, long which, long index,
                           long out_token, long max) {
    tls_session *s = session_of(session_token);
    GENERAL_NAMES *names;
    long seen = 0;
    int i;
    long result = 0;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    if (s->peer_cert == NULL) return 0;
    if (index < 0 || index > 63) return fail_plain(TLS_FAIL_RANGE);
    if (which != TLS_PEER_DNS && which != TLS_PEER_IP) return fail_plain(TLS_FAIL_INVALID_ARGUMENT);
    names = (GENERAL_NAMES *)X509_get_ext_d2i(s->peer_cert, NID_subject_alt_name, NULL, NULL);
    if (names == NULL) return 0;
    for (i = 0; i < sk_GENERAL_NAME_num(names); i++) {
        const GENERAL_NAME *gn = sk_GENERAL_NAME_value(names, i);
        int match = (which == TLS_PEER_DNS && gn->type == GEN_DNS) ||
                    (which == TLS_PEER_IP && gn->type == GEN_IPADD);
        if (!match) continue;
        if (seen == index) {
            if (which == TLS_PEER_DNS) {
                const unsigned char *p = ASN1_STRING_get0_data(gn->d.dNSName);
                int l = ASN1_STRING_length(gn->d.dNSName);
                if (p != NULL && l > 0) result = copy_bytes(s, p, (size_t)l, out_token, max);
            } else {
                char text[INET6_ADDRSTRLEN];
                const unsigned char *p = ASN1_STRING_get0_data(gn->d.iPAddress);
                int l = ASN1_STRING_length(gn->d.iPAddress);
                if (p != NULL && (l == 4 || l == 16) &&
                    inet_ntop(l == 4 ? AF_INET : AF_INET6, p, text, sizeof(text)) != NULL) {
                    result = copy_string(s, text, out_token, max);
                }
            }
            GENERAL_NAMES_free(names);
            return result;
        }
        seen++;
    }
    GENERAL_NAMES_free(names);
    return 0;
}

long tls_session_error_copy(long session_token, long out_token, long max) {
    tls_session *s = session_of(session_token);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    if (s == NULL) return fail_plain(TLS_FAIL_INVALID_RESOURCE);
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    n = strlen(s->last_error);
    if (n > (size_t)max) n = (size_t)max;
    if (n > len) n = len;
    if (n != 0) memcpy(data, s->last_error, n);
    return (long)n;
}

long tls_verify_text(long code, long out_token, long max) {
    const char *text = X509_verify_cert_error_string((long)code);
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    r = buffer_span(out_token, &data, &len);
    if (r < 0) return r;
    if (max < 0) return fail_plain(TLS_FAIL_RANGE);
    if ((size_t)max > len) max = (long)len;
    n = text != NULL ? strlen(text) : 0;
    if (n > (size_t)max) n = (size_t)max;
    if (n != 0) memcpy(data, text, n);
    return (long)n;
}

long tls_counts(long out_token) {
    long long v[TLS_COUNT_FIELDS];
    long r;
    long i;
    long cipher = 0;
    long plain = 0;
    tls_session *s;
    for (s = g_sessions; s != NULL; s = s->next) {
        long c = (long)BIO_ctrl_pending(s->rbio);
        long w = (long)BIO_ctrl_pending(s->wbio);
        if (c < 0) c = 0;
        if (w < 0) w = 0;
        cipher += c + w + (long)(s->stage_len - s->stage_off);
        if (s->state != TLS_STATE_NEW && s->state != TLS_STATE_HANDSHAKING) {
            plain += (long)SSL_pending(s->ssl);
        }
    }
    v[0] = g_live_contexts;
    v[1] = g_live_sessions;
    v[2] = g_started;
    v[3] = g_completed;
    v[4] = g_failures;
    v[5] = g_verify_failures;
    v[6] = g_alpn_rejections;
    v[7] = g_truncations;
    v[8] = cipher;
    v[9] = plain;
    v[10] = g_bytes_charged;
    v[11] = g_max_context_bytes;
    for (i = 0; i < TLS_COUNT_FIELDS; i++) {
        r = buffer_write_i64(out_token, i, v[i]);
        if (r < 0) return r;
    }
    return 1;
}
