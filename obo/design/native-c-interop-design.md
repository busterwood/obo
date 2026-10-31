# Native C interop (`cinterop`) — design note

Status: **REVISE response plus explicit-close resource-adapter checkpoint
pending independent rereview.** Date: 2026-10-31.
Baseline: released `main` at `a3e5b1f` (numeric floats, compiler `0.10`,
`.sym` format 6, ABI 1), merged into this feature. The independent architecture
review of prototype `a29f35d` returned REVISE; the scoped B1/B2, high and
practical-medium repairs are recorded in section 14, followed by the
explicit-close libpq adapter checkpoint. This remains an experimental,
trusted-build prototype: not production, not a public API, not a stable format,
and not permission to merge, push or promote a seed.

The prototype lives outside the public compiler surface under
`obo/experimental/cinterop/`. It is labelled experimental. Nothing in this note
claims that the prototype is complete; section 14 records what was actually
built and what remains blocked.

---

## 1. Problem and goals

A user wants to use a real system C library from ordinary obo code without
editing the compiler once per library. The motivating target is libpq in the
same process (no database helper daemon, no SQL evaluation by the compiler).
The user supplies C headers plus an annotated function specification; a tool
emits:

- a generated C/assembly shim and native object;
- an ordinary obo module and its ordinary `.sym`;
- a reproducible manifest with hashes, ABI and platform data.

The application then writes a normal obo import and calls ordinary obo
functions. The final program must be a real system-linked ELF with the native
object linked into it, not a runtime child process and not a compiler-time
database call.

Goals:

1. One generic compiler/build seam, never a per-library compiler edit.
2. The application-facing interface is ordinary obo source and ordinary obo
   types; no user-visible raw C pointers.
3. The C compiler and system linker do the actual C compilation and ELF link.
   The obo compiler must not grow a half-written linker.
4. Explicit, reviewable ownership/effect contracts. C is trusted, but the
   generated binding must not pretend that header types alone prove safety.
5. Reproducible, validated artifacts: spec, shim, object, manifest and wrapper
   hashes are checked before compilation/execution; wrong arity, stale
   manifests and platform mismatches fail before native code runs.
6. A useful scalar probe first, then real libpq availability/result ownership,
   then an explicit-close connection/query/result adapter. The live database
   smoke test is opt-in and locally authorized; no automatic driver.
7. A binding is a maintained obo package with ordinary imports, not a
   generator run on every application build.

### 1.1 Approved steering decision (2026-10-31)

The user-approved direction is **a small interop toolkit plus an agent skill**,
not a universal configurable C-binding generator. The earlier paragraph in
section 1 describing a tool that emits bindings from headers plus a spec is
retained as the historical proposal; it is **refined/superseded by this
decision**, not erased.

**Approved:**

- The generic native compiler/link/ABI infrastructure is essential: the
  mandatory compiler-visible native contract, the system-linker bridge, and
  explicit foreign ownership. These must not be replaced by trusting an LLM.
- A deterministic toolkit enforces signatures, effects, link inputs, artifact
  checks and ABI safety.
- Adapting each C API is done by an LLM agent following a documented skill and
  templates, not by adding a per-library compiler branch.
- Per-library output is a maintained obo package with ordinary imports, not a
  generator invocation on every application build.

**Implemented in this checkpoint:**

- The project skill and small real templates live under
  `obo/experimental/cinterop/skill/` (`SKILL.md`, `templates/`). They guide an
  agent to inspect C headers and ownership, design an idiomatic obo module,
  write C adapters from templates, declare bindings/effects/link dependencies,
  and test ABI, lifetime, errors and integration.
- The templates are real: the `library.cbind`/`wrapper.obx`/`adapter.c` set
  builds and runs with the same toolkit, and the libpq package is the second
  worked example.

**Still separate:**

- Skill packaging, global installation and a stable template catalog require a
  separate review and approval. The repository copy is a portable project
  skill, not a global install.

The existing `cinterop.py` is an experimental proof fixture/convenience. It
must not grow into a wide header parser or an option-rich universal binding
generator. Current work prioritises hardening the generic boundary and the
reusable build/validation templates. A concrete future generator is considered
only if repeated real bindings show a genuine automation benefit.

This decision does not require any compiler change per library; it preserves
the one-time generic seam. It also does not cancel the B1/B2/H1..H4/M1..M6
repairs in section 14, or approve a full production driver, resource
finalizers or skill implementation.

## 2. Non-goals

- No stable public C interop API in this proposal.
- No universal configurable C-binding generator: per-library adaptation is
  toolkit-plus-agent-skill work. The skill is implemented as a portable project
  skill in this checkpoint; it is not installed globally.
- No arbitrary C header parsing, C++ interop, templates or macros.
- No general FFI for variadic C functions, callbacks, `struct` by value, TLS,
  C `long double`/floats, or decimal values in v1.
- No automatic ownership inference from pointer types or headers.
- No user-visible `void*`/integer pointer type, no `Any`, no unsafe pointer
  escape hatch, no standard value-record redesign.
- No new language generics or user-defined variadics.
- No replacement of obo's ownership checker with "trust me" annotations.
- No compiler-time execution of SQL or arbitrary native code. Compiler-time
  trust is limited to invoking the normal C toolchain to compile and link
  declared trusted C.
- No PIE/ASLR redesign of the obo image, no runtime address lookup cheat, no
  `dlopen`/`dlsym` scheme.
- No production-quality libpq driver. The explicit-close adapter executes a
  bounded connection/query/result/error subset against a local database only
  when the user opts in; it is not a general driver, ORM, schema tool or
  automatic-ownership layer.
- No version/format number taken from the floats/decimal roadmap. Floats are
  next at `0.10`/format 6/ABI 1; decimal is `0.11`/format 6. Native interop
  assigns no such version here.

## 3. Current constraints verified in this tree

These are facts read from the baseline source, not assumptions:

1. **The compiler emits a fixed `ET_EXEC`, not an object.** `elfImage`
   (`obo/src/obo.obo:10529-10580`) writes headers and five fixed load segments:
   obo code at `CODE_BASE = 0x401000`, rodata at `0x08000000`, globals at
   `0x0C000000`, runtime text at `0x10000000` and runtime data at `0x20000000`
   (`obo/src/obo.obo:1519-1524`). There are no sections, symbol table,
   relocations or standard object-file output.
