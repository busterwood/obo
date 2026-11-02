# The native x86-64 backend

Stage 5 of [../PLAN.md](../PLAN.md): the self-hosted compiler writes x86-64
Linux ELF executables itself. No assembler, linker or C compiler runs when a
program is built natively:

    obxc file.obx --native [-o out] [--run] [-- args]

The C backend (`--emit-c`, gcc) stays as it is and stays byte-identical to
`ref/`. The native backend is new modules beside it.

## 1. Input: the generated C, read into the IR

The backend's input is the C text that `emit` already produces (option (a) of
the stage 5 brief), read by a small C front end (`cfront.obx`) into the
backend's IR (`ir.obx`), not an IR produced by `check`/`emit` directly
(option (b)). Section 1a says why this is a step and what replaces it.

Why: the generated C is a narrow and very regular language. A survey of the C
for every run test, every library test and the compiler itself (114,000
lines, 2026-11-02) found only:

- top level: `typedef struct S S;`, function-pointer typedefs, `struct`
  definitions (records; closed types as `{ int64_t tag; union { struct {...}
  V_x; ... char _e; } u; }`), string literal blocks and their `obx_str`
  constants, `extern T x_name(...) __asm__("name");`, constant tables
  (`static const T k_d[N] = {...}` and their slice), globals, prototypes,
  function definitions and `int main(int argc, char** argv)`.
- statements: declarations with an initializer, expression statements,
  `if`/`else if`/`else`, `while (1)`, `for (int64_t i = a; i < n; i++)` (and
  the `i--` form of the drop glue), `break`, `continue`, `return`, blocks,
  and `switch` on a tag with `case N: ...; break;` (only in glue). No `goto`,
  no `do`, no labels.
- expressions: calls (direct, and through a function pointer:
  `((fn3)c.fn)(c.env, x)`), casts, compound literals with designated
  initializers (`((S){.tag = 1, .u.V_Err = {.f_0 = t9}})`), `.`, `->`, `[]`,
  unary `& * - ! ~`, the binary operators, `?:`, `sizeof(type)` and
  `sizeof(*p)`, `++`/`--` in `for`, `=`, and literals (`12LL`, `7ULL`,
  `((int32_t)0)`, Python-`repr` doubles, strings with octal escapes).
- types: the `<stdint.h>` integers, `bool`, `char`, `double`, `void`,
  `__int128`/`unsigned __int128`, `size_t`, pointers, structs, unions, arrays
  inside structs, and the runtime's `obx_str`, `obx_arr`, `obx_slice`,
  `obx_clo`, `obx_sblk`, `rt_dfn`.

