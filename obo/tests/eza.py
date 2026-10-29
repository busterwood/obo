#!/usr/bin/env python3
"""The differential gate for `eza` (E0, E1 and E2).

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
reference/own goldens plus a manifest, and refuses path-leaking output.  This
deliberately differs from a literal reading of design section 9.6 (which lists
VERSION among `--record` outputs): keeping VERSION under the authorized
reference step is the safer choice and is recorded in REFERENCE.md's P0
findings.

`--require-reference` additionally runs the section 3.1 required-flags probe
against the pinned binary (`-l --no-user --bytes --time-style=long-iso
--treat-dirs-as-files`); a reference that does not accept it is a setup error.

Own eza and P4/P5b/P6b gating
-----------------------------
P4 supplies `cmd/eza.obx`; `own_available()` tests for it.  A normal run
builds it through `build_tools.build(['eza'], private_out, obo, rt)` in the
harness temp dir (its own `sym/`), compares every golden and live case, and
checks the own-tagged usage errors against `golden/own/`.  `--record` also
records those own cases from the built binary.  `--memory` builds eza, creates
10,000 entries, runs the restricted-long, name-only, tree, colour and grid
cases under
`OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`, parses the single
`obo: N peak bytes, M allocated` line, rejects any leak/double-close line or
nonzero exit, and enforces the measured `EZA_PEAK_MAX`; it also prints our max
RSS, the pinned reference's max RSS and both binary sizes for the record
(measurement only, never gated).

The P0 fallback stays for a tree without the own source: with no own build,
`--record` records reference goldens only and skips own cases, a normal run
checks the committed reference goldens against the pinned reference and skips
own cases, and `--memory` exits 2.  Nothing about the reference pin or the
reference golden checks is relaxed: `--require-reference` requires the exact
version and binary hash, and a missing or malformed golden is a setup error.
With no own build and no usable pinned reference there is nothing to run, so
the harness is a setup error (exit 2), never a silent all-skip pass.

Scope
-----
Reference behaviour outside E0/E1/E2 is not in the golden/ref matrix: multiple
operands, `.name`/`.Name` sorts, `--sort=inode|none`, plain `-l`, xattr rows,
the no-operand stdin mode, icons, GridDetails (`-l` with `-G`) and long-column
colour are excluded (the exact case list and reasons are in
`eza/REFERENCE.md`, `eza/E1-PROBES.md` and `eza/E2-PROBES.md`).  E1 adds the
colour, tree and --no-quotes families; E2 adds the grid and utf8 families, the
width sources and lossy invalid-UTF-8 names.  Probe evidence for excluded
behaviours stays in the probe report.

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
both are compared. A live-only case that mismatches is re-run once (ours and
the reference); only when a program's own output changed between the two
attempts is the host listing unstable, and the case is then skipped with
"live case unstable" -- never passed, and a stable difference still fails.  `--record` selects `golden` and `own` cases, runs the same
fixtures and environments, refuses any stdout/stderr line containing an
absolute path token (line start, after `->`, or inside quotes) or the case
directory, and refuses to overwrite a differing golden without `--force`.
`--record -k` is refused: the manifest must describe the full golden-tagged
selection, and the manifest is verified against it on every run.

Exit status: 0 pass, 1 test failure, 2 harness/setup failure.
"""

import argparse
import atexit
import concurrent.futures
import fcntl
import json
import os
import pty
import re
import resource
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))        # .../obo/tests
FORK = os.path.dirname(HERE)                             # .../obo
ROOT = os.path.dirname(FORK)                             # repository root
EZA = 'eza'
REFERENCE_PATH = '/usr/bin:/bin'
FSIZE = 16 * 1024 * 1024                                 # per-process cap
# The eza memory cap is a measured bound, not the compiler's PEAK_BUDGET:
# the first gated runs of the two 10,000-entry cases (P4, OBO_STATS=1) peaked
# at 2,185,008 (long) and 2,184,384 (names) live heap bytes, so the cap is the
# long measurement plus a 25.97% margin (2,752,512 = 672 * 4096). P5b adds the
# tree and colour cases and P6b the grid case; the merged-tree measurements are
# 2,190,032 (long), 2,189,408 (names), 2,190,160 (tree), 2,189,632 (colour)
# and 2,189,808 (grid) bytes, still under the unchanged cap. A few hundred
# bytes of the peak track the random temp-path length in the environment.
EZA_PEAK_MAX = 2752512
VERSION = os.path.join(HERE, 'eza', 'VERSION')
GOLDEN = os.path.join(HERE, 'eza', 'golden')
OWN_SOURCE = os.path.join(FORK, 'cmd', 'eza.obx')       # P4 own source

sys.path.insert(0, os.path.join(HERE, 'eza'))
sys.path.insert(0, os.path.join(FORK, 'tools'))
import build_tools  # noqa: E402
import fixtures  # noqa: E402


def own_available():
    """True once the P4 own eza source (`obo/cmd/eza.obx`) exists."""
    return os.path.isfile(OWN_SOURCE)


LONG = ('-l', '--no-user', '--bytes', '--time-style=long-iso')
FAMILIES = ('default', 'names', 'sortkeys', 'long', 'quoting', 'classify',
            'dirs', 'operands', 'errors', 'colour', 'tree', 'grid', 'utf8',
            'memory')
TAGS = ('ref', 'golden', 'own', 'skip')
REF, GOLDEN_TAG, OWN, SKIP = TAGS


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------- cases

class Case:
    def __init__(self, family, name, argv=(), cwd='', env=None, tags=(),
                 mode='normal', note='', requires=(), slow=False,
                 live_only=False, memory_entries=0, cols=80):
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
        if mode == 'pty' and (GOLDEN_TAG in tags or not live_only):
            raise SetupError('%s/%s: pty cases are live-only (design 9.4)'
                             % (family, name))
        if mode not in ('normal', 'sink', 'pipe', 'pty'):
            raise SetupError('%s/%s: unknown mode %r' % (family, name, mode))
        if cols < 0:
            raise SetupError('%s/%s: negative pty column count' % (family, name))
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
        self.cols = cols
        self.key = family + '/' + name

    def __str__(self):
        return self.key


CASES = []


def _add(family, name, argv=(), cwd='', env=None, tags=(), mode='normal',
         note='', requires=(), slow=False, live_only=False,
         memory_entries=0, cols=80):
    CASES.append(Case(family, name, argv, cwd, env, tags, mode, note,
                      requires, slow, live_only, memory_entries, cols))


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
    _g('default', 'aa_symlink_dir', '-aa', 'tree/dirlink',
       note='synthetic . is the symlink operand (probe b2)')
    _g('default', 'aa_symlink_dir_slash', '-aa', 'tree/dirlink/',
       note='trailing slash follows the operand before the dot row (probe b2)')
    _g('default', 'aa_symlink_dir_realdir', '-aa', 'links/realdirlink',
       note='empty target dir; only the two dot rows (probe b2)')
    _g('default', 'aa_selflink', '-aa', 'selflink/d/self',
       note='self-link dot target .; target display and the follow quirk')


