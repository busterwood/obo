# D4b — function-value and lambda frame layout (decimal)

**Date:** 2026-11-01
**Status:** **layout proposed, awaiting review.** Design only: this branch
contains no compiler, runtime, test or seed change.
**Baseline:** `main` `b52b886` (obo `0.18`, `SYM_FORMAT=11`, `SYM_ABI=1`).
**Scope:** decimal function values, decimal lambda parameters/returns, and
Stage B decimal captures. Direct calls, methods and interface calls keep the
D4a convention and are not reopened.

## 0. Decision summary

| Question | Decision |
|---|---|
| Where is the hidden decimal destination on a value call? | At `16 + A`, the same place as every other call. `A` is the byte size of the callee's **visible** parameters (24 per decimal, 8 otherwise). |
| Where is the function-value cell? | Pushed **before** the destination, so it sits **above** it at `24 + A`. A callee without captures never reads it. |
| Does a direct call change? | No. Direct, method and interface calls still push the destination immediately before the visible arguments; no cell is added. |
| Lambda environment | The hidden environment pseudo-parameter is removed from `FnRec.params`. The body reads the cell at `16 + A` (non-decimal return) or `24 + A` (decimal return). |
| Decimal capture | A **24-byte value snapshot** in the environment cell, at a byte offset `8 + sum(previous slot sizes)`. No pointer, no borrow, no `mut`/`move`. |
| `.sym` | `SYM_FORMAT 11 -> 12`, `OBO_VERSION 0.18 -> 0.19`; `SYM_ABI` stays 1. Function-type text is unchanged. |
| ABI change? | None to any existing valid program. The decimal-returning value-call order is new (it was refused), not a change to an existing convention. No thunk is needed. |

The key result is that **the cell goes above the destination, not below it**.
The previous implementation pushed destination then cell; the callee reads the
cell as its destination. Swapping those two pushes makes both direct and value
calls present the same callee-visible frame, and costs no direct-call ABI
change.

## 1. The blocker, exactly

`callFnWith` (`obo/src/obo.obo:7141`) currently does this for a decimal-returning
value call:

1. push the hidden destination pointer (`obo.obo:7183-7197`);
2. push the function cell (`obo.obo:7199-7213`);
3. push the visible arguments.

The callee reads its destination at
`16 + visibleTypeParamsBytes(f.fnRec.params)` (`returnStat`,
`obo.obo:12626`). For a call with `A` visible bytes the stack is therefore

```
+16+A+8   destination   <- first pushed
+16+A     cell          <- what the callee reads as its destination
...
```

and the callee loads the **cell** as its destination pointer and writes 24 bytes
into it. For a non-capturing lambda the cell is rodata, so the write faults; for
a capturing lambda it corrupts the cell. That is why `decRet && fnval` is
refused at `obo.obo:7172`.

## 2. Function-value frame layout

Let

```
A = visibleTypeParamsBytes(callee visible params)
  = 24 for each decimal parameter + 8 for each other parameter
```

For a **decimal-returning** call the destination is always at `16 + A`. The
call-kind determines what sits above it:

| Call kind | Stack, higher addresses to lower | Destination | Word above destination |
|---|---|---|---|
| direct | destination, visible args | `16+A` | none |
| method | destination, receiver, explicit args | `16+A` | none |
| interface | vtable, destination, receiver, explicit args | `16+A` | vtable at `24+A` |
| function value | **cell, destination**, visible args | `16+A` | cell at `24+A` |

This is the D4a table with one row added. A call with no decimal return simply
omits the destination row; a function-value call then has the cell at `16+A`,
which is the existing Stage A/B layout.

### 2.1 Example: `fn(d: decimal, n: int): decimal`

`A = 24 + 8 = 32`. Through a value, from higher to lower addresses:

```
+56  cell pointer          (rodata [code] or creating frame [code][caps])
+48  hidden destination -> caller's 24-byte decimal temporary
+40  d.w2
+32  d.w1
+24  d.w0
+16  n                     <- last pushed visible argument
 +8  return address
 +0  saved RBP             <- RBP in the callee
```

The callee's own view is unchanged from a direct call:

```
d at paramOffset(params, 0) = 16 + bytes(n) = 24   -> d.w0/w1/w2 at 24/32/40
n at paramOffset(params, 1) = 16                   -> n at 16
destination = 16 + A = 48
cell         = 24 + A = 56   (read only if the lambda has captures)
```

