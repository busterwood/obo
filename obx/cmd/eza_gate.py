#!/usr/bin/env python3
"""Runs the eza golden suite (tests/eza.py) on obx's eza.

    python3 cmd/eza_gate.py [--c] [--exe PATH] [eza.py options, e.g. -k tree -j 8]

The harness builds cmd/eza.obx with compiler/out/obxc (native unless --c) into a
temporary directory, or takes --exe. --reference=none is the default here (the
goldens are the oracle); pass --reference=auto to compare with a live eza too.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # .../obx/cmd
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tests'))
import eza as harness  # noqa: E402


def main(argv):
    if not any(a.startswith('--reference') for a in argv):
        argv = argv + ['--reference=none']
    return harness.main(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