Reading that C costs a lexer and a recursive-descent parser for one fixed
subset (with C's types, integer promotions and struct layout, which the
backend needs anyway to match gcc's ABI). In exchange:

- `check` and `emit` are untouched, so the byte-identity gate with `ref/`
  (`run_compare.py`) cannot be disturbed by the backend, and the backend can
  be developed and tested without touching the 5,000 lines that decide moves,
  drops and check order.
- Every decision that is observable (when a value is dropped, which check
  runs first, which runtime call reports an error) is already made in the C,
  and the differential test compares the native binary with the gcc binary of
  the same C. A native/gcc difference is then a backend bug by construction.
- The C is already written in evaluation order: `check` hoists every
  operation that can trap into its own statement when the order matters (the
  `rt4_k02*` trap-order tests), so left-to-right evaluation is always right.

A construct outside the subset is a clean error, `native: unsupported: ...`,
with the C line, never a guess.

Option (b) would give a cleaner path (no reparsing, types known) but means a
second output path through `check` (4,300 lines of templates with holes) that
must keep `--emit-c` byte-identical to `ref/` while `ref/` is the reference.

## 1a. C as IR is a step, not the end state

Once `ref/` is retired (after phase 4: the native self-build works, and native
output can be checked against the C backend instead of against ref),
`check.obx` will produce the IR directly, and both backends, C and x86-64,
will be generated from it. The C reader then goes away.

So `cfront.obx` stays a thin front end whose only job is to build the IR:

- the IR (`ir.obx`) has no C syntax in it: types with sizes, alignments and
  layouts; functions, globals and constant initializers; typed expression and
  statement trees in which every implicit conversion of C (integer
  promotions, the usual arithmetic conversions, assignment and argument
  conversions, array and function decay, pointer arithmetic) is an explicit
  node (`E_CAST`, `E_DECAY`, `E_PADD`);
- nothing after `cfront` may depend on C syntax or C-specific quirks (typedef
  names, declarator forms, literal suffixes, `sizeof`, implicit conversions):
  the code generator reads only the IR;
- the C idioms the IR still mirrors are plain imperative constructs that
  `check` can produce as well (`while`/`for`/`switch` with `case`, compound
  values built from offset/value entries, closures as an `{fn, env}` pair).

## 1b. The seams

Code generation sits behind three boundaries, so that a second target (arm64,
or another object format) can be added without touching the rest:

| Seam | Owner | Contents | Knows nothing of |
|---|---|---|---|
| front end -> IR | `cfront.obx` (later `check.obx`) | parsing, C's conversion rules | targets |
| IR | `ir.obx` | the target-independent program: types and layouts, functions, globals, typed trees | registers, instructions, encodings, C syntax |
| target layer | `x64gen.obx` + `x64.obx` | instruction selection, the SysV ABI and calling convention, the register set, frames, the link with the runtime (`x64gen`); the instruction encoder (`x64`) | C, the executable format's bytes |
| executable writer | `elf.obx` | ELF headers and segments from (address, flags, bytes) | instructions |

The IR is the main seam. There is no obx interface with dynamic dispatch for
a single target: when a second target exists, the target layer becomes a
generic over a `Target` interface, monomorphized. Struct layout is computed
in the IR from the ABI's rules (gcc's, which arm64 Linux shares for the types
the IR has); a target with other rules would supply its layout to the front
end.

## 2. Runtime

`compiler/native/rt.c` is `ref/rt.h` without libc: freestanding C, raw
Linux syscalls. `gen_rtx.py` compiles it with gcc (`-ffreestanding
-nostdlib -fno-pic`), links it with `ld` at fixed addresses
(`native/rt.ld`) together with libgcc (128-bit division and conversions),
and writes `compiler/rtx.obx`: the code and data bytes, the bss size, the
global symbols with their addresses, and the interface header. `rtx.obx` is
checked in, so the seed build still needs only gcc; run `gen_rtx.py` after
changing the runtime (the bytes depend on the gcc version).

The contract (`native/obxrt.h`) is every `rt_*` function the generated C
calls, with rt.h's signatures, plus a few `rt_n_*` helpers for the code
generator (128-bit division, conversions between doubles and 64/128-bit
integers, the entry). The native backend parses this header in place of
rt.h; rt.c includes it, so gcc checks that the definitions match.

Observable behaviour is that of rt.h:

- `memcpy`/`memset` use `rep movsb`/`rep stosb` past 64 bytes, and a large
  block (its own mapping) grows with `mremap`, so a growing buffer is not
  copied;
- stdout through the same 64 KiB buffer, flushed at exit, before a trap and
  before any write to another descriptor; stderr unbuffered;
- traps print `obx: <msg> at <where>` on stderr and exit with the code
  (`rt_trap`, `rt_panic`), `rt_exit` flushes first; `exit()` from an extern
  does not flush (as libc's `exit` does not flush the runtime's buffer);
- refcounted `str` blocks, the drop-action stack, `rt_ddefer`;
- `OBX_CHECK=1`: live objects counted in `rt_alloc`/`rt_free` and the first
  `rt_arr_push`, `obx: leak: N live objects at exit at runtime`, exit 5;
- stack overflow: an alternate signal stack and a `SIGSEGV` handler
  installed with `rt_sigaction` (with `SA_RESTORER` and a restorer that calls
  `rt_sigreturn`); a fault within the stack limit (+1 MiB) of the stack top
  prints `obx: stack overflow` and exits 4;
- args and environment from the initial stack (`rt_n_start`).

The heap is the runtime's own: size classes (16-byte steps to 128, then
+25% steps to 64 KiB) carved from 1 MiB `mmap` chunks with one free list per
class; larger blocks are their own mappings. Every block has a 16-byte
header, so blocks are 16-byte aligned as glibc's are.

