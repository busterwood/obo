#!/usr/bin/env python3
"""Deterministic fixture trees for the eza reference probes and differential gate.

`build(root)` creates every tree described in section 9.2 of
`design/stdlib-eza-design-v7.md` under `root` and returns a host-capability
dictionary.  All names are relative to `root`; the caller chooses the working
directory and operands so that no temporary path leaks into recorded output.
Only the live-only `absolute` tree below contains absolute symlink targets.

The tree is built in a fixed order with explicit modes and mtimes:
umask(0o022) plus explicit chmod, one pinned mtime per directory after its
children exist.  Only information the reference can observe through
lstat/readlink is created: regular files, directories, symlinks, FIFOs and
Unix sockets, plus an optional user xattr and one invalid-UTF-8 name.  There
are no device nodes, ACLs or trusted xattrs.

Recordable trees (relative targets only):

    empty/  one/  onedir/  mixed/  modes/  sizes/  times/  names/  ext/
    operands/  quoting/  links/  long/  many/  invalid/  hard/  xattr/
    tree/  selflink/  grid1/ grid2/ grid10/ grid100/ gridlong10/
    gridlong100/ locked/

Live/shape-only trees:

    absolute/   symlink targets /tmp, /tmp/, / and the broken /tar get
    locked/     permission-denied and no-search directories (root differs)

`long/deep` is a joined path near but under 4095 bytes; `DEEP_OPERAND` is its
relative operand.  `quoting/brokenrel` targets `../tar get`, and the fixture
root is asserted not to contain a `tar get` sibling (design v7 section 9.2).

The generator is path-agnostic and deterministic: no output contains `root`,
and no mtime depends on the wall clock.  Do not change directory mtimes after
`build` returns.
"""

import calendar
import datetime
import os
import shutil
import socket

UMASK = 0o022


def _epoch(year, month, day, hour=0, minute=0, second=0):
    """Seconds since the Unix epoch for a UTC civil time (no local TZ)."""
    return calendar.timegm(
        datetime.datetime(year, month, day, hour, minute, second).timetuple())


BASE_SEC = _epoch(2020, 9, 13, 12, 26, 40)          # 2020-09-13 12:26:40 UTC
BASE_NS = BASE_SEC * 1_000_000_000
PRE_EPOCH_NS = _epoch(1960, 1, 1) * 1_000_000_000   # 1960-01-01, long date `-`

# A joined path of 3,911 bytes: near but under the 4,095-byte design limit.
DEEP_COMPONENT = 'd' + 'z' * 148
DEEP_DEPTH = 26
DEEP_OPERAND = '/'.join(['long', 'deep'] + [DEEP_COMPONENT] * DEEP_DEPTH + ['f'])


# ------------------------------------------------------------------ primitives

def _utime(path, ns=BASE_NS, follow=True):
    os.utime(path, ns=(ns, ns), follow_symlinks=follow)


def _file(parent, name, data=b'', mode=0o644, ns=BASE_NS):
    path = os.path.join(parent, name)
    with open(path, 'wb') as f:
        f.write(data)
    os.chmod(path, mode)
    try:
        _utime(path, ns)
    except OSError:
        # Leave no partially created file when the pinned mtime is refused
        # (for example a pre-1970 timestamp on an unsupported filesystem).
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _sparse(parent, name, size, mode=0o644, ns=BASE_NS):
    path = os.path.join(parent, name)
    with open(path, 'wb') as f:
        f.truncate(size)
    os.chmod(path, mode)
    _utime(path, ns)
    return path


def _dir(parent, name=None, mode=0o755):
    path = os.path.join(parent, name) if name is not None else parent
    os.mkdir(path)
    os.chmod(path, mode)
    return path


def _link(parent, name, target, ns=BASE_NS):
    path = os.path.join(parent, name)
    os.symlink(target, path)
    _utime(path, ns, follow=False)
    return path


def _fifo(parent, name, mode=0o644, ns=BASE_NS):
    path = os.path.join(parent, name)
    os.mkfifo(path, mode)
    os.chmod(path, mode)
    _utime(path, ns)
    return path


