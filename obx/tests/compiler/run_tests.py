#!/usr/bin/env python3
"""Runs the compiler tests (run, error, unit here) with the conformance runner
(../run.py, same directives); pass --conformance to run the conformance suite
as well. Extra arguments are filters. The compiler is $OBXC, else
compiler/out/obxc.

These were the reference compiler's own tests (compiler choices and runtime
limits rather than the spec), kept when ref was retired (archive/obx-ref/).
unit/ holds run tests that need C flags (`// cflags: -DRT_STR_MAX=64` lowers a
runtime limit so that a test does not need gigabytes); they are built here
through tools/cbuild.py (obxc --emit-c-json, extra flags, cached gcc)."""
import importlib.util, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import cbuild  # noqa: E402
spec = importlib.util.spec_from_file_location('conformance', os.path.join(HERE, '..', 'run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def unit(path):
    d = runner.parse_directives(path)
    flags = [w for l in open(path) if l.startswith('// cflags:') for w in l.split(':', 1)[1].split()]
    with tempfile.TemporaryDirectory(prefix='obxunit_') as tmp:
        exe = os.path.join(tmp, 'p')
        rc, _, err = cbuild.build_obx(path, exe, cflags=flags)
        if rc:
            return f"build failed: {err.strip()[:300]}"
        r = subprocess.run([exe], capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, OBX_CHECK='1'))
    want = ''.join(l + '\n' for l in d['expect'])
    if r.stdout != want or r.returncode != d['exit']:
        return f"exit {r.returncode} (want {d['exit']}), stdout {r.stdout!r} (want {want!r}): {r.stderr[:200]}"


args = [a for a in sys.argv[1:] if a != '--conformance']
status = 0
roots = [HERE] + ([runner.HERE] if '--conformance' in sys.argv else [])
for root in roots:
    runner.HERE = root
    sys.argv = [sys.argv[0], '--compiler', cbuild.OBXC] + args
    print(f"== {os.path.relpath(root, os.path.join(HERE, '..', '..'))}")
    try:
        runner.main()
    except SystemExit as e:
        status = status or e.code
udir = os.path.join(HERE, 'unit')
units = sorted(f for f in os.listdir(udir)
               if f.endswith('.obx') and (not args or any(a in 'unit/' + f for a in args)))
bad = [(f, m) for f in units for m in [unit(os.path.join(udir, f))] if m]
for f, m in bad:
    print(f"FAIL unit/{f}: {m}")
print(f"== tests/compiler/unit: {len(units) - len(bad)} passed, {len(bad)} failed")
sys.exit(status or (1 if bad else 0))
