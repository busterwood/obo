# obo command-line tools

The tools written in obo: `cat`, `wc`, `sort` and the `eza` E1 core (P4 E0
plus P5b colour, tree and `--no-quotes`). Each
source file here is a module root that imports `../lib/...` modules by explicit
relative path; every library name is qualified (`io::Writer`, `io::newWriter`,
`fmt::writePadded`, `errors::text`, ...). `cat`, `wc` and `sort` are headerless
roots; `eza.obx` declares `module eza` and imports all ten library modules.
`tools/build_tools.py` compiles each root directly with the ordinary compiler,
dependency-first, and nothing is concatenated. The builder creates a symbol
directory under its output (`build/tools/sym` for `make tools`, `build/eza/sym`
for `make eza`): symbol writes truncate in place, so concurrent builds must use
distinct output directories.

```
cd obo
make tools                 # build build/tools/{cat,wc,sort} and build/tools/sym
make eza                   # build build/eza/eza and build/eza/sym
python3 tools/build_tools.py [cat] [--out DIR] [--obo PATH] [--rt DIR]
python3 tests/tools.py --require-reference   # the cat/wc/sort differential gate
python3 tests/tools.py --memory
python3 tests/eza.py --require-reference     # the eza E0+E1 differential gate
python3 tests/eza.py --memory
```

The library modules are `lib/io.obx` (`Reader`, `Writer`, the buffered functions,
`writeByte` and `isTty`), `lib/strings.obx` (byte classification, `cmp`, the
split/join and natord/quote helpers), `lib/fmt.obx` (`digits`, `appendPadded`,
`writePadded`, `modeString` for the eza long view), `lib/errors.obx` (the
C-locale errno `text`, `osError`, `debugPath`, `report` and the eza-shaped
`EXIT_*` constants) and `lib/env.obx` (`get`/`has`/`value`/`getOr` over the
P5a getenv builtin). They stay in `lib/`, and the tools live in `cmd/`; P2 moved
`writePadded` out of `io` and the digit/padding helpers out of `strings`, and
the four P1 compatibility functions no longer exist.
The test programs `tests/run/io_reader.obx`, `tests/run/io_writer.obx` and
`tests/lib/*.obx` import the same modules (`tests/lib/write_padded.obx` imports
`fmt` too) and are compiled with `-I lib`.

The subset is deliberate and small. Options may be clustered (`-nET`, `-lwc`,
`-rnu`) and may appear before or after operands; `--` ends option parsing. A lone
`-` is always an operand meaning stdin (so a file literally named `-` cannot be
reached), and a file whose name begins with `-` needs `--`. An unknown short
option prints one line, `tool: invalid option -- 'X'`; an unknown long option
prints `tool: unrecognized option '--foo'`. There is no `Try 'tool --help'` line,
because `--help` and `--version` are themselves unsupported (they are
unrecognized long options). Usage errors exit 1 for `cat`/`wc` and 2 for `sort`.

## cat

```
cat [-nbsET] [--] [FILE ...]
```

Copies its operands in order to standard output with no separators; no operands
(or `-`) reads standard input. The logical stream spans operands: numbering, the
`-s` squeeze state and a line without a final newline continue from one file to
the next.

| flag | effect |
|---|---|
| `-n` | number every output line `%6d\t`, from 1 |
| `-b` | number only nonblank lines; overrides `-n` in either order |
| `-s` | never emit more than one consecutive blank line (squeezed lines are not numbered) |
| `-E` | write `$` before each newline that is emitted (none after a final line without one); a CR immediately before an emitted newline is held and written as `^M`, so CRLF renders `^M$` |
| `-T` | write a tab as `^I` (no other control characters are transformed; a CR is left alone unless `-E` is also present) |

