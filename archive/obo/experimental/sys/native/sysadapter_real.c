/* Linux P1a/P1b backend: files, directories, stat snapshots, clocks, getrandom,
 * practical directory/file mutation, openat2 containment, bounded logging and
 * environment snapshots.
 *
 * Scalar-only boundary: path and payload bytes live in C-owned byte-buffer
 * resources reached by token. No pointer, struct-by-value or callback crosses.
 * Every operation is one kernel attempt; EINTR is returned, never retried.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "sysadapter.h"
#include "sysregistry.h"
#include "sysnet.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/eventfd.h>
#include <time.h>
#include <unistd.h>

#ifndef AT_EMPTY_PATH
#define AT_EMPTY_PATH 0x1000
#endif
#ifndef RENAME_NOREPLACE
#define RENAME_NOREPLACE (1 << 0)
#endif

typedef struct {
    int fd;
    unsigned char *buf;
    size_t cap;
    size_t used;
    size_t pos;
    int failed;
} sys_iter;

typedef struct {
    unsigned long long ino;
    unsigned long long off;
    unsigned short reclen;
    unsigned char type;
    char name[256];
} sys_dirent;

static long err(long e) {
    return -(e > 0 ? e : EIO);
}

static int release_fdobj(void *p) {
    sys_fdobj *f = (sys_fdobj *)p;
    int e = 0;
    if (f == NULL) return EINVAL;
    if (f->fd >= 0) {
        /* Linux close consumes the descriptor even when it reports EINTR.
         * Never retry; report the captured error once. */
        if (close(f->fd) != 0) e = errno;
        f->fd = -1;
    }
    if (f->state != NULL) {
        f->state->refs--;
        if (f->state->refs <= 0) free(f->state);
        f->state = NULL;
    }
    free(f);
    return e;
}

static int release_iter(void *p) {
    sys_iter *it = (sys_iter *)p;
    int e = 0;
    if (it == NULL) return EINVAL;
    if (it->fd >= 0) {
        if (close(it->fd) != 0) e = errno;
        it->fd = -1;
    }
    free(it->buf);
    it->buf = NULL;
    free(it);
    return e;
}

static sys_fdobj *fdobj(long token, long kind) {
    return (sys_fdobj *)sys_reg_peek(token, (int)kind);
}

static long buffer_span(long token, unsigned char **data, size_t *len) {
    long r = sys_reg_buffer_data(token, data, len);
    if (r < 0) return r;
    if (*data == NULL) return err(EINVAL);
    return 0;
}

/* A path is a byte buffer of length 1..4095 with no embedded NUL. The buffer
 * allocation always has one zero guard byte, so openat/statx receive a valid
 * NUL-terminated C string with the exact validated length. */
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

static int dirfd_of(long token) {
    sys_fdobj *d;
    if (token == 0) return AT_FDCWD;
    d = fdobj(token, SYS_KIND_DIRECTORY);
    if (d == NULL || d->fd < 0) return -1;
    return d->fd;
}

static int kind_from_mode(unsigned long long mode) {
    switch (mode & 0170000ULL) {
        case 0010000ULL: return 1;  /* FIFO */
        case 0020000ULL: return 2;  /* CHR */
        case 0040000ULL: return 4;  /* DIR */
        case 0060000ULL: return 6;  /* BLK */
        case 0100000ULL: return 8;  /* REG */
        case 0120000ULL: return 10; /* LNK */
        case 0140000ULL: return 12; /* SOCK */
        default: return 0;
    }
}

static void put64(unsigned char *out, long index, long long v) {
    int64_t x = (int64_t)v;
    memcpy(out + (size_t)index * 8, &x, sizeof(x));
}

static long fill_stat_packet(unsigned char *out, size_t outlen,
                             unsigned long long mode, unsigned long long size,
                             unsigned long long links, unsigned uid,
                             unsigned gid, long long mtime_s, unsigned mtime_ns,
                             long long atime_s, unsigned atime_ns,
                             long long ctime_s, unsigned ctime_ns,
                             int birth_present, long long birth_s,
                             unsigned birth_ns, unsigned long long dev,
                             unsigned long long ino) {
    long id;
    if (outlen < (size_t)SYS_STAT_PACKET_BYTES) return err(EINVAL);
    if (size > (unsigned long long)0x7fffffffffffffffULL) return err(ERANGE);
    if (links > (unsigned long long)0x7fffffffffffffffULL) return err(ERANGE);
    id = sys_reg_intern_identity(dev, ino);
    if (id < 0) return id;
    memset(out, 0, (size_t)SYS_STAT_PACKET_BYTES);
    put64(out, 0, kind_from_mode(mode));
    put64(out, 1, (long long)(mode & 07777ULL));
    put64(out, 2, (long long)size);
    put64(out, 3, (long long)links);
    put64(out, 4, (long long)uid);
    put64(out, 5, (long long)gid);
    put64(out, 6, mtime_s);
    put64(out, 7, (long long)mtime_ns);
    put64(out, 8, atime_s);
    put64(out, 9, (long long)atime_ns);
    put64(out, 10, ctime_s);
    put64(out, 11, (long long)ctime_ns);
    put64(out, 12, birth_present ? birth_s : 0);
    put64(out, 13, birth_present ? (long long)birth_ns : 0);
    put64(out, 14, birth_present ? 1 : 0);
    put64(out, 15, id);
    return 0;
}

