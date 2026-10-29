#!/usr/bin/env python3
"""P0 probe runner for the pinned eza reference (design section 3.4).

    python3 obo/tests/eza/probe.py --reference /path/to/eza --out REFERENCE.md
        [--only GROUP[,GROUP]] [--dt-unknown DIR] [--keep]

The reference path is required.  Without it this program exits 2 and writes
nothing: it never fabricates or replays output.  The reference is executed as
``[reference, *argv]`` with ``executable=<absolute path>`` in the exact
section 9.5 environment, inside a freshly generated fixture tree per probe.
All host-dependent temporary paths (the fixture root, the per-probe
directory and the outer ``mkdtemp``) are normalized to ``<root>`` before bytes
are rendered or hashed, and a stream that still contains one after
normalization is refused.

Each probe records its group, name, cwd, argv, stdin bytes, environment
extras, exit status (including signals), exact stdout/stderr bytes as a
Python ``bytes`` literal and sha256, and a note.  The report is grouped
Markdown with a host manifest.  Probe definitions cover all sixteen section
3.4 items (version, default output, dotfiles, dirs-as-files, name order, sort
options, quoting, classify, restricted long, errors/exit, symlink operands,
colour/icons, grid/--width, tree, environment variables, DT_UNKNOWN) plus an
invalid-UTF-8 group from sections 3.3 and 8.4, a stdin group that records
the observed no-operand stdin behaviour (design section 7.2 contradiction),
the P5b E1 re-probe group (the exact colour palette, the LS/EZA subset, the
tree view-flag rules and --no-quotes) and the P6b E2 re-probe group (grid
layout and view rules, --width/COLUMNS, UTF-8 widths and folds, lossy names).

The ``absolute`` fixture tree and the ``pinned`` directory-metadata probes are
live/shape-only: they are expected host data and never become committed
goldens.  This runner does not write goldens; only the Markdown report.
"""

import argparse
import ctypes
import errno
import fcntl
import hashlib
import os
import platform
import re
import resource
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # .../obo/tests/eza
sys.path.insert(0, HERE)
import fixtures  # noqa: E402

REFERENCE_PATH = '/usr/bin:/bin'
FSIZE = 16 * 1024 * 1024
TIMEOUT = 10.0
DT_UNKNOWN = 0

GROUPS = [
    ('version', '1. Version, help and binary identity'),
    ('default', '2. Default non-TTY output'),
    ('dotfiles', '3. `-a`/`-aa`/`-A` and synthesized dots'),
    ('dirs', '4. `-d`/`--treat-dirs-as-files`/`--list-dirs` and operands'),
    ('names', '5. Default name order and comparator ties'),
    ('sort', '6. Explicit sort keys'),
    ('quoting', '7. Quoting and symlink targets'),
    ('classify', '8. `-F`/`--classify`'),
    ('long', '9. Restricted long format'),
    ('errors', '10. Errors, exit statuses and write failures'),
    ('symlinks', '11. Symlink operands and root-parent targets'),
    ('colour', '12. Colour and icons'),
    ('grid', '13. Grid, `--width`, and a pty'),
    ('tree', '14. Tree'),
    ('environment', '15. Environment variables'),
    ('dt-unknown', '16. DT_UNKNOWN filesystem'),
    ('utf8', '17. Invalid-UTF-8 names (sections 3.3/8.4; E2)'),
    ('stdin', '18. Stdin file-name mode and the no-operand behaviour'),
    ('e1', '19. E1 re-probes (P5b: colour, tree, --no-quotes)'),
    ('e2', '20. E2 re-probes (P6b: grid, width sources, UTF-8, lossy names)'),
]


class SetupError(Exception):
    pass


class Probe:
    def __init__(self, group, name, argv=(), cwd='', env=None, mode='normal',
                 cols=0, rows=24, note='', requires=(), timeout=None,
                 skip=None, stdin=None):
        self.group = group
        self.name = name
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env or {})
        self.mode = mode            # normal | sink | pipe | pty
        self.cols = cols
        self.rows = rows
        self.note = note
        self.requires = tuple(requires)
        self.timeout = timeout if timeout is not None else TIMEOUT
        self.skip = skip            # a literal skip reason, or None
        self.stdin = stdin          # bytes fed on stdin, or None for DEVNULL


def _p(group, name, *argv, **kw):
    return Probe(group, name, argv, **kw)


# ------------------------------------------------------------------ definitions

def version_probes():
    return [
        _p('version', 'version', '--version',
           note='the exact pinned release output (three lines)'),
        _p('version', 'version_short', '-V'),
        _p('version', 'help', '--help', timeout=30.0,
           note='complete help bytes'),
        _p('version', 'help_short', '-h'),
    ]


def default_probes():
    return [
        _p('default', 'empty', 'empty'),
        _p('default', 'one', 'one'),
        _p('default', 'onedir', 'onedir'),
        _p('default', 'mixed', 'mixed',
           note='lines mode; symlinks render `name -> target`'),
        _p('default', 'mixed_dot_operand', '.', cwd='mixed'),
        _p('default', 'mixed_no_operand', cwd='mixed',
           note='no operand with non-TTY stdin: stdin file-name mode with an '
                'empty list, not a cwd listing; see the stdin group and the '
                'P0 finding on design section 7.2'),
        _p('default', 'one_lines', '-1', 'one'),
        _p('default', 'mixed_lines', '-1', 'mixed'),
        _p('default', 'mixed_lines_dot', '-1', '.', cwd='mixed'),
        _p('default', 'links', 'links'),
        _p('default', 'absolute', 'absolute',
           note='live-only absolute targets: /tmp, /tmp/, /, /tar get'),
    ]


def dotfiles_probes():
    return [
        _p('dotfiles', 'a_empty', '-a', 'empty'),
        _p('dotfiles', 'a_one', '-a', 'one'),
        _p('dotfiles', 'a_mixed', '-a', 'mixed'),
        _p('dotfiles', 'aa_empty', '-aa', 'empty'),
        _p('dotfiles', 'aa_one', '-aa', 'one'),
        _p('dotfiles', 'aa_mixed', '-aa', 'mixed'),
        _p('dotfiles', 'A_mixed', '-A', 'mixed'),
        _p('dotfiles', 'A_a_mixed', '-A', '-a', 'mixed'),
        _p('dotfiles', 'A_aa_mixed', '-A', '-aa', 'mixed'),
        _p('dotfiles', 'all_mixed', '--all', 'mixed'),
        _p('dotfiles', 'all_all_mixed', '--all', '--all', 'mixed'),
        _p('dotfiles', 'a_repeat_mixed', '-a', '-a', 'mixed'),
        _p('dotfiles', 'aa_cwd_mixed', '-aa', '.', cwd='mixed',
           note='-aa on the cwd; explicit . operand (no-operand is stdin mode)'),
        _p('dotfiles', 'aa_name_mixed', '-aa', '--sort=name', 'mixed',
           note='name-only -aa; synthetic dots are comparator-distinct'),
        _p('dotfiles', 'aa_type_mixed', '-aa', '--sort=type', 'mixed'),
        _p('dotfiles', 'aa_ext_mixed', '-aa', '--sort=extension', 'mixed'),
        _p('dotfiles', 'ext_key_d', '-aa', '--sort=extension', 'd', cwd='ext',
           note='dot key File::ext("d") = None'),
        _p('dotfiles', 'ext_key_dx_x', '-aa', '--sort=extension', 'dx.x',
           cwd='ext', note='dot key File::ext("dx.x") = Some("x")'),
        _p('dotfiles', 'ext_key_dx_dot', '-aa', '--sort=extension', 'dx.',
           cwd='ext', note='dot key File::ext("dx.") = Some("")'),
        _p('dotfiles', 'ext_key_dot', '-aa', '--sort=extension', '.',
           cwd='ext', note='dot key File::ext(".") = None'),
        _p('dotfiles', 'ext_key_dotdot', '-aa', '--sort=extension', '..',
           cwd='ext', note='dot key File::ext("..") = None'),
        _p('dotfiles', 'aa_long_mixed', '-aa', '-l', 'mixed',
           note='live/shape-only: dot rows use host parent metadata'),
        _p('dotfiles', 'aa_long_one', '-aa', '-l', 'one',
           note='live/shape-only: dot rows use host parent metadata'),
        _p('dotfiles', 'aa_size_mixed', '-aa', '--sort=size', 'mixed',
           note='live/shape-only: dot sort keys are host st_size'),
        _p('dotfiles', 'aa_modified_mixed', '-aa', '--sort=modified', 'mixed',
           note='live/shape-only: dot sort keys use host parent mtime'),
    ]


def dirs_probes():
    return [
        _p('dirs', 'd_file', '-d', 'f', cwd='operands'),
        _p('dirs', 'd_dir', '-d', 'dir', cwd='operands'),
        _p('dirs', 'd_symlink', '-d', 'l', cwd='operands'),
        _p('dirs', 'd_dangling', '-d', 'broken', cwd='operands'),
        _p('dirs', 'treat_file', '--treat-dirs-as-files', 'f', cwd='operands'),
        _p('dirs', 'treat_dir', '--treat-dirs-as-files', 'dir', cwd='operands'),
        _p('dirs', 'list_dirs_dir', '--list-dirs', 'dir', cwd='operands'),
        _p('dirs', 'list_dirs_symlink', '--list-dirs', 'dl', cwd='operands'),
        _p('dirs', 'd_long_file', '-l', '-d', 'f', cwd='operands'),
        _p('dirs', 'd_long_dir', '-l', '-d', 'dir', cwd='operands'),
        _p('dirs', 'd_long_symlink', '-l', '-d', 'l', cwd='operands'),
        _p('dirs', 'd_long_dangling', '-l', '-d', 'broken', cwd='operands'),
        _p('dirs', 'operand_dir_slash', 'dir/', cwd='operands'),
        _p('dirs', 'operand_symlink_dir_slash', 'dl/', cwd='operands'),
        _p('dirs', 'operand_double_slash', 'sub//f', cwd='operands'),
        _p('dirs', 'operand_dot_slash', 'sub/./f', cwd='operands'),
        _p('dirs', 'operand_dot_file', './f', cwd='operands'),
        _p('dirs', 'operand_dot', '.', cwd='operands'),
        _p('dirs', 'operand_dotdot', '..', cwd='operands'),
        _p('dirs', 'spaced_parent_file', 'x y/sub/f', cwd='operands'),
        _p('dirs', 'spaced_parent_dir', 'x y/sub/', cwd='operands'),
        _p('dirs', 'spaced_parent_link_d', '-d', 'x y/sub/link',
           cwd='operands'),
        _p('dirs', 'd_sub_link', '-d', 'sub/link', cwd='operands'),
        _p('dirs', 'd_sub_dirlink', '-d', 'sub/dirlink', cwd='operands'),
        _p('dirs', 'd_root', '-d', '/', note='live-only operand display /'),
        _p('dirs', 'd_sub_dotdot', '-d', 'sub/..', cwd='operands',
           note='B1: a .. operand keeps its parent prefix'),
        _p('dirs', 'd_spaced_dotdot', '-d', 'x y/..', cwd='operands',
           note="B1: 'x y'/.. with the whole parent escaped once"),
        _p('dirs', 'd_dot', '-d', '.', cwd='operands'),
        _p('dirs', 'd_dotdot', '-d', '..', cwd='operands'),
        _p('dirs', 'd_dot_slash_dotdot', '-d', './..', cwd='operands'),
        _p('dirs', 'd_sub_dotdot_slash', '-d', 'sub/../', cwd='operands',
           note='a trailing slash is dropped from the display'),
        _p('dirs', 'd_root_dotdot', '-d', '/..'),
        _p('dirs', 'd_root_dotdot_dotdot', '-d', '/../..'),
        _p('dirs', 'd_tmp_dotdot', '-d', '/tmp/..'),
        _p('dirs', 'd_slash_slash', '-d', '//'),
        _p('dirs', 'd_slash_slash_dotdot', '-d', '//..'),
    ]


