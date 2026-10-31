# Native C interop (`cinterop`) — design note

Status: **design proposal; experimental prototype exists (section 14), not a
production implementation.** Date: 2026-10-31. Baseline: `main` at `befa626`
(`obo: promote the 0.9 seed and release Stage A lambdas`), compiler `0.9`,
`.sym` format 5, ABI 1. The only work approved here is a one-time generic
capability and an experimental prototype for review. This is not approval of a
full production API, not a stable format, and not permission to merge, push or
promote a seed.

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
6. A useful scalar probe first, then libpq availability as a separate gate.
   The libpq probe is `PQlibVersion` only; no database connection.

## 2. Non-goals

- No stable public C interop API in this proposal.
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
- No production-quality libpq driver: connection, query, result and error
  lifetime rules are designed here, but only `PQlibVersion` is executed in the
  prototype.
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
6. **The symbol file is validation metadata, not a binding.** `.sym` format 5
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
        |                 foo.sym (ordinary .sym, format 5)
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
    return cinterop("add", a0, a1)
}
```

`cinterop` is a compiler-known builtin, not a new declaration grammar. It
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

This proposal does **not** make `cinterop` a public stable API. The builtin is
experimental, its argument surface is scalar-only, and it is not advertised in
`LANGUAGE.md` until a separate review accepts a stable design.

## 5. Specification language and manifest

The prototype uses a deliberately small JSON spec (TOML is also acceptable;
the key requirement is a simple, deterministic schema, not full Clang parsing):

```json
{
  "format": "cinterop-proto/0",
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

For libpq the only prototype entry is:

```json
{"name": "pq_lib_version", "symbol": "PQlibVersion", "args": [],
 "ret": "i32", "effects": ["pure"]}
```

The spec is explicit. Headers are included in the generated C file only when
the user provided them; the tool does not infer ownership, nullability,
lifetime or effects from a header. `i64` maps to a signed 64-bit C integer
(`long` on this target); `i32` maps to C `int` and is sign-extended in `rax` on
return so obo always sees a 64-bit `int`.

Future pointer/resource annotations (design only, not implemented) are
explicit and versioned:

```json
{"name": "PQconnectdb", "args": ["borrow_cstr"], "ret": "own<PGconn>",
 "release": "PQfinish", "effects": ["impure", "may_error"]}
{"name": "PQexec", "args": ["borrow<PGconn>", "borrow_cstr"],
 "ret": "own<PGresult>", "release": "PQclear", "effects": ["impure"]}
{"name": "PQgetvalue", "args": ["borrow<PGresult>", "i32", "i32"],
 "ret": "borrow_bytes", "lifetime": "owner", "owner": "arg0"}
```

The manifest is a separate, versioned file containing at least: spec hash,
wrapper hash, shim source hash, shim object hash, compiler identity, `.sym`
hash, target triple, pointer width, ABI, exported symbols, arity and effects.
The build tool recomputes the hashes and refuses a stale or modified artifact
before invoking the compiler or executing anything.

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
4. The startup uses the host `crt1.o`/`crti.o`/`crtn.o` and therefore the
   normal dynamic loader and `__libc_start_main` path, so glibc constructors,
   TLS, IFUNC resolution and libpq's dependencies behave normally. A small C
   `main` builds the initial-stack-shaped block that `rt_init` expects and
   jumps to the obo entry. This synthetic block is a prototype boundary;
   production should add a proper entry function that takes `argc/argv/envp`
   explicitly.
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

These are design requirements, not implemented v1 behavior:

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

Every native function defaults to `impure`. `pure` is allowed only when the
spec author asserts that the function has no observable side effects, no
allocation that escapes, and no dependence on mutable process state. The
compiler must be conservative:

- Native calls do not inherit a made-up `pure` summary from the wrapper.
- A native call site counts as a user call and invalidates assumptions that a
  call may free or write through a handle, even when the current prototype's
  arguments are only scalars.
- The generated wrapper's `.sym` carries the ordinary compiler-computed effect
  summary; native code is not exempt from `freesAll`/`writesHandles`
  conservatism.
- Effect summaries are inline metadata, not ownership proofs.

## 9. `.sym` checks, hashes and build validation

- The existing `.sym` format stays format 5 and ABI 1. The wrapper is a normal
  module and its `.sym` is written by the normal writer.
- Native-specific metadata lives in the manifest, not in a patched `.sym`
  grammar. It may use an experimental `cinterop-proto/0` prefix; it must not
  bump `.sym` format or obo ABI.
- The build checks, before compiling or running:
  1. spec/manifest format and platform triple;
  2. ABI string and pointer width;
  3. arity and scalar types against the generated wrapper;
  4. spec, wrapper, shim-source and shim-object hashes;
  5. that the object's exported thunk names exactly match the spec;
  6. that the final link has no missing thunk (a missing symbol is a link
     error, not a runtime lookup).
- A native object modified after manifest generation is rejected.
- A stale manifest (source or spec changed) is rejected.
- A platform mismatch is rejected before compilation.
- A wrong arity/signature is rejected by the wrapper generator and by the
  compiler's type check.
- Hashing establishes provenance and accidental-corruption detection, not
  memory safety. C remains trusted and unchecked.

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
- no database connection, SQL evaluation or network access in the prototype;
- no native code is loaded from an untrusted search path; the final ELF is a
  normal system-linked executable with its normal dynamic dependencies.

Callbacks, variadics, struct-by-value and TLS are excluded because their ABI
and lifetime rules are not yet designed. This is a deliberate safety boundary,
not an assertion that all of C is safe.

## 11. Versioning

- Current compiler: `0.9`, `.sym` format 5, ABI 1. These are unchanged by this
  proposal.
- Native prototype package format: `cinterop-proto/0` (experimental).
- Future stable native manifest: `cinterop/1`, thunk ABI `cabi1`, independent
  of `.sym` format/ABI. No stable number is assigned now.
- Floats remain next at `0.10`/format 6/ABI 1; decimal remains after that at
  `0.11`/format 6. Native interop does not take either version.
- A future ABI change to obo calls, record descriptors or handle representation
  requires a separate `SYM_ABI` decision; the native manifest records the ABI
  it was generated for and refuses a mismatch.

## 12. Test gates

P0 (ABI only, no application import):

- scalar C functions `identity`, `add`, `mix` with 0..8 signed 64-bit
  arguments;
- expected results for odd and even argument counts;
- stack alignment checked by a C function that uses aligned SSE operations or
  an explicit alignment assertion;
- `rbx`, `rbp`, `r12..r15` preservation checked from a caller that holds
  values across the call;
- no libc needed for the freestanding ABI probe.

P1 (tool and artifacts):

- spec parsing, deterministic wrapper/shim/manifest generation;
- wrong arity and wrong signature fail before execution;
- modified native object hash fails;
- platform mismatch fails;
- stale manifest fails;
- missing thunk is a link failure.

P2 (end-to-end):

- ordinary application `import "hello.obx"` and `hello::add(20,22) == 42`;
- actual system-linked `ET_EXEC`, not a runtime child process;
- `readelf -h` shows `EXEC`, `readelf -l` shows the obo segments at their
  expected addresses, `ldd` shows the intended shared libraries;
- a second independent scalar module/library proves the seam is not
  PQ-specific;
- no unresolved thunk symbols in the final link.

P3 (libpq, conditional):

- if installed headers and library are available, build `PQlibVersion` and
  print the actual version;
- `ldd`/`readelf -d` show `libpq.so.5` and normal dynamic dependencies;
- no `PQconnectdb`, query, server, socket or service action;
- if unavailable, record `blocked: libpq` and do not install anything.

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
- **Startup is bespoke.** The synthetic initial stack and custom `_start` are
  acceptable for a probe, not for a supported runtime. libc termination and
  constructors need a production entry design.
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
3. **P2** — add the minimal generic compiler seam (`cinterop` builtin plus
   relocation sidecar), build the fixed image, link with the system toolchain,
   import the generated module and run `add(20,22)`.
4. **P3** — conditional libpq `PQlibVersion` probe, if available, with no
   database connection.
5. Stop at the prototype checkpoint for architecture/security review. Do not
   merge, push, promote or seed-copy.

## 14. Prototype results

Recorded from the actual prototype in this worktree, 2026-10-31. The design
commit is `cd90a2d`; the prototype code and tests are the working-tree changes
committed separately after this document. Artifacts and logs are under
`/tmp/opencode/c-interop-prototype-20261031/`.

### 14.1 Scalar end to end (P0/P1/P2)

Command:

```sh
python3 obo/experimental/cinterop/cinterop.py build \
  obo/experimental/cinterop/examples/scalar.cbind \
  obo/experimental/cinterop/examples/scalar_app.obx \
  --out /tmp/opencode/c-interop-prototype-20261031/scalar --obo obo/obo
```

Observed: build succeeded; the ordinary obo application printed `scalar ok`
and exited 0. The test covers `zero`, signed 64-bit `identity`, `add`, and
`sum3`..`sum8` (0..8 arguments), plus `align6`, `align7`, `align8`.

Generated `.sym` for the wrapper is ordinary format 5 with no native extension
(`fn add(p0: int, p1: int): int writeshandles`), and the application import is
an ordinary `import "hello.obx"`.

Final artifact:

- `readelf -h`: `Type: EXEC (Executable file)`, entry `0x3000000`.
- `readelf -l`: `.obo.text` at `0x401000`, `.obo.rodata` at `0x08000000`,
  `.obo.bss` at `0x0C000000`, `.obo.rttext` at `0x10000000`,
  `.obo.rtdata` at `0x20000000`; host CRT/PLT/dynamic tables above.
- `nm -u`: only `__libc_start_main@GLIBC_2.34`, a normal dynamic-loader
  reference; no `obo_native_*` unresolved thunk.
- `ldd`: `libc.so.6`, the normal interpreter.

The freestanding ABI probe (`gcc -nostdlib -no-pie`, no libc) linked the
generated shim and scalar objects, ran with exit 0, and had an empty `nm -u`.
It checks the result plus `rbx`, `rbp`, `r12..r15` preservation and the
odd/even 7/8-argument alignment paths.

### 14.2 libpq availability and version (P3)

Host facts observed read-only:

- `/usr/lib/x86_64-linux-gnu/libpq.so.5` and `libpq.so.5.16` exist.
- `/usr/include/postgresql/libpq-fe.h` is absent.
- `pkg-config --modversion libpq` reports no package (the command is absent or
  the `.pc` file is not installed).
- No database connection, query, server, socket or service action was taken.

The probe therefore uses the documented fallback: a manual
`extern int PQlibVersion(void);` declaration in the generated C input and
`-l:libpq.so.5`. Build succeeded; the application printed:

```
PQlibVersion=160015
```

`ldd` shows `libpq.so.5` plus its normal dependencies (`libssl.so.3`,
`libcrypto.so.3`, GSS/Kerberos, LDAP, SASL, GnuTLS and others), which is the
expected hosted dynamic-loader path. `nm -u` has `PQlibVersion` (resolved by
the dynamic loader at run time) and `__libc_start_main`, not an unresolved
`obo_native_*` thunk.

### 14.3 Negative and regression gates

The targeted test command is:

```sh
python3 obo/experimental/cinterop/tests/test_cinterop.py
```

Observed: `libpq: PQlibVersion=160015`, `cinterop tests ok`, exit 0. It also
passes these failures:

- wrong arity against a provided C header (`add` declared with one argument):
  rejected by the generated C signature check before execution;
- platform mismatch (`aarch64-linux-gnu`): rejected by the spec validator;
- direct `cinterop` use without `--native-relocs`: rejected by the compiler;
- modified `native.o`: rejected by manifest hash verification;
- edited wrapper source with the old manifest: rejected as stale.

Regression results:

- `python3 tests/module_test.py`: 106 passed, 0 failed.
- `python3 tests/elf_test.py`: pass.
- `python3 tests/run_fork.py --smoke`: 1544 passed, 0 failed.
- A normal non-native program compiled by the seed and by the new compiler is
  byte-identical.
- The new compiler builds itself byte-identically (fixed point).
- Product peak: seed `1,988,576` bytes; new compiler `1,989,008` bytes;
  delta `+432` bytes, below the approved `2,500,000` cap. No cap increase and
  no check weakening.

### 14.4 What is still missing

This is a real scalar/link prototype, not the full generic backend:

- The compiler still emits a fixed `ET_EXEC`; the tool extracts its load
  regions into an object. A production backend should emit a real relocatable
  object with sections/relocations directly.
- The startup is the prototype synthetic-stack plus host `crt1.o` bridge.
  Production needs a supported `argc/argv/envp`, constructor, TLS, IFUNC and
  termination contract.
- Ownership/effect mapping for `PGconn`, `PGresult`, `malloc`, borrowed bytes
  and automatic `PQfinish`/`PQclear` is designed but not implemented.
- No callbacks, variadics, structs by value, TLS, floats, decimals or
  binary-only module import.
- Hash checks are provenance/corruption checks, not memory-safety proofs.
- No per-library compiler edits and no PQ-specific compiler code exist; a
  second independent scalar library (the libpq probe) used the same seam.

Status: **stop at the prototype checkpoint for separate architecture/security
review.** Not merged, pushed, promoted or seed-copied.

## 15. Open questions and review focus

1. Should production emit a relocatable object directly, or keep a separate
   link tool that consumes an image plus relocation sidecar?
2. Is the `cinterop("symbol", args...)` builtin acceptable as the minimal
   generic seam, or should the stable design use a declaration form that does
   not look variadic?
3. Where should the native entry path live so it shares the ordinary call-site
   register save/restore exactly, without duplicating `callFnWith`?
4. What is the production startup contract for `argc/argv/envp`, constructors,
   TLS, IFUNC and `atexit`, given that `rt_finish` currently exits by syscall?
5. How should a foreign-resource finalizer be represented so record drop can
   call `PQfinish`/`PQclear` exactly once, with double-close safety and no
   undocumented destructor?
6. How are effect contracts checked against generated wrappers, and should a
   native wrapper be allowed to claim `pure` at all in the first stable
   version?
7. What exact manifest signature/hash chain is required for reproducible
   supply-chain review, beyond accidental-corruption hashes?
8. When, if ever, should a binary-only module import be supported? This
   proposal deliberately does not pretend it exists.
