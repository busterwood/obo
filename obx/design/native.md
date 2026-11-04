# The native x86-64 backend

Stage 5 of [../PLAN.md](../PLAN.md): the self-hosted compiler writes x86-64
Linux ELF executables itself. No assembler, linker or C compiler runs when a
program is built natively:

    obxc file.obx --native [-o out] [--run] [-- args]

Both backends start from one IR (`ir.obx`): the C backend prints it as C for
gcc (`--emit-c`, the default build), the native backend compiles it to
machine code.

## 1. Pipeline: from check's templates to the IR

    syntax -> sema -> check (templates, tmpl.obx) -> emit (instances, glue) -> lower -> IR
                                                                                 |-> cgen          -> C -> gcc
                                                                                 '-> opt -> x64gen -> ELF

- `check` writes every function once, generic or not, as a template: trees of
  C-shaped expressions and statements (`tmpl.obx`, one arena) with holes for
  what depends on the type arguments (C types, drop and dup glue, callees,
  string literals, interface methods).
- `emit` instantiates the templates from main (a worklist) and fills their
  holes in the order of the C text, so every generated name (`S<n>_`,
  `fn<n>`, `gl<n>_`, `f<n>_`, `sl<n>`) is numbered as before. Glue (drop,
  act, rest, dup, tramp) is written as templates too.
- `lower` builds the IR from the filled templates and makes C's rules
  explicit: the types of literals and names, integer promotions, the usual
  arithmetic conversions, assignment and argument conversions, array and
  function decay, pointer arithmetic, initializers and gcc's struct layout. It
  reads the runtime's interface (`native/obxrt.h`, embedded as `rtx.HEADER`)
  line by line, and reads the flat stream of template lines (`if (c) {`,
  `} else {`, `}`) back into nested statements. A type hole is a C type name
  (`S3_foo*`, `fn5`, `obx_arr`) that lower resolves; the IR has no node for
  C syntax.
- `cgen` prints the IR as C; `opt` inlines small functions in it, `loops`
  optimizes its loops and `x64gen` compiles the result (sections 2-5).

Until 2026-11-03 the native backend read the C text that `emit` printed
(`cfront.obx`, a lexer and parser for the C subset emit writes). Its job,
C's typing rules over a narrow C subset, moved to `lower`, which applies them
to the templates directly. Nothing prints C to read it back any more, and the
gcc build prints the same IR the native backend compiles. The change was
checked with two oracles over every .obx file of the repository (2926):
the new `--emit-c` was byte-identical to cgen's printing of cfront's IR,
and every native executable was byte-identical to the old compiler's.

Every decision that is observable (when a value is dropped, which check runs
first, which runtime call reports an error) is made by `check` and is in the
IR, and the differential test compares the native binary with the gcc binary
of the same IR. A native/gcc difference is then a backend bug. The
templates are already written in evaluation order: `check` hoists every
operation that can trap into its own statement when the order matters (the
`rt4_k02*` trap-order tests), so left-to-right evaluation is always right.

A construct the native backend does not handle is a clean error,
`native: unsupported: ...`, never a guess.

## 1a. The IR

- The IR (`ir.obx`) has no C syntax in it: types with sizes, alignments and
  layouts; functions, globals and constant initializers; typed expression and
  statement trees in which every implicit conversion of C (integer
  promotions, the usual arithmetic conversions, assignment and argument
  conversions, array and function decay, pointer arithmetic) is an explicit
  node (`E_CAST`, `E_DECAY`, `E_PADD`).
- Nothing after `lower` depends on C syntax or C-specific quirks (typedef
  names, declarator forms, literal suffixes, `sizeof`, implicit conversions):
  `x64gen` reads only the IR, and `cgen` writes C from it alone.
- A node is eight ints (64 bytes, one cache line). A string literal's bytes
  and a float constant's value live in the side tables `Ir.strs` and
  `Ir.flts`, named by the node's `v`. The operand shapes of the kinds
  (`ir.KIDS`) drive the tree walks of `opt`, `loops` and `x64gen`.
- The C idioms the IR still mirrors are plain imperative constructs
  (`while`/`for`/`switch` with `case`, compound values built from
  offset/value entries, closures as an `{fn, env}` pair).
- The templates are still C-shaped (C type names, `->`, a flat stream of
  block lines) because that is how `check` was written. A later step could
  give templates typed IR nodes directly and make `lower` a thin builder;
  nothing in the backends would change.

## 1b. The seams

Code generation sits behind these boundaries, so that a second target (arm64,
or another object format) can be added without touching the rest:

| Seam | Owner | Contents | Knows nothing of |
|---|---|---|---|
| front end -> IR | `check.obx` + `emit.obx` (templates and instances) and `lower.obx` (C's typing rules, layout) | moves, drops, check order; conversions | targets |
| IR | `ir.obx` | the target-independent program: types and layouts, functions, globals, typed trees | registers, instructions, encodings, C syntax |
| IR to IR | `opt.obx`, `loops.obx` | inlining, value records in fields, dead functions; array headers out of loops, checks proved by facts (section 4) | targets |
| C target | `cgen.obx` | C declarators, precedence and parentheses, literals, designated initializers | instructions |
| x86-64 target | `x64gen.obx` + `x64.obx` | instruction selection, the SysV ABI and calling convention, the register set, frames, the link with the runtime (`x64gen`); the instruction encoder (`x64`) | C, the executable format's bytes |
| executable writer | `elf.obx` | ELF headers and segments from (address, flags, bytes) | instructions |

The IR is the main seam. There is no obx interface with dynamic dispatch for
a single target: when a second target exists, the target layer becomes a
generic over a `Target` interface, monomorphized. Struct layout is computed
in the IR from the ABI's rules (gcc's, which arm64 Linux shares for the types
the IR has); a target with other rules would supply its layout to the front
end.

## 2. Runtime