A direct call to the same signature is identical except that `+56` is absent
and the destination is at `+48`. The same body can therefore be entered
directly or through a cell; only a capturing body reads `+56`. Named function
values remain out of scope (§6), but if they are added later they work by
wrapping `[code]` in a cell exactly like a non-capturing lambda: the callee
ignores the cell at `+56`. No thunk or separate value-call convention is
needed. Adding named function values is still not a layout-only change: a named
function can be impure, so the synthetic `fncall` summary would have to be
rebuilt from the target (or only pure named functions allowed as values). That
effect question is separate from this layout and is not part of D4b.

### 2.2 Push / call / return sequence

```asm
; decimal-returning value call, A visible bytes, cell held in a frame temp
sub  rsp, pad               ; 16-byte alignment, as today
push <cell>                 ; first: ends up at 24+A
push <dest>                 ; ends up at 16+A
push args (24-byte decimals: w2, w1, w0; last arg lowest)
call qword ptr [cell]
add  rsp, A + 16 + pad      ; visible args + cell + destination
; result is the caller's 24-byte decimal temporary
```

`total` in `callFnWith` is already `A + 8 (fnval) + 8 (decRet)`; only the order
of the first two pushes changes. The dispatch `call qword ptr [cell]` is
emitted later in `callFnWith` from `recv` (`obo.obo:7430-7437`), after the
compile-time effect checks, so the cell pointer is still available then; at
runtime the cell itself lives in the caller's frame or rodata.

Implementation shape in `callFnWith`:

```obo
// before the decRet block and before expect(T_LPAREN):
if fnval {
    // existing cell push, moved up
}
if decRet && !iface {
    // existing destination push
}
```

For an interface call the destination is still pushed by
`pushIfaceReceiver` (`obo.obo:7088`) between the vtable and the object; that
code does not change.

### 2.3 Ownership and lifetime

- **The function-value cell is not owned.** A non-capturing lambda's cell is
  rodata (`obo.obo:10505-10517`); a capturing lambda's cell is a block in the
  *creating* function's frame (`obo.obo:10519-10540`). Nothing frees it
  individually, on the call path or anywhere else. The `callValue` frame temp
  (`obo.obo:7483-7502`) holds only the pointer.
- **The decimal destination is not heap.** It is the caller's `decimalMem()`
  slot (`obo.obo:3533`), a frame temporary (or a hidden global in a global
  initializer). The callee writes 24 bytes with `copyDecimalToPtr`; the caller
  receives a `mem` item. No allocation, no free, no runtime call.
- A capturing lambda is still call-argument-only, so its cell cannot outlive
  the creating frame. Decimal captures do not change that rule.

### 2.4 Rejected alternatives

- **Thunk / second value-call convention.** A per-target trampoline would
  re-push the arguments in the direct order (including 24-byte decimals) and
  make a second call. Cost: extra code per function value, a second stack
  frame, and argument copying; no benefit over moving one push.
- **Universal reserved word below the destination.** Keep the cell below and
  move every decimal-returning call's destination to `16+A+8`, pushing a dummy
  word on direct/method/interface calls. Cost: every decimal-returning call
  grows by 8 bytes, the interface vtable moves to `explicit+24`, and D4a's
  direct/interface offset is reopened. It also makes the lambda environment
  read unconditional, but that does not compensate for the ABI churn.
- **Destination-last convention.** Push the destination last so every callee
  reads it at `+16`. Cost: every decimal-returning function's parameter offsets
  shift by 8 and every caller must know the return type to choose the layout;
  this reopens D4a and all direct calls.

## 3. Non-capturing lambdas

**At construction: nothing changes.** A non-capturing decimal lambda still
emits a one-word rodata cell `[code]` and returns its address as a constant
(`obo.obo:10504-10517`). The only construction-side change is that
`fn(decimal): decimal` is no longer refused (`obo.obo:5789`, `5802`, `10121`,
`10173`).

**At the call:** the value-call path pushes the cell, then the destination, then
the arguments. The lambda body is an ordinary function with no environment
read; it reads its destination at `16 + A` and its parameters at the same
offsets as a direct call. The cell at `24 + A` is ignored.

Example, both forms of the same signature:

```obo
fn scale(d: decimal): decimal { return d * 2.0 }

fn apply(d: decimal, f: fn(decimal): decimal): decimal { return f(d) }

fn main() {
    println(scale(1.25))                    // direct: destination at 16+A
    println(apply(1.25, fn (d: decimal): decimal {
        return d * 2.0                      // value: destination still at 16+A
    }))
    let f = fn (d: decimal): decimal { return d + 1.0 }
    println(f(1.25))                        // local function value
    println(apply(1.25, f))                 // the same value, passed on
}
```

## 4. Capturing lambdas (Stage B)

### 4.1 Environment cell

The cell is still `[code][capture...]`, but slots are variable-sized:

```
cell+0    code address                      8 bytes
cell+8    capture 0                         8 or 24 bytes
cell+...  capture 1                         8 or 24 bytes
```

`capSlot(c)` is 24 when `c.kind == 4` (decimal) and 8 otherwise. The byte offset
of capture `i` is

```
off(0) = 8
off(i) = 8 + sum(capSlot(caps[j]) for j < i)
```

There is no `8*i` anywhere. For `[n: int, d: decimal, f: fn(...)]`:

```
cell+0   code
cell+8   n          (8)
cell+16  d.w0       (24: 16, 24, 32)
cell+40  f          (8)
```

The total cell size is `8 + sum(slot)`. The cell base is a frame offset, so it
is 8-aligned; every slot size is a multiple of 8, so every decimal slot is
8-aligned. Decimal's ABI alignment is 8 (D1/D2), so no padding is inserted. The
whole lambda frame is rounded to 16 at the end as today.

### 4.2 Environment read

The hidden environment pseudo-parameter is **removed** from the lambda's
`FnRec.params` (`obo.obo:10261-10268`). Visible parameter offsets are unchanged
because the environment was the first entry and every visible parameter's
offset depends only on the parameters after it. The body reads the cell at:

```
envOff = 16 + visibleParamBytes(params)            // non-decimal return
envOff = 16 + visibleParamBytes(params) + 8        // decimal return
```

For `fn(d: decimal, n: int): decimal`, `A = 32`, so the cell pointer is at
`+56` and the destination at `+48`. For the same lambda returning `int`, the
cell is at `+48`. This replaces the old `16 + 8*(n-1)`
(`obo.obo:13258`), which counted the removed pseudo-parameter.

Removing the pseudo-parameter also makes the lambda's own
`returnStat` destination formula correct: with visible-only `FnRec.params`,
`16 + visibleTypeParamsBytes(f.fnRec.params)` is `16 + A`, exactly where the
value-call path puts it. Nothing else reads the pseudo-parameter: lambdas are
not methods, are not in `.sym`, and their only `pmodes` are 0 or 1 (no `pfree`).

### 4.3 Capture store at construction

The snapshot is taken at the literal's source position, left to right, before
later arguments run. The cell is allocated first:

```obo
// lambdaExpr, capturing branch
var cellBytes = 8
for c in caps { cellBytes += capSlot(c) }
frame += cellBytes
let base = -frame
store code address at [RBP + base]
var off = 8
for c in caps {
    if c.kind == 4 {                       // decimal
        let v = localItem(c.outer, tDecimal)
        copyDecimalToBase(v, RBP, base + off)
    } else {
        let v = load(localItem(c.outer, c.typ))
        mov [RBP + base + off], v
        release(v)
    }
    off += capSlot(c)
}
```

`copyDecimalToBase` (`obo.obo:3619`) copies three words and does not release the
base register, so `RBP` survives. Use a fresh `localItem` for each capture; the
source item is consumed by the copy.

### 4.4 Capture load in the body

```obo
let er = alloc()
mov er, [RBP + envOff]                 // envOff from 4.2
var off = 8
for c in caps {
    if c.kind == 4 {
        frame += 24
        let s = newSym("let", c.name, tDecimal); s.offset = -frame
        for w in 0..3 {
            let r = alloc()
            mov r, [er + off + 8*w]
            mov [RBP + s.offset + 8*w], r
            release(r)
        }
    } else {
        frame += 8
        let s = newSym("let", c.name, c.typ); s.offset = -frame
        let r = alloc(); mov r, [er + off]; mov [RBP + s.offset], r; release(r)
    }
    off += capSlot(c)
}
release(er)
```

The decimal local is a real 24-byte frame slot (`frame += 24`), so later
arithmetic, formatting and returns treat it exactly like any other decimal
local. The load loop must not release `er` per capture; use the non-releasing
word loop above. If `copyDecimal` is used instead, give each capture its own
copy of `er`, because `copyDecimal` releases its source base.

