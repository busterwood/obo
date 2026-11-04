#!/usr/bin/env python3
"""The differential test for cmd/zoxide.obx against the real zoxide 0.9.9.

    python3 obx/tests/zoxide.py [--c] [-k PATTERN] [-v] [-j N] [--ref PATH] [--exe PATH]

Our zoxide is built with compiler/out/obxc (native, or through C with --c) and
run with OBX_CHECK=1, so a leak fails a case. The reference is --ref, else
$ZOXIDE_REF, else `zoxide` on PATH; without one the script exits 2. Install it
into a scratch directory, never into ~/.cargo/bin:

    CARGO_INSTALL_ROOT=/tmp/zoxide-ref cargo install zoxide --locked --version 0.9.9

A case is a list of steps over real temporary directories. Each step runs in
two worlds, each with its own _ZO_DATA_DIR: ours against the reference. The
step's stdout, stderr and exit status must be equal, and after every step the
two databases must hold the same entries in the same order with the same ranks
(the last-accessed times may differ by a few seconds, as they come from the
clock). A step tagged slot 1 runs the reference in the first world (a database
written by one program and read or changed by the other), so the cross cases
alternate the programs on one database.

The clock cannot be set, so cases that print scores craft the database with
fixed ages (an entry is `(path, rank, age_seconds)`), far from a bucket edge.
"""
import argparse
import concurrent.futures
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
OBXC = os.environ.get('OBXC') or os.path.join(OBX, 'compiler', 'out', 'obxc')

DAY = 86400


def write_db(path, entries, version=3):
    now = int(time.time())
    out = struct.pack('<I', version) + struct.pack('<Q', len(entries))
    for p, rank, age in entries:
        b = p.encode()
        out += struct.pack('<Q', len(b)) + b + struct.pack('<d', rank) + struct.pack('<Q', max(0, now - age))
    with open(path, 'wb') as f:
        f.write(out)


def read_db(path):
    try:
        return read_db1(path)
    except Exception:
        return ('raw', open(path, 'rb').read()) if os.path.isfile(path) else ('raw', None)


def read_db1(path):
    try:
        data = open(path, 'rb').read()
    except FileNotFoundError:
        return None
    if len(data) < 12:
        return ('raw', data)
    v, n = struct.unpack_from('<IQ', data, 0)
    pos = 12
    out = []
    for _ in range(n):
        (ln,) = struct.unpack_from('<Q', data, pos)
        pos += 8
        p = data[pos:pos + ln].decode()
        pos += ln
        rank, last = struct.unpack_from('<dQ', data, pos)
        rank = struct.pack('<d', rank)
        pos += 16
        out.append((p, rank, last))
    if pos != len(data):
        return ('raw', data)
    return (v, out)


def same_db(a, b):
    if a is None or b is None:
        return a == b
    if a[0] == 'raw' or b[0] == 'raw':
        return a == b
    if a[0] != b[0] or len(a[1]) != len(b[1]):
        return False
    for (p1, r1, t1), (p2, r2, t2) in zip(a[1], b[1]):
        if p1 != p2 or r1 != r2 or abs(t1 - t2) > 5:  # ranks compared as bytes
            return False
    return True


class Case:
    def __init__(self, name, steps, dirs=()):
        self.name = name
        self.steps = steps
        self.dirs = dirs


def S(*argv, slot=0, cwd=None, env=None):
    return ('run', slot, list(argv), cwd, env or {})


def DB(*entries, version=3):
    return ('db', list(entries), version)


def RAW(data):
    return ('raw', data)


def FS(fn):
    return ('fs', fn)


CASES = []


def case(name, steps, dirs=()):
    CASES.append(Case(name, steps, dirs))


# paths are written with {r} for the case's root directory
D = ['a', 'b', 'a/b', 'a/b/c', 'Foo', 'foo/bar', 'foo/bar/baz', 'x y', 'ünï', 'home']

case('add-query', [S('add', '{r}/a'), S('add', '{r}/b'), S('add', '{r}/a'), S('query', 'a'),
                   S('query', '-l'), S('query', '-ls')], D)
case('add-ranking', [S('add', '{r}/a', '{r}/b', '{r}/a/b'), S('add', '{r}/a/b'), S('add', '{r}/b'),
                     S('add', '{r}/b'), S('query', '-l'), S('query', '-l', '-s'), S('query')], D)
