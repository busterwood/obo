/* Scripted P1a/P1b fake backend. Selected at link time only; there is no runtime
 * production toggle and no stored obo callback. Paths are logical names; file
 * contents, directory entries and logger queues live in C-owned fake objects,
 * not real fds. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"
#include "sysnet_fake.h"
#include "sysfake_internal.h"

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
    long mode;
    long id;
    long refs;
    long ftype;          /* 0 regular, 4 directory, 10 symlink */
    char *link_target;
} fake_inode;

struct fake_desc {
    fake_inode *ino;
    size_t pos;
    int append;
    long refs;
};

typedef struct {
    long index;
    long id;
} fake_dir;

typedef struct {
    char *path;
    fake_inode *ino;
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
static long fake_open_beneath_disabled;
static long fake_log_short;

static const struct {
    const char *name;
    int kind;
} fake_entries[] = {
    {".", 4}, {"..", 4}, {"alpha", 8}, {"beta", 8}, {"gamma", 0}
};

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

int fake_pop_script(long call_class, long *result) {
    if (qhead == qtail) return 0;
    if (queue[qhead].call_class != call_class) return 0;
    *result = queue[qhead].result;
    qhead = (qhead + 1) % FAKE_QUEUE_MAX;
    return 1;
}

long fake_clock_now(void) {
    return fake_time;
}

void fake_clock_set(long value) {
    fake_time = value;
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
    fake_desc *d;
    if (f == NULL) return EINVAL;
    if (f->sock != NULL) {
        fake_sock_release(f->sock);
        f->sock = NULL;
        free(f);
        return 0;
    }
    d = f->d;
    if (d != NULL) {
        d->refs--;
        if (d->refs <= 0) {
            fake_inode *ino = d->ino;
            if (ino != NULL) {
                ino->refs--;
                if (ino->refs <= 0) {
                    free(ino->data);
                    free(ino->link_target);
                    free(ino);
                }
            }
            free(d);
        }
        f->d = NULL;
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

static fake_inode *new_inode(long mode) {
    fake_inode *ino = (fake_inode *)calloc(1, sizeof(*ino));
    if (ino == NULL) return NULL;
    ino->mode = mode & 07777;
    ino->id = fake_next_id++;
    if (ino->id <= 0) ino->id = 1;
    return ino;
}

static fake_file *new_file(long mode, size_t initial) {
    fake_file *f = (fake_file *)calloc(1, sizeof(*f));
    fake_desc *d;
    fake_inode *ino;
    if (f == NULL) return NULL;
    d = (fake_desc *)calloc(1, sizeof(*d));
    if (d == NULL) {
        free(f);
        return NULL;
    }
    ino = new_inode(mode);
    if (ino == NULL) {
        free(d);
        free(f);
        return NULL;
    }
    if (initial > 0) {
        ino->data = (unsigned char *)calloc(initial, 1);
        if (ino->data == NULL) {
            free(ino);
            free(d);
            free(f);
            return NULL;
        }
        ino->cap = initial;
        ino->len = initial;
    }
    ino->refs = 1;
    d->ino = ino;
    d->refs = 1;
    f->d = d;
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

static fake_inode *store_add(const char *path, size_t len, long mode) {
    fake_inode *ino;
    char *copy;
    if (fake_store_used >= FAKE_STORE_MAX) return NULL;
    ino = new_inode(mode);
    if (ino == NULL) return NULL;
    copy = (char *)malloc(len + 1);
    if (copy == NULL) {
        free(ino);
        return NULL;
    }
    memcpy(copy, path, len);
    copy[len] = '\0';
    ino->refs = 1;
    fake_store[fake_store_used].path = copy;
    fake_store[fake_store_used].ino = ino;
    fake_store_used++;
    return ino;
}

static fake_inode *store_ensure(const char *path, long mode) {
    long idx = store_find(path, strlen(path));
    if (idx >= 0) return fake_store[idx].ino;
    return store_add(path, strlen(path), mode);
}

static void store_remove_at(long idx) {
    if (idx < 0 || idx >= fake_store_used) return;
    free(fake_store[idx].path);
    if (fake_store[idx].ino != NULL) {
        fake_store[idx].ino->refs--;
        if (fake_store[idx].ino->refs <= 0) {
            free(fake_store[idx].ino->data);
            free(fake_store[idx].ino->link_target);
            free(fake_store[idx].ino);
        }
    }
    fake_store[idx] = fake_store[fake_store_used - 1];
    fake_store[fake_store_used - 1].path = NULL;
    fake_store[fake_store_used - 1].ino = NULL;
    fake_store_used--;
}

static void store_clear(void) {
    long i;
    for (i = 0; i < fake_store_used; i++) {
        free(fake_store[i].path);
        if (fake_store[i].ino != NULL) {
            fake_store[i].ino->refs--;
            if (fake_store[i].ino->refs <= 0) {
                free(fake_store[i].ino->data);
                free(fake_store[i].ino->link_target);
                free(fake_store[i].ino);
            }
        }
        fake_store[i].path = NULL;
        fake_store[i].ino = NULL;
    }
    fake_store_used = 0;
}

static long grow_inode(fake_inode *s, size_t need) {
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
    fake_open_beneath_disabled = 0;
    fake_log_short = 0;
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

long sysfake_disable_open_beneath(long disabled) {
    fake_open_beneath_disabled = (disabled != 0);
    return 1;
}

long sysfake_log_short(long bytes) {
    fake_log_short = bytes;
    return 1;
}

long sysfake_store_count(void) {
    return fake_store_used;
}

long sys_file_open(long dir_token, long path_token, long flags, long mode) {
    const char *path = NULL;
    size_t pathlen = 0;
    long scripted = 0;
    int have_script;
    fake_file *f;
    fake_inode *ino;
    fake_desc *d;
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
        ino = store_add(path, pathlen, mode);
        if (ino == NULL) return err(ENOMEM);
    } else {
        if ((flags & SYS_OPEN_CREATE) != 0 && (flags & SYS_OPEN_EXCLUSIVE) != 0) {
            return err(EEXIST);
        }
        ino = fake_store[idx].ino;
        if ((flags & SYS_OPEN_CREATE) != 0) ino->mode = mode & 07777;
        if ((flags & SYS_OPEN_TRUNCATE) != 0) ino->len = 0;
    }
    f = (fake_file *)calloc(1, sizeof(*f));
    d = (fake_desc *)calloc(1, sizeof(*d));
    if (f == NULL || d == NULL) {
        free(f);
        free(d);
        return err(ENOMEM);
    }
    d->ino = ino;
    d->pos = 0;
    d->append = (flags & SYS_OPEN_APPEND) != 0;
    d->refs = 1;
    ino->refs++;
    f->d = d;
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_shared, 0);
    if (token < 0) release_shared(f);
    return token;
}

long sys_dir_open(long dir_token, long path_token, long follow) {
    const char *path = NULL;
    size_t pathlen = 0;
    long scripted = 0;
    int have_script;
    fake_dir *d;
    long token;
    sys_reg_bump_call(SYS_CALL_DIR_OPEN);
    if (follow != 0 && follow != 1) return err(EINVAL);
    if (dir_token != 0) {
        fake_dir *parent = (fake_dir *)sys_reg_peek(dir_token, SYS_KIND_DIRECTORY);
        if (parent == NULL) return err(EBADF);
    }
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    have_script = pop_script(SYS_CALL_DIR_OPEN, &scripted);
    if (have_script && scripted < 0) return scripted;
    /* A root open accepts any path. An anchored child names only the scripted
     * "sub" directory; every other child is NotFound. */
    if (dir_token != 0) {
        if (follow != 0) return err(ENOENT);
        if (!(pathlen == 3 && memcmp(path, "sub", 3) == 0)) return err(ENOENT);
    }
    if (dir_token == 0) {
        /* The root script's regular entries have matching in-memory inodes so
         * path metadata and fstat identity agree for the same name. */
        if (store_ensure("alpha", 0644) == NULL ||
            store_ensure("beta", 0644) == NULL ||
            store_ensure("gamma", 0644) == NULL) {
            return err(ENOMEM);
        }
    }
    d = (fake_dir *)calloc(1, sizeof(*d));
    if (d == NULL) return err(ENOMEM);
    d->index = 0;
    d->id = (dir_token != 0) ? 1 : 0;
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
    it->id = src->id;
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
    /* Anchored child directories have no scripted entries of their own. */
    if (it->id != 0) return 0;
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
    if (f == NULL) return err(EBADF);
    if (offset < 0 || count <= 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (f->sock != NULL) return fake_sock_read(f, buf, (size_t)offset, (size_t)count);
    if (f->d == NULL || f->d->ino == NULL) return err(EBADF);
    if (have_script && scripted < 0) return scripted;
    if (position >= 0) {
        if ((unsigned long long)position >= f->d->ino->len) return 0;
        n = f->d->ino->len - (size_t)position;
    } else {
        if (f->d->pos >= f->d->ino->len) return 0;
        n = f->d->ino->len - f->d->pos;
    }
    if (n > (size_t)count) n = (size_t)count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    if (position >= 0) {
        memcpy(buf + offset, f->d->ino->data + (size_t)position, n);
    } else {
        memcpy(buf + offset, f->d->ino->data + f->d->pos, n);
        f->d->pos += n;
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
    if (f == NULL) return err(EBADF);
    if (f->sock != NULL) {
        if (offset < 0 || count < 0) return err(EINVAL);
        r = buffer_span(buf_token, &buf, &buflen);
        if (r < 0) return r;
        if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
            return err(ERANGE);
        }
        return fake_sock_write(f, buf + offset, (size_t)count);
    }
    if (f->d == NULL || f->d->ino == NULL) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (position >= 0 && f->d->append) return err(EINVAL);
    if (count == 0) return 0;
    if (have_script && scripted < 0) return scripted;
    n = (size_t)count;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    if (position >= 0) at = (size_t)position;
    else if (f->d->append) at = f->d->ino->len;
    else at = f->d->pos;
    if (at > (size_t)FAKE_FILE_MAX || n > (size_t)FAKE_FILE_MAX - at) return err(ENOSPC);
    r = grow_inode(f->d->ino, at + n);
    if (r < 0) return r;
    memcpy(f->d->ino->data + at, buf + offset, n);
    if (at + n > f->d->ino->len) f->d->ino->len = at + n;
    if (position < 0) f->d->pos = at + n;
    return (long)n;
}

long sys_io_seek(long token, long kind, long offset, long origin) {
    fake_file *f = file_of(token, kind);
    long base;
    long pos;
    sys_reg_bump_call(SYS_CALL_SEEK);
    if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EBADF);
    if (origin == SYS_SEEK_START) base = 0;
    else if (origin == SYS_SEEK_CURRENT) base = (long)f->d->pos;
    else if (origin == SYS_SEEK_END) base = (long)f->d->ino->len;
    else return err(EINVAL);
    if (offset > 0 && base > 0x7fffffffffffffffL - offset) return err(ERANGE);
    if (offset < 0) {
        if (offset == (-0x7fffffffffffffffL - 1) || base < -offset) return err(ERANGE);
    }
    pos = base + offset;
    if (pos < 0) return err(ERANGE);
    f->d->pos = (size_t)pos;
    return pos;
}

long sys_io_truncate(long token, long kind, long length) {
    fake_file *f = file_of(token, kind);
    long r;
    sys_reg_bump_call(SYS_CALL_TRUNCATE);
    if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EBADF);
    if (length < 0 || length > FAKE_FILE_MAX) return err(EINVAL);
    if ((size_t)length > f->d->ino->len) {
        r = grow_inode(f->d->ino, (size_t)length);
        if (r < 0) return r;
        memset(f->d->ino->data + f->d->ino->len, 0, (size_t)length - f->d->ino->len);
    }
    f->d->ino->len = (size_t)length;
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
    long ftype = 8;
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
        if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EBADF);
        mode = 040000 | (f->d->ino->mode & 07777);
        ftype = f->d->ino->ftype ? f->d->ino->ftype : 8;
        size = (long)f->d->ino->len;
        id = f->d->ino->id;
    } else {
        const char *path = NULL;
        size_t pathlen = 0;
        long idx;
        if (dir_token != 0 && sys_reg_peek(dir_token, SYS_KIND_DIRECTORY) == NULL) {
            return err(EBADF);
        }
        r = path_of(name_token, &path, &pathlen);
        if (r < 0) return r;
        idx = store_find(path, pathlen);
        if (idx >= 0) {
            fake_inode *ino = fake_store[idx].ino;
            mode = 040000 | (ino->mode & 07777);
            ftype = ino->ftype ? ino->ftype : 8;
            size = (long)ino->len;
            id = ino->id;
        } else {
            id = path_hash(path, pathlen);
        }
    }
    memset(out, 0, (size_t)SYS_STAT_PACKET_BYTES);
    {
        int64_t v;
        long fields[16];
        long i;
        fields[0] = ftype;
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
    if (src == NULL) return err(EBADF);
    if (to_kind <= 0) return err(EINVAL);
    if (src->d == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_DUP, &scripted);
    if (have_script && scripted < 0) return scripted;
    f = (fake_file *)calloc(1, sizeof(*f));
    if (f == NULL) return err(ENOMEM);
    f->d = src->d;
    f->d->refs++;
    out = sys_reg_new_object((int)to_kind, f, release_shared, 0);
    if (out < 0) {
        f->d->refs--;
        free(f);
    }
    return out;
}

long sys_file_retag(long token, long from_kind, long to_kind) {
    /* A connection transfer to Stream invalidates its old registrations now,
     * not only when a later wait happens to filter a stale native event. */
    fake_net_forget_source(token);
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

/* ---- P1b practical filesystem ------------------------------------------ */

static long fake_component(long token, const char **out, size_t *outlen) {
    const char *p = NULL;
    size_t len = 0;
    long r = path_of(token, &p, &len);
    if (r < 0) return r;
    if (len == 1 && p[0] == '.') return err(EINVAL);
    if (len == 2 && p[0] == '.' && p[1] == '.') return err(EINVAL);
    if (memchr(p, '/', len) != NULL) return err(EINVAL);
    *out = p;
    *outlen = len;
    return 0;
}

static int fake_dir_ok(long token) {
    if (token == 0) return 1;
    return sys_reg_peek(token, SYS_KIND_DIRECTORY) != NULL;
}

long sys_dir_mkdir(long dir_token, long name_token, long mode) {
    const char *name = NULL;
    size_t len = 0;
    long scripted = 0;
    int have_script;
    fake_inode *ino;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_MKDIR);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    if (mode < 0 || mode > 07777) return err(EINVAL);
    r = fake_component(name_token, &name, &len);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_DIR_MKDIR, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (store_find(name, len) >= 0) return err(EEXIST);
    ino = store_add(name, len, mode);
    if (ino == NULL) return err(ENOMEM);
    ino->ftype = 4;
    return 1;
}

long sys_dir_unlink(long dir_token, long name_token) {
    const char *name = NULL;
    size_t len = 0;
    long scripted = 0;
    int have_script;
    long idx;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_UNLINK);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    r = fake_component(name_token, &name, &len);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_DIR_UNLINK, &scripted);
    if (have_script && scripted < 0) return scripted;
    idx = store_find(name, len);
    if (idx < 0) return err(ENOENT);
    if (fake_store[idx].ino->ftype == 4) return err(EISDIR);
    store_remove_at(idx);
    return 1;
}

