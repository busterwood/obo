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
toupper exit abort` and the `obx_*` helpers of rt.h (`obx_errno`, `obx_stat`,
`obx_open`, ...). `strerror` has glibc's texts. Of libm it has the functions
whose result is exact, so they match glibc bit for bit: `fabs copysign sqrt
trunc floor ceil round rint fmod`. A program that calls any other extern
(libm's `pow`, `exp`, `sin`, `hypot`, ..., or any library but `"c"`) is
refused in static mode: `native: unsupported: extern C function 'hypot'
needs dynamic linking`. Phase 5 adds dynamic linking for those.

Floats: `rt_float_to_str` (`%.15g`, `%.16g` or `%.17g`, the first that reads
back, as rt.h) and `rt_parse_float` (`strtod` over the whole string) are exact
conversions with big integers of 32-bit words (round half to even, subnormals,
overflow to inf, hex floats, inf/nan words), our own code in rt.c. They were
checked against glibc's `printf`/`strtod` on 300,000 random doubles (all
bit patterns, near-1 exponents, subnormals) and decimal strings, and the libm
subset on 2,000,000 random inputs each, with a throwaway harness.

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

Phase 1 is simple and correct first (`x64gen.obx`):

- every local and parameter has a stack slot (`rbp`-relative); slots of a
  block are reused after it ends; temporaries are slots that live until the
  end of their statement;
- an expression is evaluated into `rax` (integers, pointers, bool), `rax:rdx`
  (128-bit), `xmm0` (double), or, for a struct, `rax` holds its address
  (a temporary slot for call results and compound literals);
- a binary operator spills its left operand to a temporary slot (not when
  the right one is a constant or a local, which is loaded into `rcx`); a call
  evaluates its arguments left to right into temporary slots, then loads the
  registers and copies the stack arguments; `rsp` stays fixed in the body
  (the frame size and the outgoing-argument area are patched into the
  prologue);
- only caller-saved registers are used (`rax rcx rdx rsi rdi r8-r11`,
  `xmm0-xmm7`), so no register is saved but `rbp`;
- the prologue touches the frame a page at a time from the top (a probe
  loop on `r11`), so that a frame larger than the guard gap faults at the
  guard page, which the runtime reports as `obx: stack overflow`, instead of
  jumping past it into other memory (the C build compiled by gcc without
  `-fstack-clash-protection` can do the latter: red-team `so_big_frames`);
- conditions compile to jumps (`&&`, `||`, `!` and comparisons as branches);
  128-bit add, sub, mul, shifts and compares are inline, 128-bit division and
  the conversions between doubles and 64/128-bit unsigned or 128-bit integers
  call `rt_n_*` helpers;
- constant initializers of globals are evaluated at build time (128-bit
  constants, addresses of globals, functions and strings as data
  relocations); `const` globals go to rodata.

The code generator reads only the IR, so a register allocator and a peephole
pass (phase 4/5) can be added without touching the front end.

## 5. ELF layout

A static `ET_EXEC`, no interpreter, no dynamic section:

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

## 6. Modules

| File | Contents |
|---|---|
| `compiler/native/obxrt.h` | the runtime interface (parsed by the backend, included by rt.c) |
| `compiler/native/rt.c`, `rt.ld`, `strerror.inc` | the freestanding runtime |
| `compiler/gen_rtx.py` | builds the runtime, writes `rtx.obx` |
| `compiler/rtx.obx` | generated: runtime bytes, symbols, header |
| `compiler/ir.obx` | the IR (section 1b) |
| `compiler/cfront.obx` | the C front end: lexer, parser, C's types and conversions, into the IR |
| `compiler/x64gen.obx` | the x86-64 target: code generation, ABI, frames, the link |
| `compiler/x64.obx` | the x86-64 encoder (`obxc --encode-test`) |
| `compiler/elf.obx` | the ELF writer |
| `compiler/main.obx` | `--native`: `x64gen.build(cfront.read(c))`, writes the file, `--run` |

## 7. Tests

- `tools/x64_encode_test.py`: random instructions through the encoder
  (`obxc --encode-test`) and through `as`; the bytes must be equal.
- `tests/run_native.py [--lib] [--redteam]`: the differential runner. For every run test
  (with `--lib` the library tests too, with `--redteam` the programs of
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

2026-11-02, end of phase 1 and of phase 2 (and most of phase 3):

- `tools/x64_encode_test.py`: every encoder form identical to `as` (4 seeds
  of 10,000 random instructions).
- `tests/run_native.py --lib --redteam`: 1320 pass, 0 differ, 1 unsupported
  of 1321 (all run tests, library tests and accepted red-team programs; 516
  red-team programs are rejected by the compiler and skipped). The one
  unsupported calls libm's `hypot` (phase 5). Without `--redteam`: 346 of 346.
  The whole run takes about 25 s on 16 cores; `--lib` alone about 3 s.
- Native self-build (`tools/native_selfbuild.sh`, 1.5 s): `out/obxc
  main.obx --native` builds stage 2 (2.6 MB static ELF, 0.3 s), stage 2
  builds stage 3 natively, and the two are byte-identical (stage 4 too);
  stage 2's `--emit-c` equals out/obxc's. With stage 2 as `OBXC`,
  `tests/run.py` passes 629 of 629 and `run_native.py --lib` 345/0/1.
  Stage 2 is about 2.4 times slower than the gcc -O2 build (`--emit-c` of the
  compiler: 0.26 s against 0.11 s).
- Not done: `--emit-asm`; no optimization at all (code is about what
  `gcc -O0` makes); `native_selfbuild.sh` is not in `gate.sh` yet.

Next, in order:

1. Performance: a peephole pass and register allocation for locals (the IR
   already separates the code generator from the front end).
2. Phase 5: dynamic linking for the rest of libm (`hypot`, `pow`, `exp`,
   ...) and other C libraries (sqlite3, pq).
3. After ref is retired: check.obx produces the IR directly (section 1a) and
   cfront goes away.

Phases:

- Phase 1: foundation and first programs. Done.
- Phase 2: integers, control flow, calls: all scalar tests. Done.
- Phase 3: records, arrays, str, drop glue, closures, generic instances,
  interfaces. Passing in the run and library tests.
- Phase 4: the whole suite; native self-build (the natively built compiler
  builds itself; stage 2 == stage 3). Done (no performance work yet).
- Phase 5: dynamic linking (PLT/GOT, `.dynamic`; libc, libm, libsqlite3,
  libpq externs).
