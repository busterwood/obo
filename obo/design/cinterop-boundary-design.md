# Native C interop: generic boundary types (v2 proposal, revision 3)

Status: **design for independent review; no implementation authorized.**
Date: 2026-11-01. Revision 3 follows the user's decision of 2026-11-01: foreign handles are
**file-consistent** (explicit borrowing close, leak census under `OBO_CHECK=1`), not must-close.
Revision 2 (must-close, consuming defer, compile-time ownership checks) is withdrawn. Revision 3 also
addresses the independent review (verdict REVISE; section 10).
Baseline: `main` `44e812a`. Builds on experimental cinterop per `native-c-interop-design.md` section 14.10.

This note extends `native-c-interop-design.md` sections 8 and 14 and keeps its steering (section 1.1: a
small toolkit plus an agent skill, no universal generator). Nothing here is implemented. No compiler,
runtime, `.sym`, ABI or package version is taken. This note changes no language rule. It proposes a
generated `close` per foreign type, one new use-after-close check in the native thunk, and a leak count
that reuses exit 8 (3.4, 7.2).

## Summary

1. Four primitives in one `__cinterop` seam: borrowed input, copy-out, foreign handle types, and
   typed declarations. Each is a C-type shape, not a library feature.
2. A foreign handle is a compiler-generated owned record with one private pointer word. It follows the
   file model (`closeFile`, `Dir.close`, `sysfile.close`): an explicit `close(mut &T): Result[bool]`,
   normally deferred, that zeroes the word before the C release call. A second close is `ok(false)` with
   no C call. Dropping an open handle leaks it; nothing releases it implicitly.
3. Use after close is a deterministic stop: a native that borrows a zero word exits 7, the code that
   checked `^T` handles already use. `is_open(&T)` lets wrappers return an `err` first.
4. Leaks are counted per foreign type at creation and decremented by `close`. Under `OBO_CHECK=1`,
   a nonzero count at normal end exits 8, the code that reports leaked file descriptors today.
5. Fields, optionals, `Result`, arrays and globals need no special rules. They behave as records do now.
6. About 16 generic kinds, with flags instead of separate kinds. No kind names a library, SQL statement
   or packet format. libpq and SQLite bind through small obo wrappers (5.2, 6.3). zlib keeps its adapter.
7. SQLite adopts `sqlite3_close_v2` semantics: an orphaned statement keeps working, and a read lock it
   holds makes another connection's write fail with `SQLITE_BUSY`. Both are tested.
8. Stages (a) to (h) in section 8. Stage (d), foreign handles, is the riskiest and adds no ownership-checker rules.

---

## 1. Problem and goals

Libpq and SQLite are bound through layers that exist only because the boundary is scalar.
Integer-token registries keep `PGconn*`, `PGresult*`, `sqlite3*` and `sqlite3_stmt*` on the C side
(`toolkit/libpq_adapter.c` 417 lines, `cres.c` 205, `cres.h` 60, `libpq_adapter.h` 40;
`db/sqlite/sqlite_adapter.c` 528). Bytes cross one native call per byte (`put_bytes`,
`examples/libpq.obx:34-47`), so a 1 MiB `bytea` costs about two million crossings. The libpq packet is
built in obo and parsed again in C. `nativeCall` (`obo/src/obo.obo:7361-7378`) accepts only `int`
arguments.

Goals. G1: a small fixed set of generic kinds, reusable by any library. G2: C pointers never become obo
values; borrowed input lives only for the call; bytes return only by copy. G3: a foreign handle is closed
explicitly, the way files are, and leaks are visible under `OBO_CHECK=1`. G4: libpq, SQLite and
db/postgres bind from specs with little obo, keep every existing limit, and need no C helper beyond
conninfo. G5: gated stages that mark checker exposure.

---

## 2. Verified constraints

C1. **No collector; explicit free only.** `rt_collect` returns at once (`obo/runtime/runtime.c:309-310`).
Blocks return only through `free_block` (`runtime.c:382`), reached from `rt_free` (`runtime.c:451-481`).

C2. **Arrays are a stable header over a replaceable block.** Header `{len, cap, data, esize}`
(`runtime.c:377`); elements at `data + 8` (`runtime.c:541-548`); growth frees the old block (`rt_append`,
`runtime.c:564-583`). A pointer into elements lasts only until the next growth.

C3. **Strings are `[len][bytes]`, no terminator, reference counted** (`runtime.c:428`, `433`). The nil string
is 0, and `sdata(0)` would be address 8 (`runtime.c:597-598`). Literals have no NUL (`obo/src/obo.obo:2993-3004`).

C4. **No pointer type in obo.** The seam accepts only `i64` arguments (`cinterop.py:220-223`) and sign-extends
`i32` returns (`cinterop.py:215-216`).

C5. **`exit` and traps run no defers and free nothing.** `exit` (`obo.obo:8746-8751`), block exit
(`callDefers`, `obo.obo:5353`; `closeBlock`, `5372`), `die_code` (`runtime.c:96`), `rt_exit` (`runtime.c:1539`),
hosted `OBO_EXIT` (`runtime.c:24-36`).

C6. **Record drop calls no foreign hook.** `rt_free` frees owned fields and decrements strings (`runtime.c:451-481`).
A foreign word is kept in neither list, so `rt_free` never reads or frees it.

C7. **The checker tracks moved/not-moved per owned local.** `isOwned` covers records, closed types, arrays,
optionals and results (`obo.obo:4052-4060`). `drop(x)` marks `x` moved (`obo.obo:8416-8433`). A move is not
allowed inside `defer` (`obo/LANGUAGE.md:303`). A defer may read an owned local, and that local may not be moved
after the defer (`LANGUAGE.md:450-456`). `take(x)` moves out of an optional, field or element and leaves nil
(`LANGUAGE.md:263`; `obo.obo:8458`). This design adds no rule to any of these.

C8. **Checked handles.** A use of a freed `^T` object exits 7 (`rt_deref`, `runtime.c:498`; `LANGUAGE.md` section 5).

C9. **`cres` is bounded and integer-typed.** 64 entries (`cres.h:29`); close poisons the generation (`cres.c:78`).
`cres.c` remains for existing packages (7.2, D7).

C10. **Native calls need the typed sidecar.** `--native-relocs` is required; the sidecar records argument and
result types (`obo.obo:15646-15672`). The tool checks it against the spec (native design section 14.2).

C11. **Threads are restricted.** Worker closures are limited to copied bytes and thread-safe natives that never
call obo (`systems-threads-design.md` section 4.1, T1).

C12. **Other facts.** `rt_set_host_exit` is appended by the cinterop build without changing baseline bytes
(`runtime.c:26`). `rtCall` passes at most four register arguments (native design section 3, which also fixes the
thunk frame). `LANGUAGE.md` section 9 assigns codes 2 to 9 and 10 to decimal traps; 11 and up are unassigned.

