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
| `std.obx` | 1,715 | Option, Result, Unit, Where; Eq/Ord/Hash/Show for the primitives (keyed hashing, SipHash-1-3); print/println/eprint/eprintln; min/max; float text and math methods; StrBuf and basic string helpers; Decimal; sort; Map |
| `money.obx` | 200 | Currency, USD/EUR/GBP/JPY/CHF, Money[C] |
| `math.obx` | 130 | float constants, parse, fixed, toDecimal, near |
| `collections.obx` | 390 | stable sorts, searching, rearranging, copying, Set, Heap |
| `strings.obx` | 490 | search, split/join, replace, trim, case, int parse/format, padding, UTF-8 |
| `io.obx` | 347 | IoError, whole-file read/write/append, readLines, readStdin, streaming Reader, buffered Writer |
| `fs.obx` | 260 | stat/lstat, list, mkdirAll, remove/removeAll, rename, symlink, chmod, paths |
| `os.obx` | 60 | arguments, environment, pid, working directory, quit |
| `time.obx` | 160 | wall and monotonic clocks, sleep, UTC dates, ISO 8601 text |
| `sqldialect.obx` | 260 | Value, DbError and its codes, SQL fragments with placeholders per dialect, exact decimal text and (p, s) checks |
| `ffi.obx` | 140 | C memory for modules that call C: owned `Mem` (alloc, cstr, copyBytes), unchecked peek/poke/bytes |
| `sqlite.obx` | 440 | SQLite 3 through libsqlite3: Db, Stmt |
| `postgres.obx` | 690 | PostgreSQL through libpq, asynchronous for tasks: Conn, Rows, Pool, Lease |
| `mssql.obx` | 1,050 | Microsoft SQL Server through ODBC (libodbc): Conn, Stmt, strict UTF-8/UTF-16 |
| `task.obx` | 644 | concurrent tasks on one thread: Scope, Future, Promise, Shared, Frozen, all/any, yield, cancellation, sleep, deadlines; signals; worker processes |
| `net.obx` | 910 | TCP over IPv4 and IPv6 for tasks: listen (also with SO_REUSEPORT), accept, connect, read, write; name lookup and dial |
| `tls.obx` | 950 | TLS 1.2 and 1.3 through OpenSSL 3: an engine over memory buffers, and connections over net sockets for tasks; verification, pins, client certificates, ALPN |
| `proc.obx` | 614 | other programs: start with pipes, a Command's environment, directory and streams; wait on a pidfd as a task; output, communicate, run; cancellation sends SIGTERM then SIGKILL; PATH lookup in the child's environment; optional process group |
| `url.obx` | 550 | URLs for http and https (RFC 3986): parse, resolve, percent-encoding, queries |
| `http.obx` | 1,690 | an HTTP/1.1 client for http and https (pool, strict framing, redirects, retries, deadlines) and the head parsing the server shares |
| `httpserver.obx` | 2,940 | an HTTP/1.1 server: Go 1.22 routing, middleware, streaming bodies, strict parsing, every limit and timeout, cancellation on hangup, request ids, access log, graceful drain |
| `markdown.obx` | 2,630 | CommonMark 0.31.2: parse to a tree (arena of nodes, walker) and render HTML as cmark does; with the generated data modules `markdown_entities.obx` (HTML5 entities) and `markdown_unicode.obx` (punctuation, space, case folding) |
| `regex.obx` | 2,130 | regular expressions (RE2 syntax, linear time; a port of Go's regexp): compile, find, findAll, captures, replace, split, quote |
| `regex_dfa.obx` | 1,420 | the regex engine's lazy DFA (after RE2's) and literal searches (after Rust's regex) |
| `regex_syntax.obx` | 3,060 | the regex parser, simplifier and compiler to a program, forward and reversed (Go's regexp/syntax) |
| `regex_unicode.obx` | 2,020 | generated (`gen_regex_unicode.py`): Unicode 15.0.0 categories, scripts and simple case folding |
| `json.obx` | 1,930 | JSON values (shared, copy on write; objects in insertion order), parsing with positioned errors, a decoder for sequences, compact and indented writing with jq's options |

### std (prelude)

- **Option[T]** `Some(T)`/`None`: `isSome`, `isNone`, `unwrap`, `expect(msg)`,
  `unwrapOr(d)`, `map(&f)`, `eq` (T: Eq), `toStr` (T: Show). **Result[T, E]**
  `Ok`/`Err` (the `try` lang item): `isOk`, `isErr`, `unwrap`, `expect`,
  `unwrapOr`, `unwrapErr`, `expectErr(msg)`, `map(&f)` (the Ok value),
  `mapErr(&f)` (the Err value: `try r.mapErr(toError)`), `ok()`, `err()`,
  `toStr`. **Unit**.
- **Where** (a lang item, SPEC 15): a call site, the text `"file:line"` in
  the program's static data (`toStr`, `cstr()` for C). A call of a library
  function whose last parameter is a `Where` may leave it out: the compiler
  passes the call's location. `here(): Where` is the location of its own call
  (or the `Where` passed to it). `task` uses it to name the user's line when a
  task parks inside `with` or deadlocks.
- **Eq, Ord, Hash, Show** with implementations for `int i32 u32 u64 byte i128
  u128 float bool str` (float has no `hash`). `min`, `max` for `T: Ord + copy`.
- **Keyed hashing.** A `Map` keyed by input (query parameters, header names,
  JSON keys, ids) must not let the sender pick keys that collide (hash
  flooding: n colliding keys cost n² probes). So `str`, `i128`, `u128` and
  `Decimal` (and so `Money`) hash with **SipHash-1-3** under a 128-bit key
  chosen at random per process, as Rust's std does; a seeded FNV or Murmur
  would not do, their collisions are found without the seed (Aumasson and
  Bernstein 2012). The key is derived (with SipHash) from the 16 random
  bytes the kernel gives every process (`AT_RANDOM`, no system call; glibc
  uses those bytes for its stack canary, hence derived, never used as is).
  The 64-bit integers keep a multiplicative hash, a bijection, and `Map`
  mixes a per-process seed into the slot of every hash, so integer keys land
  in slots no sender can predict either. Workers forked by `task.workers`
  share their supervisor's key; another run gets another key. `Map` keeps
  insertion order, so the key changes no program's output; a hash value is
  never meant to be printed or stored. `sipHash13(k0, k1, &s)` is SipHash-1-3
  of a string's bytes under a key you give (tests, or a hash you need to be
  the same across runs).
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
- **StrBuf**: `newStrBuf`, `add` (one copy of the string's bytes,
  `appendStr`), `addByte`, `addInt`, `addLine`, `size`, `clear`, `toStr`. Strings: `substr`, `indexOf(s, sub, from)`, `startsWith`,
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
  types). `remove` moves the last entry into the hole. Slots come from
  `(hash ^ seed) * GOLDEN` (the seed per process, above).
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
  directory"`), `isCancelled()` and `isTimeout()` (`ECANCELED`, `ETIMEDOUT`:
  a `net` call cut short by `task`), `errorText(errno)`, `read(path): Result[str, IoError]`,
  `readLines`, `readStdin`, `splitLines`, `write(path, data)`,
  `appendTo(path, data)`. `Writer` (`stdout()`, `stderr()`, `create(path)`,
  `openAppend(path)`): `write`, `writeln`, `writeByte`, `flush(): Result`,
  `close(): Result`; flushed and closed when dropped (errors then are lost); the
first error of an implicit flush (buffer over 64 KiB) is kept for the next
`flush` or `close`, and a stdout writer's `flush` also flushes the runtime's
buffer behind `print`. `flushStdout(): Result` does that and reports the first
failed write to stdout since the last call (so a program can see `ENOSPC` on a
full disk). `Reader` (`stdin()`, `open(path): Result`): `read(dst: mut
&[byte]): Result[int, IoError]` fills the front of `dst` (0 at the end),
`readLine(): Result[Option[str], IoError]` (no `"\n"`, no `"\r"` before it,
as `splitLines`), `seek(offset, whence: Whence): Result[int, IoError]` (`Whence`:
`Start Current End`; the new offset; `ESPIPE` for a pipe), `close(): Result`; the end is not sticky (a later read tries
again: a growing file, a terminal), `EINTR` is retried, a file reader is
closed when dropped. `isTerminal(fd)`, `terminalWidth(fd): Option[int]`.
- **fs**: `stat`/`lstat(path): Result[Stat, IoError]` (`size mode mtime
  mtimeNs nlink uid gid ino dev blocks`, `kind() isDir() isFile() isLink()
  perm()`), `Kind` (`File Dir Link Fifo Socket CharDev BlockDev Other`,
  `kindOfMode(mode)`), `exists`, `isDir`, `isFile`, `list(path):
  Result[[]Entry]` (`name`, `kind` from readdir, or lstat when readdir does
  not say), `listNames` (sorted), `mkdir`, `mkdirAll`, `remove`, `removeAll`
  (does not follow links), `rename`, `symlink`, `readlink`, `chmod`; paths: `join`, `baseName`, `dirName`, `extension`.
- **os**: `argv()`, `arguments()`, `env(name): Option[str]`, `envOr`,
  `setEnv`, `unsetEnv`, `pid`, `cwd`, `setCwd`, `quit(code)`.
- **time**: `Time {sec, nsec}` (`now`, `fromNanos`, `cmp`, `eq`, `toStr`,
  `addMillis`, `millisUntil`), `unixSeconds`, `unixMillis`, `monotonicNs`,
  `sleepMs` (blocks the thread, every task with it: in tasks use
  `task.sleep`); `Date {year, month, day}`: `dateOf(sec)`, `dateOfDays`,
  `daysOfDate`, `weekday`, `isLeapYear`, `daysInMonth`, `parseDate`,
  `isoDate`, `isoDateTime`.

### ffi

C memory for modules that declare externs (SPEC 12), used by sqlite,
postgres and mssql (its bound parameters). `Mem` is owned memory C may
use, freed when dropped: `alloc(n)` (zeroed, never a null pointer),
`cstr(s)` (the bytes and a NUL), `copyBytes(s)`; `ptr()`, `size()`, index-checked `get`/`set` (bytes),
`getI32`/`setI32`, `getPtr`/`setPtr`, `toStr()`. For memory C owns, at a
pointer C returned: `peek`/`poke`, `peekI32`/`pokeI32`,
`peekPtr`/`pokePtr` (element `i`) and `bytes(p, n)` (a copy); these are as
unchecked as an extern call, so wrap them like one. A null pointer is the
built-in `nullPtr()`; arrays and slices of integers, `float` and `ptr` can
be passed to C directly (`&[]T`, `mut &[]T` extern parameters), as postgres
does for `PQexecParams`'s types and values.

