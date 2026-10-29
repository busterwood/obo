# obo command-line tools

The tools written in obo: `cat`, `wc`, `sort` and the P4 `eza` E0 core. Each
source file here is a module root that imports `../lib/...` modules by explicit
relative path; every library name is qualified (`io::Writer`, `io::newWriter`,
`fmt::writePadded`, `errors::text`, ...). `cat`, `wc` and `sort` are headerless
roots; `eza.obx` declares `module eza` and imports all nine library modules.
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
python3 tests/eza.py --require-reference     # the eza E0 differential gate
python3 tests/eza.py --memory
```

The library modules are `lib/io.obx` (`Reader`, `Writer`, the buffered functions
and `writeByte`), `lib/strings.obx` (byte classification, `cmp`, the split/join
and natord/quote helpers), `lib/fmt.obx` (`digits`, `appendPadded`,
`writePadded`, `modeString` for the later eza long view) and `lib/errors.obx`
(the C-locale errno `text`, `osError`, `debugPath`, `report` and the eza-shaped
`EXIT_*` constants). They stay in `lib/`, and the tools live in `cmd/`; P2 moved
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

## eza (E0)

```
eza [-1] [E0 flags] [--] [PATH]
eza -l --bytes --time-style=long-iso (-n | --no-user) [E0 flags] [PATH]
```

The first working eza-0.23.3-compatible binary (design section 7, phase P4).
E0 is deliberately **non-TTY**: stdout is a pipe or file, one entry per line,
and — since `lines.rs` calls `with_link_paths()` — a symlink prints
`name -> target` in every E0 view. One operand or none; zero operands mean `.`
(`eza` on a non-TTY stdin is eza's stdin-file-name mode, a documented
divergence E0 does not implement). Two or more operands are this build's own
usage error. The operand is checked with `lstat`, so a dangling symlink operand
is listed (`link -> target`, broken, no marker), and a directory operand that
is a symlink to a directory is followed for listing unless `-d`.

Supported flags (everything else is rejected, see below):

| Flags | Behaviour |
|---|---|
| `-1`, `--oneline` | one per line (the non-TTY default); last of `-1`/`-l` selects the view |
| `-l`, `--long` | restricted long only (below) |
| `-a`, `--all` | count occurrences; one shows dotfiles, two also synthesizes `.`/`..` |
| `-A`, `--almost-all` | dotfiles without `.`/`..`; `-A -a` is one `-a`, `-A -aa` shows dots |
| `-d`, `--treat-dirs-as-files`, `--list-dirs` | describe a directory operand as one entry |
| `-D`, `--only-dirs` / `-f`, `--only-files` | lstat-type filters; both together cancel; operand files are exempt |
| `-r`, `--reverse` | reverse the whole order after the field sort |
| `-s`, `--sort` | `name`/`filename` (default, natural fold), `Name`/`Filename`, `size`/`filesize`, `modified`/`date`/`time`/`mod`/`new`/`newest`, `extension`/`ext`, `Extension`/`Ext`, `type` |
| `--group-directories-first`, `--group-directories-last` | stable final grouping; a symlink to a directory counts as a directory; with both, first wins (probed) |
| `-F`, `--classify[=WHEN]` | `auto`/`never` no marker on non-TTY; `always` adds `*`, `/`, `\|` or `=`; `automatic` only after `=` |
| `-B`, `--bytes`, `-n`, `--numeric`, `--no-user`, `--time-style` | restricted-long columns (below) |
| `--no-permissions`, `--no-filesize`, `--no-time` | suppress that long column (and its separator) |
| `--color`/`--colour`, `--icons` | bare/`auto`/`automatic`/`never` are no-ops without a TTY; `always` is this build's unsupported error |

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
always `None`; `-aa` listings of a relative fixture operand are committed only
when every listed pair is comparator-distinct. `--sort=size`/`--sort=modified`
with `-aa` are live-only: the synthetic dots carry the host directory and its
parent's metadata. `--sort=none`, `inode`, `changed`, `accessed`, `created`,
`age`, `old`/`oldest` and `.name`/`.Name`/`.filename`/`.Filename` are rejected
with this build's own message (`--sort=default` is not an eza value and takes
eza's own no-setting message).

Quoting is `QuoteSpaces`: a name containing a space or apostrophe is wrapped in
single quotes, or double quotes when it contains an apostrophe; `\t`, `\n`,
`\r` and other controls are escaped (`\u{1b}`), embedded quotes are not, and a
classify marker follows the closing quote. A directory listing shows names
only; an input operand shows `parentPrefix(parent) + name`, where the whole
parent is escaped once (`'x y/sub'/f`, never `'x y'/sub/f`) and a root parent
emits one `/`. Target display uses the raw readlink bytes the same way
(`'a b'/target`) while metadata and classification use `stat` (follow) on
`paths::join(base, raw)`; a broken target prints the whole raw target escaped
(`'../tar get'`) with no marker, and an unreadable target prints no arrow.

Restricted long is `-l --bytes --time-style=long-iso` plus `-n` or
`--no-user`; any other `-l` invocation is this build's usage error. Columns
are permissions, size, optional numeric user, long-iso UTC date, name. Sizes
are decimal bytes, right-aligned, with `-` for a directory, symlink, FIFO,
socket or other non-regular entry; the permission string is eza's (`s`/`S`,
`t`/`T`, `.` for a regular file, `|` for a FIFO, `s` for a socket); a
pre-1970 mtime is a `-` date; `--no-user` wins over `-n`; widths are computed
over the displayed entries and a `-d` invocation uses the one entry. `-aa -l`
is live-only because the `.` row is the listed directory and the `..` row its
host parent. `--time-style=default`/`long-iso` are accepted and ignored without
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
keeps its default disposition.

**Rejected with this build's own message** (exit 3): tree (`-T`), level
(`-L`), grid, across, recurse, `--width`, `--no-quotes`, `--follow-symlinks`,
`--dereference`, `--absolute`, colour scale, ignore globs, git status, the
long-view column flags other than the three suppressions above, `--total-size`,
`--hyperlink`, `--mounts`, `--octal-permissions`, `--context`, `--stdin`,
`--flags`, `--binary`, `--header`, `--inode`, `--links`, `-m`/`--modified`,
`--changed`, `--blocksize`, `-t`/`--time`, `-u`/`--accessed`, `-U`/`--created`,
`-@`/`--extended`, `--smart-group`, `--group`, `--no-symlinks`,
`--show-symlinks`, the `--colour-scale*` spellings and `--icons=always`/
`--color=always`. `--help`/`--version`/`-?`/`-v` are not in the E0 parser and
take the ordinary `Unknown argument` path. Values of rejected eza-valid flags
are still consumed with eza's value mode before the unsupported error
(`--width=N`, `-I=`, `-L x`).

**Measured (this P4 branch).** eza binary 126,977 bytes (static, runtime
included; the reference is 2,294,768). On 10,000 entries under
`OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`, restricted long peaks at 2,185,008
live heap bytes and 2,432 KiB max RSS; the name-only listing peaks at
2,184,384 and 2,432 KiB. `tests/eza.py --memory` gates on
`EZA_PEAK_MAX = 2,752,512` bytes (the first long measurement plus a 26.0%
margin) and on no leak/double-close line. GNU time on the same tree measures
the reference at 24,760 KiB max RSS (long) and 11,992 KiB (names) in the
recorded run (about 24,200-25,400 KiB long and 12,000 KiB names across runs);
that measurement is printed for comparison and is not gated.

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
(only when the view, sort key or marker needs it), resolves every symlink with
`readlink` plus a following `stat`, and synthesizes its own `.`/`..` records;
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