With `-E`, the CR hold spans Reader chunks and operands: a file ending in CR
followed by a file beginning with LF renders one `^M$`, a CR at the 64 KiB chunk
boundary followed by LF does the same, and a final unpaired CR is written
literally at stream EOF (`a\r` stays `a\r`, with no `$`). A second CR can become
the new pending one (`a\r\r\n` is `a\r^M$`). `-E` and every cluster containing
it (`-nE`, `-bE`, `-sE`, `-TE`, ...) follow the rule.

A blank line has no bytes before its newline: a line of spaces, a tab, a NUL or a
CR is nonblank (a CR-only line is numbered by `-n`/`-b` and never squeezed). The
number width is a minimum; line 1,000,001 prints `1000001\t`.

Errors: an open or read error prints `cat: <operand>: <text>`, sets the failure
and continues with the next operand (a directory opens and fails on read with
`Is a directory`). A write error prints `cat: write error: <text>` once, stops
immediately and never opens a later operand. Exit 1 if anything failed.
Deliberately absent: `-v`, `-e`, `-t`, `-A`, `-u`, long options, `--help`,
`--version`.

## wc

```
wc [-lwc] [--] [FILE ...]
```

Counts each operand independently and prints the selected columns in the fixed
order lines, words, bytes, with no selection meaning all three. No operands (or
`-`) reads standard input. Word state does not cross operands; a final line
without a newline is not a line.

The word rule is GNU's C-locale rule: whitespace (`0x20`, `0x09..0x0D`) ends a
word, a printable ASCII byte (`0x21..0x7E`) starts or continues one, and every
other byte (NUL, the other controls, `0x7F..0xFF`) is ignored, neither starting
nor ending a word. So `a\0b` is one word, a zero-filled file has no words, and
`0xFF` never makes a word.

Width: when padding applies, the width is
`max(7 if any operand's size is unknown, digits(bytes counted on non-stdin
operands))`; one output line with one selected column is unpadded. "Unknown" is
stdin and a directory (read error `EISDIR`). stdin bytes never enter the sum.
The total line prints when there is more than one operand (an explicit `-`
counts), even if every operand failed (`wc -l nope1 nope2` prints `0 total`);
a missing operand produces no output line. Errors print `wc: <operand>: <text>`,
continue, and exit 1; a write error prints `wc: write error` and exits 1.

