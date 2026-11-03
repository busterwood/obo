# obx library

The standard library, written as ordinary obx code over a few intrinsics.
`std.obx` is the prelude: every program sees its `pub` names unqualified.
The other modules are imported by name, `import "strings"`, and used
qualified, `strings.join(...)`; this directory is a default import root of
the reference compiler, as are any `-L` roots. A bare import path names a
library module only: a program's own modules are imported with `./` or `../`
(`import "./money"`), relative to the importing file, so a local file never
shadows a library module.

    python3 obx/lib/run_tests.py -j 8        # the library tests (OBX_CHECK=1)
    python3 obx/lib/tests/gen_decimal_random.py   # regenerate decimal_random.obx

Tests are in `tests/run` and `tests/error`, in the conformance suite's format
(`// expect:`, `// exit:`, `// stdin:`, `// error:`); the runner is the
conformance runner pointed at this directory, and every run test is checked
for leaks.

## Modules

| Module | Lines | Contents |
|---|---:|---|
| `std.obx` | 1,400 | Option, Result, Unit; Eq/Ord/Hash/Show for the primitives; print/println/eprint/eprintln; min/max; float text and math methods; StrBuf and basic string helpers; Decimal; sort; Map |
| `money.obx` | 200 | Currency, USD/EUR/GBP/JPY/CHF, Money[C] |
| `math.obx` | 130 | float constants, parse, fixed, toDecimal, near |
| `collections.obx` | 390 | stable sorts, searching, rearranging, copying, Set, Heap |
| `strings.obx` | 490 | search, split/join, replace, trim, case, int parse/format, padding, UTF-8 |
| `io.obx` | 160 | IoError, whole-file read/write/append, readLines, readStdin, buffered Writer |
| `fs.obx` | 260 | stat/lstat, list, mkdirAll, remove/removeAll, rename, symlink, chmod, paths |
| `os.obx` | 60 | arguments, environment, pid, working directory, quit |
| `time.obx` | 160 | wall and monotonic clocks, sleep, UTC dates, ISO 8601 text |
| `sqldialect.obx` | 260 | Value, DbError and its codes, SQL fragments with placeholders per dialect, exact decimal text and (p, s) checks |
| `ffi.obx` | 140 | C memory for modules that call C: owned `Mem` (alloc, cstr, copyBytes), unchecked peek/poke/bytes |
| `sqlite.obx` | 440 | SQLite 3 through libsqlite3: Db, Stmt |
| `postgres.obx` | 370 | PostgreSQL through libpq: Conn, Rows |

### std (prelude)

- **Option[T]** `Some(T)`/`None`: `isSome`, `isNone`, `unwrap`, `expect(msg)`,
  `unwrapOr(d)`, `map(&f)`, `eq` (T: Eq), `toStr` (T: Show). **Result[T, E]**
  `Ok`/`Err` (the `try` lang item): `isOk`, `isErr`, `unwrap`, `expect`,
  `unwrapOr`, `unwrapErr`, `expectErr(msg)`, `map(&f)` (the Ok value),
  `mapErr(&f)` (the Err value: `try r.mapErr(toError)`), `ok()`, `err()`,
  `toStr`. **Unit**.
- **Eq, Ord, Hash, Show** with implementations for `int i32 u32 u64 byte i128
  u128 float bool str` (float has no `hash`). `min`, `max` for `T: Ord + copy`.
- **Checked arithmetic** on every integer type: `a.checkedAdd(b)`,
  `checkedSub`, `checkedMul`, `checkedDiv`, `checkedRem` return `None` exactly
  where the operator would stop the program (overflow, zero divisor,
  `MIN / -1`). Use them where the operands come from input; the operators stop
  on what would be a bug. Decimal has the same through `Result`: `addExact`,
  `subExact`, `mulExact`, `remExact`, and `div`.
- `print`, `println`, `eprint`, `eprintln` (one `Show` argument; stdout is
  buffered and flushed at exit and before stderr writes).
- **float**: `toStr` is the shortest text that reads back as the same value
  (`0.1`, `1e25`, `1.5e-7`, `5e-324`, `-0`, `inf`, `NaN`). Methods over libm:
  `sqrt cbrt pow powi exp ln log2 log10 sin cos tan asin acos atan atan2 sinh
  cosh tanh floor ceil trunc round roundEven abs mod hypot copySign isNaN
  isInf isFinite`.
- **StrBuf**: `newStrBuf`, `add`, `addByte`, `addInt`, `addLine`, `size`,
  `clear`, `toStr`. Strings: `substr`, `indexOf(s, sub, from)`, `startsWith`,
  `endsWith`, `contains`, `split(s, sep: byte)`, `trim`, `isSpace`, `isDigit`,
  `parseInt(s): Option[int]`, `repeat`.