def names_probes():
    return [
        _p('names', 'names_default', 'names'),
        _p('names', 'names_all', '-a', 'names'),
        _p('names', 'names_reverse', '-r', 'names'),
        _p('names', 'names_Name', '--sort=Name', 'names'),
        _p('names', 'names_lower_name', '--sort=name', 'names'),
        _p('names', 'names_filename', '--sort=filename', 'names'),
        _p('names', 'names_dot_name', '--sort=.name', 'names'),
        _p('names', 'tie_Aa', '.', cwd='names/tie_Aa',
           note='live tie: A/a under natcmpFold; readdir-order sensitive'),
        _p('names', 'tie_readme', '.', cwd='names/tie_readme',
           note='live tie: README/readme under natcmpFold'),
        _p('names', 'tie_pic', '.', cwd='names/tie_pic',
           note='live tie: pic4/pic 4 under both comparators'),
        _p('names', 'tie_space', '.', cwd='names/tie_space',
           note='live tie: a/ a under both comparators'),
        _p('names', 'tie_dot', '-aa', '.', cwd='names/tie_dot',
           note='live tie: real ` .` beside synthetic .'),
        _p('names', 'tie_Aa_reverse', '-r', '.', cwd='names/tie_Aa',
           note='-r keeps natcmpFold-equal A/a in readdir order'),
        _p('names', 'tie_pic_reverse', '-r', '.', cwd='names/tie_pic',
           note='-r keeps pic4/pic 4 (equal under both) in readdir order'),
        _p('names', 'group_dirs_first', '--group-directories-first', 'mixed'),
        _p('names', 'group_dirs_first_reverse', '--group-directories-first',
           '-r', 'mixed'),
        _p('names', 'D_mixed', '-D', 'mixed'),
        _p('names', 'f_mixed', '-f', 'mixed'),
        _p('names', 'D_f_mixed', '-D', '-f', 'mixed'),
        _p('names', 'D_reverse_mixed', '-D', '-r', 'mixed'),
    ]


def sort_probes():
    return [
        _p('sort', 'size', '--sort=size', 'sizes'),
        _p('sort', 'size_reverse', '--sort=size', '-r', 'sizes'),
        _p('sort', 'size_name_only_aa', '-aa', '--sort=size', 'sizes',
           note='live/shape-only: dot keys are host metadata'),
        _p('sort', 'size_reverse_ties', '--sort=size', '-r', 'mixed',
           note='-r is stable descending: equal sizes keep readdir order'),
        _p('sort', 'modified_reverse_ties', '--sort=modified', '-r', 'mixed',
           note='-r keeps comparator-equal mtimes in readdir order'),
        _p('sort', 'modified', '--sort=modified', 'times'),
        _p('sort', 'time', '--sort=time', 'times'),
        _p('sort', 'modified_reverse', '--sort=modified', '-r', 'times'),
        _p('sort', 'modified_name_only_aa', '-aa', '--sort=modified', 'times',
           note='live/shape-only: dot keys are host metadata'),
        _p('sort', 'ext_lower', '--sort=ext', 'ext'),
        _p('sort', 'extension_lower', '--sort=extension', 'ext'),
        _p('sort', 'Ext', '--sort=Ext', 'ext'),
        _p('sort', 'Extension', '--sort=Extension', 'ext'),
        _p('sort', 'extension_reverse', '--sort=extension', '-r', 'ext'),
        _p('sort', 'type', '--sort=type', 'mixed'),
        _p('sort', 'type_reverse', '--sort=type', '-r', 'mixed'),
        _p('sort', 'name_uppercase', '--sort=Name', '-r', 'names'),
        _p('sort', 'none', '--sort=none', 'mixed',
           note='out of E0 scope; recorded for completeness'),
        _p('sort', 'inode', '--sort=inode', 'mixed',
           note='host-dependent inode order'),
        _p('sort', 'date_alias', '--sort=date', 'times'),
        _p('sort', 'newest_alias', '--sort=newest', 'times'),
    ]


def quoting_probes():
    return [
        _p('quoting', 'quoting_default', 'quoting'),
        _p('quoting', 'quoting_lines', '-1', 'quoting'),
        _p('quoting', 'quoting_long', '-l', 'quoting'),
        _p('quoting', 'quoting_no_quotes', '--no-quotes', 'quoting'),
        _p('quoting', 'quoting_classify', '-F=always', 'quoting'),
        _p('quoting', 'spaced_operand', 'quoting/with space',
           note='operand parent quoting'),
        _p('quoting', 'spaced_symlink_operand', 'quoting/valid spaced'),
        _p('quoting', 'links_spaced_target', '-1', 'links'),
        _p('quoting', 'ctl_target', '-1', 'quoting',
           note='control byte in a broken symlink target'),
        _p('quoting', 'broken_relative_spaced', 'quoting',
           note='brokenrel -> ../tar get; no root-level sibling; no marker'),
        _p('quoting', 'broken_relative_spaced_F', '-F=always', 'quoting',
           note='same broken target with -F=always: still no marker'),
        _p('quoting', 'broken_relative_spaced_long', '-l', 'quoting/brokenrel'),
    ]


def classify_probes():
    return [
        _p('classify', 'F_mixed', '-F', 'mixed'),
        _p('classify', 'F_always_mixed', '-F=always', 'mixed'),
        _p('classify', 'Fattached_always', '-Falways', 'mixed'),
        _p('classify', 'F_separate_always', '-F', 'always', 'mixed'),
        _p('classify', 'classify_always', '--classify=always', 'mixed'),
        _p('classify', 'classify_automatic', '--classify=automatic', 'mixed'),
        _p('classify', 'classify_separate_automatic', '--classify',
           'automatic', 'mixed',
           note='`automatic` is not in WHEN, so it stays an operand'),
        _p('classify', 'F_separate_automatic', '-F', 'automatic', 'mixed',
           note='`automatic` is not in WHEN, so it stays an operand'),
        _p('classify', 'F_equals_automatic', '-F=automatic', 'mixed'),
        _p('classify', 'F_lines', '-1', '-F=always', 'mixed'),
        _p('classify', 'F_links', '-F=always', 'links'),
        _p('classify', 'F_long_links', '-l', '-F=always', 'links',
           note='long target marker'),
        _p('classify', 'F_d_file', '-d', '-F=always', 'f', cwd='operands'),
        _p('classify', 'F_d_dir', '-d', '-F=always', 'dir', cwd='operands'),
        _p('classify', 'F_d_symlink', '-d', '-F=always', 'l', cwd='operands'),
        _p('classify', 'F_d_dirlink', '-d', '-F=always', 'dl', cwd='operands'),
        _p('classify', 'F_absolute', '-F=always', 'absolute',
           note='live-only root-parent target markers'),
        _p('classify', 'F_grid_width', '--width=80', '-F=always', 'mixed',
           note='grid marker on the link is E2; record now'),
    ]


LONG = ('-l', '--no-user', '--bytes', '--time-style=long-iso')


def long_probes():
    probes = []
    for tree in ('empty', 'one', 'onedir', 'mixed', 'modes', 'sizes',
                 'times', 'long', 'links', 'tree'):
        probes.append(_p('long', 'long_' + tree, *LONG, tree))
    probes.extend([
        _p('long', 'big_7digit', *LONG, 'long/big',
           note='seven-digit --bytes size under LC_ALL=C'),
        _p('long', 'pre_epoch', *LONG, 'times/tpre',
           note='pre-1970 mtime renders `-`'),
        _p('long', 't2038', *LONG, 'times/t2038'),
        _p('long', 'setuid', *LONG, 'modes/m4755'),
        _p('long', 'setgid', *LONG, 'modes/m2755'),
        _p('long', 'sticky', *LONG, 'modes/m1777'),
        _p('long', 'mode000', *LONG, 'modes/m000'),
        _p('long', 'dir_mode000', *LONG, 'modes/d000'),
        _p('long', 'long_xattr', *LONG, 'xattr',
           note='xattr fixture: the reference appends @ to the permission '
                'cell; reference data only, excluded from goldens (9.2/9.4)'),
        _p('long', 'numeric_user', '-l', '-n', 'mixed',
           note='numeric uid; hosts differ, live/shape-only'),
        _p('long', 'no_user', '-l', '--no-user', 'mixed',
           note='non-bytes sizes and default time style'),
        _p('long', 'default_columns', '-l', 'mixed',
           note='user name, bytes default; host-dependent user'),
        _p('long', 'd_file', *LONG, '-d', 'operands/f'),
        _p('long', 'd_dir', *LONG, '-d', 'operands/dir'),
        _p('long', 'd_symlink', *LONG, '-d', 'operands/l'),
        _p('long', 'd_absolute', *LONG, '-d', 'operands/x y/sub/link'),
        _p('long', 'aa_mixed', '-aa', '-l', 'mixed',
           note='live/shape-only dot rows'),
        _p('long', 'cwd_dot', *LONG, '.', cwd='mixed',
           note='pinned directory metadata; parent does not appear with -d? no, listing'),
        _p('long', 'cwd_dot_d', '-l', '-d', '.', cwd='mixed',
           note='pinned directory metadata'),
        _p('long', 'dotdot_d', '-l', '-d', '..', cwd='one',
           note='live-only host parent metadata'),
        _p('long', 'root_d', '-l', '-d', '/',
           note='live-only host root metadata'),
        _p('long', 'names_hidden', *LONG, '-a', 'names'),
    ])
    return probes


