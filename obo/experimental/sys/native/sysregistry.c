/* getrandom and O_CLOEXEC on glibc */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysregistry.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <unistd.h>

#define SYS_MAX_ENTRIES 65536L
#define SYS_MAX_NATIVE_BYTES 2147483647L
#define SYS_MAX_GEN 0x7fffffffu

typedef struct {
    uint32_t gen;
    int kind;
    int live;
    void *ptr;
    int (*release)(void *);
    size_t bytes;
} sys_entry;

typedef struct {
    size_t len;
    unsigned char *data;
} sys_buffer;

static sys_entry *entries;
static long entry_cap;
static uint32_t next_gen = 1;
static long max_bytes_cfg;
static size_t used_bytes;
static int initialized;
static long cleanup_failures;
static long call_counts[8];

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static long random_u64(uint64_t *out) {
    ssize_t n;
    int first;
    int fd;
    size_t got;
    n = getrandom(out, sizeof(*out), 0);
    if (n == (ssize_t)sizeof(*out)) return 0;
    first = errno;
    fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return first > 0 ? first : EIO;
    got = 0;
    while (got < sizeof(*out)) {
        n = read(fd, (unsigned char *)out + got, sizeof(*out) - got);
        if (n <= 0) {
            int e = errno;
            (void)close(fd);
            return e > 0 ? e : EIO;
        }
        got += (size_t)n;
    }
    (void)close(fd);
    return 0;
}

/* A fresh unpredictable per-slot identity nonce. It is a hint ingredient, not
 * a capability: the C registry token remains private and separate. */
long sys_res_nonce(void) {
    uint64_t v = 0;
    long e;
    if (!initialized) return err(EINVAL);
    e = random_u64(&v);
    if (e != 0) return err(e);
    v &= 0xffffffffffULL;   /* 40 bits, so 40 + 20 slot bits stays positive */
    if (v < 2) v += 2;      /* 0 and 1 are never issued */
    return (long)v;
}

static int token_index(long token, uint32_t *gen) {
    uint64_t t;
    uint64_t low;
    if (token <= 0 || entry_cap <= 0) return -1;
    t = (uint64_t)token;
    low = t & 0xffffffffu;
    if (low == 0 || low > (uint64_t)entry_cap) return -1;
    *gen = (uint32_t)(t >> 32);
    if (*gen == 0) return -1;
    return (int)(low - 1);
}

static uint32_t fresh_gen(void) {
    /* Never wrap a generation: retire creation instead. */
    if (next_gen == 0 || next_gen > SYS_MAX_GEN) return 0;
    return next_gen++;
}

static int release_buffer(void *p) {
    sys_buffer *b = (sys_buffer *)p;
    if (b == NULL) return EINVAL;
    free(b->data);
    b->data = NULL;
    b->len = 0;
    free(b);
    return 0;
}

long sys_reg_new_object(int kind, void *ptr, int (*release)(void *), size_t bytes) {
    long i;
    uint32_t gen;
    uint64_t token;
    if (!initialized) return err(EINVAL);
    if (kind <= 0 || ptr == NULL || release == NULL) return err(EINVAL);
    if (bytes > (size_t)max_bytes_cfg ||
        bytes > (size_t)max_bytes_cfg - used_bytes) {
        return err(EDQUOT);
    }
    for (i = 0; i < entry_cap; i++) {
        if (!entries[i].live) {
            gen = fresh_gen();
            if (gen == 0) return err(EOVERFLOW);
            token = ((uint64_t)gen << 32) | (uint64_t)(i + 1);
            if (token > 0x7fffffffffffffffULL) return err(EOVERFLOW);
            entries[i].gen = gen;
            entries[i].kind = kind;
            entries[i].ptr = ptr;
            entries[i].release = release;
            entries[i].bytes = bytes;
            entries[i].live = 1;
            used_bytes += bytes;
            return (long)token;
        }
    }
    return err(EMFILE);
}

