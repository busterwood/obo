#!/usr/bin/env python3
"""Mutation testing for the P3/P5a/P6a metadata features: the runtime/entry.S
slots and the compiler-source builtins.

    python3 obo/tests/mutate_meta.py [pattern]

Two independent runners, because a mutated runtime cannot kill a compiler-source
mutant (the compiler has already made the decisions before the runtime runs) and
a mutated compiler cannot test the runtime's syscall behaviour:

  runtime/entry.S runner   for each runtime mutant, copies runtime/runtime.c
                           (and entry.S for the slot mutants) into a private
                           directory, writes the mutant text (asserting the
                           pattern matched exactly once and the text changed),
                           builds runtime_text.bin/runtime_data.bin with the
                           Makefile flags, and judges the mutated runtime with
                           tests/rt_meta_test.c (built against the mutant
                           runtime.c) and the fs-level program corpus from
                           tests/meta/*.obx, compiled against the mutant runtime
                           with a private --sym-dir. Each entry.S mutant also
                           runs tests/run/io_basic.obx (a compiled program, so
                           its calls go through the entry.S slot table), and it
                           runs before the other cases, so a wrong slot order
                           cannot pass. A mutant is caught when any case's exit
                           code or output differs from the baseline.

  compiler runner          for each compiler mutant, writes src/obo.obo with the
                           mutation to a private file (exactly one match; text
                           differs), builds that source with the unmutated
                           compiler into a private compiler executable, and uses
                           it as the compiler for the corpus with a private
                           sym-dir per case: every tests/errors/{meta,env,term}.obx
                           case must still be refused with its expected
                           diagnostic (the marker line and text), and
                           tests/run/{meta,env,term}.obx plus tests/meta/*.obx
                           must compile, run and produce the baseline outcomes. The
                           fd-materialisation case is tests/run/meta.obx's "pin
                           dir inside" line, whose max argument (changeGfd())
                           changes the global fd that the call's first argument
                           reads.

Counts are measured, never fixed in advance. A genuinely equivalent mutant is
listed in EQUIVALENT_RUNTIME or EQUIVALENT_COMPILER with its reason and excluded
from the caught count, exactly as mutate_fork.py records its equivalents; a
missed mutant that is not listed fails the run. Stale patterns (a match count
other than one) and build failures fail it too.

    MUTATE_META_JOBS=N  run up to N mutants at once (default 4)
"""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402
import meta_test as mt  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK
CFLAGS = mt.CFLAGS
RUNTIME = os.path.join(FORK, 'runtime', 'runtime.c')
ENTRY = os.path.join(FORK, 'runtime', 'entry.S')
SRC = os.path.join(FORK, 'src', 'obo.obo')
ERRFILES = [os.path.join(FORK, 'tests', 'errors', 'meta.obx'),
            os.path.join(FORK, 'tests', 'errors', 'env.obx'),
            os.path.join(FORK, 'tests', 'errors', 'term.obx')]
RUNFILES = [os.path.join(FORK, 'tests', 'run', 'meta.obx'),
            os.path.join(FORK, 'tests', 'run', 'env.obx'),
            os.path.join(FORK, 'tests', 'run', 'term.obx')]
TMP = tempfile.mkdtemp(prefix='obo-mutate-meta-')