def errors_probes():
    return [
        _p('errors', 'missing_operand', 'missing'),
        _p('errors', 'file_as_directory', 'one/f/'),
        _p('errors', 'permission_denied', 'locked/denied',
           requires=('nonroot',), note='skipped when euid is 0'),
        _p('errors', 'permission_denied_entry', 'locked/denied/f',
           requires=('nonroot',), note='metadata failure under a 000 directory'),
        _p('errors', 'unknown_short', '-Q', 'mixed'),
        _p('errors', 'unknown_short_attached', '-Qfoo', 'mixed'),
        _p('errors', 'unknown_short_equals', '-Q=foo', 'mixed'),
        _p('errors', 'unknown_long', '--foo', 'mixed'),
        _p('errors', 'unknown_long_eq', '--foo=bar', 'mixed'),
        _p('errors', 'unknown_long_double_eq', '--foo==', 'mixed'),
        _p('errors', 'unknown_long_empty_value', '--foo=', 'mixed'),
        _p('errors', 'unknown_long_weird', '--=x', 'mixed'),
        _p('errors', 'missing_value_sort', '--sort'),
        _p('errors', 'missing_value_width', '--width'),
        _p('errors', 'missing_value_level', '-L'),
        _p('errors', 'empty_value_sort', '--sort=', 'mixed'),
        _p('errors', 'empty_value_color', '--color=', 'mixed'),
        _p('errors', 'empty_value_icons', '--icons=', 'mixed'),
        _p('errors', 'bad_value_sort', '--sort=bogus', 'mixed'),
        _p('errors', 'bad_value_classify', '--classify=bogus', 'mixed'),
        _p('errors', 'bad_value_color', '--color=bogus', 'mixed'),
        _p('errors', 'bad_value_icons', '--icons=bogus', 'mixed'),
        _p('errors', 'bad_value_time_style', '--time-style=bogus', 'mixed'),
        _p('errors', 'bad_value_level', '-T', '-L', 'x', 'tree'),
        _p('errors', 'dev_full', 'mixed', mode='sink',
           requires=('dev_full',), note='stdout is /dev/full'),
        _p('errors', 'broken_pipe', 'many', mode='pipe',
           note='stdout read end closed before the first write'),
        _p('errors', 'path_over_limit', 'a/' * 2100 + 'f',
           note='over-limit operand: expected error/limitation, not a golden'),
        _p('errors', 'path_near_limit', fixtures.DEEP_OPERAND,
           requires=('deep_path',), note='joined path under 4095 bytes'),
    ]


def symlinks_probes():
    return [
        _p('symlinks', 'operand_file_link', 'links/realfile'),
        _p('symlinks', 'operand_dir_link', 'links/realdirlink'),
        _p('symlinks', 'operand_dir_link_slash', 'links/realdirlink/'),
        _p('symlinks', 'operand_dangling', 'links/dangling'),
        _p('symlinks', 'operand_dangling_slash', 'links/dangling/'),
        _p('symlinks', 'operand_chain', 'links/chain'),
        _p('symlinks', 'operand_loop', 'links/loop1'),
        _p('symlinks', 'operand_b', 'links/b'),
        _p('symlinks', 'cwd_link_file', 'realfile', cwd='links'),
        _p('symlinks', 'cwd_link_dir', 'realdirlink', cwd='links'),
        _p('symlinks', 'cwd_dangling', 'dangling', cwd='links'),
        _p('symlinks', 'd_dirlink', '-d', 'links/realdirlink'),
        _p('symlinks', 'd_filelink', '-d', 'links/realfile'),
        _p('symlinks', 'long_dangling', '-l', 'links/dangling'),
        _p('symlinks', 'long_dirlink', '-l', 'links/realdirlink'),
        _p('symlinks', 'long_loop', '-l', 'links/loop1'),
        _p('symlinks', 'absolute_tmp', '-d', 'absolute/tmp',
           note='-d: absolute target display /tmp, no host listing'),
        _p('symlinks', 'absolute_tmp_slash', '-d', 'absolute/tmp_slash',
           note='-d: raw target /tmp/ is normalized to /tmp in the display'),
        _p('symlinks', 'absolute_tmp_F', '-d', '-F=always', 'absolute/tmp',
           note='-d with -F=always: /tmp plus the directory marker'),
        _p('symlinks', 'absolute_tmp_slash_F', '-d', '-F=always',
           'absolute/tmp_slash', note='-d with -F=always: /tmp/'),
        _p('symlinks', 'absolute_root', '-d', 'absolute/root',
           note='-d: absolute target display /'),
        _p('symlinks', 'absolute_root_F', '-d', '-F=always', 'absolute/root',
           note='expected // with the directory marker'),
        _p('symlinks', 'absolute_space_broken', 'absolute/spaceabs',
           note="expected '/tar get' with no marker"),
        _p('symlinks', 'absolute_space_broken_F', '-F=always',
           'absolute/spaceabs', note='still no marker'),
        _p('symlinks', 'broken_relative_no_sibling', 'quoting/brokenrel',
           note="expected 'link -> ../tar get' with no marker"),
        _p('symlinks', 'broken_relative_no_sibling_F', '-F=always',
           'quoting/brokenrel', note='still no marker'),
        _p('symlinks', 'd_sub_link', '-d', 'operands/sub/link'),
        _p('symlinks', 'd_sub_dirlink', '-d', 'operands/sub/dirlink'),
        _p('symlinks', 'd_sub_link_F', '-d', '-F=always', 'sub/link',
           cwd='operands',
           note='cwd-vs-operand-parent: the cwd decoy target is 0644 and '
                'sub/target is 0755, so resolution uses the operand parent'),
        _p('symlinks', 'd_sub_dirlink_F', '-d', '-F=always', 'sub/dirlink',
           cwd='operands', note='operand-parent directory target -> real/'),
        _p('symlinks', 'spaced_parent_link_d', '-d', 'operands/x y/sub/link'),
        _p('symlinks', 'spaced_parent_link_d_F', '-d', '-F=always',
           'x y/sub/link', cwd='operands',
           note="spaced operand parent: 'x y/sub'/link -> target*"),
        _p('symlinks', 'spaced_parent_link_long', '-l', 'operands/x y/sub/link'),
        _p('symlinks', 'spaced_parent_file', 'operands/x y/sub/f'),
        _p('symlinks', 'aa_symlink_dir', '-aa', 'tree/dirlink',
           note='B2: the synthetic . is the symlink operand, . -> d1'),
        _p('symlinks', 'aa_symlink_dir_F', '-aa', '-F=always',
           'tree/dirlink',
           note='B2: no marker on the dot target when the operand-base stat '
                'fails'),
        _p('symlinks', 'aa_symlink_dir_D', '-aa', '-D', 'tree/dirlink',
           note='B2: the dot is filtered by its lstat kind (link excluded)'),
        _p('symlinks', 'aa_symlink_dir_f', '-aa', '-f', 'tree/dirlink'),
        _p('symlinks', 'aa_symlink_dir_group', '-aa',
           '--group-directories-first', 'tree/dirlink',
           note='B2: a link dot groups as a file here'),
        _p('symlinks', 'aa_symlink_dir_type', '-aa', '--sort=type',
           'tree/dirlink', note='B2: the dot has link rank'),
        _p('symlinks', 'aa_symlink_dir_long', '-aa', '-l', 'tree/dirlink',
           note='B2: the dot row shows the symlink cells and target'),
        _p('symlinks', 'aa_symlink_dir_slash', '-aa', 'tree/dirlink/',
           note='B2: a trailing slash makes the dot a directory'),
        _p('symlinks', 'aa_symlink_dir_realdir', '-aa',
           'links/realdirlink', note='B2: empty target, dot rows only'),
        _p('symlinks', 'aa_symlink_dir_realdir_F', '-aa', '-F=always',
           'links/realdirlink'),
        _p('symlinks', 'aa_selflink', '-aa', 'selflink/d/self',
           note='B2: . -> . resolves with the operand as the target base'),
        _p('symlinks', 'aa_selflink_F', '-aa', '-F=always',
           'selflink/d/self', note='B2: the self-link target is marked'),
    ]


