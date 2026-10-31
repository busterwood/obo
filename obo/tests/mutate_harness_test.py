#!/usr/bin/env python3
"""Fast regression tests for the mutation harness itself, with fake builders and
matchers: no compiler is built and no suite is run.

    python3 obo/tests/mutate_harness_test.py

They pin the safety properties the catcher and timing work must keep: a catcher
can shorten a run only by finding a real difference; a pass, an unknown check or
a load timeout still runs the full comparison; the full verdicts and counts are
unchanged; and the fork judge does not run the module and .sym suites twice.
"""
import concurrent.futures
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mutate_fork as mf  # noqa: E402
import mutate_io as mio  # noqa: E402
import mutate_port as mp  # noqa: E402
import port_compare as pc  # noqa: E402


@contextlib.contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def test_multisite_edits_and_stale_site():
    src = 'alpha beta'
    text, stale = mf.apply_edits(src, [('alpha', 'ALPHA'), ('beta', 'BETA')])
    assert text == 'ALPHA BETA' and stale is None
    text, stale = mf.apply_edits(src, [('alpha', 'ALPHA'), ('missing', 'X')])
    assert text is None and 'site 2' in stale
    text, stale = mp.apply_edits(src, [('alpha', 'ALPHA')])
    assert text == 'ALPHA beta' and stale is None
    text, stale = mp.apply_edits(src, [('missing', 'X')])
    assert text is None and 'pattern found 0 times' in stale


def test_catcher_failure_short_circuits():
    calls = []

    def flat(text):
        calls.append(('flat', text))
        return ('ok', (0, b'', b'')), ('ok', (1, b'', b''))

    def tree(key):
        calls.append(('tree', key))
        return ['a tree difference']

    key, transient, unknown = pc.run_catchers(['flat case'], {'flat case': 'T'}, flat, tree)
    assert key == 'flat case' and not transient and not unknown
    assert calls == [('flat', 'T')]


def test_catcher_pass_falls_back_to_full():
    def flat(text):
        return ('ok', (0, b'', b'')), ('ok', (0, b'', b''))

    key, _, unknown = pc.run_catchers(['flat case'], {'flat case': 'T'}, flat, lambda k: [])
    assert key is None and unknown == []
    # ... and the full comparison still decides (here: it misses)
    def fake_compare(*args, **kwargs):
        return [], [], 1
    with patched(pc, 'compare', fake_compare):
        status, line = mp.try_mutant('not a mutant', 'src', 1, use_catchers=False)
    assert status == 'missed'


def test_unknown_catcher_is_not_a_pass_or_a_catch():
    key, _, unknown = pc.run_catchers(['no such check'], {},
                                      lambda t: ('ok', (0, b'', b'')),
                                      lambda k: [])
    assert key is None and unknown == ['no such check']


def test_timeout_and_oom_are_not_catches():
    ref = ('ok', (0, b'', b''))
    stats = pc.mutate_stats.Stats()
    key, _, _ = pc.run_catchers(['flat case'], {'flat case': 'T'},
                                lambda t: (ref, ('ok', ('timeout', b'', b''))),
                                lambda k: [], stats=stats)
    assert key is None and stats.get('retries') == 1
    # an out-of-memory difference is a catch, but marked for the serial
    # confirmation that main() runs; it is not treated as a load failure here
    key, transient, _ = pc.run_catchers(['flat case'], {'flat case': 'T'},
                                        lambda t: (ref, ('crash', 'exit 1: out of memory')),
                                        lambda k: [], stats=stats)
    assert key == 'flat case' and transient
    # a tree check that only times out is also left to the full path
    key, _, _ = pc.run_catchers(['modules/run/basic'], {},
                                lambda t: (ref, ref),
                                lambda k: ['cli x: a run timed out'], stats=stats)
    assert key is None and stats.get('retries') == 2
    # ... and a tree out-of-memory is a catch for the serial confirmation
    key, transient, _ = pc.run_catchers(['modules/run/basic'], {},
                                        lambda t: (ref, ref),
                                        lambda k: ['sym/x: obx: out of memory'], stats=stats)
    assert key == 'modules/run/basic' and transient


