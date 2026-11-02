Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 8. Built-in functions

| Function | What it does |
|---|---|
| `print`, `println` | write values to standard output; `println` adds a newline |
| `eprint`, `eprintln` | the same, to standard error |
| `len(x)` | length of a string or array |
| `append(a, x)` | add `x` to the end of array `a`; `x` is moved in |
| `truncate(a, n)` | shorten `a` to `n` elements; the removed elements are freed |
| `substr(s, from, to)` | part of a string |
| `int(x)`, `byte(x)` | conversions |
| `string(x)` | an int's digits, or the string made from a `[]byte` |
| `readFile(path)` | `?[]byte`, or `nil` if the file cannot be read |
| `writeFile(path, data, executable)` | write bytes; returns `bool` |
| `openFile(path, mode)` | `Result[int]`: open `path` with mode `"r"`, `"w"` or `"a"`; the caller closes the descriptor |
| `readFd(fd, max)` | `Result[?[]byte]`: one read of up to `max` bytes; `nil` is end of file |
| `writeFd(fd, data)` | `Result[int]`: one write of the borrowed `[]byte`; a short count is success |
| `closeFile(fd)` | `Result[bool]`: `ok(true)` on success |
| `readDirFd(fd, max)` | `Result[?[]byte]`: one `getdents64` read of up to `max` bytes (at most 1 MiB); `nil` is end of directory |
| `statPath(path)` | `Result[[]int]`: the 14 stat fields, following symlinks |
| `lstatPath(path)` | `Result[[]int]`: the 14 lstat fields, not following symlinks |
| `readLinkPath(path)` | `Result[[]byte]`: the raw symlink target bytes, no NUL |
| `isatty(fd)` | `bool`: true when the descriptor is a terminal |
| `getenv(name)` | `?[]byte`: the variable's value, copied; `nil` if absent or the name is invalid |
| `terminalWidth(fd)` | `int`: the terminal's column count; 0 when the descriptor is not a terminal or the count is unknown |
| `sleepMs(ms)` | `Result[bool]`: wait at least `ms` milliseconds (`0..2147483647`) |
| `stdin()`, `stdout()`, `stderr()` | the standard descriptors, 0, 1 and 2 |
| `args()` | the command-line arguments, as `[]string` |
| `exit(code)` | stop with the given exit code |
| `take(x)` | move out of an optional variable, field or element; leaves `nil` |
| `drop(x)` | free a local variable now, rather than at scope exit |
| `replace(place, value)` | store `value` in `place` and return the old value, which the caller owns |
| `handle(x)` | make a handle to `x` (section 5) |
| `ok(v)` | a `Result[T]` success holding `v`, which is moved in |
| `err(message[, code])` | a `Result[T]` failure; code defaults to 0 |
| `alive(h)` | `true` if the handle's object has not been freed |

`drop` and `replace` need a local variable or a place holding an owned value:
`drop(r.child)` is rejected.

```obo
// example: runs
// prints 3 4
// prints 1
type E = ref record { v: int }

fn main() {
    var e = new E{v: 3}
    let old = replace(e, new E{v: 4})
    println(old.v, " ", e.v)
    let d = new E{v: 1}
    drop(d)                        // freed now
    println(1)
}
```

### Incremental I/O

`openFile` returns `Result[int]`: the descriptor is a copied scalar with no
owner, and the program closes it with `closeFile`, normally through `defer`.
The modes are exactly `"r"`, `"w"` (`O_CREAT|O_TRUNC`) and `"a"` (`O_CREAT|
O_APPEND`). A constant mode is checked at compile time; any other problem is a
Result: `err("cannot open " + path, errno)`, with errno in `code` (2 for a
missing file, 22 for an empty path or an embedded NUL, 36 for a path of 4096
bytes or more). `readFd(fd, max)` makes one read: `ok(nil)` means end of file,
a non-nil array of 1..max bytes is data, and a count that is zero or negative is
`err("readFd needs a positive count", EINVAL)` before the max-sized array is
allocated. `writeFd(fd, data)` borrows the array and makes one write; a short
count is success, so the caller loops. `closeFile(0/1/2)` is refused with
EINVAL, so a program cannot close the standard streams; `closeFile` on any other
descriptor is a Result, and its error in a `defer` is discarded. A descriptor
number can be reused after a close: the language does not track ownership of the
number, so a stale `int` may read or close a file opened later under the same
number.

