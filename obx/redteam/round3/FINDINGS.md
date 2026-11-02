# Red-team findings, round 3 (2026-11-02)

Reviewed: the reference compiler at `4a7f51b` (closures, set-once globals and
constant tables, destructuring, `never`, extern changes, qualified variants,
`./` imports, fractional literals with exponents). 450 single-file cases in
`cases/`, 18 multi-module cases in `mcases/`, 14 minimal findings `h01`..`h14`
(h05 is a directory) whose headers give what the spec requires. All cases pass
under gcc ASan+UBSan and `OBX_CHECK=1`, and under clang with
`-fsanitize=function` (trampoline casts), except the findings. About 12,000
fuzzed mutants (4,000 built with gcc) found nothing new.

Counts: 2 high root causes (6 files), 3 medium, 6 low.

## High: accepted, memory corruption

- **H1** Passing or re-slicing a `mut &[T]` does not freeze its root (rules 3
  and 4): `h01_mutslice_pass_no_freeze`, `h02_reslice_no_freeze`,
  `h03_closure_mutslice_capture`. `let r = &s[0].v[0]; reset(s); println(r)`
  reads freed memory; also `reset(mut &s[0..1])`, `let t: mut &[B] = s[0..1]`,
  a `mut &[]T` variable passed as a slice, `for` loops and match bindings; the
  closure form `run(fn() { reset(s) }, &s[0].v[0])` records the capture as a
  read. Cause: the slice-parameter branch of `call()` calls `as_slice` and
  `hold` but never `frozen(v.root, 'mut', ...)`; `x_sub`'s slice-operand branch
  has no `frozen()` when `mut`; so `root.capmut` is never set for a captured
  slice either.
- **H2** Globals are dropped at exit in reverse declaration and module order
  while set-once `get()` still works: `h04_once_drop_order`,
  `h05_module_once_drop_order/`, `h06_str_global_dropped_before_once`. A drop
  method reading another global (set-once or `str`) that was dropped first
  reads freed memory; `if (G_set) drop(G)` does not clear `G_set`. Cause:
  `emit.py` `program()` (`gdrops.insert(0, ...)`).

## Medium

- **M1** `h08_frac_scale_overflow`: `1e-18446744073709551617` gives a wrong
  value (the scale is emitted as `{scale}LL` with no range check in `x_frac`);
  clang rejects the C.
- **M2** `h10_never_type_arg`: `never` can be inferred as a type argument
  (`call(fn(): never { exit(3) })` with `fn call[R](f: &fn(): R): R`); the C
  fails (`void t1`). `unify` binds `NEVER`; `call()` and `check_targs` accept it.
- **M3** `h11_void_type_arg`: the same with a function returning nothing.

## Low

- **L1** `h09_huge_literal_crash`: a literal over 4,300 digits raises a Python
  `ValueError` (`syntax.lex` `int()`, `check.x_frac`).
- **L2** `h07_once_set_during_exit_leak`: a drop at exit sets an unset
  set-once global and leaks (same cause as H2).
- **L3** `h12_extern_as_runtime_name`: `as "rt_alloc"` and `as "main"` are
  accepted and bind to the runtime's or the program's own symbols.
- **L4** `h13_var_closure_param`: `fn app(var f: &fn())` is accepted
  (`resolve_fn` checks only `ref` and `slice`).
- **L5** `h14_generic_alias_variant`: with `type R[T] = Result[T, str]`,
  `R.Ok(3)` and an `R.Ok(v)` arm are rejected ("R takes 1 type arguments");
  `Option.Some(5)` and `R[int].Ok(3)` work.
- **L6** `5.toStr()` is lexed as a bad fractional literal; and a line ending in
  `never` (`extern "c" fn abort(): never`) does not end the statement.

Not repeated: `let r = &T[1]` on a constant table (o42) is round 2's g04 cause.

## Spec decisions for the fixes (orchestrator, 2026-11-02)

- H2/L2: at exit, a module's globals are dropped before the globals of the
  modules it imports, and within a module in reverse declaration order. A
  dropped set-once global is empty again and marked dropped: `get` or `set`
  after that stops the program (exit 12). A dropped copy global (a `str`)
  reverts to its constant initializer.
- M1/L1: a fractional literal whose scale does not fit `int`, and any literal
  too long to read, is a compile error.
- M2/M3: `never` and "no result" are not types and cannot be type arguments,
  written or inferred.
- L3: an extern's C name may not be `main` or a name the runtime or the
  compiler's output uses (`rt_*`, `obx_*` and the generated prefixes).
- L4: `var` on a closure-typed parameter is an error.
- L5: a generic alias names its variants like the type it aliases:
  `R.Ok(3)` infers `R`'s type arguments.
- L6: `5.toStr()` is a method call on `5` (a fractional literal needs a digit
  after the point); a line ending in `never` ends the statement.

## What held up

Closures: nested capture propagation, `let` freezes, rule 3 for closure
arguments (nested calls, receivers, borrow-returning arguments), moves of
captures rejected, closures in `drop` cannot reassign the receiver, earlier
arguments copied when a later one has effects (int, str, fields, operators,
literals, indexes, receivers, ranges, compound assignment), `try` in closures,
closures in conditions; generic closure inference; trampolines under
`-fsanitize=function`; the library's closure functions. Set-once double set
and early get exit 12; const tables in every form tried; destructuring with
`try`, generics, drop rejection, private fields, drop order, early exits;
`never` in loops, arms, joins, methods, interfaces, function values, closures,
generics; qualified variants through aliases, modules, tables, generics; `./`
and `../` imports, cycles; extern NULL strings, substrings, NUL traps,
signature clashes; fractional literal limits and order.

## Harness

`rt.py` (default: `cases/`, `mcases/`, `h*`; `CC=clang` for the clang build;
`--probe`), `split.py batch.txt [outdir]` (`=== dir/main` makes multi-module
cases), `fuzz.py N seed` (`FZGCC=1`), `run1.sh`.
