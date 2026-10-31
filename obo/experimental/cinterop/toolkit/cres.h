/* Experimental C resource registry for the cinterop toolkit.
 *
 * This is a tiny, trusted C-side table. Obo never receives a raw pointer: it
 * receives a 64-bit token made of a table index, a generation, and a kind.
 * Every adapter call validates the token before dereferencing anything, and a
 * close poisons the generation so a stale token cannot name a recycled slot.
 *
 * This protects against accidental stale/forged uses by ordinary obo code. It
 * is not a security boundary against trusted C, and the generation is 31 bits
 * so a program that closes the same slot 2^31 times can eventually reuse a
 * generation. The design notes that limitation instead of presenting the token
 * as an unforgeable capability.
 */
#ifndef OBO_CINTEROP_CRES_H
#define OBO_CINTEROP_CRES_H

#include <stddef.h>
#include <stdint.h>

enum {
    CRES_KIND_NONE = 0,
    CRES_KIND_BUFFER = 1,
    CRES_KIND_CONN = 2,
    CRES_KIND_RESULT = 3,
    CRES_KIND_CUSTOM = 4
};

#define CRES_MAX_ENTRIES 64
#define CRES_MAX_BUFFER (16L * 1024L * 1024L)

/* Register an external resource. Returns a positive token, or 0 on failure.
 * `release` is called exactly once when the token is closed. */
long cres_register(int kind, void *ptr, void (*release)(void *));

/* Return the live pointer for a token of the expected kind, or NULL. */
void *cres_peek(long token, int kind);

/* Close a token of any kind. Returns 1 if this call released it, 0 if the
 * token was invalid, stale, already closed, or of another kind. */
long cres_close(long token);

long cres_close_kind(long token, int kind);
long cres_live_count(void);
long cres_live_kind(long kind);
long cres_kind(long token);

/* Bounded byte buffers owned by the C side. Obo reads and writes them byte by
 * byte through the exported integer API; the raw pointer stays in C. */
long cres_buf_new(long len);
long cres_buf_set(long token, long index, long value);
long cres_buf_get(long token, long index);
long cres_buf_len(long token);
long cres_buf_free(long token);

/* Internal helpers for trusted adapters (not exported through the cbind). */
long cres_buf_from_bytes(const void *data, size_t len);
long cres_buf_data(long token, unsigned char **data, size_t *len);

#endif
