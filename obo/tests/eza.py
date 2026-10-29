#!/usr/bin/env python3
"""The differential gate for `eza` (P0 skeleton; own build arrives in P4).

    python3 tests/eza.py [-k PATTERN] [-v] [-x] [-j N] [--fast]
        [--reference=auto|eza|none] [--require-reference]
        [--record [--force]] [--memory] [--list] [--keep]

This is a separate gate from the compiler suites.  It is modelled on
`tests/tools.py` but has no Python model of eza: the pinned reference and the
recorded reference goldens are the oracle.  Cases run in fresh deterministic
fixture trees from `tests/eza/fixtures.py` with the exact section 9.5
environment.

Reference and VERSION
---------------------
`--reference=auto` uses `eza` if it resolves on the host PATH; `eza` requires
it; `none` disables it.  The binary is executed as `['eza', *argv]` with
`executable=<absolute path>`, so argv[0] is the bare name.  `VERSION` holds
the exact `eza --version` output followed by metadata lines:

    <version line 1>
    <version line 2>
    <version line 3>
    binary-sha256: <64 hex digits>
    tarball-sha256: <64 hex digits>
    url: <https url>

`--require-reference` requires `VERSION` and requires the live reference's
`--version` output and binary sha256 to match it exactly; anything missing or
different is a setup error (exit 2), never a skip.  Without it, a live
reference that does not match `VERSION` is not used as the oracle and cases
fall back to committed goldens.

The P0 reference step writes `VERSION` and runs `probe.py`; the harness never
creates or edits `VERSION`.  `--record` verifies the pin and writes
reference/own goldens plus a manifest, and refuses path-leaking output.

Own eza and P4 gating
---------------------
The own source `cmd/eza.obx` arrives in P4; `own_available()` tests for it.
While it is absent, `--record` records the selected reference goldens only and
skips own-tagged cases, a normal run checks the committed reference goldens
against the pinned reference and skips own cases, and `--memory` exits 2 with
the P4 message.  Nothing about the reference pin or the reference golden
checks is relaxed: `--require-reference` still requires the exact version and
binary hash, and a missing or malformed reference golden is still a setup
error.  Once `cmd/eza.obx` exists, the designed behaviour returns: build eza
and compare/record own cases as well.

Case tags
---------
  ref     live differential required (or a committed golden if an error is
          host-dependent); skipped when no usable reference exists and no
          golden is available
  golden  a recorded reference triple exists under golden/ref/
  own     our own usage/unsupported error, pinned under golden/own/
  skip    conditionally skipped by platform/root/capability (the runner also
          computes the actual reason)

A `golden` case is also a `ref` case when a pinned reference is present, and
both are compared.  `--record` selects `golden` and `own` cases, runs the same
fixtures and environments, refuses any stdout/stderr line that starts with `/`
or contains the case directory, and refuses to overwrite a differing golden
without `--force`.

Exit status: 0 pass, 1 test failure, 2 harness/setup failure.
"""

import argparse
import atexit
import concurrent.futures
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))        # .../obo/tests
FORK = os.path.dirname(HERE)                             # .../obo
ROOT = os.path.dirname(FORK)                             # repository root
EZA = 'eza'
REFERENCE_PATH = '/usr/bin:/bin'
FSIZE = 16 * 1024 * 1024                                 # per-process cap
VERSION = os.path.join(HERE, 'eza', 'VERSION')
GOLDEN = os.path.join(HERE, 'eza', 'golden')
OWN_SOURCE = os.path.join(FORK, 'cmd', 'eza.obx')       # P4 own source

sys.path.insert(0, os.path.join(HERE, 'eza'))
import fixtures  # noqa: E402


def own_available():
    """True once the P4 own eza source (`obo/cmd/eza.obx`) exists."""
    return os.path.isfile(OWN_SOURCE)


LONG = ('-l', '--no-user', '--bytes', '--time-style=long-iso')
FAMILIES = ('default', 'names', 'sortkeys', 'long', 'quoting', 'classify',
            'dirs', 'operands', 'errors', 'memory')
TAGS = ('ref', 'golden', 'own', 'skip')
REF, GOLDEN_TAG, OWN, SKIP = TAGS


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------- cases

class Case:
    def __init__(self, family, name, argv=(), cwd='', env=None, tags=(),
                 mode='normal', note='', requires=(), slow=False,
                 live_only=False, memory_entries=0):
        if family not in FAMILIES:
            raise SetupError('unknown case family %r for %s' % (family, name))
        tags = set(tags)
        unknown = tags - set(TAGS)
        if unknown:
            raise SetupError('%s/%s: unknown tags %r' % (family, name, unknown))
        if not tags:
            raise SetupError('%s/%s: no tags' % (family, name))
        if GOLDEN_TAG in tags and REF not in tags:
            tags.add(REF)
        if GOLDEN_TAG in tags and live_only:
            raise SetupError('%s/%s: a live-only case cannot be golden'
                             % (family, name))
        if GOLDEN_TAG in tags and OWN in tags:
            raise SetupError('%s/%s: golden and own are separate goldens'
                             % (family, name))
        if live_only and REF not in tags:
            raise SetupError('%s/%s: live-only needs the ref tag' % (family, name))
        if isinstance(requires, str):
            requires = (requires,)
        self.family = family
        self.name = name
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env or {})
        self.tags = tags
        self.mode = mode
        self.note = note
        self.requires = tuple(requires)
        self.slow = slow
        self.live_only = live_only
        self.memory_entries = memory_entries
        self.key = family + '/' + name

    def __str__(self):
        return self.key


CASES = []


def _add(family, name, argv=(), cwd='', env=None, tags=(), mode='normal',
         note='', requires=(), slow=False, live_only=False,
         memory_entries=0):
    CASES.append(Case(family, name, argv, cwd, env, tags, mode, note,
                      requires, slow, live_only, memory_entries))


def _g(family, name, *argv, **kw):
    _add(family, name, argv, tags=(REF, GOLDEN_TAG), **kw)


def _r(family, name, *argv, **kw):
    kw.setdefault('live_only', True)
    _add(family, name, argv, tags=(REF,), **kw)


def _sk(family, name, *argv, **kw):
    _add(family, name, argv, tags=(REF, SKIP), live_only=True, **kw)


def _o(family, name, *argv, **kw):
    _add(family, name, argv, tags=(OWN,), **kw)


