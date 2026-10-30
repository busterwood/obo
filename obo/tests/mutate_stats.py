#!/usr/bin/env python3
"""Small, opt-in measurement helper for the mutation harnesses.

Nothing here decides a verdict. The harnesses call it only to record where time
went, so that a slow run can be read instead of guessed at. Set MUTATE_TIMINGS to
a path (or to 1 for a file under the system temporary directory) to write one
JSON object per line: a header with the inputs and host, then one record per
mutant. With MUTATE_TIMINGS unset the harness writes nothing.

Wall seconds are measured once per stage. Summed subprocess seconds are not wall
time: several cases run at once, so their sum can exceed the stage's wall.

Subprocess counters are DIRECT subprocess.run launches made by the harness, not
their descendants. The two gates count different things: mutate_fork counts the
seed build and the run_fork suite, while mutate_port counts each case compile/run
inside its comparison. Do not compare those counts as if they measured the same
number, and do not read a case count (for example run_fork's 1408) as a launch
count. The header records the input identity; it is a record, not a lock or a
promise that the inputs did not change, and no result is reused across runs.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time


class Stats:
    """Thread-safe counters and values for one stage or one mutant."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def add(self, key, n=1):
        with self._lock:
            self._data[key] = self._data.get(key, 0) + n

    def set(self, key, value):
        with self._lock:
            self._data[key] = value

    def update(self, values):
        with self._lock:
            self._data.update(values)

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self):
        with self._lock:
            return dict(self._data)


def timings_path(prefix):
    """The file to write, or None when timings are off. MUTATE_TIMINGS=1 picks a
    fresh file outside the tree, so a normal run leaves no artifact behind."""
    raw = os.environ.get('MUTATE_TIMINGS', '')
    if raw in ('', '0'):
        return None
    if raw.lower() in ('1', 'true', 'yes'):
        return os.path.join(tempfile.gettempdir(),
                            f'{prefix}-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}.jsonl')
    return raw


class Sink:
    """Append JSON records to one file, outside the repository by default."""

    def __init__(self, prefix):
        self.path = timings_path(prefix)
        self._lock = threading.Lock()
        if self.path is not None:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)

    def write(self, record):
        if self.path is None:
            return
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            with open(self.path, 'a') as f:
                f.write(line + '\n')
                f.flush()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def dir_digest(path):
    """A stable digest of a directory's file names and bytes, for provenance.
    __pycache__ and editor debris are ignored. None when the directory is absent."""
    h = hashlib.sha256()
    if not os.path.isdir(path):
        return None
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            if name.endswith(('.pyc', '.swp', '~')):
                continue
            full = os.path.join(root, name)
            h.update(os.path.relpath(full, path).encode() + b'\0')
            try:
                with open(full, 'rb') as f:
                    for block in iter(lambda: f.read(1 << 20), b''):
                        h.update(block)
            except OSError:
                h.update(b'<unreadable>')
            h.update(b'\0')
    return h.hexdigest()


def git_head(directory):
    try:
        p = subprocess.run(['git', '-C', directory, 'rev-parse', 'HEAD'],
                           capture_output=True, text=True, timeout=10)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_now():
    try:
        one, five, fifteen = os.getloadavg()
        return [round(one, 2), round(five, 2), round(fifteen, 2)]
    except (OSError, AttributeError):
        return None


def header(kind, inputs, extra=None):
    """The run's provenance. The caller states the input files it judged; the
    timing is meaningful only while those inputs do not change (see FORK.md)."""
    record = {
        'kind': 'header',
        'harness': kind,
        'when': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'pid': os.getpid(),
        'cpus': os.cpu_count(),
        'loadavg': load_now(),
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'inputs': inputs,
    }
    if extra:
        record.update(extra)
    return record
