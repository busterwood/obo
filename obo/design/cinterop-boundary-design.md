# Native C interop: generic boundary types (v2 proposal)

Status: **design for independent review; no implementation authorized.**
Date: 2026-11-01. Baseline: `cinterop-boundary` from `main` `2470b35` (compiler `0.10`
line, `.sym` format 6, ABI 1, experimental cinterop per `native-c-interop-design.md`
section 14.10).

This note extends `native-c-interop-design.md` sections 8 and 15 (open questions 2 and
5) and keeps its steering decision (section 1.1): a small toolkit plus an agent skill,
with no universal generator. Nothing here is implemented. No compiler, runtime, `.sym`,
ABI or package version is taken, and `LANGUAGE.md` is unchanged. Line numbers were read
from this tree; re-check them at review.

## Summary

1. Four primitives in the one `__cinterop` seam: borrowed input, copy-out, opaque foreign
   handle types, and release-on-drop (`borrow_bytes`/`borrow_cstr`, `cbytes`/`cstr_out`,
   `foreign`, release tags).
2. Scoped kinds for C arrays, constants, checked `i32`/`u32`, and out-parameters owned
   from the moment C writes them.
3. A foreign handle is a generated `ref record` with one private field, so existing
   ownership, `?T`, `take`, `defer` and drop rules apply. `cres` leaves the generic path.
4. Release runs only on the drop path; `exit` and traps run no release.
5. libpq binds from a spec plus about 150 lines of obo, keeping one small C helper.
   SQLite needs no policy shim once the out-parameter and constant kinds exist.
6. SQLite's lifetime hazard is removed by `sqlite3_close_v2` on the drop path (D14).
7. Stages (d) and (e) touch the checker and are riskiest. Fourteen user decisions are in
   section 6.

---

## 1. Problem and goals

Libpq and SQLite are bound through layers that exist only because the boundary is scalar.
Integer-token registries keep `PGconn*`, `PGresult*`, `sqlite3*` and `sqlite3_stmt*` on the
C side (`toolkit/libpq_adapter.c` 417 lines, `cres.c` 205, `cres.h` 60, `libpq_adapter.h`
40; `db/sqlite/sqlite_adapter.c` 528). Bytes cross one native call per byte (`put_bytes`,
`examples/libpq.obx:34-47`), so a 1 MiB `bytea` costs about two million crossings, and the
libpq packet is built in obo and parsed again in C. `nativeCall`
(`obo/src/obo.obo:7361-7378`) accepts only `int` arguments.

Goals: G1, a small fixed set of generic kinds reusable by libpq, SQLite, zlib
(`examples/zlib.cbind`) and an unrelated library. G2, C pointers never become obo values;
borrowed input lives only for the call; bytes return only by copy. G3, a foreign handle is
an owned value released exactly once through the drop path. G4, libpq and SQLite bind from
specs plus little obo, with at most one small C helper. G5, gated stages that mark checker
exposure.

---

## 2. Verified constraints

C1. **No collector; explicit free only.** `rt_collect` returns at once
(`obo/runtime/runtime.c:309-310`); blocks return only through `free_block`
(`runtime.c:382`), reached from `rt_free` (`runtime.c:451-481`). Objects do not move.

C2. **Arrays are a stable header over a replaceable block.** Header `{len, cap, data,
esize}` (`runtime.c:377`); elements at `data + 8` (`runtime.c:541-548`); growth frees
the old block (`rt_append`, `runtime.c:564-583`). A pointer into elements lasts only
until the next growth.

C3. **Strings are `[len][bytes]` with no terminator.** Reference counted
(`runtime.c:428`, `433`). The nil string is 0, and `sdata(0)` would be address 8
(`runtime.c:597-598`). Literals have no NUL (`obo/src/obo.obo:2993-3004`).

C4. **No pointer type in obo.** The seam accepts only `i64` arguments
(`cinterop.py:220-223`) and sign-extends `i32` returns (`cinterop.py:215-216`).

C5. **`exit` and traps skip defers and frees.** `exit` runs no defers
(`obo.obo:8746-8751`). Block exit runs defers, then frees owned locals
(`callDefers`, `obo.obo:5353`; `closeBlock`, `5372`). Traps use `die_code`
(`runtime.c:96`) or `rt_exit` (`runtime.c:1539`); hosted `OBO_EXIT` runs a host callback
then exits (`runtime.c:24-36`).

C6. **Record drop has no foreign hook.** `rt_free` frees owned fields and decrements
strings, and calls nothing else (`obo.obo:3016-3025`; `runtime.c:451-481`).

C7. **The checker already handles owned values.** `isOwned` covers records, closed
types, arrays, optionals and results (`obo.obo:4052-4060`). `drop(x)` frees now and
marks `x` moved (`obo.obo:8416-8433`). A defer may not move what it uses
(`obo.obo:4440-4443`). `take` moves out of an optional (`obo.obo:8458`).

C8. **Checked handles.** A use of a freed `^T` object exits 7 (`rt_deref`,
`runtime.c:498`; `LANGUAGE.md` section 5, line 639).

C9. **The `cres` registry is bounded and integer-typed.** 64 entries (`cres.h:29`);
close poisons the generation before release (`cres.c:78`).

C10. **Native calls need the typed sidecar.** `--native-relocs` is required, and the
sidecar records argument and result types (`obo.obo:15646-15672`). The tool checks it
against the spec (native design section 14.2).

C11. **Threads are not implemented,** and worker closures are limited to copied bytes and
thread-safe natives (`systems-threads-design.md` section 4.1).