case('add-score', [S('add', '-s', '5', '{r}/a'), S('add', '--score', '2.5', '{r}/b'), S('add', '-s', '-3', '{r}/b'),
                   S('add', '-s=0.5', '{r}/a/b'), S('query', '-l', '-s')], D)
case('add-relative', [S('add', '.', cwd='{r}/a'), S('add', '..', cwd='{r}/a/b'), S('add', 'b/c', cwd='{r}/a'),
                      S('add', './c//', cwd='{r}/a/b'), S('add', '{r}/a/b/../b/./c'), S('query', '-l')], D)
case('add-notdir', [S('add', '{r}/nonexistent'), S('add', '{r}/a', '{r}/nonexistent'), S('query', '-l')], D)
case('add-file', [FS(lambda r: open(r + '/file', 'w').write('x')), S('add', '{r}/file'), S('query', '-l', '-a')], D)
case('add-newline', [FS(lambda r: os.mkdir(r + '/n\nl')), S('add', '{r}/n\nl'), S('add', '{r}/a'), S('query', '-l')], D)
case('add-unicode', [S('add', '{r}/ünï', '{r}/x y'), S('query', '-l'), S('query', 'ün'), S('query', 'x y')], D)
case('add-resolve-symlinks', [FS(lambda r: os.symlink(r + '/a/b', r + '/lnk')), S('add', '{r}/lnk', env={'_ZO_RESOLVE_SYMLINKS': '1'}),
                              S('query', '-l', '-a'), S('add', '{r}/lnk'), S('query', '-l', '-a'),
                              S('query', '-l', env={'_ZO_RESOLVE_SYMLINKS': '1'}), S('query', '-l')], D)
case('add-resolve-missing', [S('add', '{r}/nonexistent', env={'_ZO_RESOLVE_SYMLINKS': '1'})], D)
case('add-resolve-loop', [FS(lambda r: os.symlink(r + '/loop2', r + '/loop1')), FS(lambda r: os.symlink(r + '/loop1', r + '/loop2')),
                          S('add', '{r}/loop1', env={'_ZO_RESOLVE_SYMLINKS': '1'})], D)
case('add-nothing', [S('add'), S('add', '--score')], D)
case('add-dashdash', [S('add', '--', '{r}/a'), S('query', '-l')], D)

# query matching rules
MATCH = [('{r}/foo/bar', 3.0, 100), ('{r}/foo/bar/baz', 2.0, 100), ('{r}/Foo', 1.0, 100), ('{r}/a/b/c', 4.0, 100),
         ('{r}/a/b', 5.0, 100), ('{r}/x y', 6.0, 100), ('{r}/ünï', 7.0, 100)]
MD = ['foo/bar/baz', 'Foo', 'a/b/c', 'x y', 'ünï']
for kw in (['foo'], ['FOO'], ['ba'], ['bar'], ['baz'], ['foo', 'baz'], ['foo', 'bar'], ['bar', 'foo'], ['foo/'], ['foo', '/'],
           ['/', 'fo', '/', 'ar'], ['oo/ba'], ['foo', 'o', 'bar'], ['a', 'c'], ['c', 'a'], ['b', 'b'], ['x y'], ['x', 'y'],
           ['ün'], ['{r}'], ['{r}/a'], ['nomatch'], [''], ['/'], ['--', '-x']):
    case('kw-' + '_'.join(kw).replace('/', '+').replace('{r}', 'R') or 'empty',
         [DB(*MATCH), S('query', '-l', *kw), S('query', *kw), S('query', '-s', '--', *kw)], MD)
case('kw-flags-after', [DB(*MATCH), S('query', 'foo', '-l'), S('query', '-l', 'foo', 'bar'), S('query', '-ls', 'a')], MD)
case('kw-base-dir', [DB(*MATCH), S('query', '-l', '--base-dir', '{r}/foo'), S('query', '-l', '--base-dir', '{r}/a/b'),
                     S('query', '-l', '--base-dir={r}/a/', 'c'), S('query', '-l', '--base-dir', '{r}/fo')], MD)

# exclusion
case('exclude-cwd', [DB(('{r}/a', 3.0, 10), ('{r}/b', 2.0, 10), ('{r}/a/b', 1.0, 10)),
                     S('query', '--exclude', '{r}/a'), S('query', '--exclude', '{r}/a', 'a'),
                     S('query', '--exclude', '{r}/a', '-l'), S('query', '--exclude', '{r}/a', '-l', '-s'),
                     S('query', '--exclude', '{r}/b', 'b'), S('query', '--exclude', '{r}/b', 'a/b'),
                     S('query', '--exclude', '{r}/a', '--exclude', '{r}/b')], D)