# ---------------------------------------------------------------- runtime mutants
# ("what is wrong", [ (file, old, new), ... ]); 'runtime' and 'entry' name the file
RUNTIME_MUTANTS = [
    ('statPath does not follow symlinks',
     [('runtime', '    return do_stat(path, 0, "cannot stat ", &fault_stat, descErr, descOk);\n',
       '    return do_stat(path, AT_SYMLINK_NOFOLLOW, "cannot stat ", &fault_stat, descErr, descOk);\n')]),
    ('lstatPath follows symlinks',
     [('runtime', '    return do_stat(path, AT_SYMLINK_NOFOLLOW, "cannot lstat ", &fault_lstat, descErr, descOk);\n',
       '    return do_stat(path, 0, "cannot lstat ", &fault_lstat, descErr, descOk);\n')]),
    ('mode and size swapped',
     [('runtime',
       '    v[0]  = (u64)(*(unsigned int *)((char *)st + 24));             /* mode (st_mode, u32) */\n'
       '    v[1]  = (u64)(*(i64 *)((char *)st + 48));                      /* size (signed) */\n',
       '    v[0]  = (u64)(*(i64 *)((char *)st + 48));                      /* size (signed) */\n'
       '    v[1]  = (u64)(*(unsigned int *)((char *)st + 24));             /* mode (st_mode, u32) */\n')]),
    ('uid and gid swapped',
     [('runtime',
       '    v[4]  = (u64)(*(unsigned int *)((char *)st + 28));             /* uid */\n'
       '    v[5]  = (u64)(*(unsigned int *)((char *)st + 32));             /* gid */\n',
       '    v[4]  = (u64)(*(unsigned int *)((char *)st + 32));             /* gid */\n'
       '    v[5]  = (u64)(*(unsigned int *)((char *)st + 28));             /* uid */\n')]),
    ('mtime and mtime_ns swapped',
     [('runtime',
       '    v[2]  = (u64)(*(i64 *)((char *)st + 88));                      /* mtime seconds */\n'
       '    v[3]  = (u64)(*(i64 *)((char *)st + 96));                      /* mtime nanoseconds */\n',
       '    v[2]  = (u64)(*(i64 *)((char *)st + 96));                      /* mtime nanoseconds */\n'
       '    v[3]  = (u64)(*(i64 *)((char *)st + 88));                      /* mtime seconds */\n')]),
    ('short dir read treated as EOF',
     [('runtime',
       '    a->len = (u64)r;                                               /* whole records; the caller validates */\n'
       '    return ok_cell(descOk, (u64)a);\n',
       '    if ((u64)r < (u64)max) { rt_free((u64)a); return ok_cell(descOk, 0); }\n'
       '    a->len = (u64)r;\n'
       '    return ok_cell(descOk, (u64)a);\n')]),
    ('EOF is an empty array, not nil',
     [('runtime',
       '    if (r < 0) { rt_free((u64)a); return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), -r); }\n'
       '    if (r == 0) { rt_free((u64)a); return ok_cell(descOk, 0); }    /* EOF is nil, never an empty array */\n',
       '    if (r < 0) { rt_free((u64)a); return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), -r); }\n'
       '    if (r == 0) { a->len = 0; return ok_cell(descOk, (u64)a); }\n')]),
    ('no 1 MiB directory cap',
     [('runtime',
       '    if (max > MAX_DIR_BUF) return err_cell(descErr, iomsg_lit("readDirFd count is too large"), 22);\n',
       '    if (0) return err_cell(descErr, iomsg_lit("readDirFd count is too large"), 22);\n')]),
    ('no fd-table check in readDirFd',
     [('runtime',
       '    if (fd < 0) return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), 9);     /* EBADF */\n'
       '    if (fd >= 0 && fd < 1024 && fdstate[fd] == 2) {\n',
       '    if (fd < 0) return err_cell(descErr, iomsg_num("cannot read dir fd ", fd), 9);     /* EBADF */\n'
       '    if (0) {\n')]),
    ('readLinkPath truncates at 4094',
     [('runtime',
       '        if (fault_link) { fault_link = 0; r = -4; } else r = sys3(89, (i64)p, (i64)buf, 4096);\n',
       '        if (fault_link) { fault_link = 0; r = -4; } else r = sys3(89, (i64)p, (i64)buf, 4094);\n')]),
    ('readLinkPath accepts a full 4096-byte target',
     [('runtime',
       '    if (r == 4096) return err_cell(descErr, iomsg_str("cannot readlink ", path), 36);  /* ENAMETOOLONG */\n',
       '    if (0) return err_cell(descErr, iomsg_str("cannot readlink ", path), 36);\n')]),
    ('getdents64 EINTR not retried',
     [('runtime',
       '        if (fault_dir) { fault_dir = 0; r = -4; } else r = sys3(217, fd, (i64)((char *)a->data + 8), max);\n'
       '        if (r != -4) break;                                        /* EINTR retried */\n',
       '        if (fault_dir) { fault_dir = 0; r = -4; } else r = sys3(217, fd, (i64)((char *)a->data + 8), max);\n'
       '        break;\n')]),
    ('newfstatat EINTR not retried',
     [('runtime',
       '        if (*fault) { *fault = 0; r = -4; } else r = sys4(262, AT_FDCWD, (i64)p, (i64)st, flags);\n'
       '        if (r != -4) break;                                        /* EINTR retried */\n',
       '        if (*fault) { *fault = 0; r = -4; } else r = sys4(262, AT_FDCWD, (i64)p, (i64)st, flags);\n'
       '        break;\n')]),
    ('readlink EINTR not retried',
     [('runtime',
       '        if (fault_link) { fault_link = 0; r = -4; } else r = sys3(89, (i64)p, (i64)buf, 4096);\n'
       '        if (r != -4) break;                                        /* EINTR retried */\n',
       '        if (fault_link) { fault_link = 0; r = -4; } else r = sys3(89, (i64)p, (i64)buf, 4096);\n'
       '        break;\n')]),
    ('sys4 fourth argument is not in r10',
     [('runtime', '    register i64 r10 __asm__("r10") = d;\n', '    i64 r10 = d;\n')]),
    ('entry.S swaps slots 33 and 34 (both sequences)',
     [('entry', '    slot rt_read_dir_fd\n    slot rt_stat_path\n',
       '    slot rt_stat_path\n    slot rt_read_dir_fd\n'),
      ('entry', '    check rt_read_dir_fd\n    check rt_stat_path\n',
       '    check rt_stat_path\n    check rt_read_dir_fd\n')]),
    ('entry.S swaps slots 32 and 33 (both sequences)',
     [('entry', '    slot rt_close_fd\n    slot rt_read_dir_fd\n',
       '    slot rt_read_dir_fd\n    slot rt_close_fd\n'),
      ('entry', '    check rt_close_fd\n    check rt_read_dir_fd\n',
       '    check rt_read_dir_fd\n    check rt_close_fd\n')]),
    # P5a: isatty and getenv
    ('isatty result inverted',
     [('runtime', '    return r == 0;\n}\n\n/* terminalWidth(fd):',
       '    return r != 0;\n}\n\n/* terminalWidth(fd):')]),
    ('isatty true for every non-negative fd',
     [('runtime', '    return r == 0;\n}\n\n/* terminalWidth(fd):',
       '    return 1;\n}\n\n/* terminalWidth(fd):')]),
    ('isatty negative fd is true',
     [('runtime', '    if (fd < 0) return 0;\n    u64 t[8];',
       '    if (fd < 0) return 1;\n    u64 t[8];')]),
    ('isatty ioctl EINTR not retried',
     [('runtime',
       '        if (fault_tty) { fault_tty = 0; r = -4; } else r = sys3(16, fd, 0x5401, (i64)t);\n'
       '        if (r != -4) break;                                        /* EINTR retried */\n',
       '        if (fault_tty) { fault_tty = 0; r = -4; } else r = sys3(16, fd, 0x5401, (i64)t);\n'
       '        break;\n')]),
    ('env_base is one word short',
     [('runtime', '    env_base = sbase + 8 * (argc + 2);\n',
       '    env_base = sbase + 8 * (argc + 1);\n')]),
    ('getenv empty name not rejected',
     [('runtime', '    if (n == 0) return 0;\n    for (u64 i = 0; i < n; i++) {\n',
       '    if (0) return 0;\n    for (u64 i = 0; i < n; i++) {\n')]),
    ("getenv '=' in the name not rejected",
     [('runtime', "        if (!c || c == '=') return 0;\n",
       '        if (!c) return 0;\n')]),
    ('getenv embedded NUL not rejected',
     [('runtime', "        if (!c || c == '=') return 0;\n",
       "        if (c == '=') return 0;\n")]),
    ("getenv matches without the entry's '=' terminator",
     [('runtime', "        if (i != n || e[n] != '=') continue;\n",
       '        if (i != n) continue;\n')]),
    ('getenv skips the first environment entry',
     [('runtime', '    for (char **env = (char **)env_base; *env; env++) {\n        char *e = *env;\n',
       '    for (char **env = (char **)env_base + 1; *env; env++) {\n        char *e = *env;\n')]),
    ('getenv does not copy the value',
     [('runtime', '        memcpy((char *)a->data + 8, e + n + 1, vn);\n',
       '        (void)vn;\n')]),
    ('getenv returns nil for an empty value',
     [('runtime', '        Array *a = (Array *)rt_alloc_array((i64)vn, 4);\n        memcpy(',
       '        if (vn == 0) return 0;\n        Array *a = (Array *)rt_alloc_array((i64)vn, 4);\n        memcpy(')]),
    ('entry.S swaps slots 36 and 37 (both sequences)',
     [('entry', '    slot rt_read_link\n    slot rt_isatty\n',
       '    slot rt_isatty\n    slot rt_read_link\n'),
      ('entry', '    check rt_read_link\n    check rt_isatty\n',
       '    check rt_isatty\n    check rt_read_link\n')]),
    ('entry.S swaps slots 37 and 38 (both sequences)',
     [('entry', '    slot rt_isatty\n    slot rt_getenv\n',
       '    slot rt_getenv\n    slot rt_isatty\n'),
      ('entry', '    check rt_isatty\n    check rt_getenv\n',
       '    check rt_getenv\n    check rt_isatty\n')]),
    # P6a: terminalWidth
    ('terminalWidth returns the row count',
     [('runtime',
       '    return (i64)ws[1];                                             /* ws_col; 0 is unknown */\n',
       '    return (i64)ws[0];                                             /* ws_col; 0 is unknown */\n')]),
    ('terminalWidth always returns 80',
     [('runtime',
       '    return (i64)ws[1];                                             /* ws_col; 0 is unknown */\n',
       '    return 80;                                                     /* ws_col; 0 is unknown */\n')]),
    ('terminalWidth is off by one',
     [('runtime',
       '    return (i64)ws[1];                                             /* ws_col; 0 is unknown */\n',
       '    return (i64)ws[1] + 1;                                         /* ws_col; 0 is unknown */\n')]),
    ('terminalWidth uses TIOCSWINSZ instead of TIOCGWINSZ',
     [('runtime',
       '        if (fault_winsz) { fault_winsz = 0; r = -4; } else r = sys3(16, fd, 0x5413, (i64)ws);\n',
       '        if (fault_winsz) { fault_winsz = 0; r = -4; } else r = sys3(16, fd, 0x5414, (i64)ws);\n')]),
    ('terminalWidth negative fd is 1',
     [('runtime', 'i64 rt_term_width(i64 fd) {\n    if (fd < 0) return 0;\n',
       'i64 rt_term_width(i64 fd) {\n    if (fd < 0) return 1;\n')]),
    ('terminalWidth ioctl EINTR not retried',
     [('runtime',
       '        if (fault_winsz) { fault_winsz = 0; r = -4; } else r = sys3(16, fd, 0x5413, (i64)ws);\n'
       '        if (r != -4) break;                                        /* EINTR retried */\n',
       '        if (fault_winsz) { fault_winsz = 0; r = -4; } else r = sys3(16, fd, 0x5413, (i64)ws);\n'
       '        break;\n')]),
    ('entry.S swaps slots 38 and 39 (both sequences)',
     [('entry', '    slot rt_getenv\n    slot rt_term_width\n',
       '    slot rt_term_width\n    slot rt_getenv\n'),
      ('entry', '    check rt_getenv\n    check rt_term_width\n',
       '    check rt_term_width\n    check rt_getenv\n')]),
]