C13. **Library non-retention is documented, not checked.** `PQconnectdb` and `PQexecParams` copy their inputs;
`PQgetvalue` pointers last until `PQclear`. SQLite's `sqlite3_column_text` and `_blob` pointers last until the next
step, reset, finalize or conversion of that column.

C14. **The SQLite adapter's lifetime rule is explicit close only.** `sq_conn_close` refuses while `stmt_live != 0`
(`db/sqlite/sqlite_adapter.c:229-232`), and `release_stmt` decrements through `s->owner` (`sqlite_adapter.c:140`).

C15. **The file model is the precedent.** `Dir.close(mut d: &Dir): Result[bool]` (`obo/lib/fs.obx:214`) and `sysfile.close(f: &File): Result[bool]`
(`obo/experimental/sys/sysfile.obx:307`) return `ok(false)` for a closed handle (`sysfile.obx:274`). The census runs
in `rt_finish` at normal end under `OBO_CHECK=1`, counts open descriptors, and reports exit 8 (`runtime.c:506-520`;
`LANGUAGE.md` section 9, row 8). The systems stdlib says the same: "explicit lifecycle", "no hidden finalizer"
(`systems-stdlib-design.md` section 4.2, lines 399-427).

C16. **Existing limits to keep.** SQL text 1 MiB (`libpq_adapter.c:25`, `postgres_adapter.c:32`, `sqlite_adapter.c:22`).
Values 16 MiB (`sqlite_adapter.c:23`, `postgres_adapter.c:33`). Connection string 16 MiB, equal to `CRES_MAX_BUFFER`
(`libpq_adapter.c:160`). Path 4 KiB (`sqlite_adapter.c:21`). Parameters 32 (`postgres_adapter.c:31`). Decimal text 98,342
bytes (`postgres_adapter.c:34`, checked at `105`). Default `connect_timeout=5` (`libpq_adapter.c:110`).

---

## 3. Proposed boundary types

### 3.1 Kinds

A spec format `cinterop-proto/2` is proposed (experimental, no stable number). Argument kinds name the obo
argument they consume as `kind@k`. Flags follow a colon.

| Argument kind | obo argument | C parameters |
|---|---|---|
| `i64` | `int` | 1 `long` |
| `i32`, `u32` | `int` | 1 `int` or `unsigned int`, range-checked |
| `ints@k:i32`, `ints@k:u32` | `[]int` | 1 pointer to a copied array, range-checked per element |
| `ints@k:len` | list `k` | 1 `int`, the element count of list `k`, checked to fit `int` |
| `null` | none | pointer 0 |
| `const:<n>` | none | fixed value `n`; `-1` only as a destructor (6.2, point 3) |
| `borrow<T>@k` | `T`, live | `T *` from the word; a zero word exits 7 before the call (3.4) |
| `borrow_bytes@k` | `[]byte` or `string` | `const void *p` (never NULL), `int n` (checked) |
| `borrow_cstr@k` | as above | `const char *`, NUL-checked scratch copy |
| `nil_bytes_list@k` | `[]?[]byte` | `const char *const *v`, `const int *len` (count from `ints@k:len`) |
| `out_own<T>` | none (adds a result field) | `T **` slot, a live handle from the write (R12) |
| `out_offset@k` | none (adds an `int` field) | `const char **` slot, offset inside argument `k`, or -1 |

| Return kind | obo result | Meaning |
|---|---|---|
| `void` | none | unit |
| `i32`, `u32`, `i64` | `int` | sign-extended or zero-extended (`Oid`) |
| `nonzero` | `int` | 1 if the pointer is non-NULL, 0 otherwise; the pointer is discarded |
| `own<T>`, `own_opt<T>` | `T`, `?T` | owned handle, counted (3.4); NULL traps for `own` |
| `cbytes` with `"length": sym`, `"cap": n` | `?[]byte` | copy of `(value, length)`, value first; nil if over cap (3.3) |
| `cstr_out` | `[]byte` | copy up to NUL, bounded; NULL gives empty |

A native with `out_*` kinds returns a compiler-generated record: `rc: int` plus one field per out kind,
each field `?T`. An `out_own` field counts from the write, so the wrapper must close it on every path (R12).

**No library-specific kinds.** Every kind is a C type shape: a scalar, a foreign pointer, a byte buffer, a list,
or an out slot. No kind names a library function, a statement, a packet format or a SQL rule. Those belong in obo
wrappers, or in a trusted C helper that uses only generic kinds (6.1, `sq_stmt_errcode`).

### 3.2 Primitive 1: borrowed input

For `borrow_bytes`, an array passes `data + 8` and `len` (`runtime.c:377`, `541-548`); an empty array points at a
static block (`runtime.c:404-410`), so the pointer is never NULL. A string passes `s + 8` and `*s`; the nil string uses
a static zero block (`runtime.c:597-598`).

**Timing (review 12).** A native call evaluates all argument expressions first. The thunk then loads every borrow's
pointer and length, makes the call, and nothing runs in between (R2). A later argument that changes a borrowed array
would be seen by the native, so a call may not both borrow a place and take a mutable reference to it.
`f(&a, grow(&a))` is a compile error. A test fixes this (section 9).

**`borrow_cstr`.** A hosted helper copies the input into `len + 1` zeroed bytes, traps on an embedded NUL (backstop),
and the thunk frees it after the call. Wrappers pre-check NUL and return `err`. Terminators are not reserved in
ordinary arrays or strings, which would change every layout and break the byte-identical gate. `borrow_bytes` never
checks NUL. SQLite's `prepare` and `bind` take a length, so they need no NUL.

### 3.3 Primitive 2: copy-out

`cbytes` calls the value function, keeps `ptr` in its frame, calls the length function with the same argument words,
and copies with `copy_out`, which allocates through `new_block` (`runtime.c:541-548`). No obo code runs between the
calls. If the length is above `"cap"`, or `ptr` is NULL with a positive length, the native returns **nil**, and the
wrapper returns `err` (review 3; the adapter returns `err` today, `sqlite.obx:380-384`). A copy-allocation failure is
the runtime's out-of-memory exit 2 (`new_block`), not a nil return (review 19). `ptr == NULL` with `len == 0` gives an
empty array. The helper never calls `strlen`, so NULs are kept. SQL NULL never reaches `cbytes`: wrappers check the type
first (6.2, point 2). `cstr_out` copies up to NUL within 16 MiB.

### 3.4 Primitive 3: foreign handles, explicit close, census

**Declaration** (D1): `type Conn = foreign "PGconn"`. The spec names the release symbol and its result shape:
`"release": "PQfinish", "release_result": "void"` (or `"int"`). Obo code never names the release function.

