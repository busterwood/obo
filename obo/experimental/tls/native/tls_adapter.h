/* Experimental server-side TLS adapter (P2T-b).
 *
 * Scalar-only boundary: every exported symbol takes `long` arguments and
 * returns `long`. OpenSSL's SSL*, SSL_CTX*, BIO*, X509* and EVP_PKEY* objects
 * stay inside this C unit. Obo receives only registry tokens (via
 * sysregistry.c), bounded byte-buffer tokens and copied diagnostics.
 *
 * This is trusted C support code for an experimental systems adapter. It is
 * not a stable ABI, not a public header and not a TLS implementation claim.
 */
#ifndef OBO_TLS_ADAPTER_H
#define OBO_TLS_ADAPTER_H

#include <stddef.h>

/* Must match systls::KIND_TLS_CONTEXT / KIND_TLS_SESSION and the systems
 * platform resource kinds. They are ordinary registry kinds; sysregistry.c is
 * generic and does not need a TLS-specific branch. */
#define TLS_KIND_CONTEXT 23L
#define TLS_KIND_SESSION 24L

/* Failure code encoding: (kind << 24) | payload. A negative return is
 * -(code); the obo wrapper reconstructs systls::Failure from it. Want/progress
 * statuses are small positive values and are never failure codes. */
#define TLS_FAIL_INVALID_ARGUMENT 1L
#define TLS_FAIL_RANGE 2L
#define TLS_FAIL_INVALID_RESOURCE 3L
#define TLS_FAIL_CLOSED 4L
#define TLS_FAIL_HANDSHAKE_FAILED 5L
#define TLS_FAIL_PROTOCOL_ALERT 6L
#define TLS_FAIL_PROTOCOL_VIOLATION 7L
#define TLS_FAIL_VERIFY_FAILED 8L
#define TLS_FAIL_NO_PEER_CERTIFICATE 9L
#define TLS_FAIL_CERTIFICATE_REJECTED 10L
#define TLS_FAIL_KEY_REJECTED 11L
#define TLS_FAIL_CONFIGURATION 12L
#define TLS_FAIL_ALPN_REQUIRED 13L
#define TLS_FAIL_NO_APPLICATION_PROTOCOL 14L
#define TLS_FAIL_ALPN_MISMATCH 15L
#define TLS_FAIL_TRUNCATED 16L
#define TLS_FAIL_NOT_READY 17L
#define TLS_FAIL_TRANSPORT 18L
#define TLS_FAIL_RESOURCE_LIMIT 19L
#define TLS_FAIL_BUSY 20L
#define TLS_FAIL_NOT_CONFIGURED 21L
#define TLS_FAIL_UNSUPPORTED 22L
#define TLS_FAIL_INTERNAL 23L

#define TLS_FAIL(kind, payload) \
    ((((long)(kind)) << 24) | ((long)(payload) & 0xffffffL))

/* Handshake / shutdown status values. */
#define TLS_STEP_DONE 1L
#define TLS_STEP_WANT_READ 2L
#define TLS_STEP_WANT_WRITE 3L
#define TLS_SHUTDOWN_SENT 2L
#define TLS_SHUTDOWN_PEER_CLOSED 3L
#define TLS_SHUTDOWN_WANT_READ 4L
#define TLS_SHUTDOWN_WANT_WRITE 5L

/* Context option packet: ten signed 64-bit fields (80 bytes). */
#define TLS_OPTION_FIELDS 10L
#define TLS_OPTION_BYTES (TLS_OPTION_FIELDS * 8L)
#define TLS_OPTION_ROLE 0L
#define TLS_OPTION_MIN_VERSION 1L
#define TLS_OPTION_MAX_VERSION 2L
#define TLS_OPTION_VERIFY 3L
#define TLS_OPTION_REQUIRE_SNI 4L
#define TLS_OPTION_VERIFY_DEPTH 5L
#define TLS_OPTION_MAX_SESSIONS 6L
#define TLS_OPTION_MAX_HANDSHAKE 7L
#define TLS_OPTION_MAX_CONTEXT_BYTES 8L
#define TLS_OPTION_RESERVED 9L