The runtime also provides the part of libc that `lib/` and the tests declare
as externs: `close opendir readdir closedir mkdir rmdir unlink rename symlink
chmod chdir getpid getenv setenv unsetenv usleep time strerror system mkdtemp
malloc free strdup strlen strcmp strncmp strchr strrchr strstr abs labs atoi
toupper exit abort` and the `obx_*` and `obxlib_*` helpers of rt.h
(`obx_errno`, `obx_stat`, `obx_open`, `obxlib_readlink`, `obxlib_isatty`,
...). `obxlib_flush_out` reports the first failed write of the stdout buffer
(both runtimes remember it). `strerror` has glibc's texts. Of libm it has the functions
whose result is exact, so they match glibc bit for bit: `fabs copysign sqrt
trunc floor ceil round rint fmod`. A program that calls any other C function
(libm's `pow`, `exp`, `sin`, `hypot`, ..., or any library but `"c"`) is
dynamically linked (section 5a), and then uses libc's versions of all of them.

Floats: `rt_float_to_str` (`%.15g`, `%.16g` or `%.17g`, the first that reads
back, as rt.h) and `rt_parse_float` (`strtod` over the whole string) are exact
conversions with big integers of 32-bit words (round half to even, subnormals,
overflow to inf, hex floats, inf/nan words), our own code in rt.c. They were
checked against glibc's `printf`/`strtod` on 300,000 random doubles (all
bit patterns, near-1 exponents, subnormals) and decimal strings, and the libm
subset on 2,000,000 random inputs each, with a throwaway harness.

In a dynamically linked program (section 5a) the runtime is the same bytes, but
the dynamic linker fills `rt_n_libc` with libc's `exit`, `getenv` and
`__errno_location`: the runtime then ends the process through libc's `exit`
(after flushing its own buffer, as rt.h's `exit(code)` does), reads the
environment through libc and keeps errno in libc's. In a static program the
table is zero and the runtime's own versions serve. Every exit of rt.h goes
through `rt_quit` (trap, panic, leak report, `rt_exit`, the end of main); the
stack-overflow handler, `abort` and a failed `exec` in `system` exit directly
(`_exit`), as in rt.h.

## 3. ABI

The SysV x86-64 calling convention and gcc's struct layout everywhere: in
generated code, between generated code and the runtime, and (phase 5) to C
libraries. A value has the same bytes as in the C backend:

- integers in registers are kept normalized to 64 bits (sign- or
  zero-extended from their C type); results of calls returning narrow types
  are normalized after the call, since the ABI leaves the upper bits
  undefined;
- structs: size and alignment as gcc (`__int128` is 16-aligned); arguments
  and results classified per eightbyte (INTEGER/SSE/MEMORY); a struct over 16
  bytes is passed on the stack and returned through a hidden pointer in
  `rdi`; an argument that needs two registers when only one is left goes to
  the stack whole;
- `obx_str` (16 bytes) is passed in two integer registers and returned in
  `rax:rdx`; `__int128` likewise; `double` in `xmm` registers.

## 4. Code generation

`x64gen.obx` is a one-pass tree walker over the IR, with a few local
optimizations (stage 5, the performance step):

- every local and parameter has a stack slot (`rbp`-relative); slots of a
  block are reused after it ends; temporaries are slots that live until the
  end of their statement;
- except the integer and pointer locals and parameters whose address is not
  taken (no `&x`, no initializer list): before a function is generated, a
  scan counts the uses of each local, a use inside a loop 8 times one
  outside (nested loops more), and the five heaviest that are named three
  times or in a loop live in `rbx r12 r13 r14 r15` (callee-saved: saved to
  frame slots in the prologue and restored in every epilogue, so calls keep
  them); such a local holds its value normalized to 64 bits, like a load;
- an expression is evaluated into `rax` (integers, pointers, bool), `rax:rdx`
  (128-bit), `xmm0` (double), or, for a struct, `rax` holds its address
  (a temporary slot for call results and compound literals);
- a binary operator spills its left operand to a temporary slot only when
  both are complex: a constant (32 bits), a local in a register, an 8-byte
  local or member in its slot are used as the instruction's operand
  (immediate, register, memory), and a simple left operand is evaluated after
  a right one that has no effect (no assignment, no call but the inlined
  checks); compares of a register local are done in place;
- a call evaluates its arguments left to right; constants, locals, members
  of locals and addresses of strings and globals are loaded straight into
  their registers at the call, the last other integer argument stays in
  `rax`, the rest go through temporary slots; the stack arguments are
  copied; `rsp` stays fixed in the body (the frame size and the
  outgoing-argument area are patched into the prologue);
- the runtime's hot helpers are generated inline, with the same results,
  trap codes and messages: checked and wrapping arithmetic (`rt_add_int`,
  `rt_wmul_u32`, ...: the operation and `jo`/`jc`/a range test), `rt_idx` and
  `rt_str_byte` (an unsigned compare), `rt_str_dup` and `rt_str_drop` (the
  reference count; `rt_free` at zero), `rt_arr_push` (the store when there is
  room; the runtime grows the array). A failed check jumps to a stub after
  the function that calls `rt_trap` (one stub per code and location);
- conditions compile to jumps (`&&`, `||`, `!`, comparisons and conversions
  to bool as branches); the C front end's `bool c = e; if (!c) break;` (every
  loop condition) and `T r = e; return r;` are generated without the local
  when it is named nowhere else, and an `if` whose body is only `break` or
  `continue` is a single conditional jump; `x = x` (the copies of drop glue)
  generates nothing;
