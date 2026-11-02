Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 7. Results and try

`Result[T]` is the type of an operation that can fail with a reason. It is a
built-in parameterized type: one owned cell holding one of two variants, `ok`
(one field `value: T`) or `err` (fields `message: string` and `code: int`). The
err variant and its descriptor are shared by every `Result[T]`, so an error cell
made for one success type can be returned from a function whose success type is
another. A result is always one allocated cell; the payload follows `T` (an owned
pointer, a reference-counted string, or a copied scalar or handle).

Construct with `ok(v)` and `err(message[, code])`; the code defaults to 0. `ok`
takes ownership of its value and rejects `nil`, a void expression, a borrowed
value and an error value (`ok(err(...))` has no payload type: type the error in a
`Result` local first). A success with no value needs a typed optional
(`let n: ?Node = nil; return ok(n)`) because `ok(nil)` has no type. `err` takes a
reference to its message, so a heap message it is given is kept alive by the cell.

A result is owned like a record: it is freed at scope exit, can be stored in
fields, arrays, optionals and globals, and its payload or message is freed with
it. `Result` is invariant: `Result[byte]` is not a `Result[int]`, because the ok
tag belongs to the success type. `==` and `!=` compare result cells by identity,
and both sides must be stored values, not `ok(...)`/`err(...)` temporaries;
`nil` comparison is only for `?Result[T]`.

```obo
// example: runs
// prints 3
// prints bad 7
fn get(n: int): Result[int] {
    if n < 0 { return err("bad", 7) }
    return ok(n)
}

fn main() {
    let a = get(3)
    match a {
        ok { println(a.value) }
        err { println(a.message, " ", a.code) }
    }
    let b = get(-1)
    match b {
        ok { println(b.value) }
        err { println(b.message, " ", b.code) }
    }
}
```

`match` narrows the result to the variant, as for a closed type: `r.value` in the
`ok` arm, `r.message` and `r.code` in the `err` arm. Both arms are required and
`else` is not allowed. This match does not consume the result: scalars, handles
and strings can be copied out (`let s = r.value` takes a string reference), and an
optional payload can be `take`n; an owned record or array cannot be moved out,
because the result still owns it.

A consuming match binds the payload instead. Write `ok(v)` and `err(m, c)`; every
arm must bind, or none may. The scrutinee must be a whole local or parameter, not
a reference: it is moved, the shell is freed, and the bindings are ordinary locals
of the arm, freed where the arm's block ends or its `return` leaves. A string
binding owns the cell's reference; returning `err(m, c)` from an `err` arm builds
a new cell from it.

```obo
// example: runs
// prints 5
// prints from 9
type Node = ref record { v: int }

fn get(n: int): Result[Node] {
    if n < 0 { return err("no node", 9) }
    return ok(new Node{v: n})
}

fn main() {
    let r = get(5)
    match r {
        ok(n) { println(n.v) }
        err(m, c) { println(m, " ", c) }
    }
    let e = get(-1)
    match e {
        ok(n) { println(n.v) }
        err(m, c) { println("from ", c) }
    }
}
```

`try e` propagates a failure. Its operand must be an owned `Result`: a whole
local or parameter, or a fresh call result. On ok, `try` moves the payload out and
frees the empty cell; on err, it runs the function's defers, frees its live owned
locals, and returns the error cell itself, so the caller sees the same message and
code. The enclosing function must return a `Result` (of any success type); `main`
does not, so it matches a helper's result instead of trying it.

```obo
// example: runs
// prints 4
// prints deeper 9
fn bottom(n: int): Result[int] {
    if n < 0 { return err("deeper", 9) }
    return ok(n)
}

fn addOne(n: int): Result[int] {
    let v = try bottom(n)
    return ok(v + 1)
}

fn main() {
    let a = addOne(3)
    match a {
        ok { println(a.value) }
        err { println(a.message, " ", a.code) }
    }
    let b = addOne(-1)
    match b {
        ok { println(b.value) }
        err { println(b.message, " ", b.code) }
    }
}
```

The tried result's success type need not be the enclosing function's: a
`Result[[]byte]` error tried inside a `Result[int]` function returns the shared
error cell, and the caller's `err` arm reads it. Nesting works the same way:
`try try f()` with `f: Result[Result[int]]` unwraps both. `try` in a loop is fine
with a fresh local per iteration; a local declared before the loop and moved by
`try` is refused at the back edge, as for any move.

`try` may appear only where its value is the first thing evaluated: the whole
value of a `let` or `var` initializer, an assignment's right-hand side (an
operator may follow on the left, as in `try f() + 1`), or a statement. It may not
be an argument, a record field, an array length, an index, a condition, a return
value, a match scrutinee, a `for` bound, a global initializer, or the right operand
of an operator, because the early return would leave a half-built value or a
pushed argument behind. It may not appear inside `defer`, whose body runs
out of line and cannot return from the function. The rule is a designed
restriction, not a hole: lifting it needs cleanup of live temporaries (drop glue).

```obo
// example: error: a try expression cannot be part of a larger expression
fn get(n: int): Result[int] { return ok(n) }
fn eat(n: int): int { return n }

fn f(): Result[int] {
    let v = eat(try get(1))
    return ok(v)
}

fn main() {}
```

```obo
// example: error: try needs a function that returns a Result; 'main' returns nothing
fn get(n: int): Result[int] { return ok(n) }

fn main() {
    let v = try get(1)
}
```

Other limits:

- A result in a field, element or global cannot be moved out: use `replace` (or
  `take` on an optional one) to get it into a local, then `try` that. `match` also
  needs a whole local; inspect a field through a local reference (`match p`).
- `obj.field = try f()` is refused when the call might free the object holding the
  target (the assignment-target rule, section 12): compute the value in a local
  first. A call that cannot free (a pure callee such as `get` above) is accepted:
  `b.n = try get(3)` compiles.
- Inside a non-consuming `ok` or `err` arm the scrutinee is narrowed to that
  variant, so `try r` there is refused ("try needs a Result value, not ok"):
  `try` the Result before the match.
- A deferred cleanup cannot propagate its error; its result is discarded and
  freed. Record the error and return it from the main path instead.
- `?Result[T]` may be nil; check it with `!= nil` before `try`, which narrows it
  to `Result[T]`. `try` on the optional itself is refused.
- `Result` has no fields until a `match` arm narrows it, and a `Result` value can
  never be `nil`.

```obo
// example: error: a Result match names the ok and err arms; 'else' is not needed
fn get(n: int): Result[int] { return ok(n) }

fn main() {
    let r = get(1)
    match r {
        ok { println(r.value) }
        err { println(r.message) }
        else { }
    }
}
```

```obo
// example: error: ok(nil) has no type
type Node = ref record { v: int }

fn f(): Result[?Node] {
    return ok(nil)
}

fn main() {}
```

```obo
// example: error: cannot use Result[byte] as Result[int]
fn getb(): Result[byte] { return ok(byte(1)) }

fn f(): Result[int] {
    return getb()
}

fn main() {}
```