2. **Calls inside the image are already absolute/fixed.** The ordinary obo
   convention pushes arguments and emits relative calls; absolute addresses
   are baked into code and rodata. There is currently no undefined symbol or
   link-time relocation for an arbitrary C function.
3. **The runtime is a fixed 40-slot jump table.** `entry.S:1-59` defines slots
   0..40 at `0x10000000 + 8*k`; the compiler's `rtCall`
   (`obo/src/obo.obo:2770-2829`) uses the System V register ABI for at most four
   arguments. Adding a C function is not a matter of appending to this table:
   there is no table-driven lookup for an arbitrary symbol, and repurposing an
   unrelated builtin would be a fake FFI.
4. **Ordinary obo calls already protect live registers.** The pool is
   `makePool` (`obo/src/obo.obo:1769-1774`): `rbx` and `r8..r15`. The call path
   `callFnWith` (`obo/src/obo.obo:5604-5843`) saves every live pool register and
   restores it after the call; local homes use `r12..r15`
   (`obo/src/obo.obo:1779-1787`). Any new native entry path must retain that
   protection, not bypass it.
5. **Obo arguments are stack words, not SysV registers.** The callee-side
   parameter layout is `16 + 8*(n-1-i)` from `rbp`; the result is in `rax`.
   The System V AMD64 ABI expects integer arguments in
   `rdi,rsi,rdx,rcx,r8,r9` and further arguments on the stack. A generated
   thunk must translate one to the other.
6. **The symbol file is validation metadata, not a binding.** `.sym` format 6
   (`obo/src/obo.obo:111-113`) records source hashes, interfaces, implementations
   and effects. It does not describe a native symbol address, relocatable
   object or linker input.
7. **Only `--check` can use a source-less dependency.** `parseImport`
   (`obo/src/obo.obo:10255-10293`) requires source in a normal build;
   `resolveSource` (`obo/src/obo.obo:10741-10766`) explicitly says that only
   `--check` may use a `.sym` without source. A binary-only module import is
   therefore not an existing capability.
8. **The runtime must be entered with the process's initial stack layout.**
   `compileProgram` (`obo/src/obo.obo:10437-10476`) starts by passing the
   current stack pointer to `rt_init`, then runs initializers, `main`, the
   module teardown and `RT_FINISH`. `rt_init` reads `argc` from that pointer
   (`obo/runtime/runtime.c:881-883`), and `rt_finish` exits with a raw syscall
   (`obo/runtime/runtime.c:360-391`). A hosted C `main` does not normally expose
   that layout.
9. **The runtime is freestanding and fixed.** `obo/runtime/rt.ld:1-8` places
   runtime text at `0x10000000` and data at `0x20000000`; `runtime.c` uses
   syscalls and has no libc startup, dynamic loader, constructors, TLS or IFUNC
   handling. libpq and its OpenSSL/GSS dependencies need the normal host
   dynamic loader and libc initialization, so a "static libpq archive with no
   libc" plan is not viable.
10. **Owned records are freed by the obo allocator only.** `rt_free`
    (`obo/runtime/runtime.c:305`) understands obo object headers and record
    descriptors (`descriptor` at `obo/src/obo.obo:2399`); record teardown frees
    fields, not foreign resources. There is no foreign destructor hook. A C
    `malloc` block, `PGconn` or `PGresult` cannot be attached to ordinary
    obo drop and expected to call `free`/`PQfinish`/`PQclear`.
11. **Effects are conservative summaries, not native proofs.** `pureFn`,
    `movesFn`, `freesAll`, `writesHandles` and `afterCall`
    (`obo/src/obo.obo:3780-3909`) are compiler-computed. A C function has no
    such summary unless the binding supplies an explicit contract.

## 4. Proposed architecture

The seam has four pieces, all generated from one spec:

```
foo.cbind + C headers
        |
        v
   cinterop tool  --->  foo.obx (ordinary wrapper module)
        |                 foo.sym (ordinary .sym, format 6)
        |                 foo_shim.S / foo_shim.o (generated thunks)
        |                 foo.manifest.json (hashes, ABI, platform, symbols)
        v
  obo compiler (generic cinterop builtin + native relocation sidecar)
        |
        v
  fixed obo image + .rela.obo.text sidecar
        |
        v
  system cc/ld + startup + shim.o + libfoo  --->  ET_EXEC
```

`foo.obx` is normal obo source. It has `pub fn`s with ordinary obo parameter
and result types. Their bodies call the one generic compiler builtin:

```obo
module hello

// generated, not hand-maintained
pub fn add(a0: int, a1: int): int {
    return __cinterop("add", a0, a1)
}
```

`__cinterop` is an internal compiler-known builtin, not a new declaration grammar. It
accepts a literal C symbol name and zero or more scalar `int` arguments in the
prototype. It emits the ordinary obo argument push sequence and a rel32 call
whose target is an unresolved symbol `obo_native_<name>`. The compiler records
the call offset and symbol name in a sidecar, exactly the information a real
object emitter would put in `.rela.text`. This is a generic mechanism: no
function name, library name or libpq-specific branch is compiled into the
compiler.

The generated thunk `obo_native_<name>` is supplied by the shim. The application
never calls it directly and never sees its ABI.

Why this seam is necessary: ordinary `.sym` plus a `.o` cannot work today.
`.sym` has no symbol binding, and the image has neither an object format nor
relocations. A wrapper in ordinary source also cannot call an arbitrary C
symbol without a generic way to emit that call. The prototype therefore adds
one small compiler builtin plus a generic relocation sidecar, and does the real
link with the system toolchain.

This proposal does **not** make `__cinterop` a public stable API. The builtin is
experimental, its argument surface is scalar-only, and it is not advertised in
`LANGUAGE.md` until a separate review accepts a stable design.

## 5. Specification language and manifest

The prototype uses a deliberately small JSON spec (TOML is also acceptable;
the key requirement is a simple, deterministic schema, not full Clang parsing).
Per the 2026-10-31 steering decision, this is a **fixture input for the
experimental proof**, not the proposed universal product: the intended
per-library workflow is an agent-maintained obo package using the documented
skill/templates, with this toolkit enforcing the generic checks. Do not grow
this schema into a wide header-parser option surface.