# each field offset shifted by one slot (+8): a wrong array position
_FIELD_LINES = [
    ('mode', 24, 32), ('size', 48, 56), ('mtime', 88, 96), ('mtime_ns', 96, 104),
    ('uid', 28, 36), ('gid', 32, 40), ('nlink', 16, 24), ('ino', 8, 16),
    ('dev', 0, 8), ('rdev', 40, 48), ('blksize', 56, 64), ('blocks', 64, 72),
    ('atime', 72, 80), ('ctime', 104, 112),
]
_FIELD_TEXT = [
    '    v[0]  = (u64)(*(unsigned int *)((char *)st + 24));             /* mode (st_mode, u32) */\n',
    '    v[1]  = (u64)(*(i64 *)((char *)st + 48));                      /* size (signed) */\n',
    '    v[2]  = (u64)(*(i64 *)((char *)st + 88));                      /* mtime seconds */\n',
    '    v[3]  = (u64)(*(i64 *)((char *)st + 96));                      /* mtime nanoseconds */\n',
    '    v[4]  = (u64)(*(unsigned int *)((char *)st + 28));             /* uid */\n',
    '    v[5]  = (u64)(*(unsigned int *)((char *)st + 32));             /* gid */\n',
    '    v[6]  = *(u64 *)((char *)st + 16);                             /* nlink */\n',
    '    v[7]  = *(u64 *)((char *)st + 8);                              /* ino */\n',
    '    v[8]  = *(u64 *)((char *)st + 0);                              /* dev */\n',
    '    v[9]  = *(u64 *)((char *)st + 40);                             /* rdev */\n',
    '    v[10] = (u64)(*(i64 *)((char *)st + 56));                      /* blksize */\n',
    '    v[11] = (u64)(*(i64 *)((char *)st + 64));                      /* blocks */\n',
    '    v[12] = (u64)(*(i64 *)((char *)st + 72));                      /* atime seconds */\n',
    '    v[13] = (u64)(*(i64 *)((char *)st + 104));                     /* ctime seconds */\n',
]
for _i, (_fname, _old, _new) in enumerate(_FIELD_LINES):
    RUNTIME_MUTANTS.append((
        f'{_fname} field reads the next slot',
        [('runtime', _FIELD_TEXT[_i],
          _FIELD_TEXT[_i].replace(f'+ {_old})', f'+ {_new})', 1))]))