C12. **Other facts.** `Sql` renders `$1..$N` with tagged values
(`template-literals-design.md:331-340`; `rt_tpl_sql_arg`, `runtime.c:1677`). The cinterop
build appends `rt_set_host_exit` (`runtime.c:26`) without changing baseline bytes.
`rtCall` passes four register arguments (native design section 3). Codes 2 to 9 are
documented (`LANGUAGE.md` line 2505), and 10 is assigned to decimal traps.

C13. **Library non-retention is documented, not checked.** `PQconnectdb` and
`PQexecParams` copy their inputs; `PQgetvalue` pointers last until `PQclear`. SQLite's
`sqlite3_column_text` and `_blob` pointers last until the next step, reset, finalize or
conversion of that column. The spec cites these (R11).

C14. **The SQLite adapter's lifetime rule is explicit-close only.** `sq_conn_close`
refuses while `stmt_live != 0` (`db/sqlite/sqlite_adapter.c:229-232`), and
`release_stmt` decrements through `s->owner` (`sqlite_adapter.c:140`). That is safe only
because close is explicit (5.7, point 1).

---

## 3. Proposed boundary types

### 3.1 Spec and kinds

A spec format `cinterop-proto/2` is proposed (experimental, no stable number). It adds
`types` for foreign types and per-function kinds. An argument kind names the obo
argument it consumes as `kind@index`; a bare kind consumes none.

| Argument kind | obo argument | C parameters | Rule |
|---|---|---|---|
| `i64` | `int` | 1 `long` | existing |
| `i32` | `int` | 1 `int` | range-checked (3.6) |
| `null` | none | pointer 0 | explicit NULL |
| `const:<n>` | none | 1, value `n` | fixed in the manifest; `-1` only in a destructor position (5.7) |
| `count_of@k` | none (reads list k) | 1 `int` | element count, range-checked |
| `borrow_bytes@k` | `[]byte` or `string` | `const void *p` (never NULL), `size_t n` | call-scoped (3.2) |
| `borrow_bytes_i32@k` | as above | `const void *p`, `int n` | length range-checked |
| `borrow_cstr@k` | as above | `const char *s` | NUL scratch copy (3.2) |
| `borrow<T>@k`, `borrow_opt<T>@k` | `T` or `?T` foreign | `T *` (NULL for `_opt`) | traps if released |
| `nil_bytes_list@k` | `[]?[]byte` | `const char *const *v`, `const int *len` | lengths derived (3.5) |
| `i32s@k`, `u32s@k` | `[]int` | `const int *`, `const unsigned int *` | copied, range-checked; length must match list k |
| `out_own<T>` | none (adds a result field) | `T **` slot | owned from the write, whatever the return code (R12) |
| `out_offset@k` | none (adds an `int` field) | `const char **` slot | offset of the written pointer inside argument k, or -1 |

| Return kind | obo result | Meaning |
|---|---|---|
| `i32`, `u32` | `int` | sign-extended, or zero-extended (`Oid`) |
| `i64` | `int` | existing |
| `nonnull` | `int` | 1 if the pointer is non-NULL; the pointer is discarded (probe) |
| `own_opt<T>`, `own<T>` | `?T`, `T` | owned foreign value; NULL is nil, or a trap for `own` |
| `cbytes` with `"length": sym` | `[]byte` | copy of `(value, length)`; value first, same arguments |
| `cstr_out` | `[]byte` | copy up to NUL, bounded; NULL gives empty (D11) |

When any `out_*` kind is present, the function returns a compiler-generated record:
`rc: int` (the declared return) plus one field per out kind. Its owned fields are
released by the drop path, so an error return cannot leak. A borrow kind is never a
return kind: references cannot be returned (`LANGUAGE.md` section 4), and a raw foreign
pointer must not outlive its owner.

### 3.2 Primitive 1: borrowed input

For `borrow_bytes`, an array passes `data + 8` and `len` (`runtime.c:377`, `541-548`).
An empty array points at a static block (`runtime.c:404-410`), so the pointer is never
NULL. A string passes `s + 8` and `*s`; the nil string is replaced by a static zero
block (`runtime.c:597-598`).

**Lifetime.** The pointer lives in registers and the thunk frame, from argument load to
native return. No obo value can hold it. Retention by trusted C is excluded by spec
(`retains: "none"`), and the tool cannot check it (R11).

**`borrow_cstr`: copy to scratch, do not reserve a terminator.** A hosted helper copies
the input into `len + 1` zeroed bytes, traps on an embedded NUL, and returns the pointer;
the thunk frees it after the call. Arrays and strings have no spare terminator
(`rt_append` can leave `cap == len`), so reserving one would change every ordinary
layout and break the byte-identical gate. Wrappers pre-check NUL. Binary input uses
`borrow_bytes`, and SQLite's `prepare` and `bind` take a length, so they need no NUL.

**Checker.** A borrow is a read, so the source stays owned; an owned temporary may be
borrowed only for the call (`LANGUAGE.md` section 4).

### 3.3 Primitive 2: copy-out

`cbytes` calls the value function, keeps `ptr` in its frame, calls the length function
with the same argument words, then calls `copy_out(ptr, len)`, which allocates a `[]byte`
(kind 4, `new_block`) and copies. No obo code runs between the calls. `ptr == NULL` with
`len == 0` is empty. A NULL with positive length, a negative length, or a length above
the cap is a contract violation (D4). The helper never calls `strlen`, so embedded NULs
are kept. SQL NULL never reaches `cbytes`: wrappers check the type first (5.7).