def _name_cases():
    _g('names', 'ordered_default', 'names/ordered')
    _g('names', 'ordered_Name', '--sort=Name', 'names/ordered')
    _g('names', 'ordered_lower', '--sort=name', 'names/ordered')
    _g('names', 'ordered_filename', '--sort=filename', 'names/ordered')
    _g('names', 'ordered_reverse', '-r', 'names/ordered')
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
    _g('names', 'ordered_filename_reverse', '--sort=filename', '-r',
       'names/ordered')
    _g('names', 'ordered_aa', '-aa', 'names/ordered')
    _g('names', 'ordered_aa_Name', '-aa', '--sort=Name', 'names/ordered')
    _g('names', 'group_dirs_first_tree', '--group-directories-first', 'tree')
    _g('names', 'D_links', '-D', 'links')
    _g('names', 'aa_D_symlink_dir', '-aa', '-D', 'tree/dirlink',
       note='a symlink dot is filtered by its lstat kind (probe b2)')
    _g('names', 'aa_f_symlink_dir', '-aa', '-f', 'tree/dirlink')
    _g('names', 'group_aa_symlink_dir', '-aa', '--group-directories-first',
       'tree/dirlink',
       note='the dot follows only when the operand-base target stat succeeds')
    _g('names', 'grouplast_aa_symlink_dir', '-aa',
       '--group-directories-last', 'tree/dirlink')
    _g('names', 'group_aa_selflink', '-aa', '--group-directories-first',
       'selflink/d/self',
       note='self-link dot classifies as a directory and groups first')
    _g('names', 'aa_D_mixed', '-aa', '-D', 'mixed')
    _g('names', 'aa_f_mixed', '-aa', '-f', 'mixed')
    _g('names', 'aa_group_mixed', '-aa', '--group-directories-first', 'mixed')
    _r('names', 'tie_Aa_reverse', '-r', '.', cwd='names/tie_Aa',
       note='-r keeps natcmpFold-equal names in readdir order')
    _r('names', 'tie_pic_reverse', '-r', '.', cwd='names/tie_pic',
       note='-r keeps both-comparator-equal names in readdir order')


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
    _g('sortkeys', 'type_aa_symlink_dir', '-aa', '--sort=type',
       'tree/dirlink',
       note='the synthetic dot uses its lstat kind (link rank), probe b2')
    _g('sortkeys', 'ext_aa_symlink_dir', '-aa', '--sort=extension',
       'tree/dirlink',
       note='the dot key is File::ext(operand), not the target path')
    _r('sortkeys', 'size_aa_symlink_dir', '-aa', '--sort=size',
       'tree/dirlink', note='equal dot/regular sizes: readdir tie')
    _r('sortkeys', 'modified_aa_symlink_dir', '-aa', '--sort=modified',
       'tree/dirlink', note='equal pinned mtimes: readdir tie')
    _r('sortkeys', 'size_reverse_ties', '--sort=size', '-r', 'mixed',
       note='-r is a stable descending comparison: equal sizes keep order')
    _r('sortkeys', 'modified_reverse_ties', '--sort=modified', '-r', 'mixed',
       note='-r keeps comparator-equal mtimes in readdir order')


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
    _g('long', 'd_pipe', *LONG, '-d', 'modes/pipe')
    _g('long', 'd_socket', *LONG, '-d', 'modes/sock')
    _g('long', 'd_sub_file', *LONG, '-d', 'operands/sub/f')
    _g('long', 'd_sub_exec', *LONG, '-d', 'operands/sub/target')
    _g('long', 'd_sub_link', *LONG, '-d', 'operands/sub/link')
    _g('long', 'd_sub_dirlink', *LONG, '-d', 'operands/sub/dirlink')
    _r('long', 'aa_mixed', '-aa', *LONG, 'mixed',
       note='restricted long; dot rows use host parent metadata')
    _r('long', 'numeric_user', '-l', '-n', '--bytes',
       '--time-style=long-iso', 'mixed',
       note='restricted long with numeric uid; width varies by host')
    _r('long', 'dot_d', *LONG, '-d', '.', cwd='mixed',
       note='-d . is live/shape-only')
    _r('long', 'dotdot_d', *LONG, '-d', '..', cwd='one',
       note='-d .. is live/shape-only')
    _r('long', 'root_d', *LONG, '-d', '/',
       note='-d / is live/shape-only')
    _g('long', 'view_1_l', '-1', *LONG, 'mixed',
       note='the later -l selects the long view')
    _g('long', 'view_l_1', '-l', '-1', '--no-user', '--bytes',
       '--time-style=long-iso', 'mixed',
       note='the later -1 selects one-per-line')
    _g('long', 'aa_symlink_dir', '-aa', *LONG, 'tree/dirlink',
       note='the dot row is the symlink operand (probe b2)')
    _g('long', 'aa_symlink_dir_F', '-aa', *LONG, '-F=always', 'tree/dirlink')
    _g('long', 'aa_selflink', '-aa', *LONG, 'selflink/d/self')
    _r('long', 'deep_operand', fixtures.DEEP_OPERAND,
       note='joined path near but under 4095 bytes')


def _quoting_cases():
    _g('quoting', 'default', 'quoting')
    _g('quoting', 'lines', '-1', 'quoting')
    _g('quoting', 'long', *LONG, 'quoting')
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
    _g('quoting', 'spaced_operand_lines', '-1', 'quoting/with space')
    _g('quoting', 'valid_spaced_operand_lines', '-1', 'quoting/valid spaced')
    _g('quoting', 'broken_relative_lines', '-1', 'quoting/brokenrel')
    _g('quoting', 'broken_relative_d_F', '-d', '-F=always',
       'quoting/brokenrel', note='no marker on a broken relative target')
    _g('quoting', 'no_quotes', '--no-quotes', 'quoting',
       note='names, parent prefixes and broken targets use NoQuotes; control '
            'characters stay escaped and a symlink target name keeps QuoteSpaces')
    _g('quoting', 'no_quotes_lines', '-1', '--no-quotes', 'quoting')
    _g('quoting', 'no_quotes_long', *LONG, '--no-quotes', 'quoting')
    _g('quoting', 'no_quotes_classify', '-F=always', '--no-quotes', 'quoting')
    _g('quoting', 'no_quotes_mixed', '--no-quotes', 'mixed',
       note='spaced -> tar get keeps its quoted target name')
    _g('quoting', 'no_quotes_links', '--no-quotes', 'links')
    _g('quoting', 'no_quotes_tree', '-T', '--no-quotes', 'quoting')
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
    _g('classify', 'F_lines', '-1', '-F=always', 'mixed')
    _g('classify', 'F_links', '-F=always', 'links')
    _g('classify', 'F_long_links', *LONG, '-F=always', 'links')
    _g('classify', 'F_d_file', '-d', '-F=always', 'f', cwd='operands')
    _g('classify', 'F_d_dir', '-d', '-F=always', 'dir', cwd='operands')
    _g('classify', 'F_d_symlink', '-d', '-F=always', 'l', cwd='operands')
    _g('classify', 'F_d_dirlink', '-d', '-F=always', 'dl', cwd='operands')
    _g('classify', 'F_d_sub_link', '-d', '-F=always', 'sub/link',
       cwd='operands',
       note='operand parent sub/ has an executable target; the cwd decoy '
            'target is 0644, so this pins operand-parent resolution')
    _g('classify', 'F_d_sub_dirlink', '-d', '-F=always', 'sub/dirlink',
       cwd='operands',
       note='operand-parent directory target gets the / marker')
    _g('classify', 'F_d_spaced_sub_link', '-d', '-F=always', 'x y/sub/link',
       cwd='operands',
       note='spaced operand parent; `x y/sub` is quoted once and the target '
            'comes from x y/sub/target')
    _g('classify', 'F_modes', '-F=always', 'modes')
    _g('classify', 'F_tree', '-F=always', 'tree')
    _g('classify', 'F_ext', '-F=always', 'ext')
    _g('classify', 'F_hard', '-F=always', 'hard')
    _g('classify', 'F_ordered', '-F=always', 'names/ordered')
    _g('classify', 'F_d_broken', '-d', '-F=always', 'broken', cwd='operands')
    _g('classify', 'classify_never', '--classify=never', 'mixed')
    _r('classify', 'F_absolute', '-F=always', 'absolute',
       note='live-only root-parent target markers')
    _g('classify', 'F_aa_symlink_dir', '-aa', '-F=always', 'tree/dirlink',
       note='no marker on the dot target when the operand-base stat fails')
    _g('classify', 'F_aa_selflink', '-aa', '-F=always', 'selflink/d/self',
       note='the self-link dot target resolves and is marked')


