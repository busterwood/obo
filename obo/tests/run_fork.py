#!/usr/bin/env python3
"""Test runner for obo, the ownership fork.

Reuses the mainline runner (../../tests/run.py) with obo as the compiler and the
fork's runtime. Every program runs with OBO_CHECK=1, so a program that ends with
live objects fails (exit 5), as does a double free (exit 6); freed memory is
poisoned, so use after free shows up as garbage.

    python3 obo/tests/run_fork.py [-k pattern] [-v] [-x] [-j N]

  obo/tests/run/*.obx      programs, with // expect: comments as in mainline
  obo/tests/errors/*.obx   ownership errors, as in mainline (// === cases)
  own-fuzz/*               random ownership programs, checked against a model
                           (own_fuzz.py; --count is the number of seeds per profile)

  -x, --fail-fast  stop after the first failing test. The failing test is still
                   reported, and the summary counts the tests not run.
  -j, --jobs N     run up to N tests at once (default 1). The cases are
                   independent: each compiles into a name of its own (run.unique
                   is locked) and gets its own scratch directory (run_exe), and
                   own_fuzz names its files by seed. With N > 1 the report is in
                   completion order, so which failure is reported first (and,
                   with --fail-fast, which tests get to run) may vary.
  --smoke          run the smoke subset (SMOKE) first, then the remaining tests.
                   The subset is a strict subset of the full suite, chosen from a
                   real run of the mutants, whose first failing tests it covers
                   best; a failure there is a failure of the full suite, and a
                   pass still runs every other test. It is only a reorder, so the
                   verdict is the same; it saves time on a mutant that fails in a
                   late test by trying the likely ones early. A name that is no
                   longer a test is reported and ignored: the suite still runs.
"""
import concurrent.futures
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(FORK), 'obx', 'tests'))
os.environ['OBO_CHECK'] = '1'
import run  # noqa: E402
import own_fuzz  # noqa: E402
import module_test  # noqa: E402
import sym_test  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK                                   # --rt: the fork's runtime
FUZZ_COUNT = 50                                   # seeds per profile for own-fuzz

# The smoke subset: the tests that a real fail-fast run of the 326 mutants found
# first, weighted by how much of the suite a failure there lets a mutant skip.
# Mostly error cases (they are cheap, and a mutant often breaks one), plus the
# run/ programs that caught the most. Thirty tests is where the gain flattens: in
# the measured cold run 138 of the 311 caught mutants failed here (44%), and the
# suite cost 186 s CPU (17.4 s wall) against 234 s (55.5 s wall) with no subset.
SMOKE = [
    "errors/arg_order.obx:an element of an array passed by reference, then the array moved by a later argument",
    "errors/fp_loops.obx:the owner moved in a loop, the handle named after the loop",
    "errors/aliasing.obx:a change through a parameter may free inside a global: the parameter may be it",
    "errors/defer_uses.obx:a defer reads a reference that is pointed at a new target and the target is freed",
    "errors/alive_narrow.obx:a loop in the guarded block that uses and frees",
    "errors/aliasing.obx:a mut call through a parameter reaches another parameter",
    "errors/fp_refs.obx:a mut callee that frees in a loop, the reference read in the loop",
    "errors/fp_loops.obx:a free before a break, the handle used after the loop",
    "errors/defer_uses.obx:two defers: the newer one frees what the older one reads",
    "errors/assignments.obx:a call that might free the array being assigned into is an error",
    "errors/arg_order.obx:writeFile's bytes, then its flag argument replaces them",
    "errors/ref_local_paths.obx:a held place under a reference that points into either of two places: a replace through it frees the array indexed",
    "errors/ref_bindings.obx:a while condition that frees a global the reference points into, and the body reads it at the top",
    "errors/ref_bindings.obx:a reference joined from a place and the place below it: a change at the lower place is seen",
    "errors/alive_narrow.obx:a move of the owner in a narrowed arm, after a returning arm moved it (refused like the same arm without the earlier move; conservative: the object lives on in c)",
    "errors/alive_narrow.obx:alive and something, or something",
    "errors/move_bits.obx:the 63rd parameter is always assumed to free: a callee that only appends through it is moving",
    "errors/held_places.obx:a handle's array indexed by an index that moves the object: a replace moves the old object out of its owner",
    "errors/held_places.obx:a reference through a handle: a replace of what it points into frees the array a local indexes, whatever names it",
    "errors/handle_frees.obx:a defer in a nested block assigns the owner: it runs where that block is left",
    "errors/handle_frees.obx:an assignment in the first arm of a match on an int",
    "errors/fp_loops.obx:a free before a break arm: the free is in the arm's block, but an outer loop repeats the use",
    "errors/arg_order.obx:an element of a global, then a later argument's call frees the global",
    "errors/call_targets.obx:an index through a mut array parameter whose index calls a function",
    "errors/call_targets.obx:an index of a whole local array whose index moves the array",
    "errors/alive_narrow.obx:a move of the owner after an early exit does not make the handle usable",
    "errors/call_targets.obx:a field's array indexed by a pure call that consumes the old array the index replaces",
    "errors/alive_narrow.obx:a free in the guarded block before the use",
    "run/fp_loops.obx",
    "run/ref_bindings.obx",
    "modules/run/basic",
    "modules/errors/private_variant",
    "sym/roundtrip_rich",
    "sym/malformed_structures",
    "sym/b6_stub_order",
]