def _default_cases():
    _g('default', 'empty', 'empty')
    _g('default', 'one', 'one')
    _g('default', 'onedir', 'onedir')
    _g('default', 'mixed', 'mixed',
       note='non-TTY lines; symlinks render name -> target')
    _g('default', 'lines_one', '-1', 'one')
    _g('default', 'lines_mixed', '-1', 'mixed')
    _g('default', 'lines_links', '-1', 'links')
    _r('default', 'cwd_mixed', cwd='mixed',
       note='no operand: stdin file-name mode (empty stdin), not a cwd listing; '
            'design section 7.2 contradiction, see REFERENCE.md; live-only')
    _g('default', 'cwd_dot', '.', cwd='mixed')
    _g('default', 'all_empty', '-a', 'empty')
    _g('default', 'all_one', '-a', 'one')
    _g('default', 'all_mixed', '-a', 'mixed')
    _g('default', 'aa_empty', '-aa', 'empty')
    _g('default', 'aa_one', '-aa', 'one')
    _g('default', 'aa_mixed', '-aa', 'mixed')
    _g('default', 'almost_all_mixed', '-A', 'mixed')
    _g('default', 'A_a_mixed', '-A', '-a', 'mixed')
    _g('default', 'A_aa_mixed', '-A', '-aa', 'mixed')
    _g('default', 'all_all_mixed', '--all', '--all', 'mixed')
    _g('default', 'lines_empty', '-1', 'empty')
    _g('default', 'lines_onedir', '-1', 'onedir')
    _g('default', 'lines_all_mixed', '-1', '-a', 'mixed')
    _g('default', 'lines_aa_mixed', '-1', '-aa', 'mixed')
    _g('default', 'lines_A_mixed', '-1', '-A', 'mixed')
    _g('default', 'all_links', '-a', 'links')
    _g('default', 'aa_links', '-aa', 'links')
    _g('default', 'all_onedir', '-a', 'onedir')
    _r('default', 'absolute_tree', 'absolute',
       note='live-only absolute targets')


def _name_cases():
    _g('names', 'ordered_default', 'names/ordered')
    _g('names', 'ordered_Name', '--sort=Name', 'names/ordered')
    _g('names', 'ordered_lower', '--sort=name', 'names/ordered')
    _g('names', 'ordered_filename', '--sort=filename', 'names/ordered')
    _g('names', 'ordered_reverse', '-r', 'names/ordered')
    _g('names', 'ordered_dot_name', '--sort=.name', 'names/ordered')
    _r('names', 'tie_fixture_default', 'names',
       note='readdir-order tie probe, live only')
    _r('names', 'tie_Aa', '.', cwd='names/tie_Aa',
       note='natcmpFold tie A/a; readdir-order sensitive')
    _r('names', 'tie_readme', '.', cwd='names/tie_readme',
       note='natcmpFold tie README/readme')
    _r('names', 'tie_pic', '.', cwd='names/tie_pic',
       note='pic4/pic 4 tie under both comparators')
    _r('names', 'tie_space', '.', cwd='names/tie_space',
       note='a/ a tie under both comparators')
    _r('names', 'tie_dot', '-aa', '.', cwd='names/tie_dot',
       note='real ` .` beside synthetic .')
    _g('names', 'group_dirs_first', '--group-directories-first', 'mixed')
    _g('names', 'group_dirs_first_reverse', '--group-directories-first',
       '-r', 'mixed')
    _g('names', 'D_mixed', '-D', 'mixed')
    _g('names', 'f_mixed', '-f', 'mixed')
    _g('names', 'D_f_mixed', '-D', '-f', 'mixed')
    _g('names', 'D_reverse_mixed', '-D', '-r', 'mixed')
    _g('names', 'ordered_Name_reverse', '--sort=Name', '-r', 'names/ordered')
    _g('names', 'ordered_dot_Name', '--sort=.Name', 'names/ordered')
    _g('names', 'ordered_dot_Name_reverse', '--sort=.Name', '-r',
       'names/ordered')
    _g('names', 'ordered_filename_reverse', '--sort=filename', '-r',
       'names/ordered')
    _g('names', 'ordered_aa', '-aa', 'names/ordered')
    _g('names', 'ordered_aa_Name', '-aa', '--sort=Name', 'names/ordered')
    _g('names', 'group_dirs_first_tree', '--group-directories-first', 'tree')
    _g('names', 'D_links', '-D', 'links')


def _sortkey_cases():
    _g('sortkeys', 'size', '--sort=size', 'sizes')
    _g('sortkeys', 'size_reverse', '--sort=size', '-r', 'sizes')
    _g('sortkeys', 'modified', '--sort=modified', 'times')
    _g('sortkeys', 'time', '--sort=time', 'times')
    _g('sortkeys', 'modified_reverse', '--sort=modified', '-r', 'times')
    _g('sortkeys', 'ext_lower', '--sort=ext', 'ext')
    _g('sortkeys', 'extension_lower', '--sort=extension', 'ext')
    _g('sortkeys', 'Ext', '--sort=Ext', 'ext')
    _g('sortkeys', 'Extension', '--sort=Extension', 'ext')
    _g('sortkeys', 'extension_reverse', '--sort=extension', '-r', 'ext')
    _g('sortkeys', 'type', '--sort=type', 'mixed')
    _g('sortkeys', 'type_reverse', '--sort=type', '-r', 'mixed')
    _g('sortkeys', 'date_alias', '--sort=date', 'times')
    _g('sortkeys', 'newest_alias', '--sort=newest', 'times')
    _r('sortkeys', 'inode', '--sort=inode', 'mixed',
       note='host inode order')
    _r('sortkeys', 'none', '--sort=none', 'mixed',
       note='readdir order; out of E0 scope')
    _g('sortkeys', 'ext_key_d', '-aa', '--sort=extension', 'd', cwd='ext',
       note='dot key File::ext("d") = None')
    _g('sortkeys', 'ext_key_dx_x', '-aa', '--sort=extension', 'dx.x',
       cwd='ext', note='dot key File::ext("dx.x") = Some("x")')
    _g('sortkeys', 'ext_key_dx_dot', '-aa', '--sort=extension', 'dx.',
       cwd='ext', note='dot key File::ext("dx.") = Some("")')
    _g('sortkeys', 'ext_key_dot', '-aa', '--sort=extension', '.', cwd='ext',
       note='dot keys File::ext(".") and File::ext("..") = None')
    _r('sortkeys', 'ext_key_dotdot', '-aa', '--sort=extension', '..',
       cwd='ext', note='parent operand is live/shape-only')
    _r('sortkeys', 'ext_key_root', '-aa', '--sort=extension', '/', cwd='empty',
       note='dot key File::ext("/") = None; host root data')
    _r('sortkeys', 'size_aa', '-aa', '--sort=size', 'sizes',
       note='dot keys are host metadata')
    _r('sortkeys', 'modified_aa', '-aa', '--sort=modified', 'times',
       note='dot keys are host metadata')
    _g('sortkeys', 'size_sizes_1', '-1', '--sort=size', 'sizes')
    _g('sortkeys', 'ext_ordered', '--sort=extension', 'names/ordered',
       note='extension keys tie on None; distinct names break the tie')
    _g('sortkeys', 'Ext_ordered', '--sort=Ext', 'names/ordered')
    _g('sortkeys', 'ext_ordered_aa', '-aa', '--sort=extension',
       'names/ordered')
    _g('sortkeys', 'type_tree', '--sort=type', 'tree')
    _g('sortkeys', 'type_tree_reverse', '--sort=type', '-r', 'tree')
    _g('sortkeys', 'type_links', '--sort=type', 'links')
    _g('sortkeys', 'ext_ext_aa', '-aa', '--sort=extension', 'ext')
    _g('sortkeys', 'modified_times_1', '-1', '--sort=modified', 'times')


def _long_cases():
    for tree in ('empty', 'one', 'onedir', 'mixed', 'modes', 'sizes',
                 'times', 'long', 'links', 'tree', 'quoting'):
        _g('long', 'long_' + tree, *LONG, tree)
    _g('long', 'big_7digit', *LONG, 'long/big',
       note='seven-digit --bytes size under LC_ALL=C')
    _g('long', 'pre_epoch', *LONG, 'times/tpre',
       note='pre-1970 mtime renders a hyphen')
    _g('long', 't2038', *LONG, 'times/t2038')
    _g('long', 'setuid', *LONG, 'modes/m4755')
    _g('long', 'setgid', *LONG, 'modes/m2755')
    _g('long', 'sticky', *LONG, 'modes/m1777')
    _g('long', 'mode000', *LONG, 'modes/m000')
    _g('long', 'dir_mode000', *LONG, 'modes/d000', requires='nonroot',
       note='mode-000 directory: the permission-denied warning only exists '
            'for a non-root euid (design 9.4 root rule)')
    _g('long', 'd_file', *LONG, '-d', 'operands/f')
    _g('long', 'd_dir', *LONG, '-d', 'operands/dir')
    _g('long', 'd_symlink', *LONG, '-d', 'operands/l')
    _g('long', 'd_spaced_link', *LONG, '-d', 'operands/x y/sub/link')
    _g('long', 'dangling', *LONG, 'links/dangling')
    _g('long', 'dir_link', *LONG, 'links/realdirlink')
    _g('long', 'hard', *LONG, 'hard')
    _r('long', 'xattr', *LONG, 'xattr',
       note='xattr @ marker is host-dependent; excluded from goldens '
            '(design 9.2/9.4); live differential only')
    _g('long', 'd_pipe', *LONG, '-d', 'modes/pipe')
    _g('long', 'd_socket', *LONG, '-d', 'modes/sock')
    _g('long', 'd_sub_file', *LONG, '-d', 'operands/sub/f')
    _g('long', 'd_sub_exec', *LONG, '-d', 'operands/sub/target')
    _g('long', 'd_sub_link', *LONG, '-d', 'operands/sub/link')
    _g('long', 'd_sub_dirlink', *LONG, '-d', 'operands/sub/dirlink')
    _r('long', 'aa_mixed', '-aa', '-l', 'mixed',
       note='dot rows use host parent metadata')
    _r('long', 'numeric_user', '-l', '-n', 'mixed',
       note='numeric uid width varies by host')
    _r('long', 'default_user', '-l', 'mixed',
       note='user names are host data')
    _r('long', 'no_user', '-l', '--no-user', 'mixed',
       note='non-bytes sizes and default time style')
    _r('long', 'dot_d', '-l', '-d', '.', cwd='mixed',
       note='-d . is live/shape-only')
    _r('long', 'dotdot_d', '-l', '-d', '..', cwd='one',
       note='-d .. is live/shape-only')
    _r('long', 'root_d', '-l', '-d', '/',
       note='-d / is live/shape-only')


def _quoting_cases():
    _g('quoting', 'default', 'quoting')
    _g('quoting', 'lines', '-1', 'quoting')
    _g('quoting', 'long', *LONG, 'quoting')
    _g('quoting', 'no_quotes', '--no-quotes', 'quoting')
    _g('quoting', 'classify', '-F=always', 'quoting')
    _g('quoting', 'spaced_operand', 'quoting/with space')
    _g('quoting', 'spaced_symlink_operand', 'quoting/valid spaced')
    _g('quoting', 'ctl_target', '-1', 'quoting')
    _g('quoting', 'broken_relative', 'quoting',
       note='brokenrel -> ../tar get; no root sibling; no marker')
    _g('quoting', 'broken_relative_F', '-F=always', 'quoting',
       note='still no marker on the broken relative target')
    _g('quoting', 'broken_relative_long', *LONG, 'quoting/brokenrel')
    _g('quoting', 'links_spaced_target', '-1', 'links')
    _g('quoting', 'lines_no_quotes', '-1', '--no-quotes', 'quoting')
    _g('quoting', 'F_no_quotes', '-F=always', '--no-quotes', 'quoting')
    _g('quoting', 'spaced_operand_lines', '-1', 'quoting/with space')
    _g('quoting', 'valid_spaced_operand_lines', '-1', 'quoting/valid spaced')
    _g('quoting', 'broken_relative_lines', '-1', 'quoting/brokenrel')
    _g('quoting', 'broken_relative_d_F', '-d', '-F=always',
       'quoting/brokenrel', note='no marker on a broken relative target')
    _r('quoting', 'root_parent_tmp', '/tmp',
       note='valid root-parent operand; live-only')
    _r('quoting', 'root_parent_tmp_F', '-F=always', '/tmp',
       note='valid root-parent operand with markers; live-only')
    _r('quoting', 'root_parent_root', '/',
       note='valid root-parent operand; live-only')
    _r('quoting', 'root_parent_root_F', '-F=always', '/',
       note='valid root-parent operand with markers; live-only')
    _r('quoting', 'absolute_broken_F', '-F=always', 'absolute',
       note='broken absolute /tar get target; no marker; live-only')


def _classify_cases():
    _g('classify', 'F_mixed', '-F', 'mixed')
    _g('classify', 'F_always', '-F=always', 'mixed')
    _g('classify', 'Fattached', '-Falways', 'mixed')
    _g('classify', 'F_separate', '-F', 'always', 'mixed')
    _g('classify', 'classify_always', '--classify=always', 'mixed')
    _g('classify', 'classify_automatic', '--classify=automatic', 'mixed')
    _g('classify', 'classify_separate_automatic', '--classify', 'automatic',
       'mixed', note='`automatic` is not in WHEN and stays an operand')
    _g('classify', 'F_separate_automatic', '-F', 'automatic', 'mixed')
    _g('classify', 'F_lines', '-1', '-F=always', 'mixed')
    _g('classify', 'F_links', '-F=always', 'links')
    _g('classify', 'F_long_links', *LONG, '-F=always', 'links')
    _g('classify', 'F_d_file', '-d', '-F=always', 'f', cwd='operands')
    _g('classify', 'F_d_dir', '-d', '-F=always', 'dir', cwd='operands')
    _g('classify', 'F_d_symlink', '-d', '-F=always', 'l', cwd='operands')
    _g('classify', 'F_d_dirlink', '-d', '-F=always', 'dl', cwd='operands')
    _g('classify', 'F_modes', '-F=always', 'modes')
    _g('classify', 'F_tree', '-F=always', 'tree')
    _g('classify', 'F_ext', '-F=always', 'ext')
    _g('classify', 'F_hard', '-F=always', 'hard')
    _g('classify', 'F_ordered', '-F=always', 'names/ordered')
    _g('classify', 'F_d_broken', '-d', '-F=always', 'broken', cwd='operands')
    _g('classify', 'classify_never', '--classify=never', 'mixed')
    _r('classify', 'F_absolute', '-F=always', 'absolute',
       note='live-only root-parent target markers')


def _dir_cases():
    _g('dirs', 'd_file', '-d', 'f', cwd='operands')
    _g('dirs', 'd_dir', '-d', 'dir', cwd='operands')
    _g('dirs', 'd_symlink', '-d', 'l', cwd='operands')
    _g('dirs', 'd_dangling', '-d', 'broken', cwd='operands')
    _g('dirs', 'treat_file', '--treat-dirs-as-files', 'f', cwd='operands')
    _g('dirs', 'list_dirs', '--list-dirs', 'dir', cwd='operands')
    _g('dirs', 'list_dirs_symlink', '--list-dirs', 'dl', cwd='operands')
    _g('dirs', 'd_long_file', *LONG, '-d', 'f', cwd='operands')
    _g('dirs', 'd_long_dir', *LONG, '-d', 'dir', cwd='operands')
    _g('dirs', 'd_long_symlink', *LONG, '-d', 'l', cwd='operands')
    _g('dirs', 'operand_dir_slash', 'dir/', cwd='operands')
    _g('dirs', 'operand_symlink_slash', 'dl/', cwd='operands')
    _g('dirs', 'operand_double_slash', 'sub//f', cwd='operands')
    _g('dirs', 'operand_dot_slash', 'sub/./f', cwd='operands')
    _g('dirs', 'operand_dot_file', './f', cwd='operands')
    _g('dirs', 'operand_dot', '.', cwd='operands')
    _g('dirs', 'spaced_parent_file', 'x y/sub/f', cwd='operands')
    _g('dirs', 'spaced_parent_dir', 'x y/sub/', cwd='operands')
    _g('dirs', 'spaced_parent_link_d', '-d', 'x y/sub/link', cwd='operands')
    _g('dirs', 'd_sub_link', '-d', 'sub/link', cwd='operands',
       note='target is an executable in sub/')
    _g('dirs', 'd_sub_dirlink', '-d', 'sub/dirlink', cwd='operands',
       note='target is a directory in sub/')
    _r('dirs', 'operand_dotdot', '..', cwd='operands',
       note='parent listing is live/shape-only')
    _r('dirs', 'd_root', '-d', '/', note='-d / is live/shape-only')
    _r('dirs', 'root_listing', '/', note='host root listing')


def _operand_cases():
    _g('operands', 'file', 'one/f')
    _g('operands', 'multiple_files', 'operands/f', 'operands/g')
    _g('operands', 'dash_operand', '--', '-', cwd='mixed')
    _g('operands', 'dashdash_name', '--', '--x', cwd='mixed')
    _g('operands', 'hidden_explicit', 'mixed/.hidden')
    _g('operands', 'directory', 'mixed/dir')
    _g('operands', 'two_directories', 'mixed/dir', 'mixed/sub.dir')
    _g('operands', 'dot_file_normalized', 'operands/./f')
    _r('operands', 'dotdot', '..', cwd='operands',
       note='parent listing is live/shape-only')
    _r('operands', 'absolute', '<cwd>/one',
       note='absolute operand is live/shape-only')


def _error_cases():
    _g('errors', 'missing_operand', 'missing')
    _g('errors', 'file_as_directory', 'one/f/')
    _g('errors', 'unknown_short', '-Q', 'mixed')
    _g('errors', 'unknown_short_attached', '-Qfoo', 'mixed')
    _g('errors', 'unknown_short_equals', '-Q=foo', 'mixed')
    _g('errors', 'unknown_long', '--foo', 'mixed')
    _g('errors', 'unknown_long_eq', '--foo=bar', 'mixed')
    _g('errors', 'unknown_long_double_eq', '--foo==', 'mixed')
    _g('errors', 'unknown_long_empty', '--foo=', 'mixed')
    _g('errors', 'unknown_long_weird', '--=x', 'mixed')
    _g('errors', 'missing_value_sort', '--sort')
    _g('errors', 'missing_value_width', '--width')
    _g('errors', 'missing_value_level', '-L')
    _g('errors', 'bad_sort', '--sort=bogus', 'mixed')
    _g('errors', 'bad_classify', '--classify=bogus', 'mixed')
    _g('errors', 'bad_color', '--color=bogus', 'mixed')
    _g('errors', 'bad_icons', '--icons=bogus', 'mixed')
    _g('errors', 'bad_time_style', '--time-style=bogus', 'mixed')
    _g('errors', 'bad_level', '-T', '-L', 'x', 'tree')
    _sk('errors', 'permission_denied', 'locked/denied', requires='nonroot',
        note='skipped when euid is 0')
    _sk('errors', 'permission_denied_entry', 'locked/denied/f',
        requires='nonroot', note='metadata lookup fails under a 000 directory')
    _g('errors', 'dev_full', 'mixed', mode='sink', requires='dev_full',
       note='stdout is /dev/full')
    _r('errors', 'broken_pipe', 'many', mode='pipe',
       note='stdout read end closed before the first write')
    _r('errors', 'path_over_limit', 'a/' * 2100 + 'f',
       note='expected-error/limitation case, not a reference golden')
    _o('errors', 'own_long_restricted', '-l', 'mixed',
       note='our restricted long mode needs --no-user --bytes '
            '--time-style=long-iso')
    _o('errors', 'own_sort_none', '--sort=none', 'mixed',
       note='E0 does not implement --sort=none')
    _o('errors', 'own_unsupported_flag', '--git', 'mixed',
       note='E0 rejects out-of-scope flags')


def _memory_cases():
    _o('memory', 'long_10000', '-l', '--no-user', '--bytes',
       '--time-style=long-iso', 'many10000', slow=True, memory_entries=10000,
       note='TODO(P4): parse the single OBO_STATS line and EZA_PEAK_MAX')
    _o('memory', 'names_10000', 'many10000', slow=True,
       memory_entries=10000, note='name-only listing under OBO_STATS')
    _o('memory', 'tree', '-T', 'tree', slow=True,
       note='tree case covers iterator buffers')


for _builder in (_default_cases, _name_cases, _sortkey_cases, _long_cases,
                 _quoting_cases, _classify_cases, _dir_cases,
                 _operand_cases, _error_cases, _memory_cases):
    _builder()


# Conditional fixture entries: a case that names one of these trees cannot be
# compared against a golden recorded with the entry present (or absent) on a
# host where the capability differs.
TREE_CAPS = {
    'mixed': ('fifo', 'socket'),
    'modes': ('fifo', 'socket'),
    'long': ('fifo', 'socket', 'deep_path'),
    'times': ('pre_epoch',),
    'hard': ('hardlink',),
}


def implied_requires(case):
    needed = set(case.requires)
    texts = [case.cwd] + [a for a in case.argv if isinstance(a, str)]
    for tree, keys in TREE_CAPS.items():
        prefix = tree + '/'
        if any(text == tree or text.startswith(prefix) for text in texts):
            needed.update(keys)
    return needed


def validate_cases():
    keys = set()
    families = set()
    for case in CASES:
        families.add(case.family)
        if case.key in keys:
            raise SetupError('duplicate case ' + case.key)
        keys.add(case.key)
        if GOLDEN_TAG in case.tags:
            if case.cwd.startswith('/') or case.cwd.startswith('..'):
                raise SetupError('%s: golden case cwd must be fixture-relative'
                                 % case.key)
            for arg in case.argv:
                text = arg.decode('utf-8', 'replace') if isinstance(arg, bytes) else arg
                if text.startswith('/') or text == '..' or text.startswith('../') \
                        or text == '<cwd>':
                    raise SetupError(
                        '%s: golden case has an absolute/live operand %r'
                        % (case.key, arg))
        if case.family == 'memory' and not case.slow:
            raise SetupError('%s: memory case must be slow' % case.key)
    missing = set(FAMILIES) - families
    if missing:
        raise SetupError('case families missing: %s' % ', '.join(sorted(missing)))


# ---------------------------------------------------------------- golden files

def golden_path(kind, case, ext):
    return os.path.join(GOLDEN, kind, case.family, case.name + '.' + ext)


def load_golden(kind, case):
    paths = [golden_path(kind, case, ext)
             for ext in ('stdout', 'stderr', 'exit')]
    if not any(os.path.exists(p) for p in paths):
        return None
    if not all(os.path.exists(p) for p in paths):
        raise SetupError('incomplete golden %s/%s under %s'
                         % (kind, case.key, GOLDEN))
    with open(paths[0], 'rb') as f:
        stdout = f.read()
    with open(paths[1], 'rb') as f:
        stderr = f.read()
    with open(paths[2], 'rb') as f:
        exit_text = f.read().strip()
    for stream, data in (('stdout', stdout), ('stderr', stderr)):
        for i, line in enumerate(data.split(b'\n')):
            if line.startswith(b'/'):
                raise SetupError('malformed golden %s/%s: %s line %d starts '
                                 'with /' % (kind, case.key, stream, i + 1))
    try:
        code = int(exit_text)
    except ValueError:
        raise SetupError('malformed golden exit for %s/%s: %r'
                         % (kind, case.key, exit_text))
    return stdout, stderr, code


def write_golden(kind, case, stdout, stderr, code):
    directory = os.path.dirname(golden_path(kind, case, 'stdout'))
    os.makedirs(directory, exist_ok=True)
    for ext, data in (('stdout', stdout), ('stderr', stderr),
                      ('exit', str(code).encode() + b'\n')):
        with open(golden_path(kind, case, ext), 'wb') as f:
            f.write(data)


# ------------------------------------------------------------------- reference

class RefInfo:
    def __init__(self, path=None, kind='none', version=b'', sha256=None,
                 reason=''):
        self.path = path
        self.kind = kind          # pinned | unverified | mismatch | none
        self.version = version
        self.sha256 = sha256
        self.reason = reason


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def read_umask():
    try:
        with open('/proc/self/status', encoding='ascii') as f:
            for line in f:
                if line.startswith('Umask:'):
                    return int(line.split()[1], 8)
    except OSError:
        pass
    old = os.umask(0)
    os.umask(old)
    return old


def _unescape_mount(text):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), text)


def fs_type(path):
    real = os.path.realpath(path)
    best_mount, best_type = '', 'unknown'
    try:
        with open('/proc/self/mountinfo', encoding='utf-8',
                  errors='replace') as f:
            for line in f:
                left, sep, right = line.partition(' - ')
                if not sep:
                    continue
                fields = left.split()
                if len(fields) < 5:
                    continue
                mount = _unescape_mount(fields[4])
                if real == mount or real.startswith(mount.rstrip('/') + '/'):
                    if len(mount) > len(best_mount):
                        rest = right.split()
                        best_mount = mount
                        best_type = rest[0] if rest else 'unknown'
    except OSError:
        pass
    return best_type


def probe_env(base):
    """The exact section 9.5 pin; HOME/TMPDIR live under the case root."""
    home = os.path.join(base, 'home')
    tdir = os.path.join(base, 'tmp')
    os.makedirs(home, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    return {'PATH': REFERENCE_PATH, 'LC_ALL': 'C', 'LANG': 'C',
            'LANGUAGE': 'C', 'TZ': 'UTC', 'HOME': home, 'TMPDIR': tdir}


def load_version(path=VERSION):
    """Return {'version': bytes, '<key>': str} or None when VERSION is absent.

    The version block is every line before the first metadata key; it is
    compared byte-for-byte (including the trailing newline) with the live
    reference's --version stdout.
    """
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        raw = f.read()
    lines = raw.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    version = []
    meta = {}
    key_re = re.compile(rb'^(binary-sha256|tarball-sha256|url): (.*)$')
    for i, line in enumerate(lines, 1):
        m = key_re.match(line)
        if m:
            if not version:
                raise SetupError('VERSION line %d: metadata before the version '
                                 'output' % i)
            meta[m.group(1).decode()] = m.group(2).decode('utf-8', 'replace')
            continue
        if meta:
            raise SetupError('VERSION line %d: unexpected line after metadata: '
                             '%r' % (i, line))
        version.append(line)
    if not version:
        raise SetupError('VERSION has no version output')
    meta['version'] = b'\n'.join(version) + b'\n'
    return meta


def reference_version(path, tmp):
    base = tempfile.mkdtemp(prefix='version-', dir=tmp)
    try:
        env = probe_env(base)
        p = subprocess.run([EZA, '--version'], executable=path, env=env,
                           cwd=base, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SetupError('cannot run the reference --version: %s' % e)
    finally:
        fixtures.rmtree(base)
    if p.returncode != 0:
        raise SetupError('reference --version exited %d: %r'
                         % (p.returncode, p.stderr))
    return p.stdout


def resolve_reference(mode, tmp):
    if mode == 'none':
        return RefInfo(kind='none', reason='reference disabled by '
                                          '--reference=none')
    path = shutil.which(EZA)
    if not path:
        if mode == 'eza':
            raise SetupError('--reference=eza: `eza` is not on the host PATH')
        return RefInfo(kind='none',
                       reason='`eza` is not on the host PATH; goldens only')
    path = os.path.abspath(path)
    try:
        digest = sha256_file(path)
        version = reference_version(path, tmp)
    except OSError as e:
        raise SetupError('cannot read the reference %s: %s' % (path, e))
    pin = load_version()
    if pin is None:
        reason = ('%s is missing; the pinned reference is recorded by the '
                  'authorized reference step' % VERSION)
        if mode == 'eza':
            raise SetupError('--reference=eza: ' + reason)
        return RefInfo(path, 'unverified', version, digest, reason)
    problems = []
    if version != pin['version']:
        problems.append('--version output %r differs from VERSION %r'
                        % (version, pin['version']))
    pinned_hash = pin.get('binary-sha256')
    if not pinned_hash:
        problems.append('VERSION has no binary-sha256 line')
    elif digest.lower() != pinned_hash.lower():
        problems.append('binary sha256 %s differs from VERSION %s'
                        % (digest, pinned_hash))
    if problems:
        reason = '; '.join(problems)
        if mode == 'eza':
            raise SetupError('--reference=eza: ' + reason)
        return RefInfo(path, 'mismatch', version, digest, reason)
    return RefInfo(path, 'pinned', version, digest, 'matches VERSION')


# ---------------------------------------------------------------------- runner

class RunResult:
    def __init__(self, rc, stdout, stderr, timed_out=False, overrun=False):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.overrun = overrun


def _limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE, FSIZE))


def run_proc(argv, cwd, io_dir, env, executable=None, sink=False, pipe=False,
             timeout=10.0, tag='out'):
    out_path = os.path.join(io_dir, '.eza-' + tag + '.stdout')
    err_path = os.path.join(io_dir, '.eza-' + tag + '.stderr')
    stdin = subprocess.DEVNULL
    pipe_w = None
    if pipe:
        if sink:
            raise SetupError('sink and pipe modes are exclusive')
        read_fd, pipe_w = os.pipe()
        os.close(read_fd)          # the first write gets EPIPE
        fout = pipe_w
    elif sink:
        fout = open('/dev/full', 'wb')
    else:
        fout = open(out_path, 'wb')
    ferr = open(err_path, 'wb')
    timed_out = False
    p = None
    try:
        p = subprocess.Popen(argv, executable=executable, cwd=cwd, env=env,
                             stdin=stdin, stdout=fout, stderr=ferr,
                             start_new_session=True, preexec_fn=_limit)
        if pipe_w is not None:
            os.close(pipe_w)
            pipe_w = None
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
    finally:
        if pipe:
            if pipe_w is not None:
                os.close(pipe_w)
                pipe_w = None
        else:
            fout.close()
        ferr.close()
    with open(err_path, 'rb') as f:
        stderr = f.read()
    stdout = b''
    if not sink and not pipe:
        with open(out_path, 'rb') as f:
            stdout = f.read()
    rc = p.returncode if p is not None else None
    overrun = timed_out is False and (
        len(stdout) >= FSIZE or len(stderr) >= FSIZE
        or rc == -signal.SIGXFSZ)
    if timed_out:
        overrun = False
    return RunResult(rc, stdout, stderr, timed_out, overrun)


def normalize_paths(data, root, base):
    data = data.replace(os.fsencode(root), b'<root>')
    data = data.replace(os.fsencode(base), b'<root>')
    return data


def describe(a, b, what):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1

    def ctx(x):
        return repr(x[max(0, i - 20):i + 40])
    return ('%s: ours %d bytes %s; expected %d bytes %s; first difference at %d'
            % (what, len(a), ctx(a), len(b), ctx(b), i))


def runtime_defect(result):
    for line in result.stderr.split(b'\n'):
        if line.startswith(b'obo: ') or line.startswith(b'obx: '):
            return line.decode('utf-8', 'replace')
    if result.rc in (8, 9):
        return 'exit %d (leak/double close)' % result.rc
    return None


class Harness:
    def __init__(self, args, tmp):
        self.args = args
        self.tmp = tmp
        self.ref = None
        self.exe = None
        self.failures = []
        self.skips = []
        self.notes = []
        self.passed = 0
        self.not_run = 0
        self._goldens = {}

    # -- setup
    def resolve_reference(self):
        self.ref = resolve_reference(self.args.reference, self.tmp)
        return self.ref

    def check_require_reference(self):
        if not self.args.require_reference:
            return
        if self.args.reference == 'none':
            raise SetupError('--require-reference is incompatible with '
                             '--reference=none')
        if self.ref.kind != 'pinned':
            raise SetupError('--require-reference: ' + self.ref.reason)

    def build(self):
        """P4 builds `eza` through build_tools.build(['eza'], ...)."""
        raise SetupError('eza build not implemented (P4)')

    def golden(self, kind, case):
        key = (kind, case.key)
        if key not in self._goldens:
            self._goldens[key] = load_golden(kind, case)
        return self._goldens[key]

    def validate_goldens(self):
        own = own_available()
        for case in self.selection():
            if GOLDEN_TAG in case.tags and self.golden('ref', case) is None:
                raise SetupError('reference golden missing for ' + case.key)
            if OWN in case.tags and own and self.golden('own', case) is None:
                raise SetupError('own golden missing for ' + case.key)

    # -- case plumbing
    def selection(self, memory=False):
        cases = CASES
        if self.args.fast:
            cases = [c for c in cases if not c.slow]
        if self.args.k:
            pattern = self.args.k
            cases = [c for c in cases
                     if pattern in c.name or pattern in c.key
                     or pattern in c.family + '_' + c.name]
        if memory:
            return [c for c in cases if c.family == 'memory']
        return [c for c in cases if c.family != 'memory']

    def prepare_case(self, case):
        base = os.path.join(self.tmp, 'cases', case.family + '-' + case.name)
        fixtures.rmtree(base)
        os.makedirs(base)
        root = os.path.join(base, 'fixtures')
        caps = fixtures.build(root)
        if case.memory_entries:
            fixtures.build_many(root, case.memory_entries)
        return base, root, caps

    def run_cwd(self, root, case):
        return os.path.join(root, case.cwd) if case.cwd else root

    def expand_argv(self, case, root):
        return [arg.replace('<cwd>', root) if isinstance(arg, str) else arg
                for arg in case.argv]

    def case_env(self, base, case):
        env = probe_env(base)
        for key, value in case.env.items():
            env[key] = value.replace('<case>', base)
        return env

    def skip_reason(self, case, caps):
        for key in implied_requires(case):
            if key == 'nonroot' and not caps.get('nonroot'):
                return 'requires a non-root euid'
            if key == 'dev_full' and not caps.get('dev_full'):
                return '/dev/full is absent'
            if key not in ('nonroot', 'dev_full') and not caps.get(key, False):
                return 'fixture capability unavailable: ' + key
        return None

    # -- one case
    def compare(self, result, expected, what):
        exp_stdout, exp_stderr, exp_rc = expected
        problems = []
        if result.rc != exp_rc:
            problems.append('exit %s, %s %s' % (result.rc, what, exp_rc))
        if result.stdout != exp_stdout:
            problems.append(describe(result.stdout, exp_stdout,
                                     'stdout vs ' + what))
        if result.stderr != exp_stderr:
            problems.append(describe(result.stderr, exp_stderr,
                                     'stderr vs ' + what))
        return problems

    def run_reference(self, case, base, root, tag='ref'):
        env = self.case_env(base, case)
        argv = [EZA] + self.expand_argv(case, root)
        res = run_proc(argv, self.run_cwd(root, case), base, env,
                       executable=self.ref.path,
                       sink=case.mode == 'sink',
                       pipe=case.mode == 'pipe',
                       timeout=60 if case.slow else 10, tag=tag)
        res.stdout = normalize_paths(res.stdout, root, base)
        res.stderr = normalize_paths(res.stderr, root, base)
        return res

    def run_reference_only(self, case, base, root, gold):
        """P0: `cmd/eza.obx` does not exist yet, so nothing of ours can run.

        Own-tagged cases are skipped.  A `golden` case still runs the pinned
        reference over the same fixtures and compares it with the committed
        reference golden, so the recorded matrix is checked against the live
        reference on this host; live-only cases have no committed bytes and
        are skipped.
        """
        if OWN in case.tags or gold is None:
            return ('skip', 'own eza not implemented (P4)')
        if self.ref.kind != 'pinned':
            return ('skip', 'own eza not implemented (P4); no pinned reference '
                            'to check the reference golden')
        ref_run = self.run_reference(case, base, root)
        problems = []
        if ref_run.timed_out:
            problems.append('reference timed out')
        if ref_run.overrun:
            problems.append('reference output overrun (>16 MiB)')
        problems.extend(self.compare(ref_run, gold, 'reference golden'))
        return ('fail', problems) if problems else ('pass', '')

    def run_case(self, case):
        base, root, caps = self.prepare_case(case)
        try:
            skip = self.skip_reason(case, caps)
            if skip:
                return ('skip', skip)
            own = self.golden('own', case) if OWN in case.tags else None
            gold = (self.golden('ref', case)
                    if GOLDEN_TAG in case.tags else None)
            if self.exe is None:
                return self.run_reference_only(case, base, root, gold)
            env = self.case_env(base, case)
            ours_env = dict(env, OBO_CHECK='1', OBX_GC='poison')
            argv = [EZA] + self.expand_argv(case, root)
            cwd = self.run_cwd(root, case)
            timeout = 60 if case.slow else 10
            ours = run_proc(argv, cwd, base, ours_env, executable=self.exe,
                            sink=case.mode == 'sink',
                            pipe=case.mode == 'pipe', timeout=timeout, tag='our')
            ours.stdout = normalize_paths(ours.stdout, root, base)
            ours.stderr = normalize_paths(ours.stderr, root, base)
            problems = []
            if ours.timed_out:
                problems.append('timed out')
            if ours.overrun:
                problems.append('output overrun (>16 MiB)')
            defect = runtime_defect(ours)
            if defect:
                problems.append('tool defect: ' + defect)
            if own is not None:
                problems.extend(self.compare(ours, own, 'own golden'))
            if gold is not None:
                problems.extend(self.compare(ours, gold, 'reference golden'))
            if REF in case.tags and self.ref.kind == 'pinned':
                ref_run = run_proc(argv, cwd, base, env,
                                   executable=self.ref.path,
                                   sink=case.mode == 'sink',
                                   pipe=case.mode == 'pipe',
                                   timeout=timeout, tag='ref')
                ref_run.stdout = normalize_paths(ref_run.stdout, root, base)
                ref_run.stderr = normalize_paths(ref_run.stderr, root, base)
                if ref_run.timed_out:
                    problems.append('reference timed out')
                if ref_run.overrun:
                    problems.append('reference output overrun (>16 MiB)')
                problems.extend(self.compare(ours,
                                             (ref_run.stdout, ref_run.stderr,
                                              ref_run.rc), 'live reference'))
            elif REF in case.tags and own is None and gold is None:
                return ('skip', 'live-only case; no usable reference or golden')
            return ('fail', problems) if problems else ('pass', '')
        finally:
            fixtures.rmtree(base)

    def run_matrix(self):
        cases = self.selection(memory=False)
        if not cases:
            raise SetupError('no cases selected')
        results = {}
        if self.args.jobs > 1 and not self.args.fail_fast:
            serial = [c for c in cases if c.mode != 'normal']
            parallel = [c for c in cases if c.mode == 'normal']
            with concurrent.futures.ThreadPoolExecutor(self.args.jobs) as pool:
                for case, result in zip(parallel, pool.map(self.run_case,
                                                           parallel)):
                    results[case.key] = result
            for case in serial:
                results[case.key] = self.run_case(case)
        else:
            for i, case in enumerate(cases):
                results[case.key] = self.run_case(case)
                if self.args.fail_fast and results[case.key][0] == 'fail':
                    self.not_run = len(cases) - i - 1
                    break
        for case in cases:
            if case.key not in results:
                continue
            status, detail = results[case.key]
            if status == 'pass':
                self.passed += 1
                if self.args.verbose:
                    print('ok   ' + case.key)
            elif status == 'skip':
                self.skips.append('%s: %s' % (case.key, detail))
            else:
                self.failures.append((case, detail))

    def run_memory(self):
        if not own_available():
            raise SetupError('eza build not implemented (P4)')
        self.build()
        raise SetupError('eza memory cases not implemented (P4)')

    # -- record
    def leak_refusal(self, data, base, root, case, stream):
        for i, line in enumerate(data.split(b'\n')):
            if line.startswith(b'/'):
                raise SetupError(
                    '%s: refusing to record %s line %d starting with /: %r'
                    % (case.key, stream, i + 1, line))
            for prefix, label in ((os.fsencode(base), 'case directory'),
                                  (os.fsencode(root), 'fixture root')):
                if prefix and prefix in line:
                    raise SetupError(
                        '%s: refusing to record %s line %d containing the %s: '
                        '%r' % (case.key, stream, i + 1, label, line))

    def record_case(self, kind, case, executable):
        base, root, caps = self.prepare_case(case)
        try:
            skip = self.skip_reason(case, caps)
            if skip:
                print('skip   %s: %s' % (case.key, skip))
                return False
            env = self.case_env(base, case)
            argv = [EZA] + self.expand_argv(case, root)
            res = run_proc(argv, self.run_cwd(root, case), base, env,
                           executable=executable, sink=case.mode == 'sink',
                           pipe=case.mode == 'pipe',
                           timeout=60 if case.slow else 10,
                           tag='ref' if kind == 'ref' else 'own')
            # Refuse leaks on the raw bytes: normalization would mask a fixture
            # path that does not start a line (the containment rule).
            self.leak_refusal(res.stdout, base, root, case, 'stdout')
            self.leak_refusal(res.stderr, base, root, case, 'stderr')
            res.stdout = normalize_paths(res.stdout, root, base)
            res.stderr = normalize_paths(res.stderr, root, base)
            old = load_golden(kind, case)
            if old is not None and old != (res.stdout, res.stderr, res.rc) \
                    and not self.args.force:
                raise SetupError(
                    '%s: recorded %s golden differs from the existing one '
                    '(use --force to overwrite)' % (case.key, kind))
            write_golden(kind, case, res.stdout, res.stderr, res.rc)
            print('recorded %s/%s' % (kind, case.key))
            return True
        finally:
            fixtures.rmtree(base)

    def write_manifest(self, recorded):
        path = os.path.join(GOLDEN, 'ref', 'MANIFEST')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'schema': 1,
            'reference': {
                'sha256': self.ref.sha256,
                'version': self.ref.version.decode('utf-8', 'replace'),
            },
            'host': {
                'euid': os.geteuid(),
                'egid': os.getegid(),
                'umask': '0%03o' % read_umask(),
                'filesystem': fs_type(self.tmp),
            },
            'cases': sorted(recorded),
            'note': 'P0 skeleton manifest; per-case host metadata is a P4 TODO',
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write('\n')

    def check_manifest(self):
        path = os.path.join(GOLDEN, 'ref', 'MANIFEST')
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self.notes.append('manifest unreadable: %s' % e)
            return
        recorded = data.get('reference', {})
        if self.ref.kind == 'pinned' and recorded.get('sha256') \
                and recorded['sha256'] != self.ref.sha256:
            self.notes.append('manifest reference sha256 differs from the '
                              'current reference')
        host = data.get('host', {})
        if host.get('euid') is not None and host['euid'] != os.geteuid():
            self.notes.append('manifest euid differs from the current euid')

    def record(self):
        if self.ref.kind != 'pinned':
            raise SetupError('--record requires the pinned reference: '
                             + self.ref.reason)
        cases = [c for c in self.selection(memory=False)
                 if GOLDEN_TAG in c.tags or OWN in c.tags]
        if not cases:
            raise SetupError('no recordable cases selected')
        ref_cases = [c for c in cases if GOLDEN_TAG in c.tags]
        own_cases = [c for c in cases if OWN in c.tags]
        if own_cases and not own_available():
            print('own eza not implemented (P4); skipping %d own goldens'
                  % len(own_cases))
            own_cases = []
        if own_cases:
            # TODO(P4): build eza through build_tools.build(['eza'], ...).
            self.build()
        recorded = []
        for case in ref_cases:
            if self.record_case('ref', case, self.ref.path):
                recorded.append('ref/' + case.key)
        for case in own_cases:
            if self.record_case('own', case, self.exe):
                recorded.append('own/' + case.key)
        if ref_cases:
            self.write_manifest(recorded)
        print('recorded %d goldens (%d reference, %d own)'
              % (len(recorded), len(ref_cases), len(own_cases)))