- the assembler's peephole drops a load of the slot the instruction just
  before stored (no label between), or turns it into a register move;
- the prologue touches the frame a page at a time from the top (a probe
  loop on `r11`), so that a frame larger than the guard gap faults at the
  guard page, which the runtime reports as `obx: stack overflow`, instead of
  jumping past it into other memory (the C build compiled by gcc without
  `-fstack-clash-protection` can do the latter: red-team `so_big_frames`);
- 128-bit add, sub, mul, shifts and compares are inline, 128-bit division and
  the conversions between doubles and 64/128-bit unsigned or 128-bit integers
  call `rt_n_*` helpers;
- constant initializers of globals are evaluated at build time (128-bit
  constants, addresses of globals, functions and strings as data
  relocations); `const` globals go to rodata.

`OBX_NATIVE_MAP=file obxc --native ...` writes the address, size and name of
every generated function (the format of perf's `/tmp/perf-PID.map`), for
profiling and disassembly.

## 5. ELF layout

A static `ET_EXEC`, no interpreter, no dynamic section (a dynamically linked
program adds those: section 5a):

| Segment | Address | Contents |
|---|---|---|
| R+X | 0x400000 | ELF header and program headers; runtime code and rodata at 0x401000; program code; program rodata |
| RW | 0x40000000 | runtime data and bss (fixed, as linked) |
| RW | after the runtime's bss | program data and bss |

`PT_GNU_STACK` marks the stack non-executable. The entry point is a stub in
the program code: `mov rdi, rsp; mov esi, main; and rsp, -16; call
rt_n_start`. All addresses are below 2 GiB, so an address is an `imm32`. `rt_n_start` reads argc, argv and the environment from the
initial stack, calls the C `main` (which calls `rt_init`, the obx main, the
global drops and `rt_finish`) and exits with its result.

## 5a. Dynamic linking

A program whose C functions are all in the runtime (section 2), or that has
none, is a static executable as above. A program that calls any other C
function (libm's `pow`, `hypot`, ..., any function of another library) is
dynamically linked, and then imports every C function it calls from the
libraries, also those the runtime has (`strlen`, `getenv`, `mkdir`, ...):
errno, the environment, the heap (`strdup` and `free`) and stdio are then
libc's, as in the C backend, and nothing the program sees mixes the two. Only
the `obx_*` and `obxlib_*` helpers (rt.h's functions, not libc's; x64gen's
`rtHelper`) stay the runtime's. The obx
heap stays the runtime's own; memory the C side allocates is the C side's.

The libraries are those of the C backend: the `lib` names of the externs
(emit's `libs` list), found as `main.obx`'s `libFlags` finds them
(`lib<name>.so` when it is an ELF file, else the first versioned
`lib<name>.so.N`), then libm and libc. Each is read (`elf.readLib`: its
section headers, `.dynsym`, the version definitions and the `DT_SONAME`);
each import is bound to the first library that defines it, with that
library's default version (`hypot@@GLIBC_2.35`, `exp@@GLIBC_2.29`: what a
new link with gcc gets; without version information ld.so would bind the
oldest version). `DT_NEEDED` lists the libraries that define an import, in
that order (as `--as-needed`, Ubuntu's default). A function no library
defines is an error at build time (`native: undefined C function`), as the
link with gcc fails.

The executable then has (`elf.dynamic`, `x64gen.link`):

| Part | Where |
|---|---|
| `PT_PHDR`, `PT_INTERP` (`/lib64/ld-linux-x86-64.so.2`) | the first page, after the program headers |
| `.dynsym`, `.dynstr`, `DT_HASH` (one bucket), `.gnu.version`, `.gnu.version_r`, `.rela.dyn` | after the program rodata (R+X) |
| `.dynamic` (`PT_DYNAMIC`) and the GOT | after the program data (RW) |

`.dynamic` has `DT_NEEDED`s, `DT_HASH`, `DT_STRTAB`, `DT_SYMTAB`,
`DT_STRSZ`, `DT_SYMENT`, `DT_RELA*`, `DT_DEBUG`, `DT_BIND_NOW`,
`DT_FLAGS`=`DF_BIND_NOW`, `DT_FLAGS_1`=`DF_1_NOW` and the version tags. All
relocations are `R_X86_64_GLOB_DAT`, bound eagerly: one GOT entry per import,
and the runtime's `rt_n_libc` slots (in its bss). There is no PLT: a call is
`mov eax, <vector registers used>; call [rip+GOT]` (the `al` count makes a
call to a variadic function such as `printf` correct). No section headers,
no `PT_GNU_RELRO` (the GOT stays writable, a hardening gap; the hooks live in
the runtime's bss, outside any page that could be made read-only).

The entry is crt1's `_start`: `__libc_start_main(main, argc, argv, 0, 0,
rtld_fini, sp)`, which initializes libc, calls the C `main` and then libc's
`exit` with its result, so the order at exit is the C backend's: the obx
buffer is flushed (by `rt_finish`), then the atexit handlers run and stdio is
flushed. Exit codes, traps, `OBX_CHECK=1` and the stack-overflow handler
(installed with raw syscalls, as in a static program) behave as in rt.h.

## 6. Modules

| File | Contents |
|---|---|
| `compiler/native/obxrt.h` | the runtime interface (parsed by the backend, included by rt.c) |
| `compiler/native/rt.c`, `rt.ld`, `strerror.inc` | the freestanding runtime |
| `compiler/gen_rtx.py` | builds the runtime, writes `rtx.obx` |
| `compiler/rtx.obx` | generated: runtime bytes, symbols, header |
| `compiler/ir.obx` | the IR (section 1b) |
| `compiler/cfront.obx` | the C front end: lexer, parser, C's types and conversions, into the IR |
| `compiler/x64gen.obx` | the x86-64 target: code generation, register locals, inlined runtime checks, ABI, frames, the link |
| `compiler/x64.obx` | the x86-64 encoder (`obxc --encode-test`) |
| `compiler/elf.obx` | the ELF writer; the dynamic tables; reading a shared library's symbols |
| `compiler/main.obx` | `--native`: `x64gen.build(cfront.read(c), libs)`, finds and reads the libraries, writes the file, `--run` |

## 7. Tests

- `tools/x64_encode_test.py`: random instructions through the encoder
  (`obxc --encode-test`) and through `as`; the bytes must be equal.
- `tests/run_native.py [--lib] [--redteam]`: the differential runner. For every run test
  and every test of `tests/native/` (C libraries through dynamic linking: libm,
  libc, stdio and exit order, traps and stack overflow in a dynamic program,
  libsqlite3, libpq; `// needs: sqlite3` skips a test when the library is not
  installed), with `--lib` the library tests too, with `--redteam` the programs of
  redteam/, rounds 1-4, that the compiler accepts) it builds the program
  with the C backend (cached by `tools/cbuild.py`) and natively, runs both
  with the same stdin and arguments, with and without `OBX_CHECK=1`, and
  compares stdout, stderr and the exit code. It reports pass, differ and
  unsupported (a clean refusal by the native backend); in the early phases
  unsupported is not a failure, a difference or a crash is. When the two
  differ but only the native run matches the test's directives, the test
  passes with a note (the C build is the one that is wrong).

## 8. Status / next phases

(kept current; the next agent starts here)

2026-11-02, phase 5 (dynamic linking) and the first performance step:

- `tools/x64_encode_test.py`: every encoder form identical to `as` (4 seeds
  of 10,000 random instructions; `[rip+disp]` operands, `imul r, r, imm`).
- `tests/run_native.py --lib`: 355 of 355 (the 346 run and library tests,
  `math_float` with `hypot` now among them, and the 9 of `tests/native/`).
  With `--redteam`: 1330 pass, 0 differ, 0 unsupported (516 red-team
  programs are rejected by the compiler and skipped). `--lib` takes about
  3 s, with `--redteam` about 17 s on 16 cores.
- Dynamic programs checked with `readelf -d`/`ldd`/`LD_DEBUG=bindings`: the
  same `DT_NEEDED` list as gcc's build of the same C (`libsqlite3.so.0` +
  `libc.so.6`; `libpq.so.5` + `libc.so.6`; `libm.so.6` + `libc.so.6`), symbols
  bound to the default versions (`hypot` GLIBC_2.35, `exp` GLIBC_2.29,
  `__libc_start_main` GLIBC_2.34). Programs without such calls, and the
  compiler itself, are static as before.
