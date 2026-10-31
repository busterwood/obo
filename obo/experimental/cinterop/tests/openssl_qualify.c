#define _GNU_SOURCE
#include <openssl/ssl.h>
#include <openssl/err.h>
#include <openssl/rand.h>
#include <openssl/provider.h>
#include <openssl/pem.h>
#include <openssl/x509.h>
#include <openssl/bio.h>
#include <openssl/evp.h>
#include <openssl/opensslv.h>
#include <pthread.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>

#include "openssl_qualify.h"

static int ctor_ran;
__attribute__((constructor)) static void qualify_ctor(void) {
    ctor_ran = 1;
    fprintf(stderr, "openssl-ctor\n");
}

static void qualify_libc_atexit(void) {
    fprintf(stderr, "openssl-atexit\n");
}

static void qualify_openssl_atexit(void) {
    fprintf(stderr, "openssl-cleanup-atexit\n");
}

static int fail(const char *what, unsigned long err) {
    fprintf(stderr, "openssl-fail: %s err=0x%lx\n", what, err);
    return 0;
}

static int alpn_select(SSL *ssl, const unsigned char **out, unsigned char *outlen,
                       const unsigned char *in, unsigned int inlen, void *arg) {
    (void)ssl;
    (void)arg;
    if (SSL_select_next_proto((unsigned char **)out, outlen,
                              (const unsigned char *)"\x08http/1.1", 9,
                              in, inlen) == OPENSSL_NPN_NEGOTIATED)
        return SSL_TLSEXT_ERR_OK;
    return SSL_TLSEXT_ERR_ALERT_FATAL;
}

static X509 *make_cert(EVP_PKEY *key) {
    X509 *x = X509_new();
    X509_NAME *name;
    if (x == NULL)
        return NULL;
    if (!X509_set_version(x, 2)
            || !ASN1_INTEGER_set(X509_get_serialNumber(x), 1)
            || X509_gmtime_adj(X509_getm_notBefore(x), 0) == NULL
            || X509_gmtime_adj(X509_getm_notAfter(x), 3600) == NULL
            || !X509_set_pubkey(x, key)) {
        X509_free(x);
        return NULL;
    }
    name = X509_get_subject_name(x);
    if (name == NULL
            || !X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC,
                                           (const unsigned char *)"localhost",
                                           -1, -1, 0)
            || !X509_set_issuer_name(x, name)
            || !X509_sign(x, key, EVP_sha256())) {
        X509_free(x);
        return NULL;
    }
    return x;
}

static int handshake(SSL *server, SSL *client) {
    int i;
    for (i = 0; i < 256; i++) {
        int sr = SSL_do_handshake(server);
        int cr = SSL_do_handshake(client);
        if (sr == 1 && cr == 1)
            return 1;
        if (sr < 1) {
            int e = SSL_get_error(server, sr);
            if (e != SSL_ERROR_WANT_READ && e != SSL_ERROR_WANT_WRITE)
                return fail("server handshake", ERR_get_error());
        }
        if (cr < 1) {
            int e = SSL_get_error(client, cr);
            if (e != SSL_ERROR_WANT_READ && e != SSL_ERROR_WANT_WRITE)
                return fail("client handshake", ERR_get_error());
        }
    }
    return fail("handshake did not finish", 0);
}

static int once_ran;

static void once_fn(void) {
    once_ran = 1;
}

struct thread_probe {
    int err;
    unsigned long queue;
};

static void *thread_probe_fn(void *vp) {
    struct thread_probe *p = vp;
    errno = 4321;
    ERR_clear_error();
    p->queue = ERR_peek_error();
    p->err = errno;
    return NULL;
}