# A 4096-byte symlink target cannot be created on Linux (the kernel's limit is
# 4095 bytes), so the runtime's r == 4096 branch is unreachable on this host and
# that mutant is genuinely equivalent; the 4094-buffer mutant still proves a
# truncation is observable.
EQUIVALENT_RUNTIME = {
    'readLinkPath accepts a full 4096-byte target':
        'no Linux filesystem can hold a 4096-byte symlink target (the kernel '
        'maximum is 4095), so the ENAMETOOLONG branch has no input; the truncating '
        'mutant is killed by the 4095-byte target instead',
    'getenv embedded NUL not rejected':
        'an environment entry is NUL-terminated and cannot hold a NUL in its '
        'name, so the byte comparison can never match a name past an embedded '
        'NUL; the rule is defence in depth and both scans return nil',
}

# ---------------------------------------------------------------- compiler mutants
COMPILER_MUTANTS = [
    ('statPath/lstatPath path type check removed',
     [('        if p.typ.kind != "string" { error(name + " needs a string path, not " + tname(p.typ)) }\n'
       '        p = pinStr(p)\n        expect(T_RPAREN, "")\n'
       '        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tInt)) }\n',
       '        if false { error(name + " needs a string path, not " + tname(p.typ)) }\n'
       '        p = pinStr(p)\n        expect(T_RPAREN, "")\n'
       '        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tInt)) }\n')]),
    ('readLinkPath path type check removed',
     [('        if p.typ.kind != "string" { error("readLinkPath needs a string path, not " + tname(p.typ)) }\n',
       '        if false { error("readLinkPath needs a string path, not " + tname(p.typ)) }\n')]),
    ('readDirFd fd not materialised',
     [('        if !isInt(fd.typ) { error("readDirFd needs an int file descriptor, not " + tname(fd.typ)) }\n'
       '        fd = materialise(fd)\n',
       '        if !isInt(fd.typ) { error("readDirFd needs an int file descriptor, not " + tname(fd.typ)) }\n')]),
    ('statPath/lstatPath path not pinned',
     [('        p = pinStr(p)\n        expect(T_RPAREN, "")\n'
       '        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tInt)) }\n',
       '        p = materialise(p)\n        expect(T_RPAREN, "")\n'
       '        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tInt)) }\n')]),
    ('readDirFd payload is []byte, not ?[]byte',
     [('        return resultCall4(RT_READ_DIR_FD, fd, n, derived("opt", derived("array", tByte)))\n',
       '        return resultCall4(RT_READ_DIR_FD, fd, n, derived("array", tByte))\n')]),
    ('statPath payload is []byte, not []int',
     [('        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tInt)) }\n',
       '        if name == "statPath" { return resultCall3(RT_STAT_PATH, p, derived("array", tByte)) }\n')]),
    ('readLinkPath payload is []int, not []byte',
     [('        return resultCall3(RT_READ_LINK_PATH, p, derived("array", tByte))\n',
       '        return resultCall3(RT_READ_LINK_PATH, p, derived("array", tInt))\n')]),
    ('statPath uses readDirFd\'s slot',
     [('const RT_STAT_PATH = 34\n', 'const RT_STAT_PATH = 33\n')]),
    ('readDirFd constant max cap check removed',
     [('        if n.mode == "const" && n.val > 1048576 { error("readDirFd count is too large") }\n',
       '        if false { error("readDirFd count is too large") }\n')]),
    ('universe omits statPath',
     [(' readDirFd statPath lstatPath readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr isNaN', ' readDirFd lstatPath readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr isNaN')]),
    ('callExempt omits readDirFd',
     [('closeFile readDirFd statPath lstatPath readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr take handle',
       'closeFile statPath lstatPath readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr take handle')]),
    ('plausible omits the metadata names',
     [('        if wordAt(q, e, "statPath") || wordAt(q, e, "lstatPath") || wordAt(q, e, "readLinkPath") { return false }\n',
       '')]),
    # P5a: isatty and getenv
    ('universe omits isatty',
     [(' isatty getenv terminalWidth sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")', ' getenv terminalWidth sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")')]),
    ('universe omits getenv',
     [(' isatty getenv terminalWidth sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")', ' isatty terminalWidth sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")')]),
    ('callExempt omits getenv',
     [('readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr take',
       'readLinkPath isatty terminalWidth sleepMs stdin stdout stderr take')]),
    ('plausible omits getenv',
     [('        if wordAt(q, e, "getenv") { return false }        // ?[]byte, not a scalar; isatty\'s bool is a safe guess\n',
       '')]),
    ('isatty fd type check removed',
     [('        if !isInt(fd.typ) { error("isatty needs an int file descriptor, not " + tname(fd.typ)) }\n',
       '        if false { error("isatty needs an int file descriptor, not " + tname(fd.typ)) }\n')]),
    ('getenv name type check removed',
     [('        if p.typ.kind != "string" { error("getenv needs a string name, not " + tname(p.typ)) }\n',
       '        if false { error("getenv needs a string name, not " + tname(p.typ)) }\n')]),
    ('isatty result is int, not bool',
     [('        return rtCall1(RT_ISATTY, fd, tBool)\n',
       '        return rtCall1(RT_ISATTY, fd, tInt)\n')]),
    ('getenv payload is []byte, not ?[]byte',
     [('        return rtCall1(RT_GETENV, p, derived("opt", derived("array", tByte)))\n',
       '        return rtCall1(RT_GETENV, p, derived("array", tByte))\n')]),
    ('isatty uses getenv\'s slot',
     [('const RT_ISATTY = 37\n', 'const RT_ISATTY = 38\n')]),
    ('getenv uses isatty\'s slot',
     [('const RT_GETENV = 38\n', 'const RT_GETENV = 37\n')]),
    ('isatty fd not materialised',
     [('        fd = materialise(fd)\n        expect(T_RPAREN, "")\n        return rtCall1(RT_ISATTY, fd, tBool)\n',
       '        expect(T_RPAREN, "")\n        return rtCall1(RT_ISATTY, fd, tBool)\n')]),
    ('getenv name not pinned',
     [('        p = pinStr(p)\n        expect(T_RPAREN, "")\n        return rtCall1(RT_GETENV, p, derived("opt", derived("array", tByte)))\n',
       '        p = materialise(p)\n        expect(T_RPAREN, "")\n        return rtCall1(RT_GETENV, p, derived("opt", derived("array", tByte)))\n')]),
    # P6a: terminalWidth
    ('universe omits terminalWidth',
     [(' isatty getenv terminalWidth sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")',
       ' isatty getenv sleepMs stdin stdout stderr isNaN isInf signbit floatBits parseFloat __cinterop")')]),
    ('callExempt omits terminalWidth',
     [('readLinkPath isatty getenv terminalWidth sleepMs stdin stdout stderr take',
       'readLinkPath isatty getenv sleepMs stdin stdout stderr take')]),
    ('terminalWidth fd type check removed',
     [('        if !isInt(fd.typ) { error("terminalWidth needs an int file descriptor, not " + tname(fd.typ)) }\n',
       '        if false { error("terminalWidth needs an int file descriptor, not " + tname(fd.typ)) }\n')]),
    ('terminalWidth fd not materialised',
     [('        fd = materialise(fd)\n        expect(T_RPAREN, "")\n        return rtCall1(RT_TERM_WIDTH, fd, tInt)\n',
       '        expect(T_RPAREN, "")\n        return rtCall1(RT_TERM_WIDTH, fd, tInt)\n')]),
    ('terminalWidth result is bool, not int',
     [('        return rtCall1(RT_TERM_WIDTH, fd, tInt)\n',
       '        return rtCall1(RT_TERM_WIDTH, fd, tBool)\n')]),
    ('terminalWidth uses getenv\'s slot',
     [('const RT_TERM_WIDTH = 39\n', 'const RT_TERM_WIDTH = 38\n')]),
]