### task

Concurrent tasks on one thread (SPEC 16; design/concurrency.md, stages
C1a and C1b): stackful coroutines, scheduled cooperatively, with one epoll
loop for the sockets of `net` and the timers; signals as events of that
loop and worker processes for several cores (stage C2), at the end.

Every function below that can park (`scope`, `scoped`, `wait`, `all`, `any`,
`yield`, `sleep`) and `with` has a last parameter `w: Where`, the caller's location,
which a call leaves out (SPEC 15): it is not shown in the signatures here. A
call through a function value passes it (`here()`), and so does a function
of the program that wants its own caller's line reported (`fn waitAt(f:
task.Future[int], w: Where)` calls `f.wait(w)`).

- `scope(f: &fn(mut &Scope))` runs `f` with a new scope and returns when
  every task started on it has finished; `scoped[R](f: &fn(mut &Scope): R):
  R` is the same for an `f` with a result.
- `s.start[A, T](f: fn(A): T, arg: A): Future[T]` (on `s: mut &Scope`)
  starts `f(arg)`. `f` is a plain function value and `arg` is moved in.
  `s.spawn[A, T](f, arg)` starts it with no future: nobody waits for it, its
  result is dropped, and it is not cancelled until the scope's task is (an
  accept loop's connections; dropping the futures of `start` would cancel
  them). The scope waits for it either way.
- `Future[T]` (owned): `wait(): Result[T, Cancelled]` consumes it and parks
  until the task has finished. Dropped without waiting, it cancels its task.
- `all[T](fs: []Future[T]): Result[[]T, Cancelled]` waits for each in
  order; on an error the rest are dropped (cancelled).
  `any[T](fs: []Future[T]): Result[T, Cancelled]` gives the first result,
  cancels the others and waits until they have finished (`fs` must not be
  empty).
- `promise[T](): Handoff[T]`: `Handoff { promise: Promise[T]; future:
  Future[T] }`, the two ends of one hand-over (`let task.Handoff{promise: p,
  future: f} = task.promise[int]()`). `p.complete(v: T)` consumes the promise and never
  parks; a promise dropped without it makes `wait` give
  `Err(Cancelled.Abandoned)`.
- `yield(): Result[Unit, Cancelled]` lets the other runnable tasks run;
  `cancelled(): bool` tells whether the running task has been cancelled.
- `sleep(ms: int): Result[Unit, Cancelled]` parks for `ms` milliseconds
  (none when `ms <= 0`); `Err(Cancelled)` when the task is cancelled (at
  once, or while it sleeps), `Err(Timeout)` when the deadline of an
  enclosing `within` comes first.
- `within[R](ms: int, f: &fn(): R): R` runs `f` with a deadline `ms`
  milliseconds from now; the earlier one wins when nested, and tasks started
  during `f` inherit it. Every call that can park in them returns
  `Err(Timeout)` (`ETIMEDOUT` from `net`) once it has passed, so `f` ends
  through its `Result`s: `task.within(500, fn(): Result[int, io.IoError] {
  return c.read(mut &buf) })` is a read with a timeout, and a deadline
  around several calls bounds them together. `f` returns a value (a closure
  with no result cannot give `R`); `within` itself never parks.
- `Cancelled` is `value closed { Cancelled; Abandoned; Timeout }` (`eq`,
  `toStr`).
- `shared[T](v: T): Shared[T]`: a counted copy handle (a lang item, SPEC 15);
  `sh.with(f: &fn(mut &T))` is the only access. Parking inside `with`, or a
  `with` on a value whose `with` is open, is exit 13. If every task is
  parked the program stops with exit 14 (deadlock). The messages name the
  user's lines (SPEC 14): `obx: a task parked inside Shared.with (the with
  at a.obx:37) at a.obx:41`, `obx: Shared.with on a value whose with is
  already open (the with at a.obx:30) at a.obx:35`, `obx: deadlock: every
  task is parked at a.obx:41`.
- `freeze[T](v: T): Frozen[T]`: a counted copy handle, like `Shared`, to a
  value that never changes again. `fr.read(f: &fn(&T): R): R` lends it as
  `&T`, and `f` **may park**: no writer exists, and the handle that `read`
  borrows keeps a count, so the value cannot go away meanwhile (the reason
  set-once globals are sound). Any number of tasks may be inside `read` at
  once. `fr.get(): T` copies a copy value. The value is dropped with the last
  handle. For what every connection's task reads and none writes: a router,
  configuration, templates. A forked worker gets its own copy with its own
  count. It is a closure, not `get(): &T`, because a result may only borrow
  a place rooted at the receiver (SPEC 10 rule 5) and the value lives in the
  runtime's box.

The scheduler waits in `epoll_wait` only when no task is runnable, and checks
sockets and timers every 64 switches otherwise. A task parked in `sleep`, on
a socket or in a blocking C call is never a deadlock; a program whose tasks
all wait on futures nobody will complete still is (exit 14).

**Blocking C calls** (SPEC 12, stage C1c). A module marks an extern that may
take long `blocking`; while another task could run, a call parks its task
and runs on a helper thread. The pool starts with the first such call:
`OBX_HELPERS` threads (default 4, at most 64; `OBX_HELPERS=0` runs every
call on the program's thread, as when nothing else could run). A round trip
through the pool costs about 0.4 us (C backend) to 0.8 us (native) while the
helpers and the event loop spin for each other (50 us each, not when the
process may use only one CPU: then about 6 us). The library's blocking
calls: sqlite's `sqlite3_step` and `sqlite3_exec`; postgres's
`PQconnectStart`, `PQconnectPoll` and `PQcancel`; mssql's ODBC calls.

A blocking call is not cut short by itself when its task is cancelled or
its deadline passes: the task sees that at its next call that can park.
`onCancelCall(f: fn(ptr), arg: ptr): CancelCall` registers a C function
that makes the call return early; while the guard lives, if the task is
cancelled or a `within` deadline passes while it is parked in a blocking
call, the runtime calls `f(arg)` once, on the program's thread, while the
call goes on running on its helper. `f` must be safe to call at the same
time as the call: `sqlite` registers `sqlite3_interrupt` around its steps
(the step returns `SQLITE_INTERRUPT`, error 9) and `mssql` `SQLCancel` around
statement calls. With a registration and a deadline a blocking call always
runs on a helper, so that the deadline is seen. Registrations nest; the
innermost counts.

**Signals** (SPEC 16, stage C2). A watched signal is an event of the loop
instead of its default action (for `SIGTERM` and `SIGINT`, the end of the
process); watching cannot be undone.

- `watchSignals(sigs: &[]int)` starts watching without waiting, so that an
  arrival before the first wait is kept, not lost. Constants: `SIGHUP`,
  `SIGINT`, `SIGQUIT`, `SIGUSR1`, `SIGUSR2`, `SIGTERM` (any signal 1 to 31
  a program may handle works; `SIGKILL`, `SIGSTOP` and the faults are a
  panic).
- `waitSignal(sigs: &[]int): Result[int, Cancelled]` watches `sigs` and
  parks until one arrives: its number. Every task waiting for it then gets
  it; with none waiting it is kept for the next wait (repeats before a wait
  are one). `terminated(): Result[int, Cancelled]` waits for `SIGTERM` or
  `SIGINT`. Cancellation and `within` end the wait as usual.

**Worker processes** (SPEC 16, stage C2): the way a program uses several
cores. Make what the workers share first (a listener, read-only data; each
gets a copy), then:

- `workers(n: int, f: &fn(int)): Result[[]WorkerEnd, GaveUp]` forks `n`
  processes, runs `f(i)` in process `i` and ends it (exit 0; a runtime
  error ends it with its code), and supervises them until all have ended;
  `workersWith(n, p: WorkerPolicy, f)` with a policy of its own.
- The supervisor restarts a crashed worker (a signal or an exit code other
  than 0) after a backoff, gives up after more than `p.maxCrashes` crashes
  of workers that were not yet ready (`workerReady`) within `p.windowMs` (sends the others `SIGTERM`, then `Err(GaveUp)`),
  forwards `SIGTERM` and `SIGINT` to every worker (a drain: no restarts;
  after `p.drainMs`, when it is more than 0, or a second signal from
  outside, `SIGKILL`), and returns when all have ended.
- `WorkerPolicy {maxCrashes, windowMs, backoffMs, maxBackoffMs, drainMs}`;
  `workerPolicy()` gives the defaults 5, 10,000, 100, 5,000 and 0 (the
  backoff doubles for each crash in a row of one worker, up to
  `maxBackoffMs`; a run that lasted `windowMs` ends the row).
- `WorkerEnd {index, pid, code, signal, drained, restarts}` (`toStr`: `worker
  2 (pid 4711): exit 0, drained, 1 restart`): the last process of each
  worker; `code` is -1 when `signal` ended it. `GaveUp {crashes, ends}`.
- `workerReady(): bool` (in a worker) marks the worker ready: it has set
  itself up and serves. A crash after that is restarted with the policy's
  backoff but never counts toward giving up, so only crashes before ready
  (a bad configuration, a missing file) can stop the supervisor, and a
  request that kills a worker cannot stop the server however often it is
  sent. False outside a worker. `httpserver.serve` calls it once it
  accepts.
- `stopWorkers(): bool` (in a worker) asks the supervisor for the drain
  (every worker gets `SIGTERM`, this one too); asking again, or from
  several workers, is one request. False outside a worker.
- A worker that serves calls `watchSignals(&[SIGTERM, SIGINT])` first and
  stops when `terminated()` returns (dropping its accept loop's future
  cancels the loop and its connections).
- `workers` stops the program with exit 15 at its call when the fork would
  not be safe: after the helper pool has started, while another task
  exists, or while the event loop holds a registration (a socket that has
  been waited on, a signal watch, a database connection's socket). Open
  database connections and pools in each worker.
- The supervisor runs no task while it supervises. Output written before the
  call is flushed first. A worker drops the globals at its end, as `main`'s
  end does; then under `OBX_CHECK=1` it checks its own objects: the count of
  live objects must not be above the one at its fork.

### net

TCP over IPv4 and IPv6 for tasks (SPEC 16; design/concurrency.md, stage C1b).
Sockets are non-blocking and registered with the runtime's epoll loop; a call
that would block parks the task until the socket is ready, so other tasks run
meanwhile, and a plain program (its main task) uses the same calls.

- Addresses are text: `"127.0.0.1:8080"`, `"[::1]:8080"`, `":8080"` and
  `"0.0.0.0:8080"` (every IPv4 interface), `"localhost:8080"` (127.0.0.1),
  port 0 for any free port. `listen` and `connect` take numeric addresses
  and `localhost`; `dial` also takes names. `canonicalAddr(addr): Option[str]` checks and normalizes one
  (IPv6 in RFC 5952 form). A malformed address is `EINVAL`.
- `listen(addr): Result[Listener, IoError]` (`SO_REUSEADDR`, backlog 4096);
  `Listener`: `localAddr(): str`, `accept(): Result[Conn, IoError]`,
  `close()`. A listener joins the event loop at its first `accept`, so one
  made before `task.workers` is shared by the workers: one accept queue,
  each worker accepting from it in its own loop (`EPOLLEXCLUSIVE`: one
  waiting worker is woken per connection).
- `listenReusePort(addr): Result[Listener, IoError]` is `listen` with
  `SO_REUSEPORT`: several sockets, in one process or in workers, listen on
  one port and the kernel spreads new connections between them by a hash of
  the addresses. Each socket has its own queue: connections queued at one
  whose process dies are reset. With port 0 each call gets a port of its
  own, so take the port from a first socket and close it before the
  workers listen.
- `connect(addr): Result[Conn, IoError]`: a non-blocking connect that parks
  until it is done (`ECONNREFUSED` and the like come from it).
- `resolve(host, port): Result[[]str, IoError]`: the addresses of a name as
  text (`"93.184.215.14:443"`, `"[2606:2800:21f::1]:443"`), in
  `getaddrinfo`'s order, without duplicates; an IP address comes back
  without a lookup. `getaddrinfo` is a `blocking` extern: it runs on the
  helper pool while the task parks, and cannot be cancelled while it runs
  (the cancellation or deadline is seen when it returns). A failed lookup
  is `op` `lookup` with errno `io.LOOKUP + n` (`isLookup()`, and
  `errorText` gives `gai_strerror`'s text).
- `dial(addr): Result[Conn, IoError]`: `connect` for `"host:port"` where
  host may be a name. Its addresses are tried in turn, IPv6 and IPv4
  interleaved; under a `task.within` deadline each attempt gets an equal
  share of the time left, at least 2 s. The error is the first attempt's.
  Native programs that reach `dial` or `resolve` are linked dynamically
  (libc's resolver needs nsswitch); programs that do not stay static.
- `Conn.readNow(buf)`: one read that never parks (`EAGAIN` when nothing is
  there); the http pool uses it to see whether an idle connection was
  closed. `Conn.writeNow(data)`: one write that never parks (the count
  written, 0 when the socket's buffer is full), usable in a `drop` or
  inside `with` (`tls` sends close_notify with it). `Conn.take()` moves the
  socket out of a `Conn` (left closed), for a wrapper that gives its socket
  back (`tls.Conn.detach`).
- `Conn`: `read(buf: mut &[byte]): Result[int, IoError]` (0: the peer has
  closed), `write(data: &[byte]): Result[Unit, IoError]` (all of it, parking
  while the socket's buffer is full), `writeStr(s)`, `closeWrite()`
  (shutdown of the write side: the peer reads the end), `setNoDelay(on)`
  (`TCP_NODELAY`), `localAddr()`, `peerAddr()`, `close()`.
- Errors are `io.IoError {errno, op, path}`: `op` is `listen`, `accept`,
  `connect`, `read`, `write`, ...; `path` is the address (the peer's for a
  `Conn`). A call made by a cancelled task, or past the deadline of a
  `task.within`, does nothing and returns `ECANCELED` or `ETIMEDOUT`
  (`isCancelled()`, `isTimeout()`); so does a call parked when that happens.
  A write to a peer that has closed is `EPIPE` or `ECONNRESET`, never
  `SIGPIPE`.
- `Listener` and `Conn` are owned: dropping one (or `close()`) closes its
  socket and never parks, so it may happen inside `Shared.with`. The calls
  that can park take a last `w: Where` like `task`'s, and stop the program
  inside `with` (exit 13).

For servers (stage S1 of design/http-server.md):

- `c.split(): Halves` (`Halves {read: ReadHalf, write: WriteHalf}`) gives
  the two sides of a connection as owned values, so a request and a
  response can each hold one; the socket is closed when both have been
  dropped. `ReadHalf`: `read`, `readNow`, `peerAddr`, `remote` (the
  address `accept` saw, with no system call), `localAddr`, `writeInterim`
  (a short interim reply the reader owes, such as `100 Continue`) and
  `cancelOnHangup(halfClose): HangupGuard`. `WriteHalf`: `write`,
  `writeStr`, `writeStalled(data, stallMs)` (`ETIMEDOUT` when the peer
  takes nothing for `stallMs`; no limit on the whole write),
  `closeWrite`, `setNoDelay`, `peerAddr`.
- `cancelOnHangup` cancels the running task, as dropping its future does,
  when the peer resets the connection or (with `halfClose`) ends its
  stream, until the guard is dropped. It is not a read: data that arrives
  stays for the reader (`EPOLLRDHUP` in the event loop). A reset already
  seen cancels at once; an end of stream already pending when it is armed
  does not count, so a client that sends its request and shuts down its
  side still gets the reply.
- `l.setDeferAccept(secs)` (`TCP_DEFER_ACCEPT`: a connection reaches
  `accept` only once it has sent something); `spare()` reserves a spare
  descriptor (`/dev/null`) and `l.shed(mut &spare)`, after `accept`
  failed with `outOfDescriptors(&e)` (`EMFILE`, `ENFILE`), frees it,
  accepts and closes one waiting connection, and reserves it again, so the
  queue drains and its clients see a close instead of waiting. It gives
  `Ok(true)` when it closed a connection and `Ok(false)` when none was
  waiting. Linux's `accept` takes a descriptor before it looks at the
  queue, so at the limit it fails even with an empty queue; after
  `Ok(false)` the caller should wait (for a new connection or a freed
  descriptor) instead of calling `accept` again at once.

A `Conn` is used by one task at a time (it is owned). `httpserver` is an
HTTP/1.1 server built on it, `cmd/httpd.obx` an example program on that
(with worker processes and a shared listener: `httpd ADDR POOL WORKERS`),
`cmd/httpload.obx` a load client, and `http` an HTTP client.

### proc

Starting other programs, talking to them through pipes, and waiting for them
as part of the task model.

```obx
let r = proc.output(&proc.command("git", ["rev-parse", "HEAD"])).expect("git")
println(r.stdoutStr())