long sys_dir_remove_empty(long dir_token, long name_token) {
    const char *name = NULL;
    size_t len = 0;
    long scripted = 0;
    int have_script;
    long idx;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_RMDIR);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    r = fake_component(name_token, &name, &len);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_DIR_RMDIR, &scripted);
    if (have_script && scripted < 0) return scripted;
    idx = store_find(name, len);
    if (idx < 0) return err(ENOENT);
    if (fake_store[idx].ino->ftype != 4) return err(ENOTDIR);
    store_remove_at(idx);
    return 1;
}

long sys_dir_readlink(long dir_token, long name_token, long buf_token, long cap) {
    const char *name = NULL;
    size_t len = 0;
    unsigned char *out = NULL;
    size_t outlen = 0;
    long scripted = 0;
    int have_script;
    long idx;
    long r;
    size_t n;
    sys_reg_bump_call(SYS_CALL_DIR_READLINK);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    r = fake_component(name_token, &name, &len);
    if (r < 0) return r;
    r = buffer_span(buf_token, &out, &outlen);
    if (r < 0) return r;
    if (cap < 1 || (size_t)cap > outlen) return err(ERANGE);
    have_script = pop_script(SYS_CALL_DIR_READLINK, &scripted);
    if (have_script && scripted < 0) return scripted;
    idx = store_find(name, len);
    if (idx < 0) return err(ENOENT);
    if (fake_store[idx].ino->ftype != 10 || fake_store[idx].ino->link_target == NULL) {
        return err(EINVAL);
    }
    n = strlen(fake_store[idx].ino->link_target);
    if (n > (size_t)cap) return err(ERANGE);
    memcpy(out, fake_store[idx].ino->link_target, n);
    return (long)n;
}

