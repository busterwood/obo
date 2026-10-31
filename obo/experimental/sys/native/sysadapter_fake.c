/* Scripted P1a fake backend. Selected at link time only; there is no runtime
 * production toggle and no stored obo callback. Paths are logical names; file
 * contents and directory entries live in C-owned fake objects, not real fds. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define FAKE_QUEUE_MAX 64
#define FAKE_STORE_MAX 64
#define FAKE_FILE_MAX (16L * 1024L * 1024L)

typedef struct {
    long call_class;
    long result;
} fake_script;

typedef struct {
    unsigned char *data;
    size_t len;
    size_t cap;
    size_t pos;
    int append;
    long mode;
    long id;
    long refs;
} fake_shared;

typedef struct {
    fake_shared *s;
} fake_file;

typedef struct {
    long index;
} fake_dir;

typedef struct {
    char *path;
    fake_shared *s;
} fake_store_entry;

static fake_script queue[FAKE_QUEUE_MAX];
static int qhead;
static int qtail;
static long fake_time;
static long fake_rt_sec = 1700000000L;
static long fake_rt_nsec = 123456789L;
static unsigned long long fake_seed = 1;
static long fake_next_id = 1;
static fake_store_entry fake_store[FAKE_STORE_MAX];
static long fake_store_used;

static const struct {
    const char *name;
    int kind;
} fake_entries[] = {
    {".", 4}, {"..", 4}, {"alpha", 8}, {"beta", 8}, {"gamma", 0}
};

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static int pop_script(long call_class, long *result) {
    if (qhead == qtail) return 0;
    if (queue[qhead].call_class != call_class) return 0;
    *result = queue[qhead].result;
    qhead = (qhead + 1) % FAKE_QUEUE_MAX;
    return 1;
}

static int release_shared(void *p) {
    fake_file *f = (fake_file *)p;
    if (f == NULL) return EINVAL;
    if (f->s != NULL) {
        f->s->refs--;
        if (f->s->refs <= 0) {
            free(f->s->data);
            free(f->s);
        }
        f->s = NULL;
    }
    free(f);
    return 0;
}

static int release_dir(void *p) {
    free(p);
    return 0;
}

static fake_file *file_of(long token, long kind) {
    return (fake_file *)sys_reg_peek(token, (int)kind);
}

static long buffer_span(long token, unsigned char **data, size_t *len) {
    long r = sys_reg_buffer_data(token, data, len);
    if (r < 0) return r;
    if (*data == NULL) return err(EINVAL);
    return 0;
}

static long path_of(long token, const char **out, size_t *outlen) {
    unsigned char *data = NULL;
    size_t len = 0;
    long r = buffer_span(token, &data, &len);
    if (r < 0) return r;
    if (len < 1) return err(EINVAL);
    if (len > 4095) return err(ENAMETOOLONG);
    if (memchr(data, 0, len) != NULL) return err(EINVAL);
    *out = (const char *)data;
    *outlen = len;
    return 0;
}

static long path_hash(const char *path, size_t len) {
    unsigned long long h = 1469598103934665603ULL;
    size_t i;
    for (i = 0; i < len; i++) {
        h ^= (unsigned char)path[i];
        h *= 1099511628211ULL;
    }
    h &= 0x7fffffffULL;
    if (h == 0) h = 1;
    return (long)h;
}

static fake_shared *new_shared(long mode) {
    fake_shared *s = (fake_shared *)calloc(1, sizeof(*s));
    if (s == NULL) return NULL;
    s->mode = mode & 07777;
    s->id = fake_next_id++;
    if (s->id <= 0) s->id = 1;
    s->refs = 1;
    return s;
}

static fake_file *new_file(long mode, size_t initial) {
    fake_file *f = (fake_file *)calloc(1, sizeof(*f));
    fake_shared *s;
    if (f == NULL) return NULL;
    s = new_shared(mode);
    if (s == NULL) {
        free(f);
        return NULL;
    }
    if (initial > 0) {
        s->data = (unsigned char *)calloc(initial, 1);
        if (s->data == NULL) {
            free(s);
            free(f);
            return NULL;
        }
        s->cap = initial;
        s->len = initial;
    }
    f->s = s;
    return f;
}

static long store_find(const char *path, size_t len) {
    long i;
    for (i = 0; i < fake_store_used; i++) {
        if (strlen(fake_store[i].path) == len &&
            memcmp(fake_store[i].path, path, len) == 0) {
            return i;
        }
    }
    return -1;
}

static fake_shared *store_add(const char *path, size_t len, long mode) {
    fake_shared *s;
    char *copy;
    if (fake_store_used >= FAKE_STORE_MAX) return NULL;
    s = new_shared(mode);
    if (s == NULL) return NULL;
    copy = (char *)malloc(len + 1);
    if (copy == NULL) {
        free(s);
        return NULL;
    }
    memcpy(copy, path, len);
    copy[len] = '\0';
    fake_store[fake_store_used].path = copy;
    fake_store[fake_store_used].s = s;
    fake_store_used++;
    return s;
}

static void store_clear(void) {
    long i;
    for (i = 0; i < fake_store_used; i++) {
        free(fake_store[i].path);
        if (fake_store[i].s != NULL) {
            if (fake_store[i].s->refs <= 1) {
                free(fake_store[i].s->data);
                free(fake_store[i].s);
            } else {
                fake_store[i].s->refs--;
            }
        }
        fake_store[i].path = NULL;
        fake_store[i].s = NULL;
    }
    fake_store_used = 0;
}

static long grow_shared(fake_shared *s, size_t need) {
    unsigned char *p;
    size_t cap;
    if (need > (size_t)FAKE_FILE_MAX) return err(ENOSPC);
    if (need <= s->cap) return 0;
    cap = s->cap ? s->cap : 64;
    while (cap < need) {
        if (cap > (size_t)FAKE_FILE_MAX / 2) {
            cap = need;
            break;
        }
        cap *= 2;
    }
    p = (unsigned char *)realloc(s->data, cap);
    if (p == NULL) return err(ENOMEM);
    if (cap > s->cap) memset(p + s->cap, 0, cap - s->cap);
    s->data = p;
    s->cap = cap;
    return 0;
}

static int valid_open_flags(long flags) {
    if ((flags & (SYS_OPEN_READ | SYS_OPEN_WRITE)) == 0) return 0;
    if ((flags & (SYS_OPEN_CREATE | SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE |
                  SYS_OPEN_APPEND)) != 0 &&
        (flags & SYS_OPEN_WRITE) == 0) return 0;
    if ((flags & SYS_OPEN_EXCLUSIVE) != 0 && (flags & SYS_OPEN_CREATE) == 0) return 0;
    if ((flags & SYS_OPEN_TRUNCATE) != 0 && (flags & SYS_OPEN_APPEND) != 0) return 0;
    return 1;
}

long sysfake_reset(void) {
    qhead = 0;
    qtail = 0;
    fake_time = 0;
    fake_rt_sec = 1700000000L;
    fake_rt_nsec = 123456789L;
    fake_seed = 1;
    fake_next_id = 1;
    store_clear();
    sys_reg_reset_counts();
    return 1;
}

long sysfake_push(long call_class, long result) {
    int next = (qtail + 1) % FAKE_QUEUE_MAX;
    if (next == qhead) return -ENOSPC;
    queue[qtail].call_class = call_class;
    queue[qtail].result = result;
    qtail = next;
    return 1;
}

long sysfake_set_time(long value) {
    fake_time = value;
    return 1;
}

long sysfake_now(void) {
    return fake_time;
}

long sysfake_set_realtime(long seconds, long nanos) {
    if (nanos < 0 || nanos > 999999999L) return -EINVAL;
    fake_rt_sec = seconds;
    fake_rt_nsec = nanos;
    return 1;
}

long sysfake_set_seed(long value) {
    fake_seed = (unsigned long long)value;
    return 1;
}

long sys_file_open(long dir_token, long path_token, long flags, long mode) {
    const char *path = NULL;
    size_t pathlen = 0;
    long scripted = 0;
    int have_script;
    fake_file *f;
    fake_shared *s;
    long idx;
    long token;
    sys_reg_bump_call(SYS_CALL_FILE_OPEN);
    if (dir_token != 0 && sys_reg_peek(dir_token, SYS_KIND_DIRECTORY) == NULL) {
        return err(EBADF);
    }
    if (mode < 0 || mode > 07777 || !valid_open_flags(flags)) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    have_script = pop_script(SYS_CALL_FILE_OPEN, &scripted);
    if (have_script && scripted < 0) return scripted;
    idx = store_find(path, pathlen);
    if (idx < 0) {
        if ((flags & SYS_OPEN_CREATE) == 0) return err(ENOENT);
        s = store_add(path, pathlen, (flags & SYS_OPEN_CREATE) ? mode : 0644);
        if (s == NULL) return err(ENOMEM);
    } else {
        if ((flags & SYS_OPEN_CREATE) != 0 && (flags & SYS_OPEN_EXCLUSIVE) != 0) {
            return err(EEXIST);
        }
        s = fake_store[idx].s;
        if ((flags & SYS_OPEN_CREATE) != 0) s->mode = mode & 07777;
        if ((flags & SYS_OPEN_TRUNCATE) != 0) {
            s->len = 0;
            s->pos = 0;
        }
    }
    s->pos = 0;
    s->append = (flags & SYS_OPEN_APPEND) != 0;
    f = (fake_file *)calloc(1, sizeof(*f));
    if (f == NULL) return err(ENOMEM);
    f->s = s;
    s->refs++;
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_shared, 0);
    if (token < 0) release_shared(f);
    return token;
}

long sys_dir_open(long path_token, long follow) {
    const char *path = NULL;
    size_t pathlen = 0;
    long scripted = 0;
    int have_script;
    fake_dir *d;
    long token;
    sys_reg_bump_call(SYS_CALL_DIR_OPEN);
    if (follow != 0 && follow != 1) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    (void)pathlen;
    have_script = pop_script(SYS_CALL_DIR_OPEN, &scripted);
    if (have_script && scripted < 0) return scripted;
    /* The fake has no child-directory namespace; an anchored child open is
     * NotFound so the scripted backend stays deterministic. */
    if (follow == 0) return err(ENOENT);
    d = (fake_dir *)calloc(1, sizeof(*d));
    if (d == NULL) return err(ENOMEM);
    d->index = 0;
    token = sys_reg_new_object(SYS_KIND_DIRECTORY, d, release_dir, 0);
    if (token < 0) free(d);
    return token;
}