long sys_reg_new_buffer(long len) {
    sys_buffer *b;
    long token;
    if (len < 0) return err(EINVAL);
    if (len > SYS_MAX_BYTE_BUFFER) return err(ERANGE);
    if (!initialized) return err(EINVAL);
    if ((size_t)len > (size_t)max_bytes_cfg ||
        (size_t)len > (size_t)max_bytes_cfg - used_bytes) {
        return err(EDQUOT);
    }
    b = (sys_buffer *)malloc(sizeof(*b));
    if (b == NULL) return err(ENOMEM);
    b->len = (size_t)len;
    b->data = (unsigned char *)calloc((size_t)len + 1, 1);
    if (b->data == NULL) {
        free(b);
        return err(ENOMEM);
    }
    token = sys_reg_new_object(SYS_KIND_BYTE_BUFFER, b, release_buffer, (size_t)len);
    if (token < 0) {
        release_buffer(b);
        return token;
    }
    return token;
}

void *sys_reg_peek(long token, int kind) {
    uint32_t gen = 0;
    int i = token_index(token, &gen);
    if (i < 0) return NULL;
    if (!entries[i].live || entries[i].gen != gen || entries[i].kind != kind) {
        return NULL;
    }
    return entries[i].ptr;
}

long sys_reg_retire(long token, int kind, void **ptr, int (**release)(void *)) {
    uint32_t gen = 0;
    int i = token_index(token, &gen);
    sys_entry *e;
    if (i < 0) return 0;
    e = &entries[i];
    if (!e->live || e->gen != gen || e->kind != kind) return 0;
    if (ptr != NULL) *ptr = e->ptr;
    if (release != NULL) *release = e->release;
    if (e->bytes <= used_bytes) used_bytes -= e->bytes;
    else used_bytes = 0;
    e->live = 0;
    e->ptr = NULL;
    e->release = NULL;
    e->bytes = 0;
    /* Poison the old generation even if the table cannot allocate a new one. */
    e->gen = fresh_gen();
    return 1;
}


long sys_reg_buffer_op(void *ptr, long op, long a, long b, long c) {
    sys_buffer *buf = (sys_buffer *)ptr;
    size_t off;
    size_t i;
    if (buf == NULL) return err(EINVAL);
    if (op == SYS_OP_BUF_LEN) {
        if (buf->len > 0x7fffffffUL) return err(EOVERFLOW);
        return (long)buf->len;
    }
    if (op == SYS_OP_BUF_FILL) {
        if (a < 0 || a > 255) return err(EINVAL);
        if (buf->len != 0) memset(buf->data, (int)a, buf->len);
        return (long)buf->len;
    }
    if (op == SYS_OP_BUF_SET || op == SYS_OP_BUF_GET) {
        if (a < 0) return err(ERANGE);
        if ((size_t)a >= buf->len) return err(ERANGE);
        if (op == SYS_OP_BUF_GET) return (long)buf->data[a];
        if (b < 0 || b > 255) return err(EINVAL);
        buf->data[a] = (unsigned char)b;
        return 1;
    }
    if (op == SYS_OP_BUF_SET_PACKED || op == SYS_OP_BUF_GET_PACKED) {
        uint64_t packed;
        if (a < 0 || c < 1 || c > 7) return err(ERANGE);
        off = (size_t)a;
        if (off > buf->len || (size_t)c > buf->len - off) return err(ERANGE);
        if (op == SYS_OP_BUF_SET_PACKED) {
            if (b < 0) return err(ERANGE);
            if ((uint64_t)b >= (1ULL << (8 * (unsigned)c))) return err(ERANGE);
            packed = (uint64_t)b;
            for (i = 0; i < (size_t)c; i++) {
                buf->data[off + i] = (unsigned char)((packed >> (8 * i)) & 0xffu);
            }
            return (long)c;
        }
        packed = 0;
        for (i = 0; i < (size_t)c; i++) {
            packed |= ((uint64_t)buf->data[off + i]) << (8 * i);
        }
        if (packed > (uint64_t)0x7fffffffffffffffULL) return err(EOVERFLOW);
        return (long)packed;
    }
    return err(EOPNOTSUPP);
}

