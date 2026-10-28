#!/usr/bin/env python3
"""The differential gate for the obo command-line tools (cat, wc, sort).

    python3 tests/tools.py [-k PATTERN] [-v] [-x] [-j N]
        [--reference=auto|gnu|busybox|any|none] [--require-reference]
        [--check-models/--no-check-models] [--record [--force]]
        [--memory] [--fast] [--keep] [--list]

This is a separate gate from the compiler suites: it is not in run_fork.py and
not part of `make promote`. The tools are built in a private temporary
directory with tools/build_tools.py (the same module-root rule as `make tools`:
the sources are compiled directly, with their own private symbol directory),
then each case runs our tool and a reference utility in deterministic per-case
directories and compares what users observe: stdout bytes, exit status, and
stderr.

The reference is GNU coreutils 9.x primarily, resolved only through the pinned
PATH /usr/bin:/bin and executed as `[tool, *args]` with `executable=<absolute
path>`: argv[0] is the bare tool name, so GNU's diagnostics carry the same
`<tool>: ` prefix as ours. Every reference process (--version probing, cases,
model-vs-GNU, --record) uses that invocation. A defensive normalizer replaces a
leading `<ref_path>: ` on every captured stderr line with `<tool>: ` and warns;
recorded and loaded goldens must have no `/`-leading stderr line.

Oracle tags (exactly one per case, plus the orthogonal `portable` marker):
  modeled    GNU >= 9 is the primary oracle (stdout/exit exact, file-error
             stderr exact, success stderr empty); without a usable reference the
             Python model is the oracle; frozen gnu goldens are also compared.
  usage      our message, pinned by a hand-written own golden; the reference is
             never invoked.
  writeerror stdout is /dev/full (the sink); our stdout/stderr/exit are pinned by
             an own golden, GNU's stdout (empty) and exit are exact and its
             stderr is shape-compared. Models are never consulted, because they
             have no stdout destination and do not implement ENOSPC.
`portable` marks modeled cases whose stdout/exit are implementation-independent
(cat byte copy, default sort); it selects the subset for a forced
`--reference=busybox` and never changes the oracle tag. wc has no portable cases
in v1 (its padding is implementation-specific).

`--check-models` (default on) compares each modeled case's Python model against
GNU on stdout/exit (and exact stderr where it is compared) and exits 2 on a
disagreement, so the fallback cannot rot silently. `--record` writes gnu goldens
(modeled cases only, golden-marked subset only) and gnu/VERSION, and refuses a
stderr line beginning with `/`. `--memory` runs the two pinned memory cases
instead of the matrix. Exit status: 0 pass, 1 test failure, 2 harness/setup
failure.
"""
import argparse
import atexit
import concurrent.futures
import os
import re
import resource
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)                      # .../obo
ROOT = os.path.dirname(FORK)                      # repository root
TOOLS = ('cat', 'wc', 'sort')
REFERENCE_PATH = '/usr/bin:/bin'
FSIZE = 16 * 1024 * 1024                          # per-process output cap
MEMORY_PEAK_MAX = 1048576
GOLDEN = os.path.join(HERE, 'tools', 'golden')

sys.path.insert(0, os.path.join(FORK, 'tools'))
import build_tools  # noqa: E402


class SetupError(Exception):
    pass


class ModelError(Exception):
    pass


# --------------------------------------------------------------------- fixtures

class Spec:
    def __init__(self, kind='file', data=None, size=None, zero_fill=False):
        self.kind = kind            # file | dir | missing | unreadable
        self.data = data
        self.size = len(data) if data is not None else (size or 0)
        self.zero_fill = zero_fill

    def bytes(self):
        if self.data is not None:
            return self.data
        if self.zero_fill:
            return b'\x00' * self.size
        return b''


BIG = b''.join(('%07d alpha beta gamma delta epsilon zeta eta theta\n' % i).encode()
               for i in range(20000))
MANY = b''.join(('%06d line %d\n' % (i % 1000, i)).encode() for i in range(50000))

FILES = {
    'empty': b'',
    'newline': b'\n',
    'noeol': b'a\nb',
    'oneline': b'hello\n',
    'blanks': b'a\n\n\n\nb\n',
    'spaces': b'   \nx\n',
    'tabs': b'a\tb\t\n',
    'crlf': b'a\r\nb\r\n',
    'words': b'  alpha\tbeta\x0bgamma\x0cdelta\repsilon zeta\nomega\npartial',
    'nul': b'a\x00b c\n\x00\n',
    'binary': bytes(range(256)) * 8,
    'invalid_utf8': b'\xff\xfe\n\x80\x81\n' + b'\xff' * 100 + b'\n',
    'longline': b'x' * 100000 + b'\n' + b'y' * 100000,
    'twelve': b'x\n' * 12,
    'long100': (b'a' * 99 + b'\n') * 100,
    'many2': b'a\n' * 12,
    'abnulcd': b'ab\x00cd\n',
    'sep': b'a\x0bb\x0cc\rd e\tf\ng\n',
    'ctl': b'a\x01b\x7fc\x80d\xff\xff\n',
    'many': MANY,
    'big': BIG,
    'dups': b'b\na\nb\nA\na\n',
    'uniq': b'01 y\n1 x\n',
    'rnu2': b'1 y\n01 x\n',
    'tie': b'1 b\n1 a\n',
    'numeric': (b'\n+2\n-0\n0\n0x10\nabc\n.5\n1e3\n02\n2\n2.\n 4\n-10\n-2\n'
                b'-0.5\n\x0b5\n1e40\n' + b'9' * 40 + b'\n' + b'1' + b'0' * 40 +
                b'\n-.5\n- 5\n--5\n.\n.0\n0.\n'),
    'near': b'20000000000000000001\n20000000000000000002\n',
    'near40': b'1' + b'0' * 38 + b'1\n' + b'1' + b'0' * 38 + b'2\n',
    'nines40': b'9' * 40 + b'\n' + b'1' + b'0' * 40 + b'\n',
    'e40': b'1e40\n',
    'endblank': b'a\n\n',
    'startblank': b'\n\nb\n',
    'leadtrail': b'\n\nb\n\n\n',
    'ab': b'a\n',
    'multi_a': b'b\na\n',
    'multi_b': b'c',
    'split_a': b'a',
    'split_b': b'b',
    'one': b'one\n',
    'two': b'two\n',
    '-n': b'-n\n',
    '-Z': b'-Z\n',
    'name with space': b'space\n',
    'million': b'\n' * 1000001,
    'crlfblanks': b'\r\n\r\n\r\n',
    'cr_eof': b'a\r',
    'cr_cross_a': b'a\r',
    'cr_cross_b': b'\nb\n',
    'cr_chunk': b'x' * 65535 + b'\r\n' + b'y\n',
    'cr_double': b'a\r\r\n',
    'cr_mid': b'a\rX\n',
}

SPECS = {name: Spec('file', data=data) for name, data in FILES.items()}
SPECS['sparse'] = Spec('file', size=1 << 20, zero_fill=True)
SPECS['tenm'] = Spec('file', size=10000000, zero_fill=True)
SPECS['sparse16m'] = Spec('file', size=16 << 20, zero_fill=True)
SPECS['dir'] = Spec('dir')
SPECS['unreadable'] = Spec('unreadable', data=b'u')
for _m in ('missing', 'nope1', 'nope2', 'nope'):
    SPECS[_m] = Spec('missing')
SPECS['/dev/null'] = Spec('file', data=b'')

ERRTEXT = {
    2: 'No such file or directory',
    5: 'Input/output error',
    9: 'Bad file descriptor',
    11: 'Resource temporarily unavailable',
    13: 'Permission denied',
    20: 'Not a directory',
    21: 'Is a directory',
    22: 'Invalid argument',
    24: 'Too many open files',
    28: 'No space left on device',
    32: 'Broken pipe',
    36: 'File name too long',
    40: 'Too many levels of symbolic links',
}


def errtext(code):
    return ERRTEXT.get(code, 'error %d' % code)


def materialize(name, casedir):
    """Create one fixture in the case directory with an explicit mode."""
    spec = SPECS[name]
    if name == '/dev/null':
        if not os.path.exists('/dev/null'):
            raise SetupError('the wc matrix needs /dev/null')
        return
    path = os.path.join(casedir, name)
    if spec.kind == 'dir':
        os.mkdir(path, 0o755)
        os.chmod(path, 0o755)
    elif spec.kind == 'missing':
        pass
    elif spec.kind == 'unreadable':
        with open(path, 'wb') as f:
            f.write(spec.data)
        os.chmod(path, 0o600)
        os.chmod(path, 0o000)
    else:
        if spec.zero_fill:
            with open(path, 'wb') as f:
                f.truncate(spec.size)
        else:
            with open(path, 'wb') as f:
                f.write(spec.data)
        os.chmod(path, 0o644)


# ------------------------------------------------------------------------- cases

class Case:
    def __init__(self, name, tool, args, stdin=None, fixtures=(), tag='modeled',
                 portable=False, golden=False, slow=False, sink=False, note=''):
        self.name = name
        self.tool = tool
        self.args = list(args)
        self.stdin = stdin
        self.fixtures = tuple(fixtures)
        self.tag = tag
        self.portable = portable
        self.golden = golden
        self.slow = slow
        self.sink = sink
        self.note = note

    @property
    def key(self):
        return (self.tool, self.name)

    def __repr__(self):
        return f'{self.tool}/{self.name}'


