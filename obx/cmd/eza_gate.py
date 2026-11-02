#!/usr/bin/env python3
"""Runs obo's eza golden suite (obo/tests/eza.py) against obx's eza.

    python3 cmd/eza_gate.py [--c] [--exe PATH] [eza.py options, e.g. -k tree -j 8]

builds cmd/eza.obx with compiler/out/obxc (native unless --c) into a
temporary directory, or takes --exe, and hands it to obo's harness in place
of obo's own build. The harness, its cases and goldens are obo's, unchanged;
--reference=none is the default (the goldens are the oracle).
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))       # .../obx/cmd
OBX = os.path.dirname(HERE)
OBO_TESTS = os.path.join(os.path.dirname(OBX), 'obo', 'tests')


def main(argv):
    exe = None
    native = True
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == '--c':
            native = False
        elif argv[i] == '--exe' and i + 1 < len(argv):
            i += 1
            exe = os.path.abspath(argv[i])
        else:
            rest.append(argv[i])
        i += 1
    if not any(a.startswith('--reference') for a in rest):
        rest.append('--reference=none')
    tmp = tempfile.mkdtemp(prefix='obx-eza-')
    if exe is None:
        exe = os.path.join(tmp, 'eza')
        cmd = [os.path.join(OBX, 'compiler', 'out', 'obxc'),
               os.path.join(HERE, 'eza.obx'), '-o', exe]
        if native:
            cmd.insert(2, '--native')
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print('eza_gate: build failed', file=sys.stderr)
            return 2
    print('eza_gate: %s (%s)' % (exe, 'native' if native else 'C'))

    sys.path.insert(0, OBO_TESTS)
    import eza as harness

    def build(self):
        self.exe = exe
    harness.Harness.build = build
    return harness.main(rest)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