- **Decimal**: a coefficient below 10^38, an exponent and a
  display scale; zero is unsigned. Literals `1.25` are Decimals (the
  `fromFraction` lang item); also `decimalOf(n)`, `Decimal.fromUnits(units,
  scale)`, `Decimal.fromInt`, `Decimal.parse(s): Result[Decimal, DecError]`.
  Operators `+ - * %` and unary `-` are exact and stop the program with exit
  code 10 when the result is not representable; `addExact`/`mulExact` return
  a Result instead. `div(b, scale, mode)`, `quantize(scale, mode)`,
  `toInt(mode)` return Results, and so do `round(scale)` (HALF_EVEN),
  `roundAway`, `floor`, `ceil`, `trunc` and `withScale(s)`. `cmp`/`eq`/`hash`
  compare values (`1.0 == 1.00`, equal hashes). `toStr` keeps the display
  scale (`0.50`), `format()` is the same as a Result (an error when the scale
  would drop digits), `canonical()` has no trailing zeros; `scale`, `exponent`,
  `coefficient`, `sign`, `isZero`, `isNeg`, `abs`, `neg`, `toFloat`,
  `sumDecimals`. **Rounding** (written qualified, `Rounding.HALF_EVEN`):
  `HALF_EVEN HALF_AWAY HALF_TOWARD_ZERO TOWARD_ZERO AWAY FLOOR CEILING
  UNNECESSARY`. A fractional literal is `fromFraction(units: i128, scale:
  int)` (`1.5e3` is units 15, scale -2); a scale over 38 stops with
  DecError.Range. The stop helpers (`orStop`, money's `stop`) return `never`. **DecError** codes:
  DecError.Overflow 1, DecError.Inexact 3, DecError.Invalid 4, DecError.DivZero 5, DecError.Range 6,
  DecError.Precision 7, DecError.Exponent 8, DecError.Display 9.
- **sort(xs: mut &[]T)** for `T: Ord`: heap sort, in place, not stable.
- **Map[K: Eq + Hash, V]**: insertion-ordered entries plus an open-addressing
  index. `newMap`, `size`, `has(&k)`, `get(&k): Option[V]` and `getOr(&k, d)`
  (V: copy), `at(k): &V`, `atMut(k): mut &V`, `atOrPut(k, d): mut &V`,
  `put(k, v)`, `remove(&k): Option[V]`, `clear`, `keyAt(i)`, `valAt(i)`,
  `valAtMut(i)` for walking, `each(fn(k, v) {...})` and `eachMut(fn(k, v)
  {...})` (v is `mut &V`) in insertion order, `keys()`/`values()` (copy
  types). `remove` moves the last entry into the hole.
- Powers of ten for Decimal come from `POW10`, a constant table (read-only
  static data) instead of a 39-arm match.

### money

`Money[C: Currency]` is a value record over a Decimal; the currency is a
type, so `usd + eur` does not compile. `money.of[C](d)` is exact (stops with
exit 10 when `d` has more decimals than the currency), `money.round[C](d,
mode)`, `fromMinor(units)`, `zero()`, `parse[C](s): Result`. Methods:
`amount`, `code`, `toMinor(): Result`, `isZero`, `isNeg`, `abs`, `+ - neg`,
`times(rate)` (HALF_EVEN), `timesWith(rate, mode)`, `timesInt(n)`, `cmp`, `eq`,
`hash`, `toStr` (`"12.50 USD"`), `allocate(n)` and `allocateBy(&weights)`
(largest remainder; the parts always add up to the amount), `money.sum(&xs)`.

### math

`PI TAU E SQRT2 LN2 LN10`, `inf()`, `nan()`, `parse(s): Option[float]`
(decimal, hex `0x1p3`, `inf`, `nan`; no spaces), `fixed(x, places)` (rounds
the shortest decimal form half to even: `fixed(2.675, 2)` is `"2.68"`),
`toDecimal(x): Option[Decimal]` (exact, from the shortest form), `near(a, b,
tol)`.

### collections

`sortBy(xs, cmp: &fn(&T, &T): int)` and `sortStable(xs)` (sortBy with
`cmp`): a stable merge sort over an index array followed by an in-place
permutation, so owned elements work. Every predicate and callback is a
closure type (`&fn`), so it may use the caller's locals:
`count(&xs, fn(x) { return x > lo })`; a named function also works. `isSorted`, `binarySearch(xs, &x): Result[int, int]` (Ok index or Err
insertion point), `lowerBound`, `upperBound`, `find`, `findBy`, `contains`,
`count(xs, pred)`, `any(xs, pred)`, `all(xs, pred)`, `equal`, `reverse`, `rotateLeft`, `insert(xs, i, x)`,
`removeAt(xs, i): T`, `swapRemove`, `retain(xs, pred)`, `dedup`,
`copyRange(xs, a, b)`, `copyOf`, `extend`, `filter`, `mapTo`.
**Set[T: Eq + Hash]** (a Map to Unit): `newSet`, `setOf(&xs)`, `size`,
`isEmpty`, `has`, `add(x): bool`, `remove(&x): bool`, `clear`, `at(i)`,
`each(f)`, `filter(pred): Set[T]`, `items()`, `sorted()`, `union`,
`intersect`, `minus`, `isSubset`, `eq`.
**Heap[T: Ord]** (min-heap): `newHeap`, `push`, `pop(): Option[T]`, `peek`,
`size`, `isEmpty`.

### strings

`lastIndexOf`, `indexOfByte`, `count`, `equalFold`, `splitOn(s, sep: str)`,
`splitN`, `fields`, `cut(s, sep): Option[Cut]`, `join(&parts, sep)`,
`replaceAll`, `replaceN`, `trimLeft`, `trimRight`, `trimPrefix`, `trimSuffix`,
`toUpper`, `toLower` (ASCII), `isLetter`, `isAlnum`; `parseInt(s)` and
`parseIntBase(s, base)` return `Result[int, ParseError]` (`Empty`,
`Invalid(at)`, `OutOfRange`); `formatInt(n, base)`, `hex`, `hex64`, `zeroPad`,
`groupDigits(n, sep)`, `padLeft`, `padRight`, `center`. UTF-8: `decodeAt(s,
i): Decoded {rune, size, ok}` (strict; an invalid part gives U+FFFD and takes
its maximal subpart, as Rust's `from_utf8_lossy`), `runes`, `runeCount`,
`isValidUtf8`, `lossy(s)` (invalid parts replaced by U+FFFD), `addRune(mut
&buf, r)`, `fromRune`, `fromRunes`, `displayWidth(s)` and `runeWidth(r)`
(terminal columns per unicode-width 0.1.14: wide East Asian and emoji 2,
combining marks, format and control characters 0).

### io, fs, os, time

- **io**: `IoError {errno, op, path}` with `toStr` (`"open x: No such file or
  directory"`), `errorText(errno)`, `read(path): Result[str, IoError]`,
  `readLines`, `readStdin`, `splitLines`, `write(path, data)`,
  `appendTo(path, data)`. `Writer` (`stdout()`, `stderr()`, `create(path)`,
  `openAppend(path)`): `write`, `writeln`, `writeByte`, `flush(): Result`,
  `close(): Result`; flushed and closed when dropped (errors then are lost); the
first error of an implicit flush (buffer over 64 KiB) is kept for the next
`flush` or `close`, and a stdout writer's `flush` also flushes the runtime's
buffer behind `print`. `flushStdout(): Result` does that and reports the first
failed write to stdout since the last call (so a program can see `ENOSPC` on a
full disk). `isTerminal(fd)`, `terminalWidth(fd): Option[int]`.
- **fs**: `stat`/`lstat(path): Result[Stat, IoError]` (`size mode mtime
  mtimeNs nlink uid gid ino blocks`, `kind() isDir() isFile() isLink()
  perm()`), `Kind` (`File Dir Link Fifo Socket CharDev BlockDev Other`,
  `kindOfMode(mode)`), `exists`, `isDir`, `isFile`, `list(path):
  Result[[]Entry]` (`name`, `kind` from readdir, or lstat when readdir does
  not say), `listNames` (sorted), `mkdir`, `mkdirAll`, `remove`, `removeAll`
  (does not follow links), `rename`, `symlink`, `readlink`, `chmod`; paths: `join`, `baseName`, `dirName`, `extension`.
- **os**: `argv()`, `arguments()`, `env(name): Option[str]`, `envOr`,
  `setEnv`, `unsetEnv`, `pid`, `cwd`, `setCwd`, `quit(code)`.
- **time**: `Time {sec, nsec}` (`now`, `fromNanos`, `cmp`, `eq`, `toStr`,
  `addMillis`, `millisUntil`), `unixSeconds`, `unixMillis`, `monotonicNs`,
  `sleepMs`; `Date {year, month, day}`: `dateOf(sec)`, `dateOfDays`,
  `daysOfDate`, `weekday`, `isLeapYear`, `daysInMonth`, `parseDate`,
  `isoDate`, `isoDateTime`.

### ffi

C memory for modules that declare externs (SPEC 12), used by sqlite and
postgres. `Mem` is owned memory C may use, freed when dropped:
`alloc(n)` (zeroed, never a null pointer), `cstr(s)` (the bytes and a NUL),
`copyBytes(s)`; `ptr()`, `size()`, index-checked `get`/`set` (bytes),
`getI32`/`setI32`, `getPtr`/`setPtr`, `toStr()`. For memory C owns, at a
pointer C returned: `peek`/`poke`, `peekI32`/`pokeI32`,
`peekPtr`/`pokePtr` (element `i`) and `bytes(p, n)` (a copy); these are as
unchecked as an extern call, so wrap them like one. A null pointer is the
built-in `nullPtr()`; arrays and slices of integers, `float` and `ptr` can
be passed to C directly (`&[]T`, `mut &[]T` extern parameters), as postgres
does for `PQexecParams`'s types and values.

### sqldialect, sqlite, postgres

Database adapters over the C client libraries (a program that imports one is
dynamically linked to libsqlite3.so.0 or libpq.so.5; the tests that need one
are skipped when it is not installed). Examples: `examples/sqlite_ledger.obx`,
`examples/postgres_ledger.obx`.

- **sqldialect** (shared): `Value` (`Null Int Float Bool Text Bytes Dec`,
  `toStr` as an SQL literal), `DbError {code, state, msg}` (`"error 19:
  UNIQUE constraint failed: t.id"`, `"error 7 (23505): duplicate key ..."`)
  and the adapters' own codes `E_INPUT E_NULL E_TYPE E_NONFINITE E_INEXACT
  E_DEC_*` (1000 and up, above every SQLite and libpq code); `Sql` fragments
  (`sql(text)`, `add`, `arg(v)`, `of(parts, args)`, `render(dialect)` with
  `?`, `$n`, `@pn`, `:n` placeholders); `parseDecimal`, `decimalText`,
  `fitDecimal(d, p, s)`.
- **sqlite**: `open(path)`, `openWith(path, flags)`; `Db`: `exec(script)`,
  `prepare(sql)` (one statement), `query(sql, &args)`, `querySql(&sql)`,
  `run(sql, &args)` (rows changed), `changes`, `lastInsertId`,
  `inTransaction`, `transaction(&fn)`, `close()`. `Stmt`: `step(): bool`,
  `reset`, `bind(i, v)`, `bindAll`, `bindDecimal(i, d, p, s)`,
  `columnCount`, `columnName`, and per column `isNull int bool float text
  bytes decimal decimalOrNull value`. Decimals are stored as exact TEXT.
- **postgres**: `connect(conninfo)`, `libVersion()`; `Conn`: `exec(script)`,
  `query(sql, &args)` (`PQexecParams`, text format, each parameter with
  its type's OID: int8, float8, bool, text, bytea, numeric; NULL untyped),
  `querySql`, `transaction`, `serverVersion`, `close()`;
  `decimalParam(d, p, s)`. `Rows` (outlive their connection): `rowCount`,
  `columnCount`, `affected`, `columnName`, `columnType` (OID),
  `columnTypmod`, `decimalType` (numeric precision and scale), and per cell
  `isNull int bool float text bytes decimal decimalOrNull value`. A failed
  statement is an `Err` with the SQLSTATE in `state`.

Every getter is exact or an error: no NULL becomes 0 or "" (`E_NULL`; use
`isNull`, `value` or `decimalOrNull`), no REAL becomes an int or a decimal
(`E_INEXACT`), and text is never parsed as a number it does not spell.
Handles (`Db`, `Stmt`, `Conn`, `Rows`) own their C object and free it when
dropped; `close()` consumes the handle, so there is no closed state to
check, and `openHandles()` counts what is live (the tests end at 0). Text
with a NUL byte cannot cross the C boundary and is refused with `E_INPUT`
before any call; bytes travel as a blob (SQLite) or bytea hex (PostgreSQL).

## What the library uses

Intrinsics: `strByte`, `strSlice`, `strFromBytes`, `strToBytes`, `intToStr`,
`i128ToStr`, `u128ToStr`, `u64ToStr`, `floatToStr`, `parseFloat`, `writeFd`,
`readFile`, `args`, `getenv`, `strFromC`, and the built-ins `isNull`, `exit`,
`panic`. No new intrinsic was added.

C functions through `extern "c"`: libm (`sqrt cbrt pow exp log log2 log10 sin
cos tan asin acos atan atan2 sinh cosh tanh floor ceil trunc round rint fabs
fmod hypot copysign`), libc (`strerror close opendir readdir closedir mkdir
rmdir unlink rename symlink chmod setenv unsetenv getpid chdir usleep`), and
these helpers in `compiler/rt.h` for what the language cannot express (a C struct,
errno, a static buffer):

| Helper | Does |
|---|---|
| `obx_errno()` | `errno` |
| `obx_dirent_name(d)`, `obx_dirent_type(d)` | fields of a `struct dirent*` |
| `obx_stat(path, follow)`, `obx_stat_field(i)` | stat/lstat into a static buffer, then its fields |
| `obx_open(path, how)` | open for read, write (truncate), append, or create-new; fd or -errno |
| `obx_clock_ns(which)` | `clock_gettime` realtime or monotonic, in ns |
| `obx_getcwd()` | `getcwd` into a static buffer |
| `obxlib_readlink(path)`, `obxlib_readlink_text()` | readlink into a static buffer; length or -errno, then the text |
| `obxlib_flush_out()` | flush the runtime's stdout buffer; errno of the first failed write since the last call, or 0 |
| `obxlib_isatty(fd)`, `obxlib_term_cols(fd)` | `isatty`; `TIOCGWINSZ` columns or 0 |
| `obxlib_alloc(n)`, `obxlib_free(p)` | a zeroed C buffer from the runtime's allocator (counted by `OBX_CHECK=1`); for `ffi` |
| `obxlib_peek(p, i)`, `obxlib_poke(p, i, b)` | read or write byte `i` of C memory; for `ffi` |
| `obxlib_peek_i32`, `obxlib_poke_i32`, `obxlib_peek_ptr`, `obxlib_poke_ptr` | element `i` of a C `int32_t[]` or `void*[]`; for `ffi` |
| `obxlib_copy_in(dst, at, src, n)`, `obxlib_copy_out(dst, src, at, n)` | `memcpy` between C memory and an obx byte array; for `ffi` |

The `obxlib_` helpers are not `obx_` names because the compiler lets
externs name only the `obx_` helpers it knows (sema's `cHelper`); any other
name defined in both runtimes works. The native backend always links the
`obx_` and `obxlib_` names to its own runtime, also in a dynamically linked
program; any other C name comes from the program's libraries.

The self-hosted compiler's runtime needs the same helpers (or the language a
way to read C structs).

## Slices

The algorithms that do not grow or shrink an array take a slice (SPEC 4.2a),
so they also work on part of one; `&xs` / `mut &xs` still pass the whole
array:

- std: `sort` (and its `siftDown`), `sumDecimals`.
- collections: `sortBy`, `sortStable`, `isSorted`, `binarySearch`,
  `lowerBound`, `upperBound`, `contains`, `find`, `findBy`, `count`, `any`, `all`, `equal`,
  `reverse`, `rotateLeft` (which reverses subslices), `copyOf` (`copyRange(xs,
  a, b)` is `copyOf(xs[a..b])`), `extend` (its `ys`), `filter`, `mapTo`,
  `setOf`.
- money: `allocateBy` (weights), `sum`.
- strings: `join`, `fromRunes`.

Functions that append, pop or insert (`insert`, `removeAt`, `swapRemove`,
`retain`, `dedup`, `extend`'s `xs`) keep `mut &[]T`.

`s[a..b]` on a str shares its bytes, so taking substrings costs no copy;
`strSlice(s, a, b)` is the same operation. `Decimal.parse(text[a..b])` parses
part of a larger text without copying it. No library code relies on a str
being NUL-terminated: an extern call gets the str's bytes when a NUL follows
them and a NUL-terminated temporary copy otherwise (freed after the call).

## Gaps

- No traps carry a source position: a Decimal or Money stop prints `obx:
  <message>` and exits 10 from inside the library.
- `float` has no `hash` (and so cannot be a Map key).
- `fixed` and `toDecimal` work from the shortest decimal form of a float, not
  its exact binary value (deliberate, but unlike C's printf).
- No streaming input: files and stdin are read whole. No line reader over a
  file descriptor, no seek, no file handles for reading.
- `fs.list` has no error for a failure in the middle of `readdir`; `Stat` has
  no atime/ctime fields (the helper can read them); no `chown`, `utimes`,
  `realpath`.
- `time` is UTC only (no time zones), with millisecond helpers only.
- `strings` case mapping is ASCII only.
- No `Deque`, no `BTreeMap`/ordered map; `Set` iterates in insertion order
  until a remove.
- Errors are errno ints in `os` (setEnv, setCwd) but `IoError` in `io` and
  `fs`.