Caveats, because phase 2 has no `stat`: GNU uses file metadata for the width.
Our output is identical for regular readable files and the cases above, but
differs in three documented situations: an unreadable regular file (GNU counts
its size, we cannot), a readable non-regular file such as `/dev/null` in a
padded form (GNU uses the 7 floor), and stdin redirected from a regular file
(ours uses 7, GNU the file's size). The differential gate keeps these out of
padded cases (the one-byte unreadable fixture's digit count is checked so the
visible output agrees). `-m`, `-L`, `--files0-from`, long options, `--help` and
`--version` are absent.

## sort

```
sort [-nru] [--] [FILE ...]
```

Reads every operand, sorts the lines in memory and writes them; nothing is
written until all input has been read, so an open or read error produces no
stdout. No operands (or `-`) reads standard input. Each operand is split on
newlines, the final partial line is a line, and every output line ends with a
newline (empty input writes nothing). Lines are opaque bytes: NUL and invalid
UTF-8 are preserved and the default order is unsigned byte order (`LC_ALL=C`).

- `-n` compares exact decimals: optional leading spaces/tabs, an optional `-`,
  digits and an optional fraction. `+2`, `1e3` (1), `0x10` (0), `abc` (0),
  `--5` (0), `.5`, `-.5`, `2.` and 40-digit numbers follow GNU's `strnumcmp`,
  with no floating point and no overflow. A leading `+` is not accepted, and
  parsing stops at the first character that is not a digit, `.`, a sign or a
  leading blank. Only ASCII `0-9` are digits: GNU 9.4's `strnumcmp` additionally
  treats a leading byte `0x80` inside the integer leading-zero run as a zero
  (`\x801` compares equal to `1`), which is outside the supported ASCII numeric
  grammar and is deliberately not reproduced; high-byte numeric inputs are kept
  out of the differential matrix.
- `-r` reverses the whole comparison, including the last-resort line comparison.
- `-u` makes key equality the only equality (the whole line, or the numeric
  value with `-n`) and keeps the first input occurrence.

Errors: every named operand is preflighted (opened and closed) before any is
read, so the first operand that cannot be opened wins, even after a directory:
`sort: cannot read: <p>: <text>`, exit 2, no stdout. A directory is caught when
read: `sort: read failed: <p>: <text>`, exit 2, no stdout. A write error prints
`sort: write error` and exits 2 (GNU 9.4 prints two lines there; the gate shape-
compares).

Sort is in memory: peak is linear in the input (roughly five times the input
size for the 50,000-line fixture, with numeric keys making it larger). There is no
external merge; a huge input can exhaust memory (the runtime exits 2). `-k`,
`-t`, `-f`, `-b`, `-d`, `-i`, `-M`, `-h`, `-V`, `-s`, `-c`, `-C`, `-o`, `-m`,
`-z`, `--parallel`, long options, `--help` and `--version` are absent.

## eza (E0 + E1)

```
eza [-1|-T] [E1 flags] [--] [PATH]
eza -l --bytes --time-style=long-iso (-n | --no-user) [E1 flags] [PATH]
```

An eza-0.23.3-compatible binary through phase P5b (design sections 7 and 8).
It never grids: output is one entry per line (the lines view) or a tree, and —
since the reference calls `with_link_paths()` in those views — a symlink prints
`name -> target`. Colour is off unless `--color=always` or an `auto` run on a
TTY asks for it; `--no-quotes` selects eza's `QuoteStyle::NoQuotes` for the
outer names while control escapes and symlink-target quoting are unchanged.
One operand or none; zero operands mean `.` (`eza` on a non-TTY stdin is eza's
stdin-file-name mode, a documented divergence this build does not implement).
Two or more operands are this build's own usage error. The operand is checked
with `lstat`, so a dangling symlink operand is listed
(`link -> target`, broken, no marker), and a directory operand that is a
symlink to a directory is followed for listing unless `-d` or tree mode.

Supported flags (everything else is rejected, see below):

| Flags | Behaviour |
|---|---|
| `-1`, `--oneline` | one per line (the non-TTY default); the last of `-1`/`-l`/`-T` selects the view (a last `-1` makes `-T` a plain listing; a last `-T` with any `-l` is a long tree) |
| `-l`, `--long` | restricted long only (below) |
| `-a`, `--all` | count occurrences; one shows dotfiles, two also synthesizes `.`/`..` |
| `-A`, `--almost-all` | dotfiles without `.`/`..`; `-A -a` is one `-a`, `-A -aa` shows dots |
| `-d`, `--treat-dirs-as-files`, `--list-dirs` | describe a directory operand as one entry |
| `-D`, `--only-dirs` / `-f`, `--only-files` | lstat-type filters; both together cancel; operand files are exempt |
| `-r`, `--reverse` | descending comparison; comparator-equal entries keep readdir order (probed) |
| `-s`, `--sort` | `name`/`filename` (default, natural fold), `Name`/`Filename`, `size`/`filesize`, `modified`/`date`/`time`/`mod`/`new`/`newest`, `extension`/`ext`, `Extension`/`Ext`, `type` |
| `--group-directories-first`, `--group-directories-last` | stable final grouping; a symlink to a directory counts as a directory; with both, first wins (probed) |
| `-F`, `--classify[=WHEN]` | `auto`/`never` no marker on non-TTY; `always` adds `*`, `/`, `\|` or `=`; `automatic` only after `=` |
| `-B`, `--bytes`, `-n`, `--numeric`, `--no-user`, `--time-style` | restricted-long columns (below) |
| `--no-permissions`, `--no-filesize`, `--no-time` | suppress that long column (and its separator) |
| `-T`, `--tree` | depth-first tree with the reference's `├──`/`└──`/`│` glyphs; links are not descended; `-L` bounds depth |
| `-L`, `--level` | tree depth (parsed only when the tree action is active; ignored without `-T`, as the probe records) |
| `--color`, `--colour[=WHEN]` | `always`/`auto`/`automatic`/`never` (bare is auto); the default is auto, or never when `NO_COLOR` is present; an explicit value overrides `NO_COLOR` |
| `--no-quotes` | outer names/targets use NoQuotes (controls still escaped; symlink target names keep QuoteSpaces) |
| `--icons` | bare/`auto`/`automatic`/`never` are no-ops (icons are out of scope); `always` is this build's unsupported error |

Sorting is stable and the golden cases use pairwise comparator-distinct names,
so readdir order never leaks into a recorded byte. `-r` is the probe-confirmed
reversed comparison, not a reversal of the finished vector: equal keys and
comparator-equal names keep their readdir order (`--sort=size -r` on equal
sizes, `-r` on fold-tied `A`/`a`, and the extension/type name fallbacks were
probed against the reference). `extension`/`ext` and
`Extension`/`Ext` use eza's `File::ext` (the substring after the last `.`,
ASCII-lowercased, `None` before `Some("")`) with the design's two-pass stable
pipeline and the folded or case-sensitive name fallback respectively; `type`
pre-orders by case-sensitive natural name and then the rank dir/reg/link/fifo/
socket/char/block/special. The synthetic `.`'s extension key is
`File::ext(operand)` (`None` for `.`, `..`, `/` and a dotless operand), `..` is
always `None`; the key is the operand path even when the operand is a symlink
to a directory, never the target's extension. `-aa` listings of a relative
fixture operand are committed only when every listed pair is
comparator-distinct. `--sort=size`/`--sort=modified` with `-aa` on a
real-directory operand are live-only: the synthetic dots carry the host
directory and its parent's metadata. `--sort=none`, `inode`, `changed`, `accessed`, `created`,
`age`, `old`/`oldest` and `.name`/`.Name`/`.filename`/`.Filename` are rejected
with this build's own message (`--sort=default` is not an eza value and takes
eza's own no-setting message).