`exit(...)` runs no defers: a `defer closeFile(fd)` does not run, and the
operating system reclaims the descriptor. Return from `main` instead when the
`OBO_CHECK` oracles (a leaked fd exits 8; a double close or a use after close
exits 9) should run.

```obo
// example: runs
// prints 22
// prints readFd needs a positive count
fn main() {
    let n = 0
    let r = readFd(stdin(), n)          // computed: EINVAL, and stdin is not read
    match r {
        ok { println("unexpected ok") }
        err { println(r.code); println(r.message) }
    }
}
```

```obo
// example: runs
// prints 0 1 2
// prints HI
// prints 2
fn main() {
    println(stdin(), " ", stdout(), " ", stderr())
    let b = new []byte(2)
    b[0] = 'H'
    b[1] = 'I'
    let r = writeFd(stdout(), b)        // the text above is flushed first
    match r {
        ok(n) { println(""); println(n) }
        err(m, c) { println(m) }
    }
}
```

`obo/lib/io.obx` is a source module (`module io`) that builds buffered
`Reader`/`Writer` types on these builtins (`fill`, `readLine`, `readN`,
`readByte`, `takeChunk`, `flush`, `pump`, `count`), adds `writeByte` and,
in P5a, `isTty`. Importers write `import "../lib/io.obx" as io` (or
`"io.obx"` with `-I obo/lib`) and qualify the names (`io::newReader`,
`io::flush`). Its own header documents the API and the fd-is-borrowed rule.
The rest of the stdlib is a set of source
modules under `obo/lib/`: `strings` (`cmp`, split/join, trim, natord and
QuoteSpaces helpers), `paths`, `sort`, `time`, `cli`, `fmt` (`digits`,
`appendPadded`, `writePadded`, `modeString`), `errors` (C-locale errno
`text`, `osError`, `debugPath` and the eza-shaped `EXIT_*` constants), `fs`
(directory iteration, stat/lstat and symlink targets; see "Metadata" below),
`env` (`get`, `has`, `value`, `getOr`; see "Environment" below), `html` and
`sqldialect`. P2 moved
`writePadded` from `io` and the digit/padding helpers from `strings` into
`fmt`, and removed the four P1 compatibility functions.

`obo/lib/html.obx` is the builder API for the HTML template domain. `Html` is
a record whose `text` field is **private to the module**; `raw` is the explicit
trusted constructor, `escapeText` encodes text content, `escapeAttr` encodes one
quoted attribute value (both quote types), `validateUrl` allow-lists relative,
`http`, `https` and `mailto` URLs and attribute-encodes the result, `join`
concatenates `Html` fragments without escaping them, and `toText` is the
read-only rendering accessor. Direct `new html::Html{text: ...}` from an importer
is not legal: it is refused as an external construction of a private-field
record. Trust is a documented convention, not type enforcement: `raw` still
accepts any string and is equivalent to the old direct construction. Escaping is
one pass and not idempotent: an already-encoded string is encoded again, and
`raw` must not be fed untrusted values. There is deliberately no API for
script/style bodies, comments, unquoted attributes or dynamic tag/attribute
names, and no sanitizer. The compiler-known `html` tag lowers literal chunks to
trusted markup and holes to the same context policy; the builder API remains the
explicit form and shares the nominal `Html` type. Its header documents the byte,
control and URL policies.

### Template literals (sql/html)

Compiler-known backtick tags are values, not a user-extensible tag protocol.
A tag name is followed by a backtick in source (whitespace between them is
accepted):

```text
tag   := "sql" | "sqlRaw" | "html" | "htmlRaw"
expr  := tag "`" (chunk | "${" expression "}")* "`"
chunk := raw source bytes; only \` and \${ escape
```

The tags are contextual: `sql` and `html` are ordinary identifiers everywhere
else, so a local or field named `sql`/`html` keeps its normal field access. The
type names `Sql`, `SqlArg`, `Html`, `HtmlAttr` and `HtmlAttrs` are reserved. `sqlRaw` and `htmlRaw` take a literal with no holes. Chunks are raw, so
backslashes, newlines and UTF-8 survive byte-for-byte. A `sql` literal chunk may
not contain `$` followed by a digit because `$n` is reserved for parameters;
`$tag$` quoting is still literal text. A template cannot be built in a global
initializer.