case('missing-dirs', [DB(('{r}/gone', 9.0, 10), ('{r}/a', 3.0, 10), ('{r}/gone2', 5.0, 5000000), ('{r}/gone3', 5.0, 10)),
                      S('query', '-l'), S('query', '-l', '-a'), S('query', '-l', '-a', '-s'), S('query', 'gone'),
                      S('query', '-a', 'gone')], D)
case('missing-ttl', [DB(('{r}/gone', 9.0, 8000000), ('{r}/gone2', 9.0, 7000000), ('{r}/a', 3.0, 10)),
                     S('query', '-l', '-a'), S('query', '-l'), S('query', '-l', '-a')], D)
case('file-not-dir', [FS(lambda r: open(r + '/file', 'w').write('x')), DB(('{r}/file', 9.0, 10), ('{r}/a', 3.0, 10)),
                      S('query', '-l'), S('query', '-l', '-a')], D)
case('exclude-env', [DB(('{r}/a', 3.0, 10), ('{r}/b', 2.0, 10), ('{r}/a/b', 1.0, 10), ('{r}/foo/bar', 1.0, 10)),
                     S('query', '-l', env={'_ZO_EXCLUDE_DIRS': '{r}/a'}), S('query', '-l', '-a'),
                     S('add', '{r}/b', env={'_ZO_EXCLUDE_DIRS': '{r}/b'}), S('query', '-l', '-a'),
                     S('add', '{r}/foo/bar', env={'_ZO_EXCLUDE_DIRS': '{r}/nothing:{r}/foo/*'}),
                     S('query', '-l', '-a', env={'_ZO_EXCLUDE_DIRS': '{r}/f*'})], D)
GLOBS = ['{r}/a', '{r}/a?', '{r}/?', '*b', '{r}/a/*', '{r}/[ab]', '{r}/[!a]', '{r}/[a-b]', '**', '{r}/**/c', '**/c',
         '{r}/a/**', '*/b/*', '[a', '{r}/[!', 'a**', '**a', '***', '{r}/a/b/c:{r}/b', '', ':', '{r}/F[o]o', '{r}/*o']
for i, g in enumerate(GLOBS):
    case('glob-%d' % i, [DB(('{r}/a', 3.0, 10), ('{r}/b', 2.0, 10), ('{r}/a/b', 1.0, 10), ('{r}/a/b/c', 1.0, 10), ('{r}/Foo', 1.0, 10)),
                         S('query', '-l', '-a', env={'_ZO_EXCLUDE_DIRS': g}), S('add', '{r}/a/b', env={'_ZO_EXCLUDE_DIRS': g})], D)
case('exclude-home', [S('add', '{r}/home'), S('add', '{r}/a'), S('query', '-l', env={'HOME': '{r}/home'})], D)
case('exclude-home-default', [S('add', '{r}/home', env={'HOME': '{r}/home'}), S('add', '{r}/a', env={'HOME': '{r}/home'}),
                              S('add', '{r}/home/x', env={'HOME': '{r}/home'}), S('query', '-l', env={'HOME': '{r}/home'})],
     D + ['home/x'])

# scores
AGES = [60, 1800, 7200, 5 * 3600, 3 * DAY, 20 * DAY, 400 * DAY]
case('score-buckets', [DB(*[('{r}/a', 1.0, AGES[0]), ('{r}/b', 1.0, AGES[1]), ('{r}/a/b', 1.0, AGES[2]), ('{r}/a/b/c', 1.0, AGES[3]),
                            ('{r}/Foo', 1.0, AGES[4]), ('{r}/foo/bar', 1.0, AGES[5]), ('{r}/foo/bar/baz', 1.0, AGES[6])]),
                       S('query', '-l', '-s')], D)
RANKS = [0.0, 0.0625, 0.1, 0.37, 1.0, 1.25, 3.0, 7.7, 9.999, 99.99, 1000.0, 2499.99, 2500.0, 5000.0]
case('score-format', [DB(*[('{r}/p%d' % i, v, 100) for i, v in enumerate(RANKS)]), S('query', '-a', '-l', '-s')], D)
TIES = [0.0625 * k for k in range(1, 40)] + [0.0125, 0.0375, 0.0625 * 40 + 0.001]
case('score-ties-rounding', [DB(*[('{r}/p%d' % i, v, 100) for i, v in enumerate(TIES)]), S('query', '-a', '-l', '-s')], D)
case('score-big', [DB(('{r}/a', 1e9, 10), ('{r}/b', 12345.678, 10), ('{r}/a/b', 1e-9, 10)), S('query', '-l', '-s'),
                   S('query', '-s'), S('query', '-s', 'b')], D)