**Representation.** A compiler-generated `ref record` with one private field, the C word. The word is in neither
the owned nor the string offset list, so `rt_free` never reads or frees it (review 6). Obo code cannot name the
field, so a closed word is seen only through the generated methods.

**Generated close.** For each foreign type the compiler emits `pub fn close(mut t: &T): Result[bool]`:
- if the word is 0, return `ok(false)` and make no C call (double close, review 9);
- otherwise copy the word, set the field to 0, decrement the type's census counter, call the release symbol with
  the copy, and return `ok(true)`. For `"release_result": "int"`, a nonzero C result is `err` with that code. The handle
  is gone either way.

The field is zeroed before the C call, so a release that traps or re-enters cannot release twice. The method takes
`mut &T`, so it may be called on a local, a field or a reference. It is deferred the way `Dir.close` is:
`defer c.close()`. The existing defer rules apply unchanged: the defer uses `c`, so `c` may not be moved after it.

**Why zeroing one word is enough (no cres).** A foreign handle is owned. Obo moves it and never copies or converts it
(R6), so there is exactly one copy of the C pointer in obo, and every `&T` reference points at that same record. Zeroing
the word makes every obo reference see the close at once. What remains is C's own contract: a library that keeps a
handle internally (a `sqlite3_stmt` inside a `sqlite3`) is covered by that library's rules, here `sqlite3_close_v2`
(6.2, point 1). `cres` exists because integer tokens are copied freely, so it needs a generation counter against stale
copies. A foreign handle has no copies to go stale, so it needs no generation.

**Use after close.** Every native with a `borrow<T>` argument checks the word in its thunk before forming the C pointer.
A zero word stops the program with exit 7 and the message "foreign handle used after close". This is the same code that
checked `^T` handles use. The thunk is the place of the check, not each wrapper, because a per-call check in every
wrapper is easy to forget and a C call such as `PQstatus(NULL)` is not safe to make. The check is one compare and is not
gated by `OBO_CHECK`, since it protects memory safety in normal runs. For a recoverable error instead, wrappers call
`is_open(&T): bool` first and return `err` (the `Closed` state in `sysfile`). `is_open` is a query, not a replacement
for the thunk check.

**Census (leaks).** The hosted runtime keeps one counter per foreign type. The counter goes up when a native returns a
nonzero word as `own` or `out_own`, and goes down only in the generated `close`. At normal end of `main`, under
`OBO_CHECK=1`, a nonzero counter prints the type and count and exits 8. That is the code `LANGUAGE.md` section 9 gives
leaked file descriptors, generalized to "file descriptors opened by `openFile` or foreign handles never closed". Without
`OBO_CHECK=1` nothing is counted or printed, which matches fds.

**Dropping an open handle is a leak.** Scope exit, `drop`, overwrite, or the `rt_free` of an enclosing record does not
release the word and does not trap. The counter stays up, and the leak is reported at normal end. This is the file rule:
dropping an open fd leaks it.

**Fields, optionals, results, arrays and globals.** No special rules. A record with a foreign field is a record, and
`Result[T]` and `?T` work as they do for records now. An array of foreign handles is an array of records, so dropping
the array drops its handles, which then leak as any dropped handle does. A global handle is counted like any other live
handle at normal end.

**Exit and traps.** Nothing is closed and no release symbol runs (C5). Whether `exit` reaches the census is checked at stage (d).

**Close errors.** A release declared `"release_result": "int"` gives `err` from `close`; the handle is gone either way.

### 3.5 Typed native declarations and the sidecar (D1)

The builtin `__cinterop` is typed `int` today (`obo.obo:7361`), so each native needs a body-less declaration. The spec
binds each name to its kinds. The compiler checks each parameter and result against the kind table, and
`--native-relocs` refuses a mismatch. Unit results use `void`. Example: `native fn getvalue(r: &Res, row: int, col: int): []byte`.

The sidecar becomes `cinterop-relocs 2` (argument and result kinds, expanded counts, effects kept as `freesall
writeshandles`). The manifest becomes `cinterop-proto/2` and adds `types` (release symbol and result shape), expansions,
constants and `signature_check` (6.2, point 6). `close` is generated; no release table is written. Each function gets
one thunk, keeping `rsp` 16-byte aligned, within the frame layout native design section 3 fixes. Calls with more than six C parameters (`PQexecParams` has eight) pass extra
words on the stack. Exporting a foreign type needs a `.sym` writer change, deferred to the first exported foreign type (D6).

---

## 4. Soundness rules

R1. **Borrows are call-scoped.** No pointer-valued obo value exists. Borrow kinds are argument-only and are never
returned, stored or retained.

R2. **No obo code runs between loading a borrow and the native return** (native design section 2). The `afterCall`
reference invalidation (native design section 8.4) still applies.

R3. **Borrows are reads.** They do not move the source. A call may not borrow a place and mutate it (3.2). A copy-out
result never aliases a borrow or a C buffer.

R4. **NUL.** `borrow_cstr` rejects an embedded NUL in the helper, and wrappers pre-check. `cstr_out` stops at NUL within
its cap. `cbytes` never calls `strlen`. Binary data is never NUL-checked.

R5. **Non-NULL for empty values.** Empty arrays, the nil string and zero-length borrows pass a non-NULL pointer. NULL
comes only from `null` or a nil list element. For SQLite this matters: a NULL data pointer makes `sqlite3_bind_blob` bind
SQL NULL.

R6. **Single owner, not forgeable.** A foreign handle cannot be copied or converted from or to an integer, and its field
is private. So its word exists in exactly one place, which is what makes zeroing one word sufficient (3.4).

R7. **Close is the only release.** Only the generated `close` calls a release symbol. A native never frees a foreign word,
and `rt_free` never reads one.

R8. **Leaks are visible, not prevented.** An open handle that is dropped stays counted and is reported at normal end under
`OBO_CHECK=1` (exit 8). No path releases it implicitly.

R9. **Threads.** Borrowed arrays must be unreachable from other workers during the call. Foreign handles are
thread-confined (C11).

R10. **Bounds first.** Counts are capped, lengths fit `int`, copy-out lengths are capped, and `i32`/`u32` values are range
checked, all before a pointer is formed. Obo wrappers check row, column and parameter indexes against the counts before any
native call (review 2).

R11. **Trusted C.** Trusted and unchecked: each function's copying, non-retention and NULL behaviour; the C signature (header,
or `declarations-only` with no claim); release functions being correct; returned pointers valid until their owner is closed;
writes confined to the given buffers; no callbacks into obo; thread safety. Hashes give provenance only (native design section 10).

R12. **Out handles are counted from the write.** An `out_own` slot that C has written is a live handle whatever the return
code, so the wrapper must close it on the error path too, or the census reports it.

---

## 5. Worked example 1: libpq

### 5.1 Functions and kinds

