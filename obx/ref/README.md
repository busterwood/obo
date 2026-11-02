# obx/ref: the reference compiler (still the gcore prototype)

A small obo-like language compiled to C by `gcore.py`, used to test whether
generics, value records, operator methods and associated functions make
decimal, money, Option, Result, sort and Map into library code. See
`../../obo/design/generic-core-rethink.md` for the findings.

    python3 gcore.py examples/money.obx --run     # compile with gcc and run
    python3 gcore.py examples/money.obx --emit-c  # print the generated C
    python3 run_tests.py                         # all examples and rejection tests

`std.obx` is the prelude, compiled before every program. The compiler knows two
names from it: fractional literals call `fromFraction`, and `try` needs `Ok`
and `Err` variants. Everything else in it is ordinary code.

The runtime counts allocations: a program that ends with live objects exits 5,
so every example also tests drop glue.

Not covered: reference invalidation, handles, effects, closures, interfaces as
values, modules, integer overflow checks, real strings. Closed types are always
heap-allocated.