long sys_dir_sync(long dir_token) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_DIR_SYNC);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    have_script = pop_script(SYS_CALL_DIR_SYNC, &scripted);
    if (have_script && scripted < 0) return scripted;
    return 1;
}

long sys_file_open_beneath_supported(void) {
    return fake_open_beneath_disabled ? 0 : 1;
}

/* Lexically normalize a beneath path the way RESOLVE_BENEATH permits: "."
 * components disappear, ".." may pop a real component but never the base.
 * The normalized text is "." when the path resolves to the base itself. */
static long beneath_normalize(const char *path, size_t len, char *out,
                              size_t *outlen) {
    size_t starts[2048];
    size_t depth = 0;
    size_t i = 0;
    size_t o = 0;
    if (len == 0) return err(EINVAL);
    if (path[0] == '/') return err(EXDEV);
    while (i < len) {
        size_t start;
        size_t clen;
        while (i < len && path[i] == '/') i++;
        if (i >= len) break;
        start = i;
        while (i < len && path[i] != '/') i++;
        clen = i - start;
        if (clen == 1 && path[start] == '.') continue;
        if (clen == 2 && path[start] == '.' && path[start + 1] == '.') {
            if (depth == 0) return err(EXDEV);
            depth--;
            o = starts[depth];
            continue;
        }
        if (depth >= sizeof(starts) / sizeof(starts[0])) return err(ENAMETOOLONG);
        starts[depth] = o;
        if (o > 0) out[o++] = '/';
        if (clen > 4095 - o) return err(ENAMETOOLONG);
        memcpy(out + o, path + start, clen);
        o += clen;
        depth++;
    }
    if (depth == 0) {
        out[0] = '.';
        o = 1;
    }
    *outlen = o;
    return 0;
}