`compiler/native/rt.c` is `compiler/rt.h` without libc: freestanding C, raw
Linux syscalls. `gen_rtx.py` compiles it with gcc (`-ffreestanding
-nostdlib -fno-pic`, `-ffunction-sections -fdata-sections`: every function
and every datum in a section of its own; the top-level asm of `rt_n_restore`
and `rt_ctx_swap` in sections of their own too), takes the libgcc members it
needs (128-bit division and conversions) from `libgcc.a` as a linker takes
archive members, and links nothing: it reads the objects (a small ELF64
reader: section headers, `.symtab`, `.rela.*`) and writes
`compiler/rtx.obx`: the sections (kind: code, read-only data, data or bss;
alignment; size; bytes), their relocations (place, type, target section and
addend: `R_X86_64_64`, `PC32` (and `PLT32`, the same here), `32`, `32S`;
any other type, a COMMON symbol or thread-local data is an error), the
global symbols (section and offset) and the interface header. Each object's
sections stay apart (libgcc's `.text`s, the string sections gcc makes per
function, `.rodata.str1.1` of the tables' strings); a relocation against a
local symbol (`.LC0`, a static function, a section) is made relative to the
target section, so the backend needs no local symbols. The backend's link
keeps the sections a program reaches and lays them out (section 5,
"Reachability"). `rtx.obx` is checked in, so the seed build still needs only
gcc; run `gen_rtx.py` after changing the runtime (the bytes depend on the gcc
version).

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
  prints `obx: stack overflow` and exits 4, and so does a fault in the
  guard page of the running task's stack;
- tasks (`lib/task`, SPEC 16): the same code as rt.h's, without the ASan
  annotations. `rt_ctx_swap` (top-level asm) saves the callee-saved
  registers, the MXCSR and x87 control words and `rsp`; stacks are 256 KiB
  `mmap`s with a `PROT_NONE` guard page below (syscalls 9, 10, 11), 64 kept
  for reuse; the drop-action stack and the C string copies are switched per
  task. The code generator's stack probes (frames over 2 KiB, page by page)
  make a task's guard page as safe as the main stack's guard gap. In a
  dynamically linked program a task may call C code on its 256 KiB stack;
- args and environment from the initial stack (`rt_n_start`);
- the clock through the vDSO: `rt_init` finds it from the auxiliary vector
  (`AT_SYSINFO_EHDR`, after the environment on the initial stack, which
  `main`'s `argv` points into in a static and a dynamically linked program
  alike), checks it is a 64-bit x86-64 ELF, reads its dynamic symbols
  (`DT_HASH`, or `DT_GNU_HASH` without it; `DT_VERSYM`/`DT_VERDEF`) and
  keeps `__vdso_clock_gettime` of version `LINUX_2.6` if a first call
  succeeds. Every clock read (`time`, `obx_clock_ns` for lib/time, the event
  loop's timers and deadlines) goes through `rt_clock`: the vDSO function if
  found, else the `clock_gettime` system call (228). Any surprise (no vDSO,
  as with `vdso=0`; another ELF; a symbol or version missing; a failing first
  call) leaves the system call; `OBX_NO_VDSO=1` forces it (for tests; the C
  runtime ignores it). The vDSO code is plain SysV ABI code: it realigns the
  stack itself, uses under 64 bytes of it (also on a task's 256 KiB stack)
  and makes no system call unless the kernel's clocksource cannot be read
  from user space, where it makes the same one. A read costs about 18 ns
  against 240 for the system call (WSL2, clocksource `tsc`).

The heap is the runtime's own: size classes (16-byte steps to 128, then
+25% steps to 64 KiB) carved from 1 MiB `mmap` chunks with one free list per
class; larger blocks are their own mappings. Every block has a 16-byte
header, so blocks are 16-byte aligned as glibc's are. The last large block
freed, up to 32 MiB, is kept (not unmapped) and reused by the next large
`malloc` it fits with at most twice the size asked for: a loop that
allocates and frees one big array (bench sieve) faults its pages in once,
as with glibc's dynamic mmap threshold.

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

In a dynamically linked program (section 5a) the runtime is the same code (the
parts the program reaches, and `rt_n_libc` always), but the dynamic linker fills
`rt_n_libc` with libc's `exit`, `getenv` and
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

Before `x64gen` sees it, the IR goes through `opt.obx` (the fifth
performance step), on the native path only:

- functions are visited callees first (a depth-first walk of the direct
  calls), so a body is inlined with its own calls already inlined; a call
  into a function still being visited (recursion, direct or mutual) stays
  a call;
- a direct call (`E_DECAY` of `E_FUNC`) at the root of a statement (`f(...);`,
  `T d = f(...);`, `x = f(...);` with x a local or a member chain of one,
  `return f(...);`) of a function of the program (not main, not extern, not
  variadic) whose body costs at most `INLINE_LIMIT` = 25 is replaced by the
  body. The cost counts operators, statements and runtime calls 1, calls of
  the program 3, and leaves, casts, decays and blocks nothing. The body is
  first put in its inlined form: every `return` made the last statement of
  its path (an `if` with a return in one branch takes the statements after
  it into the other); a return inside a loop or a switch keeps the function
  out;
- the parameters become new locals declared with the arguments, in order.
  An argument that cannot change before the body ends (a constant, an
  address, a local whose address is never taken and that no later argument
  writes) stands for a parameter the body never writes, and the argument of
  a drop glue function's record parameter (an lvalue) is passed by address
  (`T* p = &x;`, the body's `p.f` becomes `(*p).f`): no copy of the record;
- the result goes where the call's value went. `T d = f(...)` where the body
  returns a local declared at its top (or a parameter) makes that local d
  itself (no copy: the dup glue `T r = x; r.s = rt_str_dup(r.s); return r;`
  becomes `T d = x; d.s = rt_str_dup(d.s);`); `x = g(x)` where g returns its
  parameter, x is not address-taken and no other argument names x, makes
  the parameter x itself (changed in place);
- an argument that is itself a call of a function inlined there is moved
  into a declaration before the statement (and inlined), while the callee
  and the arguments before it are values a call cannot change;
- a function may grow by 20,000 nodes at most;
- after its calls are inlined, a function's local of a value record type
  declared as a copy of a place (`T n = b.nodes[i];`) and otherwise only
  read field by field becomes one local per field read, declared there from
  the place (through a pointer local when the place is not a cheap one: an
  index is evaluated and checked once). A `str` field never read, whose
  count the copy took right after the declaration (`n.s =
  rt_str_dup(n.s);`) and whose only drops are n's, loses the dup and the
  drops. `T m = n;` that moves n is the same record; followed by dups of
  m's fields (dup glue) it is a copy with counts of its own, and both stay
  records. A copy of a
  local is split only when that saves a count: otherwise the fields would
  only move from slots into callee-saved registers, which costs saves;
- functions nothing names any more (every call inlined; counted over the
  bodies and the globals' initializers, a value use included) are dropped
  and not generated.

`x64gen`'s own inlining (below) still applies to the scalar functions left.
`OBX_INLINE=n` sets the limit (0 turns inlining off; the record rewrite
still runs).

Then `loops.obx` (the sixth performance step), on each function that has a
loop (the others are left alone: there is little to gain there, and the pass
costs compile time):

- array headers out of loops: a loop's reads of an array's or slice's data
  pointer or length (`a.data`, `a.len`, `p->data`, `p->len`) whose root
  cannot change in the loop read locals declared just before the loop (or
  one already declared there with the same value: the `end = p->len` of a
  `for` range). The root is a local (an array or slice variable, or a value
  record holding one) or what a pointer parameter points to (a borrowed
  array). It cannot change in the loop when the loop does not assign or
  declare it, take its address (`append(mut &a, ..)`, `mut &a` to a call), or
  for a parameter use the pointer but to read through it; when, in the whole
  function, its address (or the pointer) goes nowhere but straight into calls
  whose results are not pointers (`rt_arr_push`'s result is an element's
  address, not the array's), so no pointer to it outlives a call; and when no
  case label leads into the loop. An alias opt made (`T* q = &l;`, the
  inlined drop glue's record parameter) counts as a use of l; one written,
  taken or passed elsewhere makes l escape. Element stores change elements,
  not the header;
- facts along the statements, in order, for scalar locals whose address is
  never taken: `a < b`, `a <= b`, `a == b` and `a == b + c` (c a constant,
  no overflow) between locals, lower and upper bounds, and the element just
  stored (`*(T*)(d + i*size)` holds local v), from conditions (in the
  branches of an `if`, and after a loop's test), bounds checks that passed
  (`rt_idx(i, n)` leaves `0 <= i < n`), constants, copies, the conversions
  that keep a value, and arithmetic (seventh step): `l = a op b`, checked
  or wrapping, gives l the range of the result (a checked operation that
  did not trap is exact, so its operands' range clipped to the kind; a
  wrapping one only when that range is inside the kind), `l = m + c` and
  `l = m - c` give `l == m + c` and the order (`m < l` for c >= 1), and
  `l = m + s` with s >= 1 gives `m < l`. The length of an array local whose
  address is never taken is a fact-carrying pseudo local (`a.len` reads it):
  `a = array(n, v)` (`rt_arr_fill(n, ..)`) gives it n and the range 0 to
  2^40, a copy `a = b` b's length, and any write of a or of a member of it
  forgets it. A bound or an order goes through up to two facts between
  locals (`j <= n`, `n < len`: `j < len`). A write of a local forgets
  what names it; a store through a pointer or into a local whose address is
  taken, or a call that may write memory, forgets the stored elements; after
  an `if` what holds at the end of every branch that falls through holds; a
  loop starts from what holds before it minus what its body, test and step
  write, and the body of a `for` sees the condition; a switch's case labels
  start from what holds at the switch minus what its body writes. A function
  with a case label anywhere but straight in its switch's block gets none.
  With the facts:
  - `rt_idx(i, n, w)` with `0 <= i < n` known becomes i;
  - a checked `rt_add/sub/mul_K(a, b, w)` whose operands' ranges cannot
    overflow K becomes the wrapping `rt_wadd/wsub/wmul_K(a, b)`, the same
    value (multiplication only for bounds within 2^31);
  - a load of the element just stored (the same pointer local, index local,
    size and type, nothing between that may write memory or those locals),
    whose own bounds check is proved, becomes the stored local;
- an induction variable of a loop (every write in the loop a checked
  `i = i + c` with a constant c >= 1, a checked `i = i + s` with s a local
  the loop does not write and that is at least 1 at the loop's entry (the
  sieve's `j = j + i`), or the `i++` of a `for` whose condition is `i < x`
  and whose body does not write i) keeps the lower bound it has at the
  loop's entry throughout the loop: a checked addition that did not trap
  only grew it, and the `for`'s `i++` runs only after `i < x`, so it cannot
  overflow;
- a counter of such a `for` (x a constant or a local the loop does not
  write; seventh step): a local c written only in the body, by one checked
  `c = c + 1` outside any inner loop, is at most its entry bound plus
  `max(0, hi(x) - lo(i0))` in the loop and after it (each iteration adds at
  most 1, and there are at most that many iterations), so its `+ 1` needs no
  check when that sum is below K's maximum (the sieve's count of primes);
- loads out of loops (eighth step): a scalar (an integer, bool, pointer or
  double) read through a base pointer b, `*b` or a member chain of it with
  no union on the way (`*p_m`, the record pointer of a `mut &R`; then
  `h->items.data`, `h->items.len`, `h->n`), that nothing in the loop may
  write, is read into a local declared just before the loop. The bases are
  the pointer parameters (borrows and owned records), the locals declared
  once to an address (`T* q = &x.f;` from an inlined borrow) that are never
  assigned nor their address taken and whose declaration the loop surely
  passed (no case label between), and the record pointers this rule
  hoists: so `(*p_m)->items.data` takes two rounds, the record pointer and
  then its field. What a loop may write is found by types: a member store
  by (struct, offset, size, type), a store through a pointer or into an
  address-taken local by its type, a call of the program's functions by a
  summary of its body and callees made when first needed (their own locals
  aside; recursion, indirect and extern calls write anything), a runtime
  function by a table of what it writes (nothing, its arguments, frees,
  anything). A pointer to a member exists only if the member's address is
  taken somewhere outside the arguments of runtime functions that keep no
  pointer. Stores overlap the load only through an object that holds the
  loaded one or one of the members on its way.

Nothing in `loops` changes which traps happen, where or in what order: a
check goes only where it cannot fail, and every other operation stays where
it was. `OBX_LOOPS=0` turns the pass off; `OBX_LOOPS=v` prints its counts.

`x64gen.obx` is a one-pass tree walker over the IR, with a few local
optimizations (stage 5, the performance steps). Before a function is
generated, its body is simplified in place (only the native backend sees
the IR; the C is unchanged), repeated on a block while statements drop out:

- `bool b = A; if (b) { [bool c = V;] b = c; }` (the C front end's `&&`;
  `||` with `!b`) becomes `bool b = A && V`, which then compiles to jumps;
- `T t = s;` renames t to s when s is declared earlier in the same block
  (within 16 statements), never address-taken and not named after t's
  statements; for a scalar, also when neither is address-taken and the
  statements (at most 8) naming t assign neither;
- `T t = E; s = t;` and `T t = E; return t;` with t named nowhere else
  become `s = E` and `return E` (scalar T for the return);
- `T t = E; S` with T a scalar of E's type and t named once more, in S, the
  next statement (an expression, a return, a declaration with an
  initializer), becomes S with E in t's place, when t is reached in S through
  operations that evaluate all their operands (calls, arithmetic, casts,
  dereferences, the value of an assignment to a local) and every other value
  S reads is a constant, a string or function address, or a local (or a
  member of one) that is never address-taken and that E does not assign
  (`T t4 = fib(n - 2); T r = rt_add_int(t3, t4)` becomes
  `rt_add_int(t3, fib(n - 2))`);
- use counts (static, per function) are kept exact through these rewrites;
  a walk that meets a node kind it does not know leaves the function as it
  is.

Then:

- small functions of the program (scalar, non-128-bit parameters and
  result, not `main`, not extern) are generated in place of direct calls:
  bodies of up to 16 IR nodes anywhere, up to 40 inside loops, never the
  function being generated or one already being generated in place
  (recursion), at most 3 deep. The arguments are evaluated as for a call and
  assigned to the callee's parameters, which (with its locals) get
  registers or slots of the caller's frame (the register scan follows the
  same calls); `return` jumps to the end of the body. Trap stubs keep the
  callee's messages and `where` strings. A call of such a function whose body
  is only `return x;` used as a condition is generated as a branch on x
  (`isDigit(c)` in an `if` is two compares). An argument `&l` (l a local of
  the pointee's type) for a parameter p that the body only dereferences
  (`*p`, never p alone, never assigned) is not passed: the body's `*p` names
  l itself while it is generated (the nodes are rewritten and put back), so
  l needs no address and can stay in a register (`x.cmp(&y)`);
- every local and parameter has a stack slot (`rbp`-relative); slots of a
  block are reused after it ends; temporaries are slots that live until the
  end of their statement;
- except the integer and pointer locals and parameters whose address is not
  taken (no `&x`, no initializer list), which get registers by live ranges.
  Before a function is generated, a scan numbers its statements (positions;
  a body generated in place is one position) and weighs each local's uses
  (a use inside a loop 8 times one outside, nested loops more). A local's
  range runs from the first to the last position naming it, widened to a
  whole loop when its value can go around the back edge (named in and out of
  the loop, or declared in it without an initializer). A local of a function
  generated in place is live only at the positions of those calls: it is
  set there (parameters at the call, the others at their declarations)
  before it is read. A position is loud when its code may change `rsi rdi
  r8 r9`: a call that is not generated inline, a 128-bit value, an aggregate
  over 128 bytes, a conversion between double and u64. Heaviest first, each
  local takes a register that no local with an overlapping range has: one of
  `rsi rdi r8 r9` when no position of its range is loud, else one of
  `rbx r12 r13 r14 r15` already saved, else (named three times or in a loop)
  the next callee-saved one (saved to a frame slot in the prologue, restored
  in every epilogue; a local whose range has a loud position takes a new one
  when named twice). Not loud for this: the position of the local's own
  declaration with an initializer (its code runs before the local is set),
  and a loud position in the statements running straight into a `return`
  (no jumps or loops between) when the local is not named from there to the
  return (its value is dead; the call is on the way out). Such a local holds
  its value normalized to 64 bits, like a load. A record place named only to
  take a member, its address or its decay (`p->items.data`, `&x.f`) is not
  a copy, so not loud however large the record. Since the eighth step a
  use inside the branches of an `if` or a `switch` weighs half as much per
  level of branches (to four levels): the weights order the locals, so a
  hot loop's locals win over those of its rare branch; whether a new
  callee-saved register pays is still decided on the weights by loop depth
  alone (every path that names the local pays the save);
- a local that gets no register over its whole range (the calls or the
  locals already in `rsi rdi r8 r9` meet it), weighing at least four uses
  outside loops and not one of a body generated in place, may still take
  one of `rsi rdi r8 r9` with its value saved around some statements
  (sites): a statement of a block, or the condition of an `if`. Each loud
  position and each position of a local given the register that meets it
  must lie in a site; for each the innermost site that is allowed is taken
  (at most 8 sites). Before a site the register is stored to the local's
  slot, inside it the local lives in the slot (its uses there name the
  slot), after it the register is loaded again (a narrow local loaded
  normalized, as a load). The choice of register is the one with the least
  cost, the sites' weights (one store and one load each, weighted by their
  loop depth and branches) plus the local's uses inside them, and it is
  taken only if that is at least 32 (two uses outside loops) less than the
  local's weight. A site is not allowed when control leaves it other than
  by falling through to after it (a `break`, `continue` or `return` that
  leaves it, a case label inside it outside a switch inside it), when it is
  in the test of a rotated loop (generated at the bottom) or in the body
  of a loop generated as `rep stos`, or in the then-branch of an `if` that
  is merged into a count (`cmp; sbb`); a pair of statements generated as
  one (a `bool c = e; if (!c)` or `T r = e; return r;`) is not, when
  either has a site;
- an expression is evaluated into `rax` (integers, pointers, bool), `rax:rdx`
  (128-bit), `xmm0` (double), or, for a struct, `rax` holds its address
  (a temporary slot for call results and compound literals);
- a binary operator spills its left operand to a temporary slot only when
  both are complex: a constant (32 bits), a local in a register, an 8-byte
  local or member in its slot are used as the instruction's operand
  (immediate, register, memory), and a simple left operand is evaluated after
  a right one that has no effect (no assignment, no call but the inlined
  checks); compares of a register local are done in place. For `+ * & | ^`
  (and the checked add and multiply) a left operand that needs no code is
  the operand after a right one that does (the right one assigns neither its
  register nor, for memory, anything). When both are complex and the right
  one's code is quiet (no loud node, no call but the inline checks), the left
  one waits in a register of `rsi rdi r8 r9` that no local of the function
  has, instead of a slot. Otherwise (the right one has calls, or no such
  register is left) it waits in a callee-saved register that no local
  holds at that position and no other temporary holds: one already saved,
  or, when the right one is not quiet, the next one, saved from then on
  (its save slot is placed below the frame when the body is done); a slot
  only when none is left, or when a return without restores was already
  generated. For a commutative operation the held value is the operand
  itself (`add rax, r12`). The same holds keep an integer argument while
  the later ones are evaluated (and an argument of a body in place), the
  function pointer of an indirect call (`call r12`), the address an
  assignment stores to, and the operands of the inline `rt_str_byte` and
  `rt_str_eq`;
- an add or subtract (checked or wrapping) or `& | ^` of a simple operand
  and one that needs no code is computed in the register it goes to: a
  register local's declaration or assignment, or the last argument of a
  call (computed when the argument registers are loaded; only arguments
  that need no code follow it): `mov rdi, rbx; sub rdi, 1; jo`;
- lvalues are generated up to their last address arithmetic and used as
  operands (`place`): `a.b.c` through pointers loads `[rax+off]`, `*p` with p
  in a register reads `[reg]`, an element `p + i*size` (size 1, 2, 4, 8) is
  `[p + i*size]` with an `rt_idx` check done on the index's register in
  place, and a simple value stored through a pointer goes straight to the
  operand (an assignment whose value is unused leaves `rax` alone). Since
  the eighth step a scalar `*p`, or a member chain of it, with p a pointer
  local in a register, is itself an operand that needs no code, `[r +
  disp]`: compared, added and indexed with in place (`cmp i, [rbx+0x10];
  jae; mov rcx, [rbx+0x8]; mov rax, [rcx+i*8]` for an element of a
  borrowed record's array), loaded straight into a register and stored to
  with an immediate;
- a local that `loops.obx` declared for a hoisted load (`ir.hoisted`) and
  that gets no register is not kept in a slot: it is dropped, each use
  reads the place again (as before the hoist), and the registers are chosen
  once more without it (one round). Record pointers that other hoisted
  loads read through stay;
- a call evaluates its arguments left to right; constants, locals, members
  of locals and addresses of strings and globals are loaded straight into
  their registers at the call, the last other integer argument stays in
  `rax`, the rest go through temporary slots; the stack arguments are
  copied; `rsp` stays fixed in the body (the frame size and the
  outgoing-argument area are patched into the prologue);
- a record result goes straight to where it is wanted: `T t = f(...)` and an
  aggregate argument that is a call get it in t's slot or the argument's
  temporary (stored from registers, or written by the callee through the
  hidden pointer: the place is new, so nothing reads it during the call);
  `t = f(...)` for an existing local stores a result in registers into t
  after the call. A returned record of whole eightbytes is loaded into
  `rax`/`rdx` from the value itself;
- `str` arguments that are `rt_str_dup(s)` of a local or member whose address
  is never taken and which the later arguments do not assign increment the
  count in order and load the registers from s, without a temporary copy;
- `r = rt_add_int(r, b)` (and sub, checked or wrapping) for a register local
  with its value unused is `add r, b` + `jo` to the same trap stub; `r++` so
  is `add r, 1`;
- a cast between integers needs no code when the operand's normalized value
  is also normalized for the result; a declaration or assignment does not
  normalize a value of its own type again;
- the runtime's hot helpers are generated inline, with the same results,
  trap codes and messages: checked and wrapping arithmetic (`rt_add_int`,
  `rt_wmul_u32`, ...: the operation and `jo`/`jc`/a range test), the
  checked conversions `rt_cvs_*`/`rt_cvu_*` of 64-bit-or-narrower values,
  `rt_div`/`rem`/`shl`/`shr`/`neg_*`, `rt_idx` and
  `rt_str_byte` (an unsigned compare; a `str` in the frame is read in place),
  `rt_str_dup` and `rt_str_drop` (the reference count; `rt_free` at zero),
  `rt_arr_push` (the store when there is room; the runtime grows the array),
  `rt_str_eq` (the lengths: different is false, both zero is true, else the
  runtime compares the bytes). A failed check jumps to a stub after
  the function that calls `rt_trap` (one stub per code, message and
  location);
- loops are rotated: a `while` (the C front end's `while (1) { [bool c = E;]
  if (!c) break; ... }`) and a `for` jump to their test, which is at the
  bottom and jumps back to the body while the loop goes on (one branch per
  iteration); `continue` jumps to the test. The body is placed at a multiple
  of 16 (of 8 when that takes more than 10 bytes), as gcc does: the nops go
  after the jump to the test, so they never run, and a small loop's back
  branch then fetches whole blocks (bench `loop` ran at two cycles per
  iteration where its body started 3 bytes before a 16-byte boundary, one
  aligned). An innermost loop of 64 bytes or less (body, test and back
  branch) is moved further, to a multiple of 16 from which it does not
  cross a 64-byte boundary, so that each iteration runs from one cache line
  (bench sieve's inner loop, 17 bytes from 48 bytes into a line, ran at
  about 1.5 times the cycles);
- conditions compile to jumps (`&&`, `||`, `!`, comparisons and conversions
  to bool as branches); the C front end's `bool c = e; if (!c) break;` (every
  loop condition) and `T r = e; return r;` are generated without the local
  when it is named nowhere else, and an `if` whose body is only `break` or
  `continue` is a single conditional jump; `x = x` (the copies of drop glue)
  generates nothing;
- the assembler's peephole drops a load of the slot the instruction just
  before stored (no label between), or turns it into a register move; a
  narrow store of a normalized value followed by a load of the same type
  from the same place is a register move too (no store-forwarding stall);
  it drops a `jmp` to the label bound right after it and a `jmp` right after
  a `ret` or `jmp`;
- `*p = c` with c an integer constant that a 32-bit immediate holds (after
  its conversions) and the value unused is one `mov [m], imm`; the test of
  a loaded integer or pointer (`if (*p)`, `!*p`) is `cmp [m], 0`;
- a count, `if (v) x = x + 1;` with no else, v a loaded integer, bool or
  pointer or a local (through `!` and conversions to bool), x a local in a
  register and the addition `rt_add_int(x, 1, w)` or `rt_wadd_int(x, 1)`, is
  `cmp v, 1; sbb x, -1` (`adc x, 0` for `!v`), then `jo` to the trap when
  checked: no branch to mispredict (the sieve's count, as gcc makes it). A
  comparison as v keeps its branch;
- a fill, `for (T i = 0; i < n; i++) { *((E*)p + i) = v; }` with i, n, p
  and v locals never address-taken (n and v may be constants) and E an
  integer type (or bool) of 1, 2, 4 or 8 bytes, is `rep stos` (b, w, d or
  q: rcx = n, rax = v, rdi = p, the caller's rdi kept in r11) when
  n >= 64, and the loop otherwise. It is the loop `array(n, v)` inlines to
  once `loops.obx` has hoisted the array's header (gcc makes a memset of
  it);
- the prologue is inserted after the body, when the frame size is known (in
  the same pass as the loops' pads):
  only the callee-saved registers in use are saved (pushed when their slots
  top the frame), `sub rsp` takes the short immediate when it fits, and
  returns leave with `leave` (a function that restores registers jumps to
  one copy of the restores; when there are at most two, each jump is
  replaced by a copy once the body is done);
- the generated code starts at a multiple of 64 and functions at multiples
  of 16 (`int3` between them), so their absolute addresses keep that
  alignment whatever the runtime's size;
- a frame over 2 KiB is touched a page at a time from the top (a probe
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
profiling and disassembly. `OBX_NATIVE_IR=file obxc --native ...` writes the
IR the native backend would compile, after `opt.obx` and `loops.obx`, printed
as C (for reading: which checks remain, what was hoisted), and builds nothing.

## 5. ELF layout

A static `ET_EXEC`, no interpreter, no dynamic section (a dynamically linked
program adds those: section 5a):

| Segment | Address | Contents |
|---|---|---|
| R+X | 0x400000 | ELF header and program headers; the runtime's linked code, then its read-only data, from 0x401000; program code; program rodata |
| RW | 0x40000000 | the runtime's linked data, then its bss (no segment when there are none) |
| RW | after the runtime's bss (page-aligned) | program data and bss |

`PT_GNU_STACK` marks the stack non-executable. The entry point is a stub in
the program code: `mov rdi, rsp; mov esi, main; and rsp, -16; call
rt_n_start`. All addresses are below 2 GiB, so an address is an `imm32`. `rt_n_start` reads argc, argv and the environment from the
initial stack, calls the C `main` (which calls `rt_init`, the obx main, the
global drops and `rt_finish`) and exits with its result.

### Reachability

Only what `main` reaches is generated (`x64gen.genLive`), as a linker with
`--gc-sections` keeps only the sections reached from the entry, with each
function and each global as a section. A function is reached when
generated code names it (a call, or its address: `symFunc`) or the
initializer of a reached global does; a global when generated code names
it (a code relocation) or a reached global's initializer does. The
worklist holds pending functions; between functions the new code
relocations are scanned for globals, and a reached global's initializer is
written to a scratch buffer to find what it names (`link` writes it for
real). Pending functions are generated lowest index first, so the layout
keeps the source's order. Unreached globals get no address in `link`. The
C backend gets the same from gcc, since every generated function is
`static`. An `import` of a module the program uses one function of costs
only that function (`import "regex"` and `regex.quote`: 185,720 bytes to
73,744).

The runtime is linked the same way, by sections (section 2; `x64gen.rtLayout`):
the roots are the sections of the runtime symbols the generated code names
(a call, an address, `rt_n_start` in the entry of a static program) and, in
a dynamically linked program, `rt_n_libc` (the dynamic linker writes it);
a live section's relocations make their target sections live. The live
sections keep `rtx.obx`'s order (gcc's) and their alignments (functions at
multiples of 64, as `-falign-functions=64` asks): code then read-only data
from 0x401000, data then bss from 0x40000000. The runtime's relocations are
applied at the link (`rtBytes`), PC-relative ones against the final layout,
and the generated code's runtime symbols (`SK_RT`) get their addresses from
it. Code padding is `int3`. Everything stays below 2 GiB, so the
relocations' 32-bit fields (and the generated code's `imm32` addresses) hold
the addresses; the link checks each value fits. An empty program links
3,200 bytes of runtime code and read-only data (`rt_init`, the stack-overflow
handler and its restorer, the exit path, `rt_n_start`), hello 3,968, eza
19,072, the HTTP server `cmd/httpd.obx` 36,672, the compiler 26,880, of
the 62,416 linked before. The `OBX_NATIVE_MAP` file lists the linked runtime
functions first (a section's name is the function at its start).
`tests/run_native.py` builds a few small programs (empty, hello, a task, a
dynamically linked one) and checks their sizes and which runtime functions
their maps list. Speed is unchanged (least of 11 interleaved runs, user
mode): the self-build 5,747M instructions against 5,743M (the link's walk of
the runtime's relocations) in 2,460M cycles against 2,502M; eza `-T
/usr/share` 632M instructions in both, 208M cycles against 212M; bench alloc
1,300M, 283M cycles against 291M; bench fib 5,630M, 2,035M against 2,033M.

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
| `compiler/native/obxrt.h` | the runtime interface (read by `lower`, included by rt.c) |
| `compiler/native/rt.c`, `strerror.inc` | the freestanding runtime |
| `compiler/gen_rtx.py` | compiles the runtime, reads its objects (and libgcc's), writes `rtx.obx` |
| `compiler/rtx.obx` | generated: the runtime's sections, relocations, symbols, header |
| `compiler/tmpl.obx` | check's templates: C-shaped trees with holes (section 1) |
| `compiler/lower.obx` | templates into the IR: C's types and conversions, initializers, layout; the runtime header |
| `compiler/ir.obx` | the IR (section 1a) |
| `compiler/cgen.obx` | the C backend: the IR printed as C |
| `compiler/x64gen.obx` | the x86-64 target: code generation, register locals, inlined runtime checks, ABI, frames, the link |
| `compiler/x64.obx` | the x86-64 encoder (`obxc --encode-test`) |
| `compiler/elf.obx` | the ELF writer; the dynamic tables; reading a shared library's symbols |
| `compiler/main.obx` | `compileIr` (check, emit, lower); `--native`: `x64gen.build(ir, libs)`, finds and reads the libraries, writes the file, `--run`; otherwise `cgen.print(ir)` and gcc |

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
- `tests/run_prev.py`: a compiler change against a previous build
  (`OBX_PREV=old/obxc`). With gcc or `--native`, both compilers must reject a
  program alike, or their two binaries must run alike under `OBX_CHECK=1`.
  The programs are the run, error and library tests, the red-team rounds 1-5,
  fresh round-5 programs (`--gen N`) and eza (`--eza`).

## 8. Status / next phases

(kept current; the next agent starts here)

2026-11-04, the thirteenth performance step (branch `perf13`: the
compiler's own data and walks, no change to the code it generates; the
native and C output of the compiler, `cmd/*.obx` and `lib/tests/run/*.obx`
is byte-identical to main's). User-mode figures for the self-build at
`1ccdc03` and after each step (the first two built on `c80b952`, whose
self-build was the same 4668M); cycles are the best of 9-11 interleaved
runs on a loaded machine, where the spread was 50-150M:

| commit | native: instructions; cycles | gcc-built: instructions; cycles |
|---|---|---|
| main `1ccdc03` | 4668M; 2213M | 3985M; 2068M |
| `55a0306` ir.Node 88 to 64 bytes | 4629M; 2079M | 3799M; 1832M |
| `a99a841` aTaken in the counting scan | 4566M; 2088M | 3761M; 1846M |
| `3adb540` walk and scan by `ir.KIDS` | 4525M; 2052M | 3762M; 1845M |
| `1c42dde` one count of uses | 4441M; 1973M | 3706M; 1758M |

- `55a0306`: `ir.Node` loses `f: float` and `s: str` (E_FLT's and E_STR's
  payloads, now in `Ir.flts` and `Ir.strs` by `v`): 88 to 64 bytes. The
  walks touch a third fewer cache lines: 6% of the native compiler's
  cycles, 11% of the gcc-built one's. The native compiler's binary grows
  12 KB (2,494,777 to 2,507,065 bytes); the gcc-built one shrinks 8 KB
  (2,009,112 to 2,000,616 bytes, after all four steps).
- `a99a841`: simplify's whole-program walk marking the address-taken
  locals is gone; the first `countUses` marks them as it visits every
  E_ADDR and E_DECAY.
- `3adb540`: `K_*` and `KIDS` move from `opt` and `loops` into `ir`;
  `x64gen.walk` goes by the table, and `scan` stops at leaves after one
  table load and walks the plain expressions by shape.
- `1c42dde`: `newGen` counted the uses twice, around simplify. Simplify's
  rewrites already adjusted the counts of the locals they touch (boolFold
  now also zeroes the dropped `c`), so the second count is gone; an
  instrumented build found the kept counts equal to a recount on 149
  programs.

Follow-ups: pack `Node`'s fields into i32 (64 to 32 bytes; every reader
and writer of nodes changes, and `v` must stay 64-bit for E_INT, so the
value would move to a side table or a pair of fields); the same side
tables for `sx.Node`'s strings in the front end.

2026-11-04, the ninth performance step (branch `perf9`: the compiler
itself, native toward gcc on the self-build). Measured as for the eighth:
both columns build this branch's sources; "main" is the backend at
`4d3de7a`. User-mode instructions; cycles the least of 5 runs:

| commit | self-build | eza `-T` | fib |
|---|---|---|---|
| main | 5800M; 2385M | 632M; 244M | 5629M; 2037M |
| `fd6f747` | 4617M; 2176M | 594M; 201M | 5629M; 2023M |

The gcc-built compiler runs the self-build in 3,911M: native went from
1.48 to 1.18 times its instructions, and from 1.1-1.2 to 1.05-1.12 times
its wall clock (best of 9: 570 against 509 ms for `--native`, 300 against
286 ms for `--emit-c`). The native compiler's binary went from 2,724,097
to 2,445,569 bytes. The steps:

- `029f0f2`: `||` chains forwarded through the operand evaluated first;
  `p + i*size` folded into the address for loaded bases and any size;
  loads straight into a local's register.
- `0fd068f`: a record literal declared as a local is built in its slot,
  zeroing only the words no field fills; constants and register locals
  stored straight.
- `96accd4`: a str read through a pointer in a register is indexed in
  place; a simple index sum computed straight into `rcx`; no-op moves
  dropped.
- `253c49c`: opt's value records in fields also cover a record read
  through members of its fields (`n.tag.name`): a scalar of a field is a
  copy of its own, a field inside a record field is read from that field's
  local.
- `39b62f5`: a compare reads a register local, or a slot in place (`cmp
  [m], reg`), after evaluating the other side; a string byte loads straight
  into its local's register.
- `fd6f747`: an inlined function taking a str by value, given a copy of a
  place (`m.keys[e].eq(k)` in `Map.find`), reads the place itself when
  nothing can change it meanwhile (the later arguments and the body write
  no memory and call only the runtime's reading functions, and the body
  only reads and drops the copy): the reference count's increment and
  decrement go.

What is left is spread thin: of the 347 functions that take longer native
than gcc's, the 20 worst hold 43% of the difference, the 100 worst 80%. It
is in the instruction mix: register-to-register moves 10% of native's
instructions against 5.4% of gcc's, stores to the stack 8.7% against 2.5%,
compares 15.8% against 10.4%. Those are spills and the same loads and
bounds checks repeated from one statement to the next (`Emitter.fill`
reads `(*p_e)->f_c->f_x->f_nodes` and checks `n` five times in a row).
Peephole steps now gain 0.1 to 0.5% each. Next, in order: reuse within
straight-line code of the loads and checked element addresses a run of
statements repeats (cheaply, in one pass: hoisting them from the loop pass
over all functions cost 10% of compile time for little); then live-range
splitting and fewer callee-saved saves.

2026-11-03, the eighth performance step (branch `perf8`, section 4: the
regex engine's hot loops). The Pike VM's `add` and `step`
(lib/regex.obx) are loops over the fields of a `mut &` record and the
arrays it holds: native code read the record pointer, then the array's
data pointer and length, again for every access, and kept the loops' own
locals in frame slots once any call happened in the function. Measured
with the regex benchmark's driver (`bench/regex/regex_bench.obx`, the
corpus of `gen_corpus.py`, one search a run) and the usual set: all columns
build this branch's sources; "main" is the backend at `dcb0d5b`. User-mode
instructions; least cycles of 7 interleaved runs under `perf stat`. The
machine was not quiet (load average 3 to 12, other agents' gates running):
instruction counts are exact, cycles are the least of the runs, and wall
clock is left out of this table:

| commit | alternation | `\w+` | `(?i)` | email | dates | self-build | eza `-T` | compile |
|---|---|---|---|---|---|---|---|---|
| main | 7679M; 1327M | 5570M; 1172M | 3829M; 860M | 4420M; 862M | 5235M; 1006M | 5665M; 2392M | 632M; 214M | 3342M; 1731M |
| `a15da21` loads out of loops (C) | 6990M; 1159M | 5206M; 1085M | 3706M; 824M | 4114M; 791M | 4783M; 857M | 5504M; 2357M | 632M; 214M | 3640M; 1955M |
| `da60e42` one round of re-reads | 6677M; 1126M | 5039M; 1056M | 3606M; 814M | 3964M; 784M | 4287M; 815M | 5497M; 2312M | 632M; 213M | 3524M; 1842M |
| `e8cf68b` memory operands | 4869M; 983M | 4151M; 1006M | 3215M; 792M | 3157M; 730M | 3292M; 749M | 5420M; 2277M | 631M; 218M | 3538M; 1912M |
| `4366164` sites, branch weights | 4543M; 897M | 3998M; 919M | 3128M; 762M | 3008M; 655M | 3147M; 665M | 5357M; 2268M | 630M; 213M | 3633M; 1959M |
| `b22166b` saves on raw weights | 4543M; 889M | 3998M; 914M | 3128M; 756M | 3008M; 650M | 3147M; 661M | 5388M; 2246M | 632M; 208M | 3649M; 1912M |

(The last row is from a separate run of 9 against `4366164`; "self-build"
is the natively built compiler running `main.obx --native`, "compile" the
gcc-built one doing the same.) The other patterns: errors 1625M; 323M to
1288M; 282M, hostile 197M; 37M to 122M; 28M, the literal 63M to 57M. The
benchmarks of bench.py are unchanged within the noise in instructions and
cycles (lists lost 3% of its cycles at `4366164` and has them back at
`b22166b`).

- `a15da21`: (C) in `loops.obx` reads the record pointer and then its
  arrays' data pointers and lengths once before each loop (section 4). The
  native code generator drops a hoisted local that gets no register (the
  place is read again at each use, as before) and chooses the registers
  again: a slot would only replace one load by another and cost a register
  choice. 9% fewer instructions on the alternation, 13% fewer cycles.
- `da60e42`: one round of that (a second one cost the regex benchmark 2 to
  10% of its instructions), and the walk of what a loop or a callee may
  write stops once it may write anything (the summaries walked 403k nodes
  of the compiler, now 85k): the compile cost of (C) from 9% to 5%.
- `e8cf68b`: a scalar through a pointer in a register is an operand (`cmp
  i, [rbx+0x10]`, `mov rax, [rcx+i*8]` after `mov rcx, [rbx+0x8]`; ten
  instructions before for an element of a borrowed record's array): 27%
  fewer instructions on the alternation, 13% fewer cycles.
- `4366164`: the Pike VM's `add` kept its stack pointer, the thread's
  offset and the capture index in slots because short temporaries of a
  rare copy loop took `rsi rdi r8 r9` and the call in `if !condMatch(..) {
  break }` made every other range loud. Sites (section 4) keep such locals
  in a register with a store before and a load after the statements with
  the calls, uses in branches weigh half per level, and a record place
  under a member is no longer a loud copy: 4 to 11% fewer cycles on the
  regex patterns. The compiler builds 2.7% more instructions (sites,
  their checks, the second register choice).
- `b22166b`: halved weights decided whether a new callee-saved register
  pays, so a borrow named twice in a match arm across a recursive call
  (bench lists' `sum`) went to a slot; the save is decided on the weights
  by loop depth again.

Native against C and Go at `b22166b`. Cycles, the least of 9 interleaved
runs (`perf stat`, the same driver built both ways; via C is unchanged by
this step):

| pattern | main native | native | via C | native/C |
|---|---:|---:|---:|---:|
| `Sherlock Holmes` | 19M | 18M | 9M | 2.0 |
| `Sherlock\|Holmes\|Watson\|Irene` | 1316M | 889M | 611M | 1.46 |
| `\w+` | 1163M | 913M | 613M | 1.49 |
| `(?i)sherlock` | 853M | 756M | 415M | 1.82 |
| email | 856M | 649M | 431M | 1.51 |
| `(?m)^ERROR .*timeout$` | 318M | 281M | 160M | 1.76 |
| dates (captures) | 997M | 660M | 476M | 1.39 |
| `(a*)*b` | 38M | 28M | 18M | 1.56 |

Native went from 1.9 to 2.15 times C's cycles to 1.4 to 1.8 times; it
does not reach C on any pattern. What still separates them, from the
profiles of the alternation (Pike VM `add` 54% of native's cycles, `step`
and `run` 45%; C: `add` 50%, the inlined `execute` 46%, at about half the
samples):

- registers: `add` and `step` have more live values than registers; gcc
  keeps the instruction pointer `&prog.inst[pc]` and the thread lists in
  registers through the dispatch, native holds several in slots (one
  site-free range per local, no splitting at loop boundaries, no
  rematerialization of anything but hoisted loads);
- the dispatch on the instruction's opcode is an if-chain of compares,
  gcc's a jump table; bools are materialized (`setcc; movzx; test`) where
  gcc branches; a multiply by a constant goes through a register (`mov
  edx, 0x28; imul rcx, rdx`);
- the overflow checks of index sums (`jo` after `add` and `imul`) that the
  facts of `loops.obx` cannot prove stay; gcc keeps them too but
  schedules around them;
- the literal is a byte loop in both builds (2x through C, 6x Go's SIMD
  `strings.Index`): a memchr-like runtime search would help both.

Next steps, in order of expected gain: jump tables for dense switches on
an integer; splitting live ranges at loop boundaries (a local in a slot
outside a loop and a register inside); branching on a bool local's
definition instead of materializing it; `imul r, r/m, imm32` and `lea`
for small constant multipliers; a `memchr` for literal prefixes.

The rewrites rest on these arguments (in addition to the sixth and
seventh steps'):

- loads out of loops (C): the load before the loop cannot fault, because a
  base points to an object that is there throughout the loop: a borrow or
  an owned record is never null, and what a borrow reaches is not freed
  while the borrow lives; a local declared once to the address of a place
  that is surely there (a local, a member of such a place, what a base
  points to, an element whose bounds check is in the address), never
  assigned nor its address taken, whose declaration the loop surely passed
  (no case label between); a hoisted record pointer, read through a base
  before the loop and not written in it, so its record is not replaced or
  freed in the loop. The value read is the one every iteration would read,
  because nothing in the loop may write it, judged by over-approximation:
  every store in the loop and in the bodies of the program's functions it
  calls (transitively, made into summaries; recursion, indirect and extern
  calls write anything), and every runtime function except those listed as
  writing nothing, their arguments or freed memory, counts. A store can
  overlap the load only through an object that holds the loaded one or a
  member on its way: by type (a member store by struct, offset and size;
  a store through a pointer by the pointee type; all non-pointer scalars
  are one type, all pointers another, since a closure's environment stores
  pointers as `void*`), and a pointer to a member exists only if the
  member's address or decay is taken somewhere (outside the arguments of a
  runtime function that keeps no pointer). Unions are never loaded through.
  A task switch happens only in a call that may write anything. A load
  cannot trap, so moving it changes no trap; when the loop never runs, the
  value read is unused (`loops_loads`, `loops_loads_trap`, loopgen's
  functions over `mut &` records);
- re-reads (x64gen, `ir.hoisted`): a hoisted local without a register is
  replaced by its place at each use, which reads the same value for the
  reasons above (it is read where the original program read it);
- memory operands: `[r + disp]` names the same bytes the load would have
  read, with r the pointer local's register. The read moves at most later
  within one expression, past code that is pure (`pairSrcC`) or that
  cannot assign r (an assignment whose value may assign r takes the old
  path); calls that may write memory are separate statements in the IR, so
  none lies between. The read cannot fault (r holds a pointer the program
  dereferences there anyway), so trap order is unchanged
  (`x64_mem_operands`);
- sites: a local l in register r with sites S. Before S, `mov [slot], r`;
  inside S, l is its slot (every use names it, l's register is unset while
  S is generated); after S, `mov r, [slot]` (the load of l's type,
  normalized, which sets no flags, so a condition site's compare is still
  live for the jump that follows it). S is entered only at its start: a
  case label in S is inside a switch in S, and a `continue` in S targets a
  loop in S. S is left only by falling through its end: no `break`,
  `continue` or `return` in S leaves it. So every path from the store
  reaches the load, nothing outside S reads l between them, and whatever
  inside S overwrites r (a call, another local given r) is invisible
  outside it. Statements not generated through the statement walker (the
  test of a rotated loop, the body of a fill made `rep stos`, the
  then-branch of a count made `cmp; sbb`, the pairs generated as one) have
  no sites. A trap inside S ends the program, and the trap stubs read no
  local (`x64_spill_sites`, `x64_spill_sites_trap`);
- branch weights and the copies of record places change only which
  locals get registers. A record place under a member, address or decay
  is generated as address arithmetic; the copies, which use `rep movsb`
  (`rsi rdi rcx`), are made where a whole record is consumed (an
  assignment, declaration, argument or return), positions still loud.

Tests: `loops_loads`, `loops_loads_trap`, `x64_mem_operands`,
`x64_spill_sites`, `x64_spill_sites_trap`; loopgen gains functions that
loop over `mut &` records (record pointers, arrays, scalar fields, calls
that write them or not).

2026-11-03, worker processes and signals (branch `c2`, design/concurrency.md
stage C2). `native/rt.c` has rt.h's new helpers with raw system calls:
`signalfd4` in the epoll set for `task.waitSignal` (`RT_SIG_TAG`, level
triggered; the watched signals blocked with `rt_sigprocmask`), and for
`task.workers` the fork, `wait4(-1, WNOHANG)`, `rt_sigtimedwait` (the
kernel's sigset is one 64-bit word, `sigsetsize` 8; the sender's pid is at
offset 16 of the siginfo, `si[4]`, not 12), `kill`, `prctl(PR_SET_PDEATHSIG)`
and `getppid`. The fork is the one place where a dynamically linked program
must not use the raw call: libc keeps the thread's id in its thread
structure (`raise`, `abort`, its recursive locks and `pthread_self`-based
checks use it), and only its `fork` updates that in the child and runs the
`pthread_atfork` handlers (libpq and others register none today, but a
library may). So `rt_n_libc` has two more hooks, x64gen's `HOOKS` `fork`
(5th) and `fflush` (6th, `fflush(NULL)` before the fork, so C stdio
buffered by a library is not written twice): with libc loaded they are
bound and used; a static program has no libc and forks with system call 57.
Either way the child sets `rt_main_tid` again (the fault handler's test that
a fault is on the program's thread: without it a worker's stack overflow
would not be exit 4) and makes a new epoll set. The leak check per
worker compares `rt_live` with its value at the fork. Tests: the
`task_workers_*`, `task_signal_wait` and `net_listen_reuseport` library
tests, which run_native.py compares on both backends; `task_workers_codes`
is dynamically linked (an `extern "c" fn kill`), so it takes the libc fork,
the others the raw one. For the leak check a worker first drops the
globals, as the end of `main` does: the drops are now a function of their
own, `gx_end(void*)`, which `main` calls after the program's entry and
passes to the runtime first (`rt_on_end`, a new obxrt.h entry), in both
backends (emit.obx). The runtime's text grew from 56,816 to 60,240 bytes.

2026-11-03, the seventh performance step (branch `sieve`, section 4: the
sieve of Eratosthenes as fast natively as in plain C). `bench/sieve.obx` is
the program as one would write it: ten rounds of `array(n + 1, true)` with
n = 10,000,000, the inner loop `while j <= n { prime[j] = false; j = j + i }`
from i * i, then a count with `for k in 0..n + 1`. `bench/c/sieve.c` is
the same in plain C (no checks, `gcc -O2`; bench.py's "plain C" column).
All columns build the same sources (this branch's); "main" is the backend
at `f0f7717`. User-mode instructions, the least cycles and the best wall
clock of 11 interleaved runs under `perf stat` (9 for eza and the compiler;
ms, perf's start-up included), on a quiet machine; "compile" is the
gcc-built compiler running `main.obx --native`, "self-build" the natively
built one doing the same:

| | main | sieve |
|---|---|---|
| sieve, native | 3042M; 1002M; 243 | 1414M; 497M; 114 |
| sieve, via C | 1428M; 741M; 168 | (the same) |
| sieve, plain C | 1414M; 508M; 118 | |
| fib | 5630M; 1547M; 334 | 5630M; 1548M; 333 |
| loop | 8000M; 1003M; 216 | 8000M; 1003M; 214 |
| array | 2413M; 409M; 92 | 2413M; 409M; 92 |
| alloc | 1360M; 301M; 67 | 1320M; 282M; 62 |
| lists | 477M; 138M; 32 | 474M; 138M; 32 |
| strings | 966M; 192M; 49 | 961M; 180M; 41 |
| trees | 293M; 78M; 22 | 292M; 78M; 21 |
| tokens | 138M; 48M; 13 | 137M; 47M; 13 |
| self-build | 4821M; 1926M; 487 | 4815M; 1916M; 495 |
| eza `-T` | 634M; 213M; 115 | 633M; 210M; 112 |
| compile | 3160M; 1624M; 423 | 3176M; 1631M; 423 |

Per commit (instructions; least cycles; ms, a separate run of 11, 5 for
the last three columns):

| commit | sieve | alloc | self-build | eza `-T` | compile |
|---|---|---|---|---|---|
| main | 3042M; 1008M; 251 | 1360M; 301M | 4821M; 1947M | 634M; 214M | 3160M; 1624M |
| `839b448` facts (steps, lengths, ranges) | 2156M; 844M; 211 | 1360M; 301M | 4810M; 1932M | 634M; 216M | 3170M; 1644M |
| `8dff271` x64gen (immediates, cmp [m], lines, fills) | 1428M; 792M; 199 | 1340M; 301M | 4807M; 1930M | 633M; 215M | 3171M; 1636M |
| `131dc9d` a large block kept | 1428M; 741M; 163 | 1360M; 341M | 4809M; 1939M | 633M; 217M | 3171M; 1643M |
| `8314a08` large paths out of line | 1428M; 751M; 174 | 1320M; 281M | 4815M; 1937M | 633M; 211M | 3171M; 1628M |
| `c32c312` counters | 1421M; 743M; 168 | 1320M; 282M | 4815M; 1947M | 633M; 211M | 3175M; 1624M |
| `4aee7a0` counts without a branch | 1414M; 518M; 120 | 1320M; 282M | 4815M; 1918M | 633M; 211M | 3176M; 1621M |

- main's native sieve kept the inner loop's bounds check (`j <= n` said
  nothing about `len(prime)`) and its overflow check on `j + i`, filled the
  array a byte at a time through a register, and branched on every element
  of the count. The facts (`839b448`) take both checks out of the inner loop
  (now `mov byte [r8+rsi], 0; add rsi, rdi; cmp rsi, rbx; jle`) and the
  bounds check out of the count: 29% fewer instructions.
- `8dff271`: the fill becomes `rep stosb` (100M stores a run), `false` is
  stored as an immediate, and the 17-byte inner loop, which started 48
  bytes into a cache line and so crossed it, is placed inside one: it ran
  at about 1.5 times the cycles across the line (measured alone, with the
  same instructions).
- `131dc9d`: the native heap unmapped the 10 MB array at the end of each
  round and mapped it again, faulting its pages in each time: 24,000 page
  faults against glibc's 5,000 and about 30 ms of kernel time a run, which
  the wall clock counts and `cycles:u` does not (the cycles' drop here is
  mostly fewer page-walk misses). Keeping the block cost bench alloc 13% of
  its cycles with the same instructions: the large path inside `free` made
  the small-class path take one more branch. `8314a08` moves the large
  paths out of line; alloc is then 3% ahead of main in instructions and 6%
  in cycles.
- `c32c312`: the count's `count + 1` loses its overflow check (it ran only
  at the primes, 6.6M a run).
- `4aee7a0`: the count is `cmp byte [p+k], 1; sbb count, -1`, as gcc's:
  the branch on `prime[k]` was mispredicted at about every prime, a third
  of the remaining cycles.
- Native now runs the plain C program's instruction count (1,414M; gcc
  unrolls the inner loop twice and counts with a pointer, obx does
  neither) in about its cycles (497M against 508M), and is ahead of obx
  via C (1,428M, 741M), where gcc keeps the checks. The other benchmarks,
  eza and the self-build are unchanged within the noise (alloc and strings
  slightly ahead); `--native` of the compiler costs 0.5% more instructions
  (the deeper fact search), no measurable time.

The rewrites rest on these arguments (in addition to the sixth step's):

- variable steps: a checked `j = j + s` either traps or adds s exactly.
  When s is a local the loop does not write (no assignment, increment or
  declaration of it in the test, body or step; it is never address-taken,
  so nothing else writes it) and s >= 1 holds at the loop's entry, s >= 1
  holds at every write of j, so j only grows: it stays at least its entry
  value, the only fact the induction rule gives. Step 0, a negative step, a
  step the loop writes or one whose lower bound is unknown keep j's facts
  forgotten (`loops_step_vary`, `loops_trap_step_neg`,
  `loops_trap_step_written`). The overflow check of `j + s` is removed
  only by the ordinary range rule, so with `j <= n`, n the int maximum, it
  stays and traps where it did (`loops_trap_step_max`);
- array lengths: the length of an array local whose address is never taken
  changes only by an assignment or declaration of the local (or of its
  `len` member: the kill walk treats a member write of the array as a write
  of the length, and `append`, `pop` and `mut &a` take the address, which
  excludes the local). The pseudo local holding its facts is forgotten
  with every such write, as a local is. `rt_arr_fill(n, size, w)` returns
  an array of length n, or traps when n < 0 or n > 2^40 (both runtimes,
  before allocating), so after it 0 <= len <= 2^40 and len == n; a copy
  `a = b` has b's length. A bounds check `rt_idx(j, a.len)` is proved by
  `j <= n` and `len == n + 1` exactly as by `j < len`;
- arithmetic ranges: a checked `l = a op b` that did not trap computed the
  exact result, which lies in the interval of the operands' ranges (sum,
  difference, product of non-negative ranges), clipped to the kind; a
  wrapping one (made only from a proved checked one) equals the exact
  result only when that interval is inside the kind, and is used only
  then. `l = m + c` (exact) gives `l == m + c`, hence m < l for c >= 1; a
  second local defined as the same `m + c` (m unchanged, the fact still
  present) is equal to the first. `l = a + b` with b >= 1 gives a < l. The
  saturated helpers (`satAdd`, `satSub`, `satMul`) never overflow the
  compiler's own arithmetic. Chains of up to two facts (`loL`, `hiL`,
  `below`) only compose these inequalities, with overflow guards;
- counters: in `for (i = i0; i < x; i++)` with i written only by its step,
  x not written in the loop, and c written only in the body by one checked
  `c = c + 1` that is not inside an inner loop: every iteration runs the
  write at most once (no goto; `continue` and `break` only skip it), every
  iteration needs i < x and adds 1 to i, so there are at most
  max(0, x - i0) iterations, and c <= c0 + max(0, hi(x) - lo(i0)) at every
  point of the loop and at each exit. The bound is added with the loop's
  entry facts, which hold after it too (`loops_count`: no iteration,
  continue and break, a counter written twice or in an inner loop, one near
  the int maximum);
- fills: `for (T i = 0; i < n; i++) { *((E*)p + i) = v; }` with i, n, p
  and v locals never address-taken (n and v perhaps constants): no store
  through p can change them, the loop writes only i and the elements, i is
  declared by the loop (out of scope after it), and the body cannot trap.
  For n >= 64 (a signed compare; smaller or negative n runs the loop as
  before) `rep stos` with rcx = n, rax = v, rdi = p and the direction flag
  clear (the ABI's) stores v into p[0] to p[n - 1] in order, the same
  bytes. rax, rcx and r11 hold nothing between statements; rdi is saved in
  r11 and restored (`loops_sieve`: fills of 0, 1, 63, 64, 65 and 4096
  bytes and of ints);
- stores of immediates and memory tests: `mov [m], imm` stores the same
  bytes as the register store of the constant (the place is computed
  first, as before; a constant has no effect); `cmp [m], 0` sets ZF as
  `test` of the loaded value does, and its callers read only ZF;
- counts without a branch: `cmp v, 1` sets CF exactly when v is 0
  (unsigned below 1, at v's size), so `sbb x, -1` computes x + 1 - CF:
  x + 1 when v is not 0, x when it is (`adc x, 0` for `!v`: x + CF). The
  overflow flag of sbb and adc is that of the whole operation, so it is set
  exactly when 1 is added to the int maximum: the `jo` of a checked count
  traps exactly when the branch's `add x, 1; jo` did, at the same `w`, after
  the same evaluation of v (`loops_trap_count_max`, `loops_trap_count_neg`:
  a false element at the maximum adds nothing and does not trap);
- placement: the pads before an innermost loop sit after the jump to its
  test, as before, so they never run;
- the heap's kept block: a large block is its own mapping with its size in
  its header; kept, it is handed out again only for a request it holds
  (and at most twice the size, so little memory is idle), with the header
  unchanged, as a fresh mapping would be. Nothing in either runtime relies
  on a large block being zeroed (`obxlib_alloc` clears its own), task
  stacks are separate mappings, and `OBX_CHECK`'s live count is kept above
  `malloc`, so leak reports are unchanged.

Tests: `tests/run/loops_sieve` (the sieve while/bool, for/byte with
`break`, int zeros with `while i <= n / i`, odd numbers with a `step`
local, one returning its array; n a constant or from the arguments; fills
around 64 elements), `loops_step_vary`, `loops_trap_step_neg`,
`loops_trap_step_max`, `loops_trap_step_oob` (a variable step past the
array's end traps at the same iteration with the same output before it),
`loops_trap_step_written`, `loops_count`, `loops_trap_count_max`,
`loops_trap_count_neg`, on both backends. `tests/loopgen.py` adds loops
stepping by a local (0, -1, 1 to 3, `n % 4`, `n - 6`, huge, from `j`, a
step changed in the loop, starts near the int maximum, bounds past the
array's end) and sieves over a fresh `array(m, v)` (while or for with
`break`, limits inside and outside the array, counts of the elements three
ways); 600 programs per seed while developing, 0 differences.

Not done, and why:

- unrolling (gcc runs the inner loop two stores an iteration): native is
  already at plain C's cycles, and the trap order would need care;
- a count's pointer form (gcc walks a pointer instead of indexing): one
  instruction an element at most;
- a fill of a value other than a constant by bytes with a wider store
  (`rep stosq` of a replicated byte): `rep stosb` is fast-string already;
- the kept large block is one block, not a cache; a program alternating two
  large arrays still maps one of them each round.

Gates at the end of the step:

- `gate.sh quick` and `gate.sh full` PASS, the native self-build at a fixed
  point;
- `run_native.py --lib --redteam` 1549 of 1549, and the same with
  `OBX_INLINE=0` and with `OBX_LOOPS=0`;
- `cmd/eza_gate.py -j 12` 482 passed, 0 failed (72 live-only skipped);
- `run_prev.py --native --lib --redteam --eza --gen 200` against main's
  compiler (`OBX_PREV`, main at `f0f7717`): 2700 same, 0 differ;
- `x64_encode_test.py` (now with `op [m], imm` and `rep stos`) identical
  to as;
- `bench.py -n 11`: sieve 182 ms via C, 127 natively, 135 in plain C;
- the seed at a fixed point on the branch.

`OBX_NATIVE_IR=file obxc --native ...` (section 4) prints the IR after
`loops.obx` as C: the way to see which checks a loop keeps.

2026-11-03, the clock through the vDSO (branch `vdso`, section 2): the
native runtime reads `clock_gettime` through the vDSO, with the system call
as fallback (`OBX_NO_VDSO=1`). `time.monotonicNs()` in a tight loop: 242 ns
before, 18 ns after (C: 19 ns). `bench/tasks.obx`, best of 3, native: a wait
that parks with a deadline 339 to 90 ns (C 109); the batched rows were
paying two clock reads per 50 operations, so start 26.7 to 21.5, wait on a
finished task 19.3 to 13.8, promise complete 16.2 to 11.1; the switch
unchanged (the same cycles; `rt_expired` is kept in line and `rt_mono` out
of line, so a path without a deadline has no call: with both left to gcc, a
yield took 5 more instructions). bench.py unchanged (best of 11). Test:
`lib/tests/run/time_clock.obx` (monotonic, 50 ms sleeps, `date` and an
`OBX_NO_VDSO=1` child agree).

2026-11-03, keyed hashing (branch `hashfrozen`, lib/README.md "Keyed
hashing"): `rt_init` keeps the address of the 16 `AT_RANDOM` bytes from
the auxiliary vector (`rt_auxval`, which the vDSO lookup now shares), and
`obxlib_hash_key(i)` gives word `i` of them (a `getrandom` system call if
the vector has none); the C runtime uses `getauxval(AT_RANDOM)`. No libc in
a static program, as before. std derives its SipHash key from the words.
Two code generation changes for SipHash's rounds: check.obx emits a shift
of a `u64`, `u32` or `byte` by a constant below the width as a C operator
on the cast operand, not `rt_shl_u64` (both backends; gcc then makes
rotates of it), and x64gen emits `rol` for `x << k | x >> (64 - k)` on a
`u64` local (either order). A short-key hash went from 1302M to 1062M
instructions natively. The runtime's text grew to 60,496 bytes. Costs are
in PERFORMANCE.md (maps 6-10% slower, the self-build unchanged).

2026-11-03, the sixth performance step (branch `perf6`, section 4: loops
rotated and aligned, `loops.obx`: array headers out of loops, checks proved
by facts). All columns build the same sources (this branch's at `103ffdf`);
"main" is the backend at `cc9828e`. User-mode instructions, the least
cycles and the best wall clock of 7 interleaved runs under `perf stat` (ms,
perf's start-up included); "compile" is the gcc-built compiler running
`main.obx --native`, "self-build" the natively built one doing the same:

| | main | perf6 |
|---|---|---|
| loop | 11000M; 2007M; 431 | 8000M; 1003M; 213 |
| array | 4615M; 782M; 171 | 2413M; 410M; 91 |
| fib | 5630M; 1548M; 330 | 5630M; 1547M; 328 |
| alloc | 1400M; 301M; 66 | 1360M; 301M; 67 |
| lists | 480M; 139M; 33 | 477M; 139M; 32 |
| strings | 969M; 193M; 49 | 966M; 192M; 49 |
| trees | 293M; 79M; 22 | 293M; 78M; 22 |
| tokens | 139M; 48M; 14 | 138M; 48M; 13 |
| self-build | 4732M; 1884M; 485 | 4673M; 1888M; 482 |
| eza `-T` | 642M; 212M; 113 | 634M; 213M; 113 |
| compile | 2817M; 1433M; 373 | 3069M; 1576M; 405 |

(gcc's builds: loop 267 ms, array 104 ms; PERFORMANCE.md.) Per commit
(instructions; least cycles):

| commit | loop | array | self-build | eza `-T` | compile |
|---|---|---|---|---|---|
| main | 11000M; 2007M | 4615M; 782M | 4732M; 1884M | 642M; 212M | 2817M; 1433M |
| `7f0f466` loops rotated | 10000M; 2006M | 4414M; 787M | 4703M; 1859M | 640M; 214M | 2817M; 1445M |
| `d232715` `loops.obx` | 8000M; 2006M | 2413M; 411M | 4673M; 1879M | 634M; 211M | 3057M; 1550M |
| `bc1092a` loop bodies at 16 | 8000M; 1003M | 2413M; 410M | 4673M; 1888M | 634M; 213M | 3086M; 1576M |
| `c39ec75` one insertion pass | (same code) | | | | 3069M |

- Rotation (the test at the bottom) saves one jump per iteration: 9% of
  loop's instructions, 4% of array's; no cycles, by itself.
- `loops.obx` halves array (the inner loop went from 7 branches to 4: the
  header loads, two bounds checks and the counter's overflow check are
  gone; one bounds check stays, `i < n` with n unrelated to `len(a)`) and
  takes loop's two overflow checks on `i * 2` and `i + 1` (8 instructions
  an iteration). On the compiler it hoists 1,201 headers and proves 174
  bounds checks and 175 overflow checks; on eza 88, 34 and 26. The
  self-build runs 0.6% fewer instructions, eza 1%; their cycles are within
  the noise.
- loop's cycles did not move until its body was aligned: the 8
  instructions ran at 2 cycles an iteration starting 3 bytes before a
  16-byte boundary, and at 1 once aligned (gcc's build, which strength-
  reduces `i * 2`, at 1.25). Native loop and array now beat gcc's builds
  (213 against 267 ms, 91 against 104).
- The cost is compile time with the gcc-built compiler: 9% more
  instructions for `--native` of the compiler (the pass's walks, about 8
  points, and x64gen's work on the hoisted locals). A first version cost
  19%; the pass now skips functions without a loop (a statement-level scan;
  it found 75 of the compiler's overflow checks outside loops, not worth
  their walks), classifies runtime calls once instead of comparing names,
  finds every root a loop may change in one walk instead of one per header,
  takes the induction variables from the kill walk, and merges the facts of
  an `if`'s branches by intersection instead of walking them again. The
  natively built compiler, whose loops the pass speeds up, comes out even.

The rewrites rest on these arguments:

- header locals: an array's header (data, length, capacity) changes only
  by an assignment or declaration of the array (or of a value record
  holding it), or through a pointer to it: `append`, `pop`, a `mut &`
  argument, a closure's environment. In the IR each is an assignment or
  increment of a place rooted at the local, its declaration, or an `&`
  (`E_ADDR`/`E_DECAY`) of such a place. A loop with none of these for a
  root (`W_ROOTS`) cannot change the root itself; a pointer made outside
  the loop could, so the root must not escape anywhere in the function:
  its address goes only straight into call arguments whose results are not
  pointers. Borrows are second class (a callee cannot store one in a record
  or a global) and such a callee cannot return it, so no pointer to the
  root outlives the call. A closure captures by reference (its environment
  holds `&v` in a compound literal, not a call argument), so a captured
  array escapes and keeps its loads (`loops_array_changes`). The aliases
  opt makes for inlined drop glue (`T* q = &l;`) count as uses of l in the
  loop, and l escapes if q is written, taken or passed on. A borrowed
  array parameter p: p is never assigned, taken or passed elsewhere, and
  inside the loop it is only read through (a call given p, such as
  `append(p, x)`, counts as a change). Rule 3 forbids overlapping mutable
  borrows: while the function holds `&` the array is frozen, while it
  holds `mut &` only p changes it. Globals are copy types or set once, so
  no global reaches an array a loop reads;
- task switches: they happen only inside runtime calls. A local lives in
  its task's frame; other tasks get moved values or `Shared` handles,
  never pointers into the frame, so another task cannot change a local's
  header. A `Shared` value is reachable only inside `with`, and a park
  inside `with` traps (exit 13), so no other task runs while a loop reads
  it there (`loops_shared_yield`: two tasks, a `Shared` array, `with` and
  `task.yield` in the loop);
- the hoisted declaration sits right before the loop in its block and runs
  each time the loop is entered: it reads what the first iteration would
  read. Reading a local's header or through a valid parameter has no
  effect and cannot trap, so a loop that runs no iteration loses nothing.
  A case label inside the loop (a jump into it, past the declaration)
  stops the loop's hoisting;
- facts: only locals whose address is never taken are tracked, so only
  their assignments, increments and declarations change them, and each
  forgets the facts that name it. The right side of `l = e` is rewritten
  with what held before the write. The facts are a must-analysis over the
  structured IR: an `if` keeps what holds at the end of every branch that
  falls through (by intersection); a loop's body starts from what held
  before the loop minus everything the loop writes, so it holds at every
  iteration's start, whatever path (`continue`, the bottom) led there, and
  after the loop the same set holds at every exit (`break` included);
  `return` leaves; the IR has no goto, and case labels restart from the
  switch's facts minus what its body writes (a label anywhere else and the
  function gets no facts). A store through a pointer, into a local whose
  address is taken, or a call that may write memory forgets the stored
  elements (`F_ST`); the bounds checks, arithmetic checks, conversions and
  `rt_str_byte` write nothing. The right operands of `&&` and `||` and the
  branches of `?:` teach nothing after them (they may not run). Conditions
  teach only when evaluating them writes nothing;
- a bounds check `rt_idx(i, n, w)` fails exactly when i < 0 or i >= n
  (one unsigned compare). It goes only when `0 <= i` and `i < n` are known
  (n a local, or a constant above i's upper bound) and its index is a local
  or a constant of its type, which then stands for it. The trap it could
  not make is the only thing removed;
- an overflow check: `rt_add/sub/mul_K(a, b, w)` becomes the wrapping
  operation when both operands' ranges are known (constants, or locals with
  bounds inside K) and the interval sum, difference or product lies inside
  K: then the checked and the wrapping operation compute the same value.
  Products need bounds within 2^31, so the compiler's own arithmetic on the
  bounds cannot overflow. The plain forms: under `i < n`, `i + 1` is at most
  n, which is in K; under `i > m`, `i - 1` is at least m. `while i <= n`
  with n the largest int teaches only `i <= n`, so `i + 1` keeps its check
  and traps where it did (`loops_trap_int_max`; `loops_trap_byte` for a
  byte counter);
- induction variables: a variable whose every write in the loop is a
  checked `i = i + c` with a constant c >= 1 either traps or grows, and the
  `i++` of a `for` whose condition is `i < x` (the body not writing i) runs
  only after `i < x`, so it neither overflows nor shrinks i: i stays at
  least its value at the loop's entry, which is the only fact about i the
  body starts with;
- forwarding: after `*(T*)(d + i*size) = v` with d, i and v locals and v of
  type T, a load of the same type, size, pointer local and index local
  whose bounds check is proved reads what was stored, as long as nothing in
  between wrote memory or d, i or v;
- traps, their order and their locations: every check that stays keeps its
  `w` string and its place in the evaluation order; a removed one could not
  fail. `std.Where` arguments are ordinary values, untouched;
- layout: rotation runs the same tests in the same order (each iteration
  starts with the test, reached from the entry and from the end of the
  body); the pads sit after an unconditional jump and before code reached
  only by jumps, so they never run. `insertAll` produces the same bytes as
  the insertions one at a time (checked on the compiler's build);
- the native/C differential checks it all: every test runs with the pass
  on the native side and without it on the C side, and
  `tests/loopgen.py` adds generated array loops (below).

Tests: `tests/run/loops_*.obx`, on both backends: the array changed in the
body (append, reassignment, a `mut &` function that grows or shrinks it, a
closure appending to it, a `for` over a growing array); the index changed
otherwise than by +1 (steps of 2 and 4, down, doubled, reset), the bound
changing in the body, a copy of the index; `while i <= n` with n = max int
(overflow trap, exit 10) and a byte counter (trap at 255); a bounds trap
mid-loop with output before it (exit 3); nested loops, break, continue,
return, slices, an index proved in one branch; a `Shared` array with
`task.yield`; arrays of value records. `tests/loopgen.py` (in the full gate,
200 programs, 7 s): random loops over int and byte arrays, C against native
under `OBX_CHECK=1`; 3,000 programs the same while developing (about half
end in a bounds or overflow trap).

Not done, and why:

- loop versioning (array's remaining check, `i < n` with n unrelated to
  `len(a)`: a copy of the loop without checks when `n <= len(a)` at entry):
  the trap must then come from the checked copy at the same iteration,
  which needs the loop duplicated in the IR; array already beats gcc;
- `str` indexing (`rt_str_byte`): not touched; its checks are inline
  compares already;
- strength reduction (gcc turns loop's `i * 2` into a second counter): loop
  already runs at 1 cycle an iteration;
- functions without loops get no facts (75 overflow checks in the compiler,
  measured: not worth the compile time);
- facts are capped at 64 per point (the oldest go first).

Gates at the end of the step:

- `gate.sh quick` and `gate.sh full` PASS (the full gate now with
  `loopgen.py -n 200`), the native self-build at a fixed point;
- `run_native.py --lib --redteam` 1540 of 1540, and the same with
  `OBX_INLINE=0` and `OBX_INLINE=200`;
- `cmd/eza_gate.py -j 12` 482 passed, 0 failed (72 live-only skipped);
- `run_prev.py --native --lib --redteam --eza --gen 200` against main's
  compiler (`OBX_PREV`, main at `cc9828e`): 2691 same, 0 differ;
- `bench.py -n 11` after the step: loop 266 ms via C, 211 natively; array
  101 and 90;
- the seed at a fixed point on the branch.

2026-11-03, the fifth performance step (branch `perf5`, section 4: `opt.obx`,
inlining in the IR, value records in fields, dead functions). All columns
build the same sources (main's, `03f9822`); "main" is the backend there.
User-mode instructions, the least cycles and the best wall clock of 11
interleaved runs under `perf stat` (ms, perf's start-up included). The
self-build is the natively built compiler running `main.obx --native`:

| | main | perf5 |
|---|---|---|
| native compiler size (bytes) | 2,152,409 | 2,381,865 |
| gcc-built compiler size (bytes) | 1,491,976 | 1,535,256 |
| eza size (bytes) | 274,368 | 290,752 |
| self-build, instructions | 4022M | 3747M |
| self-build, cycles | 1699M | 1496M |
| self-build, ms | 410 | 376 |
| `--emit-c` by the native compiler, instructions | 2374M | 1979M |
| `--emit-c` by the native compiler, cycles | 1034M | 862M |
| `--emit-c` by the native compiler, ms | 267 | 228 |
| `--emit-c` by the gcc-built compiler, ms | 209 | 210 |
| eza `-T`, instructions | 649M | 642M |
| eza `-T`, cycles | 233M | 213M |
| eza `-T`, ms | 117 | 112 |
| fib | 5630M; 1690M; 357 | 5630M; 1547M; 326 |
| loop | 11000M; 2006M; 414 | 11000M; 2006M; 415 |
| array | 4615M; 782M; 168 | 4615M; 782M; 168 |
| alloc | 1420M; 301M; 65 | 1400M; 301M; 65 |
| lists | 537M; 158M; 35 | 480M; 132M; 30 |
| strings | 992M; 211M; 52 | 969M; 205M; 50 |
| trees | 293M; 73M; 20 | 293M; 79M; 21 |
| tokens | 140M; 51M; 14 | 139M; 49M; 13 |

(the benchmarks: instructions; least cycles; best ms)

Per commit (instructions; least cycles of 11):

| commit | self-build | `--emit-c` (native) | eza `-T` | lists |
|---|---|---|---|---|
| main | 4022M; 1699M | 2374M; 1034M | 649M; 233M | 537M; 158M |
| inlining in the IR | 3818M; 1503M | 1983M; 848M | 641M; 213M | 480M; 132M |
| value records in fields | 3783M; 1528M | 1979M; 855M | 642M; 214M | 480M; 132M |
| the kind table in place | 3747M; 1496M | 1979M; 862M | 642M; 213M | 480M; 132M |

Inlining is the step: the natively built compiler runs 17% fewer
instructions and cycles on the same work (`--emit-c`, which does not run
the pass), eza 9% fewer cycles, lists 16% (`push`, a one-line constructor, and the
drop glue of the list it replaces, in place in the loop). The self-build
gains less (7% of its instructions, 12% of its cycles) because it now also
runs the pass, and x64gen compiles the larger IR it leaves (about a third
more nodes; 10% more code). The value records step is small: 0.9% of the
self-build's instructions, eza unchanged. The last commit only makes the pass
itself cheaper (1% of the self-build). The `--emit-c` of the gcc-built
compiler is unchanged in instructions (1580.5M in every column): the C
path does not run the pass.

fib and trees run exactly the same instructions as on main (their code is
the same): fib's 8% fewer cycles and trees' 8% more come from where their
functions now fall against 64-byte fetch blocks (functions that every call
inlined are gone from the code before them). The benchmarks' other gains
are small functions and glue in place (strings, tokens, alloc).

The cost is compile time when the compiler running the pass is the
gcc-built one, whose own code does not get faster: `--native` of the
compiler's sources by out/obxc takes 18% more instructions (2126M to
2516M) and 21% more cycles, of eza 12% more. 4 points of the 18 are the
pass's own walks (limit 0 below); the rest is the inlining itself and x64gen's work on the larger IR.
The natively built compiler, whose code the pass does speed up, compiles
eza in 10% fewer instructions and 17% fewer cycles.

The inlining limit was chosen by measurement (`OBX_INLINE`, the compiler
built natively with each limit; its self-build at the default limit, so the
work is the same; least cycles of 5):

| limit | compiler size | self-build | eza `-T` | eza size | `--native` of the compiler by out/obxc |
|---|---|---|---|---|---|
| 0 (records only) | 2,222,121 | 5125M; 2155M | 649M; 235M | 274,368 | 2203M; 1109M |
| 10 | 2,050,089 | 4538M; 1856M | 665M; 233M | 274,368 | |
| 15 | 2,127,913 | 3902M; 1615M | 659M; 233M | 278,464 | 2342M; 1211M |
| 20 | 2,230,313 | 3827M; 1512M | 650M; 220M | 286,656 | 2437M; 1257M |
| 25 | 2,381,865 | 3783M; 1574M | 642M; 218M | 290,752 | 2516M; 1304M |
| 30 | 2,574,377 | 3751M; 1537M | 639M; 220M | 294,848 | 2628M; 1337M |
| 40 | 2,836,521 | 3721M; 1545M | 638M; 213M | 298,944 | |
| 60 | 3,160,105 | 3644M; 1568M | 633M; 211M | 323,520 | |

(the self-build at limit 0 is the compiler with no inlining in its own code
running the pass at 25.) Up to 25 every step pays: lists from 15, fib's and
eza's cycles from 20, tokens and eza's instructions at 25. Beyond it the
instructions keep falling by about 1% a step, the cycles stay within the
noise (2-3% on this host), and the native compiler grows by 8-10% a step and
its gcc-built compile time by 4%. 25 is the knee.

Where the pass runs, and why there: in the IR, between `lower` and `x64gen`,
on the native path only. Applied to the C path as well it made the C 20%
larger, gcc's build of the compiler 16% slower (14.6 to 17.1 s) and the
gcc-built self-build only 2.7% shorter in instructions: gcc inlines on its
own. Keeping the C as before also keeps the native/C differential an
independent check of the pass (every test, red-team program and eza
listing compares a program with inlining against one without).

The rewrites rest on these arguments (none changes the C):

- evaluation order and ownership: an inlined body is the callee's IR
  statements, copied, and every dup, drop and move in them is an explicit
  statement or call that `check` wrote, so it happens as before. The
  arguments are evaluated in order into new locals before the body, as the
  call evaluated them; the result is stored where the call's value went
  after the body, as the call returned it. An argument stands for its
  parameter (no local) only when the body never writes the parameter
  (`W_WRITES`: no assignment, increment or address of it or a member) and
  the argument cannot change until the body ends: a constant, an address,
  or the value of a local (or a member chain of one) whose address is never
  taken and that no later argument writes. Reading a local has no effect in
  the IR (counts are explicit calls), so reading it twice or never is the
  same. A drop glue function's record parameter, which the body never
  writes (checked as above), is read through `&x` instead of a copy of x's bytes (a move in the IR is a
  copy of the bytes): the same bytes. Borrows are pointers in the IR and are
  passed as values;
- results: `T d = f()` with the body's returned local made d is the same as
  the body building its local and the call copying it to d, since nothing
  reads d before the call returns. `x = g(x)` in place: the body's
  parameter is a copy of x that it changes and returns, after which x is
  assigned it; in place, the changes go to x directly. x is not
  address-taken (no pointer, closure or task sees it), no other argument
  names it, and the body names only its parameters and locals, so nothing
  can tell the difference before the body ends. A trap in the body ends the
  program, so a half-changed x is never seen;
- argument calls moved before the statement: only while everything
  evaluated before them in the statement is a constant, an address or the
  value of a local that is never address-taken. Those cannot trap, call or
  be changed by a call, so the moved call's effects and traps keep their
  order;
- returns dropped: `f(...);` with a body ending in `return E;` keeps E as a
  statement unless E has no effect (no call, assignment or increment), in
  which case E is dropped. A call statement's result owns nothing (`check`
  declares and drops an owned result), so dropping E drops no count;
- value records in fields: the fields are copied from the place at n's
  declaration, all together, where n was copied: the same bytes. Nothing
  writes n (an assignment, increment or address of n or a field makes it
  stay a record; so does any use of n whole), so its fields keep those
  values and a read of `n.f` reads the field's local. A field never read
  loses its dup and drops only when the dup is the statement right after
  the declaration and every drop of the field is n's own: the count went
  up and came back with nothing reading the field in between. The string
  can now be freed earlier, when the source drops it, which nothing can
  observe (a `str` has no drop method). The place is evaluated once (a
  pointer local when it is not a local, global or `*p`), so an index check
  runs once, where it ran. `T m = n;` joins m to n only as a move: a dup
  of m's fields means m has counts of its own (a copy), and both stay
  records. Before this was checked, the copy's one dup was set against
  the drops of both n and m, and removing them left a count behind: a
  by-value receiver read by one field (`e.isCancelled()` on a record
  with heap `str` fields) leaked under `OBX_CHECK=1`. Found when C1b's
  `net_read_cancel` test met the pass; `inline_value_record_args` and
  `tests/vrgen.py` (generated programs, in the full gate) now cover it;
- the leak counter (`OBX_CHECK=1`): no allocation is added or removed. The
  removed pairs are a dup and its drops of a string the source keeps alive,
  the dropped expressions have no calls, and a dropped function is never
  called, so the live-object count at exit is unchanged. `run_native.py`
  runs every test with and without `OBX_CHECK=1`, `run_prev.py` with it;
- traps and their locations: a run-time check's location is the `w`
  string argument `check` wrote at the callee's line; it is copied with the
  node, so an inlined body reports the callee's line, as the call did
  (`inline_trap_location`, `inline_fn_value`). A `Where` (SPEC 15) is an
  argument the caller builds at the call site and is bound like any other
  (`inline_where`: `here()`, a `Where` passed on through two inlined
  functions, and a task trap that names the `with` and the wait);
- stack-overflow probes: inlined locals are slots or registers of the
  caller's frame. x64gen probes a frame over `PROBE_FREE` a page at a time
  as before, so a larger frame cannot step over the guard page, on the main
  stack or a task's. A recursion through a function that inlines others
  uses more stack per level and overflows sooner, with the same message and
  exit code 4 (`inline_deep_nesting`);
- task stacks (`lib/task.obx`, SPEC 16): a task switches stacks only
  inside the runtime (`obxlib_task_yield`, `_wait`, `_join`, `sh_enter`,
  ...), extern functions with no IR body, never inlined. The obx wrappers
  around them may be inlined, but each runtime call stays a call, in its
  order, so no code moves across a switch. The functions handed to the
  runtime as callbacks (a task's body, a result's drop) are values and are
  kept (below). Inlined frames take more of a task's 256 KiB stack, as
  above;
- function pointers and closures: only a direct call (`E_DECAY` of
  `E_FUNC`) is inlined. A call through a value (a closure's `{fn, env}`, a
  field, a parameter, a global) stays a call. A function named as a value
  keeps a count (every `E_FUNC` is counted, in bodies and in the globals'
  initializers, and copies made while inlining count too), so it is still
  generated, with its own calls inlined; closure bodies are functions like
  the others (`inline_fn_value`);
- dead functions: the count of a function falls only when a call of it is
  inlined or a tree naming it is dropped (an unused argument, an unused
  result, the body of a function dropped in turn). main and the runtime's
  functions are kept;
- the native fixed point holds (the natively built compiler builds itself
  identically), and every test, red-team program and eza listing behaves
  the same as with main's compiler (`run_prev.py`).

Not done, and why:

- calls that are not at the root of a statement and cannot be moved
  out of it (after an operand with an effect, inside `&&`, `||` and `?:`):
  they stay calls (x64gen's own inlining of scalar functions still applies
  to them);
- a body with a return inside a loop or a switch is not inlined (its
  returns cannot all be made last statements without a jump the IR does not
  have);
- inlined locals live until the end of the block the call was in, so the
  caller's frame grows (more slots, more stack per recursion level); scoping
  them to a block of their own would let x64gen reuse their slots. It was
  not needed for any measured program;
- records that are written after the copy, or copied from a call's result,
  stay records: splitting the first needs the writes followed per field,
  and splitting the second only moved fields into callee-saved registers
  (eza ran 2% more instructions when it was tried);
- record parameters of functions that are not inlined are still copied
  into the callee (the ABI's by-value records); passing them by address
  when the callee only reads them is an interprocedural change of the
  calling convention;
- the pass on the C path: measured and left off (above);
- loop rotation, invariant loads, bounds-check elimination, doubles in
  `xmm` registers and shrink-wrapping were out of this step's scope.

Gates at the end of the step:

- `gate.sh quick` and `gate.sh full` PASS, the native self-build at a
  fixed point (2,381,865 bytes);
- `run_native.py --lib --redteam` 1517 of 1517 (the five new tests
  included: they are in `tests/run`, so the native/C differential covers
  them);
- `cmd/eza_gate.py -j 12` 482 passed, 0 failed (72 live-only skipped);
- `run_prev.py --native --lib --redteam --eza --gen 200` against main's
  compiler (`OBX_PREV`, main at `03f9822`): 2668 same, 0 differ;
- `tests/vrgen.py`: 1,600 generated value-record programs the same
  through C and natively under `OBX_CHECK=1` (the compiler before the copy
  fix differed on 24 of 60);
- the seed at a fixed point on the branch.

After merging main (`77fae8a`, C1b: epoll, timers, deadlines, `lib/net`)
and the copy fix: `gate.sh full` PASS (with `vrgen.py -n 200`);
`run_native.py --lib --redteam` 1532 of 1532, and the same with
`OBX_INLINE=0` and `OBX_INLINE=200`; `eza_gate.py` 482 passed, 0 failed;
`run_prev.py --native --lib --redteam --eza --gen 200` against main's
compiler: 2683 same, 0 differ.

2026-11-03, the IR from the templates (branch `ir`, section 1): `check`
writes trees (`tmpl.obx`), `emit` fills them per instance, `lower` builds
the IR and both backends start from it; `cfront.obx` is gone. Checks:

- Byte-identical with the old compiler over 2926 .obx files: the C
  (against cgen's printing of cfront's IR) and every native executable
  (1745).
- `tests/run_prev.py` (a differential against a previous compiler's binary)
  against main's compiler: 2642 programs (run, error and library tests,
  red-team rounds 1-5, 200 freshly generated round-5 programs, eza on nine
  listings) the same with gcc and with `--native`.
- `gate.sh full` PASS; `cmd/eza_gate.py -j 12` 482 passed, 0 failed.
- Speed, compiling the compiler (best of 11, s; gcc-built out/obxc):

  | | main | ir |
  |---|---|---|
  | `--emit-c` | 0.122 | 0.186 |
  | `--native` | 0.358 | 0.226 |

  The native build no longer prints and reparses C. The C build now builds
  the IR and prints it, where it printed the templates before: lower and
  cgen take about 20% of the time each.

2026-11-03, the fourth performance step (branch `perf3`, section 4:
temporaries in callee-saved registers, forward substitution, a new register
for a local named twice across a call, functions at multiples of 16, pushed
saves and copied restores, arithmetic into its register). Both columns
build the same sources (those of `4f9b1ad`, where the branch starts); "main"
is the backend there. Best of 5, ms, interleaved; then user-mode instructions
and the least cycles of 10 interleaved runs (perf stat). The self-build is
the natively built compiler running `main.obx --native`:

| | main | perf3 |
|---|---|---|
| native compiler size (bytes) | 2,086,449 | 2,065,969 |
| eza size (bytes) | 270,272 | 270,272 |
| `--emit-c` of the compiler | 257 | 248 |
| self-build (`--native`) | 409 | 389 |
| bench fib | 448 | 349 |
| bench loop | 420 | 419 |
| bench array | 168 | 165 |
| bench alloc | 64 | 64 |
| bench lists | 35 | 33 |
| bench strings | 53 | 52 |
| bench trees | 21 | 20 |
| bench tokens | 12 | 12 |
| eza `-T` | 115 | 115 |
| self-build, instructions | 3938M | 3923M |
| self-build, cycles | 1662M | 1641M |
| eza `-T`, instructions | 659M | 649M |
| eza `-T`, cycles | 235M | 229M |
| fib, instructions | 6127M | 5630M |
| fib, cycles | 2188M | 1690M |

The other benchmarks (instructions; least cycles of 10), main then perf3:
loop 11000M; 2006M and 11000M; 2003M; array 4615M; 784M and 4615M; 781M;
alloc 1460M; 301M and 1420M; 301M; lists 537M; 166M and 537M; 158M;
strings 993M; 212M and 992M; 211M; trees 294M; 85M and 293M; 81M; tokens
146M; 51M and 139M; 51M.

Per commit (instructions; least cycles of 10):

| commit | self-build | eza `-T` | fib |
|---|---|---|---|
| main | 3938M; 1662M | 659M; 235M | 6127M; 2188M |
| temporaries across calls in callee-saved registers | 3929M; 1641M | 651M; 234M | 6458M; 2189M |
| functions at multiples of 16 | 3929M; 1652M | 651M; 232M | 6458M; 1857M |
| arguments, function pointers, store addresses held | 3930M; 1655M | 651M; 235M | 6458M; 1857M |
| pushed saves, copied restores, arithmetic in place | 3923M; 1640M | 649M; 231M | 5630M; 1691M |
| arguments of bodies in place, string checks held | 3923M; 1641M | 649M; 229M | 5630M; 1690M |

fib gains 23% of its cycles (the gcc -O2 build: 312 ms against 357 in the
same `bench.py` run). The first commit alone raises fib's instructions: the
`fib(n - 1)` that waited in a slot now waits in `r12`, which costs a save
and a restore per call, but the slot's store before the call and its load
after it were what the processor waited for (the load waits for the store
through the callee's frame). Its cycles then hung on where fib fell in the
fetch blocks: aligned at 16 it ran 15% faster, unaligned the gain was gone.
The pushed saves, the copied restores and the subtract into `rdi` took the
instructions back below main's. fib is now:

```
push rbp; mov rbp, rsp; push rbx; push r12; mov rbx, rdi
cmp rbx, 2; jge L; mov rax, rbx; mov rbx, [rbp-8]; mov r12, [rbp-16]; leave; ret
L: mov rdi, rbx; sub rdi, 1; jo trap; call fib; mov r12, rax
   mov rdi, rbx; sub rdi, 2; jo trap; call fib; add rax, r12; jo trap
   mov rbx, [rbp-8]; mov r12, [rbp-16]; leave; ret
```

The self-build and eza move by 0.4% and 1.6% in instructions and about 1%
and 2% in cycles: few of their temporaries wait across calls, and what
they spend is in the runtime (malloc, `rt_str_concat`, counts, dup glue).

The new rewrites rest on these arguments (none changes the C):

- temporaries in callee-saved registers (`hold`, `calleeTemp`): a held value
  lives from the hold to its release inside the code of one statement, so
  at one position p, the one the scan saw the node at (`posAt`; a node seen
  at several positions, or not seen, has none, and then only a register no
  local was given is taken). A local given register r is read and written
  only at positions in its range, so when no local with r has p in its
  range, nothing in the window reads r for a local or writes it as one: the
  temporary is a local whose range is [p, p], the argument by which two
  locals with disjoint ranges share a register. Two temporaries never share
  one (`held` lists those taken; they are released in the reverse order).
  The callee-saved registers survive every call, the runtime's and the C
  libraries' included (the ABI), and the generator uses `rbx` and `r12` to
  `r15` for nothing else: no scratch use, no runtime path in place;
- inlined bodies: a node of a body in place has no position of its own (one
  body serves every call), so in it `posAt` is that of the outermost call
  being generated in place. The scan gives the body's locals that position
  (they are counted per call, section 4), so a register of one of them is
  not free there, and a `return` of the body jumps to its end, inside the
  window;
- `&l` aliases: a body in place that writes `*p` writes l's register or
  slot. l is named at the call, so it is live at p and its register is no
  temporary's. `writesReg` asks about locals' registers only and is
  unaffected;
- new saved registers: a hold that will wait across a call (the code it
  waits for is not quiet) may take the next callee-saved register, saved
  from then on: its save slot is placed below the frame after the body, and
  the restores are generated after the body too, so every return made with
  a jump to the restores restores it. A return that left without restores
  (`leave; ret` in a function that had saved nothing yet) would not, so
  after one (`bareRet`) no new register is taken: the value waits in a
  slot;
- the loud/quiet classification: the quiet registers (`rsi rdi r8 r9`) are
  still taken only when the code waited for is quiet, as before. A
  callee-saved register does not depend on it (every call keeps it); the
  classification only decides whether a new register is worth its save;
- `writesReg` and operand order: held values are read only by `heldSrc`
  and `unholdInto`, right after the release, before any other code; a
  commutative operation reads the held register as its operand, which the
  overflow flags allow as before (`add`, `imul`, `mul` do not depend on the
  order);
- a new register for a local named twice: the allocator takes one for a
  local of weight 2 when its range has a loud position (a call keeps the
  value; a slot would be stored and reloaded around it). The conditions on
  sharing are unchanged;
- forward substitution (`forward`): E moves from before S to t's place in
  S. What S evaluates before t's place, and the operations on the path to t
  after it, are what can tell the difference. The path nodes (calls,
  arithmetic, casts, dereferences, the value of an assignment into a local
  or a member chain of one) evaluate all their operands before they act, so
  their traps and calls still follow E's; `&&`, `||`, `?:`, members of t
  and unknown kinds refuse. Every other operand is a constant, a string or
  function address, or a local (or a member chain of one) that is never
  address-taken and that E does not assign: reading it cannot fail and E
  cannot change it, so it reads the same before or after E, and an
  assignment's target is such a local, which E does not assign either. t is
  a scalar of E's type (no conversion at the declaration), never
  address-taken, and named exactly once in S (static use counts), so no
  other code read it. E's node moves rather than being copied (its
  declaration becomes empty), so use counts stay exact;
- arithmetic into its register (`arithInto`): one operand is simple and
  the other needs no code and is not r, so loading the first into r cannot
  change the second (a memory operand is a frame slot, `rbp`-based); the
  operation, the overflow test (the same stub and message as `rt_add_*`,
  `rt_sub_*`) and the normalization are those of rax. On overflow r holds
  the wrapped value only on the trapping path, which does not return. For
  the last call argument the operation is moved to where the argument
  registers are loaded: only arguments that need no code follow it (they
  cannot trap or call), the earlier ones are already evaluated, and its
  operands are read where a direct argument would be read, under the same
  rule that keeps the locals of call positions out of the argument
  registers;
- copied restores (`replaceJmp`): the jump is the 5-byte `jmp rel32` to
  the restores recorded when it was emitted (only when the peephole left it
  5 bytes long), and is replaced only if its byte and its fix (to the
  restores' label) are still there. The copy is the same loads, `leave`
  and `ret`, `rbp`-relative and so position independent. Labels bound
  after the jump, fixes and relocations after it move by the change in
  size; the jump's own fix is dropped; the sites are replaced from the last,
  so the earlier recorded ones have not moved. Trap stubs and the prologue
  are generated after, so nothing else records a position in the body;
- pushed saves and the stack-overflow probe: the registers whose slots are
  the top of the frame, in order (`-8`, `-16`, ...), are pushed instead of
  stored, to the same addresses. The pushes write the 8 to 40 bytes below
  `rbp`, contiguous with the return address and `rbp` the call and the
  `push rbp` just wrote, before the `sub`; a frame over `PROBE_FREE` is
  still probed a page at a time from `rbp` after it. A push that meets the
  guard page faults there, as the `push rbp` would, and `rt_segv` reports
  it;
- functions at multiples of 16: the padding is `int3` between functions,
  never executed (a function's code ends in a jump or `ret`, its stubs in a
  call that does not return); calls and maps use the labels, which are
  bound after the padding.
  The code's base was first aligned to 16 only, past the runtime's text.
  Merging the concurrency runtime moved it, and fib went back to 2,020M
  cycles with the same 5,630M instructions: the 16-byte alignment held
  within the code but not against 64-byte fetch blocks. The base is now
  aligned to 64 (`CODE_ALIGN`), and fib is at 1,690M again (351 ms against
  gcc's 321). Functions at 32 were tried: fib gains another 8% where it
  lands, the other benchmarks do not move, so 16 stays.

Not done, and why:

- loop-invariant loads of array and `str` fields, borrowed `str`
  parameters and doubles in `xmm` registers: left for later, as asked
  (each is its own analysis: invariance under the loop's writes, the IR's
  ownership rules, and the absence of callee-saved `xmm` registers);
- shrink-wrapping (saving only on the paths that need it): fib's leaf
  path still saves and restores two registers. It would need the saves
  moved past the first branch, with the probe and the pushes; the copied
  restores already make that path short;
- holds beyond the five callee-saved registers: a hold takes a slot when
  all of them are taken at its position (not measured how often).

Gates at the end of the step:

- `run_native.py --lib` 395 of 395; with `--redteam` 1491 of 1491;
- `gate.sh full` PASS, the native self-build at a fixed point (2,098,753
  bytes);
- `cmd/eza_gate.py -j 12` 482 passed, 0 failed (72 live-only skipped);
- `run_prev.py --native --lib --redteam --eza` against main's compiler
  (`OBX_PREV`, main at `2624d58`): 2442 same, 0 differ;
- the seed at a fixed point on the branch.

2026-11-03, the third performance step (branch `perf2`, section 4: registers
by live ranges, `rsi rdi r8 r9` for call-free ranges, `&l` arguments of
bodies in place, lvalues and elements as operands, commutative operands,
the `rt_str_eq` and `rt_str_byte` inline forms; in the runtime, the small
malloc classes without a search and a word-wise `memcmp`). Best of 5, ms,
the same machine and session for both columns ("main" is main's backend at
the start; both compilers build the same sources); then user-mode
instructions and the least cycles of 10 interleaved runs (perf stat):

| | main | perf2 |
|---|---|---|
| native compiler size (bytes) | 2,139,761 | 2,110,945 |
| `--emit-c` of the compiler | 177 | 169 |
| bench fib | 481 | 480 |
| bench loop | 441 | 440 |
| bench array | 250 | 179 |
| bench alloc | 71 | 67 |
| bench lists | 37 | 37 |
| bench strings | 53 | 53 |
| bench trees | 25 | 23 |
| bench tokens | 13 | 14 |
| eza `-T` | 129 | 127 |
| eza `-T -l` | 230 | 224 |
| self-build, instructions | 1817M | 1649M |
| self-build, cycles | 685M | 658M |
| eza `-T`, instructions | 757M | 659M |
| eza `-T`, cycles | 265M | 240M |

Per commit (instructions; least cycles of 10):

| commit | self-build | eza `-T` |
|---|---|---|
| main | 1817M; 685M | 757M; 265M |
| live ranges, `rsi rdi r8 r9` | 1801M; 667M | 738M; 257M |
| inlined locals per call, `&l` aliases, exit tails | 1798M; 664M | 723M; 247M |
| quiet temporaries, `rt_str_eq`, malloc classes | 1766M; 647M | 710M; 247M |
| elements and string bytes in place, commutative | 1714M; 660M | 672M; 245M |
| lvalues as operands, stores in place | 1660M; 660M | 662M; 244M |
| unused assignment values | 1649M; 658M | 659M; 240M |

Instructions fall by 9% (self-build) and 13% (eza), cycles by 4% and 10%.
Most of what went is register moves, `lea` and reloads, which the processor
did cheaply. The time is now in malloc, `rt_str_concat`, the reference
counts and the dup glue of records (`Checker.nd` copies a 192-byte node
with three counts): work in the code, not in the registers. Wall-clock
timings on this machine vary by 5-10% between runs; instruction counts do
not.

The new rewrites rest on these arguments (none changes the C):

- live ranges: positions are in the order the code is laid out. A value that
  can flow backwards (around a loop) widens its range to the whole loop,
  repeated until no range partially overlaps a loop. Two locals share a
  register only when their ranges are disjoint. Locals of functions in place
  are set at each call before they are read (parameters by the call, others
  by their declaration), so their value never flows from one call to the
  next;
- `rsi rdi r8 r9`: the codegen paths that use them are exactly the loud
  nodes (calls, which also clobber them; 128-bit values; copies over 128
  bytes; double/u64 conversions) and the runtime calls not generated inline.
  A quiet runtime call changes only `rax rcx rdx`. There are two exemptions:
  - a declaration's own initializer runs before the local is written;
  - a call in the straight statements before a `return` is followed only by
    those statements and the return, so a local not named there is dead.
    This holds in the function itself, not in a body in place, whose
    `return` continues in the caller;
- `&l` aliases: the body names p only as `*p` (every naming of p is counted
  as a dereference) and never assigns p, and l has p's pointee type, so `*p`
  and l are the same object for the whole body. `writesReg` treats `&l` as a
  possible write of l's register, so operands evaluated around the call
  re-read l;
- quiet temporaries: the register is one that no local of the function was
  given (inlined locals included), and the right operand's code is quiet by
  the same definition. Nested pairs take the next free register;
- commutative operands: the overflow flags of `add`, `imul` and `mul` do not
  depend on the operand order. The left operand is read after the right one
  only when the right one cannot change it;
- places and elements: the operand is formed from `rax`, `rcx` and the
  registers of locals. Nothing runs between forming it and using it except
  the load of a simple value into `rdx`. The `rt_idx` check is the same
  unsigned compare and stub, done before the element is read;
- `rt_str_eq`: the results are equal (`a.len == b.len && (... memcmp == 0)`
  is false for different lengths and true for two empty strings). The call
  keeps its arguments' order: a left operand in place is read after the
  right one only when that one is pure.

Not done, and why:

- doubles in `xmm` registers: no benchmark, eza or compiler path does
  floating point in a loop. The ABI has no callee-saved `xmm` registers, so
  only call-free ranges could use them;
- `rt_str_concat` inline: its cost is malloc and the copies, not the call;
- `str` count elision across statements: every call is passed an owned
  `str`, so the saving needs the callee's side too. That takes an
  interprocedural "borrowed parameter" analysis, and its safety rests on the
  IR's ownership conventions, which the `ir` branch is about to re-derive in
  check.obx. It is not a simple local argument.

The O(1) small malloc class, dropped in the second step at 0.4%, is back as
part of the runtime commit: 0.2% of the self-build's instructions, one line.

Gates at the end of the step:

- `run_native.py --lib` 395 of 395; with `--redteam` 1491 of 1491;
- `gate.sh full` PASS;
- `cmd/eza_gate.py -j 12` 482 passed, 0 failed;
- the seed at a fixed point on the branch.

2026-11-02, the second performance step (branch `perf`, section 4: the IR
simplification, small functions in place, record results into their
destinations, inline conversions and division, the prologue after the
body, narrow store-reload). Best of 5 (7 for the self-build), ms; "main"
is main's backend and "perf" this one, both building the same sources;
eza on `/usr/share` against the archived obo binary (`archive/obo/build/eza/eza`):

| | main | perf | obo |
|---|---|---|---|
| native compiler size (bytes) | 2,501,369 | 2,083,577 | |
| `--emit-c` of the compiler | 181 | 152 | |
| bench fib | 627 | 482 | |
| bench loop | 436 | 440 | |
| bench array | 289 | 270 | |
| bench alloc | 85 | 72 | |
| bench lists | 46 | 38 | |
| bench strings | 55 | 49 | |
| bench trees | 23 | 23 | |
| bench tokens | 17 | 13 | |
| eza size (bytes) | 335,832 | 282,584 | |
| eza `-T` | 151 | 129 | 147 |
| eza `-T -l` | 256 | 222 | 440 |

User-mode instructions of the self-build went from about 2020M to 1690M. eza's
output equals obo's (42,060 lines of `-T -l`). The gcc -O2 build of the same
C is still ahead (eza `-T`: about 185M cycles against 270M): what is left is
register pressure in loops with many locals (eza's `natcmp` keeps bytes in
slots), temporaries in slots, and gcc's inlining of the runtime (malloc,
`rt_str_concat`). An O(1) size class in the runtime's malloc gained 0.4%
and was not kept.

The rewrites rest on these arguments (none changes the C):

- simplification: only declarations with an initializer and no
  initializer list are touched; renames need equal types; the
  address-taken marks (computed before, and carried through a rename) keep
  any local reachable through a pointer out of case 3 and out of the
  argument shortcuts; use counts are static over the function, so "named
  nowhere else" is exact, and the walk refuses node kinds it does not know;
- record results into their destination: the destination is either new
  (a declaration or the argument's own temporary, which nothing can read
  during the call) or, for an existing local, written only after the call
  returns from registers (a result in memory still goes through the
  temporary, since the callee may read the local);
- `rt_str_dup(s)` arguments: the count is still incremented at the
  argument's place in the order; the bytes are loaded from s at the call,
  which is the same value because s's address is never taken and no later
  argument assigns s (or a member of it);
- in-place `add`/`sub`: only when the statement's value is unused, with the
  same overflow test and trap stub; the register then holds the wrapped
  value only on the trapping path, which does not return;
- functions in place: no recursion (the stack of functions being
  generated), scalar parameters only (no drop glue at the end), the
  callee's own trap messages and `where` strings.

Gates at the end of the step: `run_native.py --lib` 361 of 361, with
`--redteam` 1336 of 1336; `gate.sh full` PASS; `x64_encode_test.py`
identical; `cmd/eza_gate.py -j 8` 482 passed, 0 failed.

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

1. More performance, if wanted. Candidates:
   - the reference counts: a callee-side "borrowed `str` parameter" form,
     once check.obx builds the IR and its ownership rules are settled;
   - loops (the sixth step did headers, proved checks and forwarding):
     loop versioning for checks against an unrelated bound, `str` checks,
     strength reduction, the `mov` after an add whose result goes to a
     register local (`add rax, rbx; mov rbx, rax`);
   - the copies of value records that inlining leaves: records written
     after the copy, record parameters of functions not inlined (by
     address when the callee only reads them), records from calls;
   - inlined locals scoped to a block of their own, so their slots are
     reused (smaller frames);
   - the compile-time cost of inlining and of the loop pass for the
     gcc-built compiler (18% and 9% on `--native` of the compiler): cheaper
     passes (one walk for an expression's kills, rewrites and checks in
     `loops`), or a lower limit for large programs;
   - doubles in `xmm` registers;
   - shrink-wrapping: saves only on the paths that need them (fib's leaf
     path);
   - a faster `lower` and `cgen`, to win back the `--emit-c` time lost
     when the C began to be printed from the IR.

   Measure with `OBX_NATIVE_MAP` and perf on the self-build.
2. check.obx produces the IR directly and cfront goes away: done
   2026-11-03 (section 1). What could follow: templates of typed IR nodes
   (no C type names to resolve, no flat block lines), which would make
   `lower` a thin builder.

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
- Performance, second step: IR simplification, small functions in place,
  record results into their destinations, inline conversions. Done
  (section 4).
- Performance, third step: registers by live ranges, operands in place.
  Done (section 4).
- Performance, fourth step: temporaries in callee-saved registers, forward
  substitution, pushed saves and copied restores. Done (section 4).
- The IR from the templates: check builds trees, lower builds the IR, cgen
  prints C from it, cfront deleted. Done (section 1).
- Performance, fifth step: inlining in the IR (`opt.obx`), value records
  in fields, dead functions. Done (sections 4 and 8).
- Performance, sixth step: loops rotated and aligned, array headers out of
  loops, bounds and overflow checks proved, stored elements forwarded
  (`loops.obx`). Done (sections 4 and 8).
- The clock through the vDSO (no system call per timer). Done (sections 2
  and 8).
- Performance, seventh step: the sieve (variable steps, array lengths and
  arithmetic ranges as facts; counters bounded by their loop; fills as
  `rep stos`; counts without a branch; innermost loops inside a cache line;
  the last large block kept by the heap). Done (sections 4 and 8).
- Performance, eighth step: the regex engine's hot loops (scalar loads
  through borrows and records out of loops by types and callee summaries,
  memory operands through register pointers, locals saved around
  statements with calls, branch-weighted uses). Done (sections 4 and 8).
