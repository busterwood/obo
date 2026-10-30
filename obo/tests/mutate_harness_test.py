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
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mutate_fork as mf  # noqa: E402
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
    unknown, reps = mp.validate_catchers({'m': 'no such check'}, ['m'])
    assert unknown == [('m', 'no such check')] and reps == {}


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