CASES = []


def add(name, tool, args, **kw):
    c = Case(name, tool, args, **kw)
    CASES.append(c)
    return c


def validate_cases():
    """A tag typo must not run a case that enters no comparison branch."""
    for c in CASES:
        if c.tag not in ('modeled', 'usage', 'writeerror'):
            raise SetupError(f'{c}: unknown tag {c.tag!r}')
        if c.portable and c.tag != 'modeled':
            raise SetupError(f'{c}: the portable marker is only for modeled cases')
        if c.golden and c.tag != 'modeled':
            raise SetupError(f'{c}: golden goldens are only for modeled cases')
        if c.tag == 'usage' and c.sink:
            raise SetupError(f'{c}: a usage case cannot use the /dev/full sink')
        if c.tag == 'writeerror' and not c.sink:
            raise SetupError(f'{c}: a writeerror case must use the /dev/full sink')


def cat_cases():
    T = 'cat'
    # byte copy over every stream fixture; the six marked ones are the portable set
    add('empty', T, ['empty'], fixtures=['empty'], portable=True)
    add('newline', T, ['newline'], fixtures=['newline'])
    add('noeol', T, ['noeol'], fixtures=['noeol'], portable=True)
    add('oneline', T, ['oneline'], fixtures=['oneline'], portable=True)
    add('blanks', T, ['blanks'], fixtures=['blanks'])
    add('spaces', T, ['spaces'], fixtures=['spaces'])
    add('tabs', T, ['tabs'], fixtures=['tabs'])
    add('crlf', T, ['crlf'], fixtures=['crlf'])
    add('words', T, ['words'], fixtures=['words'])
    add('nul', T, ['nul'], fixtures=['nul'], portable=True)
    add('binary', T, ['binary'], fixtures=['binary'], portable=True)
    add('invalid_utf8', T, ['invalid_utf8'], fixtures=['invalid_utf8'], portable=True)
    add('longline', T, ['longline'], fixtures=['longline'])
    add('multi_a_multi_b', T, ['multi_a', 'multi_b'],
        fixtures=['multi_a', 'multi_b'], portable=True)
    add('stdin_words', T, [], stdin=FILES['words'], portable=True)
    add('dash_multi_a', T, ['-', 'multi_a'], stdin=b'z\n', fixtures=['multi_a'],
        portable=True)
    add('second_dash', T, ['-', '-'], stdin=b'one\ntwo\n')
    # open/read errors: print and continue
    add('missing_oneline', T, ['missing', 'oneline'], fixtures=['oneline'])
    add('dir', T, ['dir'], fixtures=['dir'])
    add('dir_oneline', T, ['dir', 'oneline'], fixtures=['dir', 'oneline'])
    add('dir_missing_oneline', T, ['dir', 'missing', 'oneline'],
        fixtures=['dir', 'oneline'])
    add('unreadable_oneline', T, ['unreadable', 'oneline'],
        fixtures=['unreadable', 'oneline'], note='skip if unreadable is readable')
    # flags and combinations
    add('n_blanks', T, ['-n', 'blanks'], fixtures=['blanks'], golden=True)
    add('b_blanks', T, ['-b', 'blanks'], fixtures=['blanks'], golden=True)
    add('n_b_blanks', T, ['-n', '-b', 'blanks'], fixtures=['blanks'])
    add('b_n_blanks', T, ['-b', '-n', 'blanks'], fixtures=['blanks'])
    add('s_blanks', T, ['-s', 'blanks'], fixtures=['blanks'])
    add('E_multi_a', T, ['-E', 'multi_a'], fixtures=['multi_a'])
    # GNU -E holds a CR before an emitted LF and prints it as ^M
    add('E_crlf', T, ['-E', 'crlf'], fixtures=['crlf'], golden=True)
    add('nE_crlf', T, ['-nE', 'crlfblanks'], fixtures=['crlfblanks'], golden=True)
    add('bE_crlf', T, ['-bE', 'crlfblanks'], fixtures=['crlfblanks'], golden=True)
    add('sE_crlf', T, ['-sE', 'crlfblanks'], fixtures=['crlfblanks'], golden=True)
    add('E_cr_eof', T, ['-E', 'cr_eof'], fixtures=['cr_eof'], golden=True)
    add('E_cr_cross_file', T, ['-E', 'cr_cross_a', 'cr_cross_b'],
        fixtures=['cr_cross_a', 'cr_cross_b'], golden=True)
    add('E_cr_chunk', T, ['-E', 'cr_chunk'], fixtures=['cr_chunk'], golden=True)
    add('E_cr_double', T, ['-E', 'cr_double'], fixtures=['cr_double'], golden=True)
    add('E_cr_mid', T, ['-E', 'cr_mid'], fixtures=['cr_mid'], golden=True)
    add('T_crlf', T, ['-T', 'crlf'], fixtures=['crlf'], golden=True)
    add('T_tabs', T, ['-T', 'tabs'], fixtures=['tabs'])
    add('nET_tabs', T, ['-nET', 'tabs'], fixtures=['tabs'], golden=True)
    add('s_n_blanks', T, ['-s', '-n', 'blanks'], fixtures=['blanks'])
    add('s_b_blanks', T, ['-s', '-b', 'blanks'], fixtures=['blanks'])
    add('n_multi_a_multi_b', T, ['-n', 'multi_a', 'multi_b'],
        fixtures=['multi_a', 'multi_b'], golden=True)
    add('E_multi_a_multi_b', T, ['-E', 'multi_a', 'multi_b'],
        fixtures=['multi_a', 'multi_b'], golden=True)
    add('noeol_oneline', T, ['noeol', 'oneline'], fixtures=['noeol', 'oneline'])
    add('n_noeol_oneline', T, ['-n', 'noeol', 'oneline'],
        fixtures=['noeol', 'oneline'])
    add('E_noeol_oneline', T, ['-E', 'noeol', 'oneline'],
        fixtures=['noeol', 'oneline'])
    # cross-file squeeze state
    add('s_endblank_startblank', T, ['-s', 'endblank', 'startblank'],
        fixtures=['endblank', 'startblank'], golden=True)
    add('sn_endblank_startblank', T, ['-sn', 'endblank', 'startblank'],
        fixtures=['endblank', 'startblank'])
    add('sn_leadtrail', T, ['-sn', 'leadtrail'], fixtures=['leadtrail'])
    add('s_leadtrail_startblank', T, ['-s', 'leadtrail', 'startblank'],
        fixtures=['leadtrail', 'startblank'])
    add('s_four_files', T, ['-s', 'endblank', 'startblank', 'endblank', 'startblank'],
        fixtures=['endblank', 'startblank'])
    add('n_spaces', T, ['-n', 'spaces'], fixtures=['spaces'])
    add('b_nul', T, ['-b', 'nul'], fixtures=['nul'])
    add('bn_nul', T, ['-bn', 'nul'], fixtures=['nul'])
    add('sparse', T, ['sparse'], fixtures=['sparse'], slow=True)
    add('many', T, ['many'], fixtures=['many'], slow=True)
    add('n_million', T, ['-n', 'million'], fixtures=['million'], slow=True)
    add('ddash_oneline', T, ['--', 'oneline'], fixtures=['oneline'])
    add('ddash_dash_n', T, ['--', '-n'], fixtures=['-n'])
    add('n_ddash_dash_n', T, ['-n', '--', '-n'], fixtures=['-n'])
    add('name_with_space', T, ['name with space'], fixtures=['name with space'])
    # /dev/full: the empty-input case issues no write and is modeled; the rest
    # are writeerror and are never handed to a model
    add('devfull_empty', T, ['empty'], fixtures=['empty'], sink=True, golden=True)
    add('devfull_oneline', T, ['oneline'], fixtures=['oneline'], sink=True,
        tag='writeerror')
    add('devfull_oneline_missing_multi_a', T, ['oneline', 'missing', 'multi_a'],
        fixtures=['oneline', 'multi_a'], sink=True, tag='writeerror')
    add('devfull_missing_oneline', T, ['missing', 'oneline'],
        fixtures=['oneline'], sink=True, tag='writeerror')
    add('devfull_missing_oneline_multi_a', T, ['missing', 'oneline', 'multi_a'],
        fixtures=['oneline', 'multi_a'], sink=True, tag='writeerror')
    # usage messages are ours, pinned by own goldens
    add('usage_z', T, ['-Z'], tag='usage')
    add('usage_help', T, ['--help'], tag='usage')
    add('usage_version', T, ['--version'], tag='usage')


