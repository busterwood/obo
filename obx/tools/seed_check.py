#!/usr/bin/env python3
"""The C seed's fixed point, as compiler/reseed.sh checks it, without writing the seed.

    python3 obx/tools/seed_check.py

1. out/obxc is built from compiler/seed/obxc.c, as compiler/build.sh does (stage 1);
2. rt.obx must be up to date with rt.h (compiler/gen_rt.py --check);
3. stage 1 builds the current sources (stage 2), stage 2 builds them again (stage 3);
4. the C of stages 2 and 3 must be identical, and equal to the seed.

So the seed is the compiler that the sources describe, and it reproduces itself. Every gcc
build goes through the tools/cbuild.py cache, so when nothing changed the check takes
seconds. Run compiler/reseed.sh after changing the compiler.
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COMP = os.path.normpath(os.path.join(HERE, '..', 'compiler'))
sys.path.insert(0, HERE)
import cbuild  # noqa: E402

SEED = os.path.join(COMP, 'seed', 'obxc.c')
MAIN = os.path.join(COMP, 'main.obx')
OBXC = os.path.join(COMP, 'out', 'obxc')
BUILD = ['gcc', '-O2', '-w', '-std=gnu11', '-o', '@OUT@', '@SRC@', '-lm']    # as build.sh


def fail(msg):
    sys.exit(f"seed_check: {msg}")


def emit(exe):
    """(C text, gcc command) of the compiler sources, compiled by exe."""
    r = subprocess.run([exe, MAIN, '--emit-c-json'], capture_output=True, text=True,
                       env=dict(os.environ, OBX_LIB=os.path.join(COMP, '..', 'lib')))
    if r.returncode:
        fail(f"{os.path.relpath(exe, COMP)} cannot compile main.obx:\n{r.stderr.strip()[:2000]}")
    j = json.loads(r.stdout)
    return j['c'], j['cmd']


def build(csrc, cmd, out, private=False):
    rc, err = cbuild.cc_build(csrc, cmd, out, private=private)
    if rc:
        fail(f"gcc failed building {os.path.basename(out)}:\n{err[:2000]}")


def main():
    seed = open(SEED).read()
    os.makedirs(os.path.dirname(OBXC), exist_ok=True)
    build(seed, BUILD, OBXC, private=True)   # its own file: people replace out/obxc
    r = subprocess.run([sys.executable, os.path.join(COMP, 'gen_rt.py'), '--check'])
    if r.returncode:
        sys.exit(1)
    with tempfile.TemporaryDirectory(prefix='obxseed_') as t:
        c1, cmd = emit(OBXC)
        build(c1, cmd, os.path.join(t, 's2'))
        c2, cmd = emit(os.path.join(t, 's2'))
        build(c2, cmd, os.path.join(t, 's3'))
        c3, _ = emit(os.path.join(t, 's3'))
    if c2 != c3:
        fail("no fixed point: the C of stages 2 and 3 differ")
    if c2 != seed:
        fail("the seed is stale: stage 2's C differs from seed/obxc.c (run compiler/reseed.sh)")
    print(f"seed/obxc.c: fixed point, stage 2 == stage 3 == seed ({len(seed.encode())} bytes); "
          f"out/obxc built from it")


if __name__ == '__main__':
    main()
