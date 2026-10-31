#include "cres.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    uint32_t gen;
    int kind;
    int live;
    void *ptr;
    void (*release)(void *);
} cres_entry;

typedef struct {
    size_t len;
    unsigned char *data;
} cres_buffer;

static cres_entry cres_table[CRES_MAX_ENTRIES];
static uint32_t cres_next_gen = 1;

static uint32_t cres_new_gen(void) {
    uint32_t g = cres_next_gen++;
    if (g == 0 || g > 0x7fffffffu) {
        /* The token must stay a non-negative signed 64-bit value. This wrap is
         * an accidental-staleness limit, not a security boundary. */
        cres_next_gen = 2;
        g = 1;
    }
    return g;
}

static int cres_index(long token, uint32_t *gen) {
    uint64_t t;
    uint64_t low;
    if (token <= 0) return -1;
    t = (uint64_t)token;
    low = t & 0xffffffffu;
    if (low == 0 || low > CRES_MAX_ENTRIES) return -1;
    *gen = (uint32_t)(t >> 32);
    if (*gen == 0) return -1;
    return (int)(low - 1);
}

long cres_register(int kind, void *ptr, void (*release)(void *)) {
    int i;
    if (kind == CRES_KIND_NONE || ptr == NULL || release == NULL) return 0;
    for (i = 0; i < CRES_MAX_ENTRIES; i++) {
        if (!cres_table[i].live) {
            uint32_t gen = cres_new_gen();
            uint64_t token;
            cres_table[i].gen = gen;
            cres_table[i].kind = kind;
            cres_table[i].ptr = ptr;
            cres_table[i].release = release;
            cres_table[i].live = 1;
            token = ((uint64_t)gen << 32) | (uint64_t)(i + 1);
            if (token > 0x7fffffffffffffffULL) {
                cres_table[i].live = 0;
                return 0;
            }
            return (long)token;
        }
    }
    return 0;
}

void *cres_peek(long token, int kind) {
    uint32_t gen = 0;
    int i = cres_index(token, &gen);
    if (i < 0) return NULL;
    if (!cres_table[i].live || cres_table[i].gen != gen || cres_table[i].kind != kind) {
        return NULL;
    }
    return cres_table[i].ptr;
}

long cres_close_kind(long token, int kind) {
    uint32_t gen = 0;
    int i = cres_index(token, &gen);
    void *ptr;
    void (*release)(void *);
    if (i < 0) return 0;
    if (!cres_table[i].live || cres_table[i].gen != gen || cres_table[i].kind != kind) {
        return 0;
    }
    ptr = cres_table[i].ptr;
    release = cres_table[i].release;
    /* Poison before calling release: a release routine that re-enters the
     * registry cannot close this entry a second time. */
    cres_table[i].live = 0;
    cres_table[i].ptr = NULL;
    cres_table[i].release = NULL;
    cres_table[i].kind = CRES_KIND_NONE;
    cres_table[i].gen = cres_new_gen();
    release(ptr);
    return 1;
}

long cres_close(long token) {
    uint32_t gen = 0;
    int i = cres_index(token, &gen);
    if (i < 0) return 0;
    if (!cres_table[i].live || cres_table[i].gen != gen) return 0;
    return cres_close_kind(token, cres_table[i].kind);
}

long cres_live_count(void) {
    int i;
    long n = 0;
    for (i = 0; i < CRES_MAX_ENTRIES; i++) if (cres_table[i].live) n++;
    return n;
}

long cres_live_kind(long kind) {
    int i;
    long n = 0;
    for (i = 0; i < CRES_MAX_ENTRIES; i++) {
        if (cres_table[i].live && cres_table[i].kind == (int)kind) n++;
    }
    return n;
}

long cres_kind(long token) {
    uint32_t gen = 0;
    int i = cres_index(token, &gen);
    if (i < 0) return 0;
    if (!cres_table[i].live || cres_table[i].gen != gen) return 0;
    return cres_table[i].kind;
}

static void cres_buffer_release(void *p) {
    cres_buffer *b = (cres_buffer *)p;
    if (b == NULL) return;
    free(b->data);
    b->data = NULL;
    b->len = 0;
    free(b);
}

long cres_buf_new(long len) {
    cres_buffer *b;
    long token;
    if (len < 0 || len > CRES_MAX_BUFFER) return 0;
    b = (cres_buffer *)malloc(sizeof(*b));
    if (b == NULL) return 0;
    b->len = (size_t)len;
    b->data = (unsigned char *)calloc((size_t)len + 1, 1);
    if (b->data == NULL) {
        free(b);
        return 0;
    }
    token = cres_register(CRES_KIND_BUFFER, b, cres_buffer_release);
    if (token == 0) cres_buffer_release(b);
    return token;
}

long cres_buf_set(long token, long index, long value) {
    cres_buffer *b = (cres_buffer *)cres_peek(token, CRES_KIND_BUFFER);
    if (b == NULL || index < 0 || (size_t)index >= b->len) return 0;
    if (value < 0 || value > 255) return 0;
    b->data[index] = (unsigned char)value;
    return 1;
}

long cres_buf_get(long token, long index) {
    cres_buffer *b = (cres_buffer *)cres_peek(token, CRES_KIND_BUFFER);
    if (b == NULL || index < 0 || (size_t)index >= b->len) return -1;
    return (long)b->data[index];
}

long cres_buf_len(long token) {
    cres_buffer *b = (cres_buffer *)cres_peek(token, CRES_KIND_BUFFER);
    if (b == NULL || b->len > 0x7fffffffUL) return -1;
    return (long)b->len;
}

long cres_buf_free(long token) {
    return cres_close_kind(token, CRES_KIND_BUFFER);
}

long cres_buf_from_bytes(const void *data, size_t len) {
    long token;
    cres_buffer *b;
    if (len > (size_t)CRES_MAX_BUFFER) return 0;
    if (data == NULL && len != 0) return 0;
    token = cres_buf_new((long)len);
    if (token == 0) return 0;
    b = (cres_buffer *)cres_peek(token, CRES_KIND_BUFFER);
    if (b == NULL) {
        cres_buf_free(token);
        return 0;
    }
    if (len != 0) memcpy(b->data, data, len);
    b->data[len] = 0;
    return token;
}

long cres_buf_data(long token, unsigned char **data, size_t *len) {
    cres_buffer *b = (cres_buffer *)cres_peek(token, CRES_KIND_BUFFER);
    if (b == NULL || data == NULL || len == NULL) return 0;
    *data = b->data;
    *len = b->len;
    return 1;
}