def _colour_cases():
    # Non-TTY colour is deterministic with --color=always; every golden below
    # is the pinned reference's exact SGR bytes. TTY auto/NO_COLOR cases use a
    # pty and are live-only (no committed bytes, design 9.4).
    _g('colour', 'default_mixed', '--color=always', 'mixed',
       note='default palette: dir, symlink, executable, regular, FIFO, socket, '
            'broken link name/arrow/target')
    _g('colour', 'all_mixed', '--color=always', '-a', 'mixed',
       note='hidden entries use their kind/extension style; no hidden SGR')
    _g('colour', 'aa_mixed', '--color=always', '-aa', 'mixed',
       note='synthetic dot rows are directories')
    _g('colour', 'modes', '--color=always', 'modes',
       note='setuid/setgid/sticky have no separate name style; executable '
            'regular files are green and directories blue')
    _g('colour', 'links', '--color=always', 'links',
       note='valid, chained, loop and broken targets')
    _g('colour', 'categories', '--color=always', 'colours',
       note='default file-type palette: build/readme, image, video, music, '
            'lossless, document, compressed, temp, compiled, source, plus '
            'fi fallback and the no-colour regular file')
    _g('colour', 'categories_all', '--color=always', '-a', 'colours')
    _g('colour', 'operand_file', '--color=always', 'mixed/alpha',
       note='the operand parent prefix uses symlink_path (cyan)')
    _g('colour', 'operand_dir', '--color=always', 'mixed/dir')
    _g('colour', 'operand_link', '--color=always', 'mixed/dirlink')
    _g('colour', 'no_quotes', '--color=always', '--no-quotes', 'mixed')
    _g('colour', 'classify', '--color=always', '-F=always', 'mixed',
       note='classify markers are plain bits after the styled name/target')
    _g('colour', 'tree', '--color=always', '-T', 'tree',
       note='tree glyphs use punctuation; entry names use the normal palette')
    _g('colour', 'tree_all', '--color=always', '-T', '-a', 'tree')
    _g('colour', 'tree_classify', '--color=always', '-T', '-F=always', 'tree')
    _g('colour', 'tree_error', '--color=always', '-T', 'modes',
       requires='nonroot',
       note='the synthetic <Permission denied> child is broken_symlink red')
    _g('colour', 'bare', '--color', 'mixed',
       note='bare --color is auto, no colour off a TTY')
    _g('colour', 'auto', '--color=auto', 'mixed')
    _g('colour', 'automatic', '--color=automatic', 'mixed')
    _g('colour', 'never', '--color=never', 'mixed')
    _g('colour', 'colour_alias', '--colour=always', 'mixed')
    _g('colour', 'no_color_default', 'mixed', env={'NO_COLOR': '1'},
       note='NO_COLOR present sets the default to Never')
    _g('colour', 'no_color_auto', '--color=auto', 'mixed',
       env={'NO_COLOR': '1'}, note='explicit --color overrides NO_COLOR')
    _g('colour', 'no_color_always', '--color=always', 'mixed',
       env={'NO_COLOR': '1'})
    _g('colour', 'no_color_empty_always', '--color=always', 'mixed',
       env={'NO_COLOR': ''},
       note='present-empty NO_COLOR also sets the default; explicit always wins')
    _g('colour', 'ls_di', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=01;32'})
    _g('colour', 'ls_ln', '--color=always', 'mixed',
       env={'LS_COLORS': 'ln=01;35'})
    _g('colour', 'ls_ex', '--color=always', 'mixed',
       env={'LS_COLORS': 'ex=01;36'})
    _g('colour', 'ls_fi', '--color=always', 'mixed',
       env={'LS_COLORS': 'fi=01;37'},
       note='fi is the regular-file fallback, including link targets')
    _g('colour', 'ls_ext', '--color=always', 'mixed',
       env={'LS_COLORS': '*.txt=01;31'})
    _g('colour', 'ls_ext_order_a', '--color=always', 'ext',
       env={'LS_COLORS': '*.gz=01;31:*.tar.gz=01;32'},
       note='a later dotted suffix overrides an earlier simple extension')
    _g('colour', 'ls_ext_order_b', '--color=always', 'ext',
       env={'LS_COLORS': '*.tar.gz=01;32:*.gz=01;31'},
       note='definition order decides, not longest suffix')
    _g('colour', 'ls_ext_case', '--color=always', 'ext',
       env={'LS_COLORS': '*.TXT=01;31'},
       note='simple extension keys are case-sensitive')
    _g('colour', 'ls_empty_value', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=:*.txt='},
       note='an empty value is not a pair; the defaults stay')
    _g('colour', 'ls_invalid_value', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=bogus'},
       note='a non-SGR value overrides the slot with the empty style')
    _g('colour', 'ls_unknown_key', '--color=always', 'mixed',
       env={'LS_COLORS': 'zz=01;31'},
       note='unknown keys are ignored by this build (documented divergence)')
    _g('colour', 'ls_sgr_attrs', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=4;31'})
    _g('colour', 'ls_sgr_order', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=31;1'},
       note='the SGR parser re-serialises canonically: 1;31')
    _g('colour', 'ls_sgr_256', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=38;5;196'})
    _g('colour', 'ls_sgr_rekind', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=38;2;1;2;3;31'},
       note='a later basic colour must not keep stale RGB components')
    _g('colour', 'ls_ext_plain', '--color=always', 'colours',
       env={'LS_COLORS': '*.png=0'},
       note='a mapping with the empty style overrides the default category')
    _g('colour', 'eza_over_ls', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=01;34', 'EZA_COLORS': 'di=01;32'})
    _g('colour', 'exa_fallback', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=01;34', 'EXA_COLORS': 'di=01;33'})
    _g('colour', 'eza_over_exa', '--color=always', 'mixed',
       env={'EZA_COLORS': 'di=01;32', 'EXA_COLORS': 'di=01;33'})
    _g('colour', 'eza_empty_keeps_ls', '--color=always', 'mixed',
       env={'LS_COLORS': 'di=01;34', 'EZA_COLORS': ''},
       note='present-empty EZA_COLORS still displaces EXA_COLORS')
    _g('colour', 'eza_reset', '--color=always', 'colours',
       env={'EZA_COLORS': 'reset:di=01;32'},
       note='reset disables the default file-type palette, LS/fi still apply')
    _g('colour', 'ls_ln_target', '--color=always', 'mixed',
       env={'LS_COLORS': 'ln=target'},
       note='the reference parses the unsupported "target" value to the '
            'empty style; symlink names are then plain')
    # TTY/auto live cases (pty; stdout is a terminal, -1 pins the lines view
    # because grid is E2). Compared live against the reference, never recorded.
    _r('colour', 'tty_default_auto', '-1', 'mixed', mode='pty',
       note='default mode on a TTY is auto colour')
    _r('colour', 'tty_color_auto', '-1', '--color=auto', 'mixed', mode='pty')
    _r('colour', 'tty_never', '-1', '--color=never', 'mixed', mode='pty')
    _r('colour', 'tty_no_color', '-1', 'mixed', mode='pty',
       env={'NO_COLOR': '1'}, note='NO_COLOR default Never on a TTY')
    _r('colour', 'tty_no_color_auto', '-1', '--color=auto', 'mixed',
       mode='pty', env={'NO_COLOR': '1'},
       note='explicit auto overrides NO_COLOR and colours on a TTY')
    _r('colour', 'tty_bare_no_color', '-1', '--color', 'mixed', mode='pty',
       env={'NO_COLOR': '1'})
    _r('colour', 'tty_no_color_empty', '-1', 'mixed', mode='pty',
       env={'NO_COLOR': ''})
    _r('colour', 'tty_no_quotes', '-1', '--color=auto', '--no-quotes', 'mixed',
       mode='pty')


def _tree_cases():
    _g('tree', 'T', '-T', 'tree', note='names only; UTF-8 glyphs; symlink '
       'directories and broken links are not descended')
    _g('tree', 'spellout', '--tree', 'tree')
    _g('tree', 'level0', '-T', '-L', '0', 'tree')
    _g('tree', 'level1', '-T', '-L', '1', 'tree')
    _g('tree', 'level2', '-T', '-L', '2', 'tree')
    _g('tree', 'level3', '-T', '-L', '3', 'tree')
    _g('tree', 'level_plus', '-T', '-L', '+2', 'tree')
    _g('tree', 'level_zeros', '-T', '-L', '02', 'tree')
    _g('tree', 'level_last', '-T', '-L', '1', '-L', '2', 'tree')
    _g('tree', 'all', '-T', '-a', 'tree')
    _g('tree', 'all_level2', '-T', '-a', '-L', '2', 'tree')
    _g('tree', 'almost_all', '-T', '-A', 'tree')
    _g('tree', 'links', '-T', 'links')
    _g('tree', 'links_all', '-T', '-a', 'links')
    _g('tree', 'empty', '-T', 'empty', note='an empty directory prints its line')
    _g('tree', 'hidden_operand', '-T', 'tree/hidden')
    _g('tree', 'hidden_operand_a', '-T', '-a', 'tree/hidden')
    _g('tree', 'long', '-T', *LONG, 'tree',
       note='cells, then the tree prefix, then the name')
    _g('tree', 'long_level1', '-T', '-L', '1', *LONG, 'tree')
    _g('tree', 'long_modes', '-T', *LONG, 'modes', requires='nonroot',
       note='long column widths are global; the error row is padded')
    _g('tree', 'long_denied', '-T', *LONG, 'locked/denied',
       requires='nonroot')
    _g('tree', 'classify', '-T', '-F=always', 'tree')
    _g('tree', 'classify_long', '-T', '-F=always', *LONG, 'tree')
    _g('tree', 'reverse', '-T', '-r', 'tree')
    _g('tree', 'sort_size', '-T', '--sort=size', 'sizes')
    _g('tree', 'sort_ext', '-T', '--sort=extension', 'ext')
    _g('tree', 'sort_type', '-T', '--sort=type', 'mixed')
    _g('tree', 'group_first', '-T', '--group-directories-first', 'mixed')
    _g('tree', 'D', '-T', '-D', 'tree',
       note='a tree level keeps lstat directories only')
    _g('tree', 'f', '-T', '-f', 'tree',
       note='--only-files drops the directory root entirely')
    _g('tree', 'f_D', '-T', '-f', '-D', 'tree')
    _g('tree', 'f_file', '-T', '-f', 'one/f')
    _g('tree', 'D_file', '-T', '-D', 'one/f')
    _g('tree', 'd_treat', '-T', '-d', 'tree',
       note='tree wins over --treat-dirs-as-files (strict mode is off)')
    _g('tree', 'oneline_last', '-T', '-1', 'tree',
       note='the last view flag wins: this is a lines listing')
    _g('tree', 'oneline_first', '-1', '-T', 'tree')
    _g('tree', 'long_oneline_last', '-T', '-l', '-1', '--no-user', '--bytes',
       '--time-style=long-iso', 'tree')
    _g('tree', 'dot_operand', '-T', '.', cwd='tree')
    _g('tree', 'path_normalized', '-T', 'tree//d1')
    _g('tree', 'path_slash', '-T', 'tree//d1/')
    _g('tree', 'dirlink_operand', '-T', 'tree/dirlink')
    _g('tree', 'dirlink_slash_operand', '-T', 'tree/dirlink/',
       note='a trailing slash follows the operand; it is then a directory')
    _g('tree', 'file_operand', '-T', 'one/f')
    _g('tree', 'dangling_operand', '-T', 'tree/broken')
    _g('tree', 'missing_operand', '-T', 'missing')
    _g('tree', 'error_aa', '-T', '-aa', 'tree')
    _g('tree', 'error_all_all', '-T', '-a', '-a', 'tree')
    _g('tree', 'error_level', '-T', '-L', 'x', 'tree')
    _g('tree', 'error_level_neg', '-T', '-L', '-1', 'tree')
    _g('tree', 'error_level_plus', '-T', '-L', '+', 'tree',
       note='Rust usize::from_str: "+" alone is InvalidDigit, not Empty')
    _g('tree', 'error_level_huge', '-T', '-L', '18446744073709551616', 'tree')
    _g('tree', 'error_level_empty', '-T', '-L', '', 'tree')
    _g('tree', 'level_without_tree', '-L', '1', 'tree',
       note='without -T the level is never parsed (probe)')
    _g('tree', 'bad_level_without_tree', '-L', 'x', 'tree')
    _g('tree', 'oneline_aa_error', '-T', '-1', '-aa', 'tree',
       note='the two -a error is raised even when -1 wins the view')
    _g('tree', 'denied', '-T', 'locked/denied', requires='nonroot')
    _g('tree', 'permission_child', '-T', 'modes', requires='nonroot')
    _r('tree', 'reverse_size_ties', '-T', '-r', '--sort=size', 'mixed',
       note='tree -r sorts ascending then reverses the vector, so equal sizes '
            'reverse readdir order')
    _r('tree', 'reverse_name_ties', '-T', '-r', '.', cwd='names/tie_Aa',
       note='natcmpFold-equal a/A reverse readdir order in a tree')
    _r('tree', 'reverse_size_group', '-T', '-r', '--sort=size',
       '--group-directories-first', 'mixed',
       note='the vector reverse runs before the stable dirs-first pass')
    _r('tree', 'dotdot_operand', '-T', '..', cwd='tree/d1',
       note='parent listing is live/shape-only')


def _grid_cases():
    """E2 grid cases. Every grid case passes --icons=never (icon rendering is
    out of scope and terminal-ish auto icons would otherwise add glyphs) and
    sets colour explicitly when colour matters. Widths 20/40/80/120 cover the
    1/2/10/100 short and 10/100 long fixtures plus a mixed-length tree."""
    for tree in ('grid1', 'grid2', 'grid10', 'grid100', 'gridlong10',
                 'gridlong100', 'gridmix'):
        for w in (20, 40, 80, 120):
            _g('grid', tree + '_w' + str(w), '--width=' + str(w),
               '--icons=never', tree)
    _g('grid', 'across_w40_grid10', '-x', '--width=40', '--icons=never',
       'grid10')
    _g('grid', 'across_w20_grid100', '-x', '--width=20', '--icons=never',
       'grid100')
    _g('grid', 'across_w40_gridmix', '-x', '--width=40', '--icons=never',
       'gridmix')
    _g('grid', 'across_w80_gridlong10', '-x', '--width=80', '--icons=never',
       'gridlong10')
    _g('grid', 'across_columns_grid10', '-x', '--icons=never', 'grid10',
       env={'COLUMNS': '20'})
    # Explicit -G on a non-TTY has no width and falls back to 80; the view
    # rules are eza's last-of -l/-1/-T/-G with the last of -G/-T deciding a
    # long view's tree/grid-details split.
    _g('grid', 'G_fallback_grid100', '-G', '--icons=never', 'grid100')
    _g('grid', 'G_fallback_mixed', '-G', '--icons=never', 'mixed')
    _g('grid', 'G_oneline_last', '-G', '-1', '--icons=never', 'mixed',
       note='-1 last disables grid: line targets are shown')
    _g('grid', 'oneline_then_G', '-1', '-G', '--icons=never', 'mixed',
       note='-G last selects grid: no targets')
    _g('grid', 'tree_then_G', '-T', '-G', '--icons=never', 'grid10')
    _g('grid', 'G_then_tree', '-G', '-T', '--icons=never', 'grid10')
    _g('grid', 'long_oneline_G', '-l', '-G', '-1', '--icons=never', 'mixed',
       note='the last view flag is -1: a lines listing, -l ignored')
    _g('grid', 'width_long', '--width=40', *LONG, 'mixed',
       note='width is accepted and ignored by the restricted long view')
    _g('grid', 'width_tree', '--width=40', '-T', '-L', '1', 'tree')
    _g('grid', 'width1_mixed', '--width=1', '--icons=never', 'mixed',
       note='grid at width 1: one cell per line and no link targets')
    _g('grid', 'width2_gridmix', '--width=2', '--icons=never', 'gridmix')
    _g('grid', 'width0_lines', '--width=0', '--icons=never', 'mixed',
       note='--width=0 deduces TerminalWidth::Automatic: non-TTY Lines')
    _g('grid', 'width0_G', '-G', '--width=0', '--icons=never', 'grid100',
       note='explicit grid with an unknown width falls back to 80')
    _g('grid', 'width1000_grid100', '--width=1000', '--icons=never',
       'grid100')
    _g('grid', 'width_int_max', '--width=9223372036854775807',
       '--icons=never', 'grid10',
       note='above the reference usize->int boundary: this build saturates and '
            'clamps; the layout is the same one-row grid')
    # COLUMNS is a Set width (0 included) and is ignored when --width is
    # present. It makes eza terminal-ish for the grid and automatic classify,
    # but automatic colour still needs a real stdout TTY.
    _g('grid', 'columns_grid100', '--icons=never', 'grid100',
       env={'COLUMNS': '40'})
    _g('grid', 'columns_0_mixed', '--icons=never', 'mixed',
       env={'COLUMNS': '0'})
    _g('grid', 'columns_plus_grid10', '--icons=never', 'grid10',
       env={'COLUMNS': '+40'})
    _g('grid', 'columns_over_width', '--width=20', '--icons=never',
       'grid10', env={'COLUMNS': '80'})
    _g('grid', 'columns_classify', '-F', '--icons=never', 'mixed',
       env={'COLUMNS': '40'}, note='automatic classify is on for a Set width')
    _g('grid', 'columns_classify_never', '-F=never', '--icons=never',
       'mixed', env={'COLUMNS': '40'})
    _g('grid', 'columns_colour_auto', '--color=auto', '--icons=never',
       'mixed', env={'COLUMNS': '40'},
       note='--width/COLUMNS do not enable automatic colour (probe)')
    _g('grid', 'columns_oneline', '-1', '--icons=never', 'mixed',
       env={'COLUMNS': '20'})
    _g('grid', 'F_auto_width', '--width=80', '-F', '--icons=never', 'mixed')
    _g('grid', 'F_always_width', '--width=80', '-F=always', '--icons=never',
       'mixed')
    _g('grid', 'F_never_width', '--width=80', '-F=never', '--icons=never',
       'mixed')
    _g('grid', 'aa_F_width', '--width=40', '-aa', '-F', '--icons=never',
       'mixed', note='synthetic dot rows get the directory marker')
    _g('grid', 'colour_width', '--width=80', '--color=always',
       '--icons=never', 'mixed')
    _g('grid', 'colour_F_width', '--width=80', '--color=always', '-F=always',
       '--icons=never', 'mixed')
    _g('grid', 'colour_links_width', '--width=80', '--color=always',
       '--icons=never', 'links',
       note='grid broken links are red; no arrow or target')
    _g('grid', 'colour_aa_width', '--width=40', '--color=always', '-aa',
       '--icons=never', 'mixed')
    _g('grid', 'colour_auto_width', '--width=80', '--color=auto',
       '--icons=never', 'mixed')
    _g('grid', 'colour_never_width', '--width=80', '--color=never',
       '--icons=never', 'mixed')
    _g('grid', 'colour_LS_width', '--width=80', '--color=always',
       '--icons=never', 'mixed', env={'LS_COLORS': 'di=01;32:ln=01;35'})
    _g('grid', 'quoting_width', '--width=80', '--icons=never', 'quoting')
    _g('grid', 'quoting_F_width', '--width=80', '-F=always', '--icons=never',
       'quoting')
    _g('grid', 'quoting_noquotes_width', '--width=80', '--no-quotes',
       '--icons=never', 'quoting')
    _g('grid', 'operand_file', '--width=80', '--icons=never', 'mixed/alpha')
    _g('grid', 'operand_spaced', '--width=80', '--icons=never',
       'mixed/tar get')
    _g('grid', 'operand_dir_d_F', '--width=80', '-d', '-F=always',
       '--icons=never', 'mixed/dir')
    _g('grid', 'operand_link', '--width=80', '--icons=never',
       'links/dangling', note='a symlink operand grids its name only')
    _g('grid', 'operand_link_F', '--width=80', '-F=always', '--icons=never',
       'links/dangling')
    _g('grid', 'operand_link_colour', '--width=80', '--color=always',
       '--icons=never', 'links/dangling')
    _g('grid', 'operand_dirlink_colour', '--width=80', '--color=always',
       '--icons=never', 'links/realdirlink')
    _g('grid', 'operand_dirlink_d_F', '--width=80', '-d', '-F=always',
       '--icons=never', 'links/realdirlink')
    _g('grid', 'reverse_grid10', '--width=20', '-r', '--icons=never',
       'grid10')
    _g('grid', 'sort_size', '--width=20', '--sort=size', '--icons=never',
       'sizes')
    _g('grid', 'sort_ext', '--width=30', '--sort=extension', '--icons=never',
       'ext')
    _g('grid', 'group_first', '--width=30', '--group-directories-first',
       '--icons=never', 'mixed')
    _g('grid', 'D_mixed', '--width=40', '-D', '--icons=never', 'mixed')
    _g('grid', 'f_mixed', '--width=40', '-f', '--icons=never', 'mixed')
    _g('grid', 'a_mixed', '--width=40', '-a', '--icons=never', 'mixed')
    _g('grid', 'aa_mixed', '--width=40', '-aa', '--icons=never', 'mixed')
    _g('grid', 'empty', '--width=40', '--icons=never', 'empty')
    _g('grid', 'empty_G', '-G', '--icons=never', 'empty')
    # Real-TTY grid lives on a raw pty with a pinned TIOCSWINSZ; these are
    # live-only (design 9.4) and the harness re-runs both sides once when a
    # live pair differs.
    _r('grid', 'tty_grid_80', '--icons=never', 'grid10', mode='pty', cols=80)
    _r('grid', 'tty_grid_40', '--icons=never', 'grid10', mode='pty', cols=40)
    _r('grid', 'tty_grid_20', '--icons=never', 'grid10', mode='pty', cols=20)
    _r('grid', 'tty_grid_120', '--icons=never', 'grid100', mode='pty',
       cols=120)
    _r('grid', 'tty_gridmix_40', '--icons=never', 'gridmix', mode='pty',
       cols=40)
    _r('grid', 'tty_mixed_auto', '--icons=never', 'mixed', mode='pty',
       cols=80, note='auto colour on a real terminal, no explicit flag')
    _r('grid', 'tty_mixed_F', '-F', '--icons=never', 'mixed', mode='pty',
       cols=80, note='automatic classify on a real terminal')
    _r('grid', 'tty_mixed_never', '--color=never', '--icons=never', 'mixed',
       mode='pty', cols=80)
    _r('grid', 'tty_columns_20', '--icons=never', 'grid10', mode='pty',
       cols=80, env={'COLUMNS': '20'})
    _r('grid', 'tty_width_40', '--width=40', '--icons=never', 'grid10',
       mode='pty', cols=80)
    _r('grid', 'tty_width_0', '--width=0', '--icons=never', 'grid10',
       mode='pty', cols=40)
    _r('grid', 'tty_oneline', '-1', '--icons=never', 'grid10', mode='pty',
       cols=40)
    _r('grid', 'tty_zero_cols', '--icons=never', 'mixed', mode='pty', cols=0,
       note='a 0x24 pty is not terminal-ish in eza (terminal_size is None): '
            'Lines mode, colour auto, no classify')
    _r('grid', 'tty_utf8_20', '--icons=never', 'utf8', mode='pty', cols=20)


def _utf8_cases():
    """E2 UTF-8 names, lossy invalid-UTF-8 names and simple-lowercase folds.
    The valid-UTF-8 and invalid-byte fixtures have pairwise comparator-distinct
    lossy names, so their goldens do not depend on readdir order; the tie trees
    (fold pairs, Unicode whitespace) are live-only."""
    _g('utf8', 'default', '--icons=never', 'utf8')
    _g('utf8', 'Name', '--sort=Name', '--icons=never', 'utf8')
    _g('utf8', 'name', '--sort=name', '--icons=never', 'utf8')
    _g('utf8', 'reverse', '-r', '--icons=never', 'utf8')
    _g('utf8', 'lines', '-1', '--icons=never', 'utf8')
    for w in (10, 12, 20, 40, 80):
        _g('utf8', 'grid_' + str(w), '--width=' + str(w), '--icons=never',
           'utf8')
    _g('utf8', 'grid_across_20', '-x', '--width=20', '--icons=never', 'utf8')
    _g('utf8', 'grid_F_20', '--width=20', '-F=always', '--icons=never',
       'utf8')
    _g('utf8', 'grid_colour_20', '--width=20', '--color=always',
       '--icons=never', 'utf8')
    _g('utf8', 'columns_12', '--icons=never', 'utf8',
       env={'COLUMNS': '12'})
    _g('utf8', 'fold_Name', '--sort=Name', '--icons=never', 'utf8fold')
    _g('utf8', 'tie_Name', '--sort=Name', '--icons=never', 'utf8tie')
    _r('utf8', 'tie_default', '--icons=never', 'utf8tie',
       note='fold ties E/e-acute, A/a and A-grave/a-grave: readdir order')
    _r('utf8', 'fold_default', '--icons=never', 'utf8fold',
       note='sigma/Cyrillic/fullwidth fold ties: readdir order')
    _r('utf8', 'ws_default', '--icons=never', 'utf8ws',
       note='Unicode whitespace ties: readdir order')
    _r('utf8', 'ws_Name', '--sort=Name', '--icons=never', 'utf8ws',
       note='whitespace is skipped by both comparators: readdir order')
    _g('utf8', 'bad_default', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_Name', '--sort=Name', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_lines', '-1', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_long', *LONG, '--icons=never', 'utf8bad')
    _g('utf8', 'bad_grid_20', '--width=20', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_grid_40', '--width=40', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_F', '-F=always', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_colour', '--color=always', '--icons=never', 'utf8bad')
    _g('utf8', 'bad_colour_grid', '--width=40', '--color=always',
       '--icons=never', 'utf8bad')
    _g('utf8', 'bad_aa', '-aa', '--icons=never', 'utf8bad')
    # a raw-byte operand is looked up by its bytes and displayed lossy
    _g('utf8', 'bad_operand', b'utf8bad/a-\xff')
    _g('utf8', 'bad_operand_long', *LONG, b'utf8bad/a-\xff')
    _g('utf8', 'bad_operand_dir_d', '-d', b'utf8bad/sub-\xff')
    _g('utf8', 'bad_reverse', '-r', '--icons=never', 'utf8bad')


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
    _g('dirs', 'd_sub_dotdot', '-d', 'sub/..', cwd='operands',
       note='a .. operand keeps its parent prefix (probe b1)')
    _g('dirs', 'd_spaced_dotdot', '-d', 'x y/..', cwd='operands')
    _g('dirs', 'd_dot', '-d', '.', cwd='operands')
    _g('dirs', 'd_dot_dotdot', '-d', './..', cwd='operands')
    _g('dirs', 'd_sub_dotdot_slash', '-d', 'sub/../', cwd='operands')
    _g('dirs', 'dangling_operand', 'links/dangling',
       note='lstat succeeds on a dangling symlink operand')
    _g('dirs', 'dangling_operand_d', '-d', 'links/dangling')
    _g('dirs', 'aa_symlink_dir_realdir_slash', '-aa',
       'links/realdirlink/',
       note='a trailing slash follows the operand before the dot rows')
    _r('dirs', 'd_dotdot_parent', '-d', '..', cwd='operands',
       note='-d .. is a live display-only case')
    _r('dirs', 'd_root_dotdot', '-d', '/..', note='live display-only')
    _r('dirs', 'd_root_dotdot_dotdot', '-d', '/../..',
       note='live display-only')
    _r('dirs', 'd_tmp_dotdot', '-d', '/tmp/..', note='live display-only')
    _r('dirs', 'd_slash_slash', '-d', '//', note='live display-only')
    _r('dirs', 'd_slash_slash_dotdot', '-d', '//..',
       note='live display-only')


def _operand_cases():
    _g('operands', 'file', 'one/f')
    _g('operands', 'dash_operand', '--', '-', cwd='mixed')
    _g('operands', 'dashdash_name', '--', '--x', cwd='mixed')
    _g('operands', 'hidden_explicit', 'mixed/.hidden')
    _g('operands', 'directory', 'mixed/dir')
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
    _g('errors', 'bad_color_quote', '--color=a"b', 'mixed',
       note='the reference Debug-escapes the displayed option value')
    _g('errors', 'bad_color_control', '--color=a\tb', 'mixed')
    _g('errors', 'bad_level_quote', '-T', '-L', 'a"b', 'tree')
    _g('errors', 'bad_icons', '--icons=bogus', 'mixed')
    _o('errors', 'bad_time_style', '--time-style=bogus', 'mixed',
       note='divergence: the reference accepts and ignores a bad --time-style '
            'in a non-long view; E0 rejects it (own usage error, exit 3)')
    _g('errors', 'bad_level', '-T', '-L', 'x', 'tree',
       note='E1 parses the level only when the tree action is active and '
            'matches the reference numeric message')
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
       note='this build does not implement --sort=none')
    _o('errors', 'own_unsupported_flag', '--git', 'mixed',
       note='this build rejects out-of-scope flags')
    _o('errors', 'own_multiple_operands', 'one', 'one',
       note='this build supports at most one operand')
    _g('errors', 'width_bad', '--width=x', 'mixed')
    _g('errors', 'width_negative', '--width=-1', 'mixed')
    _g('errors', 'width_empty_equals', '--width=', 'mixed')
    _g('errors', 'width_double_equals', '--width==', 'mixed')
    _g('errors', 'width_too_large', '--width=18446744073709551616', 'mixed')
    _g('errors', 'width_empty_value', '--width', '', 'mixed')
    _g('errors', 'columns_bad', 'mixed', env={'COLUMNS': 'x'})
    _g('errors', 'columns_empty', 'mixed', env={'COLUMNS': ''})
    _g('errors', 'columns_negative', 'mixed', env={'COLUMNS': '-1'})
    _g('errors', 'columns_too_large', 'mixed',
       env={'COLUMNS': '18446744073709551616'})
    _o('errors', 'own_grid_details', '-l', '-G', '--no-user', '--bytes',
       '--time-style=long-iso', 'mixed',
       note='E2 rejects eza GridDetails (-l with -G) with this build\'s '
            'own message')
    _o('errors', 'own_grid_details2', '-G', '-l', '--no-user', '--bytes',
       '--time-style=long-iso', 'mixed')
    _o('errors', 'own_sort_dot_name', '--sort=.name', 'mixed')
    _o('errors', 'own_sort_inode', '--sort=inode', 'mixed')
    _o('errors', 'own_sort_changed', '--sort=changed', 'mixed')
    _o('errors', 'own_icons_always', '--icons=always', 'mixed')
    _o('errors', 'own_stdin', '--stdin', 'mixed')


def _memory_cases():
    _o('memory', 'long_10000', '-l', '--no-user', '--bytes',
       '--time-style=long-iso', 'many10000', slow=True, memory_entries=10000,
       note='restricted long over 10,000 entries under OBO_STATS; '
            'EZA_PEAK_MAX and no leak line')
    _o('memory', 'names_10000', 'many10000', slow=True,
       memory_entries=10000, note='name-only 10,000 entries under '
            'OBO_STATS; EZA_PEAK_MAX and no leak line')
    _o('memory', 'tree_10000', '-T', 'many10000', slow=True,
       memory_entries=10000, note='E1 tree over 10,000 entries under '
            'OBO_STATS; EZA_PEAK_MAX and no leak line')
    _o('memory', 'colour_10000', '--color=always', 'many10000', slow=True,
       memory_entries=10000, note='E1 colour over 10,000 entries under '
            'OBO_STATS; EZA_PEAK_MAX and no leak line')
    _o('memory', 'grid_10000', '--width=80', '--icons=never', 'many10000',
       slow=True, memory_entries=10000, note='E2 grid over 10,000 entries '
            'under OBO_STATS; EZA_PEAK_MAX and no leak line')


for _builder in (_default_cases, _name_cases, _sortkey_cases, _long_cases,
                 _quoting_cases, _classify_cases, _colour_cases, _tree_cases,
                 _grid_cases, _utf8_cases, _dir_cases, _operand_cases,
                 _error_cases, _memory_cases):
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
    'utf8bad': ('invalid_utf8',),
}


def implied_requires(case):
    needed = set(case.requires)
    texts = [case.cwd] + [a for a in case.argv if isinstance(a, str)]
    for tree, keys in TREE_CAPS.items():
        prefix = tree + '/'
        if any(text == tree or text.startswith(prefix) for text in texts):
            needed.update(keys)
    return needed


def uses_long_view(case):
    """True when the case can select the long view.

    Matches `--long` and short clusters containing `l` (`-l`, `-al`,
    `-lF=always`).  `-L` is the tree level and does not count.  This is the
    guard for host-dependent long cells (ambient xattrs, `@` marker).
    """
    for arg in case.argv:
        if not isinstance(arg, str):
            continue
        if arg == '--long':
            return True
        if arg.startswith('--') or not arg.startswith('-') or len(arg) < 2:
            continue
        body = arg[1:].split('=', 1)[0]
        # -F takes an optional WHEN value that can be attached (`-Falways`);
        # everything after F is that value, not a flag cluster.
        cut = body.find('F')
        if cut != -1:
            body = body[:cut + 1]
        if 'l' in body:
            return True
    return False


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


def absolute_path_token(line):
    """The first whitespace-delimited token that names an absolute path.

    A token is absolute when, after any leading quote characters, it starts
    with `/`.  This rejects `-> /tmp` and `'/tar get'` in addition to a line
    that itself starts with `/`, so a golden cannot commit host paths after
    arrows or inside quotes.
    """
    for token in line.split():
        if token.lstrip(b'"\'').startswith(b'/'):
            return token
    return None


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
            token = absolute_path_token(line)
            if token is not None:
                raise SetupError('malformed golden %s/%s: %s line %d contains '
                                 'an absolute path token %r'
                                 % (kind, case.key, stream, i + 1, token))
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


_PTY_OK = None


def pty_available():
    """True when this host can create a pseudo-terminal for the live TTY
    colour cases; a failure skips those cases (never passes or fails them)."""
    global _PTY_OK
    if _PTY_OK is None:
        try:
            master, slave = pty.openpty()
            os.close(master)
            os.close(slave)
            _PTY_OK = True
        except OSError:
            _PTY_OK = False
    return _PTY_OK


def run_proc_pty(argv, cwd, io_dir, env, executable=None, timeout=10.0,
                 tag='out', cols=80):
    """Run with stdout on a raw pseudo-terminal (80x24) and return the bytes
    unchanged. Used only by the live auto-colour cases: isatty(stdout) is the
    behaviour under test, and no golden is recorded from a pty."""
    err_path = os.path.join(io_dir, '.eza-' + tag + '.stderr')
    master, slave = pty.openpty()
    ferr = open(err_path, 'wb')
    timed_out = False
    overrun = False
    p = None
    chunks = []
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack('HHHH', 24, cols, 0, 0))
        tty.setraw(slave)
        p = subprocess.Popen(argv, executable=executable, cwd=cwd, env=env,
                             stdin=subprocess.DEVNULL, stdout=slave,
                             stderr=ferr, start_new_session=True,
                             preexec_fn=_limit)
        os.close(slave)
        slave = None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                timed_out = True
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) >= FSIZE:
                overrun = True
                break
        if timed_out or overrun:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        p.wait()
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)
        ferr.close()
    stdout = b''.join(chunks)
    with open(err_path, 'rb') as f:
        stderr = f.read()
    rc = p.returncode if p is not None else None
    if not timed_out and (len(stdout) >= FSIZE or len(stderr) >= FSIZE
                          or rc == -signal.SIGXFSZ):
        overrun = True
    return RunResult(rc, stdout, stderr, timed_out, overrun)


def run_proc(argv, cwd, io_dir, env, executable=None, sink=False, pipe=False,
             timeout=10.0, tag='out', pseudo=False, cols=80):
    if pseudo:
        if sink or pipe:
            raise SetupError('pty and sink/pipe modes are exclusive')
        return run_proc_pty(argv, cwd, io_dir, env, executable=executable,
                            timeout=timeout, tag=tag, cols=cols)
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


def measure_rss(argv, cwd, env, out_path, err_path, tag_path):
    """Run argv and return the child's max RSS in KiB, or None.

    GNU time uses wait4 on the child, so the number is the child's own peak
    (a fork from the Python harness would include the harness's inherited
    pages in the child's max RSS). This is a measurement for the P4 record,
    never a gate; a host without /usr/bin/time simply prints no RSS.
    """
    if not os.path.exists('/usr/bin/time'):
        return None
    try:
        with open(out_path, 'wb') as out, open(err_path, 'wb') as err:
            p = subprocess.run(['/usr/bin/time', '-f', '%M', '-o', tag_path]
                               + argv, cwd=cwd, env=env,
                               stdin=subprocess.DEVNULL, stdout=out,
                               stderr=err, timeout=120)
        if p.returncode != 0:
            return None
        with open(tag_path, encoding='ascii', errors='replace') as f:
            lines = [x.strip() for x in f if x.strip()]
        if not lines or not lines[-1].isdigit():
            return None
        return int(lines[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError):
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

    def check_reference_flags(self):
        """Design 3.1: the pinned reference must accept the E0 required flags.

        The VERSION pin makes this redundant in theory; the probe exists so a
        wrongly installed reference (B8, e.g. eza 0.18 without
        --treat-dirs-as-files) fails as a setup error rather than a puzzle of
        failing cases.
        """
        if not self.args.require_reference:
            return
        base = tempfile.mkdtemp(prefix='flags-', dir=self.tmp)
        try:
            env = probe_env(base)
            try:
                p = subprocess.run(
                    [EZA, '-l', '--no-user', '--bytes',
                     '--time-style=long-iso', '--treat-dirs-as-files', '.'],
                    executable=self.ref.path, env=env, cwd=base,
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise SetupError('required flags probe failed to run: %s' % e)
        finally:
            fixtures.rmtree(base)
        if p.returncode != 0:
            raise SetupError(
                'required flags probe failed (exit %d): the pinned reference '
                'does not accept -l --no-user --bytes --time-style=long-iso '
                '--treat-dirs-as-files: %r' % (p.returncode, p.stderr))

    def build(self):
        """P4 builds `eza` through build_tools.build(['eza'], ...).

        The build lives in a private temporary output directory with its own
        `sym/`, exactly as tests/tools.py builds the other tools; the compiler
        and runtime are the tree's own (or $OBO)."""
        obo = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
        out = os.path.join(self.tmp, 'build')
        try:
            exes = build_tools.build(['eza'], out, obo, FORK)
        except build_tools.BuildError as e:
            raise SetupError('eza build failed: %s' % e)
        if len(exes) != 1:
            raise SetupError('build_tools.build returned %d binaries for one '
                             'name' % len(exes))
        self.exe = exes[0]

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
        if case.mode == 'pty' and not pty_available():
            return 'a pseudo-terminal is unavailable on this host'
        if GOLDEN_TAG in case.tags and caps.get('ambient_xattr') \
                and uses_long_view(case):
            return ('ambient xattrs present; the reference would print the '
                    'permission @ marker on a long-view golden')
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
                       pseudo=case.mode == 'pty', cols=case.cols,
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
            return ('skip', 'own eza source absent (P0 fallback)')
        if self.ref.kind != 'pinned':
            return ('skip', 'own eza source absent; no pinned reference '
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
                            pipe=case.mode == 'pipe',
                            pseudo=case.mode == 'pty', timeout=timeout,
                            tag='our', cols=case.cols)
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
            ref_run = None
            if REF in case.tags and self.ref.kind == 'pinned':
                ref_run = run_proc(argv, cwd, base, env,
                                   executable=self.ref.path,
                                   sink=case.mode == 'sink',
                                   pipe=case.mode == 'pipe',
                                   pseudo=case.mode == 'pty',
                                   timeout=timeout, tag='ref', cols=case.cols)
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
            if problems and case.live_only and ref_run is not None:
                # A live-only case has no committed bytes; the two programs
                # run sequentially, so a changing host directory (/tmp, /)
                # can make an otherwise correct pair differ. Re-run both once:
                # only if a program's own output changed between attempts is
                # the host demonstrably unstable, and then the case is a skip
                # (never a pass and never a weakened comparison). A stable
                # difference remains a failure.
                ours2 = run_proc(argv, cwd, base, ours_env,
                                 executable=self.exe,
                                 sink=case.mode == 'sink',
                                 pipe=case.mode == 'pipe',
                                 pseudo=case.mode == 'pty',
                                 timeout=timeout, tag='our2', cols=case.cols)
                ours2.stdout = normalize_paths(ours2.stdout, root, base)
                ours2.stderr = normalize_paths(ours2.stderr, root, base)
                ref2 = run_proc(argv, cwd, base, env,
                                executable=self.ref.path,
                                sink=case.mode == 'sink',
                                pipe=case.mode == 'pipe',
                                pseudo=case.mode == 'pty',
                                timeout=timeout, tag='ref2', cols=case.cols)
                ref2.stdout = normalize_paths(ref2.stdout, root, base)
                ref2.stderr = normalize_paths(ref2.stderr, root, base)
                def sig(r):
                    return (r.rc, r.stdout, r.stderr, r.timed_out, r.overrun)
                if sig(ours2) != sig(ours) or sig(ref2) != sig(ref_run):
                    return ('skip', 'live case unstable: the host listing '
                                    'changed between runs')
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
        """The design 9.7 memory gate plus a reference measurement.

        Build eza, run the two 10,000-entry cases (restricted long and
        name-only) under OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison, parse the one
        `obo: N peak bytes, M allocated` line, reject any leak/double-close
        line or nonzero exit, and enforce the measured EZA_PEAK_MAX. The
        reference RSS and both binary sizes are printed for comparison and are
        deliberately not gated (the reference is a different implementation).
        """
        if not own_available():
            raise SetupError('eza source absent; the memory gate needs cmd/eza.obx')
        self.build()
        numbers = []
        cases = self.selection(memory=True)
        if not cases:
            raise SetupError('no memory cases selected')
        for case in cases:
            base, root, caps = self.prepare_case(case)
            try:
                env = self.case_env(base, case)
                env.update({'OBO_CHECK': '1', 'OBX_GC': 'poison',
                            'OBO_STATS': '1'})
                argv = [EZA] + self.expand_argv(case, root)
                res = run_proc(argv, self.run_cwd(root, case), base, env,
                               executable=self.exe, timeout=120, tag='mem')
                lines = res.stderr.split(b'\n')
                pattern = rb'^obo: (\d+) peak bytes, (\d+) allocated$'
                stats = [x for x in lines if re.match(pattern, x)]
                other = [x for x in lines
                         if x.startswith(b'obo: ') or x.startswith(b'obx: ')]
                if res.timed_out:
                    self.failures.append((case, 'timed out'))
                    continue
                if res.rc != 0:
                    self.failures.append(
                        (case, 'exit %s (a leak/double close exits 5/8/9)'
                         % res.rc))
                    continue
                if len(stats) != 1 or len(other) != 1:
                    self.failures.append(
                        (case, 'stats line missing or duplicated '
                         '(leak/double close?): %r' % res.stderr[-400:]))
                    continue
                m = re.match(pattern, stats[0])
                peak, alloc = int(m.group(1)), int(m.group(2))
                if peak > EZA_PEAK_MAX:
                    self.failures.append(
                        (case, 'peak %d > EZA_PEAK_MAX %d live heap bytes'
                         % (peak, EZA_PEAK_MAX)))
                    continue
                numbers.append((case.key, peak, alloc))
                self.passed += 1
                print('ok   %s: %d peak bytes, %d allocated (EZA_PEAK_MAX %d)'
                      % (case.key, peak, alloc, EZA_PEAK_MAX))
                # Measurement only: our own max RSS, then the pinned
                # reference over the same tree.
                our_argv = [self.exe] + self.expand_argv(case, root)
                rss = measure_rss(our_argv, self.run_cwd(root, case), env,
                                  os.path.join(base, '.our.stdout'),
                                  os.path.join(base, '.our.stderr'),
                                  os.path.join(base, '.our.rss'))
                if rss is not None:
                    print('     eza max RSS %d KiB' % rss)
                if self.ref is not None and self.ref.kind == 'pinned':
                    ref_env = self.case_env(base, case)
                    ref_argv = [self.ref.path] + self.expand_argv(case, root)
                    rss = measure_rss(ref_argv, self.run_cwd(root, case),
                                      ref_env,
                                      os.path.join(base, '.ref.stdout'),
                                      os.path.join(base, '.ref.stderr'),
                                      os.path.join(base, '.ref.rss'))
                    if rss is not None:
                        print('     reference %s: max RSS %d KiB'
                              % (case.key, rss))
            finally:
                fixtures.rmtree(base)
        if self.exe is not None:
            print('eza binary size: %d bytes' % os.path.getsize(self.exe))
        if self.ref is not None and self.ref.kind == 'pinned':
            try:
                print('reference binary size: %d bytes'
                      % os.path.getsize(self.ref.path))
            except OSError:
                pass
        return numbers

    # -- record
    def leak_refusal(self, data, base, root, case, stream):
        for i, line in enumerate(data.split(b'\n')):
            token = absolute_path_token(line)
            if token is not None:
                raise SetupError(
                    '%s: refusing to record %s line %d containing an absolute '
                    'path token %r' % (case.key, stream, i + 1, token))
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
            if res.timed_out:
                raise SetupError('%s: reference timed out; not recording a '
                                 'golden' % case.key)
            if res.overrun:
                raise SetupError('%s: reference output overrun (>16 MiB); not '
                                 'recording a golden' % case.key)
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

    def write_manifest(self, recorded, skipped=()):
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
            'skipped': sorted('ref/' + key for key in skipped),
            'note': 'P4: reference and own goldens; per-case host metadata is not recorded',
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write('\n')

    def check_manifest(self):
        path = os.path.join(GOLDEN, 'ref', 'MANIFEST')
        if not os.path.exists(path):
            raise SetupError('MANIFEST is missing under %s; run --record to '
                             'regenerate the golden matrix' % GOLDEN)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise SetupError('MANIFEST is unreadable or malformed: %s' % e)
        if not isinstance(data, dict):
            raise SetupError('MANIFEST is malformed: expected a JSON object')
        full = {'ref/' + c.key for c in CASES if GOLDEN_TAG in c.tags}
        listed = set(data.get('cases', [])) | set(data.get('skipped', []))
        listed = {key for key in listed if key.startswith('ref/')}
        if listed != full:
            raise SetupError(
                'MANIFEST does not match the golden-tagged case set '
                '(%d listed, %d expected); re-run --record'
                % (len(listed), len(full)))
        recorded = data.get('reference', {})
        if not isinstance(recorded, dict):
            raise SetupError('MANIFEST reference entry is malformed')
        if self.ref.kind == 'pinned' and recorded.get('sha256') \
                and recorded['sha256'] != self.ref.sha256:
            self.notes.append('manifest reference sha256 differs from the '
                              'current reference')
        host = data.get('host', {})
        if not isinstance(host, dict):
            raise SetupError('MANIFEST host entry is malformed')
        if host.get('euid') is not None and host['euid'] != os.geteuid():
            self.notes.append('manifest euid differs from the current euid')

    def record(self):
        if self.ref.kind != 'pinned':
            raise SetupError('--record requires the pinned reference: '
                             + self.ref.reason)
        if self.args.k:
            raise SetupError(
                '--record with -k would write a partial matrix; the manifest '
                'must describe the full golden selection, so record without -k')
        cases = [c for c in self.selection(memory=False)
                 if GOLDEN_TAG in c.tags or OWN in c.tags]
        if not cases:
            raise SetupError('no recordable cases selected')
        ref_cases = [c for c in cases if GOLDEN_TAG in c.tags]
        own_cases = [c for c in cases if OWN in c.tags]
        if own_cases and not own_available():
            print('own eza source absent; skipping %d own goldens'
                  % len(own_cases))
            own_cases = []
        if own_cases:
            self.build()
        recorded = []
        ref_recorded = []
        ref_skipped = []
        for case in ref_cases:
            if self.record_case('ref', case, self.ref.path):
                recorded.append('ref/' + case.key)
                ref_recorded.append(case.key)
            else:
                ref_skipped.append(case.key)
        for case in own_cases:
            if self.record_case('own', case, self.exe):
                recorded.append('own/' + case.key)
        if ref_cases:
            expected = {'ref/' + c.key for c in ref_cases}
            got = ({'ref/' + key for key in ref_recorded}
                   | {'ref/' + key for key in ref_skipped})
            if got != expected:
                raise SetupError(
                    'recording did not cover the golden-tagged selection '
                    '(%d of %d cases); manifest not written'
                    % (len(got), len(expected)))
            self.write_manifest(recorded, ref_skipped)
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
        description='Differential gate for eza (E0, E1 and E2)')
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
        h.check_reference_flags()
        if not own_available() and h.ref.kind != 'pinned':
            raise SetupError(
                'no own eza build and no usable reference: nothing to run '
                '(%s)' % h.ref.reason)
        if a.record:
            h.record()
            return 0
        if a.memory:
            h.run_memory()
            for case, detail in h.failures:
                print('FAIL %s' % case.key)
                for d in (detail if isinstance(detail, list) else [detail]):
                    print('     ' + d)
            print('memory: %d passed, %d failed'
                  % (h.passed, len(h.failures)))
            return 1 if h.failures else 0
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