| libpq function | Kinds | Obo wrapper |
|---|---|---|
| `PQlibVersion` | `i32` | `lib_version` |
| `PQconnectdb` | `borrow_bytes@0`; ret `own_opt<PGconn>` | `connectdb`, used by `connect` (5.2) |
| `PQstatus` | `borrow<PGconn>@0`; ret `i32` | `status` |
| `PQerrorMessage` | `borrow<PGconn>@0`; ret `cstr_out` | `error_message` |
| `PQfinish` | release of `Conn` (spec `"release"`) | generated `close` |
| `PQexecParams` | `borrow<PGconn>@0`, `borrow_cstr@1`, `ints@2:len`, `ints@3:u32`, `nil_bytes_list@2`, `ints@4:i32`, `i32@5`; ret `own_opt<PGresult>` | `exec_params` |
| `PQresultStatus`, `PQntuples`, `PQnfields` | `borrow<PGresult>@0`; ret `i32` | `result_status`, `ntuples`, `nfields` |
| `PQgetisnull`, `PQgetlength` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `i32` | `is_null`, `length` |
| `PQgetvalue` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `cbytes` (length `PQgetlength`, cap 16 MiB) | `value` |
| `PQfname`, `PQresultErrorMessage` | `borrow<PGresult>@0`, `i32@1` or none; ret `cstr_out` | `fname`, `result_error` |
| `PQftype`, `PQfmod` | `borrow<PGresult>@0`, `i32@1`; ret `u32` or `i32` | `ftype`, `fmod` |
| `PQclear` | release of `Res` (spec `"release"`) | generated `close` |
| `PQconninfoParse` | out-parameter and struct array | stays in C (5.4) |

### 5.2 libpq wrapper (sketch, not compiled)

```obo
module libpq

type Conn = foreign "PGconn"                     // spec: release PQfinish, void
type Res = foreign "PGresult"                    // spec: release PQclear, void
type Rows = ref record { h: ?Res; pub fmt: int } // h is nil once closed

native fn connectdb(conninfo: &[]byte): ?Conn    // own_opt<PGconn>
native fn status(c: &Conn): int
native fn error_message(c: &Conn): []byte
native fn conninfo_has_timeout(s: &[]byte): int  // the one C helper (5.4)
native fn exec_params(c: &Conn, sql: &[]byte, params: &[]?[]byte,
                      oids: &[]int, formats: &[]int, binary: int): ?Res

pub fn connect(conninfo: string): Result[Conn] {
    if len(conninfo) > MAX_DSN { return err("connection string is too large", 2) }  // 16 MiB
    var raw = encodeString(conninfo)
    if hasNul(&raw) { return err("connection string contains a NUL byte", 2) }
    if conninfo_has_timeout(&raw) == 0 { raw = appendString(raw, " connect_timeout=5") }
    var c = connectdb(&raw)
    if c == nil { return err("connection could not be allocated", 2) }
    var conn = take(c)
    if status(&conn) != 0 {
        let msg = string(error_message(&conn))
        conn.close()                             // explicit, as Dir.close is
        return err(msg, 1)
    }
    return ok(conn)                              // the caller defers close
}

pub fn (mut r: &Rows) close() {
    if r.h != nil {
        var h = take(r.h)
        h.close()
    }
}
```

Caller: `var db = try connect(dsn)` then `defer db.close()`. Nothing in the rules forces either line; a missing
`close` is a census leak under `OBO_CHECK=1`.

`exec` checks SQL length against 1 MiB and parameters against 32, builds ordinary arrays (no packet), and calls
`exec_params`. A nil result becomes `err` with the connection's message. A parse failure in `connect` returns the helper's
diagnostic as `err`, as `open` does today (`postgres.obx:385`). The wrappers check `ntuples`, `nfields` and the row and
column index before `is_null`, `length`, `value` or `ftype`: today's adapter returns -1 or 0 for these
(`PQgetisnull(res,9,0)=1`, `PQftype` 0), so bounds are obo's job (review 2).

### 5.3 libpq spec fragment

```json
{"format": "cinterop-proto/2", "module": "libpq", "platform": "x86_64-linux-gnu",
 "abi": "sysv-amd64", "libs": ["-l:libpq.so.5"], "signature_check": "declarations-only",
 "types": [{"name": "PGconn", "obo": "Conn", "release": "PQfinish", "release_result": "void"},
           {"name": "PGresult", "obo": "Res", "release": "PQclear", "release_result": "void"}],
 "functions": [
   {"name": "exec_params", "symbol": "PQexecParams",
    "args": ["borrow<PGconn>@0", "borrow_cstr@1", "ints@2:len", "ints@3:u32",
             "nil_bytes_list@2", "ints@4:i32", "i32@5"],
    "ret": "own_opt<PGresult>", "retains": "none"}]}
```

The C order (conn, command, nParams, paramTypes, paramValues, paramLengths, paramFormats, resultFormat) matches `args`.
`ints@2:len` and `nil_bytes_list@2` both read list 2. The tool checks that each list is used by exactly one list kind and
every argument is used.

### 5.4 What is deleted and what remains

Deleted from the generic path: `toolkit/cres.c` and `cres.h` (205 and 60 lines, kept for the packages in 7.2), and
`libpq_adapter.c` and `.h` (417 and 40) except the conninfo check. Kept: `libpq_decls.h` (91 lines, declarations-only)
and `tests/fake_libpq.c` (363, ABI kept; the counters become release counts and census checks). `examples/libpq.obx`
(300) and `libpq.cbind` (356) are rewritten. The conninfo check (`conninfo_has_timeout`, `libpq_adapter.c:89`) is the one
helper, about 30 lines in `toolkit/libpq_conninfo.c`. `PQconninfoParse` returns a struct array that must be walked and
freed, so it stays in C. D13 may remove the helper.

### 5.5 SQL template literal to `PQexecParams`

A `Sql` value already separates text from parameters (template-literals design, lines 331-340), so the mapping is pure obo.
`Null` gives nil. `Int` gives OID 20 with decimal text. `Bool` gives OID 16 with `t` or `f`. `Str` gives OID 25, and a NUL is
refused. `Decimal` gives OID 1700 with exact text. `Bytes` gives OID 17 with binary format. `Float` is rejected in v1 (D10).
SQL text is never interpolated.

### 5.6 db/postgres: public surface and gaps

`postgres/postgres.obx` (694 lines), `postgres/postgres_adapter.c` (291) and the two `.cbind` files are the second libpq
consumer, and the libpq example did not cover them. Their public surface maps as follows (stage (g)):