EQUIVALENT_COMPILER = {
    'statPath/lstatPath path not pinned':
        'a single argument has no later-argument hazard, so uniform pinStr is not '
        'observable; materialise reads the same local at the same point',
    'callExempt omits readDirFd':
        'callExempt only feeds the register-allocation pre-scan weighting; a wrong '
        'guess costs a home, not correctness',
    'plausible omits the metadata names':
        'plausible only guesses which locals deserve a register; a wrong guess '
        'costs a register, not correctness',
    'callExempt omits getenv':
        'callExempt only feeds the register-allocation pre-scan weighting; a '
        'wrong guess costs a home, not correctness',
    'plausible omits getenv':
        'plausible only guesses which locals deserve a register; a wrong guess '
        'costs a register, not correctness',
    'isatty fd not materialised':
        'a single argument has no later-argument hazard, and rtCall loads a memory '
        'operand itself, so the same descriptor is read at the same point',
    'getenv name not pinned':
        'a single argument has no later-argument hazard; materialise and pinStr '
        'read the same local at the same point',
    'callExempt omits terminalWidth':
        'callExempt only feeds the register-allocation pre-scan weighting; a '
        'wrong guess costs a home, not correctness',
    'terminalWidth fd not materialised':
        'a single argument has no later-argument hazard, and rtCall loads a '
        'memory operand itself, so the same descriptor is read at the same point',
}