long sys_dir_iterate(long dir_token) {
    fake_dir *src = (fake_dir *)sys_reg_peek(dir_token, SYS_KIND_DIRECTORY);
    fake_dir *it;
    long scripted = 0;
    int have_script;
    long token;
    sys_reg_bump_call(SYS_CALL_DIR_ITERATE);
    if (src == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_DIR_ITERATE, &scripted);
    if (have_script && scripted < 0) return scripted;
    it = (fake_dir *)calloc(1, sizeof(*it));
    if (it == NULL) return err(ENOMEM);
    it->index = src->index;
    token = sys_reg_new_object(SYS_KIND_ITERATOR, it, release_dir, 0);
    if (token < 0) free(it);
    return token;
}

long sys_dir_next(long it_token, long buf_token, long cap) {
    fake_dir *it = (fake_dir *)sys_reg_peek(it_token, SYS_KIND_ITERATOR);
    unsigned char *dst = NULL;
    size_t dstlen = 0;
    long scripted = 0;
    int have_script;
    long r;
    size_t n;
    sys_reg_bump_call(SYS_CALL_DIR_NEXT);
    if (it == NULL) return err(EBADF);
    r = buffer_span(buf_token, &dst, &dstlen);
    if (r < 0) return r;
    if (cap < 1 || (size_t)cap > dstlen) return err(ERANGE);
    have_script = pop_script(SYS_CALL_DIR_NEXT, &scripted);
    if (have_script && scripted < 0) return scripted;
    while ((size_t)it->index < sizeof(fake_entries) / sizeof(fake_entries[0])) {
        long i = it->index++;
        if (strcmp(fake_entries[i].name, ".") == 0 ||
            strcmp(fake_entries[i].name, "..") == 0) {
            continue;
        }
        n = strlen(fake_entries[i].name);
        if (n > (size_t)cap) return err(ENAMETOOLONG);
        memcpy(dst, fake_entries[i].name, n);
        return ((long)n << 8) | fake_entries[i].kind;
    }
    return 0;
}

