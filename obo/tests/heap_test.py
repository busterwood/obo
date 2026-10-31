#!/usr/bin/env python3
"""Heap-limit and allocation-bound tests for the freestanding runtime.

    python3 obo/tests/heap_test.py

The rt section builds tests/rt_heap_test.c with runtime/runtime.c and runs each
case in its own process, so OBO_HEAP_LIMIT and RLIMIT_AS reach rt_init exactly
as they do for a compiled program.  It covers:

  * the compiled 4 GiB default and the 16 GiB compiled ceiling;
  * lower and raised limits, free-list reuse, and a sparse 16 GiB arena with
    objects and strings just across 4 GiB and 8 GiB and in the last granule;
  * the canonical OBO_HEAP_LIMIT grammar: integer bytes or KiB/MiB/GiB only,
    no sign/space/fraction/exponent, multiple of 16, 64 KiB .. 16 GiB, and no
    silent clamp above the ceiling;
  * the 4 GiB - 32-byte payload cap checked before zeroing, array/append/concat
    size overflow, the bounded free worklist (exit 6), and ordinary heap
    exhaustion ("obx: out of memory");
  * metadata mapping failure under RLIMIT_AS (a clean exit 2, not a fault).

The program section compiles a normal hello with the current compiler and the
seed and requires byte-identical images, then runs it under the default and the
64 KiB limit.  It also checks the compiler's self-compile live peak against the
2,500,000-byte numeric budget.
"""
import atexit
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
sys.path.insert(0, HERE)
import run  # noqa: E402
from peak_budget import PEAK_BUDGET  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK
TMP = tempfile.mkdtemp(prefix='obo-heap-test-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

CFLAGS = ('-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie '
          '-fno-stack-protector -fno-asynchronous-unwind-tables -mcmodel=small '
          '-fno-tree-loop-distribute-patterns -Wall').split()

failures = []


def clean_env(limit=None, check=True):
    """The runner's environment without heap/GC/check variables, then ours."""
    e = {k: v for k, v in os.environ.items()
         if k not in ('OBO_HEAP_LIMIT', 'OBO_CHECK', 'OBO_STATS', 'OBX_GC')}
    if limit is not None:
        e['OBO_HEAP_LIMIT'] = limit
    if check:
        e['OBO_CHECK'] = '1'
    e['OBX_GC'] = 'poison'
    return e


def build_harness():
    obj = os.path.join(TMP, 'rt_heap_test.o')
    exe = os.path.join(TMP, 'rt_heap_test')
    subprocess.run(['gcc', *CFLAGS, '-c', os.path.join(HERE, 'rt_heap_test.c'), '-o', obj],
                   check=True, capture_output=True)
    subprocess.run(['ld', '-T', os.path.join(FORK, 'runtime', 'rt.ld'), '-o', exe, obj],
                   check=True, capture_output=True)
    return exe


def limit_as(n):
    def set_limit():
        resource.setrlimit(resource.RLIMIT_AS, (n, n))
    return set_limit


def check_case(exe, name, case, env, want=0, stderr_has=None, stderr_exact=None,
               address_space=None, args=()):
    p = subprocess.run([exe, case, *args], capture_output=True, timeout=120, env=env,
                       preexec_fn=limit_as(address_space) if address_space else None)
    stderr = p.stderr.decode('utf-8', 'replace')
    if p.returncode != want:
        failures.append(f"{name}: exit {p.returncode}, expected {want}; stderr {stderr.strip()[:200]!r}")
    elif stderr_exact is not None and stderr != stderr_exact:
        failures.append(f"{name}: stderr {stderr!r}, expected {stderr_exact!r}")
    elif stderr_has is not None and stderr_has not in stderr:
        failures.append(f"{name}: stderr {stderr.strip()[:200]!r} lacks {stderr_has!r}")
    elif stderr_exact is None and want == 0 and stderr:
        failures.append(f"{name}: unexpected stderr {stderr.strip()[:200]!r}")
    else:
        print(f"ok   {name}")


def rt_section():
    exe = build_harness()

    # The compiled default and explicit limits inside [64 KiB, 16 GiB].
    check_case(exe, 'default 4 GiB', 'default', clean_env())
    check_case(exe, '64KiB lower limit + reuse', 'reuse', clean_env('64KiB'))
    for value in ('65536', '65552', '1MiB', '4GiB', '4294967296',
                  '4294967312', '16GiB', '17179869184', '17179869168'):
        check_case(exe, f'limit {value}', 'limit', clean_env(value), args=(value,))
    # The final start bit for a non-128-aligned limit: control (no last header)
    # and bug case (last 32-byte header in the partial byte) at three sizes.
    for value in ('524304', '524320', '1048592', '1048608',
                  '2097168', '2097184'):
        check_case(exe, f'last-granule fill {value}', 'fill', clean_env(value),
                   args=(value,))
    check_case(exe, 'sparse 16GiB arena', 'sparse', clean_env('16GiB'))

    # Bad grammar and out-of-range values: exit 2, the value named, no clamp.
    invalid = [
        '65535',                    # below 64 KiB
        '65537',                    # not a multiple of 16
        '65544',                    # 64 KiB + 8, not a multiple of 16
        '4294967295',               # 4 GiB - 1
        '17179869185',              # 16 GiB + 1
        '17179869200',              # 16 GiB + 16 (above the compiled ceiling)
        '17179869199',              # above the ceiling, not a multiple
        '34359738368',              # 32 GiB
        '4GiB+16',                  # not a canonical value
        '16GiB+16',                 # no implicit clamp
        '4GB', '4G', '4GiBB', '4Gi', '4GiB0',
        '64kib', '64KIB', '64Kib', '64KiBx',
        '4.0GiB', '4e3', '-1', '+4GiB', 'NaN', 'Infinity',
        '0', '00', '0KiB', '064KiB', '04GiB',
        '', ' ', ' 64KiB', '64KiB ', '\t64KiB',
        '999999999999999999999GiB',
        '18446744073709551616',     # 2^64
        '18446744073709551615',     # fits u64, above the ceiling
    ]
    for value in invalid:
        check_case(exe, f'invalid {value!r}', 'default', clean_env(value), want=2,
                   stderr_exact='obo: invalid OBO_HEAP_LIMIT: ' + value + '\n')

    # Bounds and arithmetic traps.
    check_case(exe, '4 GiB rounded object rejected', 'object_too_large',
               clean_env('64KiB'), want=2, stderr_has='obx: object too large')
    check_case(exe, 'largest payload passes cap, fails arena', 'at_cap',
               clean_env('64KiB'), want=2, stderr_has='obx: out of memory')
    check_case(exe, 'array size product overflow', 'array_overflow',
               clean_env('64KiB'), want=2, stderr_has='obx: allocation size overflow')
    check_case(exe, 'concat length sum overflow', 'concat_overflow',
               clean_env('64KiB'), want=2, stderr_has='obx: allocation size overflow')
    check_case(exe, 'append doubling overflow', 'append_overflow',
               clean_env('64KiB'), want=2, stderr_has='obx: allocation size overflow')
    check_case(exe, 'free worklist bound', 'free_depth',
               clean_env('64KiB'), want=6, stderr_has='object graph too deep to free')
    check_case(exe, 'ordinary heap exhaustion', 'top_exhaust',
               clean_env('64KiB'), want=2, stderr_has='obx: out of memory')

    # Address-space failures are clean exits, not segfaults.  The first lets the
    # 4 GiB arena and its 32 MiB starts bitmap map but not the 512 MiB worklist.
    check_case(exe, 'metadata mapping failure under RLIMIT_AS', 'default',
               clean_env(), want=2, stderr_has='obx: cannot reserve heap metadata',
               address_space=(4 << 30) + (160 << 20))
    check_case(exe, 'heap mapping failure under RLIMIT_AS', 'default',
               clean_env('4GiB'), want=2, stderr_has='obx: cannot reserve heap',
               address_space=1 << 30)
    check_case(exe, 'tiny heap metadata scales down', 'limit', clean_env('64KiB'),
               address_space=32 << 20, args=('64KiB',))


def program_section():
    src = os.path.join(TMP, 'hello.obx')
    with open(src, 'w') as f:
        f.write('fn main() {\n    println("hello, world")\n}\n')
    seed = os.path.join(FORK, 'seed', 'obo')
    out_cur = os.path.join(TMP, 'hello-obo')
    out_seed = os.path.join(TMP, 'hello-seed')
    p = subprocess.run([run.OBXC, src, '-o', out_cur, '--rt', FORK],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        failures.append(f'hello compile with {run.OBXC}: exit {p.returncode}: {p.stderr.strip()[:200]}')
        return
    p = subprocess.run([seed, src, '-o', out_seed, '--rt', FORK],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        failures.append(f'hello compile with seed: exit {p.returncode}: {p.stderr.strip()[:200]}')
        return
    a = open(out_cur, 'rb').read()
    b = open(out_seed, 'rb').read()
    if a != b:
        failures.append('normal hello is not byte-identical between the compiler and the seed')
    else:
        print('ok   hello byte-identical compiler vs seed')
    for label, env in (('default', clean_env(check=False)),
                       ('64KiB', clean_env('64KiB', check=False))):
        r = subprocess.run([out_cur], capture_output=True, timeout=60, env=env)
        if r.returncode != 0 or r.stdout != b'hello, world\n':
            failures.append(f'hello under {label}: exit {r.returncode}, stdout {r.stdout!r}, '
                            f'stderr {r.stderr[:160]!r}')
        else:
            print(f'ok   hello under {label}')

    # The same runtime is embedded in the compiler, so its self-compile peak is
    # the live-memory budget's direct measurement.
    out = os.path.join(TMP, 'self.out')
    env = clean_env(check=False)
    env['OBO_STATS'] = '1'
    p = subprocess.run([run.OBXC, os.path.join(FORK, 'src', 'obo.obo'),
                        '-o', out, '--rt', FORK],
                       capture_output=True, text=True, timeout=300, env=env)
    m = re.search(r'obo: (\d+) peak bytes', p.stderr)
    if p.returncode != 0 or not m:
        failures.append(f'self-compile: exit {p.returncode}: {p.stderr.strip()[:200]}')
    elif int(m.group(1)) > PEAK_BUDGET:
        failures.append(f'self-compile peak {m.group(1)} exceeds {PEAK_BUDGET}')
    else:
        print(f'ok   self-compile peak {m.group(1)} <= {PEAK_BUDGET}')


def main():
    rt_section()
    program_section()
    if failures:
        print(f'\n{len(failures)} heap test(s) failed')
        for f in failures:
            print('FAIL ' + f)
        return 1
    print('\nall heap-limit checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