class Broken(Exception):
    pass


def env_jobs(name, default):
    v = os.environ.get(name)
    if not v:
        return default
    try:
        n = int(v)
    except ValueError:
        raise SystemExit(f"{name} must be a positive integer")
    if n <= 0:
        raise SystemExit(f"{name} must be a positive integer")
    return n


def build_runtime(rt_text, entry_text, work):
    """runtime_text.bin/runtime_data.bin from the given texts, Makefile flags."""
    rt = os.path.join(work, 'rt')
    os.makedirs(rt, exist_ok=True)
    with open(os.path.join(rt, 'runtime.c'), 'w') as f:
        f.write(rt_text)
    with open(os.path.join(rt, 'entry.S'), 'w') as f:
        f.write(entry_text)
    shutil.copy(os.path.join(FORK, 'runtime', 'rt.ld'), rt)
    shutil.copy(os.path.join(FORK, 'runtime', 'decimal24_core.inc'), rt)
    subprocess.run(['gcc', *CFLAGS, '-c', 'runtime.c', '-o', 'runtime.o'], cwd=rt,
                   check=True, capture_output=True)
    subprocess.run(['gcc', '-c', 'entry.S', '-o', 'entry.o'], cwd=rt,
                   check=True, capture_output=True)
    subprocess.run(['ld', '-T', 'rt.ld', '-o', 'runtime.elf', 'entry.o', 'runtime.o'],
                   cwd=rt, check=True, capture_output=True)
    subprocess.run(['objcopy', '-O', 'binary', '-j', '.text', 'runtime.elf',
                    'runtime_text.bin'], cwd=rt, check=True, capture_output=True)
    subprocess.run(['objcopy', '-O', 'binary', '-j', '.data', '--set-section-flags',
                    '.data=alloc,load,contents', 'runtime.elf', 'runtime_data.bin'],
                   cwd=rt, check=True, capture_output=True)
    return rt


def build_harness(rt_dir, source, name):
    """Link a tests/*.c harness that includes ../runtime/runtime.c against the
    runtime in rt_dir (the include is rewritten to the private copy)."""
    text = open(source).read()
    old = '#include "../runtime/runtime.c"'
    if text.count(old) != 1:
        raise Broken(f"{source}: the runtime include was not found once")
    path = os.path.join(rt_dir, name + '.c')
    with open(path, 'w') as f:
        f.write(text.replace(old, '#include "runtime.c"', 1))
    obj = os.path.join(rt_dir, name + '.o')
    exe = os.path.join(rt_dir, name)
    subprocess.run(['gcc', *CFLAGS, '-c', name + '.c', '-o', name + '.o'],
                   cwd=rt_dir, check=True, capture_output=True)
    subprocess.run(['ld', '-T', 'rt.ld', '-o', name, name + '.o'], cwd=rt_dir,
                   check=True, capture_output=True)
    return exe


def rt_cases_outcome(rt_dir):
    """The first failing rt_meta_test case, or None."""
    exe = build_harness(rt_dir, os.path.join(HERE, 'rt_meta_test.c'), 'meta_harness')
    for case, env, want in mt.RT_CASES:
        scratch = tempfile.mkdtemp(prefix='mm-rt-', dir=TMP)
        p = subprocess.run([exe, case, scratch], capture_output=True, timeout=60, env=env)
        tag = case + ('' if env.get('OBO_CHECK') else ' (no check)')
        if p.returncode != want:
            return f"rt_meta_test {tag}: exit {p.returncode} != {want}"
    return None