def test_port_catcher_is_passed_and_catch_is_kept():
    name = 'error message changed'
    seen = {}

    def fake_compare(tmp, src, quick=False, workers=None, catchers=None, stats=None,
                     catchers_only=False):
        seen['catchers'] = list(catchers or [])
        assert quick and not catchers_only
        return ['FAIL catcher case'], ['catcher case differs from REF'], 1

    with patched(pc, 'compare', fake_compare):
        status, line = mp.try_mutant(name, 'src', 1, workers=1)
    assert status == 'caught'
    assert seen['catchers'] == list(mp.catchers_for(name))
    assert 'catcher case' in line


def test_port_peak_margin_still_catches():
    def fake_compare(*args, **kwargs):
        return [], [], 104
    with patched(pc, 'compare', fake_compare):
        status, line = mp.try_mutant('parameters kept', 'src', 100)
    assert status == 'caught' and 'peak' in line


def test_port_stale_catcher_falls_back_to_full():
    # A catcher name that is no longer in the catalogue is reported by
    # validate_catchers and never passed to a mutant as a catch.
    unknown, checks = mp.validate_catchers({'m': 'no such check'}, ['m'])
    assert unknown == [('m', 'no such check')] and checks == []


def test_validate_catchers_returns_every_distinct_check():
    table = {'a': 'cli --version', 'b': 'cli --version',
             'c': 'modules/run/basic', 'd': 'no such check'}
    unknown, checks = mp.validate_catchers(table, ['a', 'b', 'c', 'd'])
    assert checks == ['cli --version', 'modules/run/basic']
    assert unknown == [('d', 'no such check')]


def test_special_pair_returns_data_and_raises_harness_error():
    tmp = tempfile.mkdtemp(prefix='special-pair-')
    try:
        def good(exe, root, quick, stats=None):
            return (exe, quick)                         # an ordinary outcome difference

        a, b = pc.special_pair('x', good, tmp, True, 'PORT')
        assert a != b

        def boom(exe, root, quick, stats=None):
            raise ValueError('a helper bug, not a mutant outcome')

        try:
            pc.special_pair('x', boom, tmp, True, 'PORT')
        except pc.HarnessError as exc:
            assert 'ValueError' in str(exc)
        else:
            raise AssertionError('a raising special helper was not a harness error')

        def slow(exe, root, quick, stats=None):
            raise subprocess.TimeoutExpired('cmd', 1)

        a, b = pc.special_pair('x', slow, tmp, True, 'PORT')
        assert a == ('timeout',) and b == ('timeout',)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fork_summary_uses_actual_statuses_and_notes_catalog_caught():
    results = [['caught_but_catalog_equiv', 'caught', 'line', 'out', None],
               ['really_equiv', 'equiv', 'line', 'out', None],
               ['really_missed', 'missed', 'line', 'out', None]]
    summary = mf.summarize(results, {'caught_but_catalog_equiv': 'reason',
                                    'really_equiv': 'reason',
                                    'missing_from_run': 'reason'})
    assert summary == {'caught': 1, 'equiv': 1, 'missed': 1,
                       'catalog_caught': ['caught_but_catalog_equiv']}


def test_fork_serial_status_replaces_parallel_status_in_summary():
    calls = []

    def fake_build(src, fail_fast=False, jobs=1, smoke=False, stats=None):
        return subprocess.CompletedProcess([], 0, stdout='100 passed, 0 failed in 1 s\n', stderr=''), None

    def fake_try(src, name, jobs=1, stats=None):
        calls.append((name, jobs))
        if len(calls) == 1:
            return 'caught', 'caught  eq', 'timed out'
        return 'equiv', 'EQUIV   eq: reason', ''

    old = (mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT)
    mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT = {'eq': [('x', 'y')]}, {}, {'eq': 'reason'}
    try:
        with patched(mf, 'build_and_run', fake_build), patched(mf, 'try_mutant', fake_try), \
             patched(sys, 'argv', ['mutate_fork.py', '']):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    mf.main()
                except SystemExit as exc:
                    assert exc.code == 0
            text = out.getvalue()
            assert '0/1 mutants caught, 1 equivalent' in text
            assert 'catalog-equivalent' not in text
            assert len(calls) == 2 and calls[1][1] == 1     # the serial re-run replaced the catch
    finally:
        mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT = old