### 4.5 Snapshot semantics

A decimal capture is a **copy of the 24 value bytes**, not a pointer to the
outer local. It is taken when the literal is evaluated, so a later write to the
outer variable cannot affect the cell. A second capture in a later statement
takes a fresh snapshot and does see the new value:

```obo
var d = 1.25
let a = apply(0.00, fn [d] (x: decimal): decimal { return d + x })  // 1.25
d = 5.00
let b = apply(0.00, fn [d] (x: decimal): decimal { return d + x })  // 5.00
```

A source-level test cannot mutate `d` *while* a closure that captured it is
live: decimals have no `&decimal`, and captures are second-class and
call-argument-only, so no program can reach the captured local while the
closure is live. The snapshot is therefore also a soundness requirement for a
future `&decimal` or stored-closure extension, even though a by-address
implementation is observationally equivalent to a copy under today's rules (see
§8.3 for the mutant consequence).

### 4.6 Nested lambda

A nested capturing lambda resolves names in the enclosing lambda's block, where
the outer captures are ordinary decimal `let` locals. It snapshots them again
into its own cell:

```obo
fn fold(acc: decimal, d: decimal, f: fn(decimal, decimal): decimal): decimal {
    return f(acc, d)
}

fn main() {
    let a = 1.25
    let r = fold(0.00, 1.00, fn [a] (acc: decimal, x: decimal): decimal {
        // inner cell lives in the outer lambda's frame; captures a and x by value
        return fold(acc, x, fn [a, x] (acc2: decimal, y: decimal): decimal {
            return acc2 + a + x + y
        })
    })
}
```

The outer cell is in `main`'s frame (`a` at `cell+8`, 24 bytes); the inner cell
is in the outer lambda's frame (`a` and `x`, 24 bytes each, at `cell+8` and
`cell+32`). Both cells are frame blocks and are never freed individually. The
inner literal is passed directly to `fold`, satisfying the call-argument-only
rule.

## 5. `.sym`

### 5.1 Format decision

**Bump `SYM_FORMAT 11 -> 12` and `OBO_VERSION 0.18 -> 0.19`; keep `SYM_ABI 1`.**

The canonical function-type text does **not** change. `appTypeExpr`
(`obo.obo:14805`) already prints `fn(decimal): decimal`, and `symParseType`
(`obo.obo:15487`) already lexes `decimal` inside a function type; it was only
the source parser that refused it. The *meaning* changes: a format-11 compiler
has no decimal-returning value-call layout (it hits `decRet && fnval`), and its
value-call push order is the broken one. Because `.sym` compatibility is exact
format equality (`obo.obo:16742`), a format-12 file must be rejected by a
format-11 reader rather than accepted and miscompiled. Captures are not in
`.sym` and do not drive the bump; the function-type meaning does.

`SYM_ABI` stays 1: no existing valid program's layout, calling convention,
descriptor or effect summary changes. The new value-call order applies only to
types that were previously refused. If the reviewer prefers to treat a new
hidden destination slot as an ABI addition, bump ABI to 2, but that would be
inconsistent with D4a, which added decimal method/interface returns while
keeping ABI 1.

### 5.2 Canonical text and validation

Accepted, nested as needed:

```
fn(decimal): decimal
fn(decimal, int): Result[decimal]
fn(): decimal
fn(decimal)                      // void return, no hidden destination
fn(fn(decimal): decimal): decimal
pub type DecFn = fn(decimal): decimal
```

Still rejected by the source and `.sym` readers:

- `fn(&decimal): decimal` — `&` is only for owned records/closed types/arrays;
  the existing `isOwned` check gives the borrow diagnostic.
- `fn(decimal): fn(int): int` — a function type cannot return a function value.
- `fn[decimal](...)` — a function type cannot have a capture list.
- any interface or interface-bearing carrier in a function type (unchanged).

The reader needs no new production, but add round-trip cases and a negative
case for `&decimal`. A format-12 `.sym` must rebuild identically in `--check`
from the symbol file alone.

## 6. Refusals that stay

- A decimal is never a receiver; methods require a record or closed receiver
  (`methodRecvType`, `obo.obo:13324`).
- `mut` and `move` captures: `captures are read-only` (`obo.obo:10153`).
- `&decimal` anywhere: parameters, references, function-type parameters.
- Function values remain second-class: no globals, fields, arrays, optionals,
  `Result` payloads, returns, constants, references, handles or interface
  members; no comparison, no `nil`, no method values, no named function values.
