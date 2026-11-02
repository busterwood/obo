#!/usr/bin/env python3
"""Runs the reference compiler's own tests (tests/run, tests/error) with the
conformance runner (../tests/run.py, same directives); pass --conformance to
run the conformance suite as well. Extra arguments are filters.

tests/unit holds run tests that need C flags (`// cflags: -DRT_STR_MAX=64`
lowers a runtime limit so that a test does not need gigabytes); they are built
here with --emit-c and gcc."""
import importlib.util, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('conformance', os.path.join(HERE, '..', 'tests', 'run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def unit(path):
    d = runner.parse_directives(path)
    flags = [w for l in open(path) if l.startswith('// cflags:') for w in l.split(':', 1)[1].split()]
    with tempfile.TemporaryDirectory(prefix='obxunit_') as tmp:
        c = subprocess.run([sys.executable, os.path.join(HERE, 'obxc.py'), path, '--emit-c'],
                           capture_output=True, text=True)
        if c.returncode:
            return f"compile failed: {c.stderr.strip()[:300]}"
        open(os.path.join(tmp, 'p.c'), 'w').write(c.stdout)
        g = subprocess.run(['gcc', '-O2', '-w', '-std=gnu11', *flags, '-o', os.path.join(tmp, 'p'),
                            os.path.join(tmp, 'p.c'), '-lm'], capture_output=True, text=True)
        if g.returncode:
            return f"gcc failed: {g.stderr[:300]}"
        r = subprocess.run([os.path.join(tmp, 'p')], capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, OBX_CHECK='1'))
    want = ''.join(l + '\n' for l in d['expect'])
    if r.stdout != want or r.returncode != d['exit']:
        return f"exit {r.returncode} (want {d['exit']}), stdout {r.stdout!r} (want {want!r}): {r.stderr[:200]}"


args = [a for a in sys.argv[1:] if a != '--conformance']
status = 0
roots = [os.path.join(HERE, 'tests')] + ([runner.HERE] if '--conformance' in sys.argv else [])
for root in roots:
    runner.HERE = root
    sys.argv = [sys.argv[0], '--compiler', os.path.join(HERE, 'obxc.py')] + args
    print(f"== {os.path.relpath(root, HERE)}")
    try:
        runner.main()
    except SystemExit as e:
        status = status or e.code
udir = os.path.join(HERE, 'tests', 'unit')
units = sorted(f for f in os.listdir(udir)
               if f.endswith('.obx') and (not args or any(a in 'unit/' + f for a in args)))
bad = [(f, m) for f in units for m in [unit(os.path.join(udir, f))] if m]
for f, m in bad:
    print(f"FAIL unit/{f}: {m}")
print(f"== tests/unit: {len(units) - len(bad)} passed, {len(bad)} failed")
sys.exit(status or (1 if bad else 0))