case('score-one', [S('add', '{r}/a'), S('query', '-s'), S('query', '-s', 'a')], D)

# remove
case('remove', [S('add', '{r}/a', '{r}/b', '{r}/a/b', '{r}/foo/bar'), S('remove', '{r}/b'), S('query', '-l'),
                S('remove', '{r}/b'), S('remove', '{r}/a/b', '{r}/a'), S('query', '-l'), S('remove', '{r}/nonexistent')], D)
case('remove-relative', [S('add', '{r}/a/b', '{r}/a'), S('remove', 'b', cwd='{r}/a'), S('query', '-l'),
                         S('remove', '../a/./', cwd='{r}/a/b'), S('query', '-l')], D)
case('remove-none', [S('remove'), S('add', '{r}/a'), S('remove')], D)
case('remove-gone-dir', [DB(('{r}/gone', 1.0, 10), ('{r}/a', 2.0, 10)), S('remove', '{r}/gone'), S('remove', 'gone', cwd='{r}'),
                         S('query', '-l', '-a')], D)
case('remove-partial', [S('add', '{r}/a', '{r}/b'), S('remove', '{r}/a', 'zzz'), S('query', '-l')], D)

# aging
many = [('{r}/p%d' % i, 100.0 + i, 10) for i in range(10)]
case('aging-add', [DB(*many), S('add', '{r}/a', env={'_ZO_MAXAGE': '500'}), S('query', '-a', '-l', '-s')], D)
case('aging-small', [DB(*many), S('add', '{r}/a', env={'_ZO_MAXAGE': '20'}), S('query', '-a', '-l', '-s')], D)
case('aging-none', [DB(*many), S('add', '{r}/a', env={'_ZO_MAXAGE': '100000'}), S('query', '-a', '-l', '-s')], D)
case('aging-default', [DB(('{r}/a', 9990.0, 10), ('{r}/b', 5.0, 10)), S('add', '{r}/a/b'), S('query', '-a', '-l', '-s'),
                       S('add', '{r}/a/b', '{r}/b', '{r}/b'), S('query', '-a', '-l', '-s')], D)
case('aging-repeat', [S('add', '{r}/a', env={'_ZO_MAXAGE': '5'}), S('add', '{r}/b', env={'_ZO_MAXAGE': '5'}),
                      S('add', '{r}/a/b', env={'_ZO_MAXAGE': '5'}), S('add', '{r}/a', env={'_ZO_MAXAGE': '5'}),
                      S('add', '{r}/a', env={'_ZO_MAXAGE': '5'}), S('query', '-a', '-l', '-s')], D)
for v in ('abc', '-1', '5.5', '', '4294967296', '+7', '0', '4294967295'):
    case('maxage-' + (v or 'empty'), [S('add', '{r}/a', env={'_ZO_MAXAGE': v}), S('query', '-l')], D)

# init
SHELLS = ['bash', 'elvish', 'fish', 'nushell', 'posix', 'powershell', 'tcsh', 'xonsh', 'zsh']
for sh in SHELLS:
    for hook in (None, 'none', 'prompt', 'pwd'):
        for extra in ([], ['--no-cmd'], ['--cmd', 'j']):
            args = ['init', sh] + (['--hook', hook] if hook else []) + extra
            case('init-' + '-'.join(args[1:]).replace(' ', ''), [S(*args)])
    for echo in ('0', '1'):
        for res in ('0', '1'):
            case('init-%s-env-%s%s' % (sh, echo, res), [S('init', sh, '--hook', 'prompt', env={'_ZO_ECHO': echo, '_ZO_RESOLVE_SYMLINKS': res}),
                                                          S('init', sh, '--no-cmd', env={'_ZO_ECHO': echo, '_ZO_RESOLVE_SYMLINKS': res})])
case('init-ksh', [S('init', 'ksh'), S('init', 'ksh', '--hook', 'prompt', '--cmd', 'cd')])
case('init-aliases', [S('init', 'bash', '--no-aliases'), S('init', 'zsh', '--cmd=jump'), S('init', '--hook=none', 'fish')])
case('init-cmd-odd', [S('init', 'bash', '--cmd', 'a b"c'), S('init', 'zsh', '--cmd', ''), S('init', 'fish', '--cmd', '$x')])