When the listing operand is a symlink to a directory, the synthetic `.` is the
symlink itself, not the target directory: kind, mode, size, time and uid come
from `lstat(operand)`, the row displays `. -> target`, and `-D`/`-f`, type
sorting and grouping use that lstat kind (`..` is `lstat(join(operand,".."))`).
Target display and classification reorient the raw target against the operand
path, not its parent — eza's File at `dir.path` quirk — so
`-aa links/realdirlink` prints `. -> realdir` with no classify marker, while a
self-link `. -> .` classifies and groups as a directory; `-aa -F=always` marks
only the rows whose target resolution against the operand succeeds. A trailing
slash resolves the operand before the dot rows (`.` is then a directory row).
These and the `-d ..` rule above contradict design 7.2/7.3; the P0 probe bytes
win.

Quoting is `QuoteSpaces`: a name containing a space or apostrophe is wrapped in
single quotes, or double quotes when it contains an apostrophe; `\t`, `\n`,
`\r` and other controls are escaped (`\u{1b}`), embedded quotes are not, and a
classify marker follows the closing quote. A directory listing shows names
only; an input operand shows `parentPrefix(parent) + name`, where the whole
parent is escaped once (`'x y/sub'/f`, never `'x y'/sub/f`) and a root parent
emits one `/`. A parented `..` operand is not special: `-d sub/..` prints
`sub/..`, `-d 'x y/..'` prints `'x y'/..`, and `-d /..` prints `/..` (the P0
probe; design 7.2's "`.`/`..` return themselves" only holds when the parent is
empty, i.e. `-d ..`). Target display uses the raw readlink bytes the same way
(`'a b'/target`) while metadata and classification use `stat` (follow) on
`paths::join(base, raw)`; a broken target prints the whole raw target escaped
(`'../tar get'`) with no marker, and an unreadable target prints no arrow.

