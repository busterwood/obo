# Red-team findings, round 1 (2026-11-02)

The reference compiler at `417f480` was reviewed by an independent agent. It
found 25 problems. Each has a minimal program `fNN_*.obx` here, whose header
gives the behaviour the spec requires (conformance format), so each fails
until it is fixed. `cases/` has the 222 programs of the wider search, `rt.py`
runs them under ASan, UBSan and `OBX_CHECK=1`, and `fuzz.py` is a mutation
fuzzer (about 11,000 mutants; it found only the `fold` tracebacks).

| Severity | Count | Ids |
|---|---:|---|
| Accepted, memory error | 9 | F01 to F09 |
| Accepted, wrong output, wrong exit or crash at run time | 5 | F10 F11 F18 F19 F22 |
| Compiler crash (traceback, hang, C that gcc rejects) | 6 | F12 to F17 |
| Correct program rejected | 4 | F20 F21 F24 F25 |
| Harmless wrong accept | 1 | F23 |

F05 to F10 share one cause: copy reads stay as deferred C text and are
snapshotted (`snap_val`) only at some points, and only when `effects()` sees a
later side effect.

## Accepted and unsound

- **F01** `while take(d) { d = ... }` is accepted: use after free and double
  free. `s_while` does not raise `ctx.nomove` around the condition, and
  `loop()` restores the entry snapshot taken before the condition.
- **F02** A drop method can be called through a constraint
  (`interface { fn (mut &Self) drop() }`), so the destructor runs twice.
  `method_sig` refuses `drop` only on the concrete path, and `satisfies` lets
  the destructor satisfy a member named `drop`.
- **F03** `f(mut &xs[0], &first(&xs).d)` is accepted (use after free). `call()`
  gives a borrow result the argument's own path, so rule 3 sees `xs.d` and
  `xs[]` as disjoint. Fails in either order, through `check_pending`, and for a
  held left operand. Fix: a wildcard path element after a borrow result.
- **F04** A type parameter inferred from a function value can be a borrow
  (`collect(first, &xs)` gives `T = &D`), and the borrow escapes into `[]&D`.
  `unify` binds a `ref` and nothing rejects it.
- **F05** `effects()` does not look inside record or array literal `inits`, so
  a copy argument is read after the literal's call reallocates.
- **F06 to F09** The same late read in `s_assign` (right side read after the
  target's index), `b_swap` (first index), `b_array` and `s_for` (range ends).
- **F10** Operator-method calls (`add`, `neg`, `fromFraction`) are not counted
  as effects, so a global argument is read late (prints 100, not 1).
- **F11** A `str` left in a global at exit is reported as a leak: globals are
  never dropped.
- **F18** A drop method that assigns the whole of `self` recurses until the
  stack overflows.
- **F19** Dropping a million-node boxed list overflows the stack: drop glue
  recurses once per node.
- **F22** `P{b: show(1), a: show(2)}` prints 2 then 1: `order_inits` evaluates
  in declaration order, not source order.

## Compiler crashes

- **F12/F13** Tracebacks in `sema.fold`: `1 << -1`, and constant float `/ 0.0`.
  A huge shift count would also exhaust memory in `fold`.
- **F14** A huge integer literal used as a float raises OverflowError (`x_int`,
  `const_as`).
- **F15** A float literal or constant that overflows is emitted as C `inf`.
- **F16** Globals `a_b.c` and `a.b_c` both get the C name `g_a_b_c`.
- **F17** `type T[A] = record { next: Option[T[Option[A]]] }` hangs the
  compiler: only function instances have a depth limit.

## Correct programs rejected, and minor

- **F20** A variable declared inside `while true` and moved at only one `break`
  is rejected: break snapshots include inner-scope variables.
- **F21** `h.fs[0](2)` is rejected with "expected a type": `x_call` reads
  `x.f[...]` as method type arguments.
- **F24** `const C: u32 = ~0` and `const C: byte = 1 << 8` are rejected: `fold`
  uses unbounded integers.
- **F25** `fn swap(a: int)` is accepted, but `swap(1)` calls the built-in.
- **F23** Code after `return` is never checked.

## Spec decisions taken for the fixes (orchestrator, 2026-11-02)

- F11: globals are dropped at normal program end, in reverse declaration order.
- F18: a `drop` method may not assign the whole of `self`.
- F19: dropping a value must not use stack depth proportional to the length of
  a chain of boxed values; and a stack overflow anywhere stops the program with
  a message and exit code 4 instead of a bare segfault.
- F22: record and variant literal fields are evaluated in source order.
- F23: a statement after `return`, `break`, `continue`, `panic` or `exit` in
  the same block is a compile error ("unreachable").
- F24: a constant is folded in the arithmetic of its type; a typed constant
  that does not fit is an error, and `~0` as `u32` is `0xffffffff`.
- F25: the built-ins of section 11 are reserved names; declaring one is an
  error. The std intrinsics are not reserved; a module's own declaration wins.
- `var g: Decimal = 1.25` stays an error (not a constant initializer); a
  `const` fractional literal is substituted where it is used.

## What held up

About 185 of the 222 case programs pass under ASan, UBSan and `OBX_CHECK=1`:
held borrows (receivers, indexes, `replace`/`swap`/`append` against nested
`pop` and `mut &`, nested chains, disjoint fields accepted); rule 5 and
borrowing matches (escapes rejected, chained `let` freezes, `mut &` results,
function values and constraint-method results tracked); one-path moves,
`break`/`continue` across nested loops and drop order there; `try` cleanup in
arguments, literals and consuming arms; generic instances with `str`, owned and
value records, boxed closed types, `sort`, `Map`; string reference counting and
the extern NUL check; integer traps at every width; globals.