# import
Z_FILE = '{r}/a|3|1700000000\n{r}/b|2|1600000000\n{r}/a|1.5|1750000000\n\n{r}/x y|4|1\n'
AJ_FILE = '7.0\t{r}/a\n2.5\t{r}/b\n\n10\t{r}/a\n0\t{r}/x y\n'
case('import-z', [FS(lambda r: open(r + '/zdata', 'w').write(Z_FILE.replace('{r}', r))),
                  S('import', '--from=z', '{r}/zdata'), S('query', '-a', '-l', '-s'),
                  S('import', '--from', 'z', '{r}/zdata'), S('import', '--from', 'z', '--merge', '{r}/zdata'),
                  S('query', '-a', '-l', '-s')], D)
case('import-autojump', [FS(lambda r: open(r + '/ajdata', 'w').write(AJ_FILE.replace('{r}', r))),
                         S('import', '--from=autojump', '{r}/ajdata'), S('query', '-a', '-l', '-s'),
                         S('import', '--from', 'autojump', '--merge', '{r}/ajdata'), S('query', '-a', '-l', '-s')], D)
case('import-fasd', [FS(lambda r: open(r + '/zdata', 'w').write(Z_FILE.replace('{r}', r))), S('import', '--from=fasd', '{r}/zdata'),
                     S('query', '-a', '-l', '-s')], D)
case('import-merge-existing', [FS(lambda r: open(r + '/zdata', 'w').write(Z_FILE.replace('{r}', r))), S('add', '{r}/a', '{r}/foo/bar'),
                               S('import', '--from=z', '{r}/zdata'), S('import', '--merge', '--from=z', '{r}/zdata'),
                               S('query', '-a', '-l', '-s')], D)
for name, text, frm in (('bad-z-1', '{r}/a|3\n', 'z'), ('bad-z-2', 'nopipe\n', 'z'), ('bad-z-3', '{r}/a|x|1\n', 'z'),
                        ('bad-z-4', '{r}/a|3|y\n', 'z'), ('bad-z-5', '{r}/a|3|-1\n', 'z'), ('bad-aj-1', 'no tab\n', 'autojump'),
                        ('bad-aj-2', 'x\t{r}/a\n', 'autojump'), ('crlf-z', '{r}/a|3|100\r\n{r}/b|1|200\r\n', 'z'),
                        ('crlf-aj', '1\t{r}/a\r\n', 'autojump'), ('empty', '', 'z'), ('inf-aj', 'inf\t{r}/a\n-inf\t{r}/b\nnan\t{r}/x y\n', 'autojump')):
    case('import-' + name, [FS(lambda r, t=text: open(r + '/data', 'w').write(t.replace('{r}', r))),
                            S('import', '--from=' + frm, '{r}/data'), S('query', '-a', '-l', '-s')], D)
case('import-missing-file', [S('import', '--from=z', '{r}/nonexistent'), S('add', '{r}/a'), S('import', '--from=z', '{r}/nonexistent')], D)
case('import-directory', [S('import', '--from=z', '{r}/a')], D)
case('import-binary', [FS(lambda r: open(r + '/data', 'wb').write(b'\xff\xfe')), S('import', '--from=z', '{r}/data')], D)

# cross compatibility: the programs alternate on one database
case('cross-add-query', [S('add', '{r}/a', '{r}/b'), S('add', '{r}/a', slot=1), S('add', '{r}/a/b'), S('add', '{r}/b', slot=1),
                         S('query', '-l', '-s'), S('query', '-l', '-s', slot=1), S('query', 'a', slot=1), S('remove', '{r}/a/b', slot=1),
                         S('query', '-l', '-s')], D)
case('cross-aging', [DB(*many), S('add', '{r}/a', slot=1, env={'_ZO_MAXAGE': '500'}), S('add', '{r}/b', env={'_ZO_MAXAGE': '300'}),
                     S('query', '-a', '-l', '-s', slot=1), S('query', '-a', '-l', '-s')], D)
case('cross-import', [FS(lambda r: open(r + '/zdata', 'w').write(Z_FILE.replace('{r}', r))), S('import', '--from=z', '{r}/zdata', slot=1),
                      S('query', '-a', '-l', '-s'), S('add', '{r}/a/b'), S('query', '-a', '-l', '-s', slot=1)], D)