# --------------------------------------------------------------------------- CLI

def list_cases():
    for case in CASES:
        marks = [tag for tag in TAGS if tag in case.tags]
        if case.live_only:
            marks.append('live')
        if case.slow:
            marks.append('slow')
        if case.mode != 'normal':
            marks.append(case.mode)
        if case.requires:
            marks.append('requires=' + ','.join(case.requires))
        print('%-10s %-42s %s' % (case.family, case.name, ' '.join(marks)))
    print('%d cases' % len(CASES))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Differential gate for eza (P0 skeleton)')
    ap.add_argument('-k', metavar='PATTERN',
                    help='only cases whose name/key contains PATTERN')
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('-x', '--fail-fast', action='store_true')
    ap.add_argument('-j', '--jobs', type=int, default=1)
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--require-reference', action='store_true')
    ap.add_argument('--reference', choices=('auto', 'eza', 'none'),
                    default='auto')
    ap.add_argument('--memory', action='store_true')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args(argv)

    try:
        validate_cases()
    except SetupError as e:
        print('eza.py: %s' % e, file=sys.stderr)
        return 2
    if a.list:
        list_cases()
        return 0
    if a.jobs < 1:
        print('eza.py: -j needs a positive integer', file=sys.stderr)
        return 2
    if a.record and a.memory:
        print('eza.py: --record and --memory are separate modes',
              file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix='obo-eza-')
    if not a.keep:
        atexit.register(fixtures.rmtree, tmp)
    else:
        print('temporary tree: ' + tmp)
    try:
        h = Harness(a, tmp)
        h.resolve_reference()
        h.check_require_reference()
        if a.record:
            h.record()
            return 0
        if a.memory:
            h.run_memory()
            return 0
        if own_available():
            h.build()
        h.validate_goldens()
        h.check_manifest()
        h.run_matrix()
    except SetupError as e:
        print('eza.py: %s' % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('eza.py: interrupted', file=sys.stderr)
        return 2

    for case, detail in h.failures:
        print('FAIL %s' % case.key)
        for d in (detail if isinstance(detail, list) else [detail]):
            print('     ' + d)
    for note in h.notes:
        print('note ' + note)
    for skip in h.skips:
        print('skip ' + skip)
    r = h.ref
    if r.kind == 'pinned':
        ref_line = 'reference: eza sha256 %s (matches VERSION)' % r.sha256
    elif r.kind == 'none':
        ref_line = 'reference: none; goldens are the oracle'
    else:
        ref_line = 'reference: %s (%s); live differential disabled' % (
            r.kind, r.reason)
    print('\n' + ref_line)
    print('%d passed, %d failed%s%s'
          % (h.passed, len(h.failures),
             ', %d not run' % h.not_run if h.not_run else '',
             ', %d skipped' % len(h.skips) if h.skips else ''))
    return 1 if h.failures else 0


if __name__ == '__main__':
    sys.exit(main())