def wc_cases():
    T = 'wc'
    # all columns on each stream fixture
    for name in ('empty', 'newline', 'noeol', 'oneline', 'blanks', 'spaces',
                 'tabs', 'crlf', 'words', 'nul', 'binary', 'invalid_utf8'):
        add(name, T, [name], fixtures=[name])
    # selected columns, fixed l/w/c order
    add('l_words', T, ['-l', 'words'], fixtures=['words'])
    add('w_words', T, ['-w', 'words'], fixtures=['words'])
    add('c_words', T, ['-c', 'words'], fixtures=['words'])
    add('lw_words', T, ['-lw', 'words'], fixtures=['words'])
    add('lc_words', T, ['-lc', 'words'], fixtures=['words'])
    add('wc_words', T, ['-wc', 'words'], fixtures=['words'])
    add('lwc_words', T, ['-lwc', 'words'], fixtures=['words'])
    add('l_multi_a_multi_b', T, ['-l', 'multi_a', 'multi_b'],
        fixtures=['multi_a', 'multi_b'])
    # stdin (always through a pipe, so GNU's width floor is 7)
    add('stdin_words', T, [], stdin=FILES['words'])
    add('l_stdin', T, ['-l'], stdin=FILES['words'])
    add('l_dash', T, ['-l', '-'], stdin=FILES['words'])
    add('l_dash_multi_a', T, ['-l', '-', 'multi_a'], stdin=FILES['words'],
        fixtures=['multi_a'])
    add('l_ddash_dash', T, ['-l', '--', '-'], stdin=FILES['words'])
    # B1 word rule
    add('w_nul', T, ['-w', 'nul'], fixtures=['nul'])
    add('w_binary', T, ['-w', 'binary'], fixtures=['binary'])
    add('w_invalid_utf8', T, ['-w', 'invalid_utf8'], fixtures=['invalid_utf8'])
    add('w_abnulcd', T, ['-w', 'abnulcd'], fixtures=['abnulcd'])
    add('w_sep', T, ['-w', 'sep'], fixtures=['sep'])
    add('w_ctl', T, ['-w', 'ctl'], fixtures=['ctl'])
    add('w_sparse', T, ['-w', 'sparse'], fixtures=['sparse'], slow=True)
    add('c_sparse', T, ['-c', 'sparse'], fixtures=['sparse'], slow=True)
    # widths
    add('l_twelve', T, ['-l', 'twelve'], fixtures=['twelve'])
    add('l_long100_many2', T, ['-l', 'long100', 'many2'],
        fixtures=['long100', 'many2'], golden=True)
    add('lw_long100', T, ['-lw', 'long100'], fixtures=['long100'], golden=True)
    add('lw_long100_many2', T, ['-lw', 'long100', 'many2'],
        fixtures=['long100', 'many2'])
    add('lw_twelve', T, ['-lw', 'twelve'], fixtures=['twelve'])
    add('l_dash_twelve', T, ['-l', '-', 'twelve'], stdin=FILES['words'],
        fixtures=['twelve'], golden=True)
    add('l_dash_tenm', T, ['-l', '-', 'tenm'], stdin=FILES['words'],
        fixtures=['tenm'], slow=True)
    add('l_dir_tenm', T, ['-l', 'dir', 'tenm'], fixtures=['dir', 'tenm'],
        slow=True)
    add('l_dir_twelve', T, ['-l', 'dir', 'twelve'], fixtures=['dir', 'twelve'])
    add('l_dir_empty', T, ['-l', 'dir', 'empty'], fixtures=['dir', 'empty'],
        golden=True)
    add('l_dir', T, ['-l', 'dir'], fixtures=['dir'], golden=True)
    add('lwc_dir', T, ['-lwc', 'dir'], fixtures=['dir'])
    add('l_empty_twelve', T, ['-l', 'empty', 'twelve'],
        fixtures=['empty', 'twelve'])
    add('l_empty_empty', T, ['-l', 'empty', 'empty'], fixtures=['empty'])
    add('l_devnull', T, ['-l', '/dev/null'], fixtures=['/dev/null'])
    # totals and failures
    add('l_multi_a_missing', T, ['-l', 'multi_a', 'missing'],
        fixtures=['multi_a'], golden=True)
    add('missing', T, ['missing'])
    add('l_nope1_nope2', T, ['-l', 'nope1', 'nope2'], golden=True)
    add('l_empty_nope', T, ['-l', 'empty', 'nope'], fixtures=['empty'])
    add('lw_nope1_nope2', T, ['-lw', 'nope1', 'nope2'])
    add('l_dash_nope', T, ['-l', '-', 'nope'], stdin=FILES['words'])
    add('unreadable', T, ['unreadable'], fixtures=['unreadable'],
        note='skip if unreadable is readable')
    add('l_unreadable_twelve', T, ['-l', 'unreadable', 'twelve'],
        fixtures=['unreadable', 'twelve'], golden=True,
        note='skip if unreadable is readable')
    add('l_unreadable_empty', T, ['-l', 'unreadable', 'empty'],
        fixtures=['unreadable', 'empty'], note='skip if unreadable is readable')
    add('lw_unreadable_twelve', T, ['-lw', 'unreadable', 'twelve'],
        fixtures=['unreadable', 'twelve'], note='skip if unreadable is readable')
    add('w_split_a_split_b', T, ['-w', 'split_a', 'split_b'],
        fixtures=['split_a', 'split_b'])
    add('l_split_a_split_b', T, ['-l', 'split_a', 'split_b'],
        fixtures=['split_a', 'split_b'])
    add('devfull_oneline', T, ['oneline'], fixtures=['oneline'], sink=True,
        tag='writeerror')
    add('usage_z', T, ['-Z'], tag='usage')
    add('usage_help', T, ['--help'], tag='usage')


def sort_cases():
    T = 'sort'
    default = ('empty', 'oneline', 'noeol', 'dups', 'nul', 'binary',
               'invalid_utf8', 'longline', 'crlf', 'words', 'spaces', 'tabs',
               'newline', 'multi_a')
    for name in default:
        add(name, T, [name], fixtures=[name],
            portable=name in ('empty', 'oneline', 'dups'), golden=(name == 'nul'))
    add('many', T, ['many'], fixtures=['many'], slow=True)
    add('sparse', T, ['sparse'], fixtures=['sparse'], slow=True)
    add('big', T, ['big'], fixtures=['big'], slow=True)
    add('multi_a_multi_b', T, ['multi_a', 'multi_b'],
        fixtures=['multi_a', 'multi_b'], portable=True)
    add('r_dups', T, ['-r', 'dups'], fixtures=['dups'])
    add('u_dups', T, ['-u', 'dups'], fixtures=['dups'])
    add('ru_dups', T, ['-ru', 'dups'], fixtures=['dups'])
    # numeric grammar and exact decimal
    add('n_numeric', T, ['-n', 'numeric'], fixtures=['numeric'], golden=True)
    add('rn_numeric', T, ['-rn', 'numeric'], fixtures=['numeric'])
    add('nu_numeric', T, ['-nu', 'numeric'], fixtures=['numeric'])
    add('rnu_numeric', T, ['-rnu', 'numeric'], fixtures=['numeric'])
    add('n_dups', T, ['-n', 'dups'], fixtures=['dups'])
    add('rn_dups', T, ['-rn', 'dups'], fixtures=['dups'])
    add('nu_dups', T, ['-nu', 'dups'], fixtures=['dups'])
    add('rnu_dups', T, ['-rnu', 'dups'], fixtures=['dups'])
    add('n_empty', T, ['-n', 'empty'], fixtures=['empty'])
    add('n_spaces', T, ['-n', 'spaces'], fixtures=['spaces'])
    add('n_nines40', T, ['-n', 'nines40'], fixtures=['nines40'], golden=True)
    add('n_e40', T, ['-n', 'e40'], fixtures=['e40'], golden=True)
    add('nu_near', T, ['-nu', 'near'], fixtures=['near'], golden=True)
    add('nu_near40', T, ['-nu', 'near40'], fixtures=['near40'], golden=True)
    add('nu_uniq', T, ['-nu', 'uniq'], fixtures=['uniq'], golden=True)
    add('rnu_rnu2', T, ['-rnu', 'rnu2'], fixtures=['rnu2'], golden=True)
    add('nr_tie', T, ['-nr', 'tie'], fixtures=['tie'], golden=True)
    # multi-file, per-file final-line finalization, mixed stdin
    add('noeol_multi_b', T, ['noeol', 'multi_b'],
        fixtures=['noeol', 'multi_b'])
    add('multi_a_dir', T, ['multi_a', 'dir'], fixtures=['multi_a', 'dir'])
    add('dash_multi_a', T, ['-', 'multi_a'], stdin=b'c\nb\n',
        fixtures=['multi_a'])
    add('multi_a_dash', T, ['multi_a', '-'], stdin=b'c\nb\n',
        fixtures=['multi_a'])
    add('stdin_words', T, [], stdin=FILES['words'])
    add('dash_alone', T, ['-'], stdin=FILES['words'])
    # errors: preflight order and read errors, no stdout
    add('missing', T, ['missing'])
    add('multi_a_missing', T, ['multi_a', 'missing'], fixtures=['multi_a'])
    add('dir', T, ['dir'], fixtures=['dir'], golden=True)
    add('multi_a_dir_isolated', T, ['multi_a', 'dir'],
        fixtures=['multi_a', 'dir'])
    add('dir_missing', T, ['dir', 'missing'], fixtures=['dir'], golden=True)
    add('missing_dir', T, ['missing', 'dir'], fixtures=['dir'])
    add('unreadable_dir', T, ['unreadable', 'dir'], fixtures=['unreadable', 'dir'],
        note='skip if unreadable is readable')
    add('dir_unreadable', T, ['dir', 'unreadable'], fixtures=['dir', 'unreadable'],
        note='skip if unreadable is readable')
    add('ab_dir_ab', T, ['ab', 'dir', 'ab'], fixtures=['ab', 'dir'])
    add('ddash_dups', T, ['--', 'dups'], fixtures=['dups'])
    add('ddash_dash_n', T, ['--', '-n'], fixtures=['-n'])
    add('devfull_oneline', T, ['oneline'], fixtures=['oneline'], sink=True,
        tag='writeerror')
    add('usage_z', T, ['-Z'], tag='usage')
    add('usage_help', T, ['--help'], tag='usage')


