# obx library

The standard library, written as ordinary obx code over a few intrinsics.
`std.obx` is the prelude: every program sees its `pub` names unqualified.
The other modules are imported by name, `import "strings"`, and used
qualified, `strings.join(...)`; this directory is a default import root of
the reference compiler (after the importing file's directory and any `-L`
roots, so a local `money.obx` shadows the library's).

    python3 obx/lib/run_tests.py -j 8        # the library tests (OBX_CHECK=1)
    python3 obx/lib/tests/gen_decimal_random.py   # regenerate decimal_random.obx

Tests are in `tests/run` and `tests/error`, in the conformance suite's format
(`// expect:`, `// exit:`, `// stdin:`, `// error:`); the runner is the
conformance runner pointed at this directory, and every run test is checked
for leaks.

## Modules

| Module | Lines | Contents |
|---|---:|---|
| `std.obx` | 1,420 | Option, Result, Unit; Eq/Ord/Hash/Show for the primitives; print/println/eprint/eprintln; min/max; float text and math methods; StrBuf and basic string helpers; Decimal; sort; Map |
| `money.obx` | 200 | Currency, USD/EUR/GBP/JPY/CHF, Money[C] |
| `math.obx` | 130 | float constants, parse, fixed, toDecimal, near |
| `collections.obx` | 440 | stable sorts, searching, rearranging, copying, Set, Heap |
| `strings.obx` | 490 | search, split/join, replace, trim, case, int parse/format, padding, UTF-8 |
| `io.obx` | 160 | IoError, whole-file read/write/append, readLines, readStdin, buffered Writer |
| `fs.obx` | 260 | stat/lstat, list, mkdirAll, remove/removeAll, rename, symlink, chmod, paths |
| `os.obx` | 60 | arguments, environment, pid, working directory, quit |
| `time.obx` | 160 | wall and monotonic clocks, sleep, UTC dates, ISO 8601 text |

### std (prelude)

- **Option[T]** `Some(T)`/`None`: `isSome`, `isNone`, `unwrap`, `expect(msg)`,
  `unwrapOr(d)`, `eq` (T: Eq), `toStr` (T: Show). **Result[T, E]** `Ok`/`Err`
  (the `try` lang item): `isOk`, `isErr`, `unwrap`, `expect`, `unwrapOr`,
  `ok()`, `err()`, `toStr`. **Unit**.
- **Eq, Ord, Hash, Show** with implementations for `int i32 u32 u64 byte i128
  u128 float bool str` (float has no `hash`). `min`, `max` for `T: Ord + copy`.
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
- **Decimal** (obo's semantics): a coefficient below 10^38, an exponent and a
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
  `sumDecimals`. **RoundingMode**: `HALF_EVEN HALF_AWAY HALF_TOWARD_ZERO
  TOWARD_ZERO AWAY FLOOR CEILING UNNECESSARY`. **DecError** with obo's codes:
  DecOverflow 1, DecInexact 3, DecInvalid 4, DecDivZero 5, DecRange 6,
  DecPrecision 7, DecExponent 8, DecDisplay 9.
- **sort(xs: mut &[]T)** for `T: Ord`: heap sort, in place, not stable.
- **Map[K: Eq + Hash, V]**: insertion-ordered entries plus an open-addressing
  index. `newMap`, `size`, `has(&k)`, `get(&k): Option[V]` and `getOr(&k, d)`
  (V: copy), `at(k): &V`, `atMut(k): mut &V`, `atOrPut(k, d): mut &V`,
  `put(k, v)`, `remove(&k): Option[V]`, `clear`, `keyAt(i)`, `valAt(i)`,
  `valAtMut(i)` for walking, `keys()`/`values()` (copy types). `remove` moves
  the last entry into the hole.

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

`sortBy(xs, cmp: fn(&T, &T): int)` and `sortStable(xs)`: stable merge sorts
over an index array followed by an in-place permutation, so owned elements
work. `isSorted`, `binarySearch(xs, &x): Result[int, int]` (Ok index or Err
insertion point), `lowerBound`, `upperBound`, `find`, `findBy`, `contains`,
`count(xs, pred)`, `equal`, `reverse`, `rotateLeft`, `insert(xs, i, x)`,
`removeAt(xs, i): T`, `swapRemove`, `retain(xs, pred)`, `dedup`,
`copyRange(xs, a, b)`, `copyOf`, `extend`, `filter`, `mapTo`.
**Set[T: Eq + Hash]** (a Map to Unit): `newSet`, `setOf(&xs)`, `size`,
`isEmpty`, `has`, `add(x): bool`, `remove(&x): bool`, `clear`, `at(i)`,
`items()`, `sorted()`, `union`, `intersect`, `minus`, `isSubset`, `eq`.
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
i): Decoded {rune, size}` (strict; invalid bytes give U+FFFD, size 1),
`runes`, `runeCount`, `isValidUtf8`, `addRune(mut &buf, r)`, `fromRune`,
`fromRunes`, `displayWidth` (rough terminal columns: wide East Asian and
emoji 2, combining marks 0).

### io, fs, os, time

- **io**: `IoError {errno, op, path}` with `toStr` (`"open x: No such file or
  directory"`), `errorText(errno)`, `read(path): Result[str, IoError]`,
  `readLines`, `readStdin`, `splitLines`, `write(path, data)`,
  `appendTo(path, data)`. `Writer` (`stdout()`, `stderr()`, `create(path)`,
  `openAppend(path)`): `write`, `writeln`, `writeByte`, `flush(): Result`,
  `close(): Result`; flushed and closed when dropped (errors then are lost).
- **fs**: `stat`/`lstat(path): Result[Stat, IoError]` (`size mode mtime
  mtimeNs nlink uid gid ino blocks`, `kind() isDir() isFile() isLink()
  perm()`), `exists`, `isDir`, `isFile`, `list(path): Result[[]Entry]`
  (`name`, `kind` from readdir), `listNames` (sorted), `mkdirOne`,
  `mkdirAll`, `remove`, `removeAll` (does not follow links), `renamePath`,
  `makeLink`, `setPerm`; paths: `join`, `baseName`, `dirName`, `extension`.
- **os**: `argv()`, `arguments()`, `env(name): Option[str]`, `envOr`,
  `setEnv`, `unsetEnv`, `pid`, `cwd`, `setCwd`, `quit(code)`.
- **time**: `Time {sec, nsec}` (`now`, `fromNanos`, `cmp`, `eq`, `toStr`,
  `addMillis`, `millisUntil`), `unixSeconds`, `unixMillis`, `monotonicNs`,
  `sleepMs`; `Date {year, month, day}`: `dateOf(sec)`, `dateOfDays`,
  `daysOfDate`, `weekday`, `isLeapYear`, `daysInMonth`, `parseDate`,
  `isoDate`, `isoDateTime`.

## What the library uses

Intrinsics: `strByte`, `strSlice`, `strFromBytes`, `strToBytes`, `intToStr`,
`i128ToStr`, `u128ToStr`, `u64ToStr`, `floatToStr`, `parseFloat`, `writeFd`,
`readFile`, `args`, `getenv`, `strFromC`, and the built-ins `isNull`, `exit`,
`panic`. No new intrinsic was added.

C functions through `extern "c"`: libm (`sqrt cbrt pow exp log log2 log10 sin
cos tan asin acos atan atan2 sinh cosh tanh floor ceil trunc round rint fabs
fmod hypot copysign`), libc (`strerror close opendir readdir closedir mkdir
rmdir unlink rename symlink chmod setenv unsetenv getpid chdir usleep`), and
these helpers in `ref/rt.h` for what the language cannot express (a C struct,
errno, a static buffer):

| Helper | Does |
|---|---|
| `obx_errno()` | `errno` |
| `obx_dirent_name(d)`, `obx_dirent_type(d)` | fields of a `struct dirent*` |
| `obx_stat(path, follow)`, `obx_stat_field(i)` | stat/lstat into a static buffer, then its fields |
| `obx_open(path, how)` | open for read, write (truncate), append, or create-new; fd or -errno |
| `obx_clock_ns(which)` | `clock_gettime` realtime or monotonic, in ns |
| `obx_getcwd()` | `getcwd` into a static buffer |

The self-hosted compiler's runtime needs the same helpers (or the language a
way to read C structs).