```json
{
  "format": "cinterop-proto/1",
  "module": "hello",
  "platform": "x86_64-linux-gnu",
  "abi": "sysv-amd64",
  "headers": [],
  "objects": [],
  "functions": [
    {"name": "add", "symbol": "add", "args": ["i64", "i64"], "ret": "i64",
     "effects": ["impure"]},
    {"name": "mix", "symbol": "mix", "args": ["i64","i64","i64","i64","i64","i64","i64","i64"],
     "ret": "i64", "effects": ["impure"]}
  ]
}
```

The first libpq availability probe used only:

```json
{"name": "pq_lib_version", "symbol": "PQlibVersion", "args": [],
 "ret": "i32", "effects": ["impure"]}
```

The spec is explicit. Every function requires either a supplied header or an
explicit `declarations` entry; the tool does not infer ownership, nullability,
lifetime or effects from a header. `i64` maps to a signed 64-bit C integer
(`long` on this target). `i32` is supported **only as a return width**, where
the thunk sign-extends `eax` into `rax`; `i32` arguments are rejected in this
prototype because there is no checked narrowing conversion. The signature check
is labelled `header` when a real header supplied the declaration and
`declarations-only` when the author supplied the declaration text.

The explicit-close adapter implements the connection/result/query subset below.
Future pointer/resource annotations (design only) are explicit and versioned:

```json
{"name": "PQconnectdb", "args": ["borrow_cstr"], "ret": "own<PGconn>",
 "release": "PQfinish", "effects": ["impure", "may_error"]}
{"name": "PQexec", "args": ["borrow<PGconn>", "borrow_cstr"],
 "ret": "own<PGresult>", "release": "PQclear", "effects": ["impure"]}
{"name": "PQgetvalue", "args": ["borrow<PGresult>", "i32", "i32"],
 "ret": "borrow_bytes", "lifetime": "owner", "owner": "arg0"}
```

The manifest is a separate, versioned file (`cinterop-proto/1`) containing at
least: spec, application, header/declaration input, generated wrapper, shim
source/object, `native.relocs`, native object, image, executable, host/start/
link/tool/compiler and C compiler hashes; target triple, pointer width, ABI,
signature-check mode (`header` or `declarations-only`), exported thunk set,
arity, effects and resolved shared-library paths/hashes. The build writes and
verifies an input manifest **before compiling**, then verifies the final
manifest automatically after linking. Missing required files fail rather than
being omitted. The sidecar is a typed record (`offset symbol nargs argtypes
result effects`), not symbol-only. Hashes are provenance, not signatures; a
same-signature sidecar or library substitution remains a trusted-input issue.

## 6. Generated shim and ABI thunk

For each spec function the generator emits one assembly thunk. The shape for
`add(a,b)` is:

```asm
    .text
    .globl obo_native_add
    .type  obo_native_add,@function
obo_native_add:
    push %rbp
    mov  %rsp, %rbp
    mov  24(%rbp), %rdi        # obo arg 0: 16 + 8*(2-1-0)
    mov  16(%rbp), %rsi        # obo arg 1: 16 + 8*(2-1-1)
    call add@PLT
    leave
    ret
```

The real generator handles 0..8 arguments:

- Arguments 0..5 go to `rdi,rsi,rdx,rcx,r8,r9`.
- Arguments 6..7 are pushed as SysV stack arguments in reverse order.
- If the number of stack arguments is odd, one 8-byte pad is pushed before
  them so `rsp` is 0 mod 16 immediately before `call` (the callee then sees the
  normal 8 mod 16 entry).
- Obo's entry `rsp` is 8 mod 16; the thunk's `push %rbp` makes its own frame
  aligned. `leave; ret` discards stack arguments and restores `rbp`.
- The thunk touches only `rbp`; it does not rely on `rbx` or `r12..r15` being
  preserved by C. The obo caller's ordinary call path already saves every live
  pool register, including `r8..r11` and homes in `r12..r15`, before the call.
  A future direct native entry path must preserve the same protection.
- `rax` carries the result. `i32` returns are sign-extended with
  `movslq %eax, %rax`.
- C callee-saved registers `rbx`, `rbp`, `r12..r15` are preserved by the C
  callee per SysV; the thunk must not claim that `r8..r11` are callee-saved.

The prototype deliberately excludes `double`/XMM, decimal multiword values,
struct-by-value, callbacks, variadics and TLS. Those need separate ABI and
ownership designs; pretending they work is worse than refusing them.

## 7. Link and startup strategy

The prototype keeps the existing fixed obo layout and lets the system toolchain
link around it:

1. Compile the application and generated wrapper with the experimental
   compiler, producing the fixed image plus a native relocation sidecar.
2. The cinterop tool extracts the image's five load regions into a relocatable
   `.o` with sections `.obo.text`, `.obo.rodata`, `.obo.bss`,
   `.obo.rttext`, `.obo.rtdata`, and creates `.rela.obo.text` entries for the
   recorded native calls. This extraction is generic and has no PQ-specific
   case; it is a prototype bridge, not the production backend.
3. Link with the system `cc`/`ld`, a linker script that places the obo sections
   at their existing fixed virtual addresses, a small hosted startup object,
   the generated shim object and the requested system library.
4. The startup uses the host `crt1.o`/`crti.o`/`crtn.o` and the normal
   `__libc_start_main`/dynamic-loader path. A small C `main` builds the
   initial-stack-shaped block that `rt_init` expects and jumps to the obo entry.
   The block is 16-byte aligned with padding above it, checks its pointer-array
   size and a 128 KiB stack headroom before writing, and fails cleanly if the
   1 MiB buffer is insufficient. This probe does **not** link
   `crtbegin.o`/`crtend.o` and does **not** test or claim C constructors, TLS,
   IFUNC or normal `atexit` teardown; `rt_finish` still exits by raw syscall.
   Those remain prototype boundaries for a production entry design.
5. The final artifact must be `ET_EXEC` (non-PIE), not a PIE/shared object,
   and the obo segments must be at the expected addresses. This avoids an
   ASLR relocation rewrite of obo's absolute cells.

The normal host startup is not optional. A freestanding static libpq archive
would still need glibc, OpenSSL, GSS and loader services; the design does not
pretend a no-libc link is a general C interop solution.

