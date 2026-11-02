#!/usr/bin/env python3
"""split.py batch.txt [dir]: write each '=== name' section to dir/name.obx (default cases/)"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(HERE, 'cases')
cur, buf = None, []
def flush():
    if cur: open(os.path.join(OUT, cur + '.obx'), 'w').write(''.join(buf).strip('\n') + '\n')
for line in open(sys.argv[1]):
    if line.startswith('=== '):
        flush(); cur, buf = line[4:].strip(), []
    else: buf.append(line)
flush()