`sql` and `Sql` are ordinary public-field records: `text: []string` and
`params: []SqlArg`. The text array alternates chunks and arguments, so rendering
walks text, then `$1`, then text, and so on; a bound value is never interpolated
into SQL text. Supported holes are `int`, `byte`, `bool`, `string`, `float`,
`[]byte`, `decimal`, `SqlArg` and a `Sql` fragment. A string is always a
parameter, even when it contains SQL syntax. `[]byte` is moved into a `Bytes`
argument; use `sqlBytes(bytes)` for an explicit copy. A decimal is transported as
exact text in `SqlArg.DecimalText` (display form, canonical only when the display
scale was capped; never rounded). `sqlNull()` is an explicit NULL argument,
distinct from an empty fragment. Splicing a `Sql` consumes it; `sql.copy(frag)`
copies it first. `sqlJoin(parts, sep)` joins a `[]Sql` with a separator and
`sqlWhere(parts)` returns empty for an empty array, otherwise
`WHERE (p1) AND (p2)`: each predicate is parenthesized so an inner `OR` cannot
capture its neighbour. `sqlJoin` holds its parts array across the separator
expression and rejects a separator that moves, frees, replaces, appends to or
truncates it; compute such a separator before the call. A `[]Sql` is never
expanded implicitly. Literal chunks are preserved verbatim and are not parsed:
a literal `?`, `:1`, `$1` or `@p1` in a chunk is still a placeholder for the
target dialect, so quote such text or bind it as a value.

`html` and `Html` are the claimed-safe content type. Its `text` field is private
to module `html`; `html::toText` is the rendering accessor, `html::raw` is the
explicit trusted constructor, and `html.copy(frag)` copies a fragment before a
splice. Literal chunks are trusted markup. A hole in text content escapes
`& < > " '` and ASCII controls; a hole in a quoted attribute escapes both quote
characters and tab/LF/CR; a hole in a URL attribute (`href`, `src`, `action`,
`formaction`, `cite`, `poster`, `background`, `longdesc`, `usemap`, `data`,
`manifest`, `ping`, `profile`, `xlink:href`) also applies the scheme allow-list
(relative references, `http`, `https`, `mailto`) and traps on any other scheme.
URL values are validated as assembled attribute values: a scheme may not be
split across holes, a URL attribute may contain only one hole, and a literal
`&` next to a hole is rejected because it could be an HTML character reference
(for example `&#58;`) that the browser decodes before URL parsing. Build and
validate the whole string first if a dynamic URL is needed. `srcset` (a URL
list) and `content` (for example a meta refresh target) are not URL-classified
in v1: a hole there gets ordinary quoted-attribute escaping, not the URL scheme
check, so treat those attributes as trusted literal markup or pre-validate the
value. HTML whitespace is
space, tab, LF, form feed (U+000C) and CR; form feed separates names and values
exactly like the other whitespace bytes. An `Html` hole is inserted raw and is allowed only in text
content; `[]Html` concatenates there. `Html` in an attribute is a compile error,
and `HtmlAttrs`/`HtmlAttr` have no v1 hole. Holes are rejected at compile time in
script/style bodies, comments, doctypes, unquoted attribute values, tag names,
attribute names, and `srcdoc`/`style`/`on*` values (script, CSS and event-handler
contexts need typed encoders, not attribute escaping). A fake `</scriptx>` or
`</styley>` does not close a raw-text body; a closing tag needs a delimiter. There is no script/CSS encoder, dynamic tag or attribute name
API, sanitizer, or generic `escape` entry point.

```text
let q = sql`SELECT * FROM account WHERE id = ${id} AND name = ${name}`
let extra = sqlRaw`WHERE x = y`
let q2 = sql`${q} ${extra}`                    // splice: q and extra are consumed
let safe = html`<a href="/u/${id}" title="${name}">${name}</a>`
let page = html`<article>${body}</article>`   // body: Html, inserted raw
var states = new []Sql(0)
append(states, sql`state = ${s}`)
let inList = sql`state IN (${sqlJoin(states, sqlRaw`, `)})`
```