| postgres.obx | v2 | Note |
|---|---|---|
| `open`, `status`, `is_open`, `error_message`, `close` | `connect`, `status`, `error_message`, `Conn.close` | conninfo diagnostic kept |
| `execute`, `execute_binary`, `query` | `exec` (obo arrays, typed OIDs) | packet builder and C parser removed |
| `Rows.status`, `is_open`, `row_count`, `column_count`, `is_null`, `length`, `value`, `text`, `bytes` | the same, through 5.2 bounds checks | `is_null` and `length` checked before use |
| `Rows.column_name`, `column_type_oid`, `column_type_modifier`, `decimal_type` | `fname`, `ftype`, `fmod` | `fmod` returns `i32` (may be -1) |
| `Rows.decimal`, `Rows.error_message`, `Rows.close` | obo decimal path, `result_error`, `close` | |
| `make_empty_rows` | a `Rows` with nil `h` | no native needed |
| `lib_version`, `param_*` | `lib_version`; `param_*` stay pure obo | |
| `live_resources`, `live_buffers`, `live_connections`, `live_results` | **removed** | the census (3.4) replaces the counters |

Gaps the first libpq example missed:
1. **Typed parameters.** The packet sets a type per kind: 0 for untyped NULL, 25 text, 17 bytea with binary format
   (`postgres_adapter.c:213-216`), 20, 16, and 1700 numeric, including the typed NULL of kind 6 (`postgres_adapter.c:236-246`).
   The `oids` argument and `ints@3:u32` carry them. (A former note on passing `null` for `paramTypes` is withdrawn; review 17.)
2. **`Bytes` and D10.** Typed OID 17 with binary format sends raw bytes with no `::bytea` cast.
3. **Decimal checks move to obo.** The strict grammar (`valid_decimal_text`, `postgres_adapter.c:102`) and the 98,342-byte cap
   (`postgres_adapter.c:34`, `105`) are C today. The obo port repeats them, and stage (g) runs one negative table against both
   checks. This moves trust; it does not delete it.
4. **Result wrapper.** `Rows` keeps its public `fmt` (`postgres.obx:39`, `445`, `595`), and its `h` is a `?Res` so that `close`
   can take it. A SQL error is `ok(Rows)` with `PGRES_FATAL_ERROR`, read before close.

---

## 6. Worked example 2: SQLite

### 6.1 Functions and kinds

Scope: the subset that `db/sqlite/sqlite_adapter.c` calls, declared in `sqlite_decls.h` (81 lines). New declarations:
`sqlite3_close_v2`, `sqlite3_next_stmt`, `sqlite3_errcode`.

| SQLite function | Kinds | Obo wrapper |
|---|---|---|
| `sqlite3_open_v2(path, &db, flags, NULL)` | `borrow_cstr@0`, `out_own<Db>`, `i32@1`, `null`; ret `i32` | `open_v2` (6.3) |
| `sqlite3_close_v2` | release of `Db` (spec `"release": "sqlite3_close_v2"`, `"void"`; rc discarded, 6.2 point 1) | generated `close` |
| `sqlite3_next_stmt(db, NULL)` | `borrow<Db>@0`, `null`; ret `nonzero` | `has_live_statements` (informational) |
| `sqlite3_prepare_v2(db, sql, n, &stmt, &tail)` | `borrow<Db>@0`, `borrow_bytes@1`, `out_own<Stmt>`, `out_offset@1`; ret `i32` | `prepare_v2` (6.3) |
| `sqlite3_finalize` | release of `Stmt` (spec `"release": "sqlite3_finalize"`, `"void"`) | generated `close` |
| `sqlite3_step`, `sqlite3_reset` | `borrow<Stmt>@0`; ret `i32` | `step`, `reset` |
| `sqlite3_bind_null`, `sqlite3_bind_int64` | `borrow<Stmt>@0`, `i32@1`, `i64@2` for int | `bind_null`, `bind_int` |
| `sqlite3_bind_text`, `sqlite3_bind_blob` | `borrow<Stmt>@0`, `i32@1`, `borrow_bytes@2`, `const:-1` | `bind_text`, `bind_blob` |
| `sqlite3_bind_parameter_count`, `sqlite3_column_count` | `borrow<Stmt>@0`; ret `i32` | `param_count`, `column_count` |
| `sqlite3_column_type` | `borrow<Stmt>@0`, `i32@1`; ret `i32` | `column_type` |
| `sqlite3_column_int64` | `borrow<Stmt>@0`, `i32@1`; ret `i64` | `column_int` |
| `sqlite3_column_text` with `_bytes` | `borrow<Stmt>@0`, `i32@1`; ret `cbytes`, length `sqlite3_column_bytes`, cap 16 MiB | `column_text` |
| `sqlite3_column_blob` with `_bytes` | as above, value `sqlite3_column_blob` | `column_blob` |
| `sqlite3_changes`, `sqlite3_errcode` | `borrow<Db>@0`; ret `i32` | `changes`, `error_code` |
| `sqlite3_errmsg` | `borrow<Db>@0`; ret `cstr_out` | `error_message` |
| `sqlite3_errstr` | `i32`; ret `cstr_out` | `error_string` |
| `sq_stmt_errcode` (trusted C helper, 4 lines) | `borrow<Stmt>@0`; ret `i32` | `stmt_error_code` |

`sq_stmt_errcode` calls `sqlite3_errcode(sqlite3_db_handle(stmt))` inside one native call. The pointer from
`sqlite3_db_handle` never leaves C, so obo holds no pointer and needs no new kind. It is the only C helper added for
SQLite, and it uses generic kinds only. It exists because `column_text` returns NULL both for SQL NULL and for
out-of-memory, and the wrapper needs the errcode to tell them apart (6.2, point 2).

The `cbytes` entry names the value function as `symbol` and the length function in `length`:

```json
{"name": "column_text", "symbol": "sqlite3_column_text",
 "args": ["borrow<Stmt>@0", "i32@1"], "ret": "cbytes",
 "length": "sqlite3_column_bytes", "cap": 16777216}
```

### 6.2 The stress points and the review items

**Point 1. Parent and child lifetime (review 9, 16).** The current adapter's invariant is that `sqlite3_close` refuses
while statements are live (`sqlite_adapter.c:229-232`), with `release_stmt` decrementing through `s->owner`
(`sqlite_adapter.c:140`). A `Stmt` in the new design holds only its `sqlite3_stmt*` and no pointer to the connection, so a
closed `Db` leaves nothing dangling in obo. A fixture reproduces the old ordering with the current `release_stmt` shape and
runs under ASan. It reports a heap-use-after-free, which is the test that proves the failure.

The adopted design uses `sqlite3_close_v2`. Its observable semantics are documented and tested:
- closing a `Db` with live statements returns success and leaves the handle as a zombie, so `close` returns `ok(true)`;
- an orphaned `Stmt` keeps working (`step` and `column_*` succeed) until it is closed;
- a read lock held by an orphaned `Stmt` makes a write on another connection fail with `SQLITE_BUSY`;
- the current adapter refuses the close instead (`sqlite.obx:215-217`). This is a visible change, and the test records it.