var c = proc.command("sort", [])
c.stdin = proc.Stdio.Pipe
c.stdout = proc.Stdio.Pipe
var child = proc.start(&c).expect("sort")
child.stdin.writeStr("b\na\n").unwrap()
child.stdin.close()                                  // the child's end of input
let text = child.takeStdout().readAll().unwrap()
let exit = child.wait().unwrap()                     // Exit.Code(0)
```

- `command(program, args): Command` with the public fields `args`, `env`
  (`"NAME=value"` entries added to the parent's environment; `setEnv(name,
  value)` replaces one of the same name), `cleanEnv` (only `env`), `newGroup`, `cwd`,
  and `stdin`, `stdout`, `stderr` of type `Stdio`: `Inherit` (the default),
  `Pipe`, `Null` (/dev/null), `File(path)` (read for stdin, created and
  emptied for the others), `Append(path)` and, for stderr only, `Stdout`
  (2>&1). `arg(a)` adds an argument.
- `start(&cmd): Result[Child, ProcError]`. A program name without "/" is
  looked up before the fork in the PATH the child will get (a `PATH=` entry of
  `env`; else, with `cleanEnv`, `/usr/local/bin:/usr/bin:/bin`; else the
  parent's), so a missing program is an ordinary `Err` (ENOENT, `isNotFound()`). If the exec fails the child
  reports its errno through a close-on-exec pipe, and `start` returns it. The
  child has only descriptors 0 to 2, the default signal mask and
  dispositions.
- `Child`: `stdin: PipeWriter` and `stdout`, `stderr: PipeReader` (public
  fields; closed unless the stream is a `Pipe`), `takeStdin()`,
  `takeStdout()`, `takeStderr()` to move one into a task, `pid()`,
  `signal(sig)`, `kill()`, `wait(w): Result[Exit, ProcError]`. A `PipeReader`
  has `read(buf)` (0 at the end), `readAll()`, `close()`; a `PipeWriter` has
  `write(data)`, `writeStr(s)`, `close()`. The pipe ends are non-blocking and
  parked on like net's sockets, so a program without tasks works too. A write
  to a child that has closed its input is EPIPE, never SIGPIPE.
- `Exit` is `Code(n)` or `Signaled(sig)`, with `ok()`, `code()` (-1 when
  signaled), `signal()` (0 when not) and `toStr()` ("exit 3", "signal 9").
- `wait` parks on a pidfd in the event loop (Linux 5.3; an older kernel, or
  `OBX_NO_PIDFD=1`, polls every 10 ms). Cancellation or a `task.within`
  deadline while a child runs: SIGTERM, after 2 s SIGKILL, the child is
  collected, and the call returns ECANCELED or ETIMEDOUT
  (`isCancelled()`, `isTimeout()`). A second `wait` gives the same `Exit`.
- `newGroup` puts the child in a process group of its own (`setpgid` in the
  child and the parent). `signal`, `kill`, cancellation (SIGTERM, SIGKILL)
  and drop then signal the whole group, so grandchildren die too; `pgid()`
  is the group (the pid), 0 without `newGroup`. Waiting still collects the
  child only. It is off by default: a process in a group of its own cannot
  read the terminal under job control (SIGTTIN).
- Dropping a `Child` that was not waited for sends SIGKILL and collects it
  (no zombie, no orphan) and closes its pipes.
- `run(&cmd): Result[Exit, ProcError]` starts and waits with the streams as
  set (inherited by default). `output(&cmd): Result[Output, ProcError]`
  collects `stdout` and `stderr` (`[]byte`; `stdoutStr()`, `stderrStr()`) and
  the `exit`; `communicate(&cmd, input)` also writes `input` to the child's
  stdin. Both read standard output and standard error in tasks of their own,
  at once, so a full pipe cannot deadlock the child; the command's stdout and
  stderr settings are replaced by pipes, its stdin by a pipe unless it is
  `Null` or a `File`.
- `ProcError` is `errno`, `op` and `program`, with `toStr()`
  ("start nosuch: No such file or directory").
- `s.start(runOutput, cmd)` with a top-level `fn runOutput(c: proc.Command): Result[proc.Output, proc.ProcError] { return proc.output(&c) }` is a `Future`, so `task.all` and
  `task.any` run processes side by side; the loser of `any` is cancelled and
  its child stopped. A process with a live `Child` has descriptors in the
  event loop, so `task.workers` refuses to fork then (exit 15).

### tls

TLS 1.2 and 1.3 through the system's OpenSSL 3 (`libssl.so.3`,
`libcrypto.so.3`, by externs: no headers, no C shim), design/tls.md.

```
let ctx = try tls.client(tls.defaults())           // once: loads the system's trust store
var c = try ctx.connect(try net.dial("example.com:443"), "example.com")
try c.writeStr("GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
let n = try c.read(mut &buf)                        // 0: the peer closed
try c.close()                                       // close_notify
```

- `Options` (`defaults()`): `caFile`, `caDir` (trust these instead of the
  system's store; see below), `insecureSkipVerify` (false), `pins` (SHA-256
  fingerprints, hex, comma-separated: the peer's certificate must be one),
  `certFile`, `keyFile` (a server's certificate, or a client's),
  `clientCaFile` (a server asks for and verifies client certificates),
  `minVersion` `Version.Tls12`, `maxVersion` `Version.Tls13`, `alpn`
  ("h2,http/1.1"; a client's offer).
- `Context` (`client(o)`, `server(o)`): an SSL_CTX, owned, freed by its
  drop; `share()` gives another handle on it (to move into a task or a
  pool). `engine(serverName)`, `connect(sock, serverName)`,
  `accept(sock)`.
- A client verifies by default: the chain against the trust store, the
  name given to `connect` (`SSL_set1_host`; an IP literal is checked as an
  address, `[::1]` brackets dropped), SNI for a name. A name is required
  unless verification is off. Renegotiation is refused. Pins apply with
  or without verification.
- `caFile` and `caDir` **replace** the system's trust store; they do not
  add to it. With neither, a client trusts the system's store (which
  honours `SSL_CERT_FILE` and `SSL_CERT_DIR`). With either, it trusts only
  the CAs given (both, when both are set): a private CA's server verifies,
  a public site fails with `Verify` code 20 (untrusted issuer). To trust
  a private CA and the public ones, give one file that holds both:
  `cat /etc/ssl/certs/ca-certificates.crt corp-ca.pem > bundle.pem`. The
  bundle is a copy of the system's store and misses its later updates, so
  remake it when they come. `insecureSkipVerify` ignores both; a server
  verifies client certificates against `clientCaFile`.
- `Conn` (a socket): `read(buf)` (0 at close_notify, or the socket's end
  at a record boundary; the end inside a record is `Protocol`),
  `readNow(buf)` (never parks: `WANT_INPUT` when nothing is there),
  `write(data)`, `writeStr(s)`, `handshake()`, `shutdown()` (close_notify,
  reading goes on), `close()`, `detach(): Detached {sock, rest}` (TLS ends
  without close_notify; the socket and what arrived after the last
  record), `version()`, `cipher()`, `alpn()`, `peer()`, `closeNotified()`,
  `engine()`, `peerAddr()`, `localAddr()`, `setNoDelay(on)`; `over(e,
  sock)` puts an engine (from memory mode) over a socket. Calls park their
  task on the socket; dropping an open `Conn` sends close_notify if the
  socket takes it at once (never parks).
- `Engine` (memory mode, for protocols that carry TLS in their own
  packets): `feed(ciphertext)`, `handshake(): Result[bool]` (true when
  done), `pending()`, `output(buf)`, `takeOutput()` (send these after every
  call), `read(buf)` (`WANT_INPUT`: feed more), `write(data)`,
  `shutdown()`, `unread()` (fed but not used), `isOpen()`, `version()`,
  `cipher()`, `alpn()`, `peer(): Option[Cert]` (`Cert {subject, issuer,
  notBefore, notAfter, sha256}`), `verifyResult()`, `verifyText()`,
  `resumed()`, `closeNotified()`.
- `Error {kind, msg, code, peer, cause}`, `toStr()` `tls: KIND: msg
  (peer)`; `Kind`: `Config`, `Handshake`, `Verify` (code is X509's verify
  result: 10 expired, 20 unknown issuer, 62 name mismatch, 64 address
  mismatch), `Protocol`, `Io`, `Timeout`, `Cancelled` (cause is the
  socket's `IoError`). msg is OpenSSL's error queue, which every call
  clears before and reads after, on the program's thread.
- `openHandles()` counts live contexts and engines (tests check it
  returns to 0); `libVersion()`.

Tests: `tests/run/tls_*.obx` (against `openssl s_server`, two engines in
memory, and a server in the program, with a PKI made by the `openssl`
command at test time; skipped without it or without libssl) and
`http_tls.obx`.

### url

URLs for http and https (RFC 3986), strict (design/http-client.md, H1).

- `parse(s): Result[Url, Error]`. `Url` is a value record: `scheme`, `host`
  (lowercased; IPv6 without brackets, in RFC 5952 form), `port` (the
  default when none is written), `path`, `query`/`hasQuery`,
  `fragment`/`hasFragment`, as written (escapes kept).
- Refused, with an `Error {msg, input}` that says why: other schemes, no
  authority, user information (`user:pass@`: sent in clear text), a
  non-ASCII, percent-encoded or empty host, an IPv6 zone, a host whose last
  label is a number but that is not a dotted quad (`1.2.3`, `0x7f.1`,
  `2130706433`: C's resolver reads those as addresses), a port outside 1 to
  65535, spaces, control characters, a character RFC 3986 does not allow
  where it stands, a `%` not followed by two hex digits.
- Methods: `target()` (path and query: what a request sends),
  `authority()` (`host[:port]`, the port only when not the default),
  `hostText()`, `hostPort()`, `origin()`, `isDefaultPort()`, `toStr()`
  (normalized: an explicit default port is dropped), `eq`,
  `withoutFragment()`, `withQuery(&q)`, `resolve(ref)` (a relative
  reference against the URL, RFC 3986 section 5, with dot segments
  removed).
- `encode(s)` (everything but the unreserved characters as `%XX`),
  `decode(s)`; `Query` (`newQuery`, `add`, `get`, `all`, `size`, `at`,
  `encode`) and `parseQuery(s)` (`+` is a space in a query).

### http

An HTTP/1.1 client for tasks over TCP and TLS (https, through `tls`), and the
head parsing servers share (design/http-client.md, H1 and H2). A reply
with any status is `Ok`; `r.ok()` says whether it is 2xx.

```
let r = try http.get("http://api.example.com/v1/rates?ccy=EUR")
let body = try r.text()

var o = http.defaults()
o.maxPerHost = 8
let c = http.newClient(o)
var rq = http.request("POST", "http://api.example.com/v1/orders")
rq.header("Content-Type", "application/json")
rq.body = json
let r = try c.send(&rq)
```

- `Request {method, url, headers, body}` from `request(method, url)`, with
  `header(name, value)`. Host, User-Agent and Content-Length are added when
  not given. Refused before sending (`Invalid`): a method that is not a
  token, a header name that is not a token, a control character in a value,
  a Transfer-Encoding, a Content-Length that is not the body's.
- `Options` (`defaults()`): `maxPerHost` 16, `timeoutMs` 30000 (the whole
  call, redirects included, and then the body's reads; 0: none), `maxBody`
  16 MiB (for `text` and `bytes`), `redirects` true, `maxRedirects` 10,
  `allowHttpsToHttp` false, `idleTimeoutMs` 90000, `userAgent` "obx",
  `tls` (`tls.Options` for https): `tls.defaults()` with `alpn`
  "http/1.1", so the server's certificate is verified against the system's
  store and its name against the URL's host. `o.tls.caFile = "ca.pem"`
  trusts that CA instead of the system's store (not as well as it: see
  tls above); `o.tls.insecureSkipVerify = true` turns verification
  off; pins and a client certificate are `tls` options too.
- `Client` (`newClient(o)`) is a copy handle over a `task.Shared` pool:
  copies share it. `send(&rq)`, `get(u)`, `head(u)`,
  `post(u, contentType, body)`, `put(...)`, `delete(u)`; `options()`,
  `stats()` (`Stats {open, idle, waiting, dials, reuses, retries}`),
  `closeIdle()`. The module's `get`, `head`, `post`, `put`, `delete` and
  `send` use one default client made on first use.
- `Response`: `status`, `reason`, `minor`, `headers`, `url` (after
  redirects); `ok()`, `header(name)`, `contentLength()` (-1 when not
  given), `text()` and `bytes()` (the whole body, at most `maxBody`, else
  `TooLarge`), `read(mut &buf)` (streams; 0 at the end), `discard(limit)`,
  `close()`.
- `Error {kind, msg, url}`; `Kind`: `Url`, `Invalid`, `Dns`, `Connect`,
  `Tls`, `Timeout`, `Cancelled`, `Io`, `Protocol`, `TooLarge`,
  `Redirects`, `Process`. `toStr()` is `http: KIND: msg (url)`.
- The pool keeps at most `maxPerHost` connections per scheme, host and
  port; callers past that wait in FIFO order. An idle connection is closed
  after `idleTimeoutMs`, or after the server's `Keep-Alive: timeout=N`
  less one second, and one idle for more than 100 ms is checked (a read
  that does not park) before reuse.
- A `Response` holds its connection. Dropped with its body read to the end
  (or with all of it already arrived) on a connection the server keeps,
  the connection goes back to the pool; otherwise it is closed. A
  cancelled or timed-out call closes its connection.
- A request that fails on a reused connection before any of the reply came
  (the server closed it meanwhile) is sent once more on a new connection,
  for GET, HEAD, PUT, DELETE, OPTIONS and TRACE, never POST or PATCH.
- Redirects (301, 302, 303, 307, 308) are followed up to `maxRedirects`:
  303, and 301/302 after POST, become GET without a body; Authorization,
  Cookie and Proxy-Authorization are dropped when the origin changes; https
  to http is refused unless `allowHttpsToHttp`.
- Replies are framed strictly (RFC 9112 section 6.3): Content-Length with
  Transfer-Encoding, differing Content-Lengths, a coding other than
  chunked, obsolete line folding, a bare LF, a bad chunk size and a
  connection closed inside a head or a framed body are `Protocol` errors.
  Limits: a head of 64 KiB and 100 lines (trailers too), 8 interim (1xx)
  replies, a 4 KiB chunk-size line.
- A client used in another process than the one that made it (after a
  fork) refuses with `Process`: make clients inside each worker.
- https: a client makes one `tls.Context` from `o.tls` at its first https
  request (bad options fail with `Tls` before connecting); each connection
  does its own handshake (no session resumption yet). A certificate that
  does not verify, a failed handshake and a damaged record are `Tls`
  errors whose msg names the reason (`verify: certificate has expired`,
  `handshake: unsupported protocol`); socket errors during the handshake
  are `Connect` (or `Timeout`, `Cancelled`). Pools are per scheme, host
  and port, so http and https connections to one host are separate.
- Shared parsing, used by `httpserver`: `headEnd`, `parseFields`,
  `contentLength`, `parseRequest` (`RequestHead {method, target, minor,
  headers}`, `keepAlive()`, `bodyLength()`), `equalFold`, `isToken`,
  `isFieldValue`, `Headers` (`get`, `has`, `values`, `add`, `set`,
  `remove`, `hasToken`, ...).

Tests: `tests/run/url_*.obx`, `http_*.obx` (against the route and scripted
servers of `tests/support/httptest.obx`; `http_tls.obx` against an https
server of `tests/support/tlstest.obx`; `http_live.obx` only with
`OBX_HTTP_LIVE=1`, else it exits 77), and `tests/httpgen.py`, which
generates reply framings and checks every body on both backends.

`both` and `forEach` (concurrent children that borrow the parent's locals)
are not there yet: a task's function is a plain `fn`, and passing borrows
into a task needs the checker's help (design/concurrency.md, section 5).

### httpserver

An HTTP/1.1 server for tasks (design/http-server.md, stage S1). A module of
its own: the client in `http` already has a `Request`, `Response`, `Error`
and `defaults`, so a program that has both imports `"http"` and
`"httpserver" as hs`. The handler API is meant to stay the same for
HTTP/2 (S3).

```
import "httpserver" as http

type App = value record { db: postgres.Pool }      // a copy type: handles only

fn account(app: &App, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error] {
    let id = try rq.paramInt("id")                          // 400 when not an integer
    let c = try http.check(app.db.acquire(), 503)           // any error as a 503
    let rows = try http.check(c.query("SELECT name FROM account WHERE id = $1", &[postgres.Value.Int(id)]), 500)
    if rows.rowCount() == 0 { return w.fail(404) }
    return w.json(toJson(&rows))
}

var r = http.router[App]()
r.use(http.requestId[App])
r.use(http.accessLog[App])
r.handle("GET /accounts/{id}", account)
let st = try http.serve(net.listen(":8080").expect("listen"), app, r, http.defaults())
```

- `serve[A: copy](ln, app: A, r: Router[A], cfg: Config): Result[Stats,
  Error]` serves until `rq.stopServer()`, or `SIGTERM`/`SIGINT` when
  `cfg.signals`. Then it drains: it closes the listener (new connections are
  refused) and the idle connections, lets the requests in flight finish
  (their replies say `Connection: close`) for up to `drainMs`, cancels the
  rest, and returns its counters. One task per connection, keep-alive,
  pipelined requests answered in order. Each handler gets `&app`, a copy per
  connection. `Err` (kind `Config`) before anything is accepted when a
  pattern is invalid or two conflict, or when `RLIMIT_NOFILE` (raised as
  far as the hard limit allows) or `vm.max_map_count` is too low for
  `maxConns`. In a worker it marks the worker ready (`task.workerReady`);
  `workerPolicy()` is task's with a 35 s drain.
- `Handler[A] = fn(&A, mut &Request, mut &Response): Result[Unit, Error]`.
  `router[A]()`; `r.handle(pattern, h)`; `r.handleWith(pattern, h,
  RouteOpts{deadlineMs, maxBody})` (`routeOpts()` gives -1 for both: the
  Config's; `deadlineMs` 0 is none, for server-sent events).
- Patterns are Go 1.22's: `"[METHOD ][HOST]/[PATH]"`, `{name}` one
  segment, `{name...}` the rest, `{$}` the end of a path ending in `/`, a
  pattern ending in `/` matches everything under it. GET also answers HEAD
  (no body sent). The most specific pattern wins; a path that matches with
  another method gets 405 with `Allow`; `/dir` is redirected (301) to
  `/dir/` when only the subtree pattern matches; otherwise 404.
- Middleware: `Middleware[A] = fn(&A, mut &Request, mut &Response,
  Next[A]): Result[Unit, Error]`; `next.run(app, rq, w)` runs the rest.
  `r.use(m)` for every request (404s and 405s too), `r.useUnder(prefix,
  m)` for paths under prefix; the first added runs outermost. In the
  library: `requestId` (16 random bytes in hex, or a trusted proxy's
  `X-Request-Id` of 1 to 64 token characters; sent back as
  `X-Request-Id`), `accessLog` (below) and `securityHeaders`
  (`Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy:
  same-origin`).
- `Request`: `method`, `path` (decoded once), `host` (validated,
  lowercase), `proto`, `headers` (`http.Headers`), `peer`, `id`, `pattern`;
  `param(name)`, `paramInt(name)`, `query(name)` (decoded), `header(name)`,
  `rawTarget()`, `rawQuery()`, `client()` (the peer, or for a trusted proxy
  the rightmost untrusted `X-Forwarded-For` address), `note(key, value)`
  (a field for the access log), `serverStats()`, `stopServer()`. The body:
  `read(mut &buf)` (streams; 0 at the end), `bytes(limit)` and
  `text(limit)` (413 past the limit or the route's `maxBody`; 400 when the
  text is not UTF-8), `bodyRead()`, `trailers()`. With `Expect:
  100-continue` the 100 is sent at the first read; a body nobody read is
  read and dropped up to `maxDrain`, else the connection closes.
- `Response`: `status(code)`, `header(name, value)` (replaces),
  `addHeader` (adds: `Set-Cookie`), `trailer(name, value)`, `write(data)`,
  `writeStr(s)`, `flush()`, `text(s)`, `json(s)`, `fail(code)` (the
  library's fixed body: the status line and the request id),
  `redirect(code, location)`, `startEvents()` and `event(name, data)`
  (server-sent events), `statusCode()`, `headWritten()`, `bodyBytes()`.
  Nothing is sent before the handler returns, `flush` is called, or 32 KiB
  of body is buffered; a finished reply whose head was not yet written gets
  a `Content-Length`, others are chunked (until the close for HTTP/1.0). A
  `Response` that is not written to answers 200 with an empty body.
- `Error {status, kind, msg}` (`ErrorKind`: `Client`, `Server`, `Gone`,
  `Timeout`, `Limit`, `Protocol`, `Config`); `error(status, msg)`;
  `check(r, status)` turns any `Result[T, E: Show]` into one. A handler's
  `Err` before the head is written answers `status` with the fixed body
  (headers it had set are dropped; `msg` goes to the log, never to the
  client); after the head is written, the connection is closed short of the
  body's end, so the client sees a truncated reply.
- The route's deadline (`deadlineMs`, 30 s) is a `task.within` around the
  middleware and the handler; past it, a reply not yet started is 503.
  While the handler runs, a client that hangs up (a reset, or with
  `cancelOnHalfClose` also a half-close) cancels the connection's task:
  the handler's next call that can park returns `Cancelled`, a Postgres
  query is cancelled on the server (`PQcancel`), a SQLite step is
  interrupted, an ODBC call gets `SQLCancel`.
- `Stats {accepted, requests, open, rejected, timeouts, gone, errors, shed,
  idleClosed, c2xx, c3xx, c4xx, c5xx, logDropped}` (`toStr`: the first
  seven).

**Parsing** is strict (design/http-server.md section 3.1–3.3): anything
two parsers could read differently gets a 4xx and the connection closed.
Refused: `Content-Length` with `Transfer-Encoding`, differing or listed
lengths, a coding other than `chunked`, obsolete line folding, whitespace
before a colon, a bare CR or LF, NUL and controls in a field, a bad
request line or version (`HTTP/2.0` and the h2 preface: 505), a bad chunk
size (more than 16 hex digits) or extension, a missing or repeated Host
(400) or one not in `hosts` (421), an absolute-form target whose host is
not the Host, dot segments (raw or encoded), encoded `/` or NUL, invalid
UTF-8 in the decoded path, `Expect` other than `100-continue` (417). Up to
two empty lines before a request are skipped. Response headers are checked
too: a name that is not a token or a value with CR, LF, NUL or other
controls fails the response (500, the header named in the log), and no
byte of it is sent. Every response has `Date` and `X-Content-Type-Options:
nosniff`; error responses `Cache-Control: no-store`; a body without a
`Content-Type` is `application/octet-stream`. `redirect` takes a path
starting with a single `/`, or an http(s) URL whose host is in
`redirectHosts`; anything else is a 500, never an open redirect.

**Limits and timeouts** (`Config`, `defaults()`):

| Field | Default | What happens past it |
|---|---:|---|
| `headerTimeoutMs` | 10,000 | the whole head from its first byte: 408 and close |
| `idleTimeoutMs` | 60,000 | a keep-alive connection waiting for its next request is closed |
| `idlePressureMs` | 5,000 | the idle timeout from 80 % of `maxConns` on |
| `bodyGraceMs`, `bodyMinRate` | 10,000, 1,024 bytes/s | a body: any rate for the grace, then at least the rate (credit at most 5 s ahead): 408 and close |
| `writeStallMs` | 30,000 | a write that makes no progress this long: the reply fails and the connection closes (no limit on a whole write) |
| `deadlineMs` | 30,000 | the route's deadline: 503 if nothing was written |
| `drainMs` | 30,000 | requests still running after a stop are cancelled |
| `maxConns` | 10,000 | the accept loop waits; new connections wait in the kernel's queue |
| `maxConnsPerIp` | 0 (off) | per worker: 503 and close |
| `maxRequestsPerConn` | 1,000 | `Connection: close` on the last |
| `maxHeadBytes`, `maxHeaders`, `maxLineBytes` | 16,384, 100, 8,192 | 431 and close |
| `maxTargetBytes` | 8,192 | 414 and close |
| `maxBody` | 1 MiB | 413 (a declared length over it before any byte is read) |
| `maxDrain` | 256 KiB | an unread body larger than this closes the connection |
| `maxParams` | 1,000 | 400 |
| `maxTrailerBytes`, `maxTrailers` | 8,192, 32 | 431 and close |
| `memBudget` | 512 MiB | buffered bytes in the worker: new requests get 503 and close |
| `fdMargin` | 64 | descriptors kept free beyond `maxConns`, checked at start |
| `acceptBatch` | 16 | the accept loop yields after this many accepts |
| `deferAccept` | true | `TCP_DEFER_ACCEPT` with the head timeout |
| `cancelOnHalfClose` | true | a half-close cancels the running handler, as a reset does |
| `signals` | true | `SIGTERM`/`SIGINT` drain the server |
| `hosts`, `redirectHosts`, `trustedProxies` | empty | Hosts served (others 421); redirect targets; proxies believed |
| `logFd` | 1 | where `accessLog` writes (-1: nowhere) |

On `EMFILE`/`ENFILE` the accept loop uses the spare descriptor to accept
and close one waiting connection (`shed`, counted in `Stats.shed`); when
none was waiting, it sleeps 10 ms before trying again, so a server at its
descriptor limit does not spin. A connection's task yields after
each request and every 64 KiB of body copy, so a client that pipelines
without pause does not starve the others. After a reply that ends the
connection, the server shuts down its side and reads and drops what the
client still sends for up to 500 ms (256 KiB), so the client's unread
request bytes do not turn the close into a reset that loses the reply. An
idle connection costs about 15.5 KB (its task's stack pages, a 4 KiB read
buffer and a 512-byte output buffer, which grows as replies need).

**The access log** (`accessLog`) is one logfmt line per request, written by
the server through a `blocking` `write` (every 100 ms or 64 KiB; past
4 MiB unwritten, lines are dropped and counted in `logDropped`), never by
the request's task, so a slow reader of the log does not stall requests:

    time=2026-11-03T10:20:30Z id=1f0c... pid=4711 peer=10.0.0.7:51234 method=GET route="GET /accounts/{id}" path=/accounts/7 status=200 in=0 out=13 us=180 outcome=ok

Values from input are escaped (controls, quotes, backslashes and bytes past
0x7e as `\xHH`) and cut at 256 bytes. The query, `Authorization` and
`Cookie` are not logged; `rq.note` adds fields.

The router and middleware are held in a `task.Frozen`, which every
connection's task reads at once. Not there yet (S2 to S4): TLS, HTTP/2, `w.hint` (103), full duplex,
a `Cookie` type, `traceparent`, a JSON log, SSE keep-alive comments,
`maxBlocking`, per-IP limits across workers, static files, compression,
WebSockets, forms.

Tests: `tests/run/http_server_*.obx` (the parsing corpus, routing, limits,
slow clients, hangup cancellation with Postgres and SQLite, drain, the
access log, an accept flood, running out of descriptors; through `tests/support/srvtest.obx` and `srvrun.obx`),
`task_workers_ready.obx`, and `tests/httpsrvgen.py`, which checks the
parsing against Go's net/http and Node's llhttp on generated request
streams (`gate.sh full`).

### regex

Regular expressions with RE2's syntax, matched in time linear in the size of
the text whatever the pattern (design/regex.md, phases R1 and R2). A port of
Go's `regexp` and `regexp/syntax` (BSD-3-Clause; see `NOTICE`): the same
parser, errors and limits, the same program, the same three matchers, and
the same results, checked against Go's own tests and against Go on random
patterns; plus RE2's lazy DFA and literal searches after Rust's `regex`,
which find the same matches faster.

```obx
import "regex"

let re = try regex.compile("(?P<y>\\d{4})-(?P<m>\\d{2})-(?P<d>\\d{2})")
if re.isMatch("on 2026-11-03") { ... }
match re.find(text) { Some(m) { m.start; m.end; m.text } None {} }
for m in re.findAll(text) { ... }
match re.captures(text) {
    Some(c) { let y = c.name("y").unwrap().text }
    None {}
}
let s = re.replaceAll(text, "$d/$m/$y")     // "${d}x" when a letter follows
let parts = re.split(text)
```

- **Compiling.** `compile(pattern): Result[Regex, Error]` (leftmost-first,
  Perl's syntax, as Go's `Compile`); `compileLongest` (leftmost-longest);
  `compilePosix` (POSIX syntax: no `\d` and the like, leftmost-longest);
  `mustCompile` (panics on an error: for constant patterns);
  `isMatchStr(pattern, text): Result[bool, Error]`. A `Regex` is immutable
  once compiled; each call has its own matching state.
- **Error** `{code: ErrorCode, expr, pos}`: `message()` is Go's text
  (`error parsing regexp: missing closing ): `(abc``), `toStr()` adds a
  hint for the refused features below.
- **Searching.** `isMatch(text)`; `find(text): Option[Match]` and
  `findFrom(text, from)`; `findIndex(text): Option[Span]`; `findAll`,
  `findN(text, n)`, `findAllIndex`, `findAllIndexN`. `Match {start, end,
  text}` and `Span {start, end}` are value records; offsets are byte offsets.
  A search with `n < 0` finds all; as in Go, an empty match right after the
  previous match is skipped.
- **Searchers.** `searcher(): Searcher` keeps a Regex's matching state (the
  DFA's states) between searches; `findWith(mut &s, text, from):
  Option[Span]` is `findFrom` through it, and `matchEndWith(mut &s, text,
  from): int` only says whether there is a match and gives the end of one
  (the first to end; -1 for none). Through a Searcher the DFA runs on short
  texts too, so searching a line at a time costs little.
  `matchingLines(mut &s, text, from, to, max, mut &out): int` appends the
  start and end of each line of `text[from..to]` with a match (whole lines;
  for patterns that cannot match a newline), at most `max`, and returns
  where it stopped: the DFA runs over the lines once and starts again after
  each line it reports. With them,
  `canMatchNewline()` (whether a match can contain `"\n"`) and
  `requiredLiteral()` (a literal every match contains, `""` if none is known)
  let a tool like grep search lines and literals first.
- **Groups.** `captures(text): Option[Captures]`, `capturesAll`,
  `capturesN`; `Captures`: `count()` (groups plus one), `get(i):
  Option[Match]` (None for a group that did not take part), `whole()`,
  `name(n): Option[Match]`, `str(i)` (`""` if unset), `indexes()`. The index
  forms are `submatchIndex(text): []int` (2 slots per group, -1 unset; empty
  when there is no match) and `allSubmatchIndex(text, n): [][]int`.
  `numGroups()`, `groupNames()` (`""` for group 0 and unnamed groups),
  `groupIndex(name)` (-1 if none).
- **Replacing.** `replaceAll(text, template)`, `replace` (the first match),
  `replaceN`; in the template `$1`, `$name`, `${name}` and `${1}` expand to
  the group (empty if unset or unknown), `$$` is `$`; the longest name is
  taken, so `$1x` is the group `1x`: write `${1}x`. `expand(template,
  &Captures)` expands one template. `replaceAllLiteral` (no expansion),
  `replaceAllFunc(text, f: &fn(str): str)`.
- **Others.** `split(text)`, `splitN(text, n)` (Go's semantics, n < 0 for
  all); `quote(s)` escapes ``\.+*?()|[]{}^$``; `literalPrefix(): Prefix
  {text, complete}`; `pattern()`; `isLongest()`; `matcherFor(n)` (which
  matcher a search of n bytes uses: `onepass`, `literal`, `dfa`,
  `backtrack` or `pike`); `literalSearch()` (the DFA's literal search:
  `prefix`, `inner`, `suffix`, `line` or `none`; `line`: matches start at
  line starts, so the DFA skips to the next line). For tests,
  `compileForcing(pattern, longest, matcher)` forces `pike`, `backtrack`,
  `dfa` (no literal search), `prefilter` (every literal search there is) or
  `dfatiny` (a cache of three states, cleared all the time).

**Syntax.** RE2's, as Go: literals and escapes (`\x41`, `\x{263a}`, `\Q...\E`,
`\a \f \t \n \r \v`, octal); `.`; classes `[...]`, `[^...]`, `[[:alpha:]]`
and the other POSIX classes; `\d \w \s \D \W \S` (ASCII); `\pL`, `\p{Lu}`,
`\p{Greek}`, `\PL`, `\p{^L}` (the general categories, scripts and Go's
category aliases, Unicode 15.0.0); `* + ? {n} {n,} {n,m}` and their lazy forms
(`*?`); `|`; `(...)`, `(?:...)`, `(?P<name>...)` and `(?<name>...)`; flags
`i m s U` as `(?i)` or `(?i:...)` (and `(?-i)`); `^ $ \A \z \b \B`. Patterns
and texts are UTF-8 and matched by code point; an invalid byte in the text
reads as U+FFFD, one byte wide, which only `.` and negated classes match.
`(?i)` uses Unicode's simple case folding (`k` matches `K` and the Kelvin
sign).

**Refused** (an error, with a hint saying why): backreferences (`\1`),
lookaround (`(?=`, `(?!`, `(?<=`, `(?<!`) and possessive repeats (`a++`):
they are what makes backtracking engines take exponential time, and an
engine with them cannot promise linear time. Also refused as in Go: `\C`,
`\Z`, a repeat count above 1000, nesting deeper than 1000, and a pattern
whose program would be too large, so a pattern cannot exhaust memory.

**Matching.** Per search: the one-pass matcher when the pattern allows it
(anchored, every step one choice); a memory search when the pattern is a
literal; the lazy DFA on texts of 128 bytes or more (states built while
searching in a 2 MB cache; a forward run finds where the match ends, the
reversed pattern run backward where it starts; for the groups the
backtracker or the Pike VM then runs on that span only), with a literal
search to skip ahead (the literals matches start with, a literal inside
the pattern, or one they all end with; without one, the next line when
matches start at line starts); Go's choice otherwise, and when the
DFA's cache fills too often: the bounded backtracker for small programs on
short texts (a visited bitmap: each instruction at each position once), and
the Pike VM (an NFA simulation). The DFA's states live in the call's
matching state, so a `findAll` reuses them but separate calls rebuild them
(a `Searcher` keeps them).
Speed: `PERFORMANCE.md` (via C 1.3 to 2.6 times Rust's `regex` on the
benchmark set, 2 to 650 times faster than Go's `regexp`).

### json

JSON (RFC 8259) values, parsing and writing. Where JSON leaves a choice,
the module follows jq 1.7 (design/json.md).

```obx
import "json"

let v = try json.parse(`{"name": "obx", "tags": ["a", "b"], "price": 12.50}`)
v.field("name").asStr()             // Some("obx"); field of a missing key is null
v.field("price").asDecimal()        // Some(12.50), read from the literal exactly
for t in v.field("tags").items() { ... }
var out = json.object().with("ok", json.boolean(true))
out.set("ids", json.arrayOf([json.integer(1), json.integer(2)]))
out.toStr()                         // {"ok":true,"ids":[1,2]}
json.write(&out, &json.pretty())    // two spaces per level, newline at the end
```

- **Values.** A `Json` is `Null`, `Bool`, `Num`, `Str`, `Arr` or `Obj`.
  Arrays and objects are counted and shared: `clone()` is cheap, and
  changing a shared one copies it first. Readers (`field`, `item`, `items`)
  return owned values; `eachItem`, `eachEntry` and `withItems` lend them.
- **Objects** keep their keys in insertion order. A lookup scans up to 16
  keys; larger objects use a hash index.
- **Numbers** keep their literal text and the nearest double. `asInt` and
  `asDecimal` read the text exactly, and writing gives the text back.
  `cmp` and `eq` compare the texts exactly, so `1.0 == 1` and
  `0.0001e4 == 1`.
- **Strings** are bytes. Invalid UTF-8 and lone low surrogates become
  U+FFFD, as in jq; with `strictUtf8` they are errors.
- **Parsing.**
  - `parse(s)` reads one value.
  - `parseWith(s, options)` takes `maxDepth` (default 512), `strictUtf8`,
    and `lenient` (jq's `nan`, `+1` and the like).
  - `decoder(s).next()` reads a sequence of values.
  - An `Error` has a kind, a message, a byte offset, and the line and
    column.
- **Writing.** `write(&j, &options)` returns the text;
  `writeTo(mut &sink, &j, &options)` streams it. `WriteOptions` has
  `indent`, `tab`, `sortKeys`, `ascii`, `raw` and `jqNumbers`.
- **Order.** `cmp` is jq's total order.

**Tests:** `tests/run/json_*.obx`, and `tests/jsonsuite.py`, which runs
JSONTestSuite (fetched at test time). **Speed:** `bench/json/bench.py`; the
results are in design/json.md, section 9.

### sqldialect, sqlite, postgres, mssql

Database adapters over the C client libraries (a program that imports one is
dynamically linked to libsqlite3.so.0, libpq.so.5 or libodbc.so.2; the tests
that need one are skipped when it is not installed). Examples:
`examples/sqlite_ledger.obx`, `examples/postgres_ledger.obx`.

- **sqldialect** (shared): `Value` (`Null Int Float Bool Text Bytes Dec`,
  `toStr` as an SQL literal), `DbError {code, state, msg}` (`"error 19:
  UNIQUE constraint failed: t.id"`, `"error 7 (23505): duplicate key ..."`)
  and the adapters' own codes `E_INPUT E_NULL E_TYPE E_NONFINITE E_INEXACT
  E_DEC_* E_CLOSED E_LIMIT` (1000 and up, above every SQLite and libpq
  code); `Sql` fragments (`sql(text)`, `add`, `arg(v)`, `of(parts, args)`,
  `render(dialect)` with `?`, `$n`, `@pn`, `:n` placeholders); `parseDecimal`, `decimalText`,
  `fitDecimal(d, p, s)`.
- **sqlite**: `open(path)`, `openWith(path, flags)`; `Db`: `exec(script)`,
  `prepare(sql)` (one statement), `query(sql, &args)`, `querySql(&sql)`,
  `run(sql, &args)` (rows changed), `changes`, `lastInsertId`,
  `inTransaction`, `transaction(&fn)`, `close()`. `Stmt`: `step(): bool`,
  `reset`, `bind(i, v)`, `bindAll`, `bindDecimal(i, d, p, s)`,
  `columnCount`, `columnName`, and per column `isNull int bool float text
  bytes decimal decimalOrNull value`. Decimals are stored as exact TEXT.
  `step` and `exec` are blocking externs: in a program with other tasks a
  long query parks only its task (each step then pays the pool's round
  trip, 0.4 to 0.8 us, against 30 ns for a step of a cached table; alone, a
  program runs them inline). So they cannot be called inside `Shared.with`.
- **postgres** runs on libpq's asynchronous API, so a query parks only its
  task: `connect` is `PQconnectStart` and `PQconnectPoll` (blocking externs:
  name lookup, TLS and password hashing run on the helper pool) with the
  task parked on the socket between them; `query` and `exec` are
  `PQsendQueryParams` / `PQsendQuery` on a non-blocking connection, then
  `PQflush`, `PQconsumeInput`, `PQisBusy` and `PQgetResult` with the task
  parked on the socket (`obxlib_fd_wait`). The task's cancellation and its
  `task.within` deadline end a wait: `Err` with `E_CANCELLED` (1020) or
  `E_TIMEOUT` (1021), after the query is cancelled on the server
  (`PQcancel`) and its results drained (at most 5 s, not cancellable), so
  the connection is idle again. `connect_timeout` bounds the whole connect
  (`E_TIMEOUT`). Every call that can park takes a last `w: Where`.
- **postgres**: `connect(conninfo)`, `libVersion()`; `Conn`: `exec(script)`,
  `query(sql, &args)` (text format, each parameter with
  its type's OID: int8, float8, bool, text, bytea, numeric; NULL untyped),
  `querySql`, `transaction`, `serverVersion`, `close()`;
  `decimalParam(d, p, s)`. `Rows` (outlive their connection): `rowCount`,
  `columnCount`, `affected`, `columnName`, `columnType` (OID),
  `columnTypmod`, `decimalType` (numeric precision and scale), and per cell
  `isNull int bool float text bytes decimal decimalOrNull value`. A failed
  statement is an `Err` with the SQLSTATE in `state`. `Conn.idle()`: up,
  outside a transaction, no query running.
- **postgres pools**: `newPool(conninfo, size): Pool`, a counted handle
  (copies reach the same pool); `acquire(): Result[Lease, Error]` gives an
  idle connection, opens one while fewer than `size` are open, or parks
  until a `Lease` is dropped (first come first served; cancellation and
  deadlines give `E_CANCELLED` / `E_TIMEOUT` and leave the queue). A
  `Lease` has `exec`, `query`, `querySql`, `transaction`, and gives its
  connection back when dropped, or closes it when it is not `idle()` (in a
  transaction, broken, a query abandoned): its place goes to the next
  waiter, which opens a new one. `stats(): PoolStats {open, idle,
  waiting}`. The state is a `task.Shared` with the idle connections and a
  queue of waiting promises. `cmd/httpd.obx` serves `GET /db` through one.
- **mssql**: `connect(connStr)` (an ODBC connection string, for example
  `Driver={ODBC Driver 18 for SQL Server};Server=...;UID=...;PWD=...`).
  `Conn`: `exec(batch)` (every result read), `prepare(sql)`, `query(sql,
  &args)`, `querySql`, `run(sql, &args)` (rows changed), `transaction(&fn)`
  (autocommit off, COMMIT or ROLLBACK; no nesting), `inTransaction`,
  `diagnostics()` (connect's 01000 records), `close()`. `Stmt` (a cursor on
  the server; one open result per connection, no MARS): `bind(i, v)`,
  `bindAll`, `bindDecimal(i, d, p, s)`, `bindParam(i, Param)`, `paramCount`,
  `execute`, `step(): bool`, `nextResult(): bool` (batches), `affected`,
  `reset` (frees the connection for the next statement), `diagnostics()`
  (PRINT and other informational records), `setMaxValue(n)`,
  `columnCount`, `columnName`, `columnType` (the SQL_* code), `columnSize`,
  `decimalType`, and per column of the current row `isNull int bool float
  text bytes decimal decimalOrNull value`. Also `paramOf(v): Param`,
  `decimalParam(d, p, s)`, `toUtf16`, `fromUtf16`, `floatOfBits`,
  `errorOf`.

  Types: bigint, int, smallint, tinyint and bit are ints (`bool` reads bit);
  decimal, numeric, money and smallmoney exact Decimals at the column's
  scale (read as text, never through a float); float and real floats (the C
  double's bits, so real gives its exact value 0.10000000149011612); binary
  types bytes; everything else text in the driver's format ((n)varchar,
  (n)char, text, date, time, datetime2, datetimeoffset, uniqueidentifier,
  xml). Parameters: Int bigint, Bool bit, Float float (NaN and infinities
  are `E_NONFINITE`), Text nvarchar, Bytes varbinary, Dec decimal(p, s) from
  its exact digits, NULL typed by `SQLDescribeParam` (so it fits a varbinary
  column). Text crosses the boundary as UTF-16 with a strict converter in
  obx (no locale, no C): invalid UTF-8 in a parameter or SQL text is
  `E_INPUT` before any call, a lone surrogate from the server `E_TYPE`;
  NUL characters inside text values are kept. NULL is never "" or 0.

  Long values are read in chunks with SQLGetData (a buffer that grows to 1
  MiB) up to the Stmt's maxValue, 16 MiB of driver bytes by default (UTF-16
  for text); a longer value is read past and is `E_LIMIT`, and the columns
  after it stay readable. Columns can be read in any order (the skipped ones
  are kept). Errors: a failed call's first diagnostic record that is not a
  warning gives `code` (SQL Server's error number), `state` (SQLSTATE) and
  `msg`: `"error 208 (42S02): [Microsoft][ODBC Driver 18 for SQL
  Server][SQL Server]Invalid object name 'nope'."`. A `Stmt` belongs to its
  `Conn`: after the connection is closed or dropped, every call on the
  statement is `E_CLOSED` (the statement handles are freed before the
  disconnect), and dropping it frees nothing twice. Each bound value lives
  in C memory the `Stmt` owns until the parameter is bound again or the
  statement is dropped, so the driver reads it at `execute`.

  In tasks, the calls that wait for the server (connect, execute, fetch,
  reading a value, the next result, describing a NULL parameter) are
  `blocking` externs: the task parks and the call runs on the helper pool,
  so other tasks run meanwhile. A cancellation or deadline is seen after
  the call returns (ODBC's own cancel is not used). Each Conn is used by one
  task at a time, like any value.

  Live tests (`lib/tests/run/mssql_live_*.obx`) read an ODBC connection
  string from `$MSSQL_TEST_DSN`, else from `~/.obo-mssql-dsn` (one line,
  mode 600), never print it, use only `#temp` tables, and exit 77 (skipped)
  when neither is there. For example, with the Microsoft driver installed:

      export MSSQL_TEST_DSN='Driver={ODBC Driver 18 for SQL Server};Server=localhost;UID=<user>;PWD=<password>;TrustServerCertificate=yes'
      python3 lib/run_tests.py mssql_

Every getter is exact or an error: no NULL becomes 0 or "" (`E_NULL`; use
`isNull`, `value` or `decimalOrNull`), no REAL becomes an int or a decimal
(`E_INEXACT`), and text is never parsed as a number it does not spell.
Handles (`Db`, `Stmt`, `Conn`, `Rows`) own their C object and free it when
dropped; `close()` consumes the handle, so there is no closed state to
check, and `openHandles()` counts what is live (the tests end at 0). Text
with a NUL byte cannot cross the C boundary and is refused with `E_INPUT`
before any call; bytes travel as a blob (SQLite) or bytea hex (PostgreSQL).

## markdown

CommonMark 0.31.2, written from the spec's own parsing strategy (appendix "A
parsing strategy") and checked against cmark 0.31.2: the HTML is cmark's, byte
for byte, in safe and unsafe mode, with smart punctuation, `data-sourcepos`
attributes, `hardbreaks` and `nobreaks`.

    import "markdown"
    let html = markdown.toHtml("# Hello\n\n*world*\n", markdown.defaults())

    var o = markdown.defaults()
    o.unsafe = true                         // raw HTML and every URL pass through
    o.smart = true                          // curly quotes, dashes, ellipses
    let doc = markdown.parse(src, o)        // the tree
    var w = doc.walker(doc.root())          // enter and exit events, like cmark's iterator
    while w.next(&doc) {
        if w.entering && doc.kind(w.node) == markdown.Kind.Heading { println(doc.level(w.node)) }
    }
    let out = doc.html(o)                   // or render a tree you walked

- **The tree** is one array of `Node` records and a node is an `int` index:
  `doc.root()` is the document, `doc.kind(id)`, `parent`, `firstChild`,
  `lastChild`, `next`, `prev`, `text` (Text, Code, HtmlInline, HtmlBlock,
  CodeBlock), `dest` and `title` (Link, Image), `info` (fenced CodeBlock),
  `level` (Heading), and `doc.at(id)` for the whole `Node` (list `ordered`,
  `start`, `tight`, `delim`; source positions `startLine`, `startCol`,
  `endLine`, `endCol`, which are cmark's, quirks included). Kinds are those of
  cmark: Document, BlockQuote, List, Item, CodeBlock, HtmlBlock, Paragraph,
  Heading, ThematicBreak, Text, SoftBreak, LineBreak, Code, HtmlInline, Emph,
  Strong, Link, Image. Another renderer (a terminal one, say) walks this tree.
- **Linear time.** Nothing recurses: nesting 100,000 deep (block quotes, lists,
  emphasis, brackets) parses and renders, and cmark's pathological inputs run
  in the same time as cmark's.
- **Data.** `markdown_entities.obx` (the 2,125 HTML5 references that end in
  `;`) and `markdown_unicode.obx` (Unicode 15.0.0 punctuation and symbols, white
  space, and full case folding for link labels) are generated by
  `gen_markdown_data.py` from the WHATWG `entities.json` and Python's
  `unicodedata`; do not edit them.
- **Where it follows cmark rather than the prose of the spec.** Where cmark's
  scanners differ from the spec's text, the module does what cmark does (HTML
  comments, processing instructions and CDATA are cmark's patterns; a title may
  end at a quote that follows a backslash; entities in a destination, title or
  info string are decoded before backslash escapes; `\` followed by a line
  end keeps the next line's leading white space). All of these were found by
  comparing with cmark on generated inputs (`tests/markdown.py`).
- Not in the module: the XML, man, LaTeX and CommonMark writers, and the GitHub
  extensions (tables, strikethrough, task lists, autolinks).

## What the library uses

Intrinsics: `strByte`, `strSlice`, `strFromBytes`, `strToBytes`, `intToStr`,
`i128ToStr`, `u128ToStr`, `u64ToStr`, `floatToStr`, `parseFloat`, `writeFd`,
`readFile`, `args`, `getenv`, `strFromC`, `strFindByte`, `strFindAny`,
`strFind` and `strFindPair` (the first index at or after `from` of a byte,
of any byte of a set, of a string, of a position `p` with `s[p + i1]` in one
set and `s[p + i2]` in another; 16 bytes a step with SSE2, for the regex
prefilters), `appendStr(mut &bytes, s)` (the bytes of `s` at the end of a
`[]byte` in one copy: `StrBuf.add`), and the built-ins `isNull`, `exit`,
`panic`. `task` also uses the library-only intrinsics `intoPtr` and
`fromPtr` (SPEC 15), to keep a task's argument and result and a `Shared`
or `Frozen` value in the runtime's structures.

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
| `obx_read(fd, b, n)`, `obx_seek(fd, off, whence)` | `read` and `lseek`: a count or offset, or -errno (in the native runtime, system calls: no libc) |
| `obx_clock_ns(which)` | `clock_gettime` realtime or monotonic, in ns |
| `obxlib_hash_key(i)` | word `i` (0 or 1) of the 16 `AT_RANDOM` bytes (`getauxval`; the native runtime reads the auxiliary vector); std derives its hash key from them |
| `obx_getcwd()` | `getcwd` into a static buffer |
| `obxlib_readlink(path)`, `obxlib_readlink_text()` | readlink into a static buffer; length or -errno, then the text |
| `obxlib_flush_out()` | flush the runtime's stdout buffer; errno of the first failed write since the last call, or 0 |
| `obxlib_isatty(fd)`, `obxlib_term_cols(fd)` | `isatty`; `TIOCGWINSZ` columns or 0 |
| `obxlib_alloc(n)`, `obxlib_free(p)` | a zeroed C buffer from the runtime's allocator (counted by `OBX_CHECK=1`); for `ffi` |
| `obxlib_peek(p, i)`, `obxlib_poke(p, i, b)` | read or write byte `i` of C memory; for `ffi` |
| `obxlib_peek_i32`, `obxlib_poke_i32`, `obxlib_peek_ptr`, `obxlib_poke_ptr` | element `i` of a C `int32_t[]` or `void*[]`; for `ffi` |
| `obxlib_copy_in(dst, at, src, n)`, `obxlib_copy_out(dst, src, at, n)` | `memcpy` between C memory and an obx byte array; for `ffi` |
| `obxlib_task_scope_open()`, `obxlib_task_scope_close(s, w)` | open a scope; park until its tasks have finished; for `task` (`w`: the caller's `"file:line"`, reported by a trap, here and below) |
| `obxlib_task_start(s, run, job, resdrop)` | a task on a stack from the free list that runs `run(job)`; for `task` |
| `obxlib_task_wait(t, w)`, `obxlib_task_wait_any(ts, n, w)`, `obxlib_task_take(t)` | park until done (0), cancelled (1), abandoned (2) or past the deadline (3); the first done of n (-1 cancelled, -2 deadline); the result box |
| `obxlib_task_forget(t)`, `obxlib_task_join(t, w)`, `obxlib_task_detach(t)` | a dropped future (cancel, drop the result when there is one); cancel and park until finished; `spawn`'s future, given up without cancelling |
| `obxlib_task_yield(w)`, `obxlib_task_cancelled()`, `obxlib_task_check(w)` | yield to the run queue (also polls sockets and timers every 64 calls); whether the running task is cancelled; the checks before an operation that can park: exit 13 inside `with`, then cancelled (1) or past the deadline (3) |
| `obxlib_task_sleep(ms, w)`, `obxlib_task_within(ms)`, `obxlib_task_restore(old)` | park on a timer; set the running task's deadline (the earlier wins), giving the old one; put it back |
| `obxlib_net_listen(fam, addr, port, backlog, reuseport)`, `obxlib_net_connect(fam, addr, port, &pending)`, `obxlib_net_accept(fd)` | non-blocking sockets registered with epoll (edge-triggered, once; a listener at its first wait, with `EPOLLEXCLUSIVE` when the kernel takes it); fd or -errno (`pending`: the connect is in progress) |
| `obxlib_net_read(fd, b, n)`, `obxlib_net_write(fd, b, n)` | `read`, `send` with `MSG_NOSIGNAL`; count or -errno (`-EAGAIN`: park) |
| `obxlib_net_wait(fd, dir, w)` | park until the socket is readable (0) or writable (1): 0, or 1 cancelled, 3 past the deadline, -errno (a listener's registration failed) |
| `obxlib_sig_watch(mask)`, `obxlib_sig_wait(mask, w)` | block the signals of `mask` (bit s-1 for signal s) and read them from a `signalfd` in the epoll set: 0 or -errno; park until one arrives: its number, -1 cancelled, -3 deadline |
| `obxlib_workers_begin(w)`, `obxlib_workers_end()` | the checks before the first fork (exit 15 at `w`), then block `SIGTERM`, `SIGINT`, `SIGCHLD`; drop what is pending and restore the mask |
| `obxlib_proc_fork()`, `obxlib_proc_wait(ms, &status)`, `obxlib_proc_kill(pid, sig)` | flush and fork (the child: a new epoll set, the old mask, `PR_SET_PDEATHSIG`); the supervisor's wait: a child's pid (`status`: its wait status), `-sig` for `SIGTERM`/`SIGINT` (`status`: the sender's pid) or 0 after `ms`; `kill` |
| `obxlib_proc_spawn(blob, argc, envc, modes, &out)` | fork and exec a program (`proc`): PATH lookup, redirections, pipes (non-blocking, in the epoll set), environment, directory and the exec errno through a close-on-exec pipe all in the parent or by raw syscalls in the child; the pid or -errno; `out`: the pidfd (-1: none) and the parent's pipe ends |
| `obxlib_proc_write(fd, b, n)`, `obxlib_proc_reap(pid, block, &status)`, `obxlib_proc_nap(ms)` | a pipe write with SIGPIPE blocked and taken back (EPIPE); `wait4` (`block` 0: WNOHANG) with EINTR retried; a thread sleep (no-pidfd kernels, after a cancellation) |
| `obxlib_worker_exit(code)`, `obxlib_worker_stop()` | a worker's end (flush; under `OBX_CHECK=1` its leak check); `SIGTERM` to the supervisor, -1 when not a worker |
| `obxlib_fd_wait(fd, dir, ms, cancellable, w)` | park until a descriptor a C library reads itself (libpq's socket) is readable (0), writable (1) or either (2): armed one-shot in the epoll set each time; ends at cancellation and the deadline when `cancellable`, after `ms` when `ms >= 0`; 0, 1 cancelled, 3 deadline or `ms`, -errno |
| `obxlib_net_addr(fd, peer, out)`, `obxlib_net_nodelay(fd, on)`, `obxlib_net_soerror(fd)`, `obxlib_net_shutdown(fd, how)`, `obxlib_net_close(fd)` | the local or peer address (family, port, 16 address bytes); `TCP_NODELAY`; `SO_ERROR`; `shutdown`; close (and forget the fd's waiters) |
| `obxlib_task_promise(resdrop)`, `obxlib_task_complete(t, r)`, `obxlib_task_abandon(t)` | promises |
| `obxlib_sh_new(box, drop)`, `obxlib_sh_enter(s, w)`, `obxlib_sh_leave(s, box)` | a `Shared` block; lend its box to `with` (exit 13 when already lent; `w` is kept for the messages while the `with` is open) and take it back. A `Frozen` is such a block that is never entered: `read` lends the box directly |

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
- `regex`: no literal prefilter or DFA yet (R2), so a search runs the Pike VM
  over the whole text; no Unicode blocks, full case folding, `RegexSet` or
  byte-oriented matching (R3).
- No `Deque`, no `BTreeMap`/ordered map; `Set` iterates in insertion order
  until a remove.
- Errors are errno ints in `os` (setEnv, setCwd) but `IoError` in `io` and
  `fs`.
- `mssql`: no MARS (one open result per connection), no output or
  table-valued parameters, no bulk copy, no streaming of a long value (it
  is read whole, up to maxValue); a NULL parameter costs a server round trip
  (SQLDescribeParam) to learn its type; date and time columns are text, not
  `time` values.
