#!/usr/bin/env python3
"""split.py batch.txt: write each '=== name' section to cases/name.obx"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
cur, buf = None, []
def flush():
    if cur: open(os.path.join(HERE, 'cases', cur + '.obx'), 'w').write(''.join(buf).strip('\n') + '\n')
for line in open(sys.argv[1]):
    if line.startswith('=== '):
        flush(); cur, buf = line[4:].strip(), []
    else: buf.append(line)
flush()