static long stat_packet(long subject_token, long subject_kind, long dir_token,
                        long name_token, long follow, unsigned char *out,
                        size_t outlen) {
    struct statx stx;
    struct stat st;
    int fd;
    const char *path = NULL;
    size_t pathlen = 0;
    int flags = 0;
    int rc;
    long r;

    if (subject_token != 0) {
        sys_fdobj *f = fdobj(subject_token, subject_kind);
        if (f == NULL || f->fd < 0) return err(EINVAL);
        fd = f->fd;
        rc = statx(fd, "", AT_EMPTY_PATH | AT_STATX_SYNC_AS_STAT,
                   STATX_BASIC_STATS | STATX_BTIME, &stx);
        if (rc == 0) {
            return fill_stat_packet(out, outlen, stx.stx_mode, stx.stx_size,
                                    stx.stx_nlink, stx.stx_uid, stx.stx_gid,
                                    stx.stx_mtime.tv_sec, stx.stx_mtime.tv_nsec,
                                    stx.stx_atime.tv_sec, stx.stx_atime.tv_nsec,
                                    stx.stx_ctime.tv_sec, stx.stx_ctime.tv_nsec,
                                    (stx.stx_mask & STATX_BTIME) != 0,
                                    stx.stx_btime.tv_sec, stx.stx_btime.tv_nsec,
                                    ((unsigned long long)stx.stx_dev_major << 32) |
                                        stx.stx_dev_minor,
                                    stx.stx_ino);
        }
        if (errno != ENOSYS) return err(errno);
        rc = fstat(fd, &st);
        if (rc != 0) return err(errno);
        return fill_stat_packet(out, outlen, (unsigned long long)st.st_mode,
                                (unsigned long long)st.st_size,
                                (unsigned long long)st.st_nlink,
                                (unsigned)st.st_uid, (unsigned)st.st_gid,
                                (long long)st.st_mtim.tv_sec,
                                (unsigned)st.st_mtim.tv_nsec,
                                (long long)st.st_atim.tv_sec,
                                (unsigned)st.st_atim.tv_nsec,
                                (long long)st.st_ctim.tv_sec,
                                (unsigned)st.st_ctim.tv_nsec, 0, 0, 0,
                                (unsigned long long)st.st_dev,
                                (unsigned long long)st.st_ino);
    }

    r = path_of(name_token, &path, &pathlen);
    if (r < 0) return r;
    (void)pathlen;
    fd = dirfd_of(dir_token);
    if (dir_token != 0 && fd < 0) return err(EBADF);
    if (!follow) flags |= AT_SYMLINK_NOFOLLOW;
    rc = statx(fd, path, flags | AT_STATX_SYNC_AS_STAT,
               STATX_BASIC_STATS | STATX_BTIME, &stx);
    if (rc == 0) {
        return fill_stat_packet(out, outlen, stx.stx_mode, stx.stx_size,
                                stx.stx_nlink, stx.stx_uid, stx.stx_gid,
                                stx.stx_mtime.tv_sec, stx.stx_mtime.tv_nsec,
                                stx.stx_atime.tv_sec, stx.stx_atime.tv_nsec,
                                stx.stx_ctime.tv_sec, stx.stx_ctime.tv_nsec,
                                (stx.stx_mask & STATX_BTIME) != 0,
                                stx.stx_btime.tv_sec, stx.stx_btime.tv_nsec,
                                ((unsigned long long)stx.stx_dev_major << 32) |
                                    stx.stx_dev_minor,
                                stx.stx_ino);
    }
    if (errno != ENOSYS) return err(errno);
    rc = fstatat(fd, path, &st, flags);
    if (rc != 0) return err(errno);
    return fill_stat_packet(out, outlen, (unsigned long long)st.st_mode,
                            (unsigned long long)st.st_size,
                            (unsigned long long)st.st_nlink,
                            (unsigned)st.st_uid, (unsigned)st.st_gid,
                            (long long)st.st_mtim.tv_sec,
                            (unsigned)st.st_mtim.tv_nsec,
                            (long long)st.st_atim.tv_sec,
                            (unsigned)st.st_atim.tv_nsec,
                            (long long)st.st_ctim.tv_sec,
                            (unsigned)st.st_ctim.tv_nsec, 0, 0, 0,
                            (unsigned long long)st.st_dev,
                            (unsigned long long)st.st_ino);
}

static int open_flags(long flags) {
    int f;
    if ((flags & (SYS_OPEN_READ | SYS_OPEN_WRITE)) == 0) return -1;
    if ((flags & (SYS_OPEN_CREATE | SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE |
                  SYS_OPEN_APPEND)) != 0 &&
        (flags & SYS_OPEN_WRITE) == 0) return -1;
    if ((flags & SYS_OPEN_EXCLUSIVE) != 0 && (flags & SYS_OPEN_CREATE) == 0) return -1;
    if ((flags & SYS_OPEN_TRUNCATE) != 0 && (flags & SYS_OPEN_APPEND) != 0) return -1;
    if ((flags & SYS_OPEN_READ) != 0 && (flags & SYS_OPEN_WRITE) != 0) {
        f = O_RDWR;
    } else if ((flags & SYS_OPEN_WRITE) != 0) {
        f = O_WRONLY;
    } else {
        f = O_RDONLY;
    }
    if ((flags & SYS_OPEN_CREATE) != 0) f |= O_CREAT;
    if ((flags & SYS_OPEN_EXCLUSIVE) != 0) f |= O_EXCL;
    if ((flags & SYS_OPEN_TRUNCATE) != 0) f |= O_TRUNC;
    if ((flags & SYS_OPEN_APPEND) != 0) f |= O_APPEND;
    if ((flags & SYS_OPEN_NOFOLLOW) != 0) f |= O_NOFOLLOW;
    if ((flags & SYS_OPEN_NONBLOCK) != 0) f |= O_NONBLOCK;
    return f | O_CLOEXEC;
}

