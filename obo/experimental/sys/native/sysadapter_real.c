/* Linux P1a backend: files, directories, stat snapshots, clocks and getrandom.
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

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/eventfd.h>
#include <time.h>
#include <unistd.h>

#ifndef AT_EMPTY_PATH
#define AT_EMPTY_PATH 0x1000
#endif

typedef struct {
    int fd;
    int append;
    long mode;
} sys_fdobj;

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
    token = sys_reg_new_object(SYS_KIND_FILE, f, release_fdobj, 0);
    if (token < 0) {
        (void)close(fd);
        free(f);
    }
    return token;
}

long sys_dir_open(long path_token, long follow) {
    const char *path = NULL;
    size_t pathlen = 0;
    sys_fdobj *d;
    int fd;
    int flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC;
    long token;
    if (follow != 0 && follow != 1) return err(EINVAL);
    {
        long pr = path_of(path_token, &path, &pathlen);
        if (pr < 0) return pr;
    }
    (void)pathlen;
    if (!follow) flags |= O_NOFOLLOW;
    fd = openat(AT_FDCWD, path, flags);
    if (fd < 0) return err(errno);
    d = (sys_fdobj *)malloc(sizeof(*d));
    if (d == NULL) {
        (void)close(fd);
        return err(ENOMEM);
    }
    d->fd = fd;
    d->append = 0;
    d->mode = 0;
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
    if (count == 0) return 0;
    if (position >= 0 && f->append) return err(EINVAL);
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
    pos = lseek(f->fd, (off_t)offset, whence);
    if (pos < 0) return err(errno);
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
    out = sys_reg_new_object((int)to_kind, f, release_fdobj, 0);
    if (out < 0) {
        (void)close(fd);
        free(f);
    }
    return out;
}

long sys_file_retag(long token, long from_kind, long to_kind) {
    if (from_kind <= 0 || to_kind <= 0) return err(EINVAL);
    if (sys_reg_retag(token, (int)from_kind, (int)to_kind) != 1) return err(EINVAL);
    return 1;
}

long sys_clock_monotonic_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return err(errno);
    if (ts.tv_sec < 0 || ts.tv_sec > (time_t)9223372036LL) return err(EOVERFLOW);
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
    token = sys_reg_new_object(SYS_KIND_STREAM, f, release_fdobj, 0);
    if (token < 0) {
        (void)close(fd);
        free(f);
    }
    return token;
}

long sys_res_new(long kind, long a, long b) {
    (void)b;
    sys_reg_bump_call(SYS_CALL_NEW);
    if (kind == SYS_KIND_BYTE_BUFFER) {
        return sys_reg_new_buffer(a);
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
    if (!sys_reg_retire(token, kind, &ptr, &release)) return 0;
    e = release != NULL ? release(ptr) : 0;
    if (e != 0) {
        sys_reg_add_cleanup_failure();
        return err(e);
    }
    return 1;
}