**Colour (E1).** `--color=always` colours names, operand parent prefixes,
symlink arrows/targets and tree glyphs with the reference's default palette:
directories bold blue `1;34`, symlinks cyan `36`, executable regular files
bold green `1;32`, FIFOs yellow `33`, sockets bold red `1;31`, broken arrows
red `31` and broken targets red-underlined `4;31`; a regular file is plain
unless the default file-type palette (readme*/build filenames, image, video,
music, lossless, crypto, document, compressed, temp, compiled, source
extensions; the full v0.23.3 tables) or an `LS_COLORS` mapping applies. Hidden
entries have **no** separate SGR, and setuid/setgid/sticky names have no
separate name style in this view: the executable bit decides (probed; the long
mode characters are a different, deferred concern). Operand and target parent
prefixes use the cyan `symlink_path` style; `fi` is the regular-file fallback,
not an override of the file-type palette. ANSI bytes are never counted in a
width: the plain name/layout is built first and the colour bits are wrapped
around it afterwards.

`auto` asks `io::isTty(stdout())` (the P5a builtin). `NO_COLOR` **present**
(even empty) changes the default to never; an explicit `--color`/`--colour`
overrides it, so `NO_COLOR=1 eza --color=auto` colours on a TTY and
`NO_COLOR=1 eza --color=never` never does. The emitters replicate
nu-ansi-term 0.50.1's `AnsiStrings` difference rule (one prefix, the minimal
diff between adjacent styled pieces, one final reset), which is what makes the
recorded bytes exact.

`LS_COLORS`/`EZA_COLORS` support the documented subset: `di`, `ln`, `ex`,
`fi` and `*.EXT` keys with SGR parameter strings (`1`, `2`, `3`, `4`, `5`,
`7`, `8`, `9`; `30-37`, `90-97`, `38;5;n`, `38;2;r;g;b`; the background
`40-47`, `100-107`, `48;...` equivalents; unknown parameters are ignored and
the style is re-serialised canonically, attributes first, background before
foreground). `*.EXT` (no dot) matches the bytes after the last `.`,
case-sensitively; a key containing a dot (`*.tar.gz`) is a literal suffix;
the **last matching pair wins** (definition order, not longest suffix).
Empty values are not pairs (`di=` leaves the default), a non-SGR value such as
`di=bogus` replaces the slot with the empty style, and unknown keys are
ignored. `EZA_COLORS` is parsed after `LS_COLORS` (both file kinds and
extensions override), `EXA_COLORS` is the fallback when `EZA_COLORS` is
absent (present-empty still wins), and a leading `reset`/`reset:` in the
EZA/EXA string turns off the default file-type palette.

**Tree (E1).** `-T/--tree` is the details view with names only unless `-l` is
also effective. It prints the operand line, then each directory's entries
depth-first with `├── `, `└── `, `│   ` and `    ` prefixes (UTF-8 bytes E2 94
9C/94/80/82), applying the hidden/-A, `-D` and sort rules at every level.
Synthetic dots are never created (`-T` with two `-a` is eza's
`Option --tree is useless given --all --all` error, exit 3); an entry symlink
is never descended (only an lstat directory recurses; `--follow-symlinks` is
out); `-L` stops descending when `level <= depth`, so `-L 0` prints the root
alone. Empty directories print their line only. An unreadable directory prints
a red synthetic child `<Permission denied (os error 13)>` and continues (status
0). `--only-files` drops a directory root entirely (eza's files-list filter),
and `-D` keeps lstat directories at each level. `-r` in a tree sorts each
level ascending once and then reverses the whole order (so comparator ties
reverse with it), then applies the stable dirs-first/last pass; lines and
long keep the reference's stable descending comparison, where ties keep
readdir order. `-T` wins over `-d` because
strict-mode conflict checks are not enabled in this build (the reference probe
without `EZA_STRICT` does the same). Long tree column widths are global across
the tree and an error row is padded to the table width. Tree never needs the
terminal width.

**`--no-quotes` (E1).** This is eza's `QuoteStyle::NoQuotes`:
`quoteName(name, false)` adds no quote characters anywhere in the outer name,
operand parent prefix or broken target, but `\t`, `\n`, `\r` and other
control escapes still apply. A valid symlink target's *name* is always escaped
with `QuoteSpaces` (so `spaced -> 'tar get'` keeps its quotes), while that
target's parent prefix and a broken target use the outer style.

Restricted long is `-l --bytes --time-style=long-iso` plus `-n` or
`--no-user`; any other `-l` invocation is this build's usage error. Columns
are permissions, size, optional numeric user, long-iso UTC date, name. Sizes
are decimal bytes, right-aligned, with `-` for a directory, symlink, FIFO,
socket or other non-regular entry; the permission string is eza's (`s`/`S`,
`t`/`T`, `.` for a regular file, `|` for a FIFO, `s` for a socket); a
pre-1970 mtime is a `-` date; `--no-user` wins over `-n`; widths are computed
over the displayed entries and a `-d` invocation uses the one entry. `-aa -l`
on a real-directory operand is live-only because the `.` row is the listed
directory and the `..` row its host parent; the committed symlink-operand long
cases have fixture-owned dot rows. `--time-style=default`/`long-iso` are accepted and ignored without
`-l`; other styles are this build's error (eza accepts any style in a non-long
view — a documented narrowing).