`cstr_out` copies up to NUL, bounded by 16 MiB (`toolkit/cres.h:30`). A missing NUL
within the cap is a contract violation. Both kinds need the typed surface of D1.

### 3.4 Primitive 3: opaque foreign handle types

**Declaration** (D2): `type Conn = foreign "PGconn"`, paired with its spec entry.

**Representation.** A compiler-generated `ref record` with one private `foreign` field
holding the C pointer word, or 0 after release. As a record it gets `isOwned`, `?Conn`,
`take`, `handle(x)`, `alive`, `drop`, `defer`, moves and drop through enclosing records,
with no new checker rules. Obo state beside a handle nests it: libpq's `Rows` is
`ref record { h: Res; pub fmt: int }` (5.6).

**Restrictions.** No literal, `int(x)`, `string(x)`, arithmetic, ordering, equality
except `== nil` on `?T`, printing, or field read outside the native-argument path. Only
`own`, `own_opt` and `out_own` create a foreign value, and no integer converts to one,
so a handle cannot be forged.

**What `cres` still buys.** Stale handles, forged integers, wrong kinds and double release
are covered by the record itself, and capacity moves from 64 entries to the heap. **D7:**
retire `cres` from the generic path, keeping `cres.c` for integer-ID APIs.

### 3.5 Primitive 4: release on drop, and arrays of borrowed buffers

**Mechanism.** A foreign record's descriptor carries a release tag, emitted only for
foreign types, so ordinary output stays byte-identical. When `rt_free` reaches a tagged
block it zeroes the word, calls the registered release function with the old word, and
calls `free_block` (`runtime.c:382`).

**Registration.** The release address is known only at link time, and the sidecar
carries only code relocations, so rodata cannot hold it. A shim constructor calls a
hosted-only slot, like `rt_set_host_exit`, once per type into a fixed table.

**Exactly once.** `free_block` is the only free path and poisons the generation
(`runtime.c:382`, `451-481`). The field is zeroed before release, `release_now` zeroes it
too, and release runs with no obo code on the stack (R7).

**Explicit early close.** Recommended (D3): a borrowing builtin `release_now(&c)` that
zeroes then releases and returns true only if it released. A defer may name a borrowed
value but not move one (`obo.obo:4440-4443`), so `defer c.close()` works. A native call
on a released value traps (D4).

**Block exit and early return.** Defers run before owned locals are freed
(`obo.obo:5353-5372`, `5407`), so a `Result` holding a foreign value releases it on the
`err` path, as today's `connect` does by hand.

**`exit`, traps, teardown.** `exit` and traps release nothing; the OS reclaims sockets.
Hosted `atexit` handlers must not be relied on. Module teardown releases globals and
locals, and `rt_finish` (`runtime.c:507`) reports leftovers under `OBO_CHECK` (D5).
Foreign values are thread-confined (R9).

**Arrays of borrowed buffers.** `PQexecParams` and `sqlite3_bind_*` need parallel
arrays. `nil_bytes_list@k` expands to element pointers (NULL for a nil element, otherwise
`data + 8`) and element lengths taken from each element's own `len`, so user code cannot
pass a length longer than its buffer. Both are call-scoped scratch. The list is capped at
65535 elements (D8). `i32s@k` and `u32s@k` trap on a range error or a length mismatch.

### 3.6 `i32`, `u32` and typed declarations

An `i32` argument is checked against `INT32_MIN` and `INT32_MAX`, trapping with D4 on a
mismatch. A `u32` return is zero-extended (`PQftype` returns `Oid`). Both are stage (c).

The builtin is typed `int` today (`obo.obo:7361`), so a body-less `native fn` declares each
result type, for example `native fn getvalue(r: &Res, row: int, col: int): []byte`. Each name must match a spec `name`. The compiler checks each parameter and result
against its kind (`&[]byte` or `string` for `borrow_*`, `[]?[]byte` for
`nil_bytes_list`, `[]int` for `i32s` and `u32s`, `?T` for `own_opt`, a generated record
for `out_*`), and `--native-relocs` refuses any mismatch.

### 3.7 Sidecar, manifest, shim and `.sym`

The sidecar becomes `cinterop-relocs 2` (argument and result kinds, expanded parameter
counts, effects). The manifest becomes `cinterop-proto/2` and adds type entries,
registration, expansions, constants and `signature_check` (5.7, point 5). Each function
gets one thunk, keeping `rsp` 16-byte aligned; native design section 6 required touching
only `rbp`, so larger thunks need review. Calls with more than six C parameters
(`PQexecParams` has eight) pass extra words on the stack. Exporting a foreign type needs a
`.sym` writer change (D6).

---

## 4. Soundness rules

R1. **Borrows are call-scoped.** No pointer-valued obo value exists. Borrow kinds are
argument-only, never returned, never a field or element type, and never retained.

R2. **No obo code runs during a native call** (native design section 2). Between argument
loads and the native return only runtime helpers and the native function run, so a
borrowed array cannot grow or be freed. The `afterCall` invalidation of references
(native design section 8.4) still applies.

R3. **Borrows are reads.** They are `const`, do not move the source, and never alias a
copy-out result. A foreign value has one owner, and mutable borrows are out of scope.

R4. **NUL.** `borrow_cstr` rejects an embedded NUL in the helper, and wrappers pre-check.
`cstr_out` stops at NUL within its cap. `cbytes` never calls `strlen`. Binary data is
never NUL-checked.