long sys_file_open_beneath(long dir_token, long path_token, long flags,
                           long mode) {
    const char *path = NULL;
    size_t len = 0;
    char norm[4096];
    size_t normlen = 0;
    long scripted = 0;
    int have_script;
    fake_file *f;
    fake_inode *ino;
    long idx;
    long token;
    long nr;
    sys_reg_bump_call(SYS_CALL_OPEN_BENEATH);
    if (dir_token == 0) return err(EINVAL);
    if (!fake_dir_ok(dir_token)) return err(EBADF);
    if (fake_open_beneath_disabled) return err(ENOSYS);
    if (mode < 0 || mode > 07777 || !valid_open_flags(flags)) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &len);
        if (pr < 0) return pr;
    }
    nr = beneath_normalize(path, len, norm, &normlen);
    if (nr < 0) return nr;
    have_script = pop_script(SYS_CALL_OPEN_BENEATH, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (normlen == 1 && norm[0] == '.') {
        if ((flags & SYS_OPEN_CREATE) != 0) return err(EISDIR);
        ino = new_inode(0755);
        if (ino == NULL) return err(ENOMEM);
        ino->ftype = 4;
        ino->refs = 1;
    } else {
        idx = store_find(norm, normlen);
        if (idx < 0) {
            if ((flags & SYS_OPEN_CREATE) == 0) return err(ENOENT);
            ino = store_add(norm, normlen, mode);
            if (ino == NULL) return err(ENOMEM);
        } else {
            ino = fake_store[idx].ino;
            if ((flags & SYS_OPEN_CREATE) != 0 && ino->ftype == 4) {
                return err(EISDIR);
            }
            if ((flags & SYS_OPEN_CREATE) != 0 &&
                (flags & SYS_OPEN_EXCLUSIVE) != 0) {
                return err(EEXIST);
            }
            if ((flags & SYS_OPEN_TRUNCATE) != 0) ino->len = 0;
        }
    }
    f = (fake_file *)calloc(1, sizeof(*f));
    if (f == NULL) return err(ENOMEM);
    f->d = (fake_desc *)calloc(1, sizeof(*f->d));
    if (f->d == NULL) {
        free(f);
        return err(ENOMEM);
    }
    f->d->ino = ino;
    f->d->append = (flags & SYS_OPEN_APPEND) != 0;
    f->d->refs = 1;
    ino->refs++;
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_shared, 0);
    if (token < 0) release_shared(f);
    return token;
}