Rejected alternatives: plain `sqlite3_close` (BUSY while statements are live), and owning statements inside the connection
(changes the API). A generic "child keeps parent alive" rule would need stored references, which obo does not have
(`LANGUAGE.md` section 4), so it is not proposed.

**Point 2. Borrowed results (review 2, 10).** `sqlite3_column_text` and `_blob` pointers last until the next step, reset,
finalize or conversion of that column. `cbytes` calls the value function and then the length function, and copies, all in one
native call, so no pointer outlives it. The ordering rule that remains concerns types: `column_type` must be read before any
accessor, because a conversion makes the type undefined. The wrapper does so (`sqlite.obx:385-394`; `sqlite_adapter.c:486`), and
refuses REAL first (`sqlite.obx:392-393`).

NULL handling: `sqlite3_column_blob` returns NULL for a zero-length BLOB, so NULL with length 0 is empty.
`sqlite3_column_text` returns NULL for SQL NULL and for out-of-memory. After an empty text copy, the wrapper calls
`stmt_error_code` and returns `err` on `SQLITE_NOMEM` (review 10). Column and parameter indexes are checked in obo against
`column_count` and `parameter_count`, since the adapter returns -1 for these, and the probe shows an out-of-range `column_type`
returns `SQLITE_NULL` (5) (review 2).

**Point 3. `SQLITE_TRANSIENT` without callbacks.** The destructor `(sqlite3_destructor_type)-1` (`sqlite_decls.h:25-26`) is the
constant `const:-1`. It consumes no obo argument and produces one C word, with no relocation and no callback. SQLite copies the
value before the call returns, so this is the only destructor that fits R1. `SQLITE_STATIC` (0) and function pointers are refused
in destructor positions. No C shim remains for bind. Tests: text bound from an array that obo then overwrites before `step` keeps
the bound bytes, and a zero-length blob binds as a BLOB rather than NULL (R5).

**Point 4. Out-parameters and prepare (review 4, 5, 18).** `sqlite3_open_v2` writes `*ppDb` even on failure. `out_own` counts that
handle from the write, so the wrapper closes it on every path (6.3); otherwise the census reports it (R12). The wrapper reads the
message before the close. `sqlite3_prepare_v2` may write a non-NULL `*ppStmt` with an error. That case was not observed in the
probe, and the wrapper handles it anyway (review 18).

The prepare call passes the full length. `nByte` stops at an embedded NUL, so obo rejects NUL before the call (review 4). The tail
check then runs to the full length: `out_offset@1` gives the tail, and obo accepts only spaces, tabs, CR, LF and `;` to the end of
the input. That is the same rule as `only_one_statement` (`sqlite_adapter.c:293-298`), applied in obo. Empty SQL (only whitespace)
returns `err`, not a nil `Stmt` (review 13).

**Point 5. Multi-statement exec (review 5).** `exec` is not the one-statement prepare. It is a loop: prepare the next statement from
the tail, step it until `SQLITE_DONE` ignoring rows, close it, and continue with the remaining bytes. Each step's error returns
`err`. The one-statement rule stays for `prepare`.

**Reset after a failed step (review 1).** The wrapper keeps the step's result and ignores the rc of `reset`.

**Point 6. Declarations-only checking (review 13, 20).** `sqlite.cbind` sets `"headers": []` and has no signature-mode field;
`declarations-only` lives only in the README. Proposal: `signature_check` is required and is `"header"` (sigcheck compiles against
the header, and a mismatch is a compile error, `cinterop.py:299`) or `"declarations-only"` (no claim, recorded in the manifest).
This host has no `sqlite3.h`, so the mode is declarations-only. The real `libsqlite3.so.0` 3.45.1 tests are the ABI gate. The
declaration hash is provenance only (R11).

**Limits kept (review 13).** SQL text 1 MiB; values 16 MiB (`sqlite_adapter.c:21-23`); path 4 KiB; text rejects an embedded NUL;
BLOBs keep NUL; empty SQL is `err`; `len >= INT_MAX` is refused (`sqlite_adapter.c:313`).

### 6.3 SQLite open and prepare sketches (not compiled)

```obo
module sqlite

type Db = foreign "sqlite3"                    // spec: release sqlite3_close_v2, void
type Stmt = foreign "sqlite3_stmt"             // spec: release sqlite3_finalize, void
type OpenOut = ref record { rc: int; db: ?Db } // generated for out_own<Db>
type PrepOut = ref record { rc: int; st: ?Stmt; tail: int }

native fn open_v2(path: &[]byte, flags: int): OpenOut   // borrow_cstr@0, out_own<Db>, i32@1
native fn prepare_v2(db: &Db, sql: &[]byte): PrepOut    // borrow_bytes@1, out_own<Stmt>, out_offset@1
native fn error_message(db: &Db): []byte
native fn errstr(rc: int): []byte

pub fn open(path: string, flags: int): Result[Db] {
    if len(path) > MAX_PATH { return err("database path is too long", 2) }       // 4 KiB
    if hasNul(path) { return err("database path contains a NUL byte", 2) }
    var out = open_v2(encodeString(path), flags)  // out.db may be set even when rc != 0
    if out.rc != SQLITE_OK {
        if out.db == nil { return err(string(errstr(out.rc)), 1) }
        var db = take(out.db)
        let msg = string(error_message(&db))      // read before close
        db.close()
        return err(msg, 1)
    }
    if out.db == nil { return err("sqlite returned no handle", 1) }
    return ok(take(out.db))                       // the caller defers close
}

pub fn prepare(db: &Db, sql: string): Result[Stmt] {
    if len(sql) > MAX_SQL { return err("SQL text is too large", 2) }             // 1 MiB
    if hasNul(sql) { return err("SQL text contains a NUL byte", 2) }            // nByte stops at NUL
    var out = prepare_v2(db, encodeString(sql))
    if out.rc != SQLITE_OK {
        let msg = string(error_message(db))
        if out.st != nil {                        // non-NULL on error (review 18)
            var st = take(out.st)
            st.close()
        }
        return err(msg, 1)
    }
    if out.st == nil { return err("SQL text is empty", 2) }                     // not a nil Stmt
    var st = take(out.st)
    if !blank_tail(sql, out.tail) {               // only spaces, tabs, CR, LF, ';' to the end
        st.close()
        return err("prepare takes one statement; use exec for several", 2)
    }
    return ok(st)
}
```

Every path either closes an owned handle or returns it; `take` leaves `out.db` and `out.st` nil. `blank_tail`,
`hasNul`, `encodeString` and the constants are ordinary obo helpers, not shown.

---

## 7. Decisions