Prototype limitation: `rt_finish` exits with a raw syscall, so libc `atexit`
handlers and stdio flushing are bypassed at program end. The prototype's output
is syscall-based and flushes its own buffer, and the libpq probe allocates
nothing that needs a destructor. Production must bridge normal termination.

## 8. Ownership, effects and cleanup

### 8.1 Type mapping

- `int`/`i64`/`i32` map to ordinary obo `int` values. No pointer is exposed as
  an integer.
- A C pointer to an opaque foreign object is held by a generated private
  wrapper, never as a user-visible `void*`. The intended future representation
  is an obo `^T` checked internal generation handle whose table entry owns the C
  pointer. `^T` is not a `void*`: it is a checked handle with liveness and
  generation semantics.
- C `malloc` memory is not an obo string, record, array or `Result`; the two
  allocators must never free each other's objects.
- Every allocation is freed by the allocator that made it: obo objects by
  `rt_free`, C/libpq objects by `free`/`PQfinish`/`PQclear`.
- A borrowed C byte range (for example `PQgetvalue`) is copied into an ordinary
  obo `[]byte`/`string` while its owner is alive. The copy length comes from
  `PQgetlength`, never `strlen`.

### 8.2 Explicit resource rules (libpq)

The explicit-close libpq adapter implements the connection, query, result,
NULL/empty, byte and error rules below. Automatic ownership transfer and future
types remain design-only:

- `PQconnectdb` returns an owned `PGconn`; `PQfinish` is called exactly once.
- `PQexec`/`PQexecParams` return an owned `PGresult`; `PQclear` is called
  exactly once, including on error paths.
- `PQgetvalue` returns bytes borrowed from the `PGresult`; the copy must happen
  before `PQclear` and must use `PQgetlength`. Embedded NUL is preserved in a
  byte array; converting to an obo string must reject embedded NUL rather than
  silently truncate.
- SQL `NULL` is `PQgetisnull`, distinct from an empty string or a zero-length
  byte array. Null must never be inferred from a pointer value or a length.
- `PQresultStatus` decides success/error/empty/tuples-ok; an error result still
  owns a `PGresult` and is cleared with `PQclear`. `PQerrorMessage` is borrowed
  from the connection or result and is copied before the next operation that
  can invalidate it.
- Query text passed to C must be a stable NUL-terminated buffer for the call.
  An obo string containing an embedded NUL is rejected; arbitrary byte arrays
  are not silently treated as C strings. Input buffers must stay stable for the
  whole call, including any callback-free parameter array.
- Binary/text format, `PQftype`, `PQfmod` and `PQtypmod` are preserved. Unknown
  types are an error, never silently turned into a float. `money` is never
  converted to a binary float. Exact decimal is future work and must not reuse
  the float ABI.
- v1 does not connect to a database. The prototype may call `PQlibVersion`
  only. No query is evaluated and no SQL is sent to a server.

### 8.3 Automatic cleanup

Current record drop frees fields through `rt_free`; it cannot call
`PQfinish`/`PQclear`/`free`. The design therefore does not claim automatic
foreign finalization. v1 uses explicit scoped close/defer, with double-close
safety and documented ownership transfer. A future version may add a generic
foreign-resource finalizer hook, but it must be a new, reviewed mechanism, not
an undocumented destructor attached to an ordinary record. The public goal
remains automatic exact-once cleanup once ownership is proven; the prototype
does not pretend that goal is already met.

### 8.4 Effects

Every native function is conservatively `impure`. The prototype accepts no
`pure` claim and has no real effect-contract proof. The compiler treats a native
call through a synthetic callee with `freesAll = true` and
`writesHandles = true`, so `afterCall` invalidates deep references, the handle
root and every visible global before the call's result is used. It also counts
as a user call. The generated wrapper's ordinary `.sym` therefore contains
`freesall writeshandles`; the test asserts that. The scalar prototype passes no
managed pointers to C, but the summary does not rely on that narrower fact.
Effect summaries are inline metadata, not ownership proofs.

### 8.5 Explicit-close C resource registry and library adapter

The checkpoint adds a small toolkit-owned C registry
(`obo/experimental/cinterop/toolkit/cres.{h,c}`) and an explicit-close libpq
adapter (`toolkit/libpq_adapter.{h,c}`). It is a generic table, not a universal
IDL or per-library compiler branch.

- A token is `index | generation << 32`; the table entry also stores a kind and
  a release function. `cres_peek(token, kind)` validates the live bit,
  generation and kind before any dereference. Close poisons the generation
  before calling the release function, so a double close cannot call
  `PQfinish`/`PQclear` a second time.
- The generation is 31 bits and the table is trusted C. This protects ordinary
  obo code from stale, wrong-kind and accidentally forged integers; it is not an
  unforgeable capability against trusted C or an adversarial caller.
- Obo receives only `long` tokens. `PGconn*`, `PGresult*`, `char*`, `malloc`
  blocks and all pointer arithmetic stay in C. The `Connection` and `Rows`
  wrappers are ordinary `ref record`s with one integer field. Obo record fields
  are not private: `new Connection{tok: ...}` is writable by an importer, so the
  C registry, not the obo type, is what rejects an invalid token. The notes do
  not claim otherwise.
- Dropping a `Connection` or `Rows` frees the record only. It does **not** call
  `PQfinish` or `PQclear`. `close` is explicit and consumes the wrapper; double
  close is a safe false. `live_resources`/`live_connections`/`live_results`/
  `live_buffers` expose the registry counters, and the tests fail when a close is
  omitted. This is an explicit-close kit, not automatic foreign ownership.
- Byte buffers are C-owned, length-bounded (`CRES_MAX_BUFFER`), and accessed
  through the integer API. Obo builds the small fixed query packet, copies it
  into one C buffer, and reads result bytes through `cres_buf_get` with the C
  length. This byte loop is intentionally slow but honest for the first bounded
  proof; it does not pretend to be a general string bridge.
- `PQexecParams` receives the fixed SQL text and a descriptor array of parameter
  values, lengths and formats. SQL and text values reject embedded NUL; binary
  values preserve their exact length, including NUL. SQL is never interpolated.
  `NULL` is an explicit kind, distinct from an empty text or empty binary value.
