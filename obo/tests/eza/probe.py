#!/usr/bin/env python3
"""P0 probe runner for the pinned eza reference (design section 3.4).

    python3 obo/tests/eza/probe.py --reference /path/to/eza --out REFERENCE.md
        [--only GROUP[,GROUP]] [--dt-unknown DIR] [--keep]

The reference path is required.  Without it this program exits 2 and writes
nothing: it never fabricates or replays output.  The reference is executed as
``[reference, *argv]`` with ``executable=<absolute path>`` in the exact
section 9.5 environment, inside a freshly generated fixture tree per probe.
All host-dependent temporary paths are normalized to ``<root>`` before bytes
are rendered or hashed.

Each probe records its group, name, cwd, argv, environment extras, exit status
(including signals), exact stdout/stderr bytes as a Python ``bytes`` literal
and sha256, and a note.  The report is grouped Markdown with a host manifest.
Probe definitions cover all sixteen section 3.4 items (version, default output,
dotfiles, dirs-as-files, name order, sort options, quoting, classify,
restricted long, errors/exit, symlink operands, colour/icons, grid/--width,
tree, environment variables, DT_UNKNOWN) plus an invalid-UTF-8 group from
sections 3.3 and 8.4.

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
]


class SetupError(Exception):
    pass


class Probe:
    def __init__(self, group, name, argv=(), cwd='', env=None, mode='normal',
                 cols=0, rows=24, note='', requires=(), timeout=None,
                 skip=None):
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
        _p('default', 'mixed_no_operand', cwd='mixed'),
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
        _p('dotfiles', 'aa_cwd_mixed', '-aa', cwd='mixed'),
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
        _p('dotfiles', 'ext_key_root', '-aa', '--sort=extension', '/',
           cwd='empty', note='dot key File::ext("/") = None; host root data'),
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
        _p('dirs', 'root_listing', '/',
           note='live-only: host root directory listing'),
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
        _p('names', 'tie_Aa', cwd='names/tie_Aa',
           note='live tie: A/a under natcmpFold; readdir-order sensitive'),
        _p('names', 'tie_readme', cwd='names/tie_readme',
           note='live tie: README/readme under natcmpFold'),
        _p('names', 'tie_pic', cwd='names/tie_pic',
           note='live tie: pic4/pic 4 under both comparators'),
        _p('names', 'tie_space', cwd='names/tie_space',
           note='live tie: a/ a under both comparators'),
        _p('names', 'tie_dot', '-aa', cwd='names/tie_dot',
           note='live tie: real ` .` beside synthetic .'),
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
        _p('symlinks', 'absolute_tmp', 'absolute/tmp',
           note='live-only absolute target /tmp'),
        _p('symlinks', 'absolute_tmp_slash', 'absolute/tmp_slash',
           note='live-only raw target /tmp/'),
        _p('symlinks', 'absolute_tmp_F', '-F=always', 'absolute/tmp'),
        _p('symlinks', 'absolute_tmp_slash_F', '-F=always',
           'absolute/tmp_slash'),
        _p('symlinks', 'absolute_root', 'absolute/root',
           note='live-only absolute target /'),
        _p('symlinks', 'absolute_root_F', '-F=always', 'absolute/root',
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
        _p('symlinks', 'spaced_parent_link_d', '-d', 'operands/x y/sub/link'),
        _p('symlinks', 'spaced_parent_link_long', '-l', 'operands/x y/sub/link'),
        _p('symlinks', 'spaced_parent_file', 'operands/x y/sub/f'),
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
        _p('grid', 'pty_default', cwd='names', mode='pty', cols=80,
           note='stdout is a pty: TERM is unset, size from TIOCSWINSZ'),
        _p('grid', 'pty_cols40', cwd='names', mode='pty', cols=40),
        _p('grid', 'pty_cols120', cwd='names', mode='pty', cols=120),
        _p('grid', 'pty_lines', '-1', cwd='names', mode='pty', cols=80),
        _p('grid', 'pty_color_auto', '--color=auto', cwd='mixed',
           mode='pty', cols=80),
        _p('grid', 'pty_icons_always', '--icons=always', cwd='names',
           mode='pty', cols=80),
        _p('grid', 'pty_x', '-x', cwd='names', mode='pty', cols=80),
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


def utf8_probes():
    return [
        _p('utf8', 'list_invalid_dir', 'invalid',
           note='lossy U+FFFD display; E2 shape case'),
        _p('utf8', 'cwd_invalid_dir', cwd='invalid'),
        _p('utf8', 'explicit_invalid_operand', b'bad-\xff-name', cwd='invalid'),
        _p('utf8', 'explicit_invalid_path', b'invalid/bad-\xff-name'),
        _p('utf8', 'long_invalid', *LONG, 'invalid'),
        _p('utf8', 'sort_name_invalid', '--sort=name', 'invalid'),
        _p('utf8', 'classify_invalid', '-F=always', 'invalid'),
    ]


def static_probes():
    probes = []
    for fn in (version_probes, default_probes, dotfiles_probes, dirs_probes,
               names_probes, sort_probes, quoting_probes, classify_probes,
               long_probes, errors_probes, symlinks_probes, colour_probes,
               grid_probes, tree_probes, environment_probes, utf8_probes):
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


def run_reference(reference, argv, cwd, io_dir, env, mode, cols, rows, timeout):
    """Run the reference once; return (rc, stdout, stderr, notes).

    Capture files live in `io_dir`, never in `cwd`: the cwd is often the
    fixture directory being listed, and an extra file there would change the
    observation.
    """
    out_path = os.path.join(io_dir, 'stdout.bin')
    err_path = os.path.join(io_dir, 'stderr.bin')
    notes = []
    timed_out = False
    master = slave = None
    fout = None
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
            stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr,
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
    for name in (out_path, err_path):
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


def normalize_bytes(data, root, probe_dir):
    data = data.replace(os.fsencode(root), b'<root>')
    data = data.replace(os.fsencode(probe_dir), b'<root>')
    return data


def normalize_text(text, root, probe_dir):
    return text.replace(root, '<root>').replace(probe_dir, '<root>')


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
                'env': {}, 'exit': None, 'stdout': b'', 'stderr': b'',
                'stdout_sha256': None, 'stderr_sha256': None}
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
            probe.rows, probe.timeout)
    except OSError as e:
        fixtures.rmtree(probe_dir)
        raise SetupError('%s/%s: cannot run the reference: %s'
                         % (probe.group, probe.name, e))
    stdout = normalize_bytes(stdout, root, probe_dir)
    stderr = normalize_bytes(stderr, root, probe_dir)
    if os.path.isabs(probe.cwd):
        shown_cwd = normalize_text(probe.cwd, root, probe_dir)
    else:
        shown_cwd = '<root>' + (('/' + probe.cwd) if probe.cwd else '')
    shown_argv = ['<ref>']
    for arg in probe.argv:
        if isinstance(arg, bytes):
            shown_argv.append(normalize_bytes(arg, root, probe_dir))
        else:
            shown_argv.append(normalize_text(arg, root, probe_dir))
    shown_env = {k: normalize_text(v, root, probe_dir)
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


def render(results, manifest, reference):
    lines = [
        '# eza P0 probe report',
        '',
        'Produced by `obo/tests/eza/probe.py` against the authorized pinned',
        'reference. Every byte below is observed output; skipped probes have no',
        'output. Temporary fixture paths are normalized to `<root>` and the',
        'reference path to `<ref>`. Absolute-target and host-metadata probes are',
        'live/shape-only and are not committed goldens (design section 9.4).',
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
