"""Content-hash build cache shared by the obx test runners.

    build_obx(src, out, ...)  obx -> C (obxc --emit-c-json) -> binary, like `obxc src -o out`
    cc_build(csrc, cmd, out)  C text -> binary; cmd uses '@SRC@' and '@OUT@' placeholders

The key is the hash of the C text (rt.h is embedded in it), the exact C compiler command
and the compiler's version string. A hit hard-links (or copies) the cached binary to
`out`; a miss builds into a temporary file in the cache and renames it (atomic, so
parallel runners are safe). Cached binaries are read-only (0555): a hit's `out` is the
same file, so writing into `out` in place (`cp new out`, say) would change the cache
entry and every other checkout linked to it; read-only, that write fails instead.
`private=True` gives `out` its own copy, for a file people replace by hand. The cache is $OBX_CACHE or ~/.cache/obx-build and is trimmed
to $OBX_CACHE_MB (default 2000) megabytes, oldest use first. OBX_NOCACHE=1 disables it:
build_obx then runs obxc -o as before, and cc_build compiles every time.
Compile errors from obxc produce no C and are never cached.

The compiler is $OBXC, else compiler/out/obxc (built from the C seed by compiler/build.sh).
"""
import functools, hashlib, json, os, random, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.normpath(os.path.join(HERE, '..', 'compiler', 'out', 'obxc'))
# The compiler the runners use: $OBXC, default the self-hosted compiler's out/obxc.
OBXC = os.path.abspath(os.environ['OBXC']) if os.environ.get('OBXC') else DEFAULT


def obxc_argv(obxc=None):
    obxc = obxc or OBXC
    if not os.path.exists(obxc):
        sys.exit(f"{obxc} not found: build it with obx/compiler/build.sh" if obxc == DEFAULT
                 else f"compiler {obxc} not found")
    return [sys.executable, obxc] if obxc.endswith('.py') else [obxc]


def enabled():
    return os.environ.get('OBX_NOCACHE', '') in ('', '0')


def cache_dir():
    d = os.environ.get('OBX_CACHE') or os.path.expanduser('~/.cache/obx-build')
    os.makedirs(d, exist_ok=True)
    return d


@functools.lru_cache(maxsize=None)
def cc_version(cc):
    try:
        r = subprocess.run([cc, '--version'], capture_output=True, text=True)
        m = subprocess.run([cc, '-dumpmachine'], capture_output=True, text=True)
        return r.stdout.splitlines()[0] + ' ' + m.stdout.strip() if r.stdout else cc
    except OSError:
        return cc


def _place(src, out, private=False):
    try:
        os.remove(out)
    except OSError:
        pass
    if os.stat(src).st_mode & 0o222:
        os.chmod(src, 0o555)          # entries from before the cache was read-only
    if not private:
        try:
            os.link(src, out)
            return
        except OSError:
            pass
    shutil.copyfile(src, out)
    os.chmod(out, 0o755)


def _trim(d):
    cap = int(os.environ.get('OBX_CACHE_MB', '2000')) * 1024 * 1024
    files = []
    for n in os.listdir(d):
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= cap:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def cc_build(csrc, cmd, out, timeout=None, private=False):
    """Compile csrc with cmd (placeholders @SRC@, @OUT@) into out.
    Returns (returncode, stderr); the binary is at out when the code is 0."""
    def run(src, dst):
        argv = [dst if a == '@OUT@' else src if a == '@SRC@' else a for a in cmd]
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if not enabled():
        with tempfile.TemporaryDirectory(prefix='obxcc_') as t:
            c = os.path.join(t, 'prog.c')
            open(c, 'w').write(csrc)
            r = run(c, out)
            return r.returncode, r.stderr
    key = hashlib.sha256(json.dumps([csrc, cmd, cc_version(cmd[0])]).encode()).hexdigest()
    d = cache_dir()
    hit = os.path.join(d, key)
    if os.path.exists(hit):
        os.utime(hit)
        _place(hit, out, private)
        return 0, ''
    with tempfile.TemporaryDirectory(prefix='tmp_', dir=d) as t:
        c, exe = os.path.join(t, 'prog.c'), os.path.join(t, 'prog')
        open(c, 'w').write(csrc)
        r = run(c, exe)
        if r.returncode:
            return r.returncode, r.stderr
        os.chmod(exe, 0o555)
        os.rename(exe, hit)           # atomic; a racing runner stored the same bytes
    _place(hit, out, private)
    if random.random() < 0.05:
        _trim(d)
    return 0, ''


def build_obx(src, out, extra_args=(), cflags=(), obxc=None, cwd=None, timeout=None):
    """Like `obxc src -o out [extra_args]`: returns (returncode, stdout, stderr).
    cflags go before -o (they are part of the key)."""
    py = obxc_argv(obxc)
    if not enabled() and not cflags:
        r = subprocess.run(py + [src, '-o', out, *extra_args], capture_output=True, text=True,
                           errors='replace', timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    r = subprocess.run(py + [src, '--emit-c-json', *extra_args], capture_output=True, text=True,
                       errors='replace', timeout=timeout, cwd=cwd)
    if r.returncode:
        return r.returncode, r.stdout, r.stderr
    j = json.loads(r.stdout)
    cmd = j['cmd']
    i = cmd.index('-o')
    cmd = cmd[:i] + list(cflags) + cmd[i:]
    rc, err = cc_build(j['c'], cmd, out, timeout)
    if rc:
        return 3, '', f"obxc: internal error: the C compiler failed\n{err[:4000]}"
    return 0, '', ''