long sys_file_chmod(long token, long kind, long mode) {
    long scripted = 0;
    int have_script;
    fake_file *f;
    sys_reg_bump_call(SYS_CALL_CHMOD);
    if (mode < 0 || mode > 07777) return err(EINVAL);
    f = file_of(token, kind);
    if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_CHMOD, &scripted);
    if (have_script && scripted < 0) return scripted;
    f->d->ino->mode = mode;
    return 1;
}

long sys_file_sync(long token, long kind, long data_only) {
    long scripted = 0;
    int have_script;
    fake_file *f;
    sys_reg_bump_call(SYS_CALL_SYNC);
    if (data_only != 0 && data_only != 1) return err(EINVAL);
    f = file_of(token, kind);
    if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_SYNC, &scripted);
    if (have_script && scripted < 0) return scripted;
    return 1;
}

long sys_file_rename(long from_dir, long old_name, long to_dir, long new_name,
                     long replace) {
    const char *oldp = NULL;
    const char *newp = NULL;
    size_t oldlen = 0;
    size_t newlen = 0;
    long scripted = 0;
    int have_script;
    long from;
    long to;
    long r;
    sys_reg_bump_call(SYS_CALL_RENAME);
    if (!fake_dir_ok(from_dir) || !fake_dir_ok(to_dir)) return err(EBADF);
    if (replace != 0 && replace != 1) return err(EINVAL);
    r = fake_component(old_name, &oldp, &oldlen);
    if (r < 0) return r;
    r = fake_component(new_name, &newp, &newlen);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_RENAME, &scripted);
    if (have_script && scripted < 0) return scripted;
    from = store_find(oldp, oldlen);
    if (from < 0) return err(ENOENT);
    to = store_find(newp, newlen);
    if (to >= 0 && !replace) return err(EEXIST);
    if (to == from && replace) return 1;
    if (to >= 0) {
        long from_type = fake_store[from].ino->ftype;
        long to_type = fake_store[to].ino->ftype;
        if (to_type == 4 && from_type != 4) return err(EISDIR);
        if (to_type != 4 && from_type == 4) return err(ENOTDIR);
    }
    if (to >= 0) {
        store_remove_at(to);
        from = store_find(oldp, oldlen);
        if (from < 0) return err(ENOENT);
    }
    {
        char *copy = (char *)malloc(newlen + 1);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, newp, newlen);
        copy[newlen] = '\0';
        free(fake_store[from].path);
        fake_store[from].path = copy;
    }
    return 1;
}