case('cross-unicode', [S('add', '{r}/ünï'), S('add', '{r}/x y', slot=1), S('query', '-l'), S('query', 'ün', slot=1)], D)

# the database file
case('db-missing', [S('query'), S('query', '-l'), S('remove', '{r}/a')], D)
case('db-empty-file', [RAW(b''), S('query', 'x'), S('add', '{r}/a')], D)
case('db-short', [RAW(b'\x03\x00'), S('query', 'x')], D)
case('db-version', [RAW(b'\x04\x00\x00\x00'), S('query', 'x'), S('add', '{r}/a')], D)
case('db-version-old', [RAW(b'\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'), S('query', 'x')], D)
case('db-no-count', [RAW(b'\x03\x00\x00\x00'), S('query', 'x')], D)
case('db-zero', [RAW(b'\x03\x00\x00\x00' + b'\0' * 8), S('query', 'x'), S('add', '{r}/a')], D)
case('db-trailing', [RAW(b'\x03\x00\x00\x00' + b'\0' * 8 + b'\x01'), S('query', 'x')], D)
case('db-trunc', [RAW(b'\x03\x00\x00\x00\x01\0\0\0\0\0\0\0\x01'), S('query', 'x')], D)
case('db-huge-count', [RAW(b'\x03\x00\x00\x00' + b'\xff' * 7 + b'\x7f'), S('query', 'x')], D)
case('db-huge-string', [RAW(b'\x03\x00\x00\x00\x01\0\0\0\0\0\0\0' + b'\xff' * 7 + b'\x7f'), S('query', 'x')], D)
case('db-big-string', [RAW(b'\x03\x00\x00\x00\x01\0\0\0\0\0\0\0' + struct.pack('<Q', 1 << 30)), S('query', 'x')], D)
case('db-bad-utf8', [RAW(b'\x03\0\0\0\x01\0\0\0\0\0\0\0\x01\0\0\0\0\0\0\0\xff' + b'\0' * 16), S('query', 'x')], D)
case('db-bad-utf8-2', [RAW(b'\x03\0\0\0\x01\0\0\0\0\0\0\0\x02\0\0\0\0\0\0\0\xe2\x28' + b'\0' * 16), S('query', 'x')], D)
case('db-bad-utf8-3', [RAW(b'\x03\0\0\0\x01\0\0\0\0\0\0\0\x02\0\0\0\0\0\0\0a\xe2' + b'\0' * 16), S('query', 'x')], D)
case('db-bad-utf8-4', [RAW(b'\x03\0\0\0\x01\0\0\0\0\0\0\0\x03\0\0\0\0\0\0\0a\xe2\x82' + b'\0' * 16), S('query', 'x')], D)
case('db-dir', [FS(lambda r: None), ('mkdb',), S('query', 'x')], D)
case('db-dup-paths', [DB(('{r}/a', 1.0, 10), ('{r}/a', 2.0, 10), ('{r}/b', 1.0, 10)), S('query', '-l'), S('add', '{r}/a'), S('query', '-l', '-s')], D)
case('db-nan-rank', [DB(('{r}/a', float('nan'), 10), ('{r}/b', 1.0, 10)), S('query', '-l', '-s')], D)
case('db-symlinked', [('symdb',), S('add', '{r}/a'), S('add', '{r}/b', slot=1), S('query', '-l')], D)

