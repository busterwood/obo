Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 10. Compile errors

The messages below are the compiler's own text; the parts in angle brackets
depend on the program.

| Message | Meaning and fix |
|---|---|
| `'<x>' was moved and has no value here` | `<x>` was given away by a move. Assign it again before using it, or borrow it before the move. |
| `'<x>' is moved on one path but not another` | a branch or match arm moves `<x>` and another does not. Move on both paths, or on neither. |
| `'<x>' is moved inside the loop` | a loop moves `<x>` and does not give it back. |
| `cannot move out of a field or element; use take() on an optional one` | use `take(...)`, or borrow with `&`. |
| `cannot move out of global '<g>'; use take() on an optional one` | the same, for a global. |
| `cannot move '<x>' inside && or \|\|` | move the value outside the condition. |
| `cannot move '<x>' inside defer` | do the move in the body of the block. |
| `a method needs a receiver, as in 'fn (r: &T) name(...)'` | `fn () name(...)`. |
| `a method has exactly one receiver` | two names or types in the receiver group. |
| `expected a method name after the receiver` | `fn (r: &T) (...)`; write `fn (r: &T) name(...)`. |
| `expected a receiver name` | the receiver group is empty or starts with a type; a receiver named `mut` is also read as the mode word. |
| `mut needs a reference parameter: write mut name: &T` | `mut r: T`; a `mut` receiver must be `mut r: &T`. |
| `a method's receiver must be a record or closed type declared in this module, not <t>` | a built-in, array, optional, handle or `Result` receiver. |
| `cannot declare a method on imported type '<M>::<T>'; declare it in the type's own module` | a method on a foreign type. |
| `cannot declare a method on variant '<V>'; declare it on its closed type '<C>'` | a method on a variant. |
| `<T> is declared but not yet defined` | a method on a forward type before its definition. |
| `type '<T>' already has a field '<f>'; a method cannot have the same name` | a method/field name collision on its receiver type (also the closed-variant form `... in variant '<V>'`). |
| `type '<C>' already has a variant '<V>'; a method cannot have the same name` | a method named like a variant of its closed receiver type. |
| `method '<T>::<m>' is already defined` | a second body, or a heading after a body. |
| `method '<T>::<m>' does not match its forward heading` | the definition differs from the heading (including the receiver mode). |
| `method '<T>::<m>' has a forward heading but no body` | a heading with no definition at module end. |
| `exported '<T>::<m>' uses private type '<T>'; mark '<T>' pub too` | a `pub` method on a private receiver type. |
| `<t> has no methods` | a method call on `int`, `string`, `[]T`, `?T`, `^T` or `Result[T]`. |
| `type '<T>' has no method '<m>' (declare it before use; use a forward heading for mutual recursion)` | no visible method of that name on the receiver's static type. |
| `module '<m>' has no exported member '<T>::<m>' (only 'pub' declarations are visible)` | a private or unknown method of an imported type; the same message in a full build and in `--check`. |
| `type '<T>' has a field '<f>', not a method; a field cannot be called` | `x.f(` where `f` is a field. On a foreign mixed record a **private** field instead reports `type '<q>' has no public field or exported method '<f>' (only 'pub' declarations are visible outside module '<m>'; mark the field 'pub' or add a public method)`, identical in a full build and `--check`. |
| `type '<q>' has no public field '<f>' (only 'pub' fields are visible outside module '<m>'; mark '<f>' 'pub' or add an accessor)` | reading, writing, borrowing, moving, `take`ing or `replace`ing a private field of an imported record, or naming a missing field on such a record. The same message is used for all four source forms because field resolution fails first. Add `pub` in the defining module or an accessor. |
| `type '<q>' has no public field or exported method '<f>' (only 'pub' declarations are visible outside module '<m>'; mark the field 'pub' or add a public method)` | `x.f(` where `f` is a private field or an absent member of a foreign mixed record, or a private method on such a record. A public field keeps the `has a field ..., not a method` message. |
| `cannot construct '<q>' outside its module: it has private fields; add a constructor function in module '<m>'` | `new lib::T{...}` when any field of `T` is private, even if every private field is supplied or omitted. Only an all-public record (including a zero-field record) may be constructed externally. |
| `'pub' is not allowed on a variant field; variant fields are always public` | a source `pub` in a closed variant's field list. |
| `obxc: <p>:<line>:<col>: symbol file: expected 'pub', 'priv' or 'priviface' in a record field list` | a format-7 record field without a visibility marker, or an unknown marker. |
| `obxc: <p>:<line>:<col>: symbol file: a private field has no name or type` | `priv`/`priviface` followed by a name or `:`. |
| `obxc: <p>:<line>:<col>: symbol file: private fields are not allowed in a closed variant` | `priv`/`priviface`/`priv24` inside a closed definition. |
| `exported '<T>' uses private type '<S>'; mark '<S>' pub too` | a **public** field of an exported record names a private type; private fields are skipped by the closure check and may use private types freely. |
| `method '<T>::<m>' cannot be used as a value: call it with '(' after its name` | `x.m` without a call. |
| `the receiver of method '<T>::<m>' needs a mut reference, but this one is read-only` | a `mut` receiver through a read-only binding. |
| `cannot call a method that takes its receiver by value through a handle: the handle does not own the object; declare the method with '&' or 'mut &T', or call it on the owner` | an owned receiver that is a whole handle dereference. |
| `argument <n> of method '<T>::<m>' may point inside the receiver, which is mut` (or `the receiver of method '<T>::<m>' may point inside argument <n>, which is mut`) | the receiver and an argument may overlap and one changes. |
| `argument <n> of method '<T>::<m>' may free what the receiver points into, and runs after it; compute it before the call` | a later argument frees or moves what the receiver borrows. |
| `store this value in a variable first: otherwise it would never be freed` | a temporary value is used directly; store it in a variable first. |
| `store this value in a variable first: a reference cannot borrow from a temporary` | `&` needs a variable, field or element. |
| `'<r>' may point to freed memory: something under what it points into was changed; point it again first` | a use of `<r>` (a read, a write, a call, a re-borrow, a defer) on a path where a change freed what `<r>` pointed into, including a change on an earlier pass of a loop, or on one path only. Point `<r>` again before the use, or borrow it again after the change. |
| `argument <n> of <f> needs a mut reference, but this one is read-only` | the parameter is `mut`, but the argument was borrowed read-only. |
| `argument <n> of <f> may point inside argument <m>, which is mut` | two arguments may overlap and one of them changes. |
| `argument <n> of <f> may free what argument <m> points into, and runs after it; compute it before the call` | a later argument moves, frees or changes what an earlier reference argument points into; compute it into a local before the call. |
| `argument <n> of <f> makes a recursive call that writes through a handle, which could free what argument <m> points into; compute it before the call` | the same, for a recursive call in a function that writes through a handle (reported at the end of the body). |
| `'<x>' may be nil: check it with '!= nil' first` | check an optional before using its fields. |
| `cannot pass a reference that borrows through a handle to <f>: it might free what the handle names` | a reference borrowed through a handle may go only to a call that cannot free anything; pass the handle instead, or make the call pure. |
| `cannot pass a reference that points inside something to <f>: it writes through a handle, which could free it` | a reference inside a field or element may not go to a function that writes through a handle; pass a reference to the whole object, or the value. |
| `cannot pass a reference that points inside something to <f>: its body is not known yet, so it is assumed to write through a handle, which could free it` | `<f>` is only declared so far: define it before the call, or pass a whole object. |
| `'<r>' may point to freed memory: a recursive call writes through a handle, which could free what it points into` | a recursive call in a function that writes through a handle may free what `<r>` points into; borrow it again after the call. |
| `a recursive call in this loop writes through a handle, which could free what a reference inside something points into on the next iteration` | a loop calls its own function, which writes through a handle; borrow the reference inside the loop, or move the call out of it. |
| `cannot change what '<r>' points into while a for loop walks it` | a change, or a call that might free, while a loop walks `<r>`; make it after the loop. |
| `cannot point '<r>' elsewhere in a defer: a defer body runs where its block is left, and the change would not be seen there` | a defer body points a reference declared before it at something else; do the change in the block, or declare the reference inside the defer body. |
| `cannot point '<r>' elsewhere while a defer uses it: the defer runs where its block is left, and would use the new target` | a defer body uses `<r>` and the block points `<r>` at another place before it is left; point a different reference instead, or declare `<r>` after the defer. |
| `'<r>' is used by a defer but may point to freed memory at the block's end: something under what it points into was changed` | a change after the defer statement freed what `<r>` points into, and the defer runs at the block's end; make the change after the block, or borrow `<r>` inside the defer. |
| `'<r>' is used by a defer but may point to freed memory at the block's end: a recursive call writes through a handle` | the same, for a recursive call in a function that writes through a handle. |
| `'<r>' is pointed somewhere new inside the loop, and read before it is pointed again; point it before the loop, or point it afresh before reading it in the loop` | a loop reads `<r>` before pointing it again, and a change in the loop may have freed what it points into, or it points somewhere the body was not checked with on the next iteration; point it afresh before reading it. |
| `'<r>' is read-only where the loop starts again, and written before it is pointed again; point it afresh before writing it in the loop` | `<r>` is writable where the loop starts but read-only where it starts again, and is written before it is pointed again; point it afresh before writing it. |
| `the references in this loop change too often to be checked; point them afresh in the loop` | the loop's references did not settle within the checker's passes; point the references afresh inside the loop. |
| `take needs an optional variable, field or element holding an owned value` | `take` applies only to optional owned places. |
| `replace needs a variable, field or element holding an owned value` | `replace` applies only to owned places. |
| `drop needs a local variable holding an owned value` | `drop` applies only to local owned variables. |
| `handles name records and closed types, not <t>` | `^T` is only for records and closed types. |
| `handle '<h>' may name a freed object: '<x>' was assigned or dropped after the handle was made from it; make the handle again` | `let <h> = handle(<x>)`, then `<x>` was assigned over or dropped on some path, which freed the object `<h>` names, and `<h>` is used after it. Make the handle again after the assignment. |
| `cannot assign over the owner of handle '<h>' inside a loop that uses the handle` | the loop uses `<h>` somewhere in its body, and the next iteration would use the freed object. Make the handle inside the loop, or assign after it. |
| `cannot assign over '<x>' while a defer declared earlier uses handle '<h>' made from it` | the defer runs at the block's end, after the assignment (or `cannot drop`), and would use the freed object. |
| `cannot move '<x>' inside a loop while handle '<h>' made from it is in scope` | a loop moves (or drops) `<x>` while a handle made from it before the loop is in scope. Make the handle inside the loop. |
| `handle '<h>' was made from an owner that a loop moved: make the handle again` | a loop that did not name `<h>` moved its owner, so `<h>` may not be named after it (except in `alive`). |
| `replace cannot change '<r>': it is a reference` (also `take`) | `<r>` is a reference local; replace or take the field or element it borrows. |
| `try needs a function that returns a Result; '<f>' returns <t>` | `try` outside a function whose result is a `Result`; `main` returns nothing, so match a helper's result instead. |
| `try needs a function that returns a Result; there is no function here` | `try` in a global initializer. |
| `try needs a Result value, not <t>` | the operand of `try` is not a `Result`; the hint names `?Result[T]` (check it with `!= nil`) or `err(...)` (it has no success type). |
| `a try expression may only be the value of a let, var or assignment, or a statement` | `try` in a condition, a return value, a match scrutinee or a `for` bound. |
| `a try expression cannot be part of a larger expression; assign it to a local first` | `try` in an argument, a record field, an array length, an index or the right operand of an operator. |
| `cannot use try in a defer: a deferred statement cannot return from the function` | `try` inside `defer`. |
| `ok needs a value, not a void expression` | `ok` of a call that returns nothing. |
| `ok(nil) has no type: give the payload a name first, as in 'let n: ?Node = nil; ok(n)'` | name the typed optional first. |
| `ok cannot hold an error value: type it as a Result first, as in 'let inner: Result[int] = err(...); ok(inner)'` | `ok(err(...))`. |
| `ok takes ownership of its value: it cannot hold a borrowed reference` | `ok(&x)`. |
| `err needs a string message, not <t>` | the first argument of `err` is not a string. |
| `err needs an int code, not <t>` | the second argument of `err` is not an int. |
| `cannot infer a type for '<x>' from an error value; declare it as Result[T]` | `err(...)` needs a declared `Result` destination. |
| `Result needs a type: write Result[T]` | `Result` without its type argument. |
| `a Result match names the ok and err arms; 'else' is not needed` | `else` in a `Result` match. |
| `a consuming match binds its value in every arm: write ok(v) and err(m, c)` | some arms bind and some do not. |
| `a consuming match needs the Result itself, not a reference` | a consuming match on a reference; match the reference non-consumingly. |
| `a consuming match needs a local variable or parameter holding an owned Result` | a consuming match on something that is not an owned result. |
| `openFile needs a string path, not <t>` | the first argument of `openFile` is not a string. |
| `openFile needs a string mode, not <t>` | the second argument of `openFile` is not a string. |
| `unknown open mode '<m>': use "r", "w" or "a"` | a constant `openFile` mode is not one of the three. |
| `readFd needs an int file descriptor, not <t>` | the first argument of `readFd` is not an int. |
| `readFd needs an int count, not <t>` | the second argument of `readFd` is not an int. |
| `readFd needs a positive count` | a constant `readFd` count is zero or negative. |
| `writeFd needs an int file descriptor, not <t>` | the first argument of `writeFd` is not an int. |
| `writeFd needs a []byte, not <t>` | the second argument of `writeFd` is not a `[]byte`. |
| `closeFile needs an int file descriptor, not <t>` | the argument of `closeFile` is not an int. |
| `readDirFd needs an int file descriptor, not <t>` | the first argument of `readDirFd` is not an int. |
| `readDirFd needs an int count, not <t>` | the second argument of `readDirFd` is not an int. |
| `readDirFd needs a positive count` | a constant `readDirFd` count is zero or negative. |
| `readDirFd count is too large` | a constant `readDirFd` count is over 1048576 (1 MiB). |
| `statPath needs a string path, not <t>` | the argument of `statPath` is not a string. |
| `lstatPath needs a string path, not <t>` | the argument of `lstatPath` is not a string. |
| `readLinkPath needs a string path, not <t>` | the argument of `readLinkPath` is not a string. |
| `isatty needs an int file descriptor, not <t>` | the argument of `isatty` is not an int. |
| `getenv needs a string name, not <t>` | the argument of `getenv` is not a string. |
| `terminalWidth needs an int file descriptor, not <t>` | the argument of `terminalWidth` is not an int. |
| `sleepMs needs an int millisecond count, not <t>` | the argument of `sleepMs` is not an int. |