def test_fork_notice_names_catalog_equivalent_mutants_that_were_caught():
    def fake_build(src, fail_fast=False, jobs=1, smoke=False, stats=None):
        return subprocess.CompletedProcess([], 0, stdout='100 passed, 0 failed in 1 s\n', stderr=''), None

    def fake_try(src, name, jobs=1, stats=None):
        return 'caught', 'caught  eq', ''

    old = (mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT)
    mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT = {'eq': [('x', 'y')]}, {}, {'eq': 'reason'}
    try:
        with patched(mf, 'build_and_run', fake_build), patched(mf, 'try_mutant', fake_try), \
             patched(sys, 'argv', ['mutate_fork.py', '']):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    mf.main()
                except SystemExit as exc:
                    assert exc.code == 0
            text = out.getvalue()
            assert '1/1 mutants caught, 0 equivalent' in text
            assert "1 catalog-equivalent mutant(s) were caught by the suite: eq" in text
    finally:
        mf.MUTANTS, mf.MULTI_MUTANTS, mf.EQUIVALENT = old


def test_fork_equivalent_mutant_runs_to_a_pass():
    calls = {}

    def fake_build(src, fail_fast=False, jobs=1, smoke=False, stats=None):
        calls.update(fail_fast=fail_fast, jobs=jobs, smoke=smoke)
        return subprocess.CompletedProcess([], 0, stdout='1408 passed, 0 failed in 1.0 s\n', stderr=''), None

    name = next(n for n in mf.EQUIVALENT if n in mf.MUTANTS)
    with patched(mf, 'build_and_run', fake_build), \
         patched(mf, 'mutation', lambda src, n: ('mutated', None)):
        status, line, out = mf.try_mutant('src', name, jobs=2)
    assert status == 'equiv' and calls == {'fail_fast': True, 'jobs': 2, 'smoke': True}


def test_fork_broken_and_stale_are_misses_not_catches():
    def fake_build(*args, **kwargs):
        return None, 'does not parse'
    with patched(mf, 'build_and_run', fake_build), \
         patched(mf, 'mutation', lambda src, n: ('mutated', None)):
        status, line, _ = mf.try_mutant('src', 'scope exit frees nothing')
    assert status == 'missed' and 'BROKEN' in line
    status, line, _ = mf.try_mutant('src without the pattern', 'scope exit frees nothing')
    assert status == 'missed' and 'STALE' in line


def test_fork_does_not_duplicate_module_and_sym_suites():
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        if any(str(c).endswith('run_fork.py') for c in cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout='1408 passed, 0 failed in 1.0 s\n', stderr='')
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    with patched(mf.subprocess, 'run', fake_run):
        result, error = mf.build_and_run('src', fail_fast=True, jobs=2, smoke=True)
    assert error is None and result.returncode == 0
    suites = [c for c in commands if any(str(x).endswith(('module_test.py', 'sym_test.py')) for x in c)]
    assert suites == []
    runners = [c for c in commands if any(str(x).endswith('run_fork.py') for x in c)]
    assert len(runners) == 1
    assert '-x' in runners[0] and '-j' in runners[0] and '--smoke' in runners[0]
    source = open(os.path.join(HERE, 'run_fork.py')).read()
    assert 'module_test.tests()' in source and 'sym_test.tests()' in source


def test_baseline_catcher_failure_aborts_the_gate():
    calls = []

    def fake_compare(tmp, src=None, quick=False, workers=None, catchers=None,
                     stats=None, catchers_only=False):
        calls.append(bool(catchers_only))
        if catchers_only:
            if stats is not None:
                stats.set('catcher_cases', len(catchers))
                stats.set('unknown_catchers', [])
            return ['FAIL a catcher'], ['a catcher differs'], 1
        return [], [], 1

    with patched(pc, 'compare', fake_compare), \
         patched(sys, 'argv', ['mutate_port.py', 'error message changed']):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                mp.main()
        except SystemExit as exc:
            assert 'catcher path' in str(exc)
        else:
            raise AssertionError('the gate did not abort on a broken baseline catcher')
    assert calls == [False, True]


def test_baseline_exception_aborts_before_any_probe_or_mutant():
    def fake_compare(*args, **kwargs):
        raise ValueError('broken special helper')

    with patched(pc, 'compare', fake_compare), \
         patched(sys, 'argv', ['mutate_port.py', 'error message changed']):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                mp.main()
            except SystemExit as exc:
                assert 'harness error' in str(exc)
            else:
                raise AssertionError('a broken baseline helper did not abort the gate')


