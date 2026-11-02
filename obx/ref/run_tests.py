#!/usr/bin/env python3
"""Runs the reference compiler's own tests (tests/run, tests/error) with the
conformance runner (../tests/run.py, same directives); pass --conformance to
run the conformance suite as well. Extra arguments are filters."""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('conformance', os.path.join(HERE, '..', 'tests', 'run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
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
sys.exit(status)
