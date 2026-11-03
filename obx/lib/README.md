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
| `std.obx` | 1,400 | Option, Result, Unit, Where; Eq/Ord/Hash/Show for the primitives; print/println/eprint/eprintln; min/max; float text and math methods; StrBuf and basic string helpers; Decimal; sort; Map |
| `money.obx` | 200 | Currency, USD/EUR/GBP/JPY/CHF, Money[C] |
| `math.obx` | 130 | float constants, parse, fixed, toDecimal, near |
| `collections.obx` | 390 | stable sorts, searching, rearranging, copying, Set, Heap |
| `strings.obx` | 490 | search, split/join, replace, trim, case, int parse/format, padding, UTF-8 |
| `io.obx` | 210 | IoError, whole-file read/write/append, readLines, readStdin, buffered Writer |
| `fs.obx` | 260 | stat/lstat, list, mkdirAll, remove/removeAll, rename, symlink, chmod, paths |
| `os.obx` | 60 | arguments, environment, pid, working directory, quit |
| `time.obx` | 160 | wall and monotonic clocks, sleep, UTC dates, ISO 8601 text |
| `sqldialect.obx` | 260 | Value, DbError and its codes, SQL fragments with placeholders per dialect, exact decimal text and (p, s) checks |
| `ffi.obx` | 140 | C memory for modules that call C: owned `Mem` (alloc, cstr, copyBytes), unchecked peek/poke/bytes |
| `sqlite.obx` | 440 | SQLite 3 through libsqlite3: Db, Stmt |
| `postgres.obx` | 690 | PostgreSQL through libpq, asynchronous for tasks: Conn, Rows, Pool, Lease |
| `mssql.obx` | 1,050 | Microsoft SQL Server through ODBC (libodbc): Conn, Stmt, strict UTF-8/UTF-16 |
| `task.obx` | 270 | concurrent tasks on one thread: Scope, Future, Promise, Shared, all/any, yield, cancellation, sleep, deadlines |
| `net.obx` | 440 | TCP over IPv4 and IPv6 for tasks: listen, accept, connect, read, write |

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
loop for the sockets of `net` and the timers.

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
`PQconnectStart`, `PQconnectPoll` and `PQcancel`.

### net

TCP over IPv4 and IPv6 for tasks (SPEC 16; design/concurrency.md, stage C1b).
Sockets are non-blocking and registered with the runtime's epoll loop; a call
that would block parks the task until the socket is ready, so other tasks run
meanwhile, and a plain program (its main task) uses the same calls.

- Addresses are text: `"127.0.0.1:8080"`, `"[::1]:8080"`, `":8080"` and
  `"0.0.0.0:8080"` (every IPv4 interface), `"localhost:8080"` (127.0.0.1),
  port 0 for any free port. Only numeric addresses and `localhost`: a name
  lookup (`getaddrinfo` on the helper pool) is not there yet. `canonicalAddr(addr): Option[str]` checks and normalizes one
  (IPv6 in RFC 5952 form). A malformed address is `EINVAL`.
- `listen(addr): Result[Listener, IoError]` (`SO_REUSEADDR`, backlog 4096);
  `Listener`: `localAddr(): str`, `accept(): Result[Conn, IoError]`,
  `close()`.
- `connect(addr): Result[Conn, IoError]`: a non-blocking connect that parks
  until it is done (`ECONNREFUSED` and the like come from it).
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

A `Conn` is used by one task at a time (it is owned; a split into read and
write halves is not there yet). `cmd/httpd.obx` is an HTTP/1.1 server built
on it, and `cmd/httpload.obx` a load client.

