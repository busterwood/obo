#!/usr/bin/env python3
"""Heap-limit and allocation-bound tests for the freestanding runtime.

    python3 obo/tests/heap_test.py

The rt section builds tests/rt_heap_test.c with runtime/runtime.c and runs each
case in its own process, so OBO_HEAP_LIMIT and RLIMIT_AS reach rt_init exactly
as they do for a compiled program.  It covers:

  * the compiled 4 GiB default and the 64 GiB compiled ceiling;
  * lower and raised limits, free-list reuse, and sparse 32 GiB and 48 GiB
    arenas with objects and strings just across 4 GiB and 8 GiB, at 1/2 and
    3/4 of the arena, and in the last granule (offset 0xffffffff at 64 GiB).
    The mandatory suite reserves about 49 GiB of virtual address space for
    48 GiB; the 64 GiB cases require about 66 GiB and skip cleanly when
    RLIMIT_AS or overcommit cannot supply it;
  * high-offset free/realloc generation checks, including a stale handle that
    must trap after reuse;
  * the canonical OBO_HEAP_LIMIT grammar: integer bytes or KiB/MiB/GiB only,
    no sign/space/fraction/exponent, multiple of 16, 64 KiB .. 64 GiB, and no
    silent clamp above the ceiling;
  * the handle-range guard: exactly 64 GiB (2^32 granules) is accepted and one
    granule more is a clean exit 2;
  * the 4 GiB - 32-byte payload cap checked before zeroing, array/append/concat
    size overflow, the bounded free worklist (exit 6), and ordinary heap
    exhaustion ("obx: out of memory");
  * metadata mapping failure under RLIMIT_AS (a clean exit 2, not a fault).

The program section compiles a normal hello with the current compiler and the
seed and requires byte-identical images, then runs it under the default and the
64 KiB limit.  It also checks the compiler's self-compile live peak against the
2,750,000-byte budget (tests/peak_budget.py).
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


def build_text_harness():
    obj = os.path.join(TMP, 'rt_dec_text_test.o')
    exe = os.path.join(TMP, 'rt_dec_text_test')
    subprocess.run(['gcc', *CFLAGS, '-c', os.path.join(HERE, 'rt_dec_text_test.c'), '-o', obj],
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


# A large arena reserves its size plus a starts bitmap and a worklist.  Below
# the given floor the corresponding cases skip before running; a mapping
# failure above it also skips, so a host with a tight overcommit policy cannot
# turn an unavailable reservation into a test failure.
LARGE_AS_48 = 49 << 30
LARGE_AS_64 = 66 << 30


def check_large_case(exe, name, case, value, env, want=0, stderr_has=None,
                     timeout=180, as_floor=LARGE_AS_64):
    """check_case for a large arena: skip (do not fail) if it cannot map."""
    soft, _ = resource.getrlimit(resource.RLIMIT_AS)
    if soft != resource.RLIM_INFINITY and soft < as_floor:
        print(f"skip {name}: RLIMIT_AS {soft} < {as_floor} bytes")
        return
    p = subprocess.run([exe, case, value], capture_output=True, timeout=timeout, env=env)
    stderr = p.stderr.decode('utf-8', 'replace')
    if p.returncode == 2 and stderr == 'obx: cannot reserve heap\n':
        print(f"skip {name}: {stderr.strip()}")
    elif p.returncode != want:
        failures.append(f"{name}: exit {p.returncode}, expected {want}; stderr {stderr.strip()[:200]!r}")
    elif stderr_has is not None and stderr_has not in stderr:
        failures.append(f"{name}: stderr {stderr.strip()[:200]!r} lacks {stderr_has!r}")
    elif want == 0 and stderr:
        failures.append(f"{name}: unexpected stderr {stderr.strip()[:200]!r}")
    else:
        print(f"ok   {name}")


def rt_section():
    exe = build_harness()

    # The compiled default and explicit limits through 32 GiB are mandatory.
    check_case(exe, 'default 4 GiB', 'default', clean_env())
    check_case(exe, '64KiB lower limit + reuse', 'reuse', clean_env('64KiB'))
    for value in ('65536', '65552', '1MiB', '4GiB', '4294967296',
                  '4294967312', '16GiB', '17179869184', '17179869168',
                  '17179869200', '32GiB', '34359738368'):
        check_case(exe, f'limit {value}', 'limit', clean_env(value), args=(value,))
    # The larger limits reserve their arena plus metadata, so they skip cleanly
    # on a host whose address-space cap is below the floor.
    for value, floor in (('48GiB', LARGE_AS_48),
                         ('64GiB', LARGE_AS_64),
                         ('68719476736', LARGE_AS_64),
                         ('68719476720', LARGE_AS_64)):
        check_large_case(exe, f'limit {value}', 'limit', value, clean_env(value),
                         as_floor=floor)
    # The final start bit for a non-128-aligned limit: control (no last header)
    # and bug case (last 32-byte header in the partial byte) at three sizes.
    for value in ('524304', '524320', '1048592', '1048608',
                  '2097168', '2097184'):
        check_case(exe, f'last-granule fill {value}', 'fill', clean_env(value),
                   args=(value,))

    # 32 GiB and 48 GiB are mandatory; the 64 GiB ceiling cases may skip when
    # the host cannot reserve their ~66 GiB of virtual address space.
    for value in ('32GiB', '48GiB'):
        check_case(exe, f'sparse {value} arena', 'sparse', clean_env(value),
                   args=(value,))
    check_large_case(exe, 'sparse 64GiB arena (last granule 0xffffffff)',
                     'sparse', '64GiB', clean_env('64GiB'))
    check_case(exe, 'stale high handle at 48GiB', 'stale', clean_env('48GiB'),
               want=7, stderr_has='has been freed', args=('48GiB',))
    check_large_case(exe, 'stale high handle at 64GiB', 'stale', '64GiB',
                     clean_env('64GiB'), want=7, stderr_has='has been freed')
    check_case(exe, 'handle-range guard accepts 64GiB', 'guard_ok', clean_env())
    check_case(exe, 'handle-range guard rejects 64GiB+16', 'guard_bad',
               clean_env(), want=2,
               stderr_exact='obx: heap limit exceeds the 64 GiB handle range\n')

    # Bad grammar and out-of-range values: exit 2, the value named, no clamp.
    invalid = [
        '65535',                    # below 64 KiB
        '65537',                    # not a multiple of 16
        '65544',                    # 64 KiB + 8, not a multiple of 16
        '4294967295',               # 4 GiB - 1
        '17179869185',              # 16 GiB + 1, not a multiple of 16
        '17179869199',              # 16 GiB + 15, not a multiple of 16
        '68719476737',              # 64 GiB + 1
        '68719476752',              # 64 GiB + 16 (above the compiled ceiling)
        '137438953472',             # 128 GiB
        '128GiB',                   # 128 GiB, above the compiled ceiling
        '16GiB+16',                 # no implicit clamp
        '64GiB+16',                 # no implicit clamp
        '4GiB+16',                  # not a canonical value
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

    # decimal arrays (kind 7): the empty block, self-append, clone, free and truncate
    for case in ('dec_kind', 'dec_self', 'dec_clone', 'dec_free', 'dec_truncate'):
        check_case(exe, f'decimal array {case}', case, clean_env())

def text_section():
    exe = build_text_harness()
    check_case(exe, 'public max display text 98,342', 'max_output',
               clean_env('4MiB'))
    check_case(exe, 'alloc_raw_try over-cap and arena failure', 'try_bad',
               clean_env('64KiB'))
    check_case(exe, 'alloc_raw_try free-list reuse', 'try_reuse',
               clean_env('64KiB'))
    check_case(exe, 'alloc_raw object-too-large diagnostic', 'alloc_too_large',
               clean_env('64KiB'), want=2, stderr_exact='obx: object too large\n')
    check_case(exe, 'alloc_raw out-of-memory diagnostic', 'alloc_oom',
               clean_env('64KiB'), want=2, stderr_exact='obx: out of memory\n')
    for case in ('parse10', 'format10', 'canonical10', 'check10'):
        check_case(exe, f'{case}: 32-byte err block fits, payload fails',
                   case, clean_env('64KiB'))
    check_case(exe, 'format INEXACT at e+s+t == -1', 'format_truncate',
               clean_env('64KiB'))
    for case in ('bad_exp_sign', 'bad_exp_coeff', 'bad_exp_flags',
                 'bad_exp_pad', 'bad_dscale_sign', 'bad_dscale_coeff'):
        check_case(exe, f'malformed accessor {case}', case, clean_env('64KiB'),
                   want=10, stderr_exact='obo: decimal invalid value\n')
    for case in ('bad_format', 'bad_canonical', 'bad_check'):
        check_case(exe, f'malformed value {case}', case, clean_env('64KiB'))


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
    text_section()
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