R5. **Non-NULL for empty values.** Empty arrays, the nil string and zero-length borrows
pass a non-NULL pointer. NULL comes only from `null` or from a nil list element. This
matters for SQLite: a NULL data pointer makes `sqlite3_bind_blob` bind SQL NULL.

R6. **Single owner, not forgeable.** A foreign value cannot be copied, converts from and
to no integer, and has a private field.

R7. **Release is a leaf.** The release function runs from `rt_free` with no obo code on
the stack, frees only memory it owns, and is registered before `main`. `release_now` makes
a second release a false return, and a tag mismatch traps at free time.

R8. **No use after release.** A moved value is a compile error. A borrowed released value
traps (D4). `handle(x)` exits 7 (C8).

R9. **Threads.** Borrowed arrays must be unreachable from other workers during the call, and
natives may run on workers only once the threads design allows it (C11).

R10. **Bounds first.** Counts are capped, byte lengths fit `int` where the C parameter is
`int`, copy-out lengths are capped, and `i32` values are range checked, all before a
pointer is formed.

R11. **Trusted C.** Trusted and unchecked: each function's copying, non-retention and NULL
behaviour; the C signature (header, or `declarations-only` with no claim); each release
function being correct and idempotent; validity of returned pointers until their owner
releases them; writes confined to the given buffers; no callbacks into obo; thread safety.
Hashes give provenance only (native design section 10).

R12. **Out handles are owned from the write.** An `out_own` slot that C has written is a
foreign value whatever the return code, so drop releases it on the error path too.

R13. **A child holds no parent pointer.** A foreign release must not depend on another
foreign value being alive, unless the spec names a parent and the library refcounts it
(D14). The SQLite port satisfies this with `sqlite3_close_v2`.

---

## 5. Worked examples

### 5.1 Worked example 1: libpq

| libpq function | Kinds | Obo wrapper |
|---|---|---|
| `PQlibVersion` | `i32` | `lib_version` |
| `PQconnectdb` | `borrow_cstr@0`; ret `own_opt<PGconn>` | `connect` |
| `PQstatus` | `borrow<PGconn>@0`; ret `i32` | `status` |
| `PQerrorMessage` | `borrow<PGconn>@0`; ret `cstr_out` | `error_message` |
| `PQexecParams` | `borrow<PGconn>@0`, `borrow_cstr@1`, `count_of@2`, `u32s@3`, `nil_bytes_list@2`, `i32s@4`, `i32@5`; ret `own_opt<PGresult>` | `exec` |
| `PQresultStatus`, `PQntuples`, `PQnfields` | `borrow<PGresult>@0`; ret `i32` | `result_status`, `ntuples`, `nfields` |
| `PQgetisnull`, `PQgetlength` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `i32` | `is_null`, `length` |
| `PQgetvalue` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `cbytes`, length `PQgetlength` | `value` |
| `PQfname`, `PQresultErrorMessage` | `borrow<PGresult>@0`, `i32@1` or none; ret `cstr_out` | `fname`, `result_error` |
| `PQftype`, `PQfmod` | `borrow<PGresult>@0`, `i32@1`; ret `u32` or `i32` | `ftype`, `fmod` |
| `PQfinish`, `PQclear` | release tags | drop |
| `PQconninfoParse` | out-parameter and struct array | C helper (5.4) |

### 5.2 libpq wrapper (sketch, not compiled)

```obo
module libpq

type Conn = foreign "PGconn"    // D2; PQfinish on drop
type Res = foreign "PGresult"   // PQclear on drop
type Rows = ref record { h: Res; pub fmt: int }

native fn connectdb(dsn: &[]byte): ?Conn
native fn status(c: &Conn): int
native fn error_message(c: &Conn): []byte
native fn exec_params(c: &Conn, sql: &[]byte, params: &[]?[]byte,
                      oids: &[]int, formats: &[]int, binary: int): ?Res

pub fn connect(dsn: &[]byte): Result[Conn] {
    if !nulFree(dsn) { return err("connection string contains a NUL byte", 2) }
    var c = connectdb(dsn)
    if c == nil { return err("connection could not be allocated", 2) }
    let conn = take(c)
    if status(&conn) != 0 {
        return err(string(error_message(&conn)), 1)   // conn drops here: PQfinish once
    }
    return ok(conn)
}

pub fn (c: &Conn) close(): bool { return release_now(c) }
```

`exec` pre-checks SQL with `nulFree`, builds ordinary arrays (no packet), and calls
`exec_params`. Each wrapper is about 15 lines with no byte loop.

### 5.3 libpq spec fragment

```json
{"format": "cinterop-proto/2", "module": "libpq", "platform": "x86_64-linux-gnu",
 "abi": "sysv-amd64", "libs": ["-l:libpq.so.5"], "signature_check": "declarations-only",
 "types": [{"name": "PGconn", "obo": "Conn", "release": "PQfinish"},
           {"name": "PGresult", "obo": "Res", "release": "PQclear"}],
 "functions": [
   {"name": "exec_params", "symbol": "PQexecParams",
    "args": ["borrow<PGconn>@0", "borrow_cstr@1", "count_of@2", "u32s@3",
             "nil_bytes_list@2", "i32s@4", "i32@5"],
    "ret": "own_opt<PGresult>", "retains": "none"}]}
```

The C order (conn, command, nParams, paramTypes, paramValues, paramLengths, paramFormats,
resultFormat) matches `args`. `count_of@2` and `nil_bytes_list@2` both read list 2; the
tool checks the counts agree and every argument is used.

### 5.4 What is deleted and what remains

Deleted from the generic path: `toolkit/cres.c` and `cres.h` (205 and 60 lines), and
`libpq_adapter.c` and `.h` (417 and 40) except the conninfo check. Kept: `libpq_decls.h`
(91 lines, declarations-only) and `tests/fake_libpq.c` (363, ABI kept, counters become
release counts). `examples/libpq.obx` (300) and `libpq.cbind` (356) are rewritten, and the
obo example should land near 150 lines.

`PQconninfoParse` takes a `char **errmsg` and returns a struct array, which is neither a
kind nor simple obo. The connect-timeout check (`conninfo_has_timeout`,
`libpq_adapter.c:89`) moves into `pq_conninfo_has_connect_timeout` in
`toolkit/libpq_conninfo.c` (about 30 lines). D13 may remove it.

### 5.5 SQL template literal to `PQexecParams`

A `Sql` value already separates text from parameters (template-literals design, lines
331-340), so the mapping is pure obo. `Null` gives nil; `Int` gives OID 20 with decimal
text; `Bool` gives OID 16 with `t` or `f`; `Str` gives OID 25 and `nulFree` refuses NUL;
`Decimal` gives OID 1700 with exact text; `Bytes` gives OID 17 with binary format (D10).
`Float` is rejected in v1 (D10). SQL text is never interpolated.

### 5.6 Gaps the libpq example missed (from `postgres/`)

`postgres/postgres.obx` (694 lines) and `postgres/postgres_adapter.c` (291 lines) show four
things the first libpq example missed:

1. **Typed parameters.** The packet sets a type per kind: 0 for untyped NULL, 25 text,
   17 bytea with binary format (`postgres_adapter.c:213-216`), 20, 16, and 1700 numeric,
   including typed NULL kind 6 (`postgres_adapter.c:236-246`). The earlier 5.3 spec passed
   `null` for `paramTypes`, which is wrong for this packet; `u32s@3` and an `oids` argument
   (as 5.2 and 5.3 show) fix it. The C call is at `postgres_adapter.c:258`.
2. **`Bytes` and D10.** Typed OID 17 with binary format sends raw bytes with no `::bytea`
   cast in SQL text, the recommended answer to D10.
3. **Decimal checks move to obo.** The strict grammar (`valid_decimal_text`,
   `postgres_adapter.c:102`) and the 98,342-byte cap (`PG_MAX_DECIMAL_TEXT`, lines 34 and
   105) are C today. The obo port repeats them, and stage (f) runs one negative table
   against both. This moves trust; it does not delete it.
4. **Result wrapper and status.** `Rows` has a public `fmt` (`postgres.obx:39`, `445`,
   `595`), so it wraps a foreign result (5.2). A SQL error is `ok(Rows)` with
   `PGRES_FATAL_ERROR`, read before release. `decimal_type` reads `PQfmod`, which may be
   -1, so the `i32` return must sign-extend.

### 5.7 Worked example 2: SQLite

Scope: the subset `db/sqlite/sqlite_adapter.c` calls, declared in `sqlite_decls.h` (81
lines). Three declarations are new: `sqlite3_close_v2`, `sqlite3_next_stmt` and
`sqlite3_errcode`. Obo already holds only integer tokens (`sqlite.obx:65-66`).

| SQLite function | Kinds | Obo wrapper |
|---|---|---|
| `sqlite3_open_v2(path, &db, flags, NULL)` | `borrow_cstr@0`, `out_own<Db>`, `i32@1`, `null`; ret `i32` | `open` |
| `sqlite3_close_v2(db)`, `sqlite3_finalize(stmt)` | release tags `Db`, `Stmt` | `close`, drop |
| `sqlite3_next_stmt(db, NULL)` | `borrow<Db>@0`, `null`; ret `nonnull` | `has_live_statements` |
| `sqlite3_prepare_v2(db, sql, n, &stmt, &tail)` | `borrow<Db>@0`, `borrow_bytes_i32@1`, `out_own<Stmt>`, `out_offset@1`; ret `i32` | `prepare` |
| `sqlite3_step`, `sqlite3_reset` | `borrow<Stmt>@0`; ret `i32` | `step`, `reset` |
| `sqlite3_bind_null`, `sqlite3_bind_int64` | `borrow<Stmt>@0`, `i32@1`, plus `i64@2` for int | `bind_null`, `bind_int` |
| `sqlite3_bind_text`, `sqlite3_bind_blob` | `borrow<Stmt>@0`, `i32@1`, `borrow_bytes_i32@2`, `const:-1` | `bind_text`, `bind_blob` |
| `sqlite3_bind_parameter_count`, `sqlite3_column_count`, `sqlite3_column_type` | `borrow<Stmt>@0` (plus `i32@1` for type); ret `i32` | `param_count`, `column_count`, `column_type` |
| `sqlite3_column_int64` | `borrow<Stmt>@0`, `i32@1`; ret `i64` | `column_int` |
| `sqlite3_column_text` with `_bytes` | `borrow<Stmt>@0`, `i32@1`; ret `cbytes`, length `sqlite3_column_bytes` | `column_text` |
| `sqlite3_column_blob` with `_bytes` | as above, value `sqlite3_column_blob` | `column_blob` |
| `sqlite3_changes`, `sqlite3_errcode` | `borrow<Db>@0`; ret `i32` | `changes`, `error_code` |
| `sqlite3_errmsg`, `sqlite3_errstr` | `borrow<Db>@0` or `i32`; ret `cstr_out` | `error_message`, `error_string` |
| `sqlite3_exec` (not bound) | none; obo loop over prepare, step, reset, finalize | `exec` |

The `cbytes` entry names the value function as `symbol` and the length function in
`length`:

```json
{"name": "column_text", "symbol": "sqlite3_column_text",
 "args": ["borrow<Stmt>@0", "i32@1"], "ret": "cbytes",
 "length": "sqlite3_column_bytes"}
```

**Point 1. Parent and child lifetime.** `sqlite3_close` returns `SQLITE_BUSY` while
statements are live. The adapter enforces that by counting `stmt_live`
(`sqlite_adapter.c:229-232`), and `release_stmt` decrements through `s->owner`
(`sqlite_adapter.c:140`). That is safe only because explicit close refuses while
`stmt_live > 0` (C14). Under drop-driven release the invariant fails: a `Conn` dropped
before its `Stmt` frees `sq_conn`, and the later `release_stmt` reads freed memory.
Locals drop in reverse declaration order (`freeOwned`, `obo.obo:4070-4082`, from `closeBlock`, `5372`), so `let c` then `let s` is safe.
A `Conn` in an earlier record field or array, or a `Stmt` returned out of its scope,
is not.

Options:

- **(A) Recommended.** `release_conn` calls `sqlite3_close_v2`. SQLite keeps the handle as
  a zombie until its last statement finalizes, so drop order cannot affect memory safety.
  A `Stmt` holds only its `sqlite3_stmt*` (R13). Explicit `close()` keeps its contract
  (`false` while `has_live_statements()`) via the `nonnull` probe. No language rule changes.
- **(B)** Statements owned inside the connection: changes the API shape. Not recommended.
- **(C)** A generic "child keeps parent alive" rule: needs a stored reference, which obo
  does not have (`LANGUAGE.md` section 4). Left as D14.
- **(D)** Trapping when a parent drops with live children turns valid scope orders into
  traps. Rejected.

**Point 2. Borrowed results.** `sqlite3_column_text` and `_blob` pointers last until the
next step, reset, finalize or conversion of that column. `cbytes` calls the value function,
then the length function, and copies, all in one native call, so no pointer outlives the
call. The ordering rule that remains is about types: `column_type` must be read before any
accessor, since a conversion makes the type undefined. The wrapper does so, as
`column_value` does (`sqlite.obx:385-394`) and the adapter does (`sqlite_adapter.c:486`,
`503`), and refuses REAL first (`sqlite.obx:392-393`).

NULL needs care. `sqlite3_column_blob` returns NULL for a zero-length BLOB, so NULL with
length 0 is empty there. `sqlite3_column_text` returns NULL for SQL NULL and for
out-of-memory, while empty text is non-NULL. The wrapper checks the type first (NULL means
nil), and after an empty text copy it calls `error_code` and returns `err` on
`SQLITE_NOMEM`. The adapter's `-2` return enforces this (`sqlite_adapter.c:480-495`). This
is obo policy, not a shim.

**Point 3. `SQLITE_TRANSIENT` without callbacks.** The destructor
`(sqlite3_destructor_type)-1` (`sqlite_decls.h:25-26`) is the spec constant `const:-1`. It
consumes no obo argument and produces one C word, with no relocation and no callback.
SQLite copies the value before the call returns, so this is the only destructor that fits
R1. `SQLITE_STATIC` (0) and function pointers are refused in destructor positions. No C
shim remains for bind. Two tests check it: text bound from an array that obo then
overwrites before `step` keeps the bound bytes, and a zero-length blob binds as a BLOB, not
NULL (R5).

**Point 4. Out-parameters.** `sqlite3_open_v2` writes `*ppDb` even on failure, and the
adapter must close it (`sqlite_adapter.c:186-188`). `sqlite3_prepare_v2` may write a
non-NULL `*ppStmt` with an error (`sqlite_adapter.c:324-325`). Under `out_own` (R12) both
are owned from the write, so the error path releases them by drop, and `open` needs no
close code. The wrapper reads `error_message` before the record drops, which scope order
guarantees. `open` returns the generated record `{rc, db: ?Db}`.

`out_offset@1` gives the tail position. The obo `prepare` then applies the adapter's
rule: only spaces, tabs, CR, LF and `;` may follow (`only_one_statement`,
`sqlite_adapter.c:293-298`). `exec` becomes a prepare, step, reset and finalize loop, which
is what `sqlite3_exec` does with no callback, so `errmsg` and `sqlite3_free` are not needed.

**Point 5. Declarations-only checking.** `sqlite.cbind` sets `"headers": []` and has no
signature-mode field; `declarations-only` lives only in the README. Proposal:
`signature_check` is required and is `"header"` (the generated sigcheck compiles against
the header, and a mismatch is a compile error, `cinterop.py:299`) or `"declarations-only"`
(no claim, recorded in the manifest). This host has no `sqlite3.h`, so the mode is
declarations-only. The real `libsqlite3.so.0` 3.45.1 tests are the ABI gate; the
declaration hash is provenance only (R11). The README's header cross-check was done
outside the repository, so it is a review note, not a gate.

---

## 6. Decisions for the user

Each decision changes a semantic, a format, an exit code or the toolkit's shape. The
recommended default is first.

- **D1. Typed native declarations (blocks stage (b)).** Recommended: body-less `native fn`
  with kinds in the spec (3.6). Alternatives: a compiler flag that reads the spec, or
  `__cinterop[?Conn]("PQconnectdb", dsn)`. The recommended form is a language change.
- **D2. Foreign type syntax.** Recommended: `type Conn = foreign "PGconn"`, making
  `foreign` reserved. Alternative: `cinterop "PGconn"`. A plain `ref record` with a
  spec-only marker hides the foreign nature.
- **D3. Explicit close.** Recommended: deferrable `release_now`, with later native calls
  trapping. Alternative: consuming `close(c: Conn)`, which cannot be deferred and changes
  the move path (stage (e) risk).
- **D4. Exit codes.** Recommended: code 11 for native contract violations (NUL past the
  helper check, `cbytes` NULL with positive length, `i32` out of range, list mismatch or
  above cap, `own` NULL). A released foreign handle exits 7. Alternative: reuse 3 or 4,
  which conflates meanings. `LANGUAGE.md` section 9 records codes at acceptance.
- **D5. Live foreign values at normal return.** Recommended: report under `OBO_CHECK` with
  exit 5. Values left at `exit` or a trap are not reported (C5).
- **D6. `.sym` for exported foreign types.** Recommended: a format bump in the release that
  first exports one, with its own version decision (none assigned here). Alternatives:
  unexported foreign types (not viable for a `Conn` API), or source-only imports (native
  design section 14.5).
- **D7. Retire `cres` from the generic path.** Recommended: yes, keeping `cres.c` for
  integer-ID APIs. Alternative: both paths, doubling the test matrix.
- **D8. Arrays of borrowed buffers.** Recommended: scoped kinds `nil_bytes_list`, `i32s`,
  `u32s` and `count_of`, capped at 65535. Alternative: one helper, `pq_exec_params_list`,
  which keeps C parsing for later libraries.
- **D9. Terminator for `borrow_cstr`.** Recommended: scratch copy (3.2). Reserving a
  terminator changes every ordinary layout.
- **D10. Parameter typing for `Float` and `Bytes`.** Recommended: reject `Float` in v1; bind
  `Bytes` with OID 17 and binary format, as `postgres/` does (5.6). Alternatives: a
  `::bytea` cast in the template (puts a cast in SQL text); escaped text (larger); a
  documented float format (not without an exact formatter).
- **D11. `cstr_out` and NULL.** Recommended: empty, documented per function, as the adapter
  does. Alternative: `?[]byte`, which changes every call site.
- **D12. Public names.** `Connection` becomes `Conn`, and `Rows` wraps `Res`. Recommended:
  keep old names as aliases for one experimental release.
- **D13. Default `connect_timeout`.** Recommended: keep it and the helper. Alternative: drop
  the default, removing the helper and changing behaviour for DSNs without a timeout.
- **D14. Parent and child lifetime (5.7, point 1).** Recommended: no language rule. SQLite
  uses `sqlite3_close_v2` on the drop path, and explicit close keeps its refusal through the
  `nonnull` probe. Alternatives: a generic "child keeps parent alive" rule (needs stored
  references, a language change), statements owned inside a connection (an API change), or
  a trap when a parent drops with live children (rejected). The user decides only whether
  to ask for the generic rule later.

---

## 7. Staged plan

Every stage keeps the gates in native design section 12. A compiler-changing stage also
runs the section 14.6 and 14.10 gates (self-build fixed point, `module_test.py`,
`elf_test.py`, `sym_test.py` when `.sym` changes, `run_fork.py --smoke`, `hello.obx`
byte-identical, peak within 2,500,000 bytes) and `test_cinterop.py`, with live tests
skipped unless the user opts in.

**Stage (a): borrowed input.** Adds `borrow_bytes@k`, `borrow_bytes_i32@k`, `borrow_cstr@k`,
`null`, `const:<n>`, the scratch helpers, thunk expansion and sidecar version 2 input kinds.
Checker: a borrow does not mark its source moved. Risk: medium. Gate: existing scalar,
startup, negative, pq and fake tests; a `bytes` fixture (a C sum over an empty array, 1 MiB,
a nil string, embedded NULs, and a rejected embedded NUL in `borrow_cstr`); a fixture that
records a `const:-1` argument; the scratch block freed (allocation count returns).

**Stage (b): copy-out.** Adds `cbytes` (value and length in one thunk), `cstr_out`, copy
helpers and the D1 surface. Checker: a native result is a new owned temporary. Risk:
medium-high, blocked on D1. Gate: fake `PQgetvalue` for empty, NUL-containing and
NULL-with-length (which traps with D4); `cstr_out` stops at NUL and rejects a missing NUL
past the cap.

**Stage (c): `i32`, `u32`, `u32s`, `count_of`.** No checker change. Risk: low. Gate:
`INT32_MIN` and `INT32_MAX` accepted, one past either end traps; `u32` zero-extends an `Oid`
above 2^31; `u32s` rejects elements above `2^32 - 1`; `count_of` rejects a list above the cap.

**Stage (d): foreign types and out-parameters.** Adds the `foreign` kind, `own`, `own_opt`,
`borrow<T>`, `borrow_opt<T>`, `nonnull`, `out_own<T>`, `out_offset@k`, the generated result
record, R12 and the foreign counter. Checker: `isOwned`, `tname`, field privacy, equality
and conversion rules, the generated record, and the `.sym` writer (D6). Risk: high. It
cannot ship without stage (e). Gate: compile-error tests, the counter, and a fixture where
`out_own` is written and the return code is an error (released exactly once).

**Stage (e): release on drop.** Adds the release tag, the `rt_free` release step, the
registration slot and constructor, `release_now`, the released-handle check and the
`OBO_CHECK` report (D5). Checker: `release_now` modelled on `drop` (`obo.obo:8416-8433`).
Risk: high. Gate: the lifecycle matrix (section 8), with fake counts showing exactly one
`PQfinish`, `PQclear` and `sqlite3_close_v2` per object on every path.

**Stage (f): port libpq.** Rewrites `libpq.obx` and `libpq.cbind` (typed OID packet,
`Rows` wrapper); deletes `cres.*` and `libpq_adapter.*` except the conninfo helper; moves the
decimal grammar and cap into obo; rebinds zlib as a second library. Risk: low to medium.
Gate: the cinterop suite with fake tests counting releases; zlib roundtrip and errors; the
opt-in live smoke test on a user-supplied local DSN; the decimal negative table against both
checks; no `cres` or adapter symbol left in the generic path.

**Stage (g): port SQLite.** Adds `sqlite3_close_v2`, `sqlite3_next_stmt` and
`sqlite3_errcode` to `sqlite_decls.h`; rewrites `sqlite.obx` and `sqlite.cbind` to the 5.7
spec; implements `exec` as an obo loop and the tail rule in obo; deletes `sqlite_adapter.c`
and `.h`. Checker: none beyond (d) and (e). Risk: medium. Gate: the SQLite tests in section 8
(real 3.45.1 and the new fake); the existing `sqlite_memory`, `sqlite_file`,
`sqlite_decimal` and `sqlite_c` tests with unchanged output; the sanitizer run over the
parent and child matrix.

Ownership-checker exposure: (a) the borrow rule; (b) moderate; (c) none; (d) and (e) strong;
(f) none; (g) none beyond (d) and (e).

---

## 8. Test plan

**Positive.** `borrow_bytes` over empty, 1-byte, 1 MiB and string inputs (nil string gets
length 0 and a non-NULL pointer); `borrow_cstr` over ASCII, empty and UTF-8; `cbytes` over
0, 1, N-with-NULs and NULL-with-length-0; `cstr_out` stopping at NUL; `nil_bytes_list` with
nil entries, binary NULs, count 0 and the cap; `i32` boundaries and `u32` zero-extension;
foreign connect, exec, read, `defer` close, scope-end drop, and `alive(h)` false after drop.

**SQLite, real library (3.45.1).** Open and close a file database; prepare, bind int, text,
blob and NULL; step and reset with rebinding; `column_text` and `column_blob` with embedded
NULs and a zero-length BLOB (a BLOB type, not nil); a REAL column refused before conversion;
`changes`; `exec` over several statements; `has_live_statements` both ways; the `const:-1`
overwrite test (5.7, point 3).

**SQLite, fake (`tests/fake_sqlite.c`, new).** Mirrors the 5.7 subset with knobs: `open_v2`
fails with a non-NULL `*ppDb` (closed exactly once by drop); `prepare` fails with a non-NULL
`*ppStmt` (finalized once, error text kept); `column_text` returns NULL with `SQLITE_NOMEM`
(the wrapper returns `err`, not empty); `close_v2` zombie counting (closes only when the last
statement finalizes); and it records the destructor argument, copying immediately for -1, so
a read after overwrite fails for any other value.

**Negative.**
- Embedded NUL in `borrow_cstr`: the wrapper returns `err`; a direct call traps (D4).
- Use after `release_now` traps; `alive` is false for a handle taken before. Double close
  returns false; a double drop of a moved value is a compile error.
- A borrow with `retains` other than `none` is refused; `const:` with a value other than -1
  in a destructor position, including `SQLITE_STATIC`, is refused.
- `Res` passed to `status`, a foreign value in arithmetic, comparison, printing, `int(x)`,
  a literal, or a field write from another module: each a compile error. A mismatched
  release tag in the manifest is refused at build.
- List count above the cap, or formats or oids length differing from the list: a trap or a
  build refusal. Sidecar tamper is refused by the manifest check.
- `prepare` with two statements, or trailing text other than spaces or `;`: refused in obo.
  Bind index out of range, or bind after finalize: an error code.
- `exit()` or a trap with a live `Conn`: no release is counted; `OBO_CHECK` follows D5.
- `signature_check: "header"` without a header path, or a header claim when the header is
  absent: refused at build.

**Lifecycle matrix (stage (e)).** Scope exit; early return; a `try` error path; `defer`;
a loop creating and dropping values; nested records holding a `Res` that outlives its
`Conn`; module globals at teardown; `exit`; a trap; `release_now` then drop. SQLite: a
`Conn` dropped before its `Stmt` (in `let` order, a record field, an array element, and a
`Stmt` moved out of scope); a `Stmt` dropped before its `Conn`; and `close()` while a
statement is live (`false`, connection still usable), then finalize, then close. Run under
the existing sanitizer step (`test_db_adapters.py`, `sanitizers`), which would catch a
use-after-free in the old `release_stmt` shape.

---

## 9. Non-goals

- No callbacks, C variadics, struct-by-value, `long double`, floats or decimals across the
  native ABI (native design section 2); no mutable borrows, borrowed foreign returns, raw
  pointers or `void*`.
- No release at `exit`, a trap or `atexit`. No SQL driver: libpq and SQLite stay bounded
  adapters (native design section 8.5). No universal generator (native design section 1.1).
- No change to `LANGUAGE.md`, `obo/design/README.md`, `obo/FORK.md` or the seed; each is a separate review when a stage is proposed.
- No TLS, IFUNC, threads, PIE or ASLR, and no stable `cinterop/1` or `cabi1`.
