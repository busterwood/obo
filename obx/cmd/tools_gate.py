#!/usr/bin/env python3
"""Runs the differential gate (tests/tools.py) on obx's cat, wc, sort, head, tail.

    python3 cmd/tools_gate.py [--c] [tools.py options, e.g. -k sort -j 8 --memory]

The harness builds cmd/{cat,wc,sort,head,tail}.obx with compiler/out/obxc
(native unless --c) into a temporary directory and compares them with GNU
coreutils (found on /usr/bin:/bin) and with the own goldens. Without a usable
GNU reference, pass --reference=none: the Python models and the recorded gnu
goldens are the oracle then.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # .../obx/cmd
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tests'))
import tools as harness  # noqa: E402


if __name__ == '__main__':
    sys.exit(harness.main(sys.argv[1:]))