def _socket(parent, name, mode=0o644, ns=BASE_NS):
    path = os.path.join(parent, name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(path)
    finally:
        s.close()
    os.chmod(path, mode)
    _utime(path, ns)
    return path


def _pin(*paths):
    """Pin each directory mtime; call after all of its children exist."""
    for path in paths:
        if path is not None:
            _utime(path)


def _optional(caps, key, fn, *args, **kwargs):
    """Run a capability-dependent creation; clear the flag on failure."""
    try:
        fn(*args, **kwargs)
        return True
    except OSError:
        caps[key] = False
        return False


def _has_ambient_xattr(path):
    """True when `path` already carries an xattr we did not set.

    SELinux or similar can label every file, which makes eza append `@` to
    the long permission cell; the harness skips long goldens then.
    """
    try:
        return bool(os.listxattr(path, follow_symlinks=False))
    except (OSError, AttributeError):
        return False


# ----------------------------------------------------------------------- trees

def _empty(root, caps):
    _pin(_dir(root, 'empty'))


def _one(root, caps):
    one = _dir(root, 'one')
    _file(one, 'f', b'x\n')
    _pin(one)
    onedir = _dir(root, 'onedir')
    sub = _dir(onedir, 'sub')
    _pin(sub, onedir)


def _mixed(root, caps):
    mixed = _dir(root, 'mixed')
    _file(mixed, 'alpha', b'alpha\n')
    _file(mixed, 'Beta', b'beta\n')
    _file(mixed, 'gamma.txt', b'gamma\n')
    _dir(mixed, 'dir')
    _dir(mixed, 'sub.dir')
    _file(mixed, 'prog', b'#!/bin/sh\nexit 0\n', mode=0o755)
    _file(mixed, 'noexec', b'no\n')
    if caps['fifo']:
        _optional(caps, 'fifo', _fifo, mixed, 'pipe')
    if caps['socket']:
        _optional(caps, 'socket', _socket, mixed, 'sock')
    _file(mixed, '.hidden', b'hidden\n')
    _file(mixed, '..double', b'double\n')
    _file(mixed, '-', b'dash\n')
    _file(mixed, '--x', b'dashdash\n')
    _file(mixed, 'tar get', b'target\n')
    _link(mixed, 'link', 'alpha')
    _link(mixed, 'dirlink', 'dir')
    _link(mixed, 'broken', 'missing')
    _link(mixed, 'spaced', 'tar get')
    _pin(os.path.join(mixed, 'dir'), os.path.join(mixed, 'sub.dir'), mixed)


def _modes(root, caps):
    modes = _dir(root, 'modes')
    for name, mode in [('m000', 0o000), ('m600', 0o600), ('m644', 0o644),
                       ('m700', 0o700), ('m755', 0o755), ('m4755', 0o4755),
                       ('m2755', 0o2755), ('m1777', 0o1777)]:
        _file(modes, name, b'm\n', mode=mode)
    dirs = []
    for name, mode in [('d000', 0o000), ('d700', 0o700), ('d755', 0o755),
                       ('d1777', 0o1777)]:
        dirs.append(_dir(modes, name, mode=mode))
    if caps['fifo']:
        _optional(caps, 'fifo', _fifo, modes, 'pipe')
    if caps['socket']:
        _optional(caps, 'socket', _socket, modes, 'sock')
    _pin(*dirs, modes)


def _sizes(root, caps):
    sizes = _dir(root, 'sizes')
    for name, size in [('s000', 0), ('s001', 1), ('s999', 999),
                       ('s1000', 1000), ('s1024', 1024), ('s1025', 1025),
                       ('s4095', 4095), ('s4096', 4096),
                       ('s1m', 1024 * 1024)]:
        _file(sizes, name, b'x' * size)
    _sparse(sizes, 'sparse', 8 * 1024 * 1024)
    _pin(sizes)


def _times(root, caps):
    times = _dir(root, 'times')
    stamps = [
        ('t1970', _epoch(1970, 1, 1)),
        ('t1999', _epoch(1999, 12, 31)),
        ('t2000', _epoch(2000, 2, 29)),
        ('t2020', _epoch(2020, 1, 2, 3, 4, 5)),
        ('t2038', _epoch(2038, 1, 19, 3, 14, 7)),
        ('t2100', _epoch(2100, 3, 1)),
    ]
    for name, sec in stamps:
        _file(times, name, b't\n', ns=sec * 1_000_000_000)
    try:
        _file(times, 'tpre', b't\n', ns=PRE_EPOCH_NS)
    except OSError:
        caps['pre_epoch'] = False
    _pin(times)


TIE_TREES = (
    ('tie_Aa', ('a', 'A')),
    ('tie_readme', ('readme', 'README')),
    ('tie_pic', ('pic 4', 'pic4')),
    ('tie_space', (' a', 'a')),
    ('tie_dot', (' .',)),
)

# Pairwise comparator-distinct under both natcmp and natcmpFold, so name-order
# goldens recorded from this tree are readdir-order independent (section 7.5).
ORDERED_NAMES = (
    'Alpha', 'beta', 'Gamma', 'delta', '0', '2', '10', '100', '1000',
    '01', '003', 'rfc1', 'rfc2', 'rfc10', 'a1', 'a01b', 'a b', ' a',
    'b c', ' b', 'x y', ' y', '-x', '--x', '-', '--', 'x-',
    '.hidden', '.git', 'dot.', 'README', 'pic4', 'Z', 'under_score', '~tilde',
)


def _names(root, caps):
    names = _dir(root, 'names')
    plain = ['A', 'a', 'B', 'b', '2', '10', 'rfc1', 'rfc2', 'rfc10',
             '01', '1', '001', 'pic4', 'pic 4', 'a b', ' a', ' .',
             'README', 'readme', 'x-', '-x', '--x', '--', '-', '.hidden',
             'dot.', 'under_score', 'UPPER', 'z', 'Z', '~',
             'q' * 255]
    for name in plain:
        _file(names, name, b'n\n')
    ties = []
    for dirname, files in TIE_TREES:
        tie = _dir(names, dirname)
        for name in files:
            _file(tie, name, b't\n')
        ties.append(tie)
    ordered = _dir(names, 'ordered')
    for name in ORDERED_NAMES:
        _file(ordered, name, b'o\n')
    _pin(*ties, ordered, names)


def _ext(root, caps):
    ext = _dir(root, 'ext')
    for name in ['a.txt', 'b.tar.gz', 'noext', '.git', 'dot.', 'Z.TXT',
                 'same.txt', 'b']:
        _file(ext, name, b'e\n')
    _dir(ext, 'sub')
    child_dirs = [os.path.join(ext, 'sub')]
    for operand in ['d', 'dx.x', 'dx.']:
        od = _dir(ext, operand)
        _file(od, 'b', b'b\n')
        _file(od, 'a.txt', b'a\n')
        child_dirs.append(_dir(od, 'sub'))
        child_dirs.append(od)
    _pin(*child_dirs, ext)


def _operands(root, caps):
    operands = _dir(root, 'operands')
    _file(operands, 'f', b'f\n')
    _file(operands, 'g', b'g\n')
    _file(operands, 'target', b'decoy\n')
    real = _dir(operands, 'dir')
    _link(operands, 'l', 'f')
    _link(operands, 'dl', 'dir')
    _link(operands, 'broken', 'missing')
    sub = _dir(operands, 'sub')
    _file(sub, 'f', b'sub\n')
    _file(sub, 'target', b'#!/bin/sh\n', mode=0o755)
    sub_real = _dir(sub, 'real')
    _link(sub, 'link', 'target')
    _link(sub, 'dirlink', 'real')
    spaced = _dir(operands, 'x y')
    spaced_sub = _dir(spaced, 'sub')
    _file(spaced_sub, 'f', b'spaced\n')
    _file(spaced_sub, 'target', b'#!/bin/sh\n', mode=0o755)
    _link(spaced_sub, 'link', 'target')
    _pin(real, sub_real, sub, spaced_sub, spaced, operands)


QUOTING_NAMES = (
    'plain',
    'with space',
    "apos'trophe",
    'both \' and "',
    'dquote"name',
    'back\\slash',
    'tab\tname',
    'nl\nname',
    'cr\rname',
    'del\x7fname',
    'esc\x1bname',
    'ctl\x01name',
    'lead space',
    'trail ',
    # One whitespace-only name only: every all-whitespace name compares Equal
    # under natord (whitespace is skipped), so a second one would make every
    # name-order listing of this tree readdir-order dependent (design 9.4).
    ' ',
)


def _quoting(root, caps):
    quoting = _dir(root, 'quoting')
    for name in QUOTING_NAMES:
        _file(quoting, name, b'q\n')
    spaced_target = _dir(quoting, 'a b')
    _file(spaced_target, 'target')
    _link(quoting, 'valid spaced', 'a b/target')
    _link(quoting, 'broken spaced', 'a b/missing')
    _link(quoting, 'ctl target', 'ctl\x01target')
    # Broken relative target with no sibling `tar get` in the fixture root's
    # parent directory (asserted below); no marker even with -F=always.
    _link(quoting, 'brokenrel', '../tar get')
    _pin(spaced_target, quoting)


def _links(root, caps):
    links = _dir(root, 'links')
    _file(links, 'file', b'f\n')
    _file(links, 'exec-target', b'#!/bin/sh\n', mode=0o755)
    _file(links, 'tar get', b'target\n')
    realdir = _dir(links, 'realdir')
    _link(links, 'realfile', 'file')
    _link(links, 'realdirlink', 'realdir')
    _link(links, 'to_realdirlink', 'realdirlink')
    _link(links, 'real', 'realdir')
    _link(links, 'b', 'real')
    _link(links, 'chain', 'realfile')
    _link(links, 'exec', 'exec-target')
    _link(links, 'spaced', 'tar get')
    _link(links, 'loop1', 'loop2')
    _link(links, 'loop2', 'loop1')
    _link(links, 'dangling', 'nowhere')
    _pin(realdir, links)


def _long(root, caps):
    longdir = _dir(root, 'long')
    _file(longdir, 'empty', b'')
    _file(longdir, 'one', b'x')
    _file(longdir, 'small', b'x' * 999)
    _file(longdir, 'kilo', b'x' * 1000)
    _sparse(longdir, 'big', 1_234_567)          # seven digits under --bytes
    _file(longdir, 'exec', b'#!/bin/sh\n', mode=0o755)
    _dir(longdir, 'dir')
    _link(longdir, 'link', 'one')
    _link(longdir, 'dangling', 'missing')
    if caps['fifo']:
        _optional(caps, 'fifo', _fifo, longdir, 'pipe')
    if caps['socket']:
        _optional(caps, 'socket', _socket, longdir, 'sock')
    _file(longdir, 'pre', b'p\n')
    if caps['pre_epoch']:
        try:
            _utime(os.path.join(longdir, 'pre'), PRE_EPOCH_NS)
        except OSError:
            caps['pre_epoch'] = False
    # A joined path near but under 4095 bytes.
    deep = _dir(longdir, 'deep')
    chain = [deep]
    try:
        for _ in range(DEEP_DEPTH):
            chain.append(_dir(chain[-1], DEEP_COMPONENT))
        _file(chain[-1], 'f', b'deep\n')
    except OSError:
        caps['deep_path'] = False
    for path in reversed(chain):
        _pin(path)
    _pin(os.path.join(longdir, 'dir'), longdir)


def _many(root, caps):
    many = _dir(root, 'many')
    for i in range(100):
        _file(many, 'f%03d' % i, b'%d\n' % i)
    _pin(many)


def _grid(root, caps):
    specs = [
        ('grid1', 1, 'g'),
        ('grid2', 2, 'g%d'),
        ('grid10', 10, 'g%d'),
        ('grid100', 100, 'g%02d'),
        ('gridlong10', 10, 'file-number-%02d-with-a-long-name'),
        ('gridlong100', 100, 'file-number-%03d-with-a-long-name'),
    ]
    for dirname, count, fmt in specs:
        d = _dir(root, dirname)
        for i in range(count):
            _file(d, fmt % i if count > 1 else fmt, b'g\n')
        _pin(d)


def _invalid(root, caps):
    base = os.fsencode(root)
    try:
        invalid = os.path.join(base, b'invalid')
        os.mkdir(invalid)
        os.chmod(invalid, 0o755)
        bad = os.path.join(invalid, b'bad-\xff-name')
        with open(bad, 'wb') as f:
            f.write(b'bad\n')
        os.chmod(bad, 0o644)
        _utime(bad)
        _utime(invalid)
    except (OSError, UnicodeError):
        caps['invalid_utf8'] = False


def _hard(root, caps):
    hard = _dir(root, 'hard')
    one = _file(hard, 'one', b'hard\n')
    try:
        os.link(one, os.path.join(hard, 'two'))
    except OSError:
        caps['hardlink'] = False
    _pin(hard)


def _xattr(root, caps):
    xattr = _dir(root, 'xattr')
    target = _file(xattr, 'file', b'x\n')
    try:
        os.setxattr(target, b'user.obo', b'1')
        caps['xattr'] = True
    except OSError:
        caps['xattr'] = False
    _pin(xattr)


def _absolute(root, caps):
    """Live-only: absolute and broken-absolute symlink targets."""
    absolute = _dir(root, 'absolute')
    _link(absolute, 'tmp', '/tmp')
    _link(absolute, 'tmp_slash', '/tmp/')
    _link(absolute, 'root', '/')
    _link(absolute, 'spaceabs', '/tar get')
    _pin(absolute)


def _locked(root, caps):
    """Permission fixtures; running as root changes their behaviour."""
    locked = _dir(root, 'locked')
    denied = _dir(locked, 'denied', mode=0o755)
    nosearch = _dir(locked, 'nosearch', mode=0o755)
    _link(nosearch, 'lnk', 'x')
    os.chmod(denied, 0o000)
    os.chmod(nosearch, 0o444)
    _pin(denied, nosearch, locked)


def _selflink(root, caps):
    """A self-directed link: `d/self -> .` (the synthetic dot target quirk)."""
    selflink = _dir(root, 'selflink')
    d = _dir(selflink, 'd')
    _file(d, 'f', b'self\n')
    _link(d, 'self', '.')
    _pin(d, selflink)


def _tree(root, caps):
    tree = _dir(root, 'tree')
    _file(tree, 'a.txt', b'a\n')
    empty = _dir(tree, 'emptydir')
    hidden = _dir(tree, 'hidden')
    _file(hidden, '.secret', b's\n')
    _file(tree, '.hiddenfile', b'h\n')
    d1 = _dir(tree, 'd1')
    _file(d1, 'b.txt', b'b\n')
    d2 = _dir(d1, 'd2')
    _file(d2, 'c.txt', b'c\n')
    d3 = _dir(d2, 'd3')
    _file(d3, 'd.txt', b'd\n')
    _link(tree, 'dirlink', 'd1')
    _link(tree, 'broken', 'missing')
    _pin(empty, hidden, d3, d2, d1, tree)


def build(root):
    """Create every fixture tree under `root`; return host-capability flags."""
    os.makedirs(root, exist_ok=True)
    old_umask = os.umask(UMASK)
    caps = {
        'euid': os.geteuid(),
        'umask': UMASK,
        'pre_epoch': True,
        'xattr': False,
        'invalid_utf8': True,
        'fifo': True,
        'socket': True,
        'hardlink': True,
        'symlink': True,
        'deep_path': True,
        'nonroot': os.geteuid() != 0,
        'dev_full': os.path.exists('/dev/full'),
        'ambient_xattr': False,
    }
    try:
        _empty(root, caps)
        _one(root, caps)
        _mixed(root, caps)
        _modes(root, caps)
        _sizes(root, caps)
        _times(root, caps)
        _names(root, caps)
        _ext(root, caps)
        _operands(root, caps)
        _quoting(root, caps)
        _links(root, caps)
        _long(root, caps)
        _many(root, caps)
        _grid(root, caps)
        _invalid(root, caps)
        _hard(root, caps)
        _xattr(root, caps)
        _absolute(root, caps)
        _locked(root, caps)
        _selflink(root, caps)
        _tree(root, caps)
        # Design section 9.2: the broken relative `../tar get` probe must have
        # no sibling `tar get` in the case directory's parent.
        if os.path.lexists(os.path.join(root, 'tar get')):
            raise RuntimeError('fixture invariant: a root-level "tar get" exists')
        _pin(root)
        # Sample files we never label ourselves: if the host adds xattrs
        # (SELinux), long goldens would grow an `@` permission marker.
        caps['ambient_xattr'] = (
            _has_ambient_xattr(os.path.join(root, 'mixed', 'alpha'))
            or _has_ambient_xattr(root))
        return caps
    finally:
        os.umask(old_umask)


def build_many(root, count=10000):
    """A larger name-only tree for the memory cases (not part of build)."""
    d = os.path.join(root, 'many%d' % count)
    os.makedirs(d, exist_ok=True)
    old = os.umask(UMASK)
    try:
        for i in range(count):
            p = os.path.join(d, 'f%05d' % i)
            with open(p, 'wb') as f:
                f.write(b'x\n')
            os.chmod(p, 0o644)
            _utime(p)
        _pin(d)
        return d
    finally:
        os.umask(old)


def _make_removable(path):
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return
    for entry in entries:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False
        if is_dir:
            _make_removable(entry.path)


def rmtree(path):
    """Remove a fixture tree even when it contains 000/0444 directories."""
    if not os.path.lexists(path):
        return
    _make_removable(path)
    shutil.rmtree(path, ignore_errors=True)


if __name__ == '__main__':
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    print(build(root))