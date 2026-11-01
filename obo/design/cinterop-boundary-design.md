# Native C interop: generic boundary types (v2 proposal, revision 4)

Status: **approved design** (2026-11-01): user decisions recorded in 7.1; implementation proceeds in stages (a)–(h); still experimental, no stable format or public API.
Date: 2026-11-01. Revision 4 follows the independent re-review of 715af0f (verdict REVISE). It keeps the
user's cleanup decision of 2026-11-01: foreign handles are **file-consistent** (explicit borrowing close, leak
census under `OBO_CHECK=1`), not must-close. Revision 4 fixes sketch and codegen errors, specifies the exit
paths, and moves the user-visible defaults into the user list (section 7).
Baseline: `main` `44e812a`. Builds on experimental cinterop per `native-c-interop-design.md` section 14.10.

This note extends `native-c-interop-design.md` sections 8 and 14 and keeps its steering (section 1.1: a small
toolkit plus an agent skill, no universal generator). It proposes two language additions, body-less `native fn` and
`foreign` types (D1), and changes no other language rule. The rest is codegen and runtime: the generated methods,
the census and the thunk checks in 3.4. Nothing here is implemented, and no compiler, runtime, `.sym`, ABI or package
version is taken.

## Summary

1. Four primitives in one `__cinterop` seam: borrowed input, copy-out, foreign handle types, and typed
   declarations. Each is a C-type shape, not a library feature.
2. A foreign handle is a compiler-generated owned record with one private, nonzero-when-live word. Its
   generated `close(mut &T): Result[bool]` zeroes the word before the C release call. A closed handle
   gives `ok(false)` with no C call. Dropping an open handle leaks it, as dropping an open fd does.
3. Use after close stops the program with exit 7 in the native thunk. `is_open(&T)` lets wrappers return
   an `err` first.
4. A per-type census counts live handles. Under `OBO_CHECK=1`, a nonzero count at normal end exits 8,
   like the fd census. `exit(n)` and traps run no census, exactly as for fds (3.4, table).
5. Fields, optionals, `Result`, arrays and globals need no special rules. They behave as records do today.
6. About 16 generic kinds, with flags. No kind names a library, SQL statement or packet format. libpq and
   SQLite bind through small obo wrappers (5.2, 6.3). zlib keeps its adapter.
7. SQLite adopts `sqlite3_close_v2`. Its blind spot (a closed `Db` whose statements are still open) is
   documented in 6.2.
8. Stages (a) to (h) in section 8. Stage (d), foreign handles, is the riskiest. It adds a `foreign` record
   kind, name-resolution and private-field checks, and per-type runtime counters, and no ownership-checker rule.

---

## Benefits and trade-offs

Compared with today's prototype (one hand-written C adapter per library, integer tokens in `cres`):

**For binding authors**
- libpq: the adapter is 417 lines (`toolkit/libpq_adapter.c`), plus the `cres` registry it uses (205 and 60 lines). The
  design leaves a libpq binding of roughly 150 lines of obo (an estimate from the 5.2 sketch and 5.3 spec) and one small
  conninfo helper of about 30 lines (5.4).
- SQLite: the adapter (528 lines) reduces to one 4-line errcode helper (6.1). Nothing else is C.
- No byte-per-call loop: `borrow_bytes` passes an array's pointer and length in one call, `cbytes` copies a value in one
  native call, and `PQexecParams` takes obo arrays directly. Today `put_bytes` crosses once per byte
  (`examples/libpq.obx:34-47`), and the packet is built in obo and parsed in C (`postgres_adapter.c`, 291 lines).
- No per-library compiler change: a library is a spec plus obo wrappers. The kinds in 3.1 are added once, in stage (c).

**For users (obo programs)**
- A foreign handle is a real type. It cannot be forged or converted from an integer and has no arithmetic. Passing a
  `PGresult` where a `PGconn` belongs is a compile error, not a runtime registry rejection.
- One owner means one pointer copy (R6). `close` zeroes that word, a second close is `ok(false)` with no C call, and a use
  after close stops cleanly with exit 7 instead of touching freed memory (3.4).
- The file pattern carries over: `close` returns `Result[bool]`, `defer c.close()`, and the `OBO_CHECK=1` census (exit 8),
  as for `closeFile` and `Dir.close`.
- Preserved behaviour: NULL versus empty (R5), exact numeric text (decimal grammar and 98,342-byte cap, C15), NUL rejection,
  out-of-range rows and columns as `err` (review 2), and the existing size limits (C15).

**Language and compiler**
- Two language additions, both under D1: body-less `native fn` and `foreign` types. No other `LANGUAGE.md` rule changes.
- Foreign types reuse record machinery (layout, drop, `take`, `alive`), with one private word that is in neither offset list (3.4).
- No new ownership-checker rules. This follows from the file-consistent cleanup decision (section 7). Stage (d) adds name-resolution and private-field checks for the foreign kind; those are not ownership rules.
- No linker in the compiler: library names stay in the spec's `libs` field, handled by the existing cinterop build.

**Bugs fixed on the way**
- SQLite `release_stmt` use-after-free when a connection is closed before its statements (`sqlite_adapter.c:136-143`,
  C13). `sqlite3_close_v2` avoids it (6.2, point 1), and a test reproduces the old ordering (section 9).
- Already merged on `main` (commits 35c500b, 11a5962, cf9bbab, merged in 44e812a): libpq conninfo diagnostics, limit and
  header-mismatch errors, and the review fixes. The design keeps them and moves the limits into obo wrappers.

**Trade-offs and what is not provided**
- Leak detection is runtime only, under `OBO_CHECK=1`, as for files. A missing `close` is found by a run, not by the
  compiler (7.3 is the future option).
- No callbacks, C variadics, struct-by-value, floats or decimals across the ABI (section 11).
- Single thread (C10). Foreign handles are thread-confined.
- Still experimental, with a trusted build: the C side is trusted (R11), and the census does not see C-side zombie statements
  (6.2 blind spot).
- Costs: the new census and thunk check (3.4), and `Rows` accessors that need `is_open` checks (5.2) to return `err` rather
  than exit 7.

---

## 1. Problem and goals

Libpq and SQLite are bound through layers that exist only because the boundary is scalar. Integer-token
registries keep `PGconn*`, `PGresult*`, `sqlite3*` and `sqlite3_stmt*` on the C side (`toolkit/libpq_adapter.c`
417 lines, `cres.c` 205, `cres.h` 60, `libpq_adapter.h` 40; `db/sqlite/sqlite_adapter.c` 528). Bytes cross one
native call per byte (`put_bytes`, `examples/libpq.obx:34-47`), so a 1 MiB `bytea` costs about two million
crossings. The libpq packet is built in obo and parsed again in C. `nativeCall` (`obo/src/obo.obo:7361-7378`)
accepts only `int` arguments.

Goals. G1: a small fixed set of generic kinds, reusable by any library. G2: C pointers never become obo values;
borrowed input lives only for the call; bytes return only by copy. G3: a foreign handle is closed explicitly,
the way files are, and leaks are visible under `OBO_CHECK=1`. G4: libpq, SQLite and db/postgres bind from specs
with little obo, keep every existing limit, and need no C helper beyond conninfo. G5: gated stages that mark
checker exposure.

---

## 2. Verified constraints

C1. **No collector; explicit free only.** `rt_collect` returns at once (`obo/runtime/runtime.c:309-310`); blocks return only through `free_block` (`runtime.c:382`) from `rt_free` (`runtime.c:451-481`).

C2. **Arrays and strings.** Arrays are `{len, cap, data, esize}` (`runtime.c:377`), elements at `data + 8` (`runtime.c:541-548`), and growth frees the old block (`rt_append`, `runtime.c:564-583`). Strings are `[len][bytes]`, no terminator, reference counted (`runtime.c:428`, `433`); the nil string is 0 (`runtime.c:597-598`). Literals have no NUL (`obo/src/obo.obo:2993-3004`).

C3. **No pointer type in obo.** The seam accepts only `i64` arguments (`cinterop.py:220-223`) and sign-extends `i32` returns (`cinterop.py:215-216`).

C4. **Exit and traps run no defers and no census.** `exit(n)` lowers to `RT_EXIT` (`obo.obo:8746-8751`), which is `rt_exit`: flush, then `OBO_EXIT(n)` (`runtime.c:1539`). Traps go through `die_code` (`runtime.c:96`). Neither calls `rt_finish` (3.4).

C5. **Record drop calls no foreign hook.** `rt_free` frees owned fields and decrements strings (`runtime.c:451-481`). A foreign word is in neither list, so `rt_free` never reads or frees it.

C6. **The checker needs no new ownership rule.** It tracks moved/not-moved per owned local (`freeOwned`, `obo.obo:4070-4082`; `sameStates`, `obo.obo:4396-4399`). A change through a handle is a change (`LANGUAGE.md:502`). A field cannot be narrowed (`LANGUAGE.md:1109`). `take(x)` moves out of an optional, field or element and leaves nil (`LANGUAGE.md:263`; `obo.obo:8458`). Stage (d) adds only name-resolution and private-field checks for the foreign kind.

C7. **Checked handles.** A use of a freed `^T` object exits 7 (`rt_deref`, `runtime.c:498-500`). `alive(h)` tests that the object is not freed (`rt_alive`, `runtime.c:503`).

C8. **`cres` is bounded and integer-typed.** 64 entries (`cres.h:29`); close poisons the generation (`cres.c:78`). It stays for existing packages (D7).

C9. **Native calls need the typed sidecar.** `--native-relocs` is required (`obo.obo:15646-15672`); the tool checks it against the spec (native design section 14.2).

C10. **Threads are restricted.** Worker closures are limited to copied bytes and thread-safe natives that never call obo (`systems-threads-design.md` section 4.1, T1).

C11. **Other facts.** `rt_set_host_exit` is appended by the cinterop build without changing baseline bytes (`runtime.c:26`). `rtCall` passes at most four register arguments (native design section 3, which fixes the thunk frame). `LANGUAGE.md` section 9 assigns codes 2 to 9 and 10; 11 and up are unassigned.

C12. **Library non-retention is documented, not checked.** `PQconnectdb` and `PQexecParams` copy their inputs; `PQgetvalue` pointers last until `PQclear`. SQLite column text and blob pointers last until the next step, reset, finalize or conversion of that column (6.2, point 2).

C13. **The SQLite adapter's lifetime rule is explicit close only.** `sq_conn_close` refuses while `stmt_live != 0` (`db/sqlite/sqlite_adapter.c:229-232`), and `release_stmt` decrements through `s->owner` (`sqlite_adapter.c:140`).

C14. **The file model is the precedent.** `Dir.close(mut d: &Dir): Result[bool]` (`obo/lib/fs.obx:214`) and `sysfile.close(f: &File): Result[bool]` (`obo/experimental/sys/sysfile.obx:307`) return `ok(false)` for a closed handle (`sysfile.obx:274`). `closeFile(fd)` returns `Result[bool]` (`LANGUAGE.md:2116`). The fd census runs in `rt_finish` (3.4). The systems stdlib agrees: "explicit lifecycle", "no hidden finalizer" (`systems-stdlib-design.md` section 4.2, lines 399-427).

C15. **Existing limits to keep.** SQL text 1 MiB (`libpq_adapter.c:25`, `postgres_adapter.c:32`, `sqlite_adapter.c:22`). Values 16 MiB (`sqlite_adapter.c:23`, `postgres_adapter.c:33`). Connection string 16 MiB, equal to `CRES_MAX_BUFFER` (`libpq_adapter.c:160`). Path 4 KiB (`sqlite_adapter.c:21`). Parameters 32 (`postgres_adapter.c:31`). Decimal text 98,342 bytes (`postgres_adapter.c:34`, `105`). Default `connect_timeout=5` (`libpq_adapter.c:110`).

C16. **Temporaries and results.** A temporary must be stored in a variable before use (`LANGUAGE.md:304`). A reference cannot borrow from a temporary (`LANGUAGE.md:2589`). `string(x)` refuses a temporary `[]byte` (`obo.obo:7336`, `noTemp`). A discarded owned call result is freed at once by the expression statement (`obo.obo:10473-10494`), so `conn.close()` needs no `let _ =`. A deferred cleanup's result is discarded (`LANGUAGE.md:2056`).
## 3. Proposed boundary types

### 3.1 Kinds

A spec format `cinterop-proto/2` is proposed (experimental, no stable number). Argument kinds name the obo argument
they consume as `kind@k`. Flags follow a colon.

| Argument kind | obo argument | C parameters |
|---|---|---|
| `i64` | `int` | 1 `long` |
| `i32`, `u32` | `int` | 1 `int` or `unsigned int`, range-checked |
| `ints@k:i32`, `ints@k:u32` | `[]int` | 1 pointer to a copied array, range-checked per element |
| `ints@k:len` | list `k` | 1 `int`, the element count of list `k`, checked to fit `int` |
| `null` | none | pointer 0 |
| `const:<n>` | none | fixed value `n`; `-1` only as a destructor (6.2, point 3) |
| `borrow<T>@k` | `T`, live | `const T *` from the word (read-only); a zero word exits 7 before the call (3.4) |
| `borrow_mut<T>@k` | `T`, live | `T *` from the word (mutable); a zero word exits 7 before the call (3.4) |
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

A native with `out_*` kinds has no hand-written result type: the compiler generates a record named `<native>_out` from the spec, with `rc: int` plus one field per out kind, each
`?T`. An `out_own` field counts from the write, so the wrapper closes it on every path (R12). The 6.3
sketch names them in its native declarations.

**No library-specific kinds.** Every kind is a C type shape: a scalar, a foreign pointer, a byte buffer, a list,
or an out slot. No kind names a library function, a statement, a packet format or a SQL rule. Those belong in obo
wrappers, or in a trusted C helper that uses only generic kinds (6.1, `sq_stmt_errcode`).

### 3.2 Primitive 1: borrowed input

For `borrow_bytes`, an array passes `data + 8` and `len` (`runtime.c:377`, `541-548`); an empty array points at a
static block (`runtime.c:404-410`), so the pointer is never NULL. A string passes `s + 8` and `*s`; the nil string
uses a static zero block (`runtime.c:597-598`).

**Timing is a codegen rule (review 12).** The thunk call site evaluates every argument expression first. It then
loads the data pointer and length of every borrow, immediately before the call, and nothing runs in between (R2). So
a later argument that grows a borrowed array is seen by the native: `f(&a, grow(&a))` passes the grown array. This
is not a new checker rule. The checker's existing argument-overlap rules still apply (`LANGUAGE.md` error table: "may
point inside argument"). The borrow-timing test in section 9 covers the case: where the checker accepts the call, the native sees the grown array,
and where it refuses the call, the test asserts that refusal.

**`borrow_cstr`.** A hosted helper copies the input into `len + 1` zeroed bytes, traps on an embedded NUL (D4),
and the thunk frees it after the call. Wrappers pre-check NUL and return `err`. Terminators are not reserved in
ordinary arrays or strings, which would change every layout and break the byte-identical gate. `borrow_bytes` never
checks NUL. SQLite's `prepare` and `bind` take a length, so they need no NUL.

### 3.3 Primitive 2: copy-out

`cbytes` calls the value function, keeps `ptr` in its frame, calls the length function with the same argument words,
and copies with `copy_out`, which allocates through `new_block` (`runtime.c:541-548`). No obo code runs between the
calls. If the length is above `"cap"`, the native returns **nil**, and the wrapper returns `err` (review 3; the
adapter returns `err` today, `sqlite.obx:380-384`). A negative length, or `ptr` NULL with a positive length, exits
**11** (D4; stage (b) in section 8 implements it). A copy-allocation failure is
the runtime's out-of-memory exit 2 (`new_block`), not a nil return (review 19). `ptr == NULL` with `len == 0` gives an
empty array. The helper never calls `strlen`, so NULs are kept. SQL NULL never reaches `cbytes`: wrappers check the
type first (6.2, point 2). `cstr_out` copies up to NUL within 16 MiB.

### 3.4 Primitive 3: foreign handles, explicit close, census

**Declaration** (D1): `type Conn = foreign "PGconn"`. The spec names the release symbol and its result shape:
`"release": "PQfinish", "release_result": "void"` (or `"int"`; `"discard"` calls an int release and ignores its result). Obo code never names the release function.

**Representation and creation.** A compiler-generated `ref record` with one private field, the C word, nonzero while
the handle is live. The word is in neither the owned nor the string offset list, so `rt_free` never reads or frees it
(review 6). A foreign value is created only by a native result or an out slot (`own`, `out_own`), which must be
non-NULL. There is no user-constructible value, and no zero value that obo code can name. A closed value is simply a
record whose word has been zeroed.

**Generated methods.** For each foreign type the compiler emits:
- `close(mut t: &T): Result[bool]`. If the word is 0, it returns `ok(false)` and makes no C call (double close, review 9).
  Otherwise it copies the word, sets the field to 0, decrements the census counter, calls the release symbol with the copy,
  and returns `ok(true)`. For `"release_result": "int"`, a nonzero C result is `err` with that code. `"discard"` ignores the int a release such as `sqlite3_close_v2` returns. The handle is gone
  either way.
- `is_open(&T): bool`. It returns true when the word is nonzero.

The field is zeroed before the C call, so a release that traps or re-enters cannot release twice. The method form is
`t.close()`. It may be called on a local, a field, a reference, or through a handle (`^T`), since a change through a
handle is a change (`LANGUAGE.md:502`); a test covers the handle case (section 9). A discarded `Result[bool]` from a
statement `t.close()` is freed at once (C16), so no `let _ =` is needed. It is deferred the way `Dir.close` is:
`defer c.close()`. The existing defer rules apply unchanged.

**Why zeroing one word is enough (no `cres`).** A foreign handle is owned. Obo moves it and never copies or converts it
(R6), so there is exactly one copy of the C pointer in obo, and every `&T` reference points at that record. Zeroing the
word makes every obo reference see the close at once. What remains is C's own contract: a library that keeps a handle
internally (a `sqlite3_stmt` inside a `sqlite3`) is covered by that library's rules, here `sqlite3_close_v2` (6.2, point
1). `cres` exists because integer tokens are copied freely, so it needs a generation counter against stale copies. A
foreign handle has no copies to go stale, so it needs no generation.

**`alive` and `is_open`.** `alive(h)` on a foreign record is record liveness, as for any record: the object has not
been freed (`rt_alive`, `runtime.c:503`). It is not an open/closed query. `is_open(&T)` is the open query.

**Use after close.** Every native with a `borrow<T>` or `borrow_mut<T>` argument checks the word in its thunk before forming the C pointer.
A zero word stops the program with exit 7 and the message "foreign handle used after close". This is the code checked
`^T` handles already use (`runtime.c:500`). The check lives in the thunk, not in each wrapper, because a per-call check
in every wrapper is easy to forget and a C call such as `PQstatus(NULL)` is unsafe. It is one compare and is not gated
by `OBO_CHECK`, since it protects memory safety in normal runs. Wrappers that want a recoverable `err` call `is_open`
first (5.2 `Rows` accessors, 6.3).

**Census (leaks).** The hosted runtime keeps one counter per foreign type, a `static` alongside `live_owned`
(`runtime.c:144`). The counter goes up when a native returns a nonzero word as `own` or `out_own`, and goes down only in
the generated `close`. A decrement at zero stops the program with exit 4 "obo: internal error: foreign handle census
underflow" (`die_code`, `runtime.c:96`, the same code as the misaligned-stack check, `runtime.c:1536`). It can arise only
from a runtime bug, because `close` decrements only a nonzero word. A test hook, `OBO_CENSUS_FAULT=underflow`, forces the case; like `OBO_IO_FAULT` and `OBO_META_FAULT` (`runtime.c:231-236`), it is cleared unless `OBO_CHECK=1`.

**Census at exit and the existing order.** `rt_finish` (`runtime.c:507`) runs at normal end. Its order today is:
flush and stats, then, under `OBO_CHECK=1`, the fd census (`fdstate == 1`, exit 8, `runtime.c:517-527`), then the object
census (`live_owned`, exit 5, `runtime.c:529-536`), then `OBO_EXIT(0)`. The foreign census goes **between the two**: after
the fd census and before the object census. If any type's counter is nonzero, it writes "obo: foreign handles leaked: T n"
per type and exits 8. As with the fd census, only the first nonzero category exits, so a program with both a leaked fd
and a leaked foreign handle exits 8 with the fd message. The object census is not reached in that case, which matches
today's order.

**Exit and trap behaviour, all census paths.** The foreign census behaves exactly like the fd census:

| Path | What runs | fd census | foreign census | object census |
|---|---|---|---|---|
| normal return from `main` | `rt_finish` (`runtime.c:507`) | exit 8 under `OBO_CHECK=1` (517-527) | exit 8 under `OBO_CHECK=1`, after fd (new) | exit 5 under `OBO_CHECK=1` (529-536) |
| `exit(n)` | `RT_EXIT` → `rt_exit` (`runtime.c:1539`): flush, `OBO_EXIT(n)` | none | none | none |
| trap (`die_code`, e.g. exit 4, 6, 7) | message, `OBO_EXIT(code)` (`runtime.c:96`) | none | none | none |
| hosted build | `OBO_EXIT` → `exit_process` (`runtime.c:27-34`) → host callback | as above | as above | as above |

So `exit(n)` and a trap run no census, and open fds or handles are reclaimed by the OS. Nothing is closed and no release
symbol runs on these paths (C4). This is a settled behaviour, not a deferred check.

**Dropping an open handle is a leak.** Scope exit, `drop`, overwrite, or the `rt_free` of an enclosing record does not
release the word and does not trap. The counter stays up, and the leak is reported at normal end under `OBO_CHECK=1`.
This is the file rule: dropping an open fd leaks it.

**Fields, optionals, results, arrays and globals.** No special rules. A record with a foreign field is a record, and
`Result[T]` and `?T` work as they do for records now. An array of foreign handles is an array of records; dropping the
array drops its handles, which then leak as any dropped handle does. A global handle is counted like any other live
handle at normal end.

**Close errors.** A release declared `"release_result": "int"` gives `err` from `close`; the handle is gone either way. `"discard"` calls the same int-returning release but always gives `ok(true)` (D14).

### 3.5 Typed native declarations and the sidecar (D1)

The builtin `__cinterop` is typed `int` today (`obo.obo:7361`), so each native needs a body-less declaration. The spec
binds each name to its kinds. The compiler checks each parameter and result against the kind table, and
`--native-relocs` refuses a mismatch. Unit results use `void`. Example: `native fn getvalue(r: &Res, row: int, col: int): []byte`.

The sidecar becomes `cinterop-relocs 2` (argument and result kinds, expanded counts, effects kept as `freesall
writeshandles`). The manifest becomes `cinterop-proto/2` and adds `types` (release symbol and result shape), expansions,
constants and `signature_check` (6.2, point 6). `close` is generated; no release table is written. Each function gets one
thunk, keeping `rsp` 16-byte aligned, within the frame layout native design section 3 fixes. Calls with more than six C
parameters (`PQexecParams` has eight) pass extra words on the stack. Exporting a foreign type needs a `.sym` writer change,
deferred to the first exported foreign type (D6).

---

## 4. Soundness rules

R1. **Borrows are call-scoped.** No pointer-valued obo value exists. Borrow kinds are argument-only and are never returned,
stored or retained.

R2. **Borrow loads are last.** No obo code runs between loading a borrow's pointer and length and the native return. The
loads happen after all argument expressions (3.2). The `afterCall` reference invalidation (native design section 8.4) still
applies.

R3. **Borrows are reads.** They do not move the source. A copy-out result never aliases a borrow or a C buffer.

R4. **NUL.** `borrow_cstr` rejects an embedded NUL in the helper, and wrappers pre-check. `cstr_out` stops at NUL within its
cap. `cbytes` never calls `strlen`. Binary data is never NUL-checked.

R5. **Non-NULL for empty values.** Empty arrays, the nil string and zero-length borrows pass a non-NULL pointer. NULL comes
only from `null` or a nil list element. For SQLite this matters: a NULL data pointer makes `sqlite3_bind_blob` bind SQL NULL.

R6. **Single owner, not forgeable.** A foreign handle cannot be copied or converted from or to an integer, and its field is
private. Its word therefore exists in exactly one place, which is what makes zeroing one word sufficient (3.4).

R7. **Close is the only release.** Only the generated `close` calls a release symbol. A native never frees a foreign word, and
`rt_free` never reads one.

R8. **Leaks are visible, not prevented.** An open handle that is dropped stays counted and is reported at normal end under
`OBO_CHECK=1` (exit 8). No path releases it implicitly, and exit or a trap runs no census (3.4).

R9. **Threads.** Borrowed arrays must be unreachable from other workers during the call. Foreign handles are thread-confined
(C10).

R10. **Bounds first.** Counts are capped, lengths fit `int`, copy-out lengths are capped, and `i32`/`u32` values are range
checked, all before a pointer is formed. Obo wrappers check row, column and parameter indexes against the counts before any
native call (review 2).

R11. **Trusted C.** Trusted and unchecked: each function's copying, non-retention and NULL behaviour; the C signature (header,
or `declarations-only` with no claim); release functions being correct; returned pointers valid until their owner is closed;
writes confined to the given buffers; no callbacks into obo; thread safety. Hashes give provenance only (native design section 10).

R12. **Out handles are counted from the write.** An `out_own` slot that C has written is a live handle whatever the return code,
so the wrapper must close it on the error path too, or the census reports it.

---

## 5. Worked example 1: libpq

### 5.1 Functions and kinds

| libpq function | Kinds | Obo wrapper |
|---|---|---|
| `PQlibVersion` | `i32` | `lib_version` |
| `PQconnectdb` | `borrow_cstr@0`; ret `own_opt<PGconn>` | `connectdb`, used by `connect` (5.2) |
| `PQstatus` | `borrow<PGconn>@0`; ret `i32` | `status` |
| `PQerrorMessage` | `borrow<PGconn>@0`; ret `cstr_out` | `error_message` |
| `PQfinish` | release of `Conn` (spec `"release"`) | generated `close` |
| `PQexecParams` | `borrow_mut<PGconn>@0`, `borrow_cstr@1`, `ints@2:len`, `ints@3:u32`, `nil_bytes_list@2`, `ints@4:i32`, `i32@5`; ret `own_opt<PGresult>` | `exec_params` |
| `PQmakeEmptyPGresult` | `null` (NULL connection), `i32@0`; ret `own<PGresult>` | `make_empty` (empty `Rows`) |
| `PQresultStatus`, `PQntuples`, `PQnfields` | `borrow<PGresult>@0`; ret `i32` | `result_status`, `ntuples`, `nfields` |
| `PQgetisnull`, `PQgetlength` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `i32` | `is_null`, `length` |
| `PQgetvalue` | `borrow<PGresult>@0`, `i32@1`, `i32@2`; ret `cbytes` (length `PQgetlength`, cap 16 MiB) | `value` |
| `PQfname`, `PQresultErrorMessage` | `borrow<PGresult>@0`, `i32@1` or none; ret `cstr_out` | `fname`, `result_error` |
| `PQftype`, `PQfmod` | `borrow<PGresult>@0`, `i32@1`; ret `u32` or `i32` | `ftype`, `fmod` |
| `PQclear` | release of `Res` (spec `"release"`) | generated `close` |
| `PQconninfoParse` | out-parameter and struct array | stays in C (5.4) |

The read-only libpq prototypes take `const PGconn *`/`const PGresult *`, so those rows use
`borrow<T>` (C parameter `const T *`). `PQexecParams` takes a mutable `PGconn *` and uses
`borrow_mut<T>`. Stage (d) implements both forms; constness is only a C type check and both pass the
same live-word check.

### 5.2 libpq wrapper (sketch, not compiled)

```obo
module libpq

type Conn = foreign "PGconn"                    // spec: release PQfinish, void
type Res = foreign "PGresult"                   // spec: release PQclear, void
type Rows = ref record { h: Res; pub fmt: int } // h is closed when its word is 0

native fn connectdb(conninfo: &[]byte): ?Conn   // own_opt<PGconn>
native fn status(c: &Conn): int
native fn error_message(c: &Conn): []byte
native fn conninfo_has_timeout(s: &[]byte): int // the one C helper (5.4)
native fn make_empty(st: int): Res             // null, i32@0, own<PGresult>
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
        var mb = error_message(&conn)
        let msg = string(mb)                    // a local, not a temporary (C16)
        conn.close()                            // Result[bool] discarded: freed at once (C16)
        return err(msg, 1)
    }
    return ok(conn)                             // the caller defers close
}

pub fn (mut r: &Rows) close() {
    r.h.close()                                 // ok(false) if already closed
}

native fn getvalue(r: &Res, row: int, col: int): ?[]byte   // cbytes, length PQgetlength (5.1)

pub fn (r: &Rows) value(row: int, col: int): Result[?[]byte] {
    if !is_open(&r.h) { return err("rows are closed", 2) }   // no exit 7 from a closed Rows
    if row < 0 || col < 0 || row >= ntuples(&r.h) || col >= nfields(&r.h) {
        return err("row or column is out of range", 2)     // ntuples, nfields (5.1)
    }
    return ok(getvalue(&r.h, row, col))
}
```

Caller: `var db = try connect(dsn)` then `defer db.close()`. A missing `close` is a census leak under `OBO_CHECK=1`.
Rows accessors begin with `is_open`, as `value` does above, so a closed `Rows` gives `err` and never reaches
`make_empty` takes no connection: `PQmakeEmptyPGresult(NULL, status)` is how the current prototype calls it and how its "libpq result ownership" test checks it (`examples/libpq.obx` `make_empty_rows`). `make_empty_rows(status)` keeps its signature.

`exec` checks SQL length against 1 MiB and parameters against 32, builds ordinary arrays (no packet), and calls
`exec_params`. A nil result becomes `err` with the connection's message. A parse failure in `connect` returns the
helper's diagnostic as `err`, as `open` does today (`postgres.obx:385`). The wrappers check `ntuples`, `nfields` and
the row and column index before `is_null`, `length`, `value` or `ftype`: today's adapter returns -1 or 0 for these
(`PQgetisnull(res,9,0)=1`, `PQftype` 0), so bounds are obo's job (review 2).

### 5.3 libpq spec fragment

```json
{"format": "cinterop-proto/2", "module": "libpq", "platform": "x86_64-linux-gnu",
 "abi": "sysv-amd64", "libs": ["-l:libpq.so.5"], "signature_check": "header",
 "types": [{"name": "PGconn", "obo": "Connection", "release": "PQfinish", "release_result": "void"},
           {"name": "PGresult", "obo": "Res", "release": "PQclear", "release_result": "void"}],
 "functions": [
   {"name": "exec_params", "symbol": "PQexecParams",
    "args": ["borrow_mut<PGconn>@0", "borrow_cstr@1", "ints@2:len", "ints@3:u32",
             "nil_bytes_list@2", "ints@4:i32", "i32@5"],
    "ret": "own_opt<PGresult>", "retains": "none"}]}
```

The C order (conn, command, nParams, paramTypes, paramValues, paramLengths, paramFormats, resultFormat) matches `args`.
`ints@2:len` and `nil_bytes_list@2` both read list 2. The tool checks that each list is used by exactly one list kind and
every argument is used.

### 5.4 What is deleted and what remains

**Kept:** `toolkit/cres.c` and `cres.h` (205 and 60 lines) stay for the packages in 7.2 (D7). v2 stops using them for libpq,
SQLite and db/postgres only. **Deleted:** `libpq_adapter.c` and `.h` (417 and 40 lines), except the conninfo check. **Kept:**
`libpq_decls.h` (91 lines, the manual declaration header used in header mode) and `tests/fake_libpq.c` (363, ABI kept; the counters become release and census
checks). **Rewritten:** `examples/libpq.obx` (300) and `libpq.cbind` (356). The conninfo check (`conninfo_has_timeout`,
`libpq_adapter.c:89`) is the one helper, about 30 lines in `toolkit/libpq_conninfo.c`. `PQconninfoParse` returns a struct array
that must be walked and freed, so it stays in C. D13 may remove the helper.

### 5.5 SQL template parameters

`Sql` parameters map to OIDs as before: Int 20, Bool 16, Str 25, Decimal 1700, Bytes 17 in binary format; a NUL is refused; `Float` is rejected (D10). Text is never interpolated.

### 5.6 db/postgres: public surface and gaps

`postgres/postgres.obx` (694 lines), `postgres/postgres_adapter.c` (291) and the two `.cbind` files are the second libpq
consumer, and the libpq example did not cover them. Their public surface maps as follows (stage (g)):

| postgres.obx | v2 | Note |
|---|---|---|
| `open`, `status`, `is_open`, `error_message`, `close` | `connect`, `status`, `error_message`, `Conn.close` | conninfo diagnostic kept |
| `execute`, `execute_binary`, `query` | `exec` (obo arrays, typed OIDs) | packet builder and C parser removed |
| `Rows.status`, `is_open`, `row_count`, `column_count`, `is_null`, `length`, `value`, `text`, `bytes` | the same, through 5.2 checks | `is_null` and `length` checked before use |
| `Rows.column_name`, `column_type_oid`, `column_type_modifier`, `decimal_type` | `fname`, `ftype`, `fmod` | `fmod` returns `i32` (may be -1) |
| `Rows.decimal`, `Rows.error_message`, `Rows.close` | obo decimal path, `result_error`, `close` | |
| `make_empty_rows(status)` | `make_empty` (native, no connection, non-NULL) | signature unchanged |
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
4. **Result wrapper.** `Rows` keeps its public `fmt` (`postgres.obx:39`, `445`, `595`). Its `h` is a non-optional `Res`, closed by
   a zero word. A SQL error is `ok(Rows)` with `PGRES_FATAL_ERROR`, read before close.

---

## 6. Worked example 2: SQLite

### 6.1 Functions and kinds

Scope: the subset that `db/sqlite/sqlite_adapter.c` calls, declared in `sqlite_decls.h` (81 lines). New declarations:
`sqlite3_close_v2`, `sqlite3_next_stmt`, `sqlite3_errcode`.

| SQLite function | Kinds | Obo wrapper |
|---|---|---|
| `sqlite3_open_v2(path, &db, flags, NULL)` | `borrow_cstr@0`, `out_own<Db>`, `i32@1`, `null`; ret `i32` | `open` (6.3) |
| `sqlite3_close_v2` | release of `Db` (spec `"release": "sqlite3_close_v2"`, `"discard"`; rc ignored) | generated `close` |
| `sqlite3_next_stmt(db, NULL)` | `borrow_mut<Db>@0`, `null`; ret `nonzero` | `has_live_statements` (informational) |
| `sqlite3_prepare_v2(db, sql, n, &stmt, &tail)` | `borrow_mut<Db>@0`, `borrow_bytes@1`, `out_own<Stmt>`, `out_offset@1`; ret `i32` | `prepare` (6.3) |
| `sqlite3_finalize` | release of `Stmt` (spec `"release": "sqlite3_finalize"`, `"discard"`) | generated `close` |
| `sqlite3_step`, `sqlite3_reset` | `borrow_mut<Stmt>@0`; ret `i32` | `step`, `reset` |
| `sqlite3_bind_null`, `sqlite3_bind_int64` | `borrow_mut<Stmt>@0`, `i32@1`, `i64@2` for int | `bind_null`, `bind_int` |
| `sqlite3_bind_text`, `sqlite3_bind_blob` | `borrow_mut<Stmt>@0`, `i32@1`, `borrow_bytes@2`, `const:-1` | `bind_text`, `bind_blob` |
| `sqlite3_bind_parameter_count`, `sqlite3_column_count` | `borrow_mut<Stmt>@0`; ret `i32` | `param_count`, `column_count` |
| `sqlite3_column_type` | `borrow_mut<Stmt>@0`, `i32@1`; ret `i32` | `column_type` |
| `sqlite3_column_int64` | `borrow_mut<Stmt>@0`, `i32@1`; ret `i64` | `column_int` |
| `sqlite3_column_text` with `_bytes` | `borrow_mut<Stmt>@0`, `i32@1`; ret `cbytes`, length `sqlite3_column_bytes`, cap 16 MiB | `column_text` |
| `sqlite3_column_blob` with `_bytes` | as above, value `sqlite3_column_blob` | `column_blob` |
| `sqlite3_changes`, `sqlite3_errcode` | `borrow_mut<Db>@0`; ret `i32` | `changes`, `error_code` |
| `sqlite3_errmsg` | `borrow_mut<Db>@0`; ret `cstr_out` | `error_message` |
| `sqlite3_errstr` | `i32`; ret `cstr_out` | `error_string` |
| `sq_stmt_errcode` (trusted C helper, 4 lines) | `borrow_mut<Stmt>@0`; ret `i32` | `stmt_error_code` |