long sys_io_read(long token, long kind, long buf_token, long offset, long count,
                 long position) {
    fake_file *f = file_of(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    long scripted = 0;
    int have_script;
    long r;
    size_t n;
    have_script = pop_script(SYS_CALL_READ, &scripted);
    sys_reg_bump_call(SYS_CALL_READ);
    if (f == NULL || f->s == NULL) return err(EBADF);
    if (offset < 0 || count <= 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (have_script && scripted < 0) return scripted;
    if (position >= 0) {
        if ((unsigned long long)position >= f->s->len) return 0;
        n = f->s->len - (size_t)position;
    } else {
        if (f->s->pos >= f->s->len) return 0;
        n = f->s->len - f->s->pos;
    }
    if (n > (size_t)count) n = (size_t)count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    if (position >= 0) {
        memcpy(buf + offset, f->s->data + (size_t)position, n);
    } else {
        memcpy(buf + offset, f->s->data + f->s->pos, n);
        f->s->pos += n;
    }
    return (long)n;
}

long sys_io_write(long token, long kind, long buf_token, long offset, long count,
                  long position) {
    fake_file *f = file_of(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    long scripted = 0;
    int have_script;
    long r;
    size_t n;
    size_t at;
    have_script = pop_script(SYS_CALL_WRITE, &scripted);
    sys_reg_bump_call(SYS_CALL_WRITE);
    if (f == NULL || f->s == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (count == 0) return 0;
    if (position >= 0 && f->s->append) return err(EINVAL);
    if (have_script && scripted < 0) return scripted;
    n = (size_t)count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    at = (position >= 0) ? (size_t)position : f->s->pos;
    if (at > (size_t)FAKE_FILE_MAX || n > (size_t)FAKE_FILE_MAX - at) return err(ENOSPC);
    r = grow_shared(f->s, at + n);
    if (r < 0) return r;
    memcpy(f->s->data + at, buf + offset, n);
    if (at + n > f->s->len) f->s->len = at + n;
    if (position < 0) f->s->pos = at + n;
    return (long)n;
}

long sys_io_seek(long token, long kind, long offset, long origin) {
    fake_file *f = file_of(token, kind);
    long base;
    long pos;
    sys_reg_bump_call(SYS_CALL_SEEK);
    if (f == NULL || f->s == NULL) return err(EBADF);
    if (origin == SYS_SEEK_START) base = 0;
    else if (origin == SYS_SEEK_CURRENT) base = (long)f->s->pos;
    else if (origin == SYS_SEEK_END) base = (long)f->s->len;
    else return err(EINVAL);
    if ((offset > 0 && base > 0x7fffffffffffffffL - offset) ||
        (offset < 0 && base < -offset)) {
        return err(ERANGE);
    }
    pos = base + offset;
    if (pos < 0) return err(EINVAL);
    f->s->pos = (size_t)pos;
    return pos;
}

long sys_io_truncate(long token, long kind, long length) {
    fake_file *f = file_of(token, kind);
    long r;
    sys_reg_bump_call(SYS_CALL_TRUNCATE);
    if (f == NULL || f->s == NULL) return err(EBADF);
    if (length < 0 || length > FAKE_FILE_MAX) return err(EINVAL);
    if ((size_t)length > f->s->len) {
        r = grow_shared(f->s, (size_t)length);
        if (r < 0) return r;
        memset(f->s->data + f->s->len, 0, (size_t)length - f->s->len);
    }
    f->s->len = (size_t)length;
    if (f->s->pos > f->s->len) f->s->pos = f->s->len;
    return 1;
}

long sys_stat_get(long subject_token, long subject_kind, long dir_token,
                  long name_token, long follow, long buf_token) {
    unsigned char *out = NULL;
    size_t outlen = 0;
    long scripted = 0;
    int have_script;
    long r;
    long id;
    long mode = 040644;
    long size = 0;
    long mtime_s = fake_time / 1000000000L;
    long mtime_ns = fake_time % 1000000000L;
    sys_reg_bump_call(SYS_CALL_STAT);
    r = buffer_span(buf_token, &out, &outlen);
    if (r < 0) return r;
    if (outlen < (size_t)SYS_STAT_PACKET_BYTES) return err(EINVAL);
    if (follow != 0 && follow != 1) return err(EINVAL);
    have_script = pop_script(SYS_CALL_STAT, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (subject_token != 0) {
        fake_file *f = file_of(subject_token, subject_kind);
        if (f == NULL || f->s == NULL) return err(EBADF);
        mode = 040000 | (f->s->mode & 07777);
        size = (long)f->s->len;
        id = f->s->id;
    } else {
        const char *path = NULL;
        size_t pathlen = 0;
        if (dir_token != 0 && sys_reg_peek(dir_token, SYS_KIND_DIRECTORY) == NULL) {
            return err(EBADF);
        }
        r = path_of(name_token, &path, &pathlen);
        if (r < 0) return r;
        id = path_hash(path, pathlen);
    }
    memset(out, 0, (size_t)SYS_STAT_PACKET_BYTES);
    {
        int64_t v;
        long fields[16];
        long i;
        fields[0] = 8;             /* regular */
        fields[1] = mode & 07777;
        fields[2] = size;
        fields[3] = 1;             /* links */
        fields[4] = 0;             /* uid */
        fields[5] = 0;             /* gid */
        fields[6] = mtime_s;
        fields[7] = mtime_ns;
        fields[8] = mtime_s;
        fields[9] = mtime_ns;
        fields[10] = mtime_s;
        fields[11] = mtime_ns;
        fields[12] = 0;
        fields[13] = 0;
        fields[14] = 0;            /* birth unavailable */
        fields[15] = id;
        for (i = 0; i < 16; i++) {
            v = (int64_t)fields[i];
            memcpy(out + i * 8, &v, sizeof(v));
        }
    }
    return 0;
}

long sys_file_dup(long token, long kind, long to_kind) {
    fake_file *src = file_of(token, kind);
    fake_file *f;
    long scripted = 0;
    int have_script;
    long out;
    sys_reg_bump_call(SYS_CALL_DUP);
    if (src == NULL || src->s == NULL) return err(EBADF);
    if (to_kind <= 0) return err(EINVAL);
    have_script = pop_script(SYS_CALL_DUP, &scripted);
    if (have_script && scripted < 0) return scripted;
    f = (fake_file *)calloc(1, sizeof(*f));
    if (f == NULL) return err(ENOMEM);
    f->s = src->s;
    f->s->refs++;
    out = sys_reg_new_object((int)to_kind, f, release_shared, 0);
    if (out < 0) {
        f->s->refs--;
        free(f);
    }
    return out;
}

long sys_file_retag(long token, long from_kind, long to_kind) {
    sys_reg_bump_call(SYS_CALL_RETAG);
    if (from_kind <= 0 || to_kind <= 0) return err(EINVAL);
    if (sys_reg_retag(token, (int)from_kind, (int)to_kind) != 1) return err(EINVAL);
    return 1;
}

long sys_clock_monotonic_ns(void) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_CLOCK);
    have_script = pop_script(SYS_CALL_CLOCK, &scripted);
    if (have_script && scripted < 0) return scripted;
    return fake_time;
}

long sys_clock_realtime_sec(void) {
    sys_reg_bump_call(SYS_CALL_CLOCK);
    return fake_rt_sec;
}

long sys_clock_realtime_nsec(void) {
    sys_reg_bump_call(SYS_CALL_CLOCK);
    return fake_rt_nsec;
}

long sys_sleep_until(long monotonic_ns) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_SLEEP);
    if (monotonic_ns < 0) return err(EINVAL);
    have_script = pop_script(SYS_CALL_SLEEP, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (monotonic_ns > fake_time) fake_time = monotonic_ns;
    return 1;
}

long sys_random(long buf_token, long offset, long count, long blocking) {
    unsigned char *buf = NULL;
    size_t buflen = 0;
    long scripted = 0;
    int have_script;
    long r;
    size_t n;
    size_t i;
    sys_reg_bump_call(SYS_CALL_RANDOM);
    if (blocking != 0 && blocking != 1) return err(EINVAL);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (count == 0) return 0;
    have_script = pop_script(SYS_CALL_RANDOM, &scripted);
    if (have_script && scripted < 0) return scripted;
    n = (size_t)count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    for (i = 0; i < n; i++) {
        fake_seed = fake_seed * 6364136223846793005ULL + 1442695040888963407ULL;
        buf[offset + (long)i] = (unsigned char)(fake_seed >> 33);
    }
    return (long)n;
}

long sys_stream_standard(long which) {
    fake_file *f;
    long token;
    sys_reg_bump_call(SYS_CALL_STANDARD);
    if (which < 0 || which > 2) return err(EINVAL);
    f = new_file(0600, 0);
    if (f == NULL) return err(ENOMEM);
    token = sys_reg_new_object(SYS_KIND_STREAM, f, release_shared, 0);
    if (token < 0) release_shared(f);
    return token;
}

long sys_res_new(long kind, long a, long b) {
    long scripted = 0;
    int have_script;
    (void)b;
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        return sys_reg_new_buffer(a);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        fake_file *f;
        long token;
        have_script = pop_script(SYS_CALL_NEW, &scripted);
        if (have_script && scripted < 0) return scripted;
        f = new_file(0600, 0);
        if (f == NULL) return err(ENOMEM);
        token = sys_reg_new_object((int)kind, f, release_shared, 0);
        if (token < 0) release_shared(f);
        return token;
    }
    return err(EINVAL);
}

long sys_res_op(long token, long kind, long op, long a, long b, long c) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_OP);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        void *ptr = sys_reg_peek(token, SYS_KIND_BYTE_BUFFER);
        if (ptr == NULL) return err(EINVAL);
        return sys_reg_buffer_op(ptr, op, a, b, c);
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        fake_file *f = file_of(token, kind);
        if (f == NULL || f->s == NULL) return err(EINVAL);
        have_script = pop_script(SYS_CALL_OP, &scripted);
        if (have_script && scripted < 0) return scripted;
        if (op == SYS_OP_TEST_WRITE) {
            if (a <= 0) return err(EINVAL);
            if (grow_shared(f->s, 8) < 0) return err(ENOMEM);
            memcpy(f->s->data, &a, sizeof(long));
            f->s->len = 8;
            f->s->pos = 0;
            return 1;
        }
        if (op == SYS_OP_TEST_READ) {
            long v = 0;
            if (have_script) return scripted;
            if (f->s->len < 8) return err(EAGAIN);
            memcpy(&v, f->s->data, sizeof(v));
            f->s->len = 0;
            f->s->pos = 0;
            return v;
        }
        if (op == SYS_OP_TEST_PROGRESS) {
            return have_script ? scripted : 0;
        }
        return err(EOPNOTSUPP);
    }
    return err(EINVAL);
}

long sys_res_close(long token, long kind) {
    long scripted = 0;
    int have_script;
    void *ptr = NULL;
    int (*release)(void *) = NULL;
    int e;
    sys_reg_bump_call(SYS_CALL_CLOSE);
    if (sys_reg_peek(token, (int)kind) == NULL) return 0;
    have_script = pop_script(SYS_CALL_CLOSE, &scripted);
    if (!sys_reg_retire(token, kind, &ptr, &release)) return 0;
    e = release != NULL ? release(ptr) : 0;
    /* A scripted close error still consumes the fake resource, matching Linux
     * close semantics instead of manufacturing a leak. */
    if (have_script && scripted < 0) {
        sys_reg_add_cleanup_failure();
        return scripted;
    }
    if (e != 0) {
        sys_reg_add_cleanup_failure();
        return err(e);
    }
    return 1;
}