## Slices: functions to change

SPEC 4.2a adds `&[T]` / `mut &[T]` slices and `s[a..b]` on str; the
reference compiler does not implement them yet. Each function below is marked
`// slice:` in its source and should take a slice instead of `&[]T` /
`mut &[]T` (callers passing `&xs` keep working), or use one internally:

- std: `StrBuf.add` (one copy of a byte run), `Decimal.parse` (a byte range
  of a larger text), `sumDecimals`, `sort`.
- collections: `sortBy`, `sortStable`, `isSorted`, `binarySearch`,
  `lowerBound`, `upperBound`, `contains`, `find`, `findBy`, `count`, `equal`,
  `reverse`, `rotateLeft` (and its `reverseRange`, which becomes
  `reverse(xs[a..b])`), `copyRange` (becomes `copyOf(xs[a..b])`), `copyOf`,
  `extend` (ys), `filter`, `mapTo`, `setOf`.
- money: `allocateBy` (weights), `sum`.
- strings: `join`, `fromRunes`.

Substrings are always taken with `strSlice` (a copy today, a shared view with
slices); no library code relies on a str being NUL-terminated. C calls get a
NUL-terminated copy from the compiler (`rt_cstr` today relies on the str's own
terminator, which must change when str slices share bytes).

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
  `realpath`, `readlink`.
- `time` is UTC only (no time zones), with millisecond helpers only.
- `strings` case mapping and `displayWidth` are ASCII / rough tables, not
  Unicode data.
- No `Deque`, no `BTreeMap`/ordered map; `Set` iterates in insertion order
  until a remove.
- Errors are errno ints in `os` (setEnv, setCwd) but `IoError` in `io` and
  `fs`.