# data dir
case('datadir-relative', [S('query', env={'_ZO_DATA_DIR': 'rel'}), S('add', '{r}/a', env={'_ZO_DATA_DIR': ''})], D)
case('datadir-create', [S('add', '{r}/a', env={'_ZO_DATA_DIR': '{r}/new/deeper'}), S('query', '-l', env={'_ZO_DATA_DIR': '{r}/new/deeper'})], D)
case('datadir-uncreatable', [FS(lambda r: open(r + '/file', 'w').write('x')), S('query', env={'_ZO_DATA_DIR': '{r}/file/sub'})], D)
case('datadir-xdg', [S('add', '{r}/a', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': '{r}/xdg'}),
                     S('query', '-l', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': '{r}/xdg'}),
                     S('query', '-l', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': '{r}/xdg', 'HOME': '{r}/b'})], D)
case('datadir-home', [S('add', '{r}/a', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': None, 'HOME': '{r}/home'}),
                      S('query', '-l', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': None, 'HOME': '{r}/home'})], D)
case('datadir-nohome', [S('query', env={'_ZO_DATA_DIR': None, 'XDG_DATA_HOME': None, 'HOME': None})], D)
case('echo-env', [S('add', '{r}/a', env={'_ZO_ECHO': '1'}), S('query', 'a', env={'_ZO_ECHO': '1'})], D)
case('readonly-dir', [S('add', '{r}/a'), ('chmod', 0o500), S('add', '{r}/b'), S('query', '-l'), ('chmod', 0o700)], D)

case('interactive-no-fzf', [S('add', '{r}/a'), S('query', '-i'), S('query', '-i', 'a', '-s')], D)

# command line
USAGE = [[], ['--help'], ['-h'], ['-V'], ['--version'], ['bogus'], ['-x'], ['--bogus'], ['edit', 'foo'],
         ['add', '--help'], ['add', '-h'], ['query', '--help'], ['remove', '-h'], ['init', '--help'], ['import', '-h'],
         ['add', '-V'], ['query', '--version'], ['add', '--help', 'x'],
         ['init'], ['init', 'foo'], ['init', 'bash', 'zsh'], ['init', 'bash', '--hook', 'x'], ['init', 'bash', '--hook'],
         ['init', 'bash', '--cmd'], ['init', 'bash', '--no-cmd=x'], ['init', 'bash', '-x'], ['init', 'bash', '--hook=pwd', '--hook=none'],
         ['init', '--hook'], ['init', '--bogus', 'bash'], ['init', 'bash', '--no-cmd', '--no-cmd'],
         ['query', '--bogus'], ['query', '-x'], ['query', '-lx'], ['query', '-i', '-l'], ['query', '-l', '-i'], ['query', '-i', '--list'],
         ['query', '-l', '-l'], ['query', '--exclude'], ['query', '--exclude', '-l'], ['query', '--exclude', '-x'], ['query', '--base-dir'],
         ['query', '-a', '-a'], ['query', '-la'], ['query', '-sl', 'zz'], ['query', '-s', '-s'],
         ['add'], ['add', '-s'], ['add', '-s', 'x', '/tmp'], ['add', '-s', '-5', '/tmp'], ['add', '--score=x', '/tmp'], ['add', '-s', '1', '-s', '2', '/tmp'],
         ['add', '--score', 'inf', '/tmp'], ['add', '--score', '1e3', '/tmp'], ['add', '-s', '0x10', '/tmp'], ['add', '--score', '.5', '/tmp'],
         ['add', '--score', '5.', '/tmp'], ['add', '--score', 'NaN', '/tmp'], ['add', '--score', '', '/tmp'], ['add', '--score', '1_0', '/tmp'],
         ['remove', '-x'], ['remove', '--bogus', 'a'], ['remove', '--', '-x'],
         ['import'], ['import', '--merge'], ['import', 'x'], ['import', '--from=z'], ['import', '--from', 'x', 'y'], ['import', '--from=z', 'a', 'b'],
         ['import', '--from'], ['import', '--from=z', '--from=z', 'a'], ['import', '--merge', '--merge']]
for i, u in enumerate(USAGE):
    case('cli-%d-%s' % (i, '_'.join(u).replace('/', '+')[:40] or 'none'), [S(*u)], D)


# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, ours, ref, verbose):
        self.ours = ours
        self.ref = ref
        self.verbose = verbose

    def sub(self, s, root, ddir):
        s = s.replace('{r}', root).replace('{d}', ddir)
        return s

    def run_case(self, c):
        root = tempfile.mkdtemp(prefix='zx')
        root = os.path.realpath(root)
        try:
            return self._run_case(c, root)
        finally:
            subprocess.run(['chmod', '-R', 'u+rwx', root])
            shutil.rmtree(root, ignore_errors=True)

    def _run_case(self, c, root):
        for d in c.dirs:
            os.makedirs(os.path.join(root, d), exist_ok=True)
        os.makedirs(os.path.join(root, 'cwd'), exist_ok=True)
        # world 0 runs ours for slot 0 and the reference for slot 1; world 1 the reverse
        # of nothing: the reference for every slot, as the oracle.
        wd = [os.path.join(root, 'data0'), os.path.join(root, 'data1')]
        for w in wd:
            os.makedirs(w, exist_ok=True)
        home = os.path.join(root, 'home')
        os.makedirs(home, exist_ok=True)
        log = []
        for n, step in enumerate(c.steps):
            kind = step[0]
            if kind == 'run':
                _, slot, argv, cwd, env = step
                outs = []
                for world in (0, 1):
                    exe = self.ref if (world == 1 or slot == 1) else self.ours
                    e = {'PATH': '/usr/bin:/bin', 'HOME': home, 'LANG': 'C.UTF-8', '_ZO_DATA_DIR': wd[world],
                         'OBX_CHECK': '1' if exe == self.ours else '0'}
                    for k, v in env.items():
                        if v is None:
                            e.pop(k, None)
                        else:
                            e[k] = self.sub(v, root, wd[world])
                    cw = self.sub(cwd, root, wd[world]) if cwd else os.path.join(root, 'cwd')
                    args = [self.sub(a, root, wd[world]) for a in argv]
                    r = subprocess.run([exe] + args, env=e, cwd=cw, capture_output=True, timeout=60)
                    outs.append(self.norm(r, root, wd[world]))
                if outs[0] != outs[1]:
                    return 'step %d %r differs:\n  ours/mixed: %r\n  reference:  %r' % (n, argv, outs[0], outs[1])
                log.append(outs[0])
                err = self.check_db(wd, root)
                if err:
                    return 'step %d %r: %s' % (n, argv, err)
            elif kind == 'db':
                for w in wd:
                    write_db(os.path.join(w, 'db.zo'), [(self.sub(p, root, w), r, a) for p, r, a in step[1]], step[2])
            elif kind == 'raw':
                for w in wd:
                    open(os.path.join(w, 'db.zo'), 'wb').write(step[1])
            elif kind == 'fs':
                step[1](root)
            elif kind == 'mkdb':
                for w in wd:
                    os.mkdir(os.path.join(w, 'db.zo'))
            elif kind == 'symdb':
                for w in wd:
                    os.makedirs(os.path.join(root, 'real'), exist_ok=True)
                    os.symlink(os.path.join(root, 'real', 'x%d' % wd.index(w)), os.path.join(w, 'db.zo'))
            elif kind == 'chmod':
                for w in wd:
                    os.chmod(w, step[1])
        return None

    def check_db(self, wd, root):
        a = b = None
        try:
            a = read_db(os.path.join(wd[0], 'db.zo'))
            b = read_db(os.path.join(wd[1], 'db.zo'))
        except Exception as e:
            return 'database unreadable: %s' % e
        if not same_db(a, b):
            return 'databases differ:\n  ours/mixed: %r\n  reference:  %r' % (a, b)
        return None

    def norm(self, r, root, ddir):
        def n(b):
            s = b.decode('utf8', 'replace')
            s = s.replace(ddir, '<D>')
            s = re.sub(r'tmp_[A-Za-z0-9]{12}', 'tmp_XXXXXXXXXXXX', s)
            return s
        return (r.returncode, n(r.stdout), n(r.stderr))


def build(c_backend, out):
    argv = [OBXC, os.path.join(OBX, 'cmd', 'zoxide.obx')] + ([] if c_backend else ['--native']) + ['-o', out]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('build failed:\n' + r.stdout + r.stderr)


def find_ref(arg):
    cands = [arg, os.environ.get('ZOXIDE_REF'), shutil.which('zoxide')]
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--c', action='store_true', help='build through C instead of native')
    ap.add_argument('-k', default='')
    ap.add_argument('-v', action='store_true')
    ap.add_argument('-j', type=int, default=8)
    ap.add_argument('--ref')
    ap.add_argument('--exe', help='a built zoxide to test instead of building')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    if a.list:
        for c in CASES:
            print(c.name)
        return 0
    ref = find_ref(a.ref)
    if not ref:
        print('no reference zoxide: pass --ref, set ZOXIDE_REF or put zoxide on PATH', file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix='zxbuild')
    try:
        ours = a.exe or os.path.join(tmp, 'zoxide')
        if not a.exe:
            build(a.c, ours)
        r = Runner(ours, ref, a.v)
        cases = [c for c in CASES if a.k in c.name]
        fails = 0
        with concurrent.futures.ThreadPoolExecutor(a.j) as ex:
            for c, res in zip(cases, ex.map(r.run_case, cases)):
                if res:
                    fails += 1
                    print('FAIL %s: %s' % (c.name, res))
                elif a.v:
                    print('ok   %s' % c.name)
        print('%d passed, %d failed (%s build, reference %s)' % (len(cases) - fails, fails, 'C' if a.c else 'native', ref))
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