Ownership follows ordinary records: a template builder is a hidden owned local
freed on every exit, the result is an owned `Sql`/`Html`, and splicing moves the
fragment. `SqlArg` is a closed type (`Null`, `Int`, `Bool`, `Float`, `Str`,
`Bytes`, `DecimalText`); direct `new Sql{...}`/`new Html{...}` construction is
the explicit raw escape hatch, and `Html` is private to its module. The
value/parameter pair is dialect-neutral. `obo/lib/sqldialect.obx` renders it
without parsing or interpolation: `render(&s, dialect)` and
`renderPostgres`/`renderSqlite`/`renderSqlServer`/`renderSqlServerOdbc`/
`renderOracle` emit `$1..$n`, `?`, `@p1..`, `?` or `:1..`, validate
`len(text) == len(params) + 1`, and preserve literal text. The experimental
`postgres` package adds `execute_sql(&conn, &s)` and SQLite adds
`prepare_sql(&conn, &s)`/`bind_sql(&stmt, &s)`, mapping the ordered arguments
(including exact `DecimalText` and deep-copied `Bytes`) without interpolation.
`SqlArg.Float` returns the explicit `SQL_ARG_FLOAT_UNSUPPORTED` (1014) error in
both bridges; there is no binary64 parameter path. SQL Server and Oracle have
renderer support only, not adapters. Identifier/`ORDER BY` allow-lists remain
later work. Tests live in `tests/run/template_*.obx`,
`tests/errors/template.obx` and `tests/stdlib/sqldialect_dialects.obx`.

### Metadata

`readDirFd(fd, max)` is one `getdents64` read into a fresh byte array. The
checks run before that array is allocated, in this order: a `max` that is zero
or negative is `err("readDirFd needs a positive count", EINVAL)`; a `max` over
1 MiB (1048576) is `err("readDirFd count is too large", EINVAL)`; a negative
descriptor is `err("cannot read dir fd N", EBADF)`; a tracked descriptor that
was closed is `err(..., EBADF)`, or exits 9 under `OBO_CHECK=1` with no
syscall. A constant count outside 1..1048576 is a compile error. The kernel
returns whole `linux_dirent64` records; a short buffer is data and only a
zero-byte read is end of directory, reported as `ok(nil)` (never an empty
array). A descriptor for a regular file gives the kernel's `ENOTDIR`. The call
flushes pending text output first, as `readFd` does.

`statPath(path)` and `lstatPath(path)` return the 14 numbers of the x86-64
Linux `struct stat` in this order, which is not the kernel's layout:

| index | field | index | field |
|---|---|---|---|
| 0 | mode (`st_mode`, type bits included) | 7 | ino |
| 1 | size (signed) | 8 | dev |
| 2 | mtime seconds | 9 | rdev |
| 3 | mtime nanoseconds | 10 | blksize |
| 4 | uid | 11 | blocks |
| 5 | gid | 12 | atime seconds |
| 6 | nlink | 13 | ctime seconds |

`statPath` follows a symlink; `lstatPath` does not. Both are path-based and
resolve a relative path against the process cwd. A missing path is ENOENT (2),
an empty path or an embedded NUL is EINVAL (22) before the syscall, and a path
of 4096 bytes or more is ENAMETOOLONG (36). `readLinkPath(path)` returns the
raw target bytes with no NUL: a dangling target still succeeds, a non-symlink
is EINVAL, and the longest accepted target is 4095 bytes (a full 4096-byte
target would be ENAMETOOLONG, never a silent truncation). `readlink` does not
resolve the target; the caller decides how to join it.

```obo
// example: runs
// prints 14 true
fn main() {
    let r = statPath(".")                  // the directory the check runs in
    match r {
        ok { println(len(r.value), " ", r.value[0] & 0xF000 == 0x4000) }
        err { println(r.message) }
    }
}
```

The directory buffer is 32 KiB by default. `readDirFd`, `statPath`, `lstatPath`
and `readLinkPath` retry `EINTR`; a test-only `OBO_META_FAULT=eintr` hook (inert
unless `OBO_CHECK=1`) injects one synthetic `EINTR` per call kind.