`both` and `forEach` (concurrent children that borrow the parent's locals)
are not there yet: a task's function is a plain `fn`, and passing borrows
into a task needs the checker's help (design/concurrency.md, section 5).

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

## What the library uses

Intrinsics: `strByte`, `strSlice`, `strFromBytes`, `strToBytes`, `intToStr`,
`i128ToStr`, `u128ToStr`, `u64ToStr`, `floatToStr`, `parseFloat`, `writeFd`,
`readFile`, `args`, `getenv`, `strFromC`, and the built-ins `isNull`, `exit`,
`panic`. `task` also uses the library-only intrinsics `intoPtr` and
`fromPtr` (SPEC 15), to keep a task's argument and result and a `Shared`
value in the runtime's structures.

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
| `obxlib_task_scope_open()`, `obxlib_task_scope_close(s, w)` | open a scope; park until its tasks have finished; for `task` (`w`: the caller's `"file:line"`, reported by a trap, here and below) |
| `obxlib_task_start(s, run, job, resdrop)` | a task on a stack from the free list that runs `run(job)`; for `task` |
| `obxlib_task_wait(t, w)`, `obxlib_task_wait_any(ts, n, w)`, `obxlib_task_take(t)` | park until done (0), cancelled (1), abandoned (2) or past the deadline (3); the first done of n (-1 cancelled, -2 deadline); the result box |
| `obxlib_task_forget(t)`, `obxlib_task_join(t, w)`, `obxlib_task_detach(t)` | a dropped future (cancel, drop the result when there is one); cancel and park until finished; `spawn`'s future, given up without cancelling |
| `obxlib_task_yield(w)`, `obxlib_task_cancelled()`, `obxlib_task_check(w)` | yield to the run queue (also polls sockets and timers every 64 calls); whether the running task is cancelled; the checks before an operation that can park: exit 13 inside `with`, then cancelled (1) or past the deadline (3) |
| `obxlib_task_sleep(ms, w)`, `obxlib_task_within(ms)`, `obxlib_task_restore(old)` | park on a timer; set the running task's deadline (the earlier wins), giving the old one; put it back |
| `obxlib_net_listen(fam, addr, port, backlog)`, `obxlib_net_connect(fam, addr, port, &pending)`, `obxlib_net_accept(fd)` | non-blocking sockets registered with epoll (edge-triggered, once); fd or -errno (`pending`: the connect is in progress) |
| `obxlib_net_read(fd, b, n)`, `obxlib_net_write(fd, b, n)` | `read`, `send` with `MSG_NOSIGNAL`; count or -errno (`-EAGAIN`: park) |
| `obxlib_net_wait(fd, dir, w)` | park until the socket is readable (0) or writable (1): 0, or 1 cancelled, 3 past the deadline |
| `obxlib_fd_wait(fd, dir, ms, cancellable, w)` | park until a descriptor a C library reads itself (libpq's socket) is readable (0), writable (1) or either (2): armed one-shot in the epoll set each time; ends at cancellation and the deadline when `cancellable`, after `ms` when `ms >= 0`; 0, 1 cancelled, 3 deadline or `ms`, -errno |
| `obxlib_net_addr(fd, peer, out)`, `obxlib_net_nodelay(fd, on)`, `obxlib_net_soerror(fd)`, `obxlib_net_shutdown(fd, how)`, `obxlib_net_close(fd)` | the local or peer address (family, port, 16 address bytes); `TCP_NODELAY`; `SO_ERROR`; `shutdown`; close (and forget the fd's waiters) |
| `obxlib_task_promise(resdrop)`, `obxlib_task_complete(t, r)`, `obxlib_task_abandon(t)` | promises |
| `obxlib_sh_new(box, drop)`, `obxlib_sh_enter(s, w)`, `obxlib_sh_leave(s, box)` | a `Shared` block; lend its box to `with` (exit 13 when already lent; `w` is kept for the messages while the `with` is open) and take it back |

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
- `mssql`: no MARS (one open result per connection), no output or
  table-valued parameters, no bulk copy, no streaming of a long value (it
  is read whole, up to maxValue); a NULL parameter costs a server round trip
  (SQLDescribeParam) to learn its type; date and time columns are text, not
  `time` values.