- Values and error messages are copied with `PQgetlength`/`strlen` as
  appropriate; `PQgetvalue` is never used as a C string. `PQntuples`,
  `PQnfields`, `PQgetlength`, `PQfnumber`-style indices and `PQgetisnull` are
  checked for bounds before use. Error results still own a `PGresult` and are
  cleared exactly once. A `PGresult` remains usable after its `PGconn` is closed;
  the adapter tests that lifetime.
- Exact numeric text, OID and typmod are preserved as text/metadata. No numeric,
  `money` or decimal database value is routed through a binary float. Source
  float arguments/returns are rejected by the native contract; floats are not a
  supported native type.
- The adapter packet and registry are fixed, reviewed C. Do not grow them into a
  generic IDL, a callback bridge, a variadic shim, struct-by-value ABI, TLS,
  IFUNC or a raw-pointer escape hatch. If a library needs one of those, stop and
  report a concrete blocker.

The opt-in live test uses an ordinary local PostgreSQL role/database only with
explicit user authorization. It is not a product default and does not modify
server configuration, roles, schema or persistent data.

## 9. `.sym` checks, hashes and build validation

- The existing `.sym` format stays format 6 and ABI 1. The wrapper is a normal
  module and its `.sym` is written by the normal writer.
- Native-specific metadata lives in the typed sidecar and manifest, not in a
  patched `.sym` grammar. It uses the experimental `cinterop-proto/1` prefix; it
  does not bump `.sym` format or obo ABI.
- The compiler records every native call's offset, symbol, argument count, all
  `i64` argument types, result width and conservative effects. The tool checks
  every record against the spec before `make_object`/link, rejects missing or
  extra arguments and incompatible symbols, and requires every spec symbol to
  have a call.
- The build writes and verifies an input manifest before compiling, then
  automatically verifies the final manifest after linking. Required roles
  include the application, spec, headers, sources, wrapper, wrapper `.sym`,
  shim source/object, signature check, sidecar, image, native object, host/
  start/link/tool/compiler and C compiler. Missing files fail.
- The generated shim's `obo_native_*` export set is checked against the spec
  before link. The object's relocation symbols come from the validated sidecar.
- The final ELF is checked for `ET_EXEC`, expected fixed VAs and flags, segment
  file bounds/overlap, no W+X segment and a non-executable `GNU_STACK`. The
  linker script asserts each region's capacity.
- A native object, sidecar, header, wrapper, `.sym` or manifest modified after
  build is rejected. A platform/ABI/pointer-width mismatch is rejected.
- A wrong arity is rejected by the typed sidecar check; a header/declaration
  mismatch is rejected by the generated C signature check; `i32` arguments are
  rejected rather than narrowed.
- The resolved shared libraries are recorded with paths and hashes, with the
  documented limitation that the dynamic loader can still resolve a different
  file at run time. Hashing establishes provenance and accidental-corruption
  detection, not memory safety or toolchain authorization. C remains trusted
  and unchecked; a same-signature sidecar/lib substitution is trusted input.

## 10. Security and trusted C

C code linked through this mechanism can do anything the process can do. The
design does not sandbox it, does not prove its memory safety, and does not
claim that annotated headers make it safe. The threat model is:

- the user chooses and trusts the C library and headers;
- the spec is user-authored and reviewed like source;
- the manifest prevents stale/mismatched artifacts but is not a signature
  authority;
- no automatic execution of native code at compiler time beyond invoking the
  trusted C toolchain for declared inputs;
- no database connection, SQL evaluation or network access in the default
  tests or compiler; the live database smoke test is opt-in, local and
  explicitly authorized;
- no native code is loaded from an untrusted search path; the final ELF is a
  normal system-linked executable with its normal dynamic dependencies.

Callbacks, variadics, struct-by-value and TLS are excluded because their ABI
and lifetime rules are not yet designed. This is a deliberate safety boundary,
not an assertion that all of C is safe.

## 11. Versioning

- Current compiler: `0.10`, `.sym` format 6, ABI 1. The native experiment does
  not take a new version, format or ABI number.
- Native prototype package format: `cinterop-proto/1` (experimental, mandatory
  typed sidecar; no stable native ABI).
- Future stable native manifest: `cinterop/1`, thunk ABI `cabi1`, independent
  of `.sym` format/ABI. No stable number is assigned now.
- Floats are released at `0.10`/format 6/ABI 1; decimal remains after that at
  `0.11`/format 6 (Stage B prep is separate). Native interop does not take
  either version.
- A future ABI change to obo calls, record descriptors or handle representation
  requires a separate `SYM_ABI` decision; the native manifest records the ABI
  it was generated for and refuses a mismatch.

## 12. Test gates

P0 (ABI only, no application import):

- scalar C functions with 0..8 signed 64-bit arguments, including
  `INT64_MIN`/`INT64_MAX`;
- expected results for odd and even argument counts;
- `i32` returns at `INT32_MIN`/`INT32_MAX`, sign-extended; `i32` arguments
  rejected;
- stack alignment checked for 6/7/8 arguments;
- `rbx`, `rbp`, `r12..r15` preservation checked from a caller that holds
  values across the call;
- compiled-`__cinterop` path with nested arguments, non-zero stack depth and
  live locals/homes, not only the handwritten ABI probe;
- no libc needed for the freestanding ABI probe.

P1 (tool and artifacts):

- spec parsing, deterministic wrapper/shim/manifest generation;
- wrong direct arity, extra arguments and incompatible symbol substitution fail
  before link even with `--native-relocs`;
- `i32` arguments, missing header/declaration, duplicate C symbols and
  variadic/unsupported declarations are rejected;
- modified native object, sidecar, header, wrapper `.sym` and manifest fail;
- platform/ABI mismatch and missing required files fail;
- missing thunk/C definition is a link failure;
- `--check` refuses native modules rather than pretending to have checked them.

P2 (end-to-end):

- ordinary application `import "hello.obx"` and `hello::add(20,22) == 42`;
- actual system-linked `ET_EXEC`, not a runtime child process;
- `readelf -h` shows `EXEC`, `readelf -l` shows the obo segments at their
  expected addresses and permissions, `ldd` shows the intended shared
  libraries;
- argv/`argc` and environment work for both parities; `OBO_CHECK`/`OBO_STATS`
  are visible; the synthetic stack fails cleanly near its pointer-array limit;