long sys_file_open(long dir_token, long path_token, long flags, long mode) {
    const char *path = NULL;
    size_t pathlen = 0;
    sys_fdobj *f;
    int fd;
    int oflags;
    int dfd;
    long token;

    if (mode < 0 || mode > 07777) return err(EINVAL);
    if (flags < 0 || (flags & ~(SYS_OPEN_READ | SYS_OPEN_WRITE | SYS_OPEN_CREATE |
                                SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE |
                                SYS_OPEN_APPEND | SYS_OPEN_NOFOLLOW |
                                SYS_OPEN_NONBLOCK)) != 0) {
        return err(EINVAL);
    }
    oflags = open_flags(flags);
    if (oflags < 0) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    (void)pathlen;
    dfd = dirfd_of(dir_token);
    if (dir_token != 0 && dfd < 0) return err(EBADF);
    fd = openat(dfd, path, oflags, (mode_t)((flags & SYS_OPEN_CREATE) ? mode : 0));
    if (fd < 0) return err(errno);
    f = (sys_fdobj *)malloc(sizeof(*f));
    if (f == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    f->fd = fd;
    f->append = (flags & SYS_OPEN_APPEND) != 0;
    f->mode = mode;
    f->sock = 0;
    f->state = NULL;
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_fdobj, 0);
    if (token < 0) {
        (void)close(fd);
        free(f);
    }
    return token;
}