`sq_stmt_errcode` calls `sqlite3_errcode(sqlite3_db_handle(stmt))` inside one native call. The pointer from
`sqlite3_db_handle` never leaves C, so obo holds no pointer and needs no new kind. It is the only C helper added for
SQLite, and it uses generic kinds only. It exists because `column_text` returns NULL both for SQL NULL and for
out-of-memory (6.2, point 2).

The `cbytes` entry names the value function as `symbol` and the length function in `length`:

```json
{"name": "column_text", "symbol": "sqlite3_column_text",
 "args": ["borrow_mut<Stmt>@0", "i32@1"], "ret": "cbytes",
 "length": "sqlite3_column_bytes", "cap": 16777216}
```

### 6.2 The stress points and the review items

**Point 1. Parent and child lifetime (review 9, 16).** The current adapter's invariant is that `sqlite3_close` refuses while
statements are live (`sqlite_adapter.c:229-232`), with `release_stmt` decrementing through `s->owner` (`sqlite_adapter.c:140`).
A `Stmt` in the new design holds only its `sqlite3_stmt*` and no pointer to the connection, so a closed `Db` leaves nothing
dangling in obo. A fixture reproduces the old ordering with the current `release_stmt` shape and runs under ASan. It reports
a heap-use-after-free, which is the test that proves the failure.

The adopted design uses `sqlite3_close_v2`. Its observable semantics are documented and tested:
- closing a `Db` with live statements returns success, so `close` returns `ok(true)`. The `Db` census drops to zero, and the
  handle is a zombie in C;
- an orphaned `Stmt` keeps working (`step` and `column_*` succeed) until it is closed;
- a read lock held by an orphaned `Stmt` makes a write on another connection fail with `SQLITE_BUSY`;
- the current adapter refuses the close instead (`sqlite.obx:215-217`). This is a visible change, and the test records it.

**Blind spot (documented).** The census counts obo handles, not C objects. After `db.close()` with an orphaned `Stmt`, the `Db`
census is zero, but the file lock persists until the last statement is closed. Census zero for `Db` does not mean the database
file is unlocked. The `Stmt` census is the signal: a `Stmt` that is never closed is reported at normal end under `OBO_CHECK=1`
(exit 8). Tests cover both counts.

Rejected alternatives: plain `sqlite3_close` (BUSY while statements are live), and owning statements inside the connection
(changes the API). A generic "child keeps parent alive" rule would need stored references, which obo does not have
(`LANGUAGE.md` section 4), so it is not proposed.

**Point 2. Borrowed results (review 2, 10).** `sqlite3_column_text` and `_blob` pointers last until the next step, reset,
finalize or conversion of that column. `cbytes` calls the value function and then the length function, and copies, all in one
native call, so no pointer outlives it. The ordering rule that remains concerns types: `column_type` must be read before any
accessor, because a conversion makes the type undefined. The wrapper does so (`sqlite.obx:385-394`; `sqlite_adapter.c:486`),
and refuses REAL first (`sqlite.obx:392-393`).

NULL handling: `sqlite3_column_blob` returns NULL for a zero-length BLOB, so NULL with length 0 is empty. `sqlite3_column_text`
returns NULL for SQL NULL and for out-of-memory. After an empty text copy, the wrapper calls `stmt_error_code` and returns `err`
on `SQLITE_NOMEM` (review 10). Column and parameter indexes are checked in obo against `column_count` and `parameter_count`, since
the adapter returns -1 for these, and the probe shows an out-of-range `column_type` returns `SQLITE_NULL` (5) (review 2).

**Point 3. `SQLITE_TRANSIENT` without callbacks.** The destructor `(sqlite3_destructor_type)-1` (`sqlite_decls.h:25-26`) is the
constant `const:-1`. It consumes no obo argument and produces one C word, with no relocation and no callback. SQLite copies the
value before the call returns, so this is the only destructor that fits R1. `SQLITE_STATIC` (0) and function pointers are refused
in destructor positions. Tests: text bound from an array that obo then overwrites before `step` keeps the bound bytes, and a
zero-length blob binds as a BLOB rather than NULL (R5).

**Point 4. Out-parameters and prepare (review 4, 5, 18).** `sqlite3_open_v2` writes `*ppDb` even on failure. `out_own` counts that
handle from the write, so the wrapper closes it on every path (6.3); otherwise the census reports it (R12). The wrapper reads the
message before the close. `sqlite3_prepare_v2` may write a non-NULL `*ppStmt` with an error. That case was not observed in the
probe, and the wrapper handles it anyway (review 18).

The prepare call passes the full length. `nByte` stops at an embedded NUL, so obo rejects NUL before the call (review 4). The tail
check then runs to the full length: `out_offset@1` gives the tail, and obo accepts only spaces, tabs, CR, LF and `;` to the end of the
input. That is the same rule as `only_one_statement` (`sqlite_adapter.c:293-298`), applied in obo. Empty SQL returns `err`, not a
nil `Stmt` (review 13).

**Point 5. Multi-statement exec (review 5, 5b).** `exec` is not the one-statement prepare. It is a loop over the tail:

```
loop while bytes remain:
    prepare the next statement from the tail     // on error: return err; nothing is open
    step until SQLITE_DONE, ignoring rows        // on any step error: close the statement, return err
    close the statement                          // every exit from the body closes it first
```

Every exit from the loop body closes the current `Stmt` first: a step error, a reset or tail error, `break`, and a normal end. So the
census is zero whichever path is taken. The one-statement rule stays for `prepare`. The exec-loop test in section 9 fails a step in the middle of a script and
checks that the `Stmt` census is zero at normal end.

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

type Db = foreign "sqlite3"                    // spec: release sqlite3_close_v2, discard
type Stmt = foreign "sqlite3_stmt"             // spec: release sqlite3_finalize, discard

// The result types open_v2_out {rc: int, db: ?Db} and prepare_v2_out {rc: int, st: ?Stmt, tail: int}
// are generated from the out kinds (3.1); the names follow the `<native>_out` rule.
native fn open_v2(path: &[]byte, flags: int): open_v2_out   // borrow_cstr@0, out_own<Db>, i32@1, null
native fn prepare_v2(db: &Db, sql: &[]byte): prepare_v2_out   // borrow_mut<Db>@0, borrow_bytes@1, out_own<Stmt>, out_offset@1
native fn error_message(db: &Db): []byte
native fn errstr(rc: int): []byte

pub fn open(path: string, flags: int): Result[Db] {
    if len(path) > MAX_PATH { return err("database path is too long", 2) }       // 4 KiB
    if hasNul(path) { return err("database path contains a NUL byte", 2) }
    var pb = encodeString(path)                  // stored first: a reference cannot borrow a temporary (C16)
    var out = open_v2(&pb, flags)                // out.db may be set even when rc != 0
    if out.rc != SQLITE_OK {
        if out.db == nil {
            var es = errstr(out.rc)
            return err(string(es), 1)
        }
        var db = take(out.db)
        var mb = error_message(&db)
        let msg = string(mb)                     // read before close
        db.close()
        return err(msg, 1)
    }
    if out.db == nil { return err("sqlite returned no handle", 1) }
    return ok(take(out.db))                      // the caller defers close
}

pub fn prepare(db: &Db, sql: string): Result[Stmt] {
    if len(sql) > MAX_SQL { return err("SQL text is too large", 2) }             // 1 MiB
    if hasNul(sql) { return err("SQL text contains a NUL byte", 2) }            // nByte stops at NUL
    var sb = encodeString(sql)
    var out = prepare_v2(db, &sb)
    if out.rc != SQLITE_OK {
        var mb = error_message(db)
        let msg = string(mb)
        if out.st != nil {                       // non-NULL on error (review 18)
            var bad = take(out.st)
            bad.close()
        }
        return err(msg, 1)
    }
    if out.st == nil { return err("SQL text is empty", 2) }                     // not a nil Stmt
    var st = take(out.st)
    if !blank_tail(&sb, out.tail) {              // only spaces, tabs, CR, LF, ';' to the end
        st.close()
        return err("prepare takes one statement; use exec for several", 2)
    }
    return ok(st)                                // the caller defers close
}
```

Checks: each path closes an owned handle or returns it, and `take` leaves `out.db` and `out.st` nil. `string(...)` is applied only to
stored `[]byte` locals (C16). The `close()` calls are statements, so their discarded `Result[bool]` is freed at once. `blank_tail`,
`hasNul`, `encodeString` and the constants are ordinary obo helpers, not shown.

---

## 7. Decisions

Decided by the user (2026-11-01, not for review): cleanup is **file-consistent explicit close**. Foreign handles use a borrowing,
deferrable `close` and a leak census. No must-close rule, no consuming defer, no release on drop.

### 7.1 User decisions (decided 2026-11-01)

Each item below was decided by the user with the recommended option. The chosen option is stated for each.

- **D1. Body-less `native fn` plus `type X = foreign "C"`.** Decided (user, 2026-11-01): the native form and the foreign type, one approval for both. They are the only language change in this note. Not chosen: a compiler flag that reads the spec.
- **D4. Exit codes.** Decided (user, 2026-11-01): exit **11** (new) for native contract violations (a NUL past the helper check, a `cbytes` NULL with positive length, an `i32` or `u32` out of range, a list mismatch or above the cap, an `own` NULL); exit **7** (existing) for a foreign handle used after close; exit **8** (existing, generalized) for a foreign handle left open at normal end under `OBO_CHECK=1`; exit **4** (existing) for a census underflow. Not chosen: a new code 13 for foreign leaks. `LANGUAGE.md` section 9 gets the 11 entry and the generalized 8 entry when stage (d) is accepted.
- **D14. SQLite `close_v2` with live statements succeeds.** Decided (user, 2026-11-01): `close` returns `ok(true)` while statements are live, the `Db` census drops, and the file lock persists until the last statement closes (6.2, point 1). Not chosen: plain `sqlite3_close`, which refuses with BUSY, as today.
- **D16. `is_open(&T): bool`.** Decided (user, 2026-11-01): a generated query next to `close`, for wrappers that want a `Result` for a closed handle. It is not a substitute for the thunk check (3.4).
- **D6. `.sym` for foreign types.** Decided (user, 2026-11-01): deferred until the first exported foreign type, with its own version decision.
- **D8. List kinds versus one helper.** Decided (user, 2026-11-01): scoped kinds (`nil_bytes_list`, `ints` with flags, `ints@k:len`). Not chosen: one helper `pq_exec_params_list`.
- **D12. No rename in this change.** Decided (user, 2026-11-01): keep `Connection`, `Rows` and the existing names.
- **D13. Keep the `connect_timeout` default and its helper.** Decided (user, 2026-11-01): keep the default and its helper. Not chosen: drop the default.

### 7.2 Technical defaults (no approval needed unless challenged)

- **D2.** `foreign` is a reserved word, with D1.
- **D5.** Nothing is reported at exit beyond the census at normal end under `OBO_CHECK=1` (D4, code 8; 3.4).
- **D7.** `cres.c` is kept for `sys`, `tls`, `http2`, `zlib`, the buffer example and the skill templates. v2 stops using it for libpq,
  SQLite and db/postgres only (review 14).
- **D9.** Scratch copy for `borrow_cstr`; no reserved terminators.
- **D10.** `Float` is rejected in v1. `Bytes` uses OID 17 with binary format, with no `::bytea` cast.
- **D11.** `cstr_out` returns empty for NULL, per call.
- **D15.** `close` returns `Result[bool]` as `Dir.close` does. A `"void"` or `"discard"` release gives `ok(true)`; `ok(false)` marks a second close.

### 7.3 Future option (not in scope)

Compile-time must-close could later be added to files and foreign handles together. That would be one checker change covering both,
and it would need its own review. This revision does not add it.

---

## 8. Staged plan

Every stage keeps the gates in native design section 12. A compiler-changing stage also runs the gates in native design sections 14.6 and 14.10 and `test_cinterop.py` (self-build fixed point, baseline byte-identical check). Live tests stay opt-in.

**Stage (a): borrowed input and scalar kinds.** Adds `borrow_bytes@k`, `borrow_cstr@k`, `null`, `const:<n>`, `i32`, `u32`, `ints@k` with flags, `nil_bytes_list@k`, and the scratch helpers. Codegen: borrow loads after all argument expressions (3.2). Checker: none beyond the existing `&T` argument rules. Risk: medium. Gate: the existing scalar, startup and fake tests; a `bytes` fixture (empty, 1 MiB, nil string, embedded NULs, NUL rejected in `borrow_cstr`); the `const:-1` recorder; the borrow timing test (section 9); `u32` above `2^31`.

*Stage (a) implemented* (commits `0d6ff91` compiler, `3845739` tool, thunks, host helpers and tests; `experimental/cinterop/README.md` documents the kinds). The compiler takes the descriptor as a string literal after the symbol, because it cannot see the kinds at the call site before the typed sidecar of stage (c); the sidecar carries it in the argtypes field of the v1 record, so `cinterop-relocs 1` is unchanged here and stage (c) bumps it. A `null` or `const:<n>` slot has no obo argument to imply its C type, so the spec gives it in `ctypes` and the signature check compares that type with the header exactly; the other slots keep their fixed types, so a mismatch on them is still refused. A `ctypes` entry must be exactly one C type (a fixed integer type, a pointer, or a function pointer whose parameters are pointer or integer types), since a struct by value would pass the check and then be passed in a register, and a comma or a declarator name would change what the signature check compares. A `const:<n>` value must fit its integer type by width and sign. Element counts are capped only at `INT_MAX`; a per-library cap belongs to the wrapper (R10). The `obo_ci_*` helpers have hidden visibility, which prevents dynamic-symbol clashes only: a second static object defining an `obo_ci_*` name is a duplicate-symbol link error. Argument indexes are below 16. The borrow timing rule passes `f(&a, grow(&a))` with the grown array, and the checker accepts the call. Gates: `make obo` and the stage 2/3 fixed point, byte-identical output on the 134 non-kinds programs, `run_fork` 1809 passed, `cinterop tests ok`, `db adapter tests ok`. Not run: an ASan build over the obo image; scratch is checked by the `obo_ci_scratch_live()` counter instead.

**Stage (b): copy-out and typed returns.** Adds `cbytes` returning `?[]byte` with caps, `cstr_out`, `void`, `nonzero`. Checker: none. Risk: medium. Gate: fake `PQgetvalue` for empty, NUL-containing, NULL-with-length (traps with D4) and over-cap (nil, then `err`); `cstr_out` stops at NUL.

*Stage (b) implemented together with stage (c)* (one review, as the coordinator directed). `void`, `u32`, `nonzero`, `cbytes` and `cstr_out` are added; `i32`/`i64` stay. `obo_ci_copy_out` and `obo_ci_cstr_out` live in `host.c` with hidden visibility and allocate through the existing runtime jump-table slot 3 (`rt_alloc_array`), reached by an address in `host_abi.h`; no slot was added to `entry.S`, so the baseline runtime bytes are unchanged. The array layout is `Array {len, cap, data, esize}` with elements at `data + 8`, as `runtime.c` has it. A copy-out array is an ordinary obo allocation (`live_owned`), so `OBO_CHECK=1` leak checking applies; the test runs the whole stage (b) app under it. `cbytes` calls the value function, keeps the pointer in the thunk frame, calls the length function with the same expanded arguments, then copies; every call resets `rsp` to the fixed frame, so the second call has the same alignment and stack words. The fixture covers both stack parities for both calls: 9 C parameters (three stack words, an odd count, with the alignment pad) and 8 C parameters (two stack words), with the length depending on the last argument in each case. `cbytes` never calls `strlen`; empty/NULL-0 gives an empty array, NULL with a positive length exits 11, and over `cap` returns nil for the wrapper to turn into `err`. `cstr_out` copies to the first NUL and exits 11 if there is none within 16 MiB. `ret_ctype` is required for `cbytes`/`cstr_out`/`nonzero` and is the exact type in the generated function-pointer assignment; `cbytes` also checks its length function (same parameter types, `int` return). Gate: `make obo`, stage 2/3 fixed point, byte-identical images to the seed on all 125 of the 134 `tests/run`/`examples` programs that build standalone with both compilers (`diff=0`; the other nine fail standalone with either compiler), `cinterop tests ok`, `db adapter tests ok`.

**Stage (c): typed declarations and sidecar v2 (no foreign types).** Adds the body-less `native fn` form (D1, without `foreign`), the kind table, `cinterop-relocs 2`, `cinterop-proto/2` without `types`, and `signature_check` (6.2, point 6). Checker: parse and type-check body-less natives only. Risk: medium. Gate: sidecar tamper refused; a kind mismatch refused by `--native-relocs`; existing int-only natives byte-identical.

*Stage (c) implemented* (with stage (b)). `native` is contextual: it is recognised only at the start of a top-level declaration, so existing identifiers named `native` still compile. A native declaration takes no body or receiver, rejects `pub`, `main`, duplicates, and a later normal definition (and the reverse) with specific messages; supported parameter types are `int`, `string`, `&[]byte`, `&[]int`, `&[]?[]byte` and supported returns are unit, `int`, `[]byte`, `?[]byte`. A call reuses the `nativeKindCall` evaluation, borrow-binding, ownership and conservative-effect machinery through a shared `nativeCallCore`; the compiler builds a synthetic kind list only to drive the same word layout, and records a shape per obo argument. The tool compares each shape with the spec (`int` with `i64`/`i32`/`u32`, `bytes` with `borrow_bytes`/`borrow_cstr`, `ints` with `ints:i32`/`ints:u32`, `byteslist` with `nil_bytes_list`), expands C-only `null`/`const`/`ints:len` slots in the thunk, and rewrites the reloc from the obo name to the spec symbol. Sidecar v2 is `off name nargs mode shapes ret-shape effects`; a v1 reader remains and is tested. A `/2` spec requires an explicit `signature_check` (`header` needs headers, `declarations-only` needs declarations); `/1` keeps its old semantics. `--check` parses and type-checks declarations but refuses a native call, as for `__cinterop`. Not done here: `own`/`own_opt`/`out_*`/`borrow<T>`, `foreign`, generated wrappers, census. Gate: the native declaration negatives, sidecar v2 tamper refusals, spec/declaration shape mismatches, a v1 sidecar, `--check`, and the same byte-identity and fixed-point gates as stage (b).

**Stage (d): foreign handles, borrowing close and census.** Adds the `foreign` record kind with one private word (3.4), `own`, `own_opt`, `out_own`, `borrow<T>`, the generated `close` and `is_open`, the thunk use-after-close check (exit 7), and per-type runtime counters (exit 8 under `OBO_CHECK=1`, exit 4 on underflow, D4). The test hook `OBO_CENSUS_FAULT=underflow` is gated like `OBO_IO_FAULT` (cleared unless `OBO_CHECK=1`). Runtime: the foreign word is kept out of both offset lists (review 6); the counter report sits in `rt_finish` between the fd and object checks (3.4). Checker: a `foreign` record kind, private-field checks for the word, and the existing defer and move rules. No ownership-checker rule. Risk: medium to high (the census and thunk check are new). Gate: the foreign handle suite and the exit table (section 9); the SQLite UAF reproduction; ASan on the fixtures.

*Stage (d) implemented* (branch `cinterop-v2-d`). `foreign` is a reserved word; `type X = foreign "C"` builds a normal record with one private `int` word at offset 0 and a descriptor with no owned or string offsets, so `rt_free` frees the record only. A foreign value comes only from `own`/`own_opt`; `new X`, field access to the word, and int/string conversions are refused. `own` and `own_opt` are spec return kinds (`own<C>`/`own_opt<C>`); the parameter kinds are `borrow<C>@k` (`const C *`) and `borrow_mut<C>@k` (`C *`); the compiler shapes are `own:X`, `own_opt:X`, `foreign:X` (the obo name, mapped through the spec `types` to the C name). The thunk calls `obo_ci_foreign_word` for a borrow (exit 7 for nil or a zero word) and returns the raw word for an own result; the compiler then calls `obo_ci_foreign_own(id, raw, desc, optional)` to allocate the record and count it. `close`/`is_open` are implemented pragmatically as internal builtins `__foreignClose`/`__foreignIsOpen`, with the wrapper declaring the exported methods (semantics and public API are unchanged; this avoids compiler-generated method bodies). `close` zeroes the word before the release, decrements the census and returns a fully built `Result[bool]` cell; a `release_result: "int"` error becomes `err("foreign release failed", code)` and the word stays zero. `close` is deliberately not conservatively summarized, so a field receiver is allowed (`is_open` is an inline word load either way). A `pub type X = foreign` exports as an ordinary opaque record with one `priv` placeholder field (the reader already supports this; no `.sym` format bump and no C name for importers). The sidecar starts with `foreign <id> <obo> <c>` lines; internal helpers are `host` relocations, and the tool generates their thunks and the per-binding `foreign_table.c`. The census lives in `host.c`; the runtime gets a hosted-only appended jump-table slot 94 (`rt_set_foreign_census`) after the last existing slot, and `rt_finish` calls it between the fd and object censuses under `OBO_CHECK=1` (exit 8, exit 4 on underflow; `OBO_CENSUS_FAULT=underflow` is inert without `OBO_CHECK=1`). The baseline runtime and entry table are unchanged. `out_own` and `out_offset` are **deferred to stage (f)**, where SQLite needs them. Gate: the foreign handle suite (including borrow/borrow_mut constness), the exit table, `OBO_CHECK=1` runs, ASan/UBSan over the C fixture (`foreign_fixture_test.c`), the same fixed point and byte-identity checks, `test_cinterop.py` and `test_db_adapters.py`; not run: an ASan build over the obo image itself or the SQLite UAF reproduction (stage (f)).

**Stage (e): port libpq.** Rewrites `libpq.obx` and `libpq.cbind` to 5.2 and 5.3, deletes `libpq_adapter.c` and `.h` except the conninfo helper, and keeps `cres.c`. Risk: low to medium. Gate: the cinterop suite with fake release counts; bounds tests (`PQgetisnull(res,9,0)`, `PQftype` out of range); the caps in C15; a `Rows` dropped without `close` reported by the census; a live smoke test on a user-supplied local DSN, opt-in.

*Stage (e) implemented* (branch `cinterop-v2-e`). `libpq.obx` uses `pub type Connection = foreign "PGconn"` and `pub type Res = foreign "PGresult"` (the public `Connection` name is kept per D12), with exported `close`/`is_open` methods over the stage (d) builtins. `Rows = ref record { h: Res; pub fmt: int }` owns one `Res`. The native surface is `PQconnectdb`, `PQstatus`, `PQerrorMessage`, `PQexecParams`, `PQresultStatus`, `PQntuples`, `PQnfields`, `PQgetisnull`, `PQgetlength`, `PQgetvalue` (`cbytes`, length `PQgetlength`, cap 16 MiB), `PQfname`, `PQftype` (`u32`), `PQfmod` (`i32`), `PQresultErrorMessage`, `PQmakeEmptyPGresult` (`null`/`i32@0`/`own<PGresult>`), `PQlibVersion`, and the one `pq_conninfo_has_timeout` helper; releases are `PQfinish`/`PQclear` (void). `toolkit/libpq_conninfo.c`/`.h` implement the conninfo probe with `PQconninfoParse`/`PQconninfoFree` and keep the copied diagnostic; the wrapper appends `connect_timeout=5` when absent. Limits move into obo: DSN 16 MiB, SQL 1 MiB, 32 parameters, each value 16 MiB, and NUL checks for DSN/SQL/text values. A text-format parameter is passed as a `len+1` copy truncated back to `len`, because libpq reads text `paramValues[]` with `strlen` and ignores `paramLengths`; binary values keep their explicit length and may contain NUL. `toolkit/libpq_adapter.c`/`.h` and `cres` are retained because `db/postgres` still uses them until stage (g); libpq no longer lists them in its spec or wrapper sources, and `buffer.obx` is no longer a wrapper source. Two pragmatic deviations are recorded: `Rows.value` keeps `Result[[]byte]` and distinguishes SQL NULL (`err("SQL NULL", 1)`) from a copy-out over the cap (`err("value is larger than the 16 MiB limit", 2)`) rather than returning `Result[?[]byte]`, and a foreign borrow argument no longer triggers the conservative deep-reference refusal (it passes the C word, not the enclosing obo record), which is what lets `&r.h` reach the libpq accessors; array borrows still get the refusal. Closed/out-of-range integer accessors use sentinels (`status`/`ntuples`/`nfields` -1, `is_null` false, `length` -1, `ftype` 0, `fmod` -1); the `Result` accessors return an explicit error. `PQmakeEmptyPGresult` returning NULL is now an `own` exit 11 (visible change; the fake test forces it). The `live_*` counters are removed; the foreign-handle census replaces them. `Rows.fmt` is 0 for text and 1 for binary results. The tests also cover a dropped `Rows` (`Res` census exit 8 with no `PQclear`), a SQL-NULL `value` error, an over-cap copy-out, closed-Rows accessors, NUL rejection, an explicit `connect_timeout` not being duplicated, and an exactly-capacity text value on the live `SELECT $1::text` path. Gate: the cinterop suite (fake and real probes, release counts, census, bounds, use-after-close, limits), `test_db_adapters.py`, the fixed point and byte-identity checks, and the opt-in live PostgreSQL SELECT-only smoke test.

**Stage (f): port SQLite.** Adopts `sqlite3_close_v2` (D14), the `nonzero` probe and `sq_stmt_errcode`. Rewrites `sqlite.obx` and `sqlite.cbind` to 6.1 and 6.3, implements `exec` as the loop in 6.2 point 5, and deletes `sqlite_adapter.c` and `.h`. Risk: medium. Gate: the real 3.45.1 tests, the new fake, the UAF reproduction (fails in the old shape, passes in the new), the BUSY, zombie and census tests, and the `sqlite_memory`, `sqlite_file` and `sqlite_decimal` cases in `test_db_adapters.py` unchanged except for the documented close change.

*Stage (f) implemented* (branch `cinterop-v2-f`). Declaration syntax adds `out name: T` for an `out_own<T>` and `out name: int from input` for `out_offset@k`; out parameters consume no call argument, follow the inputs, allow at most one of each form, and the return type is the generated `<native>_out` record (`rc: int` first, then the out fields in declaration order). The record is built directly, never registered in module globals or `.sym`; a hidden non-owned frame word holds it, the C result is stored in `rc` before any helper call, and every `out_own` write is wrapped in a counted foreign record before the call returns an `outResult` item. A bare out-call statement is refused. The sidecar keeps `cinterop-relocs 2` and adds optional `outrec` lines; old readers reject them and the new reader accepts sidecars without them. `release_result: "discard"` is implemented for `sqlite3_close_v2`/`sqlite3_finalize`, and a function-pointer `const` slot accepts only `-1`. SQLite is ported to v2: `sqlite.obx`/`sqlite.cbind` use `Connection`/`Stmt` foreign handles, `open_v2`/`prepare_v2` out records, `sq_stmt_errcode` in `db/sqlite/sq_stmt_errcode.c`, a `nonzero` `has_live`, `cbytes` text/blob reads, and `exec` as the close-on-every-exit prepare/step/finalize loop; `sqlite_adapter.c/.h` are deleted and the `live_*` counters are gone. `sqlite_decls.h` keeps ABI-identical declarations-only spellings (`const void *`, `long`) and a real-library ABI test covers every function. Gate: the out fixture (success, error non-NULL/NULL out_own closed once, interleaved/trailing slots, seven C parameters, every tail case, tamper/negative cases), byte identity (`125 identical / 9 standalone failures / 0 mismatch`), fixed point, `run_fork` 1809, `test_cinterop.py`, `test_db_adapters.py`, the real 3.45.1 suite, the fake suite, the ASan UAF fixture (old fails, new passes), the ABI test, and `OBO_CHECK=1` runs with zero census. One deviation: an out_offset-only record stored in a `var` is freed at scope end and exits 0, not the object-census exit 5 sketched earlier; the only discard paths are refused at compile time, so that expectation is unreachable and the test asserts the no-census result.

**Stage (g): port db/postgres.** Maps the surface in 5.6 onto `connect`, `exec` and `Rows`, removes the `live_*` counters and the packet builder, and moves the decimal grammar and cap into obo. Risk: medium. Gate: the postgres cases in `test_db_adapters.py` (fake and decimal fake), the decimal negative table against both checks, the `postgres_adapter_test.c` cases ported, and the opt-in live smoke test (SELECT-only, `CINTEROP_TEST_DSN`).

**Stage (h): skill and templates.** Updates `skill/SKILL.md` and the project-skill templates for the explicit `close`, the census, the kinds and the `cres` policy (7.2). zlib stays on its current adapter, outside the v2 gates (review 7). Risk: low. Gate: a generated project builds; existing `cres` tests pass.

Exposure: (a) none beyond the existing `&T` argument rules; (b) none; (c) parse and type-check of body-less natives; (d) a `foreign` record kind, private-field checks and per-type runtime counters, with no ownership-checker rule; (e) to (h) none.
## 9. Test plan

**Positive.** `borrow_bytes` over empty, 1-byte, 1 MiB and string inputs (a nil string gets length 0 and a non-NULL pointer; embedded NULs kept). `borrow_cstr` over ASCII, empty and UTF-8. `cbytes` over 0, 1, N-with-NULs and NULL-with-length-0. `cstr_out` stopping at NUL. `ints` with `:len`, `:u32` and `:i32`, including 32 entries. Foreign connect, exec, read, `defer c.close()`, a double close (`ok(false)`), `is_open` before and after close, a field handle closed through a `mut` receiver, a handle closed through `^T` (`LANGUAGE.md:502`), and an array of handles closed element by element.

**Borrow timing (review 12).** `f(&a, grow(&a))`: where the checker accepts the call, the native sees the grown array; where the existing overlap rule refuses it, the test asserts the refusal.

**Foreign handle suite (stage (d)).**
`out_own` and `out_offset` are implemented in stage (f), where SQLite needs them; the stage (d) tests
cover own/own_opt/borrow/borrow_mut.

- use after close: a `borrow<T>`/`borrow_mut<T>` native on a closed handle exits 7 with its message and makes no C call; `alive(h)` stays true (record liveness) while `is_open` is false;
- double close: `ok(false)` and exactly one C release call;
- census underflow, forced by `OBO_CENSUS_FAULT=underflow` (inert unless `OBO_CHECK=1`): exit 4;
- census under `OBO_CHECK=1`: a handle never closed exits 8 at normal end and names the type; the same for a handle dropped at scope end, overwritten, or dropped with its enclosing record, each with no C release call;
- (stage (f), with `out_own`) an `out_own` written with an error return and not closed on the error
  path: exit 8; an out record stored in a `var` is freed at scope end, and an out record with a live
  `out_own` reports `foreign handles leaked: T 1` at normal end;
- a release returning an error (`"release_result": "int"`): `err` with the code, and the handle is zero afterwards;
- exit paths (3.4 table): `exit(n)` with a leaked fd and a live handle exits `n` with no message; a trap with a live handle exits with the trap code and no message; the hosted build matches;
- without `OBO_CHECK=1`: no census output and no exit 8 for the same programs.

**Exec loop (review 5).** A script whose second statement fails at step, and a `break` out of the loop, each leave the `Stmt` census at zero at normal end.

**Review regression.** The UAF reproduction of the old `release_stmt` shape (6.2, point 1) must fail under ASan; the `close_v2` shape must pass. The orphaned-statement BUSY, zombie and census tests (6.2, point 1) must pass.

**Stage (f) implemented tests.** `tests/out_fixture.c/.h` and `out_wrap.obx` cover out_own success, a failure with a non-NULL handle closed exactly once, a NULL write, interleaved (`open_v2` shape) and trailing (`prepare_v2` shape) slots, seven C parameters (out slot on the stack), every `out_offset` case (exact, 0, len, NULL, before-base, past-end), and sidecar `outrec` assertions. The tool suite mutates the sidecar (missing/wrong `outrec`, wrong record name, wrong order, `rc` not first, duplicate field, unknown own type, wrong offset target) and refuses out in `__cinterop`, non-scalar out results, two out_own/out_offset slots, and `const:0` in a function-pointer slot. SQLite adds `sqlite_v2_app.obx` (open/close, bind int/text/blob/NULL, zero-length BLOB, NUL rules, `changes`, multi-statement `exec`, empty SQL, caps, `close_v2` orphan + `SQLITE_BUSY` + finalize, `sq_stmt_errcode`, `has_live`, `const:-1` overwrite, `OBO_CHECK=1` zero census), `sqlite_fake_app.obx` with `fake_sqlite.c/.h` (failed open/prepare with non-NULL writes, reset after a failed step, `NOMEM`, destructor `-1`, zombies), `sqlite_uaf_fixture.c` under ASan (old shape fails, new passes), and `real_sqlite_abi_test.c` over every declared function.

**SQLite, real library (3.45.1).** Open and close a file database. Prepare, bind int, text, blob and NULL. Step and reset with rebinding. `column_text` and `column_blob` with embedded NULs and a zero-length BLOB (BLOB type, not nil). A REAL column refused before conversion. `changes`. Multi-statement `exec`. Empty SQL refused. The `const:-1` overwrite test. The close-with-live-statement test (6.2, point 1).

**SQLite, fake (`tests/fake_sqlite.c`, new).** Knobs: `open_v2` fails with a non-NULL `*ppDb` (closed once by the wrapper); `prepare` fails with a non-NULL `*ppStmt` (closed once, error text kept); `reset` returns the step error (the wrapper keeps the step result); `column_text` returns NULL with `SQLITE_NOMEM` (the wrapper returns `err`); `close_v2` zombie counting; the destructor argument is recorded and copied for -1 only; prepare with an embedded NUL and with a trailing statement (refused).

**Runtime negatives.** An embedded NUL in `borrow_cstr` (wrapper `err`; a direct call traps with D4). `prepare` with two statements, or with trailing text other than whitespace or `;` (refused). An out-of-range row, column or parameter index (`err`, not NULL). A `cbytes` over its cap (`err`). `signature_check: "header"` without a header path (refused). A sidecar tamper (refused).

**Compile-time negatives.** A `borrow` and a mutation of one place in one call, where the existing overlap rule applies; a field write without `mut` or an owned local.

**Sanitizers.** ASan and LSan over the fixtures.
## 10. Review findings addressed

| Review item | Where addressed |
|---|---|
| 1. `sqlite3_reset` after failed step | 6.2 (reset) |
| 2. Out-of-range row/col silently accepted | 5.2, 6.2 point 2, R10; tests in section 9 |
| 3. `cbytes` over cap must be an error | 3.3 (nil, then `err`); 6.1 caps |
| 4. `prepare` with `nByte` and embedded NUL | 6.2 point 4; 6.3 |
| 5. exec versus one-statement prepare | 6.2 point 5 (close on every exit) |
| 6. Foreign word in offset lists | 3.4 representation (in neither list); C5; R7 |
| 7. zlib `compress2` output buffer | zlib removed from v2 gates (stage (h)) |
| 8. db/postgres stage and surface | 5.6; stage (g) |
| 9. Reproduce the release_stmt UAF | 6.2 point 1; section 9 |
| 10. `column_text` NULL on OOM needs a db handle | 6.1 (`sq_stmt_errcode`); 6.2 point 2 |
| 11. Field writes need `mut &T` | 3.4; 5.2; 6.3 |
| 12. Borrow timing | 3.2 (codegen rule); R2; section 9 |
| 13. Keep existing limits | C15; 5.2; 6.2 limits; 6.3 |
| 14. `cres` contradiction | 5.4, 7.2 (D7); stage (h) |
| 15. Globals | 3.4: allowed, counted at normal end |
| 16. D14 close_v2 and its semantics | 6.2 point 1 (with the blind spot); section 9 |
| 17. Stale `null` paramTypes note | 5.6 (withdrawn) |
| 18. Non-NULL `*ppStmt` on error | 6.2 point 4; 6.3 |
| 19. `cbytes` OOM exit 2 | 3.3 |
| 20. `release_now` moot | 3.4: `close` is the explicit release |

---

## 11. Non-goals and follow-up

- No callbacks, C variadics, struct-by-value, `long double`, floats or decimals across the native ABI (native design section 2); no
  mutable borrows, borrowed foreign returns, raw pointers or `void*`.
- No release at `exit`, a trap or `atexit` (3.4).
- No SQL driver: libpq, SQLite and db/postgres stay bounded adapters (native design section 8.5).
- No universal generator (native design section 1.1).
- No change to `LANGUAGE.md`, `obo/design/README.md`, `obo/FORK.md` or the seed in this note. D1 needs its own review. The exit code
  entries (D4) and the census need review when stage (d) is proposed.
- No compile-time must-close (7.3 is a future option only).
- No TLS, IFUNC, threads, PIE or ASLR, and no stable `cinterop/1` or `cabi1`.
- **Follow-up (out of scope).** The systems stdlib's leak census for fds and sockets (`systems-stdlib-design.md` section 4.2) is unchanged.
