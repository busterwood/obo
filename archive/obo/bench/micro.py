#!/usr/bin/env python3
"""Phase 0 measurements for obo's optimization plan (obo/FORK.md): four
micro-benchmarks, obo/bench/NAME_obo.obx, built with the current compiler.

For each workload:
  time   median wall time of the timed runs (OBO_CHECK unset)
  peak   peak resident memory of the child process (ru_maxrss)
  instr  instructions in the program's code segment, counted by objdump: from
         the entry point to the end of the executable PT_LOAD segment that holds
         it. The runtime's text (at 0x10000000) is not counted.
Each workload also runs once with OBO_CHECK=1, which must exit 0 and print the
expected checksum (a leak exits 5, a double free exits 6).

    python3 obo/bench/micro.py                  all four, 5 timed runs each
    python3 obo/bench/micro.py -n 9 --only fib  more runs, one workload
    python3 obo/bench/micro.py --disasm loop    objdump listing of one code segment

Needs Python 3 and objdump only. The compiler is rebuilt first with make; OBO=path measures another
compiler (for example seed/obo, the compiler of main) instead."""
import argparse
import os
import re
import statistics
import struct
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
WORKLOADS = ['loop', 'fib', 'array', 'alloc']

# Expected output, derived without the compiler:
#   loop   sum of 2*i for i < 1e9                     = n*(n-1)
#   fib    fib(40), with fib(0) = 0 and fib(1) = 1
#   array  sum over rounds r < 200 of sum_i (i + r*(r+1)/2), i < 1e6
#   alloc  sum of i + 2 for i < 2e7                    = n*(n-1)/2 + 2n
EXPECT = {
    'loop': '999999999000000000',
    'fib': '102334155',
    'array': '101333200000000',
    'alloc': '200000030000000',
}

INSTR = re.compile(r'^\s+([0-9a-f]+):\t[0-9a-f ]+\t(\S.*)$')


def build(name, tmp):
    exe = os.path.join(tmp, name)
    subprocess.run([os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo'))), os.path.join(HERE, name + '_obo.obx'),
                    '-o', exe, '--rt', FORK], check=True)
    return exe


def build_peak(tmp):
    """bench/peak.c, the helper bench.py uses: runs a program and reports its peak RSS.
    Python's own RSS would count against the child (a forked child starts with the
    parent's pages until exec), so the helper does the fork and wait4."""
    exe = os.path.join(tmp, 'peak')
    subprocess.run(['gcc', '-O2', os.path.join(HERE, 'peak.c'), '-o', exe], check=True)
    return exe


def run(peak, exe, check=False):
    """Run once: (exit code, stdout, stderr, wall seconds, peak RSS in KB)."""
    env = dict(os.environ)
    env.pop('OBO_CHECK', None)
    if check:
        env['OBO_CHECK'] = '1'
    t0 = time.perf_counter()
    p = subprocess.run([peak, exe], capture_output=True, text=True, env=env)
    dt = time.perf_counter() - t0
    lines = p.stderr.splitlines()
    kb, code = re.match(r'(\d+) KB peak, exit (\d+)$', lines[-1]).groups()   # last line is the helper's
    return int(code), p.stdout.strip(), '\n'.join(lines[:-1]), dt, int(kb)


def code_segment(exe):
    """(entry address, bytes) from the entry point to the end of its executable segment."""
    with open(exe, 'rb') as f:
        data = f.read()
    entry, = struct.unpack_from('<Q', data, 0x18)
    phoff, = struct.unpack_from('<Q', data, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', data, 0x36)
    for i in range(phnum):
        ptype, flags, offset, vaddr, _, filesz = struct.unpack_from('<IIQQQQ', data, phoff + i * phentsize)
        if ptype == 1 and flags & 1 and vaddr <= entry < vaddr + filesz:   # PT_LOAD, PF_X
            return entry, data[offset + entry - vaddr: offset + filesz]
    raise SystemExit(f'{exe}: no executable segment holds the entry point')


def listing(exe):
    """The code segment's instructions, as (address, bytes, text) from objdump."""
    entry, code = code_segment(exe)
    with tempfile.NamedTemporaryFile(suffix='.bin') as f:
        f.write(code)
        f.flush()
        text = subprocess.run(['objdump', '-D', '-b', 'binary', '-mi386:x86-64',
                               f'--adjust-vma={entry:#x}', f.name],
                              check=True, capture_output=True, text=True).stdout
    rows = []
    for line in text.splitlines():
        if line.strip() == '...':
            raise SystemExit(f'{exe}: objdump collapsed a run of zero bytes; the count would be wrong')
        m = INSTR.match(line)            # continuation lines of long instructions have no mnemonic
        if m:
            rows.append((int(m.group(1), 16), m.group(2)))
    return rows


def main():
    ap = argparse.ArgumentParser(description='obo Phase 0 baseline measurements')
    ap.add_argument('-n', '--runs', type=int, default=5, help='timed runs per workload (default 5)')
    ap.add_argument('--only', nargs='+', choices=WORKLOADS, default=WORKLOADS)
    ap.add_argument('--disasm', choices=WORKLOADS,
                    help="print one workload's code segment instead of measuring")
    args = ap.parse_args()
    subprocess.run(['make', '-C', FORK], check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory() as tmp:
        if args.disasm:
            exe = build(args.disasm, tmp)
            for addr, text in listing(exe):
                print(f'{addr:x}:\t{text}')
            return
        peak = build_peak(tmp)
        print('| Benchmark | Median wall time (ms) | Min-max (ms) | Peak RSS (MB) | '
              'Instructions in code segment |')
        print('|---|---|---|---|---|')
        for name in args.only:
            exe = build(name, tmp)
            code, out, err, _, _ = run(peak, exe, check=True)
            if code != 0 or out != EXPECT[name]:
                raise SystemExit(f'{name} with OBO_CHECK=1: exit {code}, output {out!r}, stderr {err!r}')
            times, rss_max = [], 0
            for _ in range(args.runs):
                code, out, err, dt, rss = run(peak, exe)
                if code != 0 or out != EXPECT[name]:
                    raise SystemExit(f'{name}: exit {code}, output {out!r}, stderr {err!r}')
                times.append(dt * 1000)
                rss_max = max(rss_max, rss)
            n_instr = len(listing(exe))
            print(f'| {name} | {statistics.median(times):.0f} | '
                  f'{min(times):.0f}-{max(times):.0f} | {rss_max / 1024:.1f} | {n_instr} |')


if __name__ == '__main__':
    main()