- Native self-build (`tools/native_selfbuild.sh`, in the quick gate): stage 2
  (2.4 MB static ELF) builds stage 3, identical; stage 2's `--emit-c` equals
  out/obxc's.
- Performance (section 4; best of 5, ms, gcc -O2 build of the same C against
  native):

  | | before | after | gcc -O2 |
  |---|---|---|---|
  | `--emit-c` of the compiler | 265 | 174 | 113 |
  | bench fib | 1080 | 640 | 373 |
  | bench loop | 3272 | 448 | 277 |
  | bench array | 1595 | 301 | 106 |
  | bench alloc | 162 | 86 | 127 |
  | bench lists | 59 | 45 | 37 |
  | bench strings | 72 | 56 | 82 |
  | bench trees | 30 | 24 | 25 |
  | bench tokens | 29 | 19 | 17 |

  What is left is mostly what gcc gets from inlining the runtime's static
  functions into the program (`rt_str_concat`, `rt_arr_push`'s slow path,
  malloc) and from keeping temporaries in registers across a statement.
- The runtime grew by 512 bytes of code for the `rt_n_libc` checks and by
  1.9 KB for the faster `memcpy`/`memset` (inlined into their callers by gcc).
- Not done: `--emit-asm`; no `PT_GNU_RELRO` and no section headers in dynamic
  executables; no lazy binding (not needed).