cat_cases()
wc_cases()
sort_cases()
CASES.sort(key=lambda c: (TOOLS.index(c.tool), c.name))

# ------------------------------------------------------------------------ models


def parse_argv(tool, argv):
    """GNU-style permutation, a lone '-' as an operand, '--' ends options."""
    opts = set()
    operands = []
    end = False
    known = {'cat': set('nbsET'), 'wc': set('lwc'), 'sort': set('nru')}[tool]
    code = 1 if tool in ('cat', 'wc') else 2
    for a in argv:
        if not end and a == '--':
            end = True
        elif not end and len(a) > 1 and a.startswith('-'):
            if len(a) > 2 and a[1] == '-':
                raise ModelError(f"{tool}: unrecognized option '{a}'")
            for ch in a[1:]:
                if ch not in known:
                    raise ModelError(f"{tool}: invalid option -- '{ch}'")
                opts.add(ch)
        else:
            operands.append(a)
    return opts, operands, code


def kind_of(name, world):
    if name == '-':
        return 'stdin'
    spec = world.get(name)
    if spec is None:
        return SPECS.get(name, Spec('missing')).kind
    return spec.kind


def model_cat(world, argv, stdin_bytes):
    opts, operands, _ = parse_argv('cat', argv)
    number_every = 'n' in opts
    number_nonblank = 'b' in opts
    squeeze = 's' in opts
    show_ends = 'E' in opts
    show_tabs = 'T' in opts
    out = bytearray()
    err = bytearray()
    line_no = 0
    at_start = True
    last_blank = False
    pending_cr = False
    failed = False
    stdin_used = False

    if not operands:
        operands = ['-']

    def apply_chunk(data):
        nonlocal line_no, at_start, last_blank, pending_cr
        for b in data:
            handled = False
            if pending_cr:
                # -E holds a CR for a possible LF: emit ^M$ on LF, else the CR
                pending_cr = False
                if b == 0x0A:
                    out.extend(b'^M$\n')
                    at_start = True
                    last_blank = False
                    handled = True
                else:
                    out.append(0x0D)
            if not handled:
                if at_start:
                    if b == 0x0A:
                        if squeeze and last_blank:
                            pass
                        else:
                            if number_every and not number_nonblank:
                                line_no += 1
                                out.extend(b'%6d\t' % line_no)
                            if show_ends:
                                out.append(0x24)
                            out.append(0x0A)
                        last_blank = True
                    else:
                        line_no += 1
                        if number_every or number_nonblank:
                            out.extend(b'%6d\t' % line_no)
                        at_start = False
                        last_blank = False
                        if b == 0x09 and show_tabs:
                            out.extend(b'^I')
                        elif b == 0x0D and show_ends:
                            pending_cr = True
                        else:
                            out.append(b)
                else:
                    if b == 0x0A:
                        if show_ends:
                            out.append(0x24)
                        out.append(0x0A)
                        at_start = True
                        last_blank = False
                    elif b == 0x09 and show_tabs:
                        out.extend(b'^I')
                    elif b == 0x0D and show_ends:
                        pending_cr = True
                    else:
                        out.append(b)

    for name in operands:
        kind = kind_of(name, world)
        if kind == 'missing':
            err.extend((b'cat: ' + os.fsencode(name) +
                        b': No such file or directory\n'))
            failed = True
            continue
        if kind == 'unreadable':
            err.extend((b'cat: ' + os.fsencode(name) + b': Permission denied\n'))
            failed = True
            continue
        if kind == 'dir':
            err.extend((b'cat: ' + os.fsencode(name) + b': Is a directory\n'))
            failed = True
            continue
        if kind == 'stdin':
            if not stdin_used:
                apply_chunk(stdin_bytes)
                stdin_used = True
        else:
            apply_chunk(world[name].bytes())
    if pending_cr:
        out.append(0x0D)
    return (1 if failed else 0), bytes(out), bytes(err)


def count_bytes(data):
    lines = data.count(b'\n')
    words = 0
    in_word = False
    for b in data:
        if b in (0x20, 0x09, 0x0A, 0x0B, 0x0C, 0x0D):
            in_word = False
        elif 0x21 <= b <= 0x7E:
            if not in_word:
                words += 1
                in_word = True
    return lines, words, len(data)


def model_wc(world, argv, stdin_bytes):
    opts, operands, _ = parse_argv('wc', argv)
    sel = [ch for ch in 'lwc' if ch in opts]
    if not sel:
        sel = ['l', 'w', 'c']
    stats = []          # (label, lines, words, bytes, unknown, is_stdin)
    err = bytearray()
    failed = False
    explicit = bool(operands)
    if not operands:
        operands = ['']

    for name in operands:
        label = name
        is_stdin = (name == '-' or name == '')
        kind = 'stdin' if is_stdin else kind_of(name, world)
        if kind == 'missing':
            err.extend(b'wc: ' + os.fsencode(name) + b': No such file or directory\n')
            failed = True
            continue
        if kind == 'unreadable':
            err.extend(b'wc: ' + os.fsencode(name) + b': Permission denied\n')
            failed = True
            continue
        if kind == 'stdin':
            lines, words, nbytes = count_bytes(stdin_bytes)
            stats.append(('', lines, words, nbytes, True, True)
                         if name == '' else ('-', lines, words, nbytes, True, True))
            continue
        if kind == 'dir':
            err.extend(b'wc: ' + os.fsencode(name) + b': Is a directory\n')
            stats.append((label, 0, 0, 0, True, False))
            failed = True
            continue
        spec = world[name]
        if spec.zero_fill:
            lines, words, nbytes = 0, 0, spec.size
        else:
            lines, words, nbytes = count_bytes(spec.bytes())
        stats.append((label, lines, words, nbytes, False, False))

    operand_count = len(operands)
    total = (sum(s[1] for s in stats), sum(s[2] for s in stats),
             sum(s[3] for s in stats))
    if not (len(stats) == 1 and operand_count == 1 and len(sel) == 1):
        size_sum = sum(s[3] for s in stats if not s[5])
        width = len(str(size_sum))
        if any(s[4] for s in stats) and width < 7:
            width = 7
    else:
        width = 0

    out = bytearray()

    def emit(label, lines, words, nbytes):
        vals = {'l': lines, 'w': words, 'c': nbytes}
        fields = []
        for ch in sel:
            s = str(vals[ch])
            if width:
                s = s.rjust(width)
            fields.append(s)
        line = ' '.join(fields)
        if label:
            line += ' ' + label
        out.extend(line.encode() + b'\n')

    for label, lines, words, nbytes, _unknown, _stdin in stats:
        emit(label, lines, words, nbytes)
    if operand_count > 1:
        emit('total', total[0], total[1], total[2])
    return (1 if failed else 0), bytes(out), bytes(err)


NUM_RE = re.compile(rb'[ \t]*(-?)(\d*)(?:\.(\d*))?')


def num_key(line):
    m = NUM_RE.match(line)
    neg = m.group(1) == b'-'
    ip = m.group(2).lstrip(b'0')
    fp = m.group(3) or b''
    fp = fp.rstrip(b'0')
    if not ip and not fp:
        return (0, b'', b'')
    return (-1 if neg else 1, ip, fp)


def num_cmp(a, b):
    az = not a[1] and not a[2]
    bz = not b[1] and not b[2]
    if az and bz:
        return 0
    if a[0] != b[0]:
        return -1 if a[0] < b[0] else 1
    ai, bi = len(a[1]), len(b[1])
    if ai != bi:
        c = -1 if ai < bi else 1
    elif a[1] != b[1]:
        c = -1 if a[1] < b[1] else 1
    else:
        af, bf = a[2], b[2]
        n = min(len(af), len(bf))
        if af[:n] != bf[:n]:
            c = -1 if af[:n] < bf[:n] else 1
        elif len(af) != len(bf):
            c = -1 if len(af) < len(bf) else 1
        else:
            c = 0
    return -c if a[0] < 0 else c


def lines_of(data):
    if not data:
        return []
    parts = data.split(b'\n')
    if parts and parts[-1] == b'':
        parts.pop()
    return parts