`obo/lib/fs.obx` builds the eza-facing surface on these builtins: the `STAT_*`
and `KIND_*` constants, `Stat`/`DirEntry`/`Dir`, `open` (via `openFile "r"`),
`next`, `close` (idempotent: a second close is `ok(false)` with no syscall),
`statArray`/`stat`/`lstat`, `readlink`, `typeOf`, the type predicates and
`sizeForLong`. **`fs::next` skips the records named exactly `.` and `..`**,
because eza's Rust `read_dir` never yields them and the eza layer synthesizes
them itself; every emitted name is a copy (`substr`) that survives refills and
`close`, and `.`/`..` do not count against the iteration. The entry kind is the
kernel's `d_type`; for `DT_UNKNOWN` `next` lazily lstats the entry path
(`paths::join(dir.path, name)`) and uses `typeOf(mode)`, leaving the kind
unknown/special when that lstat fails. A malformed record (a length below 20,
a record past the buffer, or no NUL inside it) is
`err("bad directory entry", EIO=5)` and the iterator does not advance.

### Environment and terminal size

`isatty(fd)` is `true` only when `ioctl(fd, TCGETS)` succeeds: a negative
descriptor is `false` before any syscall, and a pipe, a regular file,
`/dev/null`, a directory or a closed descriptor is `false` (`ENOTTY`/`EBADF`),
never an fd-table trap. A tracked descriptor that was closed may have been
reused by the kernel, and the ioctl then observes the reused one. There is no
`Result` and no allocation; the ioctl retries `EINTR` under the same
`OBO_META_FAULT=eintr` hook as the metadata calls.

`getenv(name)` is `?[]byte`: `nil` when the name is invalid (empty, an
embedded NUL, or containing `=`) or absent, and a non-nil empty array when the
variable is present with an empty value. The name is matched as `name=`
against `envp`; the first match wins, and the value is copied into an array
the caller owns, so later changes to the environment cannot affect it. There
is no `setenv` and no enumeration.

```obo
// example: runs
// prints false true
fn main() {
    let missing = getenv("")          // an empty name is invalid
    println(isatty(-1), " ", missing == nil)
}
```

`terminalWidth(fd)` is the terminal's column count from
`ioctl(fd, TIOCGWINSZ)`: `0` when the descriptor is not a terminal
(`ENOTTY`), invalid (`EBADF`) or negative, and `0` when the terminal reports a
zero column count (unknown; a fresh pty may do so). It never consults the fd
table and allocates nothing, with the same closed/reused-descriptor caveat and
`EINTR` retry as `isatty`. The descriptor is handed to the kernel as its 32-bit
`int`, so a value outside 0..0xFFFFFFFF reaches the kernel as its low 32 bits;
this is pre-existing and shared by `isatty` and the other fd builtins. Callers
fall back to 80 for an unknown width.

```obo
// example: runs
// prints 0 true
fn main() {
    let w = terminalWidth(-1)         // not a terminal: 0
    println(w, " ", w == 0)
}
```

`sleepMs(ms)` waits at least `ms` milliseconds and returns `ok(true)`. The
accepted range is `0..2147483647` (about 24.8 days); a negative count is
`err("sleepMs needs a non-negative count", EINVAL)` and a larger one is
`err("sleepMs count is too large", ERANGE)`, both before any syscall. It uses
`nanosleep` and retries `EINTR` with the kernel's remaining time; pending text
output is flushed before the wait, so a prompt is visible. Zero returns without
a syscall. The test-only `OBO_META_FAULT=eintr` hook injects one synthetic
interruption.

```obo
// example: runs
// prints true
// prints 22
fn main() {
    let z = sleepMs(0)
    match z {
        ok(v) { println(v) }
        err(m, c) { println(m) }
    }
    let n = sleepMs(-1)
    match n {
        ok(v) { println("unexpected") }
        err(m, c) { println(c) }
    }
}
```

`obo/lib/env.obx` wraps `getenv`: `env::get` (the `?[]byte`), `env::has`
(present, even when empty), `env::value` (a `string`, `""` when absent or
empty) and `env::getOr(name, fallback)`. `obo/lib/io.obx` adds `io::isTty(fd)`
and `io::termWidth(fd)`.