Exit classes are eza 0.23.3's: 0 success, 1 write failure
(`No space left on device (os error 28)` with no prefix), 2 input metadata
failure (`"path": No such file or directory (os error 2)`), and 3 usage and
unsupported options. The `errors` module keeps eza's documented permission
constant 13, but the pinned 0.23.3 binary does not exit 13 for a directory it
cannot open (the P0 probe records 0), and this build matches the probe. A
permission-denied directory operand prints the reference's
`Permission denied: <path> - code: 13`, blank line and
`Skipped 1 directories due to permission denied: ` summary, and keeps status 0
(the P0 probe records 0.23.3's behaviour, not the man page's 13). `SIGPIPE`
keeps the disposition it inherits: E0 installs no handler, so a default parent
gets the probe's signal 13, while a parent that already ignores SIGPIPE makes a
closed pipe an EPIPE write error.

**Rejected with this build's own message** (exit 3): grid, across, recurse,
`--width`, `--follow-symlinks`, `--dereference`, `--absolute`, colour scale,
ignore globs, git status, the long-view column flags other than the three
suppressions above, `--total-size`, `--hyperlink`, `--mounts`,
`--octal-permissions`, `--context`, `--stdin`, `--flags`, `--binary`,
`--header`, `--inode`, `--links`, `-m`/`--modified`, `--changed`,
`--blocksize`, `-t`/`--time`, `-u`/`--accessed`, `-U`/`--created`,
`-@`/`--extended`, `--smart-group`, `--group`, `--no-symlinks`,
`--show-symlinks`, the `--colour-scale*` spellings and `--icons=always`.
`--help`/`--version`/`-?`/`-v` are not in this build's parser and take the
ordinary `Unknown argument` path. Values of rejected eza-valid flags are still
consumed with eza's value mode before the unsupported error (`--width=N`,
`-I=`, `-L x`).

**Measured (this P5b branch, merged 0.5 tree).** eza binary 172,033 bytes
(sha256 `6f0e8472…`; static, runtime included; the reference is 2,294,768).
On 10,000 entries under `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`, the P5b
measurements are 2,189,984 live heap bytes / 2,560 KiB max RSS for restricted
long, 2,189,360 / 2,560 KiB for the name-only listing, 2,190,112 / 2,560 KiB
for the tree and 2,189,584 / 2,560 KiB for the coloured listing.
`tests/eza.py --memory` keeps the P4 cap `EZA_PEAK_MAX = 2,752,512` bytes (not
raised) and still checks no leak/double-close line. GNU time on the same tree
measures the reference at about 25 MB max RSS (long), 12 MB (names), 14 MB
(tree) and 12 MB (colour) across runs; that measurement is printed for
comparison and is not gated.

