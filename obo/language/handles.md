Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 5. Handles

`handle(x)` makes a `^T` from a record or closed value held in a variable, field
or element. A handle does not own its object, so it can link back to a parent,
point into a shared arena, or form a graph.

Every use of a handle checks that its object is still alive. If the object has
been freed, the program stops with exit code 7 and the message
`obo: the object this handle names has been freed`. `alive(h)` tells you without
stopping.

```obo
// example: runs
// prints root
type Node = ref record { name: string; parent: ?^Node }

fn main() {
    let root = new Node{name: "root", parent: nil}
    let kid = new Node{name: "kid", parent: handle(root)}
    let p = kid.parent
    if p != nil { println(p.name) }
}
```

```obo
// example: runs
// exit: 7
type Box = ref record { v: int }
type Link = ref record { to: ^Box }

fn main() {
    var b = new Box{v: 1}
    let l = new Link{to: handle(b)}
    println(l.to.v)                // prints 1
    b = new Box{v: 2}              // frees the first box
    println(l.to.v)                // exits 7 here
}
```

Where the compiler can see it, a use of a freed handle is an error instead. A
handle made by `let h = handle(b)` from a whole owned local `b` names `b`'s
object. Assigning over `b`, or `drop(b)`, frees that object, so after it a use
through `h` (`h.v`, `h.v = 1`) is refused, on every path that the assignment may
have run on. `alive(h)`, or a copy of `h`, is not a use; a handle that is not
used again after the assignment is not an error. Make the handle again after the
assignment to use the new object:

```obo
// example: error: handle 'h' may name a freed object: 'b' was assigned or dropped after the handle was made from it
type Box = ref record { v: int }

fn main() {
    var b = new Box{v: 1}
    let h = handle(b)
    println(h.v)
    b = new Box{v: 2}              // frees the first box
    println(h.v)                   // refused: h names it
}
```

Inside a loop, an assignment over the owner is refused when the handle was made
before the loop and the loop uses the handle anywhere in its body, before or after
the assignment in the text, since the next iteration would use the freed object:
`cannot assign over the owner of handle 'h' inside a loop that uses the handle`
(the same for a `drop` of the owner). A free that ends its block in a `break` or a `return`, with no
`continue` in the block, is not followed by another iteration (a `break` leaves only the loop it is in, so a
handle made outside an enclosing loop is still refused; a handle made in the scope directly outside the loop
it leaves is not, since no iteration of that loop follows the break): the loop may use the handle, and a use
after the loop is refused by the paths. `alive(h)`, as a bare name, does not count as the loop using the handle, and
neither does a `let` copy of a handle that is already known to be dead there (the copy is dead too). A handle made
inside the loop is fresh each iteration. A move of the owner (`var c = a`, passing it) on one path
does not hide a later free of the owner on a path that did not move it: `if k { var d = a; return } else { a = nb() }
use(h)` is refused, since the else path freed the object. A handle is safe after a free only if every path that
reaches it moved the owner. The compiler does not
know that two conditions agree, so `if k == 1 { c = a; a = nb(2) }  if k == 1 { a = nb(3) }  use(h)` is refused
although it is safe (known false positive): put the move and the free in one arm. If the arm that frees the object ends in
a `break` or a `continue`, a use of a handle made inside the loop after the `if`
is accepted, since that code runs only on the paths that did not free it; a use
after the loop is reached through the break, and is refused. A `defer` that uses
the handle runs after the free, so a free of the owner after the defer is
declared is refused: `cannot assign over 'b' while a defer declared earlier uses
handle 'h' made from it` (and `cannot drop`). The rule covers `let` handles
made from a whole local, and from a field or element of a local (below). A `var`
handle, a copy kept in a `var` or a record, a handle made through a reference
parameter, another handle or a global, and a handle parameter are checked at run
time, as above.

A `let` handle made from a field or element of an owned local (`handle(p.child)`,
`handle(p.kids[2])`, `handle(xs[i])`) is tracked by its place. It is dead after the
place, or any place above it, is assigned over, replaced, taken or truncated away,
or the owner is assigned over or dropped; directly, through a reference that may
point at or above the place, or by passing a `mut` argument that reaches the place
to a function that frees below its parameter. A change to a sibling field, to
another element with a constant index, to a scalar, or inside the object the handle
names does not reach it, nor does `append` or a `truncate` with a constant count
that keeps the element. A `mut` call frees only the places its callee frees below the
parameter (a callee that sets `p.other` leaves a handle to `p.child` alone); a recursive
or unknown callee, or one that frees many places, frees anywhere below the argument. An element with a variable index is reached by a change
to the array or to any element. A move of the owner is a move, as for a whole
owner. A write through a handle made from the whole owner is a change of the
owner. A write through any other handle is not seen, and the use is checked at run
time. Known false positives: the old value replaced (`replace`) and kept elsewhere, a
`truncate` with a count that is not a constant, and a free on a path the program does
not take.

```obo
// example: error: handle 'h' may name a freed object: a place in 'p'
type Kid = ref record { n: int }
type Par = ref record { child: Kid; other: Kid }

fn main() {
    var p = new Par{child: new Kid{n: 1}, other: new Kid{n: 2}}
    let h = handle(p.child)
    p.other = new Kid{n: 3}        // another place: h is still good
    println(h.n)
    p.child = new Kid{n: 4}        // frees the first child
    println(h.n)                   // refused: h names it
}
```

A test on `alive` narrows the handle: inside the block it guards, `h` is known to be live, so a use is
accepted although the owner may have been freed on some path before. The guard forms are `if alive(h) { ... }`,
`if alive(h) && cond { ... }` (and `cond` itself may use `h`), `if !alive(h) { ... } else { ... }` (the else
arm is narrowed) and `if !alive(h) { exit }` where the block always ends in a `return`, `break` or `continue`
(the code after the `if` is narrowed). `h` must be a bare name and `alive(h)` the first thing in the condition;
`!alive(h)` must be the whole condition, and an `||` anywhere in a condition with `&&` turns the narrowing off.
The narrowing ends at the next assignment over the owner or `drop` of it, which marks the handle dead again, and
it does not outlive the block (or, for the early exit, the enclosing block): after `if alive(h) { ... }` the
handle is as dead as it was before. Refused as before: `while alive(h)`, the else arm of `if alive(h)`,
`alive(f(h))`, `alive(h) == true`, `alive(h) || c`, `!alive(h) && c`, a guard on a copy or another handle, and a
loop that uses the handle and frees its owner anywhere in its body, guard or not.

```obo
// example: runs
// prints 2
type Box = ref record { v: int }

fn main() {
    var b = new Box{v: 1}
    let h = handle(b)
    var c = true
    if c { b = new Box{v: 2} }     // may free the object h names
    if alive(h) { println(h.v) } else { println(2) }
}
```

Handles can be stored in fields and optional fields (`?^Node`), and they are
checked for `nil` in the same way as other optionals. A handle needs a variable,
field or element, and must name a record or closed type.