def colour_probes():
    return [
        _p('colour', 'icons_default', 'mixed'),
        _p('colour', 'icons_bare', '--icons', 'mixed'),
        _p('colour', 'icons_never', '--icons=never', 'mixed'),
        _p('colour', 'icons_auto', '--icons=auto', 'mixed'),
        _p('colour', 'icons_automatic', '--icons=automatic', 'mixed'),
        _p('colour', 'icons_always', '--icons=always', 'mixed'),
        _p('colour', 'color_bare', '--color', 'mixed'),
        _p('colour', 'colour_bare', '--colour', 'mixed'),
        _p('colour', 'color_auto', '--color=auto', 'mixed'),
        _p('colour', 'color_automatic', '--color=automatic', 'mixed'),
        _p('colour', 'color_never', '--color=never', 'mixed'),
        _p('colour', 'color_always', '--color=always', 'mixed'),
        _p('colour', 'color_always_long', '-l', '--color=always', 'mixed'),
        _p('colour', 'no_color_env', 'mixed', env={'NO_COLOR': '1'}),
        _p('colour', 'no_color_with_color_always', '--color=always', 'mixed',
           env={'NO_COLOR': '1'}),
        _p('colour', 'no_color_with_color_auto', '--color=auto', 'mixed',
           env={'NO_COLOR': '1'}),
        _p('colour', 'ls_colors', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=01;34:*.txt=01;35'}),
        _p('colour', 'eza_colors', '--color=always', 'mixed',
           env={'EZA_COLORS': 'di=01;32'}),
        _p('colour', 'exa_colors', '--color=always', 'mixed',
           env={'EXA_COLORS': 'di=01;33'}),
        _p('colour', 'color_and_icons_always', '--color=always',
           '--icons=always', 'mixed'),
        _p('colour', 'color_auto_icons_auto', '--color=auto', '--icons=auto',
           'mixed'),
        _p('colour', 'color_never_icons_always', '--color=never',
           '--icons=always', 'mixed'),
    ]


def grid_probes():
    return [
        _p('grid', 'width_20_names', '--width=20', 'names'),
        _p('grid', 'width_40_names', '--width=40', 'names'),
        _p('grid', 'width_80_names', '--width=80', 'names'),
        _p('grid', 'width_120_names', '--width=120', 'names'),
        _p('grid', 'width_0_names', '--width=0', 'names'),
        _p('grid', 'width_1_names', '--width=1', 'names'),
        _p('grid', 'width_40_grid2', '--width=40', 'grid2'),
        _p('grid', 'width_40_grid10', '--width=40', 'grid10'),
        _p('grid', 'width_80_grid100', '--width=80', 'grid100'),
        _p('grid', 'width_80_gridlong10', '--width=80', 'gridlong10'),
        _p('grid', 'width_80_gridlong100', '--width=80', 'gridlong100'),
        _p('grid', 'width_80_many', '--width=80', 'many'),
        _p('grid', 'width_x_names', '--width=x', 'names'),
        _p('grid', 'width_80_color_auto', '--width=80', '--color=auto',
           'names'),
        _p('grid', 'width_80_color_always', '--width=80', '--color=always',
           'names'),
        _p('grid', 'width_80_classify', '--width=80', '-F=always', 'mixed'),
        _p('grid', 'width_80_icons', '--width=80', '--icons', 'names'),
        _p('grid', 'width_80_icons_always', '--width=80', '--icons=always',
           'names'),
        _p('grid', 'width_40_icons_always', '--width=40', '--icons=always',
           'names'),
        _p('grid', 'x_names', '-x', 'names'),
        _p('grid', 'width_80_x_names', '--width=80', '-x', 'names'),
        _p('grid', 'pty_default', '.', cwd='names', mode='pty', cols=80,
           note='stdout is a pty: TERM is unset, size from TIOCSWINSZ'),
        _p('grid', 'pty_cols40', '.', cwd='names', mode='pty', cols=40),
        _p('grid', 'pty_cols120', '.', cwd='names', mode='pty', cols=120),
        _p('grid', 'pty_lines', '-1', '.', cwd='names', mode='pty', cols=80),
        _p('grid', 'pty_color_auto', '--color=auto', '.', cwd='mixed',
           mode='pty', cols=80),
        _p('grid', 'pty_icons_always', '--icons=always', '.', cwd='names',
           mode='pty', cols=80),
        _p('grid', 'pty_x', '-x', '.', cwd='names', mode='pty', cols=80),
    ]


def tree_probes():
    return [
        _p('tree', 'tree_T', '-T', 'tree'),
        _p('tree', 'tree_level1', '-T', '-L', '1', 'tree'),
        _p('tree', 'tree_level2', '-T', '-L', '2', 'tree'),
        _p('tree', 'tree_level3', '-T', '-L', '3', 'tree'),
        _p('tree', 'tree_all', '-T', '-a', 'tree'),
        _p('tree', 'tree_all_level3', '-T', '-a', '-L', '3', 'tree'),
        _p('tree', 'tree_links', '-T', 'links',
           note='symlink directories are not descended by default'),
        _p('tree', 'tree_links_all', '-T', '-a', 'links'),
        _p('tree', 'tree_empty', '-T', 'empty'),
        _p('tree', 'tree_hidden_dir', '-T', 'tree/hidden'),
        _p('tree', 'tree_long', '-T', *LONG, 'tree'),
        _p('tree', 'tree_color', '-T', '--color=always', 'tree'),
        _p('tree', 'tree_icons', '-T', '--icons=always', 'tree'),
        _p('tree', 'tree_spellout', '--tree', 'tree'),
        _p('tree', 'tree_level0', '-T', '-L', '0', 'tree'),
    ]


def environment_probes():
    return [
        _p('environment', 'TIME_STYLE_long_iso', *LONG, 'times',
           env={'TIME_STYLE': 'long-iso'}),
        _p('environment', 'TIME_STYLE_full_iso', '-l', '--no-user',
           '--bytes', 'times', env={'TIME_STYLE': 'full-iso'}),
        _p('environment', 'TIME_STYLE_iso', '-l', '--no-user', '--bytes',
           'times', env={'TIME_STYLE': 'iso'}),
        _p('environment', 'TIME_STYLE_bogus', '-l', '--no-user', '--bytes',
           'times', env={'TIME_STYLE': 'bogus'}),
        _p('environment', 'COLUMNS_40', 'names', env={'COLUMNS': '40'}),
        _p('environment', 'EZA_GRID_ROWS', '--width=40', 'names',
           env={'EZA_GRID_ROWS': '2'}),
        _p('environment', 'EXA_GRID_ROWS', '--width=40', 'names',
           env={'EXA_GRID_ROWS': '2'}),
        _p('environment', 'EZA_ICONS_AUTO', 'names', env={'EZA_ICONS_AUTO': '1'}),
        _p('environment', 'EXA_ICONS_AUTO', 'names', env={'EXA_ICONS_AUTO': '1'}),
        _p('environment', 'NO_COLOR', 'mixed', env={'NO_COLOR': '1'}),
        _p('environment', 'LS_COLORS', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=01;34'}),
        _p('environment', 'EZA_COLORS', '--color=always', 'mixed',
           env={'EZA_COLORS': 'di=01;32'}),
        _p('environment', 'EXA_COLORS', '--color=always', 'mixed',
           env={'EXA_COLORS': 'di=01;33'}),
        _p('environment', 'TZ_New_York', *LONG, 'times',
           env={'TZ': 'America/New_York'}),
        _p('environment', 'TZ_UTC', *LONG, 'times', env={'TZ': 'UTC'}),
        _p('environment', 'EZA_STRICT', '-a', '-a', 'mixed',
           env={'EZA_STRICT': '1'}),
        _p('environment', 'EXA_STRICT', '-a', '-a', 'mixed',
           env={'EXA_STRICT': '1'}),
        _p('environment', 'EZA_STRICT_empty', '-a', '-a', 'mixed',
           env={'EZA_STRICT': ''}),
        _p('environment', 'EZA_DEBUG', 'mixed', env={'EZA_DEBUG': '1'}),
        _p('environment', 'EXA_DEBUG', 'mixed', env={'EXA_DEBUG': '1'}),
        _p('environment', 'EZA_CONFIG_DIR', 'mixed',
           env={'EZA_CONFIG_DIR': '<probe>/env/config'}),
        _p('environment', 'XDG_CONFIG_HOME', 'mixed',
           env={'XDG_CONFIG_HOME': '<probe>/env/xdg'}),
        _p('environment', 'EZA_ICON_SPACING', '--icons=always', 'mixed',
           env={'EZA_ICON_SPACING': '2'}),
        _p('environment', 'EXA_ICON_SPACING', '--icons=always', 'mixed',
           env={'EXA_ICON_SPACING': '2'}),
        _p('environment', 'EZA_OVERRIDE_GIT', 'mixed',
           env={'EZA_OVERRIDE_GIT': '1'}),
        _p('environment', 'EXA_OVERRIDE_GIT', 'mixed',
           env={'EXA_OVERRIDE_GIT': '1'}),
        _p('environment', 'EZA_MIN_LUMINANCE', '--color=always', 'mixed',
           env={'EZA_MIN_LUMINANCE': '0.5'}),
        _p('environment', 'EXA_MIN_LUMINANCE', '--color=always', 'mixed',
           env={'EXA_MIN_LUMINANCE': '0.5'}),
    ]


def stdin_probes():
    return [
        _p('stdin', 'empty_no_operand', cwd='mixed', stdin=b'',
           note='no operands; stdin is a non-TTY pipe with an empty name list'),
        _p('stdin', 'dot_no_operand', cwd='mixed', stdin=b'.\n',
           note='no operands; the stdin line "." is an operand and lists the cwd'),
        _p('stdin', 'names_no_operand', cwd='mixed',
           stdin=b'alpha\nBeta\ndir\n',
           note='no operands; stdin lines become input operands'),
        _p('stdin', 'missing_no_operand', cwd='mixed', stdin=b'missing\n',
           note='no operands; a missing stdin operand is an input-metadata '
                'error (exit 2)'),
        _p('stdin', 'dot_all_no_operand', '-a', cwd='mixed', stdin=b'.\n',
           note='flags apply to the stdin-provided operand'),
        _p('stdin', 'operand_ignores_stdin', 'mixed', stdin=b'alpha\n',
           note='with an operand present stdin is not read'),
    ]


def utf8_probes():
    return [
        _p('utf8', 'list_invalid_dir', 'invalid',
           note='lossy U+FFFD display; E2 shape case'),
        _p('utf8', 'cwd_invalid_dir', '.', cwd='invalid',
           note='explicit . operand; a no-operand invocation is stdin mode'),
        _p('utf8', 'explicit_invalid_operand', b'bad-\xff-name', cwd='invalid'),
        _p('utf8', 'explicit_invalid_path', b'invalid/bad-\xff-name'),
        _p('utf8', 'long_invalid', *LONG, 'invalid'),
        _p('utf8', 'sort_name_invalid', '--sort=name', 'invalid'),
        _p('utf8', 'classify_invalid', '-F=always', 'invalid'),
    ]


def e1_probes():
    """The P5b re-probes: the exact default palette (including hidden and
    setuid/setgid/sticky entries and the file-type categories), the
    LS_COLORS/EZA_COLORS subset semantics, the tree view's view-flag and
    conflict rules, the styled permission-denied child, and --no-quotes.
    Run with ``--only e1`` to write a separate report."""
    return [
        _p('e1', 'colour_palette_all', '--color=always', '-a', 'mixed',
           note='hidden files have no separate SGR; regular files stay plain'),
        _p('e1', 'colour_palette_modes', '--color=always', 'modes',
           note='setuid/setgid/sticky names are executable green, directories '
                'blue; no special SGR for the mode bits in the name view'),
        _p('e1', 'colour_filetypes', '--color=always', 'colours',
           note='default file-type palette: readme/build names, image, video, '
                'music, lossless, crypto, document, compressed, temp, '
                'compiled, source, fi fallback'),
        _p('e1', 'colour_ls_ext_order_a', '--color=always', 'ext',
           env={'LS_COLORS': '*.gz=01;31:*.tar.gz=01;32'},
           note='later definition wins; a dotted key is a literal suffix'),
        _p('e1', 'colour_ls_ext_order_b', '--color=always', 'ext',
           env={'LS_COLORS': '*.tar.gz=01;32:*.gz=01;31'}),
        _p('e1', 'colour_ls_ext_case', '--color=always', 'ext',
           env={'LS_COLORS': '*.TXT=01;31'}),
        _p('e1', 'colour_ls_empty', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=:*.txt='},
           note='an empty value is dropped before the SGR parser'),
        _p('e1', 'colour_ls_invalid', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=bogus'},
           note='a non-SGR value overrides the slot with the empty style'),
        _p('e1', 'colour_ls_sgr_order', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=31;1'},
           note='canonical re-serialisation: bold before colour'),
        _p('e1', 'colour_eza_over_ls', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=01;34', 'EZA_COLORS': 'di=01;32'}),
        _p('e1', 'colour_exa_fallback', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=01;34', 'EXA_COLORS': 'di=01;33'}),
        _p('e1', 'colour_eza_empty', '--color=always', 'mixed',
           env={'LS_COLORS': 'di=01;34', 'EZA_COLORS': ''},
           note='present-empty EZA_COLORS still displaces EXA_COLORS'),
        _p('e1', 'colour_ln_target', '--color=always', 'mixed',
           env={'LS_COLORS': 'ln=target'},
           note='the unsupported "target" value parses to the empty style'),
        _p('e1', 'colour_no_quotes', '--color=always', '--no-quotes', 'mixed',
           note='a valid target name keeps QuoteSpaces; the broken arrow is red'),
        _p('e1', 'colour_no_color_always', '--color=always', 'mixed',
           env={'NO_COLOR': '1'},
           note='an explicit --color overrides the NO_COLOR default'),
        _p('e1', 'tree_oneline_last', '-T', '-1', 'tree',
           note='the last view flag wins: a lines listing, not a tree'),
        _p('e1', 'tree_oneline_first', '-1', '-T', 'tree'),
        _p('e1', 'tree_d_treat', '-T', '-d', 'tree',
           note='without strict mode tree wins over -d (no conflict)'),
        _p('e1', 'tree_error_aa', '-T', '-aa', 'tree'),
        _p('e1', 'tree_oneline_aa_error', '-T', '-1', '-aa', 'tree',
           note='the tree/all-all error is independent of the view flag'),
        _p('e1', 'tree_level_plus', '-T', '-L', '+2', 'tree'),
        _p('e1', 'tree_level_ignored', '-L', 'x', 'tree',
           note='without -T the level is never parsed'),
        _p('e1', 'tree_dirlink_slash', '-T', 'tree/dirlink/',
           note='a trailing slash follows the operand; the root is a directory'),
        _p('e1', 'tree_permission_child', '-T', 'modes',
           requires=('nonroot', 'fifo', 'socket'),
           note='an unreadable directory prints a red synthetic child'),
        _p('e1', 'tree_long_permission', '-T', *LONG, 'locked/denied',
           requires=('nonroot',),
           note='the error row is padded to the table total width'),
        _p('e1', 'no_quotes', '--no-quotes', 'quoting'),
        _p('e1', 'no_quotes_classify', '-F=always', '--no-quotes', 'quoting'),
    ]


def e2_probes():
    """The P6b re-probes: grid layout at pinned widths (short, long, mixed),
    -x, -1 and the -G/-T/-l view rules, every width source (--width and
    COLUMNS with their parse errors), the terminal-ish automatic classify but
    not automatic colour, grid symlink/marker/quoting/broken-colour rules,
    real-TTY grids on a pty, valid UTF-8 widths and sort orders, simple
    lowercase folding, Unicode whitespace ties, and lossy invalid-UTF-8 names
    and targets. Every grid probe passes --icons=never except the pty default
    probes, where the default is no icons anyway. Run with ``--only e2`` for a
    separate report."""
    return [
        # layout: 1/2/10/100 short, 10/100 long and mixed lengths
        _p('e2', 'width_20_grid10', '--width=20', '--icons=never', 'grid10'),
        _p('e2', 'width_40_grid10', '--width=40', '--icons=never', 'grid10'),
        _p('e2', 'width_20_grid100', '--width=20', '--icons=never', 'grid100'),
        _p('e2', 'width_40_grid100', '--width=40', '--icons=never', 'grid100'),
        _p('e2', 'width_80_grid100', '--width=80', '--icons=never', 'grid100'),
        _p('e2', 'width_120_grid100', '--width=120', '--icons=never',
           'grid100'),
        _p('e2', 'width_20_gridlong10', '--width=20', '--icons=never',
           'gridlong10', note='the widest name does not fit: one full name '
                              'per line, no truncation'),
        _p('e2', 'width_40_gridlong10', '--width=40', '--icons=never',
           'gridlong10'),
        _p('e2', 'width_40_gridlong100', '--width=40', '--icons=never',
           'gridlong100'),
        _p('e2', 'width_80_gridlong100', '--width=80', '--icons=never',
           'gridlong100'),
        _p('e2', 'width_20_gridmix', '--width=20', '--icons=never', 'gridmix'),
        _p('e2', 'width_40_gridmix', '--width=40', '--icons=never', 'gridmix'),
        _p('e2', 'width_1_mixed', '--width=1', '--icons=never', 'mixed',
           note='width 1 is a real grid: one cell per line, no link targets'),
        _p('e2', 'width_2_gridmix', '--width=2', '--icons=never', 'gridmix'),
        _p('e2', 'width_40_x_gridmix', '-x', '--width=40', '--icons=never',
           'gridmix', note='-x fills left to right'),
        _p('e2', 'width_20_x_grid10', '-x', '--width=20', '--icons=never',
           'grid10'),
        _p('e2', 'width_40_mixed', '--width=40', '--icons=never', 'mixed',
           note='grid never shows ` -> target`'),
        _p('e2', 'width_40_quoting', '--width=40', '--icons=never',
           'quoting', note='cells are quoted/escaped names'),
        # view rules
        _p('e2', 'G_fallback_grid100', '-G', '--icons=never', 'grid100',
           note='explicit -G with no width uses the 80-column fallback'),
        _p('e2', 'G_oneline_last', '-G', '-1', '--icons=never', 'mixed'),
        _p('e2', 'oneline_then_G', '-1', '-G', '--icons=never', 'mixed'),
        _p('e2', 'tree_then_G', '-T', '-G', '--icons=never', 'grid10',
           note='the last of -G/-T wins'),
        _p('e2', 'G_then_tree', '-G', '-T', '--icons=never', 'grid10'),
        _p('e2', 'long_oneline_G', '-l', '-G', '-1', '--icons=never',
           'mixed'),
        _p('e2', 'lG_grid10', '-l', '-G', '--no-user', '--bytes',
           '--time-style=long-iso', '--icons=never', 'grid10',
           note='eza GridDetails: long rows in grid columns; out of E2 scope '
                'and rejected by this build'),
        _p('e2', 'Gl_grid10', '-G', '-l', '--no-user', '--bytes',
           '--time-style=long-iso', '--icons=never', 'grid10'),
        _p('e2', 'G_l_T', '-G', '-l', '-T', '--no-user', '--bytes',
           '--time-style=long-iso', '--icons=never', 'grid2',
           note='the last of -G/-T is -T: a long tree'),
        _p('e2', 'l_G_T', '-l', '-G', '-T', '--no-user', '--bytes',
           '--time-style=long-iso', '--icons=never', 'grid2'),
        # width sources and parse results
        _p('e2', 'width_0_mixed', '--width=0', '--icons=never', 'mixed',
           note='--width=0 is Automatic: non-TTY Lines, targets shown'),
        _p('e2', 'G_width_0', '-G', '--width=0', '--icons=never', 'grid10'),
        _p('e2', 'width_plus_40', '--width=+40', '--icons=never', 'grid10'),
        _p('e2', 'width_leading_zero', '--width=040', '--icons=never',
           'grid10'),
        _p('e2', 'width_x', '--width=x', 'grid10'),
        _p('e2', 'width_negative', '--width=-1', 'grid10'),
        _p('e2', 'width_empty_value', '--width', '', 'grid10'),
        _p('e2', 'width_empty_equals', '--width=', 'grid10'),
        _p('e2', 'width_double_equals', '--width==', 'grid10'),
        _p('e2', 'width_too_large', '--width=18446744073709551616', 'grid10'),
        _p('e2', 'columns_40_grid100', '--icons=never', 'grid100',
           env={'COLUMNS': '40'}),
        _p('e2', 'columns_0_mixed', '--icons=never', 'mixed',
           env={'COLUMNS': '0'},
           note='COLUMNS=0 is Set(0): a real grid, one column'),
        _p('e2', 'columns_plus', '--icons=never', 'grid10',
           env={'COLUMNS': '+40'}),
        _p('e2', 'columns_over_width', '--width=20', '--icons=never',
           'grid10', env={'COLUMNS': '80'},
           note='--width wins over COLUMNS'),
        _p('e2', 'columns_width_0', '--width=0', '--icons=never', 'grid10',
           env={'COLUMNS': '40'},
           note='--width=0 still suppresses COLUMNS'),
        _p('e2', 'columns_x', '--icons=never', 'grid10',
           env={'COLUMNS': 'x'}),
        _p('e2', 'columns_empty', '--icons=never', 'grid10',
           env={'COLUMNS': ''}),
        _p('e2', 'columns_negative', '--icons=never', 'grid10',
           env={'COLUMNS': '-1'}),
        # terminal-ish auto modes: classify yes, colour no
        _p('e2', 'width_classify_auto', '--width=40', '-F', '--icons=never',
           'mixed'),
        _p('e2', 'width_classify_always', '--width=40', '-F=always',
           '--icons=never', 'mixed'),
        _p('e2', 'width_classify_never', '--width=40', '-F=never',
           '--icons=never', 'mixed'),
        _p('e2', 'G_classify_no_width', '-G', '-F', '--icons=never', 'mixed',
           note='explicit -G without a width is not terminal-ish: no markers'),
        _p('e2', 'columns_classify', '-F', '--icons=never', 'mixed',
           env={'COLUMNS': '40'}),
        _p('e2', 'columns_colour_auto', '--color=auto', '--icons=never',
           'mixed', env={'COLUMNS': '40'},
           note='Set width grids but automatic colour still needs a real '
                'stdout TTY'),
        _p('e2', 'width_colour_auto', '--width=80', '--color=auto',
           '--icons=never', 'mixed'),
        _p('e2', 'width_colour_always', '--width=80', '--color=always',
           '--icons=never', 'mixed',
           note='broken links are red, links cyan; no targets'),
        _p('e2', 'width_colour_F', '--width=80', '--color=always', '-F=always',
           '--icons=never', 'mixed', note='the marker is a plain bit on the '
                                           'styled link name'),
        _p('e2', 'width_colour_links', '--width=80', '--color=always',
           '--icons=never', 'links'),
        _p('e2', 'width_colour_aa', '--width=40', '--color=always', '-aa',
           '--icons=never', 'mixed'),
        _p('e2', 'width_aa_F', '--width=40', '-aa', '-F', '--icons=never',
           'mixed', note='synthetic `.`/`..` are grid cells and take the / '
                         'marker'),
        _p('e2', 'width_no_quotes', '--width=40', '--no-quotes',
           '--icons=never', 'quoting'),
        _p('e2', 'width_quoting_F', '--width=40', '-F=always', '--icons=never',
           'quoting'),
        _p('e2', 'width_d_dir_F', '--width=80', '-d', '-F=always',
           '--icons=never', 'mixed/dir'),
        _p('e2', 'width_dangling', '--width=80', '--icons=never',
           'links/dangling', note='a single symlink operand grids its name '
                                  'only'),
        _p('e2', 'width_dangling_F', '--width=80', '-F=always',
           '--icons=never', 'links/dangling'),
        _p('e2', 'width_dangling_colour', '--width=80', '--color=always',
           '--icons=never', 'links/dangling'),
        _p('e2', 'width_dirlink_d_F', '--width=80', '-d', '-F=always',
           '--icons=never', 'links/realdirlink'),
        # real TTY grids (live/shape-only)
        _p('e2', 'pty_grid_80', '--icons=never', 'grid10', mode='pty',
           cols=80),
        _p('e2', 'pty_grid_40', '--icons=never', 'grid10', mode='pty',
           cols=40),
        _p('e2', 'pty_grid_20', '--icons=never', 'grid10', mode='pty',
           cols=20),
        _p('e2', 'pty_zero_cols', '--icons=never', 'mixed', mode='pty',
           cols=0, note='a zero column report is not terminal-ish: Lines mode'),
        _p('e2', 'pty_zero_rows', '--icons=never', 'mixed', mode='pty',
           cols=80, rows=0,
           note='terminal_size also requires a non-zero row count: with rows=0 '
                'eza is in Lines mode, but io::termWidth exposes only the '
                'column count, so this build cannot see this case (documented)'),
        _p('e2', 'pty_colour_mixed', '--icons=never', 'mixed', mode='pty',
           cols=80, note='auto colour and grid on a real terminal'),
        _p('e2', 'pty_F_mixed', '-F', '--icons=never', 'mixed', mode='pty',
           cols=80),
        _p('e2', 'pty_columns_20', '--icons=never', 'grid10', mode='pty',
           cols=80, env={'COLUMNS': '20'}),
        _p('e2', 'pty_width_40', '--width=40', '--icons=never', 'grid10',
           mode='pty', cols=80),
        _p('e2', 'pty_width_0', '--width=0', '--icons=never', 'grid10',
           mode='pty', cols=40),
        _p('e2', 'pty_oneline', '-1', '--icons=never', 'grid10', mode='pty',
           cols=40),
        # UTF-8 widths, order and folding
        _p('e2', 'utf8_default', '--icons=never', 'utf8',
           note='code point order: a, e+U+0301, f, e-acute, omega, CJK, '
                'halfwidth katakana, emoji'),
        _p('e2', 'utf8_Name', '--sort=Name', '--icons=never', 'utf8'),
        _p('e2', 'utf8_grid_10', '--width=10', '--icons=never', 'utf8',
           note='e-acute width 1, CJK/emoji width 2, combining sequence '
                'width 1'),
        _p('e2', 'utf8_grid_12', '--width=12', '--icons=never', 'utf8'),
        _p('e2', 'utf8_grid_20', '--width=20', '--icons=never', 'utf8'),
        _p('e2', 'utf8_grid_40', '--width=40', '--icons=never', 'utf8'),
        _p('e2', 'utf8_columns_12', '--icons=never', 'utf8',
           env={'COLUMNS': '12'}),
        _p('e2', 'utf8_fold_default', '--icons=never', 'utf8fold',
           note='sigma/Cyrillic/fullwidth lowercasing; tie order is readdir'),
        _p('e2', 'utf8_fold_Name', '--sort=Name', '--icons=never', 'utf8fold'),
        _p('e2', 'utf8_tie_default', '--icons=never', 'utf8tie',
           note='E-acute/e-acute fold tie: readdir order'),
        _p('e2', 'utf8_tie_Name', '--sort=Name', '--icons=never', 'utf8tie'),
        _p('e2', 'utf8_ws_default', '--icons=never', 'utf8ws',
           note='ASCII space, NBSP, EM SPACE and IDEOGRAPHIC SPACE all '
                'compare Equal: readdir order'),
        # lossy invalid UTF-8
        _p('e2', 'bad_list', '--icons=never', 'utf8bad', requires=('invalid_utf8',),
           note='one U+FFFD per maximal invalid subpart'),
        _p('e2', 'bad_Name', '--sort=Name', '--icons=never', 'utf8bad',
           requires=('invalid_utf8',)),
        _p('e2', 'bad_long', *LONG, '--icons=never', 'utf8bad',
           requires=('invalid_utf8',),
           note='lossy names and lossy symlink targets'),
        _p('e2', 'bad_grid_20', '--width=20', '--icons=never', 'utf8bad',
           requires=('invalid_utf8',),
           note='grid width counts replacements as width 1'),
        _p('e2', 'bad_grid_40', '--width=40', '--icons=never', 'utf8bad',
           requires=('invalid_utf8',)),
        _p('e2', 'bad_colour', '--color=always', '--icons=never', 'utf8bad',
           requires=('invalid_utf8',)),
        _p('e2', 'bad_F', '-F=always', '--icons=never', 'utf8bad',
           requires=('invalid_utf8',)),
    ]


def static_probes():
    probes = []
    for fn in (version_probes, default_probes, dotfiles_probes, dirs_probes,
               names_probes, sort_probes, quoting_probes, classify_probes,
               long_probes, errors_probes, symlinks_probes, colour_probes,
               grid_probes, tree_probes, environment_probes, stdin_probes,
               utf8_probes, e1_probes, e2_probes):
        probes.extend(fn())
    return probes


def dt_unknown_probes(path, detected):
    note = 'DT_UNKNOWN=%s at %s' % (detected, path)
    return [
        _p('dt-unknown', 'dt_default', '.', cwd=path, note=note),
        _p('dt-unknown', 'dt_long', '-l', '--no-user', '--bytes',
           '--time-style=long-iso', '.', cwd=path, note=note),
        _p('dt-unknown', 'dt_classify', '-F=always', '.', cwd=path, note=note),
        _p('dt-unknown', 'dt_D', '-D', '.', cwd=path, note=note),
        _p('dt-unknown', 'dt_f', '-f', '.', cwd=path, note=note),
        _p('dt-unknown', 'dt_type_sort', '--sort=type', '.', cwd=path,
           note=note),
        _p('dt-unknown', 'dt_group_dirs', '--group-directories-first', '.',
           cwd=path, note=note),
    ]


# ------------------------------------------------------------- DT_UNKNOWN scan

SYS_GETDENTS64 = 217


def getdents64(path):
    """[(name bytes, d_type)] via getdents64; None when unsupported."""
    if platform.machine() != 'x86_64':
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        buf = ctypes.create_string_buffer(32768)
        out = []
        while True:
            n = libc.syscall(SYS_GETDENTS64, fd, buf, len(buf))
            if n < 0:
                err = ctypes.get_errno()
                if err == errno.EINTR:
                    continue
                return None
            if n == 0:
                return out
            data = buf.raw[:n]
            off = 0
            while off + 19 <= n:
                reclen = int.from_bytes(data[off + 16:off + 18], 'little')
                if reclen <= 0 or off + reclen > n:
                    return None
                dtype = data[off + 18]
                name = data[off + 19:off + reclen].split(b'\0', 1)[0]
                out.append((name, dtype))
                off += reclen
    finally:
        os.close(fd)


def scan_dt_unknown(path):
    entries = getdents64(path)
    if entries is None:
        return None
    unknown = [name for name, dtype in entries if dtype == DT_UNKNOWN]
    return {'entries': len(entries), 'unknown': len(unknown),
            'sample': [os.fsdecode(x) for x in unknown[:8]]}


# --------------------------------------------------------------------- plumbing

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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def pinned_env(probe_dir, extras):
    env = {
        'PATH': REFERENCE_PATH,
        'LC_ALL': 'C',
        'LANG': 'C',
        'LANGUAGE': 'C',
        'TZ': 'UTC',
        'HOME': os.path.join(probe_dir, 'home'),
        'TMPDIR': os.path.join(probe_dir, 'tmp'),
    }
    for key, value in extras.items():
        env[key] = value
    return env


def expand_env(probe_dir, root, value):
    return value.replace('<probe>', probe_dir).replace('<root>', root)


def _limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE, FSIZE))


def _kill(p, pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def run_reference(reference, argv, cwd, io_dir, env, mode, cols, rows, timeout,
                  stdin_data=None):
    """Run the reference once; return (rc, stdout, stderr, notes).

    Capture files live in `io_dir`, never in `cwd`: the cwd is often the
    fixture directory being listed, and an extra file there would change the
    observation.  `stdin_data` is written to a file in `io_dir` and passed as
    stdin (a non-TTY pipe equivalent); None passes /dev/null as before.
    """
    out_path = os.path.join(io_dir, 'stdout.bin')
    err_path = os.path.join(io_dir, 'stderr.bin')
    stdin_path = os.path.join(io_dir, 'stdin.bin')
    notes = []
    timed_out = False
    master = slave = None
    fout = None
    stdin_file = None
    stdin = subprocess.DEVNULL
    if stdin_data is not None:
        with open(stdin_path, 'wb') as f:
            f.write(stdin_data)
        stdin_file = open(stdin_path, 'rb')
        stdin = stdin_file
    pipe = mode == 'pipe'
    ferr = open(err_path, 'wb')
    p = None
    try:
        if mode == 'sink':
            fout = open('/dev/full', 'wb')
        elif mode == 'pty':
            master, slave = os.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack('HHHH', rows, cols, 0, 0))
            fout = slave
        elif pipe:
            fout = subprocess.PIPE
        else:
            fout = open(out_path, 'wb')
        p = subprocess.Popen(
            argv, executable=reference, cwd=cwd, env=env,
            stdin=stdin, stdout=fout, stderr=ferr,
            start_new_session=True, preexec_fn=_limit)
        if mode == 'pty':
            os.close(slave)
            slave = None
        if pipe:
            p.stdout.close()
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            notes.append('timed out after %.0fs; process group killed' % timeout)
            _kill(p, p.pid)
            p.wait()
    finally:
        try:
            ferr.close()
        except OSError:
            pass
        if stdin_file is not None:
            try:
                stdin_file.close()
            except OSError:
                pass
        if slave is not None:
            try:
                os.close(slave)
            except OSError:
                pass
        if fout is not None and mode in ('normal', 'sink'):
            try:
                fout.close()
            except OSError:
                pass
    stdout = b''
    if mode == 'pty':
        chunks = []
        deadline = time.time() + 2.0
        while master is not None:
            left = deadline - time.time()
            if left <= 0:
                notes.append('pty drain timed out')
                break
            r, _, _ = select.select([master], [], [], left)
            if not r:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError as e:
                if e.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
        stdout = b''.join(chunks)
        os.close(master)
    elif pipe:
        notes.append('stdout pipe read end closed before the first write')
    elif mode == 'sink':
        notes.append('stdout is /dev/full; captured bytes are empty')
    else:
        with open(out_path, 'rb') as f:
            stdout = f.read()
    with open(err_path, 'rb') as f:
        stderr = f.read()
    for name in (out_path, err_path, stdin_path):
        try:
            os.unlink(name)
        except OSError:
            pass
    rc = p.returncode if p is not None else None
    if rc == -signal.SIGXFSZ:
        notes.append('output limit (%d bytes) exceeded' % FSIZE)
    if timed_out:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    return rc, stdout, stderr, notes


def _exit_text(rc):
    if rc is None:
        return 'unknown'
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = 'signal %d' % -rc
        return 'signal %d (%s)' % (-rc, name)
    return str(rc)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def normalize_bytes(data, root, probe_dir, tmp):
    """Replace every harness temporary path with the `<root>` placeholder.

    `root` (the fixture root) is inside `probe_dir` (the per-probe directory),
    which is inside `tmp` (the outer mkdtemp).  All three are normalized so a
    default-TMPDIR run cannot commit the harness's own path; the caller then
    refuses any stream that still contains one of them raw.
    """
    data = data.replace(os.fsencode(root), b'<root>')
    data = data.replace(os.fsencode(probe_dir), b'<root>')
    data = data.replace(os.fsencode(tmp), b'<root>')
    return data


def normalize_text(text, root, probe_dir, tmp):
    return (text.replace(root, '<root>')
            .replace(probe_dir, '<root>')
            .replace(tmp, '<root>'))


def capture_probe(reference, probe, tmp):
    probe_dir = tempfile.mkdtemp(prefix='probe-', dir=tmp)
    root = os.path.join(probe_dir, 'fixtures')
    home = os.path.join(probe_dir, 'home')
    tdir = os.path.join(probe_dir, 'tmp')
    os.makedirs(home)
    os.makedirs(tdir)
    caps_now = fixtures.build(root)
    skip_reason = probe.skip
    if skip_reason is None:
        for key in probe.requires:
            if key == 'nonroot' and not caps_now.get('nonroot'):
                skip_reason = 'fixture requires a non-root euid'
            elif key == 'dev_full' and not caps_now.get('dev_full'):
                skip_reason = '/dev/full is absent'
            elif not caps_now.get(key, False):
                skip_reason = 'fixture capability unavailable: ' + key
    if skip_reason:
        fixtures.rmtree(probe_dir)
        return {'group': probe.group, 'name': probe.name, 'skipped': skip_reason,
                'note': probe.note, 'argv': [], 'cwd': probe.cwd,
                'env': {}, 'stdin': probe.stdin, 'exit': None, 'stdout': b'',
                'stderr': b'', 'stdout_sha256': None, 'stderr_sha256': None}
    cwd = probe.cwd if os.path.isabs(probe.cwd) else os.path.join(root, probe.cwd)
    if not os.path.isdir(cwd):
        fixtures.rmtree(probe_dir)
        raise SetupError('%s/%s: cwd does not exist: %r'
                         % (probe.group, probe.name, probe.cwd))
    env = pinned_env(probe_dir, {})
    extras = {k: expand_env(probe_dir, root, v) for k, v in probe.env.items()}
    env.update(extras)
    argv = [os.fsencode(reference)] + [os.fsencode(a) for a in probe.argv]
    try:
        rc, stdout, stderr, notes = run_reference(
            reference, argv, cwd, probe_dir, env, probe.mode, probe.cols,
            probe.rows, probe.timeout, stdin_data=probe.stdin)
    except OSError as e:
        fixtures.rmtree(probe_dir)
        raise SetupError('%s/%s: cannot run the reference: %s'
                         % (probe.group, probe.name, e))
    stdout = normalize_bytes(stdout, root, probe_dir, tmp)
    stderr = normalize_bytes(stderr, root, probe_dir, tmp)
    for stream, data in (('stdout', stdout), ('stderr', stderr)):
        for path in (root, probe_dir, tmp):
            if os.fsencode(path) in data:
                raise SetupError(
                    '%s/%s: %s still contains the harness path %r after '
                    'normalization' % (probe.group, probe.name, stream, path))
    if os.path.isabs(probe.cwd):
        shown_cwd = normalize_text(probe.cwd, root, probe_dir, tmp)
    else:
        shown_cwd = '<root>' + (('/' + probe.cwd) if probe.cwd else '')
    shown_argv = ['<ref>']
    for arg in probe.argv:
        if isinstance(arg, bytes):
            shown_argv.append(normalize_bytes(arg, root, probe_dir, tmp))
        else:
            shown_argv.append(normalize_text(arg, root, probe_dir, tmp))
    shown_env = {k: normalize_text(v, root, probe_dir, tmp)
                 for k, v in extras.items()}
    note = probe.note
    if notes:
        note = (note + '; ' if note else '') + '; '.join(notes)
    result = {
        'group': probe.group,
        'name': probe.name,
        'skipped': None,
        'cwd': shown_cwd,
        'argv': shown_argv,
        'env': shown_env,
        'stdin': probe.stdin,
        'exit': rc,
        'stdout': stdout,
        'stderr': stderr,
        'stdout_sha256': _sha(stdout),
        'stderr_sha256': _sha(stderr),
        'note': note,
    }
    fixtures.rmtree(probe_dir)
    return result


# -------------------------------------------------------------------- rendering

def _code(text):
    text = str(text)
    fence = '`'
    while fence in text:
        fence += '`'
    pad = ' ' if fence == '`' else ''
    return '%s%s%s%s%s' % (fence, pad, text, pad, fence)


def _bytes_block(data):
    return '```python\n' + repr(data) + '\n```'


def render_manifest(manifest):
    lines = ['## Host manifest', '']
    for key, value in manifest:
        if isinstance(value, (dict, list, tuple)):
            lines.append('- %s: %s' % (key, _code(repr(value))))
        else:
            lines.append('- %s: %s' % (key, _code(value)))
    lines.append('')
    return lines


FINDINGS_MD = """\
## P0 findings (not probe output)

### Zero operands read stdin when stdin is not a TTY (design 7.2)

Design section 7.2 says "Zero operands means `.`". The pinned binary instead
selects stdin file-name mode whenever stdin is not a terminal
(`src/options/stdin.rs` `FilesInput::deduce`; `src/main.rs` substitutes `.`
only for `FilesInput::Args`). With an empty stdin name list a no-operand
invocation prints nothing and exits 0, even in a non-empty directory and even
with `-a`/`-aa`.

Evidence: `default/mixed_no_operand` is empty; in the stdin group,
`stdin/empty_no_operand` is empty while `stdin/dot_no_operand` lists the cwd
and `stdin/names_no_operand` treats the stdin lines as input operands;
`stdin/operand_ignores_stdin` shows stdin is not read when an operand is
present.

Decision for E0: this is a documented non-TTY divergence. E0 does not
implement stdin file-name mode; no no-operand case is in the diff matrix
(`default/cwd_mixed` was removed) and the directory-listing intent is covered
with explicit `.` operands (`default/cwd_dot` and the tie/pty probes). The
stdin probes stay as evidence. The design note is not edited.

### A parented `..` operand keeps its parent prefix (design 7.2)

Design 7.2 says `.` and `..` operands display as themselves. The pinned binary
instead applies the same parent-prefix rule as any other operand: `-d sub/..`
prints `sub/..`, `-d 'x y/..'` prints `'x y'/..`, `-d /..` prints `/..`, and
`-d /../..` prints `/../..`; only an empty parent (`-d ..`, `-d .`, `-d ./`)
prints the bare name. Evidence: `dirs/d_sub_dotdot`, `dirs/d_spaced_dotdot`,
`dirs/d_dot`, `dirs/d_dotdot`, `dirs/d_dot_slash_dotdot`,
`dirs/d_sub_dotdot_slash`, `dirs/d_root_dotdot`,
`dirs/d_root_dotdot_dotdot`, `dirs/d_tmp_dotdot`, `dirs/d_slash_slash` and
`dirs/d_slash_slash_dotdot`. P4 implements the probe rule.

### The synthetic `.` on a symlink-to-directory operand is the link (design 7.3)

Design 7.3 models the synthetic `.` row as a directory whose path is the
operand. The pinned binary instead reads `lstat(operand)`, so for a
symlink-to-directory operand the `.` row is the symlink: `-aa tree/dirlink`
prints `. -> d1` followed by the target directory's children, type sorting
gives the dot the link rank (`..`, `d2`, `b.txt`, `. -> d1`), `-D` excludes it
and `-f` excludes both dots, and the restricted long row is `lrwxrwxrwx ... .
-> d1`. `..` is `lstat(join(operand,".."))`.

Target display and classification reorient the raw target against the operand
path itself, not its parent: `-aa links/realdirlink` prints `. -> realdir`
with no marker under `-F=always` (the operand-base stat fails), while
`-aa selflink/d/self` prints `. -> .` and marks/groups it as a directory, and
an absolute or `self`-style target resolves and is marked. A trailing slash
resolves the operand first, so `.` is then a directory row. Evidence:
`symlinks/aa_symlink_dir*`, `symlinks/aa_selflink*`, plus the P4 committed
counterparts `default/aa_symlink_dir*`, `names/aa_*_symlink_dir`,
`sortkeys/*_aa_symlink_dir`, `classify/F_aa_*` and `long/aa_*`. P4 implements
the probe rule.

### `-r` is a stable descending comparison (design 5.3/7.5)

Design 5.3/7.5 say eza sorts ascending and then reverses the whole vector,
which would reverse ties. The pinned binary compares descending with a stable
sort instead: comparator-equal entries keep their readdir order. Evidence:
`sort/size_reverse_ties` and `sort/modified_reverse_ties` on the mixed tree
(equal sizes/mtimes), and `names/tie_Aa_reverse`/`names/tie_pic_reverse`
(fold-tied and both-comparator-tied names). Distinct keys sort descending
either way, so the committed goldens (all tie-free) are unchanged. P4 uses a
stable descending comparison and keeps tie cases live-only.

### E0 matrix exclusions (deliberate; P4 must not silently re-add these)

Removed from the differential matrix because the behaviour is outside E0.
Probe evidence remains where probes exist; the exceptions are called out per
item.

- `names/ordered_dot_name`, `names/ordered_dot_Name`,
  `names/ordered_dot_Name_reverse`: `--sort=.name`/`.Name` are rejected in E0
  (design 7.11). Only `--sort=.name` is probed (`names/names_dot_name`); there
  is no `.Name` probe.
- `quoting/no_quotes`, `quoting/lines_no_quotes`, `quoting/F_no_quotes`:
  `--no-quotes` is E1.
- `operands/multiple_files`, `operands/two_directories`: multiple operands are
  out of E0. No direct multiple-operand probe remains; the related evidence is
  the `automatic`-as-operand probes below (they exit 2).
- `classify/classify_separate_automatic`, `classify/F_separate_automatic`:
  `automatic` is not in WHEN, so eza treats it as an operand (multiple
  operands, out of E0).
- `sortkeys/inode`, `sortkeys/none`: out of E0 (design 7.11).
- `memory/tree`: tree is E1.
- `long/xattr`: xattr `@` is not in E0 (design 7.8/9.2/9.4); the
  `long/long_xattr` probe records the reference behaviour only.
- `errors/bad_level` is retagged own-only: `-T`/`-L` are E1, so E0 rejects
  them as unsupported options with E0's own message, not eza's numeric level
  value error. The `errors/bad_value_level` probe keeps the reference bytes.
- `long/default_user`, `long/no_user`: plain `-l` is not E0; only the
  restricted `-l --no-user --bytes --time-style=long-iso` (or `-n`) form is.
- `default/cwd_mixed`: no-operand stdin mode (above).
- `dirs/root_listing`, `sortkeys/ext_key_root`: redundant host-root live
  cases, removed; their probes were also removed in the host-noise cleanup
  (B1). The design-required root-parent live cases
  (`quoting/root_parent_tmp[_F]` for `eza /tmp`, `quoting/root_parent_root[_F]`
  for `eza /`) remain and do list `/` and `/tmp` in P4 (design 3.4 item 11).

Retagged (still in the matrix, deliberately):

- `errors/bad_time_style` is own-only: the reference accepts and ignores a bad
  `--time-style` in a non-long view; E0 rejects it (own usage error, exit 3).
  This is a recorded divergence.
- `long/aa_mixed`, `long/dot_d`, `long/dotdot_d`, `long/root_d` now use the
  restricted long invocation; `long/numeric_user` uses
  `-l -n --bytes --time-style=long-iso`. All five stay live-only because
  their rows carry host metadata.
- `long/dir_mode000` declares `requires='nonroot'`.
- `classify/F_d_sub_link`, `F_d_sub_dirlink`, `F_d_spaced_sub_link` pin
  cwd-vs-operand-parent target resolution (`sub/link -> target*`,
  `sub/dirlink -> real/`, `'x y/sub'/link -> target*`).

### Host-independent golden matrix (audit)

The quoting fixture has one all-whitespace name. Every all-whitespace name
compares Equal under natord (whitespace is skipped), so two of them made every
name-order listing of that tree readdir-order dependent (design 9.4). An
audit of every recordable tree, including the synthetic `.`/`..` names, found
no comparator-equal pair under `natcmp` or `natcmpFold` outside the live-only
`names`/`names/tie_*` trees; extension-key groups and type-rank groups contain
no equal names in recordable trees, and the size/time sort keys are distinct.

### Host noise and path leakage

- The absolute-target probes (`symlinks/absolute_tmp*`, `symlinks/absolute_root*`)
  now use `-d`, so they record the target display instead of listing the
  host's `/tmp` or `/`. The host-listing probes `dirs/root_listing` and
  `dotfiles/ext_key_root` were removed; no probe in this report lists a host
  directory.
- The probe runner normalizes all three temporary paths (fixture root,
  per-probe dir and the outer probe `mkdtemp` directory) to `<root>` and
  refuses a stream that still contains one after normalization, so a
  default-TMPDIR run cannot commit the harness path.

### Other P0 notes

- DT_UNKNOWN (section 3.4 item 16) remains explicitly **unverified** on this
  host: `/proc`, `/sys`, `/dev/shm` and `/run` all report d_type != 0 (scan
  counts in the host manifest). Re-run with `--dt-unknown DIR` on a
  filesystem that returns an unknown d_type.
- The section 3.1 required-flags probe is implemented in `tests/eza.py`
  (`check_reference_flags`): under `--require-reference` the pinned binary
  must accept `-l --no-user --bytes --time-style=long-iso
  --treat-dirs-as-files`, else setup exits 2.
- `--record` deliberately does not write `VERSION`, although design section
  9.6 lists it among `--record` outputs; keeping VERSION under the authorized
  reference step is the safer choice stated by the harness docstring.
- The xattr fixture is protected against ambient xattrs: `fixtures.build`
  reports `ambient_xattr` when a file we never label carries an xattr
  (SELinux), and every long-view golden case (detected from `-l`/`--long`,
  not just the `long` family: also `quoting/long`,
  `quoting/broken_relative_long`, `classify/F_long_links`,
  `dirs/d_long_file`, `dirs/d_long_dir`, `dirs/d_long_symlink`) then skips
  rather than absorb an `@` marker.
- The invalid-UTF-8 shape case stays probe-only until an E2 shape mode
  exists; there is no shape-comparison runner in P0.
- REFERENCE.md is not byte-for-byte reproducible: the host manifest records
  live host data (e.g. `/proc` entry counts). That is accepted.

Note for reviewers: `git diff --check` flags trailing whitespace in
`long/dir_mode000.stderr` (the permission summary line ends with a space).
That is an exact reference byte, not formatting; do not edit it.
"""


def render(results, manifest, reference):
    lines = [
        '# eza probe report (P0; group 19 is the P5b E1 re-probe and group 20 '
        'the P6b E2 re-probe)',
        '',
        'Produced by `obo/tests/eza/probe.py` against the authorized pinned',
        'reference. Every byte below is observed output; skipped probes have no',
        'output. Temporary harness paths are normalized to `<root>`; probe',
        'arguments show the reference as `<ref>`, while the report header and',
        'host manifest record its absolute path and sha256. Absolute-target and',
        'host-metadata probes are live/shape-only and are not committed goldens',
        '(design section 9.4).',
        '',
        'Reference: %s' % _code(reference),
        '',
    ]
    lines.extend(render_manifest(manifest))
    by_group = {}
    for result in results:
        by_group.setdefault(result['group'], []).append(result)
    for group, title in GROUPS:
        group_results = by_group.get(group, [])
        if not group_results:
            continue
        lines.append('## ' + title)
        lines.append('')
        for result in group_results:
            lines.append('### ' + result['name'])
            lines.append('')
            if result.get('skipped'):
                lines.append('- skipped: ' + _code(result['skipped']))
                if result.get('note'):
                    lines.append('- note: ' + _code(result['note']))
                lines.append('')
                continue
            lines.append('- cwd: ' + _code(result['cwd']))
            lines.append('- argv: ' + _code(repr(result['argv'])))
            lines.append('- env extras: ' + _code(repr(result['env'])))
            if result.get('stdin') is not None:
                lines.append('- stdin bytes:')
                lines.append('')
                lines.append(_bytes_block(result['stdin']))
                lines.append('')
            lines.append('- exit: ' + _code(_exit_text(result['exit'])))
            lines.append('- stdout sha256: ' + _code(result['stdout_sha256']))
            lines.append('- stderr sha256: ' + _code(result['stderr_sha256']))
            lines.append('- stdout bytes:')
            lines.append('')
            lines.append(_bytes_block(result['stdout']))
            lines.append('')
            lines.append('- stderr bytes:')
            lines.append('')
            lines.append(_bytes_block(result['stderr']))
            lines.append('')
            if result.get('note'):
                lines.append('- note: ' + _code(result['note']))
                lines.append('')
    lines.append(FINDINGS_MD.rstrip('\n'))
    return '\n'.join(lines) + '\n'


# ------------------------------------------------------------------------- main

def select_probes(probes, only):
    if not only:
        return probes
    wanted = {part.strip() for part in only.split(',') if part.strip()}
    return [p for p in probes
            if p.group in wanted or any(w in p.name for w in wanted)]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Record the pinned eza reference probes (design section 3.4)')
    ap.add_argument('--reference', metavar='PATH',
                    help='the pinned eza binary to execute (required)')
    ap.add_argument('--out', metavar='PATH', required=True,
                    help='Markdown report to write')
    ap.add_argument('--only', metavar='GROUP[,GROUP]',
                    help='only these groups (or case-name substrings)')
    ap.add_argument('--dt-unknown', metavar='DIR', action='append', default=[],
                    help='a directory on a DT_UNKNOWN filesystem (repeatable)')
    ap.add_argument('--keep', action='store_true',
                    help='keep the temporary fixture tree and print its path')
    a = ap.parse_args(argv)

    if not a.reference:
        print('probe.py: --reference <path> is required: the pinned eza binary '
              'must be supplied by the authorized reference step; refusing to '
              'invent output', file=sys.stderr)
        return 2
    reference = os.path.abspath(a.reference)
    if not os.path.isfile(reference):
        print('probe.py: reference is not a regular file: %s' % reference,
              file=sys.stderr)
        return 2
    if not os.access(reference, os.X_OK):
        print('probe.py: reference is not executable: %s' % reference,
              file=sys.stderr)
        return 2

    try:
        reference_sha = sha256_file(reference)
    except OSError as e:
        print('probe.py: cannot hash the reference: %s' % e, file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix='obo-eza-probe-')
    if a.keep:
        print('temporary tree: ' + tmp, file=sys.stderr)
    try:
        probes = static_probes()

        candidates = a.dt_unknown or ['/proc', '/sys', '/dev/shm', '/run']
        scans = {}
        chosen = None
        for candidate in candidates:
            path = os.path.abspath(candidate)
            scan = scan_dt_unknown(path)
            scans[path] = scan
            if scan and scan['unknown'] and chosen is None:
                chosen = path
        if a.dt_unknown and not chosen:
            for candidate in a.dt_unknown:
                path = os.path.abspath(candidate)
                if os.path.isdir(path):
                    chosen = path
                    break
        if chosen:
            probes.extend(dt_unknown_probes(chosen, scans.get(chosen)))
        else:
            probes.append(Probe(
                'dt-unknown', 'dt_unavailable', skip=(
                    'no DT_UNKNOWN filesystem found among %r; pass '
                    '--dt-unknown DIR' % (candidates,)),
                note='the lazy-lstat fallback is not exercised on this host'))

        probes = select_probes(probes, a.only)
        if not probes:
            raise SetupError('no probes selected')
        fixture_caps = fixtures.build(os.path.join(tmp, 'capability-probe'))
        fixtures.rmtree(os.path.join(tmp, 'capability-probe'))
        manifest = [
            ('platform', platform.platform()),
            ('kernel', os.uname().release),
            ('machine', platform.machine()),
            ('python', platform.python_version()),
            ('euid', os.geteuid()),
            ('egid', os.getegid()),
            ('umask', '0%03o' % read_umask()),
            ('filesystem', fs_type(tmp)),
            ('environment pin', 'PATH=/usr/bin:/bin LC_ALL=C LANG=C '
                                'LANGUAGE=C TZ=UTC; HOME/TMPDIR per probe; '
                                'all section 9.5 variables unset unless set'),
            ('stdin', '/dev/null unless the probe supplies stdin bytes '
                      '(group 18)'),
            ('reference path', reference),
            ('reference sha256', reference_sha),
            ('fixture capabilities', fixture_caps),
            ('dt-unknown candidates', {k: v for k, v in scans.items()}),
        ]
        results = []
        for i, probe in enumerate(probes, 1):
            result = capture_probe(reference, probe, tmp)
            results.append(result)
            status = 'skip' if result.get('skipped') else _exit_text(
                result['exit'])
            print('[%d/%d] %-12s %-32s %s'
                  % (i, len(probes), probe.group, probe.name, status),
                  file=sys.stderr)
        text = render(results, manifest, reference)
        out_dir = os.path.dirname(os.path.abspath(a.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(a.out, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        print('wrote %s (%d probes)' % (a.out, len(results)), file=sys.stderr)
        return 0
    except SetupError as e:
        print('probe.py: %s' % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('probe.py: interrupted', file=sys.stderr)
        return 2
    finally:
        if not a.keep:
            fixtures.rmtree(tmp)


if __name__ == '__main__':
    sys.exit(main())