Decided by the user (2026-11-01, not for review): cleanup is **file-consistent explicit close**. Foreign handles use a borrowing,
deferrable `close` and a leak census. No must-close rule, no consuming defer, no release on drop.

### 7.1 For the user (each with a recommendation)

- **D1. Body-less `native fn` plus `type X = foreign "C"` (one approval).** Recommended. The alternative is a compiler flag that
  reads the spec. The form adds a declaration and a type syntax, which is a language change.
- **D6. `.sym` for foreign types.** Recommended: deferred until the first exported foreign type, with its own version decision.
  Nothing here is exported.
- **D8. List kinds versus one helper.** Recommended: scoped kinds (`nil_bytes_list`, `ints` with flags, `ints@k:len`). Alternative:
  one helper `pq_exec_params_list`, which keeps C parsing for later libraries.
- **D12. No rename in this change.** Recommended: keep `Connection`, `Rows` and the existing names.
- **D13. Keep the `connect_timeout` default and its helper.** Recommended. Alternative: drop the default, which removes the helper and
  changes behaviour for DSNs without a timeout.

### 7.2 Technical defaults (no approval needed unless challenged)

- **D2.** `foreign` is a reserved word, with D1.
- **D4. Exit codes.** 11 (new) for native contract violations: a NUL past the helper check, a `cbytes` NULL with positive length, an
  `i32` or `u32` out of range, a list mismatch or above the cap, an `own` NULL. 7 (existing) for a foreign handle used after close.
  8 (existing, generalized) for a foreign handle left open at normal end under `OBO_CHECK=1`. Alternative for the leak: a new code
  13, so fd and foreign leaks are told apart. Recommended: reuse 8, because both are "a resource was never closed" and the message
  names the kind. `LANGUAGE.md` section 9 gets the 11 entry and the generalized 8 entry when stage (d) is accepted.
- **D5.** Nothing is reported at exit beyond the census at normal end under `OBO_CHECK=1` (D4, code 8).
- **D7.** `cres.c` is kept for `sys`, `tls`, `http2`, `zlib`, the buffer example and the skill templates. v2 stops using it for libpq,
  SQLite and db/postgres only (review 14).
- **D9.** Scratch copy for `borrow_cstr`; no reserved terminators.
- **D10.** `Float` is rejected in v1. `Bytes` uses OID 17 with binary format, with no `::bytea` cast.
- **D11.** `cstr_out` returns empty for NULL, per call.
- **D14.** SQLite adopts `sqlite3_close_v2` with the documented semantics in 6.2, point 1, and the tests in section 9.
- **D15.** `close` returns `Result[bool]` as `Dir.close` does. A `"void"` release gives `ok(true)`; `ok(false)` marks a second close.
- **D16.** `is_open(&T): bool` is a generated query, next to `close`. It is for wrappers that want a `Result`, not a substitute for the
  thunk check (3.4).

### 7.3 Future option (not in scope)

Compile-time must-close could later be added to files and foreign handles together. That would be one checker change covering both, and
it would need its own review. This revision does not add it.

---

## 8. Staged plan

Every stage keeps the gates in native design section 12. A compiler-changing stage also runs the gates in native design sections 14.6 and
14.10 and `test_cinterop.py`, including the self-build fixed point and the baseline byte-identical check. Live tests stay opt-in.

**Stage (a): borrowed input and scalar kinds.** Adds `borrow_bytes@k`, `borrow_cstr@k`, `null`, `const:<n>`, `i32`, `u32`, `ints@k` with
flags, `nil_bytes_list@k`, and the scratch helpers. Checker: a borrow is a read, and a call may not borrow and mutate the same place (3.2).
Risk: medium. Gate: existing scalar, startup and fake tests; a `bytes` fixture covering an empty array, 1 MiB, a nil string, embedded NULs
and a rejected NUL in `borrow_cstr`; a `const:-1` recorder; the scratch block freed; the borrow timing test; `u32` above `2^31`.

**Stage (b): copy-out and typed returns.** Adds `cbytes` returning `?[]byte` with caps, `cstr_out`, `void`, `nonzero`. Checker: a copy-out
result is a new owned temporary. Risk: medium. Gate: fake `PQgetvalue` for empty, NUL-containing, NULL-with-length (which traps with D4) and
over-cap (which returns nil, then `err`); `cstr_out` stops at NUL and rejects a missing NUL past the cap.

**Stage (c): typed declarations and sidecar v2 (no foreign types).** Adds the body-less `native fn` form (D1 without `foreign`), the kind
table, `cinterop-relocs 2`, `cinterop-proto/2` without `types`, and `signature_check` (6.2, point 6). Checker: parsing and type-checking of
body-less natives only. Risk: medium. Gate: sidecar tamper refused; `--native-relocs` refuses a kind mismatch; the existing int-only natives
still byte-identical.

**Stage (d): foreign handles, borrowing close and leak census.** Adds `foreign` types with one private word (3.4), `own`, `own_opt`,
`out_own`, `borrow<T>`, the generated `close` and `is_open`, the use-after-close check in the thunk (exit 7), and the per-type census (exit 8
under `OBO_CHECK=1`, D4). Runtime: the foreign word is kept out of both offset lists (review 6); the census counters and their report in
`rt_finish`. Checker: `foreign` as a record type, field privacy for the word, and the existing defer and move rules. No new ownership rules.
Risk: medium to high (the runtime offset list and the census are new; the checker is not). Gate: the negative suite (section 9): use after
close (exit 7, no C call), double close (`ok(false)`, one C release), census (exit 8 under `OBO_CHECK=1` for a missing close, a dropped handle
and an `out_own` left open on the error path), no census output without `OBO_CHECK=1`; the exit and trap test (no release call); the SQLite
UAF reproduction; ASan on the fixtures.

**Stage (e): port libpq.** Rewrites `libpq.obx` and `libpq.cbind` to 5.2 and 5.3, deletes `libpq_adapter.c` and `.h` except
the conninfo helper, and keeps `cres.c`. Risk: low to medium. Gate: the cinterop suite with fake release counts per object; bounds tests
(`PQgetisnull(res,9,0)`, `PQftype` out of range); the caps in C16; a `Rows` dropped without `close` reported by the census; the live smoke test
on a user-supplied local DSN, opt-in.

**Stage (f): port SQLite.** Adopts `sqlite3_close_v2`, the `nonzero` probe, and the `sq_stmt_errcode` helper. Rewrites `sqlite.obx` and
`sqlite.cbind` to the 6.1 spec and the 6.3 wrappers, implements `exec` as the loop in 6.2 point 5, and deletes `sqlite_adapter.c` and `.h`.
Risk: medium. Gate: the real 3.45.1 tests, the new fake (section 9), the UAF reproduction (fails in the old shape, passes in the new one), the
BUSY and zombie test, and the existing `sqlite_memory`, `sqlite_file` and `sqlite_decimal` cases in `test_db_adapters.py` unchanged except for
the documented close change.