long sys_file_link(long from_dir, long old_name, long to_dir, long new_name) {
    const char *oldp = NULL;
    const char *newp = NULL;
    size_t oldlen = 0;
    size_t newlen = 0;
    long scripted = 0;
    int have_script;
    long from;
    long r;
    sys_reg_bump_call(SYS_CALL_LINK);
    if (!fake_dir_ok(from_dir) || !fake_dir_ok(to_dir)) return err(EBADF);
    r = fake_component(old_name, &oldp, &oldlen);
    if (r < 0) return r;
    r = fake_component(new_name, &newp, &newlen);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_LINK, &scripted);
    if (have_script && scripted < 0) return scripted;
    from = store_find(oldp, oldlen);
    if (from < 0) return err(ENOENT);
    if (fake_store[from].ino->ftype == 4) return err(EPERM);
    if (store_find(newp, newlen) >= 0) return err(EEXIST);
    if (fake_store_used >= FAKE_STORE_MAX) return err(ENOSPC);
    {
        char *copy = (char *)malloc(newlen + 1);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, newp, newlen);
        copy[newlen] = '\0';
        fake_store[fake_store_used].path = copy;
        fake_store[fake_store_used].ino = fake_store[from].ino;
        fake_store[fake_store_used].ino->refs++;
        fake_store_used++;
    }
    return 1;
}