- a second independent scalar module/library proves the seam is not
  PQ-specific;
- no unresolved thunk symbols in the final link.

P3 (libpq, conditional):

- if installed headers and library are available, build `PQlibVersion` and
  print the actual version;
- `ldd`/`readelf -d` show `libpq.so.5` and normal dynamic dependencies;
- real `PQmakeEmptyPGresult(NULL, status)` + `PQclear` proves result ownership
  without a connection;
- the explicit-close adapter is exercised against a fake libpq for NULL/empty,
  binary/NUL, errors, allocation failure and close counts;
- the live `PQconnectdb`/`PQexecParams` smoke test runs only when the user
  explicitly supplies a local DSN; it uses SELECTs and does not modify server
  configuration, roles, schema or persistent data;
- if the library is unavailable, record `blocked: libpq` and do not install
  anything.

Regression gates for the prototype commit:

- existing compiler/module tests remain unchanged; no test is skipped or
  removed;
- normal (non-native) emitted code is byte-identical except an explicit
  opt-in comparison;
- compiler self-build and self-host fixed point still hold if the compiler is
  changed;
- product self-measurement stays at or below the approved `2,500,000` peak
  budget; if the compiler change breaches it, stop and report rather than
  increasing the cap or weakening a check;
- targeted ordinary module/ownership/ABI/no-libc tests and the actual PQ
  version test only; no final full 679/529 mutant matrix for this private
  experimental checkpoint.

## 13. Risks and staged plan

Risks:

- **Prototype link bridge is not production.** Extracting a fixed executable
  into sections is a generic but fragile bridge. Production should emit a real
  relocatable object with sections and relocations directly from the compiler.
- **Fixed addresses and non-PIE.** The current image depends on fixed addresses.
  Hosted linking at those addresses works for a non-PIE `ET_EXEC`, but it
  forecloses PIE/ASLR and needs careful linker-script ownership of the dynamic
  sections.
- **Startup is bespoke.** The synthetic initial stack plus host `crt1` bridge is
  acceptable for a probe, not for a supported runtime. Constructors, TLS, IFUNC
  and libc termination are untested/unclaimed; they need a production entry
  design.
- **Agent adaptation is not a safety proof.** The approved toolkit-plus-skill
  direction keeps mandatory compiler/link checks, and the project skill and
  templates are implemented. Human/agent C-header and ownership interpretation
  can still be wrong; only the enforced contracts and tests are checked.
- **Effects and ownership are not automatic.** Trusted C can violate any
  annotation. No finalizer exists, so automatic cleanup is future work.
- **Limited ABI surface.** No floats, callbacks, variadics, structs by value or
  TLS. That is intentional for v1.
- **Build reproducibility.** Hashes pin one build, but the system toolchain and
  shared-library versions still affect the final binary. The manifest must
  record the toolchain and dependency versions.
- **Source-only wrapper.** The prototype requires generated obo source. A
  binary-only module would need a new source-less import/binding design; it is
  not smuggled in through `.sym`.

Plan:

1. **P0** — write and test generated thunks against scalar C functions,
   including 0..8 args and alignment/register checks.
2. **P1** — implement the cinterop tool, spec/manifest validation, generated
   wrapper and shim, negative tests.
3. **P2** — add the minimal generic compiler seam (`__cinterop` builtin plus
   relocation sidecar), build the fixed image, link with the system toolchain,
   import the generated module and run `add(20,22)`.
4. **P3** — conditional libpq `PQlibVersion` probe, if available, with no
   database connection.
5. Stop at the prototype checkpoint for architecture/security review. Do not
   merge, push, promote or seed-copy.
6. The agent skill/templates for maintaining per-library obo packages are
   implemented in `obo/experimental/cinterop/skill/`. Packaging/global
   installation remains separate; do not expand the fixture into a universal
   configurable generator.

## 14. Prototype results

Recorded from the repair worktree, 2026-10-31. Reviewed prototype:
`a29f35d`; independent verdict: REVISE. Historical repair baseline: `cd90a2d`,
base `befa626` (`0.9`, `.sym` format 5, ABI 1). The feature was then merged with
released `main` `a3e5b1f` (`0.10`, `.sym` format 6, ABI 1) and the explicit-close
libpq adapter was added; sections 14.6-14.9 record that checkpoint. Evidence
directories: `/tmp/opencode/c-interop-repair-a29f35d/` (before/failing cases),
the fresh repair archive, and the final merged feature archive recorded with the
report. This section records what ran, not what was hoped.

### 14.1 B1 and B2 reproduced before repair

**B1, startup half-parity.** The committed `a29f35d` host built the block and
inserted the alignment pad below `argc`. Running its runtime probe with an odd
environment:

```text
env -i FOO=bar .../app one two
argc=0
FOO=nil
zero=0
```

The first word read as `argc` was the pad, and the environment pointer pointed at
`argv`. This is the actual failing case, not a code-reading inference.

**B2, direct wrong-arity call.** The committed tool accepted a direct call
`cinterop("add", 1)` even though the spec's `add` takes two arguments. The build
succeeded, the executable ran, and printed a wrong value:

```text
direct=4198445
```

The sidecar recorded only `offset symbol`; the tool checked symbol membership,
not arity.

### 14.2 Repairs and verified gates

**B1/H4.** `host.c` now computes the required pointer words with checked
addition, aligns the block base before writing `argc`, places the pad above the
block, requires 128 KiB of stack growth headroom, and fails with
`obo: cinterop startup stack overflow` before writing outside the 1 MiB buffer.

Verified:

```text
env -i FOO=bar app one two          -> argc=3, arg0=<app>, FOO=bar
env -i FOO=bar BAZ=qux app one two  -> argc=3, arg0=<app>, FOO=bar
env -i FOO=bar OBO_STATS=1 app      -> stderr has "peak bytes"
env -i FOO=bar OBO_CHECK=1 leak-app -> exit 8, "file descriptors leaked"
110000 very short environment vars  -> exit 0 (fits)
116000 very short environment vars  -> exit 127, clean startup-stack message
```

The 116,000-variable case is permitted by the host ARG_MAX and reaches the
pointer-array boundary honestly; the failure is clean, not a write below the
buffer.

**B2/H1/H2.** The compiler's builtin is now the internal `__cinterop`. Its typed
sidecar is:

```text
cinterop-relocs 1
<offset> <symbol> <nargs> <argtypes> <result> freesall,writeshandles
```

Every record is checked against the spec before `make_object`/link: missing and
extra arguments fail, unknown/incompatible symbols fail, every spec symbol must
have a call, and duplicate offsets fail. Direct wrong-arity with
`--native-relocs` is now:

```text
cinterop: native call to 'add' has 1 argument(s), spec says 2
```

`i32` arguments are rejected instead of silently narrowed; `i32` returns remain
sign-extended, with `INT32_MIN`/`INT32_MAX` tests. Native calls receive a
conservative `freesAll`/`writesHandles` summary through `afterCall`; the wrapper
`.sym` is asserted to contain `freesall writeshandles`, and a module test
confirms a reference into a global is invalidated across the native call.

**H3/M6.** The build now writes and verifies an input manifest before compiling,
then verifies the final manifest automatically. Required hashes include the
application, spec, headers/declarations, sources, wrapper, wrapper `.sym`,
shim source/object, signature check, sidecar, image, native object,
host/start/link/tool/compiler and C compiler. Missing files fail. Signature
checks are labelled `header` (scalar example) or `declarations-only` (libpq
manual declaration); there is no silent "checked" mode. The shim's
`obo_native_*` export set is validated against the spec before link. Resolved
dynamic library paths/hashes are recorded by `ldd`,
with the documented limitation that run-time resolution can differ.

Negative tests cover changed header, deleted and edited sidecar, modified
native object, missing wrapper `.sym`, manifest metadata tamper, missing
native definition, variadic declaration, duplicate C symbol, no
header/declaration, `i32` argument, platform mismatch and `--check` refusal.

**M1/M2.** The linker script now asserts each fixed region's capacity. The tool
validates program-header file bounds, `filesz <= memsz`, segment overlap,
expected obo VAs/flags, no W+X segment and non-executable `GNU_STACK`. The
review's "1 MiB before 0x401000" address-order note was mistaken: the host
region starts at `0x03000000` and is bounded below `0x04000000`; the obo text
is at `0x00401000`. The actual layout is asserted, not copied from that note.

### 14.3 End-to-end results after repair

Scalar command (ordinary obo import + generated wrapper):

```sh
python3 obo/experimental/cinterop/cinterop.py build \
  obo/experimental/cinterop/examples/scalar.cbind \
  obo/experimental/cinterop/examples/scalar_app.obx \
  --out /tmp/.../scalar --obo obo/obo
```

Observed: `scalar ok`, exit 0. This covers 0..8 `i64` arguments, nested calls,
non-zero expression depth, live locals/homes, `INT64_MIN/MAX`, and
`INT32_MIN/MAX` returns. The handwritten freestanding ABI probe still runs with
no libc and empty `nm -u`; it checks `rbx`, `rbp`, `r12..r15`, while the
compiled-path test covers the generated call sequence and register homes.

Final ELF: `Type: EXEC`, entry `0x3000030` (host crt1); `.obo.text` at `0x401000`,
`.obo.rodata` at `0x08000000`, `.obo.bss` at `0x0C000000`, `.obo.rttext` at
`0x10000000`, `.obo.rtdata` at `0x20000000`; `GNU_STACK RW`; no W+X segment;
no unresolved `obo_native_*` symbol.

libpq availability probe:

- `/usr/lib/x86_64-linux-gnu/libpq.so.5` -> `libpq.so.5.16` exists.
- `/usr/include/postgresql/libpq-fe.h` is absent and `pkg-config libpq` is not
  installed, so the probe uses an explicit `declarations-only` declaration.
- Actual run: `PQlibVersion=160015`.
- `ldd` includes `libpq.so.5` and the normal installed dependencies
  (`libssl.so.3`, `libcrypto.so.3`, GSS/Kerberos, LDAP, SASL, GnuTLS, ...).
- No database connection, query, server, socket or service action was taken.

### 14.4 Regression, budget and fixed point

- `tests/module_test.py`: 106 passed, 0 failed.
- `tests/elf_test.py`: pass.
- `tests/run_fork.py --smoke`: 1544 passed, 0 failed (the full suite reordered,
  not a reduced subset).
- Normal non-native program: seed and repaired compiler output byte-identical.
- Repaired compiler self-build: byte-identical fixed point.
- Product peak: seed `1,990,992` bytes; repaired compiler `1,991,792` bytes;
  delta `+800`, under the approved `2,500,000` cap. No cap increase or check
  weakening.
- Targeted cinterop tests: pass, including the libpq probe; libpq is skipped
  only when `libpq.so.5` is absent, and a build failure with the library present
  is a failure.

No final full 679/529 mutant matrix was run for this private checkpoint.

### 14.5 Remaining limits and status

The fixed-image extraction, host `crt1` plus synthetic-stack bridge, non-PIE
layout and prototype linker script are not a production backend. The probe
does not link `crtbegin.o`/`crtend.o` and does not test or claim C constructors,
TLS, IFUNC or normal `atexit` teardown; `rt_finish` still exits by raw syscall.
At the `a29f35d` repair stage no pointer/ownership/foreign-resource API
existed. The later explicit-close checkpoint (14.6-14.9) adds `PGconn`/`PGresult`
tokens, bounded borrowed bytes and explicit `PQfinish`/`PQclear`, but still no
automatic foreign finalizer. No callbacks, variadics as user syntax, structs by
value, source floats/decimals in the native contract. C is trusted and
unchecked; hashes are provenance, not signatures. The direct `__cinterop`
builtin is internal and experimental, and same-signature sidecar edits remain a
trusted-input issue.

During this repair the user approved the toolkit-plus-agent-skill steering
recorded in section 1.1. That decision is recorded here and in the experimental
README; the portable project skill and templates are now implemented under
`obo/experimental/cinterop/skill/`, while global installation/packaging remains
separate. No universal generator/API expansion was added.

Status: **REVISE response complete for scoped repairs; stop for independent
rereview.** Not merged, pushed, promoted or seed-copied.

### 14.6 Merged 0.10 checkpoint and regression gates