**Stage (g): port db/postgres.** Maps the public surface in 5.6 onto `connect`, `exec` and the `Rows` wrapper, removes the `live_*` counters and
the packet builder, and moves the decimal grammar and cap into obo. Risk: medium. Gate: `test_db_adapters.py` postgres cases (fake and decimal
fake), the decimal negative table against both checks, the `postgres_adapter_test.c` cases ported, and the live smoke test, SELECT-only and opt-in
through `CINTEROP_TEST_DSN`.

**Stage (h): skill and templates.** Updates `skill/SKILL.md` and the project-skill templates for the explicit `close`, the census, the kinds and the
`cres` policy in 7.2. zlib stays on its current adapter and is outside the v2 gates (review 7). Risk: low. Gate: a generated project builds, and the
existing `cres` tests still pass.

Ownership-checker exposure: (a) the borrow read rule; (d) low (records only, plus the `foreign` declaration); the rest none.

---

## 9. Test plan

**Positive.** `borrow_bytes` over empty, 1-byte, 1 MiB and string inputs (nil string gets length 0 and a non-NULL pointer; embedded NULs
kept). `borrow_cstr` over ASCII, empty and UTF-8. `cbytes` over 0, 1, N-with-NULs and NULL-with-length-0. `cstr_out` stopping at NUL. `ints` with
`:len`, `:u32` and `:i32`, including 32 entries. Foreign connect, exec, read, `defer c.close()`, a double close (`ok(false)`), `is_open` before and
after close, a field handle closed through a `mut` receiver, and a handle in an array closed element by element.

**Foreign handle suite (stage (d)).**
- use after close: a `borrow<T>` native on a closed handle exits 7 with the message and makes no C call;
- double close: `ok(false)` and exactly one C release call;
- census under `OBO_CHECK=1`: a handle never closed exits 8 at normal end and names the type; the same for a handle dropped at scope end,
  overwritten, or dropped with its enclosing record, each without a C release call;
- an `out_own` written with an error return and not closed on the error path: exit 8;
- a release that returns an error (`"release_result": "int"`): `err` with the code, and the handle is zero afterwards;
- without `OBO_CHECK=1`: no census output and no exit 8 for the same programs;
- `exit()` and a trap with live handles: no release call, and the documented census behaviour.

**Review regression.** The UAF reproduction of the old `release_stmt` shape (6.2, point 1) must fail under ASan, and the new `close_v2` shape must
pass. The orphaned-statement BUSY and zombie test (6.2, point 1) must pass.

**SQLite, real library (3.45.1).** Open and close a file database. Prepare, bind int, text, blob and NULL. Step and reset with rebinding. `column_text`
and `column_blob` with embedded NULs and a zero-length BLOB (BLOB type, not nil). A REAL column refused before conversion. `changes`. Multi-statement
`exec`. Empty SQL refused. The `const:-1` overwrite test. The close-with-live-statement test (point 1).

**SQLite, fake (`tests/fake_sqlite.c`, new).** Knobs: `open_v2` fails with a non-NULL `*ppDb` (closed exactly once by the wrapper); `prepare` fails with a
non-NULL `*ppStmt` (closed once, error text kept); `reset` returns the step error (the wrapper keeps the step result); `column_text` returns NULL with
`SQLITE_NOMEM` (the wrapper returns `err`, not empty); `close_v2` zombie counting; the destructor argument is recorded and copied for -1 only; prepare with an
embedded NUL and with a trailing statement (refused before or after the call).

**Runtime negatives.** An embedded NUL in `borrow_cstr` (wrapper `err`; a direct call traps with D4). A `prepare` with two statements, or with trailing text
other than whitespace or `;` (refused). An out-of-range row, column or parameter index (`err`, not NULL). A `cbytes` over its cap (`err`). A
`signature_check: "header"` without a header path (refused). A sidecar tamper (refused).

**Compile-time negatives.** A borrow and a mutation of one place in one call (3.2); a field write without `mut` or an owned local; a move of `c`
after `defer c.close()`.

**Sanitizers.** ASan and LSan over the fixtures.

---

## 10. Review findings addressed

| Review item | Where addressed |
|---|---|
| 1. `sqlite3_reset` after failed step | 6.2 (reset) |
| 2. Out-of-range row/col silently accepted | 5.2, 6.2 point 2, R10; tests in section 9 |
| 3. `cbytes` over cap must be an error | 3.3 (nil, then `err`); 6.1 caps |
| 4. `prepare` with `nByte` and embedded NUL | 6.2 point 4; 6.3 |
| 5. exec versus one-statement prepare | 6.2 point 5 |
| 6. Foreign word in offset lists | 3.4 representation (in neither list); C6; R7 |
| 7. zlib `compress2` output buffer | zlib removed from v2 gates (stage (h)) |
| 8. db/postgres stage and surface | 5.6; stage (g) |
| 9. Reproduce the release_stmt UAF | 6.2 point 1; section 9 |
| 10. `column_text` NULL on OOM needs a db handle | 6.1 (`sq_stmt_errcode`); 6.2 point 2 |
| 11. Field writes need `mut &T` | 3.4; 5.2; 6.3 |
| 12. Borrow timing | 3.2; R3; section 9 |
| 13. Keep existing limits | C16; 5.2; 6.2 limits; 6.3 |
| 14. `cres` contradiction | 7.2 (D7); stage (h) |
| 15. Globals | 3.4: allowed, counted at normal end |
| 16. D14 close_v2 and its semantics | 6.2 point 1; section 9 |
| 17. Stale `null` paramTypes note | 5.6 (withdrawn) |
| 18. Non-NULL `*ppStmt` on error | 6.2 point 4; 6.3 |
| 19. `cbytes` OOM exit 2 | 3.3 |
| 20. `release_now` moot | 3.4: `close` is the explicit release; no `release_now` |

---

## 11. Non-goals and follow-up

- No callbacks, C variadics, struct-by-value, `long double`, floats or decimals across the native ABI (native design section 2); no
  mutable borrows, borrowed foreign returns, raw pointers or `void*`.
- No release at `exit`, a trap or `atexit`.
- No SQL driver: libpq, SQLite and db/postgres stay bounded adapters (native design section 8.5).
- No universal generator (native design section 1.1).
- No change to `LANGUAGE.md`, `obo/design/README.md`, `obo/FORK.md` or the seed in this note. The exit code entries (D4) and the census
  need their own review when stage (d) is proposed.
- No compile-time must-close (7.3 is a future option only).
- No TLS, IFUNC, threads, PIE or ASLR, and no stable `cinterop/1` or `cabi1`.
- **Follow-up (out of scope).** The systems stdlib's leak census for fds and sockets (`systems-stdlib-design.md` section 4.2) is unchanged.