long sys_reg_live_count(void) {
    long i;
    long n = 0;
    for (i = 0; i < entry_cap; i++) if (entries[i].live) n++;
    return n;
}

long sys_reg_live_kind(long kind) {
    long i;
    long n = 0;
    for (i = 0; i < entry_cap; i++) {
        if (entries[i].live && entries[i].kind == (int)kind) n++;
    }
    return n;
}

long sys_reg_bytes(void) {
    if (used_bytes > 0x7fffffffUL) return 0x7fffffffL;
    return (long)used_bytes;
}

long sys_reg_cleanup_failures(void) {
    return cleanup_failures;
}

void sys_reg_add_cleanup_failure(void) {
    if (cleanup_failures < 0x7fffffffL) cleanup_failures++;
}

long sys_reg_call_count(long call_class) {
    if (call_class < 0 || call_class >= (long)(sizeof(call_counts) / sizeof(call_counts[0]))) {
        return 0;
    }
    return call_counts[call_class];
}

void sys_reg_bump_call(long call_class) {
    if (call_class >= 0 && call_class < (long)(sizeof(call_counts) / sizeof(call_counts[0]))) {
        if (call_counts[call_class] < 0x7fffffffL) call_counts[call_class]++;
    }
}

void sys_reg_reset_counts(void) {
    memset(call_counts, 0, sizeof(call_counts));
}

long sys_res_validate(long token, long kind) {
    sys_reg_bump_call(SYS_CALL_VALIDATE);
    return sys_reg_peek(token, kind) != NULL ? 1 : 0;
}

long sys_res_kind(long token) {
    uint32_t gen = 0;
    int i = token_index(token, &gen);
    if (i < 0) return 0;
    if (!entries[i].live || entries[i].gen != gen) return 0;
    return entries[i].kind;
}

long sys_res_live_count(void) { return sys_reg_live_count(); }
long sys_res_live_kind(long kind) { return sys_reg_live_kind(kind); }
long sys_res_bytes(void) { return sys_reg_bytes(); }
long sys_res_cleanup_failures(void) { return sys_reg_cleanup_failures(); }
long sys_res_call_count(long call_class) { return sys_reg_call_count(call_class); }

long sys_adapter_init(long max_resources, long max_bytes) {
    if (initialized) {
        /* sysplatform owns reinitialization policy; the adapter refuses a
         * second initialization of any kind. */
        return err(EBUSY);
    }
    if (max_resources < 1 || max_resources > SYS_MAX_ENTRIES) return err(EINVAL);
    if (max_bytes < 1 || max_bytes > SYS_MAX_NATIVE_BYTES) return err(EINVAL);
    entries = (sys_entry *)calloc((size_t)max_resources, sizeof(*entries));
    if (entries == NULL) return err(ENOMEM);
    entry_cap = max_resources;
    max_bytes_cfg = max_bytes;
    used_bytes = 0;
    cleanup_failures = 0;
    sys_reg_reset_counts();
    initialized = 1;
    return 0;
}

long sys_adapter_shutdown(void) {
    long i;
    long closed = 0;
    if (!initialized) return err(EINVAL);
    for (i = 0; i < entry_cap; i++) {
        if (entries[i].live) {
            void *ptr = entries[i].ptr;
            int (*release)(void *) = entries[i].release;
            long e;
            entries[i].live = 0;
            entries[i].ptr = NULL;
            entries[i].release = NULL;
            entries[i].bytes = 0;
            entries[i].gen = fresh_gen();
            if (release != NULL) {
                e = release(ptr);
                if (e != 0) sys_reg_add_cleanup_failure();
            }
            closed++;
        }
    }
    free(entries);
    entries = NULL;
    entry_cap = 0;
    max_bytes_cfg = 0;
    used_bytes = 0;
    initialized = 0;
    return closed;
}
