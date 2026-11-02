# seed/obxc

A prebuilt obxc: the trusted previous stage. `make` uses it to build the current
compiler from `src/obxc.obx`, and the stage tests compare the two.

To change the compiler:

1. Edit `src/obxc.obx` and run `./run_tests.sh`.
   - Program, error, encoder, fuzz and GC tests must pass.
   - `stage/*` tests compare the new compiler with this bootstrap. A change to
     code generation or messages shows up there as differences; review them.
2. `make promote` checks the new compiler reaches a fixed point (it compiles
   itself to identical bytes, twice), runs the suite, and copies it here.

A new language feature takes two rounds: first implement it (without using it in
obxc's own source) and promote; only then can `src/obxc.obx` use it.