def compile_program_file(src, name, rt_dir):
    base = run.unique('mm_' + name)
    symdir = tempfile.mkdtemp(prefix='sym-', dir=TMP)
    p = subprocess.run([run.OBXC, src, '-o', base, '--rt', rt_dir,
                        '-I', mt.LIBDIR, '--sym-dir', symdir],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise Broken(f"{src} does not compile against the runtime: {p.stderr.strip()[:200]}")
    return base


def io_outcome(rt_dir):
    """tests/run/io_basic.obx: the representative I/O program the entry.S slot
    mutants must not disturb (the C harnesses have their own _entry and never
    go through the slot table)."""
    exe = compile_program_file(os.path.join(FORK, 'tests', 'run', 'io_basic.obx'),
                               'io_basic', rt_dir)
    p = mt.run_program(exe, [])
    return (p.returncode, p.stdout)


def program_outcomes(rt_dir, fx, compiler=None):
    """name -> (exit, stdout) for the whole tests/meta corpus, compiled against
    rt_dir (and, for the compiler runner, by `compiler`)."""
    cache = {}
    out = {}
    for name, file, args, cwd, _check in mt.program_invocations(fx):
        if file not in cache:
            cache[file] = mt.compile_program(file, compiler=compiler, rt=rt_dir)
        p = mt.run_program(cache[file], args, cwd)
        out[name] = (p.returncode, p.stdout)
    return out


def judge_runtime(rt_dir, fx, base_prog, base_io, need_io):
    # An entry.S slot mutant runs the representative I/O program first, before
    # any other case can end the judging: its calls go through the slot table,
    # so a wrong slot order cannot pass unnoticed even if the metadata corpus
    # happened to agree.
    if need_io:
        try:
            o = io_outcome(rt_dir)
        except Broken as e:
            return str(e)
        if o != base_io:
            return f"tests/run/io_basic.obx: {o!r} != baseline {base_io!r}"
    err = rt_cases_outcome(rt_dir)
    if err:
        return err
    try:
        out = program_outcomes(rt_dir, fx)
    except SystemExit as e:
        return f"program compile: {e}"
    for name in out:
        if out[name] != base_prog[name]:
            return f"{name}: {out[name]!r} != baseline {base_prog[name]!r}"
    return None


# ---------------------------------------------------------------- compiler runner
def marker_of(src):
    """(line, substring) of the one // error: marker in a case text."""
    marks = []
    for i, line in enumerate(src.split('\n')):
        k = line.find('// error:')
        if k >= 0:
            marks.append((i + 1, line[k + len('// error:'):].strip()))
    if len(marks) != 1:
        raise Broken(f"an error case has {len(marks)} // error: markers")
    return marks[0]


def compile_mutant(compiler, text, name, extra=()):
    """Compile text with `compiler`; returns (path, exe, completed process)."""
    base = run.unique('mm_' + name)
    path = base + '.obx'
    with open(path, 'w') as f:
        f.write(text)
    p = subprocess.run([compiler, path, '-o', base, '--rt', FORK, *extra],
                       capture_output=True, text=True, timeout=120)
    return path, base, p


def error_cases():
    out = []
    for f in ERRFILES:
        out += run.error_cases(f)
    return out


def error_outcome(compiler, name, src):
    """None if the case is refused with the expected diagnostic, else a reason."""
    line, sub = marker_of(src)
    path, exe, p = compile_mutant(compiler, src, 'err_' + name.replace(':', '_'))
    if p.returncode == 0:
        return f"errors/{name}: compiled, expected an error at line {line}"
    if p.returncode != 1 or not p.stderr.startswith(path + ':'):
        return f"errors/{name}: exit {p.returncode}: {p.stderr.strip()[:160]}"
    rest = p.stderr[len(path) + 1:]
    try:
        got = int(rest.split(':', 1)[0])
    except ValueError:
        return f"errors/{name}: no line:col in {rest[:160]!r}"
    if got != line:
        return f"errors/{name}: line {got}, expected {line}"
    if sub not in p.stderr:
        return f"errors/{name}: stderr lacks {sub!r}: {rest.strip()[:160]!r}"
    return None


def runfile_outcomes(compiler, tag):
    """[(file basename, (exit, stdout))] for the metadata run programs compiled
    by `compiler`, or (None, reason). Each compile gets a private sym dir: the
    env program imports library modules, so it writes .sym files."""
    outs = []
    for f in RUNFILES:
        name = os.path.basename(f)
        symdir = tempfile.mkdtemp(prefix='sym-', dir=TMP)
        try:
            path, exe, p = compile_mutant(compiler, open(f).read(),
                                          tag + '_' + name[:-4],
                                          extra=['-I', mt.LIBDIR, '--sym-dir', symdir])
        except subprocess.TimeoutExpired:
            return None, f"tests/run/{name}: compile timed out"
        if p.returncode != 0:
            return None, f"tests/run/{name}: did not compile: {p.stderr.strip()[:160]}"
        try:
            r = subprocess.run([exe, tempfile.mkdtemp(prefix='mm-run-', dir=TMP)],
                               capture_output=True, env=mt.clean_env(), timeout=120)
        except subprocess.TimeoutExpired:
            return None, f"tests/run/{name}: run timed out"
        outs.append((name, (r.returncode, r.stdout)))
    return outs, None


def baseline_compiler_corpus(fx):
    """(run-program outcomes, program outcomes); every error diagnostic must
    already match under the unmutated compiler."""
    for name, src, lo, hi in error_cases():
        reason = error_outcome(run.OBXC, name, src)
        if reason:
            raise SystemExit(f"the baseline compiler fails an error case: {reason}")
    runs, why = runfile_outcomes(run.OBXC, 'base_run')
    if runs is None:
        raise SystemExit(f"the baseline compiler fails the run corpus: {why}")
    return runs, program_outcomes(FORK, fx)


def judge_compiler(compiler, fx, base_runs, base_prog):
    for name, src, lo, hi in error_cases():
        reason = error_outcome(compiler, name, src)
        if reason:
            return reason
    runs, why = runfile_outcomes(compiler, 'run')
    if runs is None:
        return why
    for (name, got), (_, want) in zip(runs, base_runs):
        if got != want:
            return (f"tests/run/{name}: {(got[0], got[1][:120])!r} != "
                    f"baseline {want[0]}, {want[1][:120]!r}")
    try:
        out = program_outcomes(FORK, fx, compiler=compiler)
    except SystemExit as e:
        return f"program compile: {e}"
    for name in out:
        if out[name] != base_prog[name]:
            return f"{name}: {out[name]!r} != baseline {base_prog[name]!r}"
    return None


# ---------------------------------------------------------------- main
def apply_edits(texts, edits):
    """Apply (file, old, new) edits to a dict file -> text; each old must match
    exactly once and every text must change."""
    out = dict(texts)
    changed = False
    for which, old, new in edits:
        n = out[which].count(old)
        if n != 1:
            raise Broken(f"STALE pattern ({n} matches) in {which}: {old[:80]!r}")
        before = out[which]
        out[which] = before.replace(old, new, 1)
        if out[which] == before:
            raise Broken(f"the edit changed nothing in {which}: {old[:80]!r}")
        changed = True
    if not changed:
        raise Broken("a mutant has no edits")
    return out


def try_runtime_mutant(name, edits, base_prog, base_io, need_io):
    work = tempfile.mkdtemp(prefix='mm-', dir=TMP)
    try:
        texts = {'runtime': open(RUNTIME).read(), 'entry': open(ENTRY).read()}
        texts = apply_edits(texts, edits)
        rt = build_runtime(texts['runtime'], texts['entry'], work)
        first = judge_runtime(rt, FX, base_prog, base_io, need_io)
    except (Broken, subprocess.CalledProcessError) as e:
        return 'broken', f"BROKEN runtime: {name}: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if first is None:
        if name in EQUIVALENT_RUNTIME:
            return 'equiv', f"equiv  runtime: {name}  ({EQUIVALENT_RUNTIME[name]})"
        return 'missed', f"MISSED runtime: {name}"
    return 'caught', f"caught runtime: {name}  ({first})"


def try_compiler_mutant(name, edits, base_runs, base_prog):
    work = tempfile.mkdtemp(prefix='mm-', dir=TMP)
    try:
        texts = apply_edits({'obo': open(SRC).read()},
                            [('obo', old, new) for old, new in edits])
        src = os.path.join(work, 'mutant.obo')
        exe = os.path.join(work, 'mutant_obo')
        with open(src, 'w') as f:
            f.write(texts['obo'])
        b = subprocess.run([run.OBXC, src, '-o', exe, '--rt', FORK],
                           capture_output=True, text=True, timeout=120)
        if b.returncode != 0:
            raise Broken(f"the mutant compiler does not compile: {b.stderr.strip()[:160]}")
        first = judge_compiler(exe, FX, base_runs, base_prog)
    except (Broken, subprocess.CalledProcessError) as e:
        return 'broken', f"BROKEN compiler: {name}: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if first is None:
        if name in EQUIVALENT_COMPILER:
            return 'equiv', f"equiv  compiler: {name}  ({EQUIVALENT_COMPILER[name]})"
        return 'missed', f"MISSED compiler: {name}"
    return 'caught', f"caught compiler: {name}  ({first})"


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ''
    global FX
    FX = mt.make_fixtures()

    # the unmutated outcomes every mutant is compared with. The baseline runtime
    # is built in a private directory too: build_harness writes its rewritten
    # harness source there, never into the tree.
    base_work = tempfile.mkdtemp(prefix='mm-base-', dir=TMP)
    base_rt_dir = build_runtime(open(RUNTIME).read(), open(ENTRY).read(), base_work)
    base_rt = rt_cases_outcome(base_rt_dir)
    if base_rt:
        raise SystemExit(f"the baseline runtime fails ({base_rt}), so no mutant can be judged")
    base_prog = program_outcomes(base_rt_dir, FX)
    base_io = io_outcome(base_rt_dir)
    base_runs, base_prog_c = baseline_compiler_corpus(FX)
    if base_prog != base_prog_c:
        raise SystemExit("the baseline compiler's program outcomes differ from the runtime baseline")
    print(f"baseline: {len(mt.RT_CASES)} rt_meta_test cases, "
          f"{len(base_prog)} program cases, {len(base_runs)} run programs, "
          f"{len(error_cases())} compiler error cases pass")

    jobs = env_jobs('MUTATE_META_JOBS', 4)
    rt_todo = [(n, e, 'entry' in {w for w, _, _ in e})
               for n, e in RUNTIME_MUTANTS if pattern in n]
    cc_todo = [(n, e) for n, e in COMPILER_MUTANTS if pattern in n]
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        rt_futures = [pool.submit(try_runtime_mutant, n, e, base_prog, base_io, io)
                      for n, e, io in rt_todo]
        cc_futures = [pool.submit(try_compiler_mutant, n, e, base_runs, base_prog)
                      for n, e in cc_todo]
        rt_results = [f.result() for f in rt_futures]
        cc_results = [f.result() for f in cc_futures]
    for status, line in rt_results + cc_results:
        print(line)
    bad = 0
    for label, results in (('runtime/entry.S', rt_results), ('compiler', cc_results)):
        caught = sum(s == 'caught' for s, _ in results)
        equiv = sum(s == 'equiv' for s, _ in results)
        missed = sum(s == 'missed' for s, _ in results)
        broken = sum(s == 'broken' for s, _ in results)
        bad += missed + broken
        print(f"{label}: {caught}/{len(results)} caught, {equiv} equivalent, "
              f"{missed} missed, {broken} broken")
    sys.exit(1 if bad else 0)


FX = None
if __name__ == '__main__':
    main()
