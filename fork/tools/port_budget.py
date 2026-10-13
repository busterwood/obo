#!/usr/bin/env python3
"""Stage B memory budget: the port compiling itself must stay within a recorded
live peak (exact, from OBO_STATS), so a change that keeps memory alive fails a
test instead of going unnoticed."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tests', 'port_compare.py')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("import glob\nimport os\n", "import glob\nimport os\nimport re\n")
sub("SRC = os.path.join(FORK, 'port', 'obo_own_wip.obx')\n",
    "SRC = os.path.join(FORK, 'port', 'obo_own_wip.obx')\n"
    "# live peak, in bytes, of the port compiling itself: about 3% above the measured\n"
    "# figure. Lower it when memory improves; raise it only for a reason recorded in FORK.md.\n"
    "PEAK_BUDGET = 1_305_000\n")
sub("""    for f in failures[:12]:
        print("FAIL", f)
""", """    p = subprocess.run([PORT, SRC, '-o', os.path.join(tmp, 'self'), '--rt', FORK], capture_output=True, text=True,
                       env=dict(os.environ, OBO_CHECK='1', OBO_STATS='1'), timeout=120)
    m = re.search(r'obo: (\\d+) peak bytes, (\\d+) allocated', p.stderr)
    if p.returncode != 0 or not m:
        failures.insert(0, f"memory: no OBO_STATS report (exit {p.returncode}): {p.stderr.strip()[:200]}")
    else:
        peak, total = int(m.group(1)), int(m.group(2))
        print(f"memory: compiling itself, the port peaks at {peak:,} bytes live "
              f"(budget {PEAK_BUDGET:,}) and allocates {total:,} in total")
        if peak > PEAK_BUDGET:
            failures.insert(0, f"memory: live peak {peak:,} bytes is over the budget of {PEAK_BUDGET:,}")
    for f in failures[:12]:
        print("FAIL", f)
""")
open(P, 'w').write(s)
print("patched", P)