- A decimal capture is a `let` local; writing it is refused. Extend the
  capture-specific assignment message (`obo.obo:11425`) to include decimal so
  it says captures are read-only rather than the generic immutable-`let`
  message.
- `string(decimal)` is unchanged and out of D4b.

## 7. Effects and purity

A decimal-returning function-value call must preserve the existing summary:

- The synthetic `fncall` symbol (`callValue`, `obo.obo:7483`) keeps
  `bind: noBind`, `freesAll = false`, `writesHandles = false`,
  `fnRec.freed = []`, and the visible `pmodes` from the function type. Do not
  set any summary bit because the return is decimal.
- The destination is a caller frame temp; writing it is not a global/handle
  write, so `afterCall` must not invalidate anything. Calling through a cell is
  still a **pure** call in the compiler's predicate.
- All function values are lambdas in v1, and a lambda body is already required
  to be pure (`a lambda must be pure`, `obo.obo:10508`). There is no impure
  callback path.
- No new callback into obo: the destination pointer is computed by `lea`, the
  cell is dispatched by `call [cell]`, and the 24-byte copy is inline
  `mov`/`mov` (or `copyDecimalToPtr`). No `rt_` helper is added for D4b.
- A `Result[decimal]` return is a one-word owned Result cell, not a hidden
  destination; the existing result ownership/free rules apply unchanged.

## 8. Tests, gates and mutants

### 8.1 Run and error tests

- `tests/run/decimal_fnvalues.obx`
  - a named decimal function called directly and a non-capturing lambda with the
    same signature called through a local and through a direct argument;
  - zero arguments returning decimal; one and two decimal parameters; a
    decimal parameter followed by an int (the `A = 24 + 8` case);
  - `fn(decimal): Result[decimal]` and `fn(): Result[decimal]` (no hidden
    destination);
  - a decimal-returning lambda passed to a fold/accumulator;
  - a sort comparator that captures a decimal threshold (the comparator returns
    `int`, so the capture is the decimal path);
  - a direct decimal call, a decimal method and a decimal interface member in
    the same function as a value call, to prove the offsets do not collide.
- `tests/run/decimal_captures.obx`
  - `fn [d]` with a decimal local and with a decimal parameter;
  - `[d, n]` and `[n, d]` orderings (byte offsets, not `8*i`);
  - a decimal with non-zero `w1`/`w2` (for example `123456789.12` and `0.01`)
    so an 8-byte slot loses the value;
  - a second capture after a later assignment sees the new value;
  - the nested example of §4.6;
  - a capturing lambda passed to a fold and to a sort.
- `tests/errors/decimal_fnvalues.obx`: `&decimal` in a function type, a
  function type returning a function type, an interface in a function type,
  `mut`/`move` capture, and a write to a captured decimal.
- `tests/modules/run/decimal_fnvalue/`: `lib` exports
  `pub type DecFn = fn(decimal): decimal` and
  `pub fn apply(d: decimal, f: DecFn): decimal` (plus a
  `Result[decimal]` variant); `main` passes non-capturing and capturing
  lambdas across the module boundary; a `--check` build from `.sym` only.
- `tests/sym_test.py`: new `sym/decimal_fnvalue` cases for the canonical texts
  of §5.2, the round trip, the `&decimal`/nested-return/capture-list rejections,
  and the format-12 constant.

### 8.2 Gates

`make`, `run_fork.py`, `module_test.py`, `sym_test.py`, `sym_fuzz.py`,
`doc_check.py`, `heap_test.py` (leak and peak under `OBO_CHECK=1`, plus the
self-compile fixed point), `port_test.py`, `port_compare.py` (peak),
`mutate_fork.py`, `mutate_decimal.py`, `own_fuzz.py`, `elf_test.py`, `io_test.py`,
`meta_test.py`, `imports.py`, `stdlib.py`, `tools.py --require-reference`, and
`stage2 == stage3`. Record the peak against the 2,750,000 budget in `FORK.md`.

### 8.3 Mutants

Add to `mutate_decimal.py` (or `mutate_fork.py`) with the listed catchers:

| Mutant | Catcher |
|---|---|
| push destination before cell again (old order) | decimal value call returns garbage or writes the cell; a non-capturing cell is rodata (fault), a capturing cell corrupts the frame |
| destination at `16+A-8` or `16+A+8` | a value call with a decimal return fails; if the shared `returnStat` offset is changed, direct-call tests fail too |
| lambda env read `16+A` when the return is decimal | capturing decimal lambda reads the destination pointer as its cell; `cap + x` is wrong or crashes |
| lambda env read `24+A` when the return is not decimal | existing capture tests read one word high |
| remove `+8` from `envOff` only for decimal | same as the first env mutant |
| capture store copies only `w0` (8 bytes) | capture `123456789.12`/`0.01`; fraction/scale is lost |
| capture load copies only `w0` | same |
| capture slot `8` instead of `24` (frame and cell) | 24-byte copy overruns the next slot; `[d, n]` test corrupts `n` |
| capture offset `8 + 8*i` | `[d, n]` puts `n` at `cell+16` instead of `cell+32`, inside `d`'s third word; the value is garbage |
| `total` omits the cell or destination byte | stack imbalance; a call in a loop or the heap/fixed-point gate fails |
| synthetic `fncall` `ret` loses decimal | value call omits the destination; result is garbage |
| set `freesAll`/`writesHandles` on the synthetic fncall | a reference/handle used after a decimal value call is wrongly invalidated; add a positive "reference survives" case |
| capture by address (store `&d`, load 24 bytes from it) | **no safe source test can kill this today**: there is no `&decimal`, and captures are second-class and call-argument-only, so no program can reach the captured local while the closure is live. Keep the 24-byte copy, and either add a compiler self-test that checks the emitted cell size/offsets or mark this mutant review-only. Do not claim a behavioral catcher that does not exist. |

## 9. Implementation order

Each step is one compiler commit on the D4b branch, reviewed before the next.

1. **Layout + call path (with the smallest reachable value).** Move the
   `fnval` cell push above the destination, remove `decRet && fnval`, remove the
   source function-type and lambda decimal refusals, and update `lambdaBody`'s
   environment read to the byte offset. A call path cannot be exercised without
   a value, so the non-capturing decimal lambda is necessarily part of this
   checkpoint. Test direct vs value, decimal params/returns and
   `Result[decimal]`. Checkpoint: `make`, the new run test, the D4a run/error
   tests, `heap_test.py`, fixed point. No format bump yet.
2. **Capturing lambdas.** Remove the decimal capture refusal, add kind 4,
   variable slot sizes and byte offsets, the construction copy and the body
   load, and the capture-message tweak. Test capture/nested/orderings and the
   `mut`/`move` refusals. Checkpoint: same gates plus the capture tests.
3. **`.sym`.** Bump `SYM_FORMAT` to 12 and `OBO_VERSION` to 0.19, add the
   canonical/round-trip/rejection cases, update the format constants in
   `tests/sym_test.py` and `tests/mutate_fork.py`, refresh the hand-written
   fixtures and goldens mechanically, and run the cross-module `--check` case.
   Checkpoint: the full §8.2 gate set and `stage2 == stage3`. Intermediate
   checkpoints stay branch-only; the format bump must land before merge.
4. **Docs.** Update `LANGUAGE.md` §5c/§5d and the decimal section,
   `design/decimal-language-design.md` status and `FORK.md` after the
   implementation review. Not part of this design commit.

## 10. Open questions for review

1. **Cell above destination.** Confirm this over a thunk or a universal
   reserved slot. It is the only option found that leaves direct, method and
   interface calls byte-identical; the cost is that the lambda's environment
   read is conditional on a decimal return.
2. **Environment pseudo-parameter.** Confirm removing it from
   `FnRec.params` rather than keeping it and special-casing the return offset
   helper. Visible parameter offsets are unchanged either way, but removal
   makes `returnStat` uniform across functions and lambdas.
3. **Format/version.** Confirm `12`/`0.19`/ABI `1`. If ABI is bumped, say why
   D4a's decimal method/interface returns did not.
4. **By-address capture mutant.** Confirm the review-only/structural treatment
   in §8.3, or name the introspection mechanism to be added.
5. **Capture kind.** Use a new `kind == 4` or reuse `kind == 0` plus a
   `sizeOf` test? Kind 4 is recommended because slot size and copy width are
   then explicit at every site.
6. **Named function values.** They stay out of scope. Confirm the layout's
   forward compatibility (a `[code]` cell wrapper needs no thunk) is not a
   reason to add them in D4b.