def model_sort(world, argv, stdin_bytes):
    opts, operands, _ = parse_argv('sort', argv)
    numeric = 'n' in opts
    reverse = 'r' in opts
    unique = 'u' in opts
    err = bytearray()
    if not operands:
        operands = ['-']
    # preflight every named operand, in order
    for name in operands:
        if name == '-':
            continue
        kind = kind_of(name, world)
        if kind == 'missing':
            err.extend(b'sort: cannot read: ' + os.fsencode(name) +
                       b': No such file or directory\n')
            return 2, b'', bytes(err)
        if kind == 'unreadable':
            err.extend(b'sort: cannot read: ' + os.fsencode(name) +
                       b': Permission denied\n')
            return 2, b'', bytes(err)
    lines = []
    for name in operands:
        kind = kind_of(name, world)
        if kind == 'dir':
            err.extend(b'sort: read failed: ' + os.fsencode(name) +
                       b': Is a directory\n')
            return 2, b'', bytes(err)
        if kind == 'stdin':
            lines.extend(lines_of(stdin_bytes))
        else:
            lines.extend(lines_of(world[name].bytes()))

    import functools
    if numeric:
        keys = [num_key(x) for x in lines]
        cmp = functools.cmp_to_key(lambda i, j: num_cmp(keys[i], keys[j]))
        if unique:
            order = sorted(range(len(lines)), key=cmp, reverse=reverse)
        else:
            order = sorted(range(len(lines)),
                           key=functools.cmp_to_key(
                               lambda i, j: num_cmp(keys[i], keys[j]) or
                               (-1 if lines[i] < lines[j] else
                                (1 if lines[i] > lines[j] else 0))),
                           reverse=reverse)
    else:
        order = sorted(range(len(lines)), key=lambda i: lines[i], reverse=reverse)
    kept = []
    for i in order:
        if unique and kept:
            last = kept[-1]
            if numeric:
                same = num_cmp(keys[i], keys[last]) == 0
            else:
                same = lines[i] == lines[last]
            if same:
                continue
        kept.append(i)
    out = b''.join(lines[i] + b'\n' for i in kept)
    return 0, out, b''


MODELS = {'cat': model_cat, 'wc': model_wc, 'sort': model_sort}


# -------------------------------------------------------------------- reference

class RefInfo:
    def __init__(self, tool, path, kind, version):
        self.tool = tool
        self.path = path
        self.kind = kind          # gnu | gnu-old | busybox | unknown | none
        self.version = version    # first version line, or a description


def probe_env(tmp):
    home = os.path.join(tmp, 'refhome')
    tdir = os.path.join(tmp, 'reftmp')
    os.makedirs(home, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    return {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C',
            'TZ': 'UTC', 'HOME': home, 'TMPDIR': tdir}


def classify(tool):
    path = shutil.which(tool, path=REFERENCE_PATH)
    if not path:
        return RefInfo(tool, None, 'none', 'not found on ' + REFERENCE_PATH)
    path = os.path.abspath(path)
    tmp = tempfile.mkdtemp(prefix='tool-ref-')
    try:
        env = probe_env(tmp)
        lines = []
        for flag in ('--version', '--help'):
            try:
                p = subprocess.run([tool, flag], executable=path, env=env,
                                   capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired) as e:
                lines.append(str(e))
                continue
            lines.extend(x.strip() for x in (p.stdout + p.stderr).decode(
                'utf-8', 'replace').splitlines() if x.strip())
        text = '\n'.join(lines)
        first = lines[0] if lines else ''
        if 'BusyBox' in text or os.path.basename(os.path.realpath(path)) == 'busybox':
            return RefInfo(tool, path, 'busybox', first or 'BusyBox')
        m = re.search(r'\(GNU coreutils\)\s+(\d+)\.(\d+)', text)
        if m:
            major = int(m.group(1))
            return RefInfo(tool, path, 'gnu' if major >= 9 else 'gnu-old',
                           first or f'GNU coreutils {m.group(1)}.{m.group(2)}')
        return RefInfo(tool, path, 'unknown', first or 'unrecognized implementation')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def normalize_paths(data, casedir):
    prefix = casedir.encode()
    if prefix and prefix in data:
        return data.replace(prefix, b'<case>')
    return data


def normalize_ref_stderr(stderr, ref, tool):
    """Replace a leading '<ref_path>: ' on every line with '<tool>: ' (the
    defensive safety net for the bare-argv[0] invocation rule)."""
    prefix = (ref.path or '').encode() + b': '
    bare = tool.encode() + b': '
    changed = False
    out = []
    for line in stderr.split(b'\n'):
        if line.startswith(prefix):
            out.append(bare + line[len(prefix):])
            changed = True
        else:
            out.append(line)
    return b'\n'.join(out), changed


# ----------------------------------------------------------------------- golden

def golden_path(root, tool, name, ext):
    return os.path.join(GOLDEN, root, tool, name + '.' + ext)


def load_golden(root, tool, name):
    paths = [golden_path(root, tool, name, e) for e in ('stdout', 'stderr', 'exit')]
    if not any(os.path.exists(p) for p in paths):
        return None
    if not all(os.path.exists(p) for p in paths):
        raise SetupError(f'incomplete golden for {tool}/{name} under {root}')
    with open(paths[0], 'rb') as f:
        stdout = f.read()
    with open(paths[1], 'rb') as f:
        stderr = f.read()
    with open(paths[2], 'rb') as f:
        exit_text = f.read().strip()
    for i, line in enumerate(stderr.split(b'\n')):
        if line.startswith(b'/'):
            raise SetupError(
                f'malformed golden {tool}/{name}: stderr line {i+1} starts with /')
    try:
        code = int(exit_text)
    except ValueError:
        raise SetupError(f'malformed golden exit for {tool}/{name}: {exit_text!r}')
    return stdout, stderr, code


def write_golden(root, tool, name, stdout, stderr, code):
    d = os.path.join(GOLDEN, root, tool)
    os.makedirs(d, exist_ok=True)
    with open(golden_path(root, tool, name, 'stdout'), 'wb') as f:
        f.write(stdout)
    with open(golden_path(root, tool, name, 'stderr'), 'wb') as f:
        f.write(stderr)
    with open(golden_path(root, tool, name, 'exit'), 'wb') as f:
        f.write(str(code).encode() + b'\n')


# ------------------------------------------------------------------------ runner

class RunResult:
    def __init__(self, rc, stdout, stderr, timed_out=False, overrun=False):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.overrun = overrun


def _limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE, FSIZE))