**Deferred/divergences (colour and tree).** Long-column colour is deferred:
the reference colours the permission, size, date and name cells individually
(the P0 `color_always_long` probe records exact bytes), but this build leaves
the long cells plain and colours only the name/target; no long-colour case is
in the differential matrix (design 8.1). The EZA_COLORS UI keys (`xx`, `or`,
`pi`/`so`/`bd`/`cd`, permission/size/user/date/git styles and the file-type
category overrides) are advanced keys and are ignored, as are real glob
patterns with `?*[]`; unknown LS_COLORS keys are ignored (the reference turns
them into filename globs, which no E1 fixture matches), and the reference's
compiled-source heuristic (a `.o` beside its `.c`) and mount-point style are
not implemented. Plain `-l` is still this build's restricted-long usage
error, so `-T -l` needs the same `--bytes --time-style=long-iso -n/--no-user`
flags as any long view. Icons, grid, hyperlinks, non-UTF-8 lossy display (E2)
and the no-operand stdin mode remain out. The eza theme configuration
(`EZA_CONFIG_DIR`, `XDG_CONFIG_HOME` and `~/.config/eza/theme.yml`) is not
read: the built-in palette and the environment subset always apply. On a TTY
this build still prints
lines (never a grid) and `-F`'s auto mode stays marker-free, while colour
`auto` does use `isatty(stdout())` as above. The P5b re-probes with exact SGR and
tree bytes are in `tests/eza/E1-PROBES.md` (same pinned binary sha256 as
`REFERENCE.md`).

## Diagnostics and divergences

Messages are English and fixed:

```
cat: <operand>: No such file or directory
cat: write error: No space left on device
wc: <operand>: Is a directory
wc: write error
sort: cannot read: <operand>: Permission denied
sort: read failed: <operand>: Is a directory
sort: write error
```

GNU quotes operands that contain spaces, quotes or control characters; these
tools print the operand bytes verbatim. The harness restricts exact-stderr cases
to quote-free operand names (successful operands may contain spaces). An empty
operand is outside the matrix: `openFile` rejects it with `EINVAL`, so ours says
`cat: : Invalid argument` where GNU prints a quoted empty name. `SIGPIPE` keeps
its default disposition, so `sort | head` behaves like coreutils.

## Metadata (P3) and the path limit

`lib/fs.obx` (P3) is the metadata layer the `eza` core (P4) uses: directory
iteration (`fs::open`/`next`/`close`, which skips `.` and `..` like Rust's
`read_dir`), `statArray`/`stat`/`lstat` over the runtime's 14-int stat array,
`readlink` and the type predicates. `eza` fills each entry with one lazy lstat
(only when the view, sort key or marker needs it; the synthetic dots always
need one), resolves every symlink with `readlink` plus a following `stat`, and
synthesizes its own `.`/`..` records; `statArray` remains the form for a future
parallel-key hot loop rather than an E0 caller;
`cat`, `wc` and `sort` do not import `fs` and their output is unchanged.

The API is path-based: `fs::stat(lstat)`, `fs::readlink` and a tree child's
`fs::open` take a whole path built with `paths::join(operand, name)`. `cpath`
rejects a path of 4096 bytes or more with `ENAMETOOLONG` (36), so one joined
path of 4096+ bytes fails where eza's `DirEntry::metadata()` (an `fstatat` on the
directory fd) would succeed. The same design point is the race between
`getdents64` and the later path-based stat: an entry replaced in between is
followed by the new name. E0's exact cases keep joined paths under 4096 bytes;
the documented future fix is `statAt(dirfd, name)`/`readlinkAt(dirfd, name)` on
`fs::Dir.fd`, which removes both the limit and the race. There is no `getcwd`
and no raw `syscall` builtin in P3, and `fs` does not resolve symlink targets
itself: the caller joins `readlink`'s raw bytes against the link's directory.
