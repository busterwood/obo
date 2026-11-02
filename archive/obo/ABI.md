# obo ABI and layouts

Part of the obo language reference. Index and core syntax:
[LANGUAGE.md](LANGUAGE.md), with the topic chapters in
[language/](language/README.md).

This document is the stable layout contract behind the language rules: value
representations, parameter and return slots, the interface vtable, and the
function-value cell. It is not needed to write ordinary obo; it is needed to
read the compiler's calling convention, write a foreign adapter, or reason about
a `.sym` layout. The `.sym` text itself is in [SYMFILE.md](SYMFILE.md).

## Values

A scalar (`int`, `byte`, `bool`, `float`) is one 8-byte word and is copied by
value. `string`, `[]T`, `ref record`, `closed` and `I`/`?I` values are one-word
pointers or boxes with their own runtime representation. A `Result[T]` is one
cell; an owned payload follows the cell.

A `decimal` is 24 bytes, pointer-free and 8-byte aligned:

| Word | Contents |
|---|---|
| `w0` | coefficient bits 0..63 |
| `w1` | coefficient bits 64..127 (the coefficient is at most `10^38 - 1`) |
| `w2` | `int16` exponent, `uint16` display scale, sign, reserved bits |

There is no NaN, infinity or negative zero. A decimal is always copied by value;
there is no `&decimal` and it is never a receiver. A decimal field is 24 bytes
inline, in declaration order and 8-byte aligned; a `[]decimal` element is 24
bytes inline at a 24-byte stride. A decimal global takes 24 zero-initialised
bytes. A decimal constant takes no storage: each use materialises the three
words.

## Calls and returns

At callee entry, `[rbp+0]` is the saved frame pointer and `[rbp+8]` the return
address; visible arguments occupy `[rbp+16, rbp+16+A)`, where `A` is the sum of
the visible parameter slot sizes: 24 bytes for `decimal`, 8 for anything else.
For a method or interface call, the receiver/object is parameter 0 and counts in
`A`. Arguments are pushed/laid out in that order.

A function returning a bare `decimal` also receives a hidden destination
pointer at `16 + A`; the callee copies the 24 result bytes there. A
`Result[decimal]` return is a one-word `Result` cell, not a hidden destination.

The call kinds differ only in the rows above the visible arguments:

| Call kind | Order, first to last | Destination | Cell / vtable |
|---|---|---|---|
| direct function | destination, arguments | `16 + A` | — |
| method | destination, receiver, explicit arguments | `16 + A` | — |
| interface | vtable, destination, object, explicit arguments | `16 + A` | vtable at `24 + A` |
| function value | cell, destination, arguments | `16 + A` | cell at `24 + A` |

The destination row is present only for a bare decimal return. For a
non-decimal return the interface vtable is at `16 + A`, and for a non-decimal or
`Result[decimal]` function-value call the cell is at `16 + A`. The word at
`24 + A` is call-kind-specific: an interface vtable or a function cell, never
both.

An interface call dispatches through the vtable at `[24 + A]` and loads the
entry by explicit argument byte count plus 8 for the object, plus 8 when the
destination row is present — not by an `8*n` word count. The vtable holds one
code address per member. `I` is a box `{ obj, vtable }`; `?I` is `nil` or a box;
`&I` is a pointer to a `{obj, vtable}` pair and owns its own pair slot when it is
a local.

## Function values and captures

A function value is one word: a pointer to an immutable cell whose word 0 is the
code address. A call passes that cell before the visible arguments and
dispatches indirectly through it. The environment is not a source name; a user
global, local or parameter called `env` remains an ordinary name.

A non-capturing lambda's cell is in read-only data. A capturing lambda builds
the cell in the creating frame at the point the literal is evaluated; it is
plain stack memory, with no heap allocation, destructor or escape. The cell is
valid for the dynamic extent of the creating call. A stored closure would need
an owned cell and is not implemented.

The cell layout is `[code][slot...]`. A capture slot is 8 bytes, except a
`decimal` capture, which is a 24-byte value snapshot. Capture `i` is at byte
offset `8 + the sizes of the earlier slots` (not `8*i`), and the cell size is
`8 + the sum of the slot sizes`. A scalar capture is a value snapshot; an owned
capture is a read-only borrow of the whole local; a decimal capture copies all
three value words when the literal is evaluated, so a later write to the outer
local does not change it. `mut` and `move` captures are not implemented.

The callee reads the cell at `16 + visible parameter bytes`, plus 8 when it
returns a bare decimal (the destination row sits below the cell). The decimal
value ABI is unchanged: decimal parameters and returns still use 24-byte slots
and the hidden destination.

## Runtime additions

The decimal storage tier appends the `rt_append_dec` runtime slot (slot 94) and
array kind 7; the hosted build's census slot is 95 because slot 94 exists in
both builds. No existing runtime index, including the hosted exit slot 59,
changes.