def run_proc(argv, cwd, env, stdin_data=None, executable=None, sink=False,
             timeout=10.0, tag='out'):
    out_path = os.path.join(cwd, '.tools-' + tag + '.stdout')
    err_path = os.path.join(cwd, '.tools-' + tag + '.stderr')
    stdin = subprocess.DEVNULL if stdin_data is None else subprocess.PIPE
    if sink:
        fout = open('/dev/full', 'wb')
    else:
        fout = open(out_path, 'wb')
    ferr = open(err_path, 'wb')
    timed_out = False
    try:
        p = subprocess.Popen(argv, executable=executable, cwd=cwd, env=env,
                             stdin=stdin, stdout=fout, stderr=ferr,
                             start_new_session=True, preexec_fn=_limit)
        try:
            p.communicate(input=stdin_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
    finally:
        fout.close()
        ferr.close()
    with open(err_path, 'rb') as f:
        stderr = f.read()
    if sink:
        stdout = b''
    else:
        with open(out_path, 'rb') as f:
            stdout = f.read()
    overrun = timed_out is False and (
        len(stdout) >= FSIZE or len(stderr) >= FSIZE or p.returncode == -signal.SIGXFSZ)
    if timed_out:
        overrun = False
    return RunResult(p.returncode, stdout, stderr, timed_out, overrun)


DEFECT_TEXT = (b'index out of range', b'out of memory', b'negative array length',
               b'objects leaked', b'file descriptors leaked', b'double free',
               b'use of a closed file descriptor', b'double close of file descriptor',
               b'has been freed', b'internal error')


def runtime_defect(result):
    for line in result.stderr.split(b'\n'):
        if line.startswith(b'obo: ') or line.startswith(b'obx: '):
            return line.decode('utf-8', 'replace')
        for t in DEFECT_TEXT:
            if t in line:
                return line.decode('utf-8', 'replace')
    if result.rc in (3, 4, 5, 6, 7, 8, 9):
        return f'exit {result.rc}'
    return None


def describe(a, b, what):
    """A compact byte mismatch report."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    def ctx(x):
        s = x[max(0, i - 20):i + 40]
        return repr(s)
    return (f'{what}: ours {len(a)} bytes {ctx(a)}; '
            f'expected {len(b)} bytes {ctx(b)}; first difference at {i}')


def stderr_shape(stderr, tool, operands):
    """Shape rule: every non-empty line starts '<tool>: ', and an operand name
    appears. Returns None when the shape is acceptable."""
    lines = [x for x in stderr.split(b'\n') if x]
    if not lines:
        return f'empty {tool} stderr (expected at least one `<tool>: ` line)'
    want = tool.encode() + b': '
    for line in lines:
        line = re.sub(rb'^sort: (cannot read|read failed|open failed):',
                      b'sort: <file-error>:', line)
        if not line.startswith(want):
            return f'stderr line does not start with {want!r}: {line!r}'
    names = [os.fsencode(x) for x in operands if x not in ('-', '')]
    if names and not any(any(n and n in line for n in names) for line in lines):
        return f'no operand name in stderr: {stderr!r}'
    return None


def writeerror_shape(stderr, tool):
    lines = [x for x in stderr.split(b'\n') if x]
    want = tool.encode() + b': '
    if not lines:
        return f'empty {tool} stderr (a write error must print a line)'
    for line in lines:
        if not line.startswith(want):
            return f'stderr line does not start with {want!r}: {line!r}'
    return None


class ToolCtx:
    def __init__(self, tool, exe, ref, mode):
        self.tool = tool
        self.exe = exe
        self.ref = ref
        self.mode = mode          # the --reference value
        self.diff = None          # None | 'exact' | 'shape' | 'portable' | 'any'
        kind = ref.kind
        if mode == 'gnu':
            if kind not in ('gnu', 'gnu-old'):
                raise SetupError(
                    f'--reference=gnu: {tool}: found {kind} ({ref.version})')
            self.diff = 'exact' if kind == 'gnu' else 'shape'
        elif mode == 'busybox':
            if kind != 'busybox':
                raise SetupError(
                    f'--reference=busybox: {tool}: found {kind} ({ref.version})')
            self.diff = 'portable'
        elif mode == 'any':
            if kind == 'none':
                raise SetupError(f'--reference=any: {tool}: not found')
            self.diff = 'any'
        elif mode == 'none':
            self.diff = None
        else:                      # auto
            if kind == 'gnu':
                self.diff = 'exact'
            elif kind == 'gnu-old':
                self.diff = 'shape'
            else:
                self.diff = None
        self.gnu_goldens = mode not in ('any', 'busybox')

    def differential(self, case):
        if self.diff == 'portable':
            return case.portable
        return self.diff in ('exact', 'shape', 'any') and case.tag == 'modeled'

    def oracle_is_ref(self, case):
        if not self.differential(case):
            return False
        if self.diff in ('exact', 'shape', 'any'):
            return True
        return False


class Harness:
    def __init__(self, args, tmp):
        self.args = args
        self.tmp = tmp
        self.failures = []        # test failures
        self.model_errors = []    # exit-2 harness/model errors
        self.skips = []
        self.notes = []
        self.passed = 0
        self.not_run = 0
        if args.reference == 'none':
            self.refs = {t: RefInfo(t, None, 'none', 'disabled') for t in TOOLS}
        else:
            self.refs = {t: classify(t) for t in TOOLS}
        self.ctx = {t: ToolCtx(t, None, self.refs[t], args.reference) for t in TOOLS}
        self.golden_usage = {'gnu': 0, 'own': 0, 'model': 0}
        self._goldens = {}

    # -- setup
    def check_require_reference(self):
        if self.args.require_reference:
            if self.args.reference == 'none':
                raise SetupError('--require-reference is incompatible with --reference=none')
            for t in TOOLS:
                r = self.refs[t]
                if r.kind != 'gnu':
                    raise SetupError(
                        f'--require-reference: {t}: need GNU coreutils >= 9, '
                        f'found {r.kind} ({r.version})')

    def build(self):
        obo = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
        out = os.path.join(self.tmp, 'build')
        try:
            exes = build_tools.build(list(TOOLS), out, obo, FORK)
        except build_tools.BuildError as e:
            raise SetupError(str(e))
        for t, exe in zip(TOOLS, exes):
            self.ctx[t].exe = exe

    def golden(self, root, tool, name):
        key = (root, tool, name)
        if key not in self._goldens:
            self._goldens[key] = load_golden(root, tool, name)
        return self._goldens[key]

    def validate_goldens(self):
        """Every golden-marked modeled case needs a complete gnu triple, and
        every usage/writeerror case a complete own triple."""
        for c in CASES:
            if c.golden and self.golden('gnu', c.tool, c.name) is None:
                raise SetupError(
                    f'gnu golden missing for golden-marked {c.tool}/{c.name}')
            if c.tag in ('usage', 'writeerror'):
                if self.golden('own', c.tool, c.name) is None:
                    raise SetupError(f'own golden missing for {c.tool}/{c.name}')

    def check_version_note(self):
        path = os.path.join(GOLDEN, 'gnu', 'VERSION')
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                recorded = [x.strip() for x in f if x.strip()]
        except OSError:
            return
        current = [self.refs[t].version for t in TOOLS if self.refs[t].kind == 'gnu']
        if len(recorded) == len(TOOLS) and current and recorded != current:
            self.notes.append(
                'gnu/VERSION differs from the current reference lines: recorded '
                + ' | '.join(recorded) + '; now ' + ' | '.join(current))

    # -- one case
    def run_case(self, case):
        casedir = os.path.join(self.tmp, 'cases', f'{case.tool}-{case.name}')
        os.makedirs(casedir, exist_ok=True)
        home = os.path.join(casedir, 'home')
        tdir = os.path.join(casedir, 'tmp')
        os.makedirs(home, exist_ok=True)
        os.makedirs(tdir, exist_ok=True)
        for fx in case.fixtures:
            materialize(fx, casedir)
        world = {}
        for name in set(case.args) | set(case.fixtures):
            if name == '-':
                continue
            world[name] = SPECS.get(name, Spec('missing'))

        if (case.sink or case.tag == 'writeerror') and not os.path.exists('/dev/full'):
            return ('skip', '/dev/full is absent')
        # unreadable cases are skipped where the fixture is actually readable
        if 'unreadable' in case.fixtures:
            up = SPECS['unreadable']
            if up.size != 1:
                raise SetupError('the unreadable fixture must be one byte')
            p = os.path.join(casedir, 'unreadable')
            if os.path.exists(p) and os.access(p, os.R_OK):
                return ('skip', 'unreadable fixture is readable (root?)')
            # the documented width divergence must be invisible: adding the
            # unreadable file's byte must not change the digit count of the sum
            readable = 0
            for name in parse_argv(case.tool, case.args)[1]:
                if name in ('unreadable', '-', ''):
                    continue
                spec = world.get(name, Spec('missing'))
                if spec.kind != 'file' or name == '/dev/null':
                    continue
                readable += spec.size
            if len(str(readable)) != len(str(readable + 1)):
                raise SetupError(
                    f'{case}: unreadable digit precondition fails for sum {readable}')

        env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C',
               'LANGUAGE': 'C', 'TZ': 'UTC', 'HOME': home, 'TMPDIR': tdir}
        ours_env = dict(env, OBO_CHECK='1', OBX_GC='poison')
        stdin_data = case.stdin
        ours = run_proc([self.ctx[case.tool].exe, *case.args], casedir, ours_env,
                        stdin_data, sink=case.sink, timeout=60 if case.slow else 10,
                        tag='our')
        problems = []
        if ours.timed_out:
            problems.append('timed out')
        if ours.overrun:
            problems.append('output overrun (>16 MiB)')
        defect = runtime_defect(ours)
        if defect:
            problems.append(f'tool defect: {defect}')
        # path normalization safety net; relative operands make it unnecessary
        ours.stdout = normalize_paths(ours.stdout, casedir)
        ours.stderr = normalize_paths(ours.stderr, casedir)

        # our side compared against its own/gnu golden
        own = self.golden('own', case.tool, case.name) if case.tag in ('usage', 'writeerror') else None
        gnu = self.golden('gnu', case.tool, case.name) if case.tag == 'modeled' and \
            self.ctx[case.tool].gnu_goldens else None
        if case.tag in ('usage', 'writeerror'):
            if own is None:
                raise SetupError(f'own golden missing for {case.tool}/{case.name}')
            problems.extend(self.compare_our(ours, own, 'own golden'))
            self.golden_usage['own'] += 1
        if gnu is not None:
            problems.extend(self.compare_our(ours, gnu, 'gnu golden'))
            self.golden_usage['gnu'] += 1

        # model oracle / model-vs-GNU check
        model = None
        if case.tag == 'modeled':
            try:
                model = MODELS[case.tool](world, case.args,
                                          stdin_data if stdin_data is not None else b'')
            except ModelError as e:
                self.model_errors.append(f'{case}: model {e}')
                model = None
        if case.tag == 'modeled' and not self.ctx[case.tool].oracle_is_ref(case):
            if model is None and not self.model_errors:
                raise SetupError(f'no model for {case}')
            if model is not None:
                problems.extend(self.compare_our(
                    ours, (model[1], model[2], model[0]), 'model'))
                self.golden_usage['model'] += 1

        # the reference side
        ref_run = None
        if self.ctx[case.tool].differential(case) or (
                case.tag == 'writeerror' and self.ctx[case.tool].diff in ('exact', 'shape')):
            ref = self.refs[case.tool]
            ref_run = run_proc([case.tool, *case.args], casedir, env, stdin_data,
                               executable=ref.path, sink=case.sink,
                               timeout=60 if case.slow else 10, tag='ref')
            if ref_run.timed_out:
                problems.append('reference timed out')
            if ref_run.overrun:
                problems.append('reference output overrun (>16 MiB)')
            ref_run.stdout = normalize_paths(ref_run.stdout, casedir)
            ref_run.stderr = normalize_paths(ref_run.stderr, casedir)
            norm, changed = normalize_ref_stderr(ref_run.stderr, ref, case.tool)
            if changed:
                self.notes.append(f'{case}: reference stderr carried {ref.path}: (normalized)')
            ref_run.stderr = norm
            if ref_run.rc != ours.rc:
                problems.append(f'exit {ours.rc}, reference {ref_run.rc}')
            if ours.stdout != ref_run.stdout:
                problems.append(describe(ours.stdout, ref_run.stdout, 'stdout'))
            if case.tag == 'writeerror':
                shape = writeerror_shape(ref_run.stderr, case.tool)
                if shape:
                    problems.append('reference writeerror shape: ' + shape)
            elif case.tag == 'modeled':
                if case.sink:
                    if ours.stderr:
                        problems.append(f'our sink stderr: {ours.stderr!r}')
                    if ref_run.stderr:
                        problems.append(f'reference sink stderr: {ref_run.stderr!r}')
                elif self.ctx[case.tool].diff == 'portable':
                    if ours.stderr or ref_run.stderr:
                        problems.append(
                            f'portable case stderr not empty: ours {ours.stderr!r}, '
                            f'reference {ref_run.stderr!r}')
                elif ours.stderr or ref_run.stderr:
                    if self.ctx[case.tool].diff == 'exact':
                        if ours.stderr != ref_run.stderr:
                            problems.append(describe(ours.stderr, ref_run.stderr,
                                                     'file-error stderr'))
                    else:
                        operands = parse_argv(case.tool, case.args)[1]
                        shape = stderr_shape(ref_run.stderr, case.tool, operands)
                        if shape:
                            problems.append('reference stderr shape: ' + shape)
                        shape = stderr_shape(ours.stderr, case.tool, operands)
                        if shape:
                            problems.append('our stderr shape: ' + shape)

        # model vs GNU (hard harness failure on disagreement)
        if (model is not None and ref_run is not None and
                self.refs[case.tool].kind == 'gnu' and
                self.args.check_models and case.tag == 'modeled'):
            if model[0] != ref_run.rc:
                self.model_errors.append(
                    f'{case}: model exit {model[0]}, GNU {ref_run.rc}')
            if model[1] != ref_run.stdout:
                self.model_errors.append(
                    f'{case}: model stdout differs from GNU: ' +
                    describe(model[1], ref_run.stdout, 'stdout'))
            if (self.ctx[case.tool].diff == 'exact' and not case.sink and
                    (model[2] or ref_run.stderr)):
                if model[2] != ref_run.stderr:
                    self.model_errors.append(
                        f'{case}: model stderr differs from GNU: ' +
                        describe(model[2], ref_run.stderr, 'stderr'))

        # the cat write-error cases pin the stop-before-later-operand behavior
        if case.tool == 'cat' and case.tag == 'writeerror':
            writes = ours.stderr.count(b'write error')
            if writes != 1:
                problems.append(f'{writes} write-error lines (expected exactly 1)')
            if case.name == 'devfull_oneline_missing_multi_a' and b'missing' in ours.stderr:
                problems.append('opened/mentioned `missing` after the fatal write')
            if case.name == 'devfull_missing_oneline_multi_a':
                misses = ours.stderr.count(b'No such file or directory')
                if misses != 1:
                    problems.append(f'{misses} missing-operand errors (expected 1)')

        return ('fail', problems) if problems else ('pass', '')

    def compare_our(self, ours, expected, what):
        exp_stdout, exp_stderr, exp_rc = expected
        problems = []
        if ours.rc != exp_rc:
            problems.append(f'exit {ours.rc}, {what} {exp_rc}')
        if ours.stdout != exp_stdout:
            problems.append(describe(ours.stdout, exp_stdout, f'stdout vs {what}'))
        if ours.stderr != exp_stderr:
            problems.append(describe(ours.stderr, exp_stderr, f'stderr vs {what}'))
        return problems

    # -- modes
    SERIAL_NAMES = ('stream', 'pipeline', 'stdin_unread')

    def run_matrix(self):
        cases = CASES
        if self.args.fast:
            skipped = [c for c in cases if c.slow]
            self.skips.extend(f'{c}: slow (--fast)' for c in skipped)
            cases = [c for c in cases if not c.slow]
        if self.args.k:
            cases = [c for c in cases if self.args.k in c.name or
                     self.args.k in f'{c.tool}/{c.name}']
        serial_only = (bool(self.args.k) and
                       any(self.args.k in n for n in self.SERIAL_NAMES))
        if not cases and not serial_only:
            raise SetupError('no cases selected')
        results = {}
        if cases and self.args.jobs > 1 and not self.args.fail_fast:
            # -j runs the plain cases in the pool; write-error/sink cases run
            # serially as the design requires
            parallel = [c for c in cases if not (c.sink or c.tag == 'writeerror')]
            serial = [c for c in cases if c.sink or c.tag == 'writeerror']
            with concurrent.futures.ThreadPoolExecutor(self.args.jobs) as pool:
                for c, res in zip(parallel, pool.map(self.run_case, parallel)):
                    results[c.key] = res
            for c in serial:
                results[c.key] = self.run_case(c)
        else:
            # serial run (and every -x run): stop scheduling at the first failure
            for i, c in enumerate(cases):
                results[c.key] = self.run_case(c)
                if self.args.fail_fast and results[c.key][0] == 'fail':
                    self.not_run = len(cases) - i - 1
                    break
        for c in cases:
            if c.key not in results:
                continue
            status, detail = results[c.key]
            if status == 'pass':
                self.passed += 1
                if self.args.verbose:
                    print(f'ok   {c}')
            elif status == 'skip':
                self.skips.append(f'{c}: {detail}')
            else:
                self.failures.append((c, detail))
        if self.args.fail_fast and self.failures:
            return
        self.run_serial_cases()

    def run_serial_cases(self):
        k = self.args.k
        if k and not any(k in n for n in ('stream', 'pipeline', 'stdin_unread')):
            return
        self.stream_case(k)
        self.stdin_unread_case(k)
        self.pipeline_case(k)

    def _special_dir(self):
        d = os.path.join(self.tmp, 'special')
        os.makedirs(d, exist_ok=True)
        for fx in ('ab', 'multi_a'):
            materialize(fx, d)
        return d

    def stream_case(self, k):
        if k and 'stream' not in k:
            return
        exe = self.ctx['cat'].exe
        env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C',
               'TZ': 'UTC', 'HOME': self.tmp, 'TMPDIR': self.tmp,
               'OBO_CHECK': '1', 'OBX_GC': 'poison'}
        for label, argv, want in (
                ('cat streams before EOF', [exe], b'abc\n'),
                ('cat -n streams before EOF', [exe, '-n'], b'     1\tabc\n')):
            p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env,
                                 start_new_session=True)
            try:
                p.stdin.write(b'abc\n')
                p.stdin.flush()
                got = b''
                deadline = time.time() + 10.0
                while len(got) < len(want):
                    left = deadline - time.time()
                    if left <= 0:
                        break
                    ready, _, _ = select.select([p.stdout], [], [], left)
                    if not ready:
                        break
                    chunk = os.read(p.stdout.fileno(), len(want) - len(got))
                    if not chunk:
                        break
                    got += chunk
                if got != want:
                    self.failures.append((label, f'got {got!r}, expected {want!r}'))
                else:
                    self.passed += 1
                    if self.args.verbose:
                        print(f'ok   {label}')
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                if p.returncode != 0:
                    self.failures.append((label, f'exit {p.returncode}'))

    def stdin_unread_case(self, k):
        if k and 'stdin_unread' not in k:
            return
        d = self._special_dir()
        env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C',
               'TZ': 'UTC', 'HOME': d, 'TMPDIR': d,
               'OBO_CHECK': '1', 'OBX_GC': 'poison'}
        for tool, args in (('cat', ['ab']), ('wc', ['ab']), ('sort', ['ab'])):
            label = f'{tool} does not read stdin when it is not an operand'
            p = subprocess.Popen([self.ctx[tool].exe, *args], cwd=d, env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, start_new_session=True)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                self.failures.append((label, 'blocked reading an unused stdin'))
                continue
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
            if p.returncode != 0:
                self.failures.append((label, f'exit {p.returncode}'))
            else:
                self.passed += 1
                if self.args.verbose:
                    print(f'ok   {label}')

    def pipeline_case(self, k):
        if k and 'pipeline' not in k:
            return
        d = self._special_dir()
        env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C',
               'TZ': 'UTC', 'HOME': d, 'TMPDIR': d,
               'OBO_CHECK': '1', 'OBX_GC': 'poison'}
        head = shutil.which('head', path=REFERENCE_PATH)
        pipelines = [
            ('cat ab | wc -l', [self.ctx['cat'].exe, 'ab'],
             [self.ctx['wc'].exe, '-l'], b'1\n'),
            ('cat -n ab | sort -n', [self.ctx['cat'].exe, '-n', 'ab'],
             [self.ctx['sort'].exe, '-n'], b'     1\ta\n'),
        ]
        if head:
            pipelines.append(('sort multi_a | head -n 1',
                              [self.ctx['sort'].exe, 'multi_a'], [head, '-n', '1'],
                              b'a\n'))
        for label, first, second, want in pipelines:
            # both sides are process-group leaders and p1.stderr is drained to
            # DEVNULL, so a hung pipeline cannot evade the timeout or fill a pipe
            p1 = subprocess.Popen(first, cwd=d, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL,
                                  start_new_session=True)
            p2 = subprocess.Popen(second, cwd=d, env=env, stdin=p1.stdout,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True)
            p1.stdout.close()
            try:
                out, err2 = p2.communicate(timeout=20)
                p1.wait(timeout=20)
            except subprocess.TimeoutExpired:
                for p in (p1, p2):
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        pass
                for p in (p1, p2):
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                self.failures.append((label, 'pipeline timed out'))
                continue
            if out != want or p2.returncode != 0:
                self.failures.append((label, f'stdout {out!r} want {want!r}, '
                                             f'exit {p2.returncode}'))
            else:
                self.passed += 1
                if self.args.verbose:
                    print(f'ok   {label}')

    # -- memory mode
    def run_memory(self):
        d = os.path.join(self.tmp, 'memory')
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, 'sparse16m')
        with open(p, 'wb') as f:
            f.truncate(16 << 20)
        env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C',
               'TZ': 'UTC', 'HOME': d, 'TMPDIR': d,
               'OBO_CHECK': '1', 'OBX_GC': 'poison', 'OBO_STATS': '1'}
        cases = [
            ('cat sparse16m', [self.ctx['cat'].exe, 'sparse16m'], True),
            ('wc -lwc sparse16m', [self.ctx['wc'].exe, '-lwc', 'sparse16m'], False),
        ]
        numbers = []
        for label, argv, devnull in cases:
            stdout = subprocess.DEVNULL if devnull else subprocess.PIPE
            p2 = subprocess.run(argv, cwd=d, env=env, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=subprocess.PIPE, timeout=120)
            err = p2.stderr
            stats = [x for x in err.split(b'\n')
                     if re.match(rb'^obo: \d+ peak bytes, \d+ allocated$', x)]
            other = [x for x in err.split(b'\n')
                     if x.startswith(b'obo: ') or x.startswith(b'obx: ')]
            if p2.returncode != 0:
                self.failures.append((label, f'exit {p2.returncode}'))
                continue
            if len(stats) != 1 or len(other) != 1:
                self.failures.append((label, f'stats line missing or duplicated: {err!r}'))
                continue
            m = re.match(rb'^obo: (\d+) peak bytes, (\d+) allocated$', stats[0])
            peak, alloc = int(m.group(1)), int(m.group(2))
            if peak > MEMORY_PEAK_MAX:
                self.failures.append(
                    (label, f'peak {peak} > {MEMORY_PEAK_MAX} live heap bytes'))
            else:
                numbers.append((label, peak, alloc))
                self.passed += 1
                print(f'ok   {label}: peak {peak} bytes, {alloc} allocated')
        return numbers

    # -- record
    def record(self):
        if self.args.reference == 'none':
            raise SetupError('--record needs a GNU reference')
        for t in TOOLS:
            r = self.refs[t]
            if r.kind != 'gnu':
                raise SetupError(f'--record needs GNU coreutils >= 9 for {t}, '
                                 f'found {r.kind} ({r.version})')
        if self.args.k:
            cases = [c for c in CASES if self.args.k in c.name or
                     self.args.k in f'{c.tool}/{c.name}']
        else:
            cases = list(CASES)
        selected = [c for c in cases if c.tag == 'modeled' and c.golden]
        if not selected:
            raise SetupError('no golden-marked modeled cases selected')
        for case in selected:
            casedir = os.path.join(self.tmp, 'record', f'{case.tool}-{case.name}')
            os.makedirs(casedir, exist_ok=True)
            home = os.path.join(casedir, 'home')
            tdir = os.path.join(casedir, 'tmp')
            os.makedirs(home, exist_ok=True)
            os.makedirs(tdir, exist_ok=True)
            for fx in case.fixtures:
                materialize(fx, casedir)
            if (case.sink or case.tag == 'writeerror') and not os.path.exists('/dev/full'):
                print(f'skip   {case}: /dev/full is absent')
                continue
            if 'unreadable' in case.fixtures:
                up = os.path.join(casedir, 'unreadable')
                if os.path.exists(up) and os.access(up, os.R_OK):
                    print(f'skip   {case}: unreadable fixture is readable (root?)')
                    continue
            env = {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C',
                   'LANGUAGE': 'C', 'TZ': 'UTC', 'HOME': home, 'TMPDIR': tdir}
            ref = self.refs[case.tool]
            stdin_data = case.stdin
            res = run_proc([case.tool, *case.args], casedir, env, stdin_data,
                           executable=ref.path, sink=case.sink,
                           timeout=60 if case.slow else 10, tag='ref')
            norm, changed = normalize_ref_stderr(res.stderr, ref, case.tool)
            if changed:
                self.notes.append(f'{case}: normalized a reference path prefix')
            for line in norm.split(b'\n'):
                if line.startswith(b'/'):
                    raise SetupError(
                        f'{case}: refusing to record a stderr line starting with /: '
                        f'{line!r}')
            old = None
            try:
                old = load_golden('gnu', case.tool, case.name)
            except SetupError:
                old = None
            if old is not None and old != (res.stdout, norm, res.rc) and not self.args.force:
                raise SetupError(
                    f'{case}: recorded reference differs from the existing golden '
                    '(use --force to overwrite)')
            write_golden('gnu', case.tool, case.name, res.stdout, norm, res.rc)
            print(f'recorded gnu/{case.tool}/{case.name}')
        lines = []
        for t in TOOLS:
            lines.append(self.refs[t].version)
        d = os.path.join(GOLDEN, 'gnu')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'VERSION'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(f'recorded {len(selected)} goldens and gnu/VERSION')


# --------------------------------------------------------------------------- CLI

def list_cases():
    for c in CASES:
        marks = [c.tag]
        if c.portable:
            marks.append('portable')
        if c.golden:
            marks.append('golden')
        if c.slow:
            marks.append('slow')
        if c.sink:
            marks.append('sink')
        print(f'{c.tool:4s} {c.name:38s} {" ".join(marks)}')
    print(f'{len(CASES)} cases')


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Differential gate for the obo command-line tools')
    ap.add_argument('-k', metavar='PATTERN', help='only cases whose name contains PATTERN')
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('-x', '--fail-fast', action='store_true')
    ap.add_argument('-j', '--jobs', type=int, default=1)
    ap.add_argument('--reference', choices=('auto', 'gnu', 'busybox', 'any', 'none'),
                    default='auto')
    ap.add_argument('--require-reference', action='store_true')
    ap.add_argument('--check-models', dest='check_models', action='store_true',
                    default=True)
    ap.add_argument('--no-check-models', dest='check_models', action='store_false')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--memory', action='store_true')
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args(argv)

    try:
        validate_cases()
    except SetupError as e:
        print(f'tools.py: {e}', file=sys.stderr)
        return 2
    if a.list:
        list_cases()
        return 0
    if a.jobs < 1:
        print('tools.py: -j needs a positive integer', file=sys.stderr)
        return 2
    if a.record and a.memory:
        print('tools.py: --record and --memory are separate modes', file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix='obo-tools-')
    if not a.keep:
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    else:
        print(f'temporary tree: {tmp}')
    try:
        h = Harness(a, tmp)
        h.check_require_reference()
        if a.record:
            h.record()
            return 0
        h.build()
        for t in TOOLS:
            r = h.refs[t]
            where = f' ({r.path})' if r.path else ''
            print(f'reference {t}: {r.kind}: {r.version}{where}')
            if h.ctx[t].diff is None and r.kind in ('busybox', 'unknown'):
                print(f'  {t}: differential skipped: {r.kind} reference; '
                      'models and goldens are the oracle')
            if h.ctx[t].diff == 'portable' and not any(
                    c.portable for c in CASES if c.tool == t):
                print(f'  {t}: no portable cases in v1; no differential cases run')
        h.check_version_note()
        if a.memory:
            h.run_memory()
        else:
            h.validate_goldens()
            h.run_matrix()
    except SetupError as e:
        print(f'tools.py: {e}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('tools.py: interrupted', file=sys.stderr)
        return 2

    for c, detail in h.failures:
        print(f'FAIL {c}')
        details = detail if isinstance(detail, list) else [detail]
        for d in details:
            print(f'     {d}')
    for err in h.model_errors:
        print(f'MODEL {err}')
    for n in h.notes:
        print(f'note {n}')
    for s in h.skips:
        print(f'skip {s}')
    if a.record:
        return 0

    if h.model_errors:
        print(f'\n{len(h.model_errors)} model/harness errors (exit 2)')
        return 2
    kinds = {h.refs[t].kind for t in TOOLS}
    version = None
    if kinds == {'gnu'}:
        m = re.search(r'\(GNU coreutils\)\s+([0-9][0-9.]*)',
                      h.refs[TOOLS[0]].version)
        version = m.group(1) if m else '9.x'
    if a.memory:
        print(f'\nmemory: {h.passed} passed, {len(h.failures)} failed')
        if h.failures:
            return 1
        return 0
    if a.reference == 'any' and kinds == {'gnu'}:
        ref_line = (f'reference: any (GNU coreutils {version}): stdout/exit exact, '
                    'stderr shape')
    elif kinds == {'gnu'}:
        ref_line = f'reference: GNU coreutils {version} (cat, wc, sort)'
    elif kinds == {'none'}:
        ref_line = (f'reference: none; {h.golden_usage["model"]} modeled cases via '
                    f'models, {h.golden_usage["gnu"]} gnu goldens, '
                    f'{h.golden_usage["own"]} own goldens, {len(h.skips)} skipped')
    else:
        ref_line = 'reference: ' + ', '.join(
            f'{t}: {h.refs[t].kind}' for t in TOOLS)
    print(f'\n{ref_line}')
    print(f'{h.passed} passed, {len(h.failures)} failed'
          + (f', {h.not_run} not run' if h.not_run else '')
          + (f', {len(h.skips)} skipped' if h.skips else ''))
    return 1 if h.failures else 0


if __name__ == '__main__':
    sys.exit(main())