long sys_file_symlink(long target_token, long to_dir, long name_token) {
    const char *target = NULL;
    const char *name = NULL;
    size_t targetlen = 0;
    size_t namelen = 0;
    long scripted = 0;
    int have_script;
    fake_inode *ino;
    long r;
    sys_reg_bump_call(SYS_CALL_SYMLINK);
    if (!fake_dir_ok(to_dir)) return err(EBADF);
    {
        long pr = path_of(target_token, &target, &targetlen);
        if (pr < 0) return pr;
    }
    if (targetlen == 0) return err(EINVAL);
    r = fake_component(name_token, &name, &namelen);
    if (r < 0) return r;
    have_script = pop_script(SYS_CALL_SYMLINK, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (store_find(name, namelen) >= 0) return err(EEXIST);
    {
        char *copy = (char *)malloc(targetlen + 1);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, target, targetlen);
        copy[targetlen] = '\0';
        ino = store_add(name, namelen, 0777);
        if (ino == NULL) {
            free(copy);
            return err(ENOMEM);
        }
        ino->ftype = 10;
        ino->link_target = copy;
    }
    return 1;
}

/* ---- fake bounded logger ------------------------------------------------ */

typedef struct {
    fake_file *sink;
    unsigned char *buf;
    size_t cap;
    size_t start;
    size_t end;
    long dropped;
} fake_logger;

static int release_fake_logger(void *p) {
    fake_logger *l = (fake_logger *)p;
    if (l == NULL) return EINVAL;
    if (l->sink != NULL) release_shared(l->sink);
    free(l->buf);
    free(l);
    return 0;
}

long sys_log_open(long which, long capacity) {
    fake_logger *l;
    long token;
    sys_reg_bump_call(SYS_CALL_LOG_OPEN);
    if (which < 0 || which > 2) return err(EINVAL);
    if (capacity < 1 || capacity > (16L * 1024L * 1024L)) return err(ERANGE);
    l = (fake_logger *)calloc(1, sizeof(*l));
    if (l == NULL) return err(ENOMEM);
    l->buf = (unsigned char *)malloc((size_t)capacity);
    l->sink = new_file(0600, 0);
    if (l->buf == NULL || l->sink == NULL) {
        free(l->buf);
        if (l->sink != NULL) release_shared(l->sink);
        free(l);
        return err(ENOMEM);
    }
    l->cap = (size_t)capacity;
    token = sys_reg_new_object(SYS_KIND_LOGGER, l, release_fake_logger, 0);
    if (token < 0) release_fake_logger(l);
    return token;
}

