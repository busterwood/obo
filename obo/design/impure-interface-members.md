# Impure interface members (0.17, format 10, ABI 1)

**Status:** implemented, stage I1 (compiler only), branch `impure-handler`; gate
results are recorded in `FORK.md`.
**Version:** `OBO_VERSION = "0.17"`, `SYM_FORMAT = 10`, `SYM_ABI = 1`.
**Follow-up:** stage I2 changes `http1server`'s handler contract (not this stage).

## 1. The gap

The interface member tiers of `interfaces-v3-design.md` have no tier for a member
that does real I/O. A method that frees a global, or writes an owned value through
a handle, cannot implement either the `pure` tier or the plain (local) tier:

```obo
module main
var g: []int = new []int(0)
fn zap() { truncate(g, 0) }

type App = ref record { n: int }
type Handler = interface { mut fn handle(x: int): int }
fn (mut a: &App) handle(x: int): int { zap(); a.n += x; return a.n }
impl Handler for App
fn main() { var a = new App{n: 0}; println(a.handle(1)) }
```

is refused with

```text
method 'App::handle' cannot implement 'Handler::handle': it frees global 'g';
interface methods may only change what is below their own mut parameters
```

A blocking DB call has exactly this shape. A server handler must be able to own a
connection (through a handle) and call code that frees globals. The member's
*caller* cannot know which concrete method runs, so it must assume the worst.

## 2. Syntax

A third member marker, `impure`, is accepted on an interface member:

```obo
type Handler = interface {
    impure fn handle(x: int): int          // read-only receiver
    impure mut fn reset()                  // mutable receiver
}
```

- `impure` and `pure` are mutually exclusive. At most one of each, and either
  comes before `mut` and `fn`; the canonical order is `impure` (or `pure`),
  `mut`, `fn`.
- `impure` on anything that is not an interface member (`impure fn f()`,
  `impure fn (r: &R) m()`) is a declaration error, not a new top-level form.
- Repeated or conflicting markers are errors with their own text (section 7).
- `impure` is contextual: it is a marker only in the member-marker position, so
  `fn impure(...)` still names a method `impure`.

Accepted source spellings, all equivalent to the canonical one:
`impure fn m(...)` and `impure mut fn m(...)`. As for `pure`, the marker comes
before `mut`; `mut impure fn m(...)` is refused, and the writer always emits the
canonical order.

## 3. The summary: the maximum

An impure member's synthetic summary, built by `setMemberSummary` from the
markers alone, is the maximum a function summary can express:

```text
freesAll     = true
writesHandles = true
pfree        = 0            (freesAll dominates; no per-place rows)
freed        = []           (it may free anything, not a named global)
addr         = 0            (an interface member)
```

This is deliberately the same shape as a call to a function whose body is not
known yet. No per-member, per-implementation summary is created or serialized:
the summary is rebuilt from the marker by `setMemberSummary` in both the source
parser and the `.sym` reader, so the two cannot diverge.

The caller reuses the existing machinery in `afterCall`, with no special case:

- `freesAll` makes every live reference invalidated and sets the caller's own
  `freesAll`; a function that calls an impure member is therefore itself impure
  for every later tier check and every later caller.
- `writesHandles` invalidates deep references, refuses a deep argument or a
  deep capturing closure (the existing "it writes through a handle" texts), and
  refuses a reference borrowed through a handle (`pureFn` is false). A handle
  receiver (`h.d.m()`) is refused the same way a handle receiver of an impure
  direct method is.
- `pureFn(member)` is false, so every `mut` argument (including the receiver)
  is invalidated; `mutFrees` is true for every parameter index.

Because the summary is the maximum, the only effect a forged `impure` marker can
have is to over-invalidate: it is the safe direction of the trust model. A forged
`pure` marker remains the defining module's responsibility to catch at `impl`,
exactly as before.

## 4. Implementation rule

At `impl I for T` the method `c` is checked against the member `m`:

| member marker | required of `c` |
|---|---|
| `pure fn`, `pure mut fn` | unchanged: `pureFn(c) && !c.writesHandles` |
| `fn`, `mut fn` (plain/local) | unchanged: `!c.writesHandles && !c.freesAll && len(c.freed) == 0 && (c.pfree & ~S(m).pfree) == 0` |
| `impure fn`, `impure mut fn` | **any** method that matches the signature: no effect check at all |

An impure member may therefore be implemented by a pure method, a plain method,
or an impure one; the declaration is an upper bound on the member's effects, and
the caller pays for it unconditionally. Signature checks (receiver mode, arity,
parameter types and modes, return type) are unchanged.

Plain and pure members keep their current rules and messages. In particular a
`pure` member with an impure implementation is still refused with
`method 'T::m' is not pure, but interface 'I' declares 'm' pure`.

## 5. Dispatch

No code-generation change. The receiver's box (`I` or `&I`; `^I` is a later
phase) is loaded, the arguments run, and the member's vtable slot is called
exactly as for every other member. Only the member metadata (`freesAll`, `writesHandles`) read by the call
machinery changes.