The feature was merged with released `main` `a3e5b1f` (do not merge the feature
back into `main` from this checkpoint). The merge conflicts were the expected
generic compiler lists: the builtin universe now contains both the float
builtins and `__cinterop`; `callExempt` contains the float names and
`__cinterop`; the usage line contains both `--native-relocs` and
`--test-float-bits`. The promoted `0.10` seed from main was preserved; the
feature's old `0.9` seed was not copied over it. The merged compiler reports
`obo 0.10 sym-format 6 abi 1`.

Verified on the merged feature:

| Gate | Result |
|---|---|
| `make obo` from released 0.10 seed | pass |
| `tests/module_test.py` | 107 passed, 0 failed |
| `tests/elf_test.py` | pass |
| `tests/float_f1.py` | ok, 19 groups |
| `tests/doc_check.py` | 62 examples, 0 problems |
| `tests/homes_check.py` | pass |
| `tests/sym_test.py` | 42 passed, 0 failed |
| `tests/run_fork.py` | 1555 passed, 0 failed |
| normal `examples/hello.obx` vs seed | byte-identical executable |
| compiler self-build fixed point | stage2 == stage3 == merged `obo` (`9cd9b55f...`) |
| product peak (`OBO_STATS=1`, self-compile) | 2,042,368 bytes; seed 2,041,568; delta +800, under 2,500,000 |
| targeted cinterop suite | pass, including fake adapter and opt-in live DB |

### 14.7 Explicit-close resource adapter results

The toolkit now contains the generic registry and the libpq adapter described
in §8.5. Deterministic coverage uses `tests/fake_libpq.c` and
`tests/fake_adapter_test.c`; no socket is opened there.

The fake adapter tests exercise and pass:

- connect success, connect `BAD` with error text, and `NULL` connect;
- `PQfinish` exactly once on normal/double/stale/wrong-kind close;
- query success, error result, `NULL` result and empty result;
- `PQclear` exactly once, including error results and a result that outlives
  its connection;
- NULL vs empty parameter separation, binary parameter with embedded NUL, text
  parameter with NUL rejection, and SQL text with NUL rejection;
- malformed packet (trailing bytes, bad kind, bad result format, bounds);
- allocation failure in `PQexecParams` (no token and no leak);
- injection-shaped text passed as a parameter while the SQL text remains the
  fixed statement;
- exact numeric text plus OID `1700` and typmod `-1`, never a float;
- row/column bounds and stale-token rejection before dereference;
- registry live counts return to zero after every cleanup path.

The obo wrapper is tested against the fake for close, error-result close,
binary/NUL, NULL/empty, numeric text/metadata, no real `libpq.so.5` dependency,
and the deliberate missing-close app. The missing-close app prints `live=1` and
`conns=1`, so the leak check fails if a close is omitted.

Real libpq without a database:

```text
PQlibVersion=160015
empty_status=2
empty_rows=0
empty_fields=0
empty_closed=true
live=0
```

This proves the real dynamic link and real `PQmakeEmptyPGresult(NULL, ...)` /
`PQclear` ownership without connecting. It does not prove a driver.

### 14.8 Opt-in live PostgreSQL smoke test

The user explicitly authorized local database testing for this checkpoint. The
test used the least-privileged local role `chris` (no superuser, no createdb,
no createrole) and the existing `postgres` database over the local Unix socket:

```text
host=/var/run/postgresql port=5432 dbname=postgres user=chris connect_timeout=3
```

This is a private local-development configuration recorded for reproducibility,
not a universal default. The test did not create roles/databases/schema, edit
`pg_hba`, start/stop services, expose ports, install packages, or read
credential files. The server was PostgreSQL 16.15; `current_user=chris`,
`current_database=postgres`.

The in-process obo app used the adapter, not `psql` as a runtime helper, and
ran only `SELECT`s. Observed output:

```text
server_version=160015
numeric_text=1.234567890123456789 oid=1700 typmod=-1
null_and_empty=ok
sql_error_length=134
binary_bytes=00ff41 len=3
live=0
live_db_smoke=ok
```

Covered: parameterized text treated as data (a SQL-injection-shaped string
round-trips unchanged), `$2::int` text parameter, SQL `NULL` distinct from empty
string, exact `numeric` text plus OID/typmod, a real server error result cleared
cleanly, binary `bytea` result with embedded NUL bytes `00 ff 41`, connection and
result close, and zero live registry resources. No temporary or persistent table
was needed.

### 14.9 Status and remaining limits

Status: **explicit-close adapter checkpoint, pending independent rereview.** Not
merged to `main`, not pushed, not promoted, no seed copy.

Still not implemented or claimed: automatic foreign finalization on ordinary
record drop; a production driver or full libpq API; callbacks, C variadics,
struct-by-value, TLS/IFUNC, source floats/decimals in the native contract;
binary-only module imports; stable `cinterop/1`/`cabi1`; PIE/ASLR; normal libc
termination (`rt_finish` is still a raw syscall); `crtbegin`/`crtend`
constructors. The fixed-image linker bridge and synthetic startup stack remain
prototype infrastructure. Global skill packaging/installation remains separate;
the repository project skill is implemented. Hashes are provenance, not
signatures.

## 15. Open questions and review focus

1. Should production emit a relocatable object directly, or keep a separate
   link tool that consumes an image plus relocation sidecar?
2. Is the internal `__cinterop` builtin acceptable as the minimal seam, or
   should the stable design use a non-variadic declaration form? The prototype
   accepts 0..8 arguments and checks the sidecar; it does not stabilize that
   surface. The approved toolkit-plus-skill direction does not require a
   universal generator, but the stable declaration/contract form is still open.
3. Where should the native entry path live so it shares the ordinary call-site
   register save/restore exactly, without duplicating `callFnWith`?
4. What is the production startup contract for `argc/argv/envp`, constructors,
   TLS, IFUNC and `atexit`, given that `rt_finish` currently exits by syscall?
5. How should a foreign-resource finalizer be represented so record drop can
   call `PQfinish`/`PQclear` exactly once, with double-close safety and no
   undocumented destructor?
6. Should a future stable binding accept any `pure` claim? The current answer is
   no: native calls are `freesall`/`writeshandles` until a checked effect
   contract exists.
7. What manifest signature/hash chain is required for supply-chain review,
   beyond accidental-corruption hashes, and how should resolved dynamic-library
   substitutions be handled?
8. When, if ever, should a binary-only module import be supported? This
   proposal deliberately does not pretend it exists.