long sys_log_emit(long token, long buf_token, long offset, long count,
                  long policy) {
    fake_logger *l;
    unsigned char *src = NULL;
    size_t srclen = 0;
    size_t used;
    long r;
    sys_reg_bump_call(SYS_CALL_LOG_EMIT);
    if (policy != 0 && policy != 1) return err(EINVAL);
    if (offset < 0 || count <= 0) return err(EINVAL);
    l = (fake_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    r = buffer_span(buf_token, &src, &srclen);
    if (r < 0) return r;
    if ((size_t)offset > srclen || (size_t)count > srclen - (size_t)offset) {
        return err(ERANGE);
    }
    used = l->end - l->start;
    if ((size_t)count > l->cap - used) {
        if (policy == 1) {
            l->dropped++;
            return 0;
        }
        return err(EAGAIN);
    }
    if (l->end + (size_t)count > l->cap) {
        memmove(l->buf, l->buf + l->start, used);
        l->start = 0;
        l->end = used;
    }
    memcpy(l->buf + l->end, src + offset, (size_t)count);
    l->end += (size_t)count;
    return 1;
}

static long fake_log_write(fake_logger *l, size_t n) {
    size_t at = l->sink->d->ino->len;
    if (grow_inode(l->sink->d->ino, at + n) < 0) return err(ENOSPC);
    memcpy(l->sink->d->ino->data + at, l->buf + l->start, n);
    l->sink->d->ino->len = at + n;
    l->start += n;
    if (l->start == l->end) {
        l->start = 0;
        l->end = 0;
    }
    return 1;
}

long sys_log_drain(long token, long finite, long deadline_ns) {
    fake_logger *l;
    long scripted = 0;
    int have_script;
    size_t n;
    sys_reg_bump_call(SYS_CALL_LOG_DRAIN);
    if (finite != 0 && finite != 1) return err(EINVAL);
    if (finite && deadline_ns < 0) return err(EINVAL);
    l = (fake_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_LOG_DRAIN, &scripted);
    if (have_script && scripted < 0) return scripted;
    n = l->end - l->start;
    if (have_script && scripted >= 0 && (size_t)scripted < n) n = (size_t)scripted;
    if (fake_log_short > 0 && (size_t)fake_log_short < n) n = (size_t)fake_log_short;
    if (n > 0 && fake_log_write(l, n) < 0) return err(ENOSPC);
    if (l->start < l->end) return 0;
    return 1;
}

long sys_log_flush(long token) {
    fake_logger *l;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_LOG_FLUSH);
    l = (fake_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    have_script = pop_script(SYS_CALL_LOG_FLUSH, &scripted);
    if (have_script && scripted < 0) return scripted;
    while (l->start < l->end) {
        if (fake_log_write(l, l->end - l->start) < 0) return err(ENOSPC);
    }
    return 1;
}

long sys_log_dropped(long token) {
    fake_logger *l = (fake_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    return l->dropped;
}

long sys_log_queued(long token) {
    fake_logger *l = (fake_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    return (long)(l->end - l->start);
}

static const char *fake_env[] = {
    "OBO_FAKE_ENV=1",
    "PATH=/fake/bin",
    "OBO_FAKE_EMPTY=",
    "OBO_FAKE_DUP=first",
    "OBO_FAKE_DUP=second",
    NULL
};

long sys_env_size(void) {
    long total = 0;
    long i;
    for (i = 0; fake_env[i] != NULL; i++) {
        total += (long)strlen(fake_env[i]) + 1;
    }
    return total;
}

long sys_env_snapshot(long buf_token, long cap) {
    unsigned char *out = NULL;
    size_t outlen = 0;
    long total = 0;
    long i;
    long r;
    if (cap < 0) return err(EINVAL);
    r = buffer_span(buf_token, &out, &outlen);
    if (r < 0) return r;
    if ((size_t)cap > outlen) return err(ERANGE);
    for (i = 0; fake_env[i] != NULL; i++) {
        size_t n = strlen(fake_env[i]) + 1;
        if ((size_t)total > (size_t)cap || n > (size_t)cap - (size_t)total) {
            return err(ERANGE);
        }
        memcpy(out + total, fake_env[i], n);
        total += (long)n;
    }
    return total;
}

long sys_res_new(long kind, long a, long b) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_REACTOR) {
        return fake_reactor_new(a, b);
    }
    if (kind == SYS_KIND_CANCELLATION) {
        (void)a;
        (void)b;
        return fake_cancel_new();
    }
    (void)b;
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
        if (f == NULL || f->d == NULL || f->d->ino == NULL) return err(EINVAL);
        have_script = pop_script(SYS_CALL_OP, &scripted);
        if (have_script && scripted < 0) return scripted;
        if (op == SYS_OP_TEST_WRITE) {
            if (a <= 0) return err(EINVAL);
            if (grow_inode(f->d->ino, 8) < 0) return err(ENOMEM);
            memcpy(f->d->ino->data, &a, sizeof(long));
            f->d->ino->len = 8;
            f->d->pos = 0;
            return 1;
        }
        if (op == SYS_OP_TEST_READ) {
            long v = 0;
            if (have_script) return scripted;
            if (f->d->ino->len < 8) return err(EAGAIN);
            memcpy(&v, f->d->ino->data, sizeof(v));
            f->d->ino->len = 0;
            f->d->pos = 0;
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
    fake_net_forget_source(token);
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