## 6. `.sym` and versions

The interface definition line carries the marker in its canonical position:

```text
type Handler = interface { impure fn handle(p0: int): int; impure mut fn reset() }
```

No summary field is added: the reader rebuilds it from the marker and parameter
modes through `setMemberSummary`, exactly as for `pure` and plain members.

This is a **grammar change**: older readers do not know the word `impure` and
would reject or misread the line. `SYM_FORMAT` is bumped **9 → 10** and
`OBO_VERSION` **0.16 → 0.17**. The format check runs before the body is parsed,
so a 0.16 compiler reading a 0.17 `.sym` reports
`has format 10; this compiler reads 9; rebuild it` rather than reading the
marker. `SYM_ABI` stays `1`: layout, calling convention, descriptors and the
vtable shape are unchanged.

The committed `.sym` fixtures and the test harness version constants are updated
mechanically (`obosym 9` → `obosym 10`, `compiler "0.16"` → `compiler "0.17"`).
The deliberate format-mismatch fixture stays on an older format (8) so that it
still exercises the format check. Every non-version byte of a regenerated golden
is reviewed for a semantic diff; no test is weakened.

## 7. Diagnostics

| situation | message |
|---|---|
| `pure` and `impure` together | `'pure' and 'impure' are mutually exclusive on an interface member` |
| `impure` twice | `'impure' is repeated on an interface member` |
| `impure` after `mut` | `'impure' comes before 'fn'` |
| `impure` outside an interface | `'impure' is only allowed on an interface member: write impure fn m(...) inside an interface` |

The existing `'pure' and 'mut' come before 'fn'` text is unchanged for the
markers it already covers.

## 8. Tests and gates

- `tests/run/ifaces_impure.obx`: an `impure fn` and an `impure mut fn`
  implemented by a method that frees a global and by one that calls an impure
  helper; direct calls and calls through `I` and `&I`; two implementations of
  one interface.
- `tests/modules/run/iface_impure/`: a library interface with impure members,
  written to and read back from `.sym`; the importer calls through `lib::Handler`.
- `tests/errors/ifaces_impure.obx`: a live reference borrowed through a handle
  and a deep reference into a local object, each used after an impure member
  call; a deep argument refused; a reference used after a call through a
  wrapper that calls an impure member (the wrapper inherits the maximum);
  an impure member that fails the signature check; `pure`/`impure` conflicts
  and `impure` on a non-member.
- `tests/modules/errors/iface_impure/`: a root that keeps a deep reference into
  a local object across a call through an imported impure member. It is in
  `sym_test`'s full-build/`--check` parity list, so a reader that rebuilds the
  member as plain accepts the source-less check and fails the gate.
- `tests/errors/ifaces_tiers.obx`: unchanged, proving the plain/pure rules did
  not move; `tests/run/ifaces_effects.obx` must keep passing.
- `tests/sym_test.py`: impure marker round-trip and re-emission; a forged
  duplicate marker and a pure+impure marker are refused; the format-9 file is
  refused by the new reader; the imported-impure parity case above.
- `tests/mutate_fork.py`: the impure summary setter, the impl-tier branch, the
  reader's summary rebuild, the writer's marker, a summary that frees all but
  does not write through handles, and a pure-member check that accepts an
  impure method. Each has a real catcher.
- `tests/mutate_fork.py`: add an interface mutant only if it has a real catcher
  (the `impure` summary setter and the impl-tier branch).
- Docs: `LANGUAGE.md` interface section, `FORK.md` 0.17 section, this design's
  status line, and the v3 design's "not in v1" note.

Gates (run from `obo/`): `make obo` (0.17 / format 10 / ABI 1), the fixed point
`stage2 == stage3`, `run_fork.py` (≥1855), `module_test.py`, `sym_test.py`,
`sym_fuzz.py`, `doc_check.py`, `heap_test.py` (≤2,750,000), `port_compare.py`,
and a clean `git status --short`.

## 9. Follow-up: stage I2 (not this stage)

After this compiler tier lands, `http1server` can accept an interface handler
instead of a closure: the server owns the listener/connection, the application
implements `Handler` with `impure handle(req: &Request): Response` (or a
data-carrying response record), and the connection setup, blocking I/O and
teardown live in the implementation or in `impure` helpers it calls. The server's
loop then calls `handler.handle(req)` through the interface; because the member
is impure, the loop's references and handles are conservatively invalidated and
the compiler refuses to keep a borrow across the call. One constraint I2 must
respect: an impure member cannot be called through an interface stored inside a
record or a global (`h.d.run()`/`G.d.run()` is refused as a deep receiver that
writes through a handle). The server must therefore hold the handler in a local
or pass it as an `&I` parameter, not keep it in a `Server` field or a global, or
I2 needs a compiler change that this stage excludes. I2 changes
`experimental/http1server` (and its design note around the current "no impure
interface-member tier" limitation), not the compiler.