/* ALPN packet: policy, count, then count (length, bytes) entries. */
#define TLS_ALPN_POLICY 0L
#define TLS_ALPN_COUNT 1L
#define TLS_ALPN_HEADER_BYTES 16L
#define TLS_ALPN_DISABLED 0L
#define TLS_ALPN_PREFER 1L
#define TLS_ALPN_REQUIRE 2L

/* Session info selectors. */
#define TLS_INFO_VERSION 1L
#define TLS_INFO_CIPHER 2L
#define TLS_INFO_ALPN 3L
#define TLS_INFO_SNI 4L
#define TLS_INFO_SUBJECT 5L
#define TLS_INFO_ISSUER 6L
#define TLS_INFO_SHA256 7L
#define TLS_INFO_NOT_BEFORE 8L
#define TLS_INFO_NOT_AFTER 9L
#define TLS_INFO_PEER_PRESENT 10L
#define TLS_INFO_VERIFICATION 11L
#define TLS_INFO_VERIFY_CODE 12L
#define TLS_INFO_STATE 13L

/* Peer item selectors. */
#define TLS_PEER_DNS 1L
#define TLS_PEER_IP 2L

/* Aggregate count packet: twelve signed 64-bit fields (96 bytes). */
#define TLS_COUNT_FIELDS 12L
#define TLS_COUNT_BYTES (TLS_COUNT_FIELDS * 8L)
#define TLS_CONTEXT_COUNT_FIELDS 8L
#define TLS_CONTEXT_COUNT_BYTES (TLS_CONTEXT_COUNT_FIELDS * 8L)

/* Limits enforced by this adapter. */
#define TLS_MAX_TRANSFER 65536L
#define TLS_MAX_STAGE 131072L
#define TLS_MAX_BIO 131072L
#define TLS_MAX_CHAIN_PEM 1048576L
#define TLS_MAX_KEY_PEM 65536L
#define TLS_MAX_TRUST_PEM 4194304L
#define TLS_MAX_CHAIN_CERTS 16L
#define TLS_MAX_TRUST_CERTS 256L
#define TLS_MAX_CERT_DER 262144L
#define TLS_MAX_ALPN 16L
#define TLS_MAX_ALPN_BYTES 1024L
#define TLS_MAX_MESSAGE 1024L

long tls_init(void);
long tls_version_num(void);

long tls_context_new(long option_token);
long tls_context_load_chain(long context_token, long pem_token);
long tls_context_load_key(long context_token, long pem_token);
long tls_context_set_trust(long context_token, long pem_token);
long tls_context_use_system_trust(long context_token);
long tls_context_set_alpn(long context_token, long packet_token);
long tls_context_set_version(long context_token, long which, long version);
long tls_context_set_ciphers(long context_token, long which, long list_token);
long tls_context_set_groups(long context_token, long list_token);
long tls_context_ready(long context_token);
long tls_context_role(long context_token);
long tls_context_live_sessions(long context_token);
long tls_context_counts(long context_token, long out_token);
long tls_context_error_copy(long context_token, long out_token, long max);

long tls_session_new(long context_token);
long tls_session_handshake(long session_token);
long tls_session_read(long session_token, long out_token, long max);
long tls_session_write(long session_token, long in_token, long offset, long count);
long tls_session_drain(long session_token, long out_token, long max);
long tls_session_commit(long session_token, long written);
long tls_session_feed(long session_token, long in_token, long offset, long count);
long tls_session_eof(long session_token);
long tls_session_fail(long session_token);
long tls_session_output_pending(long session_token);
long tls_session_ciphertext_pending(long session_token);
long tls_session_plaintext_pending(long session_token);
long tls_session_shutdown(long session_token);
long tls_session_info_copy(long session_token, long field, long out_token, long max);
long tls_session_info_scalar(long session_token, long field);
long tls_session_peer_count(long session_token, long which);
long tls_session_peer_item(long session_token, long which, long index,
                           long out_token, long max);
long tls_session_error_copy(long session_token, long out_token, long max);
long tls_verify_text(long code, long out_token, long max);

long tls_counts(long out_token);

#endif