def program_test(p):
    """A tests/run program. One that starts with '// prelude: io' is compiled
    with obo/lib/io.obo concatenated before it: the library's build rule, which
    io_test.py also applies for the tests/lib programs."""
    with open(p) as f:
        text = f.read()
    if not text.startswith('// prelude: io'):
        return run.program_test(p)
    lib = open(os.path.join(FORK, 'lib', 'io.obo')).read()
    fd, merged = tempfile.mkstemp(prefix='io-prelude-', suffix='.obx')
    try:
        os.write(fd, (lib + '\n' + text).encode())
        os.close(fd)
        fd = -1
        return run.program_test(merged)
    finally:
        if fd >= 0:
            os.close(fd)
        os.unlink(merged)


def main():
    args = sys.argv[1:]
    pattern = args[args.index('-k') + 1] if '-k' in args else ''
    verbose = '-v' in args
    fail_fast = '-x' in args or '--fail-fast' in args
    smoke = '--smoke' in args
    jobs = int(args[args.index('-j') + 1]) if '-j' in args else \
        int(args[args.index('--jobs') + 1]) if '--jobs' in args else 1
    tests = []
    for f in sorted(os.listdir(os.path.join(HERE, 'run'))):
        if f.endswith('.obx'):
            p = os.path.join(HERE, 'run', f)
            tests.append((f"run/{f}", lambda p=p: program_test(p)))
    for f in sorted(os.listdir(os.path.join(HERE, 'errors'))):
        if f.endswith('.obx'):
            for name, src, lo, hi in run.error_cases(os.path.join(HERE, 'errors', f)):
                tests.append((f"errors/{name}", lambda s=src, lo=lo, hi=hi: run.error_test(s, lo, hi)))
    tests += own_fuzz.tests(FUZZ_COUNT)
    tests += module_test.tests()               # source modules: run, errors, --deps, tokens
    tests += sym_test.tests()                  # Stage 2 symbol files: writer, reader, --check, staleness
    smoke_names = set()
    if smoke:
        known = {name for name, _ in tests}
        for name in SMOKE:
            if name not in known:
                print(f"note: smoke test not found: {name}", file=sys.stderr)
        smoke_names = {name for name in SMOKE if name in known}
        if len(smoke_names) < len(tests):
            # a reorder only: every test still runs, the likely ones first. With a
            # failure among them the run can stop early; a pass runs all the rest.
            tests = [t for t in tests if t[0] in smoke_names] + [t for t in tests if t[0] not in smoke_names]
    tests = [t for t in tests if pattern in t[0]]
    t0 = time.perf_counter()
    failed = 0
    ran = 0

    def report(name, e):
        """Prints a test's result; True if it failed"""
        if e is None:
            if verbose:
                print(f"ok   {name}")
            return False
        print(f"FAIL {name}\n     " + str(e).replace('\n', '\n     '))
        if name in smoke_names:
            print("     (in the smoke subset)")
        return True

    if jobs <= 1:
        for name, fn in tests:
            ran += 1
            try:
                fn()
                e = None
            except Exception as exc:
                e = exc
            if report(name, e):
                failed += 1
                if fail_fast:
                    break
    else:
        with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
            pending = {}
            it = iter(tests)

            def fill():
                # keep at most jobs cases scheduled: with --fail-fast, a failure
                # must not have already queued the whole suite behind it
                while len(pending) < jobs:
                    try:
                        name, fn = next(it)
                    except StopIteration:
                        return
                    pending[pool.submit(fn)] = name

            fill()
            stop = False
            while pending:
                finished, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in finished:
                    name = pending.pop(f)
                    if f.cancelled():
                        continue
                    try:
                        f.result()                          # a test's return value is not a result
                        e = None
                    except Exception as exc:
                        e = exc
                    ran += 1
                    if report(name, e):
                        failed += 1
                        stop = stop or fail_fast
                if stop:                                    # not started: the first failure ends the run
                    for f in pending:
                        f.cancel()                          # the ones already running finish, and are counted
                else:
                    fill()
    not_run = len(tests) - ran
    tail = f", {not_run} not run" if not_run else ""
    print(f"\n{ran - failed} passed, {failed} failed{tail} in {time.perf_counter() - t0:.1f} s")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