long sys_dir_open(long dir_token, long path_token, long follow) {
    const char *path = NULL;
    size_t pathlen = 0;
    sys_fdobj *d;
    int fd;
    int dfd;
    int flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC;
    long token;
    if (follow != 0 && follow != 1) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    (void)pathlen;
    dfd = dirfd_of(dir_token);
    if (dir_token != 0 && dfd < 0) return err(EBADF);
    if (!follow) flags |= O_NOFOLLOW;
    fd = openat(dfd, path, flags);
    if (fd < 0) return err(errno);
    d = (sys_fdobj *)malloc(sizeof(*d));
    if (d == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    d->fd = fd;
    d->append = 0;
    d->mode = 0;
    d->sock = 0;
    d->state = NULL;
    token = sys_reg_new_object(SYS_KIND_DIRECTORY, d, release_fdobj, 0);
    if (token < 0) {
        (void)close(fd);
        free(d);
    }
    return token;
}

long sys_dir_iterate(long dir_token) {
    sys_fdobj *d = fdobj(dir_token, SYS_KIND_DIRECTORY);
    sys_iter *it;
    int fd;
    long token;
    if (d == NULL || d->fd < 0) return err(EBADF);
    fd = fcntl(d->fd, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    it = (sys_iter *)calloc(1, sizeof(*it));
    if (it == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    it->fd = fd;
    it->cap = 8192;
    it->buf = (unsigned char *)malloc(it->cap);
    if (it->buf == NULL) {
        (void)close(fd);
        free(it);
        return err(ENOMEM);
    }
    token = sys_reg_new_object(SYS_KIND_ITERATOR, it, release_iter, 0);
    if (token < 0) {
        (void)close(fd);
        free(it->buf);
        free(it);
    }
    return token;
}

static long parse_dirent(const unsigned char *p, size_t avail, sys_dirent *out) {
    unsigned short reclen;
    unsigned char type;
    size_t n;
    if (avail < 19) return err(EIO);
    memcpy(&reclen, p + 16, sizeof(reclen));
    type = p[18];
    if (reclen < 19 || (size_t)reclen > avail) return err(EIO);
    for (n = 0; n < (size_t)reclen - 19; n++) {
        if (p[19 + n] == 0) break;
    }
    if (n >= (size_t)reclen - 19 || n == 0 || n > 255) return err(EIO);
    out->reclen = reclen;
    out->type = type;
    out->name[0] = '\0';
    memcpy(out->name, p + 19, n);
    out->name[n] = '\0';
    return (long)n;
}

long sys_dir_next(long it_token, long buf_token, long cap) {
    sys_iter *it = (sys_iter *)sys_reg_peek(it_token, SYS_KIND_ITERATOR);
    unsigned char *dst = NULL;
    size_t dstlen = 0;
    long r;
    int refills = 0;
    if (it == NULL) return err(EBADF);
    if (it->failed) return err(EIO);
    r = buffer_span(buf_token, &dst, &dstlen);
    if (r < 0) return r;
    if (cap < 1 || (size_t)cap > dstlen) return err(ERANGE);
    for (;;) {
        while (it->pos < it->used) {
            sys_dirent ent;
            long n = parse_dirent(it->buf + it->pos, it->used - it->pos, &ent);
            if (n < 0) {
                it->failed = 1;
                return n;
            }
            it->pos += ent.reclen;
            if (strcmp(ent.name, ".") == 0 || strcmp(ent.name, "..") == 0) continue;
            if ((size_t)n > (size_t)cap) return err(ENAMETOOLONG);
            memcpy(dst, ent.name, (size_t)n);
            if (ent.type == 4) return ((long)n << 8) | 4;
            if (ent.type == 1) return ((long)n << 8) | 1;
            if (ent.type == 2) return ((long)n << 8) | 2;
            if (ent.type == 6) return ((long)n << 8) | 6;
            if (ent.type == 8) return ((long)n << 8) | 8;
            if (ent.type == 10) return ((long)n << 8) | 10;
            if (ent.type == 12) return ((long)n << 8) | 12;
            return ((long)n << 8);
        }
        if (refills >= 64) {
            it->failed = 1;
            return err(EIO);
        }
        refills++;
        for (;;) {
            long n = syscall(SYS_getdents64, it->fd, it->buf, it->cap);
            if (n < 0) {
                if (errno == EINTR) return err(EINTR);
                return err(errno);
            }
            if (n == 0) {
                it->used = 0;
                it->pos = 0;
                return 0;
            }
            it->used = (size_t)n;
            it->pos = 0;
            break;
        }
    }
}

long sys_io_read(long token, long kind, long buf_token, long offset, long count,
                 long position) {
    sys_fdobj *f = fdobj(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    ssize_t n;
    long r;
    if (f == NULL || f->fd < 0) return err(EBADF);
    if (offset < 0 || count <= 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (f->sock && f->state != NULL) {
        if (f->state->connecting) return err(EINPROGRESS);
        if (f->state->failed) return err(f->state->failure);
        if (f->state->read_closed) return 0;
        n = recv(f->fd, buf + offset, (size_t)count, 0);
        if (n == 0) f->state->read_closed = 1;
        else if (n < 0) {
            int e = errno;
            if (e == ECONNRESET) f->state->read_closed = 1;
            return err(e);
        }
        return (long)n;
    }
    if (position >= 0) {
        n = pread(f->fd, buf + offset, (size_t)count, (off_t)position);
    } else {
        n = read(f->fd, buf + offset, (size_t)count);
    }
    if (n < 0) return err(errno);
    return (long)n;
}

long sys_io_write(long token, long kind, long buf_token, long offset, long count,
                  long position) {
    sys_fdobj *f = fdobj(token, kind);
    unsigned char *buf = NULL;
    size_t buflen = 0;
    ssize_t n;
    long r;
    if (f == NULL || f->fd < 0) return err(EBADF);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (position >= 0 && f->append) return err(EINVAL);
    if (f->sock && f->state != NULL) {
        if (f->state->connecting) return err(EINPROGRESS);
        if (f->state->failed) return err(f->state->failure);
        if (f->state->write_closed) return err(EPIPE);
        if (count == 0) return 0;
        n = send(f->fd, buf + offset, (size_t)count, MSG_NOSIGNAL);
        if (n < 0) {
            int e = errno;
            if (e == EPIPE) f->state->write_closed = 1;
            return err(e);
        }
        return (long)n;
    }
    if (count == 0) return 0;
    if (position >= 0) {
        n = pwrite(f->fd, buf + offset, (size_t)count, (off_t)position);
    } else {
        n = write(f->fd, buf + offset, (size_t)count);
    }
    if (n < 0) return err(errno);
    return (long)n;
}

long sys_io_seek(long token, long kind, long offset, long origin) {
    sys_fdobj *f = fdobj(token, kind);
    int whence;
    off_t pos;
    if (f == NULL || f->fd < 0) return err(EBADF);
    if (origin == SYS_SEEK_START) whence = SEEK_SET;
    else if (origin == SYS_SEEK_CURRENT) whence = SEEK_CUR;
    else if (origin == SYS_SEEK_END) whence = SEEK_END;
    else return err(EINVAL);
    if (origin == SYS_SEEK_START && offset < 0) return err(ERANGE);
    pos = lseek(f->fd, (off_t)offset, whence);
    if (pos < 0) {
        if (errno == EINVAL || errno == EOVERFLOW) return err(ERANGE);
        return err(errno);
    }
    return (long)pos;
}

long sys_io_truncate(long token, long kind, long length) {
    sys_fdobj *f = fdobj(token, kind);
    if (f == NULL || f->fd < 0) return err(EBADF);
    if (length < 0) return err(EINVAL);
    if (ftruncate(f->fd, (off_t)length) != 0) return err(errno);
    return 1;
}

long sys_stat_get(long subject_token, long subject_kind, long dir_token,
                  long name_token, long follow, long buf_token) {
    unsigned char *out = NULL;
    size_t outlen = 0;
    long r;
    if (follow != 0 && follow != 1) return err(EINVAL);
    r = buffer_span(buf_token, &out, &outlen);
    if (r < 0) return r;
    if (outlen < (size_t)SYS_STAT_PACKET_BYTES) return err(EINVAL);
    return stat_packet(subject_token, subject_kind, dir_token, name_token, follow,
                       out, outlen);
}

long sys_file_dup(long token, long kind, long to_kind) {
    sys_fdobj *src = fdobj(token, kind);
    sys_fdobj *f;
    int fd;
    long out;
    if (src == NULL || src->fd < 0) return err(EBADF);
    if (to_kind <= 0) return err(EINVAL);
    fd = fcntl(src->fd, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    f = (sys_fdobj *)malloc(sizeof(*f));
    if (f == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    f->fd = fd;
    f->append = src->append;
    f->mode = src->mode;
    f->sock = src->sock;
    f->state = src->state;
    if (f->state != NULL) f->state->refs++;
    out = sys_reg_new_object((int)to_kind, f, release_fdobj, 0);
    if (out < 0 && f->state != NULL) {
        f->state->refs--;
        f->state = NULL;
    }
    if (out < 0) {
        (void)close(fd);
        free(f);
    }
    return out;
}

long sys_file_retag(long token, long from_kind, long to_kind) {
    /* A connection transfer to Stream invalidates its old registrations now,
     * not only when a later wait happens to filter a stale native event. */
    sys_net_forget_source(token);
    if (from_kind <= 0 || to_kind <= 0) return err(EINVAL);
    if (sys_reg_retag(token, (int)from_kind, (int)to_kind) != 1) return err(EINVAL);
    return 1;
}

/* An anchored name is exactly one ordinary component. The obo wrappers also
 * reject these; C repeats the check so a stale wrapper cannot smuggle a path. */
static long component_of(long token, const char **out) {
    const char *p = NULL;
    size_t len = 0;
    long r = path_of(token, &p, &len);
    if (r < 0) return r;
    if (len == 1 && p[0] == '.') return err(EINVAL);
    if (len == 2 && p[0] == '.' && p[1] == '.') return err(EINVAL);
    if (memchr(p, '/', len) != NULL) return err(EINVAL);
    *out = p;
    return 0;
}

long sys_dir_mkdir(long dir_token, long name_token, long mode) {
    const char *name = NULL;
    int dfd;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_MKDIR);
    if (mode < 0 || mode > 07777) return err(EINVAL);
    dfd = dirfd_of(dir_token);
    if (dfd < 0) return err(EBADF);
    r = component_of(name_token, &name);
    if (r < 0) return r;
    if (mkdirat(dfd, name, (mode_t)mode) != 0) return err(errno);
    return 1;
}

long sys_dir_unlink(long dir_token, long name_token) {
    const char *name = NULL;
    int dfd;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_UNLINK);
    dfd = dirfd_of(dir_token);
    if (dfd < 0) return err(EBADF);
    r = component_of(name_token, &name);
    if (r < 0) return r;
    if (unlinkat(dfd, name, 0) != 0) return err(errno);
    return 1;
}

long sys_dir_remove_empty(long dir_token, long name_token) {
    const char *name = NULL;
    int dfd;
    long r;
    sys_reg_bump_call(SYS_CALL_DIR_RMDIR);
    dfd = dirfd_of(dir_token);
    if (dfd < 0) return err(EBADF);
    r = component_of(name_token, &name);
    if (r < 0) return r;
    if (unlinkat(dfd, name, AT_REMOVEDIR) != 0) return err(errno);
    return 1;
}

long sys_dir_readlink(long dir_token, long name_token, long buf_token, long cap) {
    const char *name = NULL;
    unsigned char *out = NULL;
    size_t outlen = 0;
    int dfd;
    long r;
    ssize_t n;
    sys_reg_bump_call(SYS_CALL_DIR_READLINK);
    dfd = dirfd_of(dir_token);
    if (dfd < 0) return err(EBADF);
    r = component_of(name_token, &name);
    if (r < 0) return r;
    r = buffer_span(buf_token, &out, &outlen);
    if (r < 0) return r;
    if (cap < 1 || (size_t)cap > outlen) return err(ERANGE);
    n = readlinkat(dfd, name, (char *)out, (size_t)cap);
    if (n < 0) return err(errno);
    return (long)n;
}

long sys_dir_sync(long dir_token) {
    sys_fdobj *d;
    sys_reg_bump_call(SYS_CALL_DIR_SYNC);
    d = fdobj(dir_token, SYS_KIND_DIRECTORY);
    if (d == NULL || d->fd < 0) return err(EBADF);
    if (fsync(d->fd) != 0) return err(errno);
    return 1;
}

/* openat2(2) is Linux 5.6+. Define the packet locally so the build does not
 * depend on a particular userspace header version. */
struct sys_open_how {
    uint64_t flags;
    uint64_t mode;
    uint64_t resolve;
};

#ifndef RESOLVE_NO_MAGICLINKS
#define RESOLVE_NO_MAGICLINKS 0x02
#endif
#ifndef RESOLVE_BENEATH
#define RESOLVE_BENEATH 0x08
#endif

long sys_file_open_beneath_supported(void) {
    struct sys_open_how how;
    long raw;
    memset(&how, 0, sizeof(how));
    how.flags = O_RDONLY | O_CLOEXEC;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
    raw = syscall(SYS_openat2, AT_FDCWD, "", &how, sizeof(how));
    if (raw >= 0) {
        (void)close((int)raw);
        return 1;
    }
    if (errno == ENOSYS) return 0;
    return 1;
}

long sys_file_open_beneath(long dir_token, long path_token, long flags,
                           long mode) {
    const char *path = NULL;
    size_t pathlen = 0;
    sys_fdobj *f;
    struct sys_open_how how;
    int dfd;
    int oflags;
    long raw;
    long token;
    sys_reg_bump_call(SYS_CALL_OPEN_BENEATH);
    if (dir_token == 0) return err(EINVAL);
    if (mode < 0 || mode > 07777) return err(EINVAL);
    if (flags < 0 || (flags & ~(SYS_OPEN_READ | SYS_OPEN_WRITE | SYS_OPEN_CREATE |
                                SYS_OPEN_EXCLUSIVE | SYS_OPEN_TRUNCATE |
                                SYS_OPEN_APPEND | SYS_OPEN_NOFOLLOW |
                                SYS_OPEN_NONBLOCK)) != 0) {
        return err(EINVAL);
    }
    oflags = open_flags(flags);
    if (oflags < 0) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    (void)pathlen;
    dfd = dirfd_of(dir_token);
    if (dir_token != 0 && dfd < 0) return err(EBADF);
    memset(&how, 0, sizeof(how));
    how.flags = (uint64_t)oflags;
    how.mode = (flags & SYS_OPEN_CREATE) ? (uint64_t)mode : 0;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
    raw = syscall(SYS_openat2, dfd, path, &how, sizeof(how));
    if (raw < 0) return err(errno);
    f = (sys_fdobj *)malloc(sizeof(*f));
    if (f == NULL) {
        (void)close((int)raw);
        return err(ENOMEM);
    }
    f->fd = (int)raw;
    f->append = (flags & SYS_OPEN_APPEND) != 0;
    f->mode = mode;
    f->sock = 0;
    f->state = NULL;
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_fdobj, 0);
    if (token < 0) {
        (void)close(f->fd);
        free(f);
    }
    return token;
}

long sys_file_chmod(long token, long kind, long mode) {
    sys_fdobj *f;
    sys_reg_bump_call(SYS_CALL_CHMOD);
    if (mode < 0 || mode > 07777) return err(EINVAL);
    f = fdobj(token, kind);
    if (f == NULL || f->fd < 0) return err(EBADF);
    if (fchmod(f->fd, (mode_t)mode) != 0) return err(errno);
    return 1;
}

long sys_file_sync(long token, long kind, long data_only) {
    sys_fdobj *f;
    sys_reg_bump_call(SYS_CALL_SYNC);
    if (data_only != 0 && data_only != 1) return err(EINVAL);
    f = fdobj(token, kind);
    if (f == NULL || f->fd < 0) return err(EBADF);
    if ((data_only ? fdatasync(f->fd) : fsync(f->fd)) != 0) return err(errno);
    return 1;
}

long sys_file_rename(long from_dir, long old_name, long to_dir, long new_name,
                     long replace) {
    const char *oldp = NULL;
    const char *newp = NULL;
    int fdf;
    int tdf;
    long r;
    int flags;
    sys_reg_bump_call(SYS_CALL_RENAME);
    if (replace != 0 && replace != 1) return err(EINVAL);
    fdf = dirfd_of(from_dir);
    tdf = dirfd_of(to_dir);
    if (fdf < 0 || tdf < 0) return err(EBADF);
    r = component_of(old_name, &oldp);
    if (r < 0) return r;
    r = component_of(new_name, &newp);
    if (r < 0) return r;
    flags = replace ? 0 : RENAME_NOREPLACE;
#ifdef SYS_renameat2
    if (syscall(SYS_renameat2, fdf, oldp, tdf, newp, (unsigned)flags) == 0) return 1;
    /* Linux filesystems disagree on the error for "RENAME_NOREPLACE
     * unsupported". Component names are already validated, so EINVAL on the
     * no-replace path is treated as absent primitive; all other errors are
     * preserved. */
    if (errno != ENOSYS && !(errno == EINVAL && !replace)) return err(errno);
    if (!replace) return err(EOPNOTSUPP);
#else
    if (!replace) return err(EOPNOTSUPP);
#endif
    if (renameat(fdf, oldp, tdf, newp) != 0) return err(errno);
    return 1;
}

long sys_file_link(long from_dir, long old_name, long to_dir, long new_name) {
    const char *oldp = NULL;
    const char *newp = NULL;
    int fdf;
    int tdf;
    long r;
    sys_reg_bump_call(SYS_CALL_LINK);
    fdf = dirfd_of(from_dir);
    tdf = dirfd_of(to_dir);
    if (fdf < 0 || tdf < 0) return err(EBADF);
    r = component_of(old_name, &oldp);
    if (r < 0) return r;
    r = component_of(new_name, &newp);
    if (r < 0) return r;
    if (linkat(fdf, oldp, tdf, newp, 0) != 0) return err(errno);
    return 1;
}

long sys_file_symlink(long target_token, long to_dir, long name_token) {
    const char *target = NULL;
    const char *name = NULL;
    size_t len = 0;
    int tdf;
    long r;
    sys_reg_bump_call(SYS_CALL_SYMLINK);
    tdf = dirfd_of(to_dir);
    if (tdf < 0) return err(EBADF);
    r = path_of(target_token, &target, &len);
    if (r < 0) return r;
    if (len == 0) return err(EINVAL);
    r = component_of(name_token, &name);
    if (r < 0) return r;
    if (symlinkat(target, tdf, name) != 0) return err(errno);
    return 1;
}

/* ---- bounded logger ----------------------------------------------------- */

typedef struct {
    int fd;
    unsigned char *buf;
    size_t cap;
    size_t start;
    size_t end;
    long dropped;
} sys_logger;

static int release_logger(void *p) {
    sys_logger *l = (sys_logger *)p;
    int e = 0;
    if (l == NULL) return EINVAL;
    if (l->fd >= 0) {
        if (close(l->fd) != 0) e = errno;
        l->fd = -1;
    }
    free(l->buf);
    l->buf = NULL;
    free(l);
    return e;
}

static long logger_now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return -1;
    if (ts.tv_sec < 0 || ts.tv_sec > (time_t)9223372036LL) return -1;
    return (long)((int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec);
}

long sys_log_open(long which, long capacity) {
    sys_logger *l;
    int fd;
    long token;
    sys_reg_bump_call(SYS_CALL_LOG_OPEN);
    if (which < 0 || which > 2) return err(EINVAL);
    if (capacity < 1 || capacity > (16L * 1024L * 1024L)) return err(ERANGE);
    /* F_DUPFD shares the open file description with fd 0/1/2. Do not change
     * its status flags here: O_NONBLOCK would leak to the process stream and
     * survive logger close. log_drain_guarded toggles/restores only around
     * each write/poll operation. */
    fd = fcntl((int)which, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    l = (sys_logger *)calloc(1, sizeof(*l));
    if (l == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    l->buf = (unsigned char *)malloc((size_t)capacity);
    if (l->buf == NULL) {
        (void)close(fd);
        free(l);
        return err(ENOMEM);
    }
    l->fd = fd;
    l->cap = (size_t)capacity;
    token = sys_reg_new_object(SYS_KIND_LOGGER, l, release_logger, (size_t)capacity);
    if (token < 0) release_logger(l);
    return token;
}

long sys_log_emit(long token, long buf_token, long offset, long count,
                  long policy) {
    sys_logger *l;
    unsigned char *src = NULL;
    size_t srclen = 0;
    size_t used;
    long r;
    sys_reg_bump_call(SYS_CALL_LOG_EMIT);
    if (policy != 0 && policy != 1) return err(EINVAL);
    if (offset < 0 || count <= 0) return err(EINVAL);
    l = (sys_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
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

/* Write what the nonblocking sink accepts. EINTR/EAGAIN are ordinary
 * backpressure here; this is a named compound helper with a finite deadline. */
static long log_drain(sys_logger *l, int finite, long deadline) {
    while (l->start < l->end) {
        ssize_t n = write(l->fd, l->buf + l->start, l->end - l->start);
        if (n > 0) {
            l->start += (size_t)n;
            if (l->start == l->end) {
                l->start = 0;
                l->end = 0;
            }
            continue;
        }
        if (n < 0 && errno == EINTR) {
            if (finite) {
                long now = logger_now();
                if (now < 0 || now >= deadline) return 0;
            }
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd pfd;
            long now;
            int timeout;
            if (finite) {
                now = logger_now();
                if (now < 0) return err(EIO);
                if (now >= deadline) return 0;
                {
                    long left = deadline - now;
                    timeout = left > 2147483647L ? 2147483647 : (int)((left + 999999L) / 1000000L);
                }
            } else {
                timeout = -1;
            }
            pfd.fd = l->fd;
            pfd.events = POLLOUT;
            pfd.revents = 0;
            if (poll(&pfd, 1, timeout) < 0) {
                if (errno == EINTR) {
                    if (finite) {
                        long again = logger_now();
                        if (again < 0 || again >= deadline) return 0;
                    }
                    continue;
                }
                return err(errno);
            }
            continue;
        }
        return n < 0 ? err(errno) : err(EIO);
    }
    return 1;
}

/* The logger duplicate shares its open file description with the process
 * standard stream. P1b is single-threaded, so temporarily request O_NONBLOCK
 * for this one drain attempt and restore the exact prior flags before returning
 * on every path. This keeps fd 0/1/2 flags untouched before open, during idle
 * periods and after close. */
static long log_drain_guarded(sys_logger *l, int finite, long deadline) {
    int saved;
    long rc;
    int saved_errno;
    saved = fcntl(l->fd, F_GETFL);
    if (saved < 0) return err(errno);
    if (fcntl(l->fd, F_SETFL, saved | O_NONBLOCK) != 0) return err(errno);
    rc = log_drain(l, finite, deadline);
    saved_errno = errno;
    if (fcntl(l->fd, F_SETFL, saved) != 0 && rc >= 0) {
        rc = err(errno);
    }
    errno = saved_errno;
    return rc;
}

long sys_log_drain(long token, long finite, long deadline_ns) {
    sys_logger *l;
    sys_reg_bump_call(SYS_CALL_LOG_DRAIN);
    if (finite != 0 && finite != 1) return err(EINVAL);
    if (finite && deadline_ns < 0) return err(EINVAL);
    l = (sys_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    return log_drain_guarded(l, (int)finite, deadline_ns);
}

long sys_log_flush(long token) {
    sys_logger *l;
    sys_reg_bump_call(SYS_CALL_LOG_FLUSH);
    l = (sys_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    /* An immediate attempt: never wait for a writable sink. */
    return log_drain_guarded(l, 1, 0);
}

long sys_log_dropped(long token) {
    sys_logger *l = (sys_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    return l->dropped;
}

long sys_log_queued(long token) {
    sys_logger *l = (sys_logger *)sys_reg_peek(token, SYS_KIND_LOGGER);
    if (l == NULL) return err(EBADF);
    return (long)(l->end - l->start);
}

extern char **environ;

long sys_env_size(void) {
    long total = 0;
    long i;
    if (environ == NULL) return 0;
    for (i = 0; environ[i] != NULL; i++) {
        size_t n = strlen(environ[i]) + 1;
        if (n > (size_t)(0x7fffffffL - total)) return err(EOVERFLOW);
        total += (long)n;
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
    if (environ == NULL) return 0;
    for (i = 0; environ[i] != NULL; i++) {
        size_t n = strlen(environ[i]) + 1;
        if ((size_t)total > (size_t)cap || n > (size_t)cap - (size_t)total) {
            return err(ERANGE);
        }
        memcpy(out + total, environ[i], n);
        total += (long)n;
    }
    return total;
}

long sys_clock_monotonic_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return err(errno);
    if (ts.tv_sec < 0 || ts.tv_sec > (time_t)9223372036LL) return err(EOVERFLOW);
    if (ts.tv_sec == (time_t)9223372036LL && ts.tv_nsec > 854775807L) return err(EOVERFLOW);
    return (long)((int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec);
}

long sys_clock_realtime_sec(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) return err(errno);
    if (sizeof(time_t) > sizeof(long) && (int64_t)ts.tv_sec > (int64_t)0x7fffffffffffffffLL) {
        return err(EOVERFLOW);
    }
    return (long)ts.tv_sec;
}

long sys_clock_realtime_nsec(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) return err(errno);
    if (ts.tv_nsec < 0 || ts.tv_nsec > 999999999L) return err(EIO);
    return (long)ts.tv_nsec;
}

long sys_sleep_until(long monotonic_ns) {
    struct timespec ts;
    int rc;
    if (monotonic_ns < 0) return err(EINVAL);
    ts.tv_sec = (time_t)(monotonic_ns / 1000000000LL);
    ts.tv_nsec = (long)(monotonic_ns % 1000000000LL);
    rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
    if (rc != 0) return err(rc);
    return 1;
}

long sys_random(long buf_token, long offset, long count, long blocking) {
    unsigned char *buf = NULL;
    size_t buflen = 0;
    ssize_t n;
    int flags;
    long r;
    if (blocking != 0 && blocking != 1) return err(EINVAL);
    if (offset < 0 || count < 0) return err(EINVAL);
    r = buffer_span(buf_token, &buf, &buflen);
    if (r < 0) return r;
    if ((size_t)offset > buflen || (size_t)count > buflen - (size_t)offset) {
        return err(ERANGE);
    }
    if (count == 0) return 0;
    flags = blocking ? 0 : GRND_NONBLOCK;
    n = getrandom(buf + offset, (size_t)count, flags);
    if (n < 0) return err(errno);
    return (long)n;
}

long sys_stream_standard(long which) {
    sys_fdobj *f;
    int fd;
    long token;
    if (which < 0 || which > 2) return err(EINVAL);
    fd = fcntl((int)which, F_DUPFD_CLOEXEC, 0);
    if (fd < 0) return err(errno);
    f = (sys_fdobj *)malloc(sizeof(*f));
    if (f == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    f->fd = fd;
    f->append = 0;
    f->mode = 0;
    f->sock = 0;
    f->state = NULL;
    token = sys_reg_new_object(SYS_KIND_STREAM, f, release_fdobj, 0);
    if (token < 0) {
        (void)close(fd);
        free(f);
    }
    return token;
}

long sys_res_new(long kind, long a, long b) {
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        return sys_reg_new_buffer(a);
    }
    if (kind == SYS_KIND_REACTOR) {
        return sys_net_reactor_new(a, b);
    }
    if (kind == SYS_KIND_CANCELLATION) {
        (void)a;
        (void)b;
        return sys_net_cancel_new();
    }
    if (kind == SYS_KIND_SPAWN_PLAN) {
        (void)a;
        (void)b;
        return sys_proc_plan_new();
    }
    if (kind == SYS_KIND_TEST_RESOURCE || kind == SYS_KIND_TEST_RESOURCE_ALT) {
        int fd;
        sys_fdobj *f;
        long token;
        fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (fd < 0) return err(errno);
        f = (sys_fdobj *)malloc(sizeof(*f));
        if (f == NULL) {
            (void)close(fd);
            return err(ENOMEM);
        }
        f->fd = fd;
        f->append = 0;
        f->mode = 0;
        f->sock = 0;
        f->state = NULL;
        token = sys_reg_new_object((int)kind, f, release_fdobj, 0);
        if (token < 0) {
            (void)close(fd);
            free(f);
        }
        return token;
    }
    return err(EINVAL);
}

long sys_res_op(long token, long kind, long op, long a, long b, long c) {
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
        sys_fdobj *f = fdobj(token, kind);
        if (f == NULL) return err(EINVAL);
        if (op == SYS_OP_TEST_WRITE) {
            uint64_t v;
            ssize_t n;
            if (a <= 0) return err(EINVAL);
            v = (uint64_t)a;
            n = write(f->fd, &v, sizeof(v));
            if (n < 0) return err(errno);
            if (n != (ssize_t)sizeof(v)) return err(EIO);
            return 1;
        }
        if (op == SYS_OP_TEST_READ) {
            uint64_t v = 0;
            ssize_t n = read(f->fd, &v, sizeof(v));
            if (n < 0) return err(errno);
            if (n != (ssize_t)sizeof(v)) return err(EIO);
            if (v > (uint64_t)0x7fffffffffffffffULL) return err(EOVERFLOW);
            return (long)v;
        }
        /* Progress is a fake-backend script hook, not a real eventfd op. */
        return err(EOPNOTSUPP);
    }
    return err(EINVAL);
}

long sys_res_close(long token, long kind) {
    void *ptr = NULL;
    int (*release)(void *) = NULL;
    int e;
    sys_reg_bump_call(SYS_CALL_CLOSE);
    sys_net_forget_source(token);
    if (!sys_reg_retire(token, kind, &ptr, &release)) return 0;
    e = release != NULL ? release(ptr) : 0;
    if (e != 0) {
        sys_reg_add_cleanup_failure();
        return err(e);
    }
    return 1;
}