static int qualify_once(void) {
    OSSL_PROVIDER *provider = NULL;
    EVP_PKEY *key = NULL, *loaded_key = NULL;
    X509 *cert = NULL, *loaded_cert = NULL;
    BIO *cert_mem = NULL, *key_mem = NULL;
    BIO *server_bio = NULL, *client_bio = NULL;
    SSL_CTX *server_ctx = NULL, *client_ctx = NULL;
    SSL *server = NULL, *client = NULL;
    pthread_once_t once = PTHREAD_ONCE_INIT;
    struct thread_probe probe = {0, 0};
    pthread_t thread;
    unsigned char random_bytes[32];
    unsigned char protos[] = "\x08http/1.1";
    const unsigned char *selected = NULL;
    unsigned int selected_len = 0;
    struct timespec ts;
    void *mapped = NULL;
    char buf[8];
    int i, all_zero = 1, rc = 0;

    if (!ctor_ran)
        return fail("constructor did not run", 0);

    if (atexit(qualify_libc_atexit) != 0)
        return fail("libc atexit registration", 0);
    if (!OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL))
        return fail("OPENSSL_init_ssl", ERR_get_error());
    if (!OPENSSL_atexit(qualify_openssl_atexit))
        return fail("OPENSSL_atexit", ERR_get_error());

    if (OpenSSL_version_num() < 0x30000000UL)
        return fail("OpenSSL_version_num too old", OpenSSL_version_num());
    if (!OSSL_PROVIDER_available(NULL, "default"))
        return fail("default provider unavailable", ERR_get_error());
    provider = OSSL_PROVIDER_load(NULL, "default");
    if (provider == NULL)
        return fail("OSSL_PROVIDER_load", ERR_get_error());

    if (RAND_bytes(random_bytes, sizeof(random_bytes)) != 1)
        return fail("RAND_bytes", ERR_get_error());
    for (i = 0; i < (int)sizeof(random_bytes); i++) {
        if (random_bytes[i] != 0) {
            all_zero = 0;
            break;
        }
    }
    if (all_zero)
        return fail("RAND_bytes returned all zeroes", 0);

    key = EVP_PKEY_Q_keygen(NULL, NULL, "RSA", (size_t)2048);
    if (key == NULL)
        return fail("EVP_PKEY_Q_keygen", ERR_get_error());
    cert = make_cert(key);
    if (cert == NULL)
        return fail("make_cert", ERR_get_error());

    cert_mem = BIO_new(BIO_s_mem());
    key_mem = BIO_new(BIO_s_mem());
    if (cert_mem == NULL || key_mem == NULL)
        return fail("BIO_new(BIO_s_mem)", ERR_get_error());
    if (PEM_write_bio_X509(cert_mem, cert) != 1)
        return fail("PEM_write_bio_X509", ERR_get_error());
    if (PEM_write_bio_PrivateKey(key_mem, key, NULL, NULL, 0, NULL, NULL) != 1)
        return fail("PEM_write_bio_PrivateKey", ERR_get_error());
    loaded_cert = PEM_read_bio_X509(cert_mem, NULL, NULL, NULL);
    if (loaded_cert == NULL)
        return fail("PEM_read_bio_X509", ERR_get_error());
    loaded_key = PEM_read_bio_PrivateKey(key_mem, NULL, NULL, NULL);
    if (loaded_key == NULL)
        return fail("PEM_read_bio_PrivateKey", ERR_get_error());

    server_ctx = SSL_CTX_new(TLS_server_method());
    client_ctx = SSL_CTX_new(TLS_client_method());
    if (server_ctx == NULL || client_ctx == NULL)
        return fail("SSL_CTX_new", ERR_get_error());
    if (!SSL_CTX_set_min_proto_version(server_ctx, TLS1_2_VERSION)
            || !SSL_CTX_set_max_proto_version(server_ctx, TLS1_3_VERSION)
            || SSL_CTX_get_min_proto_version(server_ctx) != TLS1_2_VERSION)
        return fail("server min/max version", ERR_get_error());
    if (!SSL_CTX_set_min_proto_version(client_ctx, TLS1_3_VERSION)
            || !SSL_CTX_set_max_proto_version(client_ctx, TLS1_3_VERSION))
        return fail("client TLS 1.3 pin", ERR_get_error());
    SSL_CTX_set_alpn_select_cb(server_ctx, alpn_select, NULL);
    if (SSL_CTX_set_alpn_protos(client_ctx, protos, sizeof(protos) - 1) != 0)
        return fail("SSL_CTX_set_alpn_protos", ERR_get_error());
    if (SSL_CTX_use_certificate(server_ctx, loaded_cert) != 1)
        return fail("SSL_CTX_use_certificate", ERR_get_error());
    if (SSL_CTX_use_PrivateKey(server_ctx, loaded_key) != 1)
        return fail("SSL_CTX_use_PrivateKey", ERR_get_error());
    if (SSL_CTX_check_private_key(server_ctx) != 1)
        return fail("SSL_CTX_check_private_key", ERR_get_error());
    SSL_CTX_set_verify(client_ctx, SSL_VERIFY_NONE, NULL);

    if (BIO_new_bio_pair(&server_bio, 0, &client_bio, 0) != 1)
        return fail("BIO_new_bio_pair", ERR_get_error());
    server = SSL_new(server_ctx);
    client = SSL_new(client_ctx);
    if (server == NULL || client == NULL)
        return fail("SSL_new", ERR_get_error());
    SSL_set_bio(server, server_bio, server_bio);
    SSL_set_bio(client, client_bio, client_bio);
    SSL_set_accept_state(server);
    SSL_set_connect_state(client);

    if (!handshake(server, client))
        return 0;
    if (!SSL_is_init_finished(server) || !SSL_is_init_finished(client))
        return fail("handshake finished flags", 0);
    if (SSL_version(client) != TLS1_3_VERSION)
        return fail("negotiated protocol is not TLS 1.3", (unsigned long)SSL_version(client));
    SSL_get0_alpn_selected(server, &selected, &selected_len);
    if (selected_len != 8 || memcmp(selected, "http/1.1", 8) != 0)
        return fail("ALPN did not select http/1.1", 0);

    if (SSL_write(client, "ping", 4) != 4)
        return fail("SSL_write", ERR_get_error());
    if (SSL_read(server, buf, sizeof(buf)) != 4 || memcmp(buf, "ping", 4) != 0)
        return fail("SSL_read", ERR_get_error());

    for (i = 0; i < 64; i++) {
        int sr = SSL_shutdown(server);
        int cr = SSL_shutdown(client);
        if (sr >= 0 && cr >= 0)
            break;
        if (sr < 0 && SSL_get_error(server, sr) != SSL_ERROR_WANT_READ
                && SSL_get_error(server, sr) != SSL_ERROR_WANT_WRITE)
            return fail("server shutdown", ERR_get_error());
        if (cr < 0 && SSL_get_error(client, cr) != SSL_ERROR_WANT_READ
                && SSL_get_error(client, cr) != SSL_ERROR_WANT_WRITE)
            return fail("client shutdown", ERR_get_error());
    }

    if (pthread_once(&once, once_fn) != 0 || !once_ran)
        return fail("pthread_once", 0);
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return fail("clock_gettime", 0);
    mapped = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped == MAP_FAILED)
        return fail("mmap", 0);
    memset(mapped, 0x5a, 4096);
    if (((unsigned char *)mapped)[0] != 0x5a)
        return fail("mmap readback", 0);
    if (munmap(mapped, 4096) != 0)
        return fail("munmap", 0);
    mapped = NULL;

    errno = 1234;
    ERR_clear_error();
    ERR_raise(ERR_LIB_USER, 88);
    if (pthread_create(&thread, NULL, thread_probe_fn, &probe) != 0)
        return fail("pthread_create", 0);
    if (pthread_join(thread, NULL) != 0)
        return fail("pthread_join", 0);
    if (errno != 1234)
        return fail("thread-local errno was overwritten", (unsigned long)errno);
    if (probe.err != 4321 || probe.queue != 0)
        return fail("thread-local OpenSSL error queue leaked", probe.queue);
    if (ERR_GET_LIB(ERR_peek_error()) != ERR_LIB_USER
            || ERR_GET_REASON(ERR_peek_error()) != 88)
        return fail("main OpenSSL error queue was cleared", ERR_peek_error());
    ERR_clear_error();

    printf("openssl-qualify=ok\n");
    printf("openssl-version=%s\n", OpenSSL_version(OPENSSL_VERSION));
    printf("openssl-version-num=0x%lx\n", (unsigned long)OpenSSL_version_num());
    printf("openssl-tls=1.3 alpn=http/1.1\n");
    rc = 1;

    SSL_free(server);
    server = NULL;
    SSL_free(client);
    client = NULL;
    SSL_CTX_free(server_ctx);
    server_ctx = NULL;
    SSL_CTX_free(client_ctx);
    client_ctx = NULL;
    X509_free(loaded_cert);
    loaded_cert = NULL;
    EVP_PKEY_free(loaded_key);
    loaded_key = NULL;
    X509_free(cert);
    cert = NULL;
    EVP_PKEY_free(key);
    key = NULL;
    BIO_free(cert_mem);
    cert_mem = NULL;
    BIO_free(key_mem);
    key_mem = NULL;
    OSSL_PROVIDER_unload(provider);
    provider = NULL;

    OPENSSL_cleanup();
    printf("openssl-post-cleanup=ok\n");
    if (OpenSSL_version_num() < 0x30000000UL)
        return fail("post-cleanup version check", 0);
    return rc;
}

long obo_openssl_qualify(long mode) {
    (void)mode;
    return qualify_once();
}

long obo_openssl_version_num(void) {
    return (long)OpenSSL_version_num();
}