- Language limits met by the tests (not changed): an extern cannot take a
  function (no `qsort` with an obx comparison: the C ABI has no closures and
  SPEC 12 allows no function types), cannot be variadic (`printf` is declared
  with a fixed list per program, and one C name has one signature per
  program), and there is no null `ptr` literal (the tests get one from
  `strchr("", 1)`).

Next, in order:

1. More performance, if wanted: temporaries in caller-saved registers within
   a statement (`rsi rdi r8-r10` are free between calls), doubles in `xmm`
   registers, `rt_str_concat` and `rt_str_eq` inline, a better choice of
   register locals (live ranges instead of counts). Measure with
   `OBX_NATIVE_MAP` and perf on the self-build.
2. After ref is retired: check.obx produces the IR directly (section 1a) and
   cfront goes away.

Phases:

- Phase 1: foundation and first programs. Done.
- Phase 2: integers, control flow, calls: all scalar tests. Done.
- Phase 3: records, arrays, str, drop glue, closures, generic instances,
  interfaces. Done.
- Phase 4: the whole suite; native self-build (the natively built compiler
  builds itself; stage 2 == stage 3). Done (no performance work yet).
- Phase 5: dynamic linking (GOT, `.dynamic`, symbol versions; libc, libm,
  libsqlite3, libpq externs). Done (section 5a).
- Performance, first step: register locals, inlined runtime checks,
  operand forms, a peephole. Done (section 4).
