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
    int pipe;            /* parent end of a scripted child pipe */
    int nonblocking;     /* taken-pipe contract; survives producer exit */
    long pipe_ordinal;
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

static int fake_pipe_alive(long ordinal);

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
    if (pathlen == 1 && path[0] == '.') {
        /* The root path "." is a directory, matching the real backend. Its
         * identity is stable across opens. */
        if ((flags & SYS_OPEN_CREATE) != 0) {
            if ((flags & SYS_OPEN_EXCLUSIVE) != 0) return err(EEXIST);
            return err(EISDIR);
        }
        ino = store_ensure(".", 0755);
        if (ino == NULL) return err(ENOMEM);
        ino->ftype = 4;
    } else {
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
        if (f->d->pos >= f->d->ino->len) {
            if (f->d->pipe) {
                if (fake_pipe_alive(f->d->pipe_ordinal)) return err(EAGAIN);
                return 0;
            }
            return 0;
        }
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
        if ((flags & SYS_OPEN_CREATE) != 0) {
            /* openat2/O_CREAT|O_EXCL on an existing directory is EEXIST, as it
             * is for any existing name; plain O_CREAT on a directory is EISDIR. */
            if ((flags & SYS_OPEN_EXCLUSIVE) != 0) return err(EEXIST);
            return err(EISDIR);
        }
        idx = store_find(".", 1);
        if (idx < 0) {
            ino = store_add(".", 1, 0755);
            if (ino == NULL) return err(ENOMEM);
            ino->ftype = 4;
        } else {
            ino = fake_store[idx].ino;
        }
    } else {
        idx = store_find(norm, normlen);
        if (idx < 0) {
            if ((flags & SYS_OPEN_CREATE) == 0) return err(ENOENT);
            ino = store_add(norm, normlen, mode);
            if (ino == NULL) return err(ENOMEM);
        } else {
            ino = fake_store[idx].ino;
            if ((flags & SYS_OPEN_CREATE) != 0 &&
                (flags & SYS_OPEN_EXCLUSIVE) != 0) {
                return err(EEXIST);
            }
            if ((flags & SYS_OPEN_CREATE) != 0 && ino->ftype == 4) {
                return err(EISDIR);
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
    if (kind == SYS_KIND_SPAWN_PLAN) {
        (void)a;
        (void)b;
        return sys_proc_plan_new();
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
    if (kind == SYS_KIND_SPAWN_PLAN) {
        return sys_proc_plan_set(token, op, a, b);
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

/* ---- P3 fake process, signal and terminal backend ---------------------- */

typedef struct {
    char *exec;
    unsigned char *argv_blob;
    size_t argv_len;
    int argc;
    unsigned char *env_blob;
    size_t env_len;
    int envc;
    int env_mode;
    int redir[3];
    long src_token[3];
    int group;
} fake_plan;

typedef struct fake_child {
    long token;
    long pid;
    long ordinal;
    int state;              /* 0 running, 1 exited */
    int detached;
    int observed;
    long status;
    int group;
    int merge_error;
    fake_file *parent[3];
    fake_inode *ino[3];
    int ino_owned[3];
    size_t read_pos[3];
    struct fake_child *next;
} fake_child;

static fake_child *fake_children;
static long fake_child_next = 1;
static long fake_proc_ordinal;
static long fake_arg_max = 2097152L;
static void fake_child_close_pipes(fake_child *c);
static char fake_cwd[64] = "/fake-cwd";
static long fake_signal_active;
static long fake_signal_bits;
static long fake_signal_pending[6];
static long fake_term_rows = 24;
static long fake_term_columns = 80;
static int fake_term_raw;

static fake_plan *fake_plan_of(long token) {
    return (fake_plan *)sys_reg_peek(token, SYS_KIND_SPAWN_PLAN);
}

static int release_fake_plan(void *p) {
    fake_plan *plan = (fake_plan *)p;
    if (plan == NULL) return EINVAL;
    free(plan->exec);
    free(plan->argv_blob);
    free(plan->env_blob);
    free(plan);
    return 0;
}

long sys_proc_plan_new(void) {
    fake_plan *plan;
    long token;
    sys_reg_bump_call(SYS_CALL_PROC_PLAN_NEW);
    plan = (fake_plan *)calloc(1, sizeof(*plan));
    if (plan == NULL) return err(ENOMEM);
    plan->redir[0] = SYS_REDIRECT_INHERIT;
    plan->redir[1] = SYS_REDIRECT_INHERIT;
    plan->redir[2] = SYS_REDIRECT_INHERIT;
    token = sys_reg_new_object(SYS_KIND_SPAWN_PLAN, plan, release_fake_plan, 0);
    if (token < 0) release_fake_plan(plan);
    return token;
}

static int fake_count_nul(const unsigned char *data, size_t len, int env) {
    size_t i;
    size_t start = 0;
    int count = 0;
    if (len < 1 || data[len - 1] != 0) return -1;
    for (i = 0; i < len; i++) {
        if (data[i] != 0) continue;
        if (i == start && env) return -1;
        if (env) {
            size_t j;
            int sep = 0;
            for (j = start; j < i; j++) {
                if (data[j] == '=' && j > start) { sep = 1; break; }
            }
            if (!sep) return -1;
        }
        count++;
        start = i + 1;
    }
    return count;
}

long sys_proc_plan_set(long plan_token, long field, long a, long b) {
    fake_plan *plan = fake_plan_of(plan_token);
    unsigned char *data = NULL;
    size_t len = 0;
    long r;
    sys_reg_bump_call(SYS_CALL_PROC_PLAN_SET);
    if (plan == NULL) return err(EINVAL);
    if (field == SYS_OP_PLAN_GROUP) {
        if (a != 0 && a != 1) return err(EINVAL);
        plan->group = (int)a;
        return 1;
    }
    if (field == SYS_OP_PLAN_ENV_MODE) {
        if (a != 0 && a != 1) return err(EINVAL);
        plan->env_mode = (int)a;
        return 1;
    }
    if (field == SYS_OP_PLAN_INPUT || field == SYS_OP_PLAN_OUTPUT ||
        field == SYS_OP_PLAN_ERROR) {
        int which = (int)(field - SYS_OP_PLAN_INPUT);
        if (a < SYS_REDIRECT_INHERIT || a > SYS_REDIRECT_MERGE_ERROR) return err(EINVAL);
        if (a == SYS_REDIRECT_MERGE_ERROR && which != 2) return err(EINVAL);
        if (a == SYS_REDIRECT_STREAM) {
            fake_file *sf = file_of(b, SYS_KIND_STREAM);
            if (sf == NULL) return err(EBADF);
            if (sf->d != NULL && sf->d->nonblocking) {
                /* Nonblocking pipe source: the child would inherit EAGAIN
                 * semantics, so reject it like the real adapter. */
                return err(EOPNOTSUPP);
            }
            plan->src_token[which] = b;
        } else {
            if (b != 0) return err(EINVAL);
            plan->src_token[which] = 0;
        }
        plan->redir[which] = (int)a;
        return 1;
    }
    if (field == SYS_OP_PLAN_DIRECTORY) {
        if (a != 0 && sys_reg_peek(a, SYS_KIND_DIRECTORY) == NULL) return err(EBADF);
        return 1;
    }
    r = sys_reg_buffer_data(a, &data, &len);
    if (r < 0) return r;
    if (data == NULL) return err(EINVAL);
    if (field == SYS_OP_PLAN_EXEC) {
        char *copy;
        if (len < 1 || len > 4095 || memchr(data, 0, len) != NULL) return err(EINVAL);
        copy = (char *)malloc(len + 1);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, data, len);
        copy[len] = '\0';
        free(plan->exec);
        plan->exec = copy;
        return 1;
    }
    if (field == SYS_OP_PLAN_ARGV || field == SYS_OP_PLAN_ENV) {
        int count;
        unsigned char *copy;
        if (len == 0 && field == SYS_OP_PLAN_ENV) {
            free(plan->env_blob);
            plan->env_blob = NULL;
            plan->env_len = 0;
            plan->envc = 0;
            return 1;
        }
        count = fake_count_nul(data, len, field == SYS_OP_PLAN_ENV);
        if (count < 0) return err(EINVAL);
        if (field == SYS_OP_PLAN_ARGV && count < 1) return err(EINVAL);
        copy = (unsigned char *)malloc(len);
        if (copy == NULL) return err(ENOMEM);
        memcpy(copy, data, len);
        if (field == SYS_OP_PLAN_ARGV) {
            free(plan->argv_blob);
            plan->argv_blob = copy;
            plan->argv_len = len;
            plan->argc = count;
        } else {
            free(plan->env_blob);
            plan->env_blob = copy;
            plan->env_len = len;
            plan->envc = count;
        }
        return 1;
    }
    return err(EINVAL);
}

static fake_file *fake_pipe_file(fake_inode *ino, long ordinal) {
    fake_file *f = (fake_file *)calloc(1, sizeof(*f));
    fake_desc *d = (fake_desc *)calloc(1, sizeof(*d));
    if (f == NULL || d == NULL) {
        free(f);
        free(d);
        return NULL;
    }
    d->ino = ino;
    d->pipe = 1;
    d->nonblocking = 1;
    d->pipe_ordinal = ordinal;
    d->refs = 1;
    ino->refs++;
    f->d = d;
    return f;
}

static fake_child *fake_find_ordinal(long ordinal) {
    fake_child *c = fake_children;
    while (c != NULL && c->ordinal != ordinal) c = c->next;
    return c;
}

static int fake_pipe_alive(long ordinal) {
    fake_child *c = fake_find_ordinal(ordinal);
    return c != NULL && c->state == 0;
}

static void fake_child_unlink(fake_child *victim) {
    fake_child **pp = &fake_children;
    while (*pp != NULL) {
        if (*pp == victim) {
            *pp = victim->next;
            return;
        }
        pp = &(*pp)->next;
    }
}

static void fake_child_drop_inodes(fake_child *c) {
    int i;
    for (i = 0; i < 3; i++) {
        if (c->ino_owned[i] && c->ino[i] != NULL) {
            fake_inode *ino = c->ino[i];
            c->ino[i] = NULL;
            c->ino_owned[i] = 0;
            ino->refs--;
            if (ino->refs <= 0) {
                free(ino->data);
                free(ino->link_target);
                free(ino);
            }
        }
    }
}

static int release_fake_child(void *p) {
    fake_child *c = (fake_child *)p;
    int i;
    if (c == NULL) return EINVAL;
    for (i = 0; i < 3; i++) {
        if (c->parent[i] != NULL) {
            release_shared(c->parent[i]);
            c->parent[i] = NULL;
        }
    }
    fake_child_drop_inodes(c);
    /* close on a running or not-yet-observed child transfers the obligation
     * to the module reaper, exactly like the real backend. */
    if (!c->observed) {
        c->token = 0;
        return 0;
    }
    fake_child_unlink(c);
    free(c);
    return 0;
}

static fake_child *fake_child_of(long token) {
    return (fake_child *)sys_reg_peek(token, SYS_KIND_CHILD);
}

long sys_proc_spawn(long plan_token) {
    fake_plan *plan = fake_plan_of(plan_token);
    fake_child *c;
    long token;
    long scripted = 0;
    int have_script;
    int i;
    int fail_code = ENOMEM;
    sys_reg_bump_call(SYS_CALL_PROC_SPAWN);
    if (plan == NULL || plan->exec == NULL || plan->argc < 1) return err(EINVAL);
    have_script = pop_script(SYS_CALL_PROC_SPAWN, &scripted);
    if (have_script && scripted < 0) return scripted;
    have_script = pop_script(SYS_CALL_PROC_EXEC, &scripted);
    if (have_script && scripted < 0) return scripted;
    c = (fake_child *)calloc(1, sizeof(*c));
    if (c == NULL) return err(ENOMEM);
    c->pid = 1000 + fake_child_next;
    c->ordinal = ++fake_proc_ordinal;
    c->group = plan->group;
    c->merge_error = plan->redir[2] == SYS_REDIRECT_MERGE_ERROR;
    for (i = 0; i < 3; i++) {
        int kind = plan->redir[i];
        if (kind == SYS_REDIRECT_NULL) {
            c->ino[i] = NULL;
        } else if (kind == SYS_REDIRECT_PIPE) {
            fake_inode *ino;
            fake_file *pf;
            have_script = pop_script(SYS_CALL_PROC_PIPE, &scripted);
            if (have_script && scripted < 0) { fail_code = (int)(0 - scripted); goto fail; }
            ino = new_inode(0600);
            if (ino == NULL) goto fail;
            ino->refs = 1;
            pf = fake_pipe_file(ino, c->ordinal);
            if (pf == NULL) {
                ino->refs--;
                free(ino->data);
                free(ino);
                goto fail;
            }
            c->ino[i] = ino;
            c->ino_owned[i] = 1;
            c->parent[i] = pf;
        } else if (kind == SYS_REDIRECT_STREAM) {
            fake_file *sf;
            have_script = pop_script(SYS_CALL_PROC_DUP, &scripted);
            if (have_script && scripted < 0) { fail_code = (int)(0 - scripted); goto fail; }
            sf = file_of(plan->src_token[i], SYS_KIND_STREAM);
            if (sf == NULL || sf->d == NULL || sf->d->ino == NULL) goto fail;
            c->ino[i] = sf->d->ino;
            /* The child owns this reference for its whole lifetime, so closing
             * the source stream cannot free the inode under proc_read/write. */
            c->ino_owned[i] = 1;
            c->ino[i]->refs++;
        }
    }
    if (c->merge_error) {
        c->ino[2] = c->ino[1];
        c->ino_owned[2] = 0;
    }
    token = sys_reg_new_object(SYS_KIND_CHILD, c, release_fake_child, 0);
    if (token < 0) goto fail;
    c->token = token;
    c->state = 0;
    c->status = 0;
    c->observed = 0;
    c->read_pos[0] = 0;
    c->read_pos[1] = 0;
    c->read_pos[2] = 0;
    c->next = fake_children;
    fake_children = c;
    fake_child_next++;
    return token;
fail:
    for (i = 0; i < 3; i++) {
        if (c->parent[i] != NULL) {
            release_shared(c->parent[i]);
            c->parent[i] = NULL;
        }
    }
    fake_child_drop_inodes(c);
    free(c);
    return err(fail_code);
}

long sys_proc_take(long child_token, long which, long to_kind) {
    fake_child *c = fake_child_of(child_token);
    long token;
    sys_reg_bump_call(SYS_CALL_PROC_TAKE);
    {
        long scripted = 0;
        int have_script = pop_script(SYS_CALL_PROC_TAKE, &scripted);
        if (have_script && scripted < 0) return scripted;
    }
    if (c == NULL) return err(EINVAL);
    if (which < 0 || which > 2 || to_kind != SYS_KIND_STREAM) return err(EINVAL);
    if (c->detached) return err(ECHILD);
    if (c->parent[which] == NULL) return err(EINVAL);
    token = sys_reg_new_object(SYS_KIND_STREAM, c->parent[which], release_shared, 0);
    if (token < 0) return token;
    c->parent[which] = NULL;
    return token;
}

long sys_proc_wait(long child_token, long block) {
    fake_child *c = fake_child_of(child_token);
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_PROC_WAIT);
    have_script = pop_script(SYS_CALL_PROC_WAIT, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (c == NULL) return err(EINVAL);
    if (block != 0 && block != 1) return err(EINVAL);
    if (c->detached) return err(ECHILD);
    if (c->state == 1) {
        c->observed = 1;
        return c->status;
    }
    if (block) return err(EAGAIN);
    return 0;
}

long sys_proc_terminate(long child_token, long force, long group) {
    fake_child *c = fake_child_of(child_token);
    long id;
    sys_reg_bump_call(SYS_CALL_PROC_KILL);
    {
        long scripted = 0;
        int have_script = pop_script(SYS_CALL_PROC_KILL, &scripted);
        if (have_script && scripted < 0) return scripted;
    }
    if (c == NULL) return err(EINVAL);
    if ((force != 0 && force != 1) || (group != 0 && group != 1)) return err(EINVAL);
    if (c->detached) return err(ECHILD);
    if (group && !c->group) return err(EINVAL);
    if (c->state == 1) return 0;
    id = force ? 6 : 2;
    c->state = 1;
    c->status = (id << 8) | SYS_EXIT_SIGNALLED;
    fake_child_close_pipes(c);
    return 1;
}

long sys_proc_detach(long child_token) {
    fake_child *c = fake_child_of(child_token);
    sys_reg_bump_call(SYS_CALL_PROC_DETACH);
    {
        long scripted = 0;
        int have_script = pop_script(SYS_CALL_PROC_DETACH, &scripted);
        if (have_script && scripted < 0) return scripted;
    }
    if (c == NULL) return err(EINVAL);
    if (c->detached || c->state == 1) return 0;
    c->detached = 1;
    return 1;
}

long sys_proc_reap_children(void) {
    fake_child **pp = &fake_children;
    long n = 0;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_PROC_REAP);
    have_script = pop_script(SYS_CALL_PROC_REAP, &scripted);
    if (have_script && scripted < 0) return scripted;
    while (*pp != NULL) {
        fake_child *c = *pp;
        if (c->state == 1 && !c->observed) {
            c->observed = 1;
            n++;
        }
        if (c->observed && c->token == 0) {
            *pp = c->next;
            free(c);
            continue;
        }
        pp = &c->next;
    }
    return n;
}

long sysfake_proc_last(void) {
    return fake_proc_ordinal;
}

long sys_proc_child_count(void) {
    fake_child *c = fake_children;
    long n = 0;
    while (c != NULL) {
        if (c->token > 0) n++;
        c = c->next;
    }
    return n;
}

long sys_proc_watch_fd(long child_token) {
    (void)child_token;
    return err(EOPNOTSUPP);
}

long sysfake_proc_ready(long child_token) {
    fake_child *c = fake_child_of(child_token);
    if (c == NULL) return 0;
    return c->state == 1 ? 1 : 0;
}

long sysfake_set_arg_max(long value) {
    if (value < 4096 || value > 2147483647L) return err(EINVAL);
    fake_arg_max = value;
    return 1;
}

static void fake_child_close_pipes(fake_child *c) {
    int i;
    for (i = 0; i < 3; i++) {
        if (c->parent[i] != NULL && c->parent[i]->d != NULL) {
            c->parent[i]->d->pipe = 0;
        }
    }
}

long sysfake_proc_exit(long ordinal, long flags, long payload) {
    fake_child *c = fake_children;
    while (c != NULL && c->ordinal != ordinal) c = c->next;
    if (c == NULL) return err(ENOENT);
    if (c->state != 0) return 0;
    if (flags != SYS_EXIT_EXITED && flags != SYS_EXIT_SIGNALLED &&
        flags != (SYS_EXIT_SIGNALLED | SYS_EXIT_CORE)) {
        return err(EINVAL);
    }
    c->state = 1;
    c->status = ((payload & 0xffffff) << 8) | flags;
    fake_child_close_pipes(c);
    return 1;
}

long sysfake_proc_write(long ordinal, long which, long buf_token,
                        long offset, long count) {
    fake_child *c = fake_children;
    unsigned char *data = NULL;
    size_t len = 0;
    fake_inode *ino;
    size_t at;
    long r;
    while (c != NULL && c->ordinal != ordinal) c = c->next;
    if (c == NULL) return err(ENOENT);
    if (which < 0 || which > 2 || offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)offset > len || (size_t)count > len - (size_t)offset) return err(ERANGE);
    ino = c->ino[which];
    if (ino == NULL) return count; /* inherited or /dev/null: discarded */
    at = ino->len;
    if (at > (size_t)FAKE_FILE_MAX || (size_t)count > (size_t)FAKE_FILE_MAX - at) {
        return err(ENOSPC);
    }
    r = grow_inode(ino, at + (size_t)count);
    if (r < 0) return r;
    if (count > 0) memcpy(ino->data + at, data + offset, (size_t)count);
    ino->len = at + (size_t)count;
    return count;
}

long sysfake_proc_read(long ordinal, long which, long buf_token,
                       long offset, long count) {
    fake_child *c = fake_children;
    unsigned char *data = NULL;
    size_t len = 0;
    fake_inode *ino;
    size_t n;
    long r;
    while (c != NULL && c->ordinal != ordinal) c = c->next;
    if (c == NULL) return err(ENOENT);
    if (which < 0 || which > 2 || offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &data, &len);
    if (r < 0) return r;
    if ((size_t)offset > len || (size_t)count > len - (size_t)offset) return err(ERANGE);
    ino = c->ino[which];
    if (ino == NULL) return 0;
    if (c->read_pos[which] >= ino->len) return 0;
    n = ino->len - c->read_pos[which];
    if (n > (size_t)count) n = (size_t)count;
    if (n > 0) memcpy(data + offset, ino->data + c->read_pos[which], n);
    c->read_pos[which] += n;
    return (long)n;
}

long sys_proc_cwd(long buf_token, long cap) {
    unsigned char *data = NULL;
    size_t len = 0;
    size_t n;
    long r;
    sys_reg_bump_call(SYS_CALL_PROC_CWD);
    if (cap < 1) return err(EINVAL);
    r = sys_reg_buffer_data(buf_token, &data, &len);
    if (r < 0) return r;
    if (data == NULL || (size_t)cap > len) return err(ERANGE);
    n = strlen(fake_cwd);
    if (n + 1 > (size_t)cap) return err(ERANGE);
    memcpy(data, fake_cwd, n + 1);
    return (long)n;
}

long sys_proc_chdir(long dir_token) {
    sys_reg_bump_call(SYS_CALL_PROC_CHDIR);
    if (dir_token == 0 || sys_reg_peek(dir_token, SYS_KIND_DIRECTORY) == NULL) {
        return err(EBADF);
    }
    strcpy(fake_cwd, "/fake-dir");
    return 1;
}

long sys_proc_arg_max(void) {
    return fake_arg_max;
}

long sys_proc_pidfd_supported(void) {
    return 1;
}

long sys_signal_init(long ignore_pipe) {
    if (ignore_pipe != 0 && ignore_pipe != 1) return err(EINVAL);
    return 1;
}

typedef struct fake_signal_source {
    long bits;
    long pending[6];
} fake_signal_source;

static fake_signal_source *fake_signal_current;

static int release_fake_signal(void *p) {
    fake_signal_source *s = (fake_signal_source *)p;
    sys_reg_bump_call(SYS_CALL_SIGNAL_CLOSE);
    free(s);
    if (fake_signal_active == 1) {
        fake_signal_active = 0;
        fake_signal_bits = 0;
        fake_signal_current = NULL;
    }
    return 0;
}

long sys_signal_subscribe(long bits) {
    fake_signal_source *s;
    long token;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_SIGNAL_SUBSCRIBE);
    have_script = pop_script(SYS_CALL_SIGNAL_SUBSCRIBE, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (bits <= 0 || (bits & ~0x1fL) != 0) return err(EINVAL);
    if (fake_signal_active) return err(EBUSY);
    s = (fake_signal_source *)calloc(1, sizeof(*s));
    if (s == NULL) return err(ENOMEM);
    s->bits = bits;
    token = sys_reg_new_object(SYS_KIND_SIGNAL_SOURCE, s, release_fake_signal, 0);
    if (token < 0) {
        free(s);
        return token;
    }
    fake_signal_active = 1;
    fake_signal_bits = bits;
    fake_signal_current = s;
    return token;
}

long sys_signal_next(long source_token) {
    fake_signal_source *s = (fake_signal_source *)sys_reg_peek(source_token,
                                                               SYS_KIND_SIGNAL_SOURCE);
    long i;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_SIGNAL_NEXT);
    have_script = pop_script(SYS_CALL_SIGNAL_NEXT, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (s == NULL) return err(EINVAL);
    for (i = 1; i <= 5; i++) {
        if (s->pending[i] > 0) {
            long count = s->pending[i];
            s->pending[i] = 0;
            return (count << 8) | i;
        }
    }
    return 0;
}

long sysfake_signal_push(long signal, long count) {
    fake_signal_source *s = fake_signal_current;
    if (signal < 1 || signal > 5 || count < 1 || count > 255) return err(EINVAL);
    if (!fake_signal_active || s == NULL) return err(EINVAL);
    s->pending[signal] = count;
    fake_signal_pending[signal] = count;
    return 1;
}

long sysfake_signal_ready(long source_token) {
    fake_signal_source *s = (fake_signal_source *)sys_reg_peek(source_token,
                                                               SYS_KIND_SIGNAL_SOURCE);
    long i;
    if (s == NULL) return 0;
    for (i = 1; i <= 5; i++) {
        if (s->pending[i] > 0) return 1;
    }
    return 0;
}

/* Fake signal pending is global; sys_signal_next reads the active source. Add
 * the global into the next lookup by mirroring it when subscribed. */
long sys_signal_watch_fd(long source_token) {
    (void)source_token;
    return err(EOPNOTSUPP);
}

typedef struct {
    int raw;
    int restores;
} fake_terminal;

static int release_fake_terminal(void *p) {
    free(p);
    return 0;
}

long sys_terminal_open(long which) {
    fake_terminal *t;
    long token;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_TERMINAL_OPEN);
    have_script = pop_script(SYS_CALL_TERMINAL_OPEN, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (which < 0 || which > 2) return err(EINVAL);
    t = (fake_terminal *)calloc(1, sizeof(*t));
    if (t == NULL) return err(ENOMEM);
    token = sys_reg_new_object(SYS_KIND_TERMINAL, t, release_fake_terminal, 0);
    if (token < 0) free(t);
    return token;
}

long sys_terminal_size(long terminal_token) {
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_TERMINAL_SIZE);
    have_script = pop_script(SYS_CALL_TERMINAL_SIZE, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (sys_reg_peek(terminal_token, SYS_KIND_TERMINAL) == NULL) return err(EBADF);
    if (fake_term_rows < 0 || fake_term_rows > 65535 ||
        fake_term_columns < 0 || fake_term_columns > 65535) return err(ERANGE);
    return (fake_term_rows << 32) | fake_term_columns;
}

long sys_terminal_raw(long terminal_token) {
    fake_terminal *t = (fake_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_TERMINAL_RAW);
    have_script = pop_script(SYS_CALL_TERMINAL_RAW, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (t == NULL) return err(EBADF);
    if (t->raw) return 0;
    t->raw = 1;
    fake_term_raw = 1;
    return 1;
}

long sys_terminal_restore(long terminal_token) {
    fake_terminal *t = (fake_terminal *)sys_reg_peek(terminal_token, SYS_KIND_TERMINAL);
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_TERMINAL_RESTORE);
    have_script = pop_script(SYS_CALL_TERMINAL_RESTORE, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (t == NULL) return err(EBADF);
    if (!t->raw) return 0;
    t->raw = 0;
    t->restores++;
    fake_term_raw = 0;
    return 1;
}

long sys_terminal_dup(long terminal_token) {
    fake_file *f;
    long token;
    long scripted = 0;
    int have_script;
    sys_reg_bump_call(SYS_CALL_TERMINAL_DUP);
    have_script = pop_script(SYS_CALL_TERMINAL_DUP, &scripted);
    if (have_script && scripted < 0) return scripted;
    if (sys_reg_peek(terminal_token, SYS_KIND_TERMINAL) == NULL) return err(EBADF);
    f = new_file(0600, 0);
    if (f == NULL) return err(ENOMEM);
    token = sys_reg_new_object(SYS_KIND_STREAM, f, release_shared, 0);
    if (token < 0) release_shared(f);
    return token;
}

long sysfake_terminal_size(long rows, long columns) {
    if (rows < 0 || rows > 65535 || columns < 0 || columns > 65535) return err(EINVAL);
    fake_term_rows = rows;
    fake_term_columns = columns;
    return 1;
}

long sysfake_terminal_raw(long raw) {
    fake_term_raw = raw != 0;
    return 1;
}

void sys_proc_shutdown(void) {
    fake_child *c = fake_children;
    fake_children = NULL;
    while (c != NULL) {
        fake_child *next = c->next;
        if (c->state == 0) {
            /* Still running at explicit shutdown: report the outstanding
             * obligation like the real backend, then free the scripted state
             * so LSan sees no child leak. */
            sys_reg_add_cleanup_failure();
        }
        c->next = NULL;
        c->token = 0;
        c->observed = 1;
        release_fake_child(c);
        c = next;
    }
    fake_signal_active = 0;
    fake_signal_bits = 0;
    fake_signal_current = NULL;
    memset(fake_signal_pending, 0, sizeof(fake_signal_pending));
}
