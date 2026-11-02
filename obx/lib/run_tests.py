#!/usr/bin/env python3
"""Runs the library tests (lib/tests/run, lib/tests/error) with the conformance
runner (../tests/run.py: same directives, run tests under OBX_CHECK=1).

    python3 obx/lib/run_tests.py [--compiler PATH] [-j N] [filters...]

The default compiler is $OBXC, else ../compiler/out/obxc, which finds the
library modules here by default."""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('conformance', os.path.join(HERE, '..', 'tests', 'run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
default = runner.cbuild.OBXC   # $OBXC, else ../compiler/out/obxc
runner.HERE = os.path.join(HERE, 'tests')
if '--compiler' not in sys.argv:
    sys.argv[1:1] = ['--compiler', default]
runner.main()