def test_baseline_probe_runs_all_selected_catchers_not_one_per_kind():
    table = {'error message changed': 'cli --version',
             'condition test compares with 1': 'modules/run/basic'}
    seen = []

    def fake_compare(tmp, src=None, quick=False, workers=None, catchers=None,
                     stats=None, catchers_only=False):
        if catchers_only:
            seen.append(list(catchers))
            if stats is not None:
                stats.set('catcher_cases', len(catchers))
                stats.set('unknown_catchers', [])
            # the second, non-representative check fails
            return ['FAIL modules/run/basic'], ['modules/run/basic differs'], 1
        return [], [], 1

    with patched(mp, 'TABLE', table), patched(pc, 'compare', fake_compare), \
         patched(sys, 'argv', ['mutate_port.py', '']):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                mp.main()
            except SystemExit as exc:
                assert 'catcher path' in str(exc)
            else:
                raise AssertionError('a non-representative baseline catcher failure did not abort')
    assert seen == [['cli --version', 'modules/run/basic']]


def test_port_main_counts_and_exit_codes():
    def fake_compare(tmp, src=None, quick=False, workers=None, catchers=None,
                     stats=None, catchers_only=False):
        if catchers_only:
            if stats is not None:
                stats.set('catcher_cases', len(catchers))
                stats.set('unknown_catchers', [])
            return [], [], 1
        return [], [], 1

    def fake_mutant(name, text, base_peak, workers=None, stats=None, use_catchers=True):
        if stats is not None:
            stats.set('verdict', 'caught')
        return 'caught', f"caught  {name}"

    with patched(pc, 'compare', fake_compare), patched(mp, 'try_mutant', fake_mutant), \
         patched(sys, 'argv', ['mutate_port.py', 'error message changed']):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                mp.main()
            except SystemExit as exc:
                assert exc.code == 0
        assert '1/1 mutants caught' in out.getvalue()

    def fake_miss(name, text, base_peak, workers=None, stats=None, use_catchers=True):
        return 'missed', f"MISSED  {name}"

    with patched(pc, 'compare', fake_compare), patched(mp, 'try_mutant', fake_miss), \
         patched(sys, 'argv', ['mutate_port.py', 'error message changed']):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                mp.main()
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError('a missed port mutant did not fail the gate')


def test_concurrent_mutants_use_isolated_scratch_dirs():
    seen = []
    lock = __import__('threading').Lock()

    def fake_compare(tmp, src=None, quick=False, workers=None, catchers=None,
                     stats=None, catchers_only=False):
        with lock:
            seen.append(tmp)
        return [], [], 1

    names = ['a', 'b', 'c', 'd']
    with patched(pc, 'compare', fake_compare):
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda n: mp.try_mutant(n, 'src', 1, use_catchers=False), names))
    assert [r[0] for r in results] == ['missed'] * 4
    assert len(seen) == 4 and len(set(seen)) == 4


def test_memory_oracle_missing_stats_is_a_harness_error():
    try:
        mio.parse_peak(0, 'no stats here')
    except mio.MemoryOracleError as exc:
        assert 'no live peak' in str(exc)
    else:
        raise AssertionError('missing live peak was accepted')


def test_memory_oracle_compile_failure_is_not_a_catch():
    try:
        mio.parse_peak(7, 'crash')
    except mio.MemoryOracleError as exc:
        assert 'self-compile failed' in str(exc)
    else:
        raise AssertionError('a failed self-compile was accepted')


def test_memory_baseline_over_budget_aborts():
    with patched(mio, 'build_runtime', lambda src, work: work), \
         patched(mio, 'runtime_peak', lambda rt, work: mio.PEAK_BUDGET + 1):
        try:
            mio.memory_baseline()
        except SystemExit as exc:
            assert 'baseline' in str(exc) and 'over' in str(exc)
        else:
            raise AssertionError('an over-budget baseline did not abort')


def test_empty_array_delta_extra_heap_is_caught():
    caught, delta = mio.empty_array_regression(2_000_000, 2_044_672)
    assert caught and delta == 44_672


def test_empty_array_delta_below_tolerance_not_caught():
    caught, delta = mio.empty_array_regression(2_000_000, 2_001_000)
    assert not caught and delta == 1_000


def test_memory_baseline_oracle_failure_aborts():
    def fail(rt, work):
        raise mio.MemoryOracleError('no live peak')

    with patched(mio, 'build_runtime', lambda src, work: work), \
         patched(mio, 'runtime_peak', fail):
        try:
            mio.memory_baseline()
        except SystemExit as exc:
            assert 'memory baseline harness failure' in str(exc)
        else:
            raise AssertionError('a broken baseline oracle did not abort')


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - report the test, keep going
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
