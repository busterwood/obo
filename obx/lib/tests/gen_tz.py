#!/usr/bin/env python3
"""Regenerates lib/tests/run/tz_zones.obx: the offsets and abbreviations of tz.obx
checked against Python's zoneinfo (which reads the same TZif files).

    python3 obx/lib/tests/gen_tz.py

Each row is a zone (or a POSIX TZ string taken from a zone's TZif footer) and an
instant; the expected line is the offset in seconds east and the abbreviation.
Instants: each zone's transitions in 2025-2028 and 2099-2101 (the second before
and at each), fixed dates across history and the future, and seeded random
instants from 1880 to 2150. The DST flag is not compared (zoneinfo derives it
from the offsets, TZif stores it); the test checks offset and abbreviation.
"""
import datetime
import os
import random
import struct
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'tests', 'run', 'tz_zones.obx')
ZONEINFO = '/usr/share/zoneinfo'

ZONES = [
    'UTC', 'Europe/London', 'Europe/Dublin', 'Europe/Moscow', 'Europe/Lisbon',
    'America/New_York', 'America/Chicago', 'America/Sao_Paulo', 'America/Santiago',
    'America/St_Johns', 'America/Caracas', 'Australia/Sydney', 'Australia/Adelaide',
    'Pacific/Auckland', 'Pacific/Chatham', 'Pacific/Apia', 'Asia/Kolkata',
    'Asia/Kathmandu', 'Asia/Tokyo', 'Asia/Tehran', 'Africa/Casablanca',
    'Antarctica/Troll', 'Etc/GMT+5', 'EST5EDT', 'Asia/Shanghai',
]

# Zones whose footer rule is also checked directly as a POSIX TZ string (tz.fromSpec).
FOOTER_ZONES = [
    'Europe/London', 'America/New_York', 'Australia/Sydney', 'Pacific/Auckland',
    'Asia/Kolkata', 'America/Santiago', 'America/St_Johns', 'Africa/Casablanca',
]

FIXED = [
    0, -1, 1, 86399, 86400, 951782400, 1000000000, 1234567890, 1500000000,
    1700000000, 2147483647, 2147483648, 2147483647 + 86400 * 400,
    -2147483648, -2147483649, -2147483648 - 86400 * 400,
    -1500000000, -2500000000, -4102444800, 4102444800, 4102444800 + 3 * 86400 * 365,
    7258118400, 10413792000, -5364662400,
]


def info(zone, sec):
    """(offset seconds east, abbreviation) of zone at sec, as zoneinfo sees it."""
    dt = datetime.datetime.fromtimestamp(sec, datetime.timezone.utc).astimezone(ZoneInfo(zone))
    off = int(dt.utcoffset().total_seconds())
    return off, dt.tzname()


def footer(zone):
    """The POSIX TZ string at the end of the zone's TZif file."""
    with open(os.path.join(ZONEINFO, zone), 'rb') as f:
        data = f.read()
    last = data.rstrip(b'\n').rsplit(b'\n', 1)[-1]
    return last.decode('ascii')


def last_transition(zone):
    """Unix seconds of the zone's last explicit transition (the 64-bit block of its
    TZif file), or -2**63 when the file has none."""
    with open(os.path.join(ZONEINFO, zone), 'rb') as f:
        data = f.read()

    def counts(p):
        return struct.unpack('>6l', data[p + 20:p + 44])

    isut, isstd, leap, timecnt, typecnt, charcnt = counts(0)
    p = 0
    if data[4:5] >= b'2':
        # skip the 32-bit block: transitions, indices, types, abbreviations, leaps, flags
        p = 44 + timecnt * 5 + typecnt * 6 + charcnt + leap * 8 + isstd + isut
        isut, isstd, leap, timecnt, typecnt, charcnt = counts(p)
        p += 44
        size = 8
    else:
        size = 4
    if timecnt == 0:
        return -2 ** 63
    fmt = '>%dq' % timecnt if size == 8 else '>%dl' % timecnt
    return struct.unpack(fmt, data[p:p + size * timecnt])[-1]


def transitions(zone, years):
    """Instants (Unix seconds) where the offset or abbreviation changes in years."""
    out = []
    for year in years:
        t = int(datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
        end = int(datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
        prev = None
        while t < end:
            cur = info(zone, t)
            if prev is not None and cur != prev:
                lo, hi = t - 3600, t          # the change is in (lo, hi]
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    if info(zone, mid) == prev:
                        lo = mid
                    else:
                        hi = mid
                out.append(hi)
            prev = cur
            t += 3600
    return out


def rows():
    """(zone, spec, sec): spec is '' for a zone name, else a POSIX TZ string."""
    rng = random.Random(20261106)
    out = []
    lo = int(datetime.datetime(1880, 1, 1).timestamp())
    hi = int(datetime.datetime(2150, 1, 1).timestamp())
    for zone in ZONES:
        times = set(FIXED)
        for t in transitions(zone, list(range(2025, 2029)) + [2099, 2100, 2101]):
            times.update([t - 1, t])
        for _ in range(40):
            times.add(rng.randrange(lo, hi))
        for sec in sorted(times):
            out.append((zone, '', sec))
    for zone in FOOTER_ZONES:
        spec = footer(zone)
        # the footer rule applies only after the last explicit transition
        start = max(last_transition(zone) + 1, 1700000000)
        for sec in range(start, 4000000000, 86400 * 97 + 3599):
            out.append((zone, spec, sec))
        for year in (2030, 2060, 2100, 2300):
            t = int(datetime.datetime(year, 6, 1, tzinfo=datetime.timezone.utc).timestamp())
            if t > start:
                out.append((zone, spec, t))
                out.append((zone, spec, t + 200 * 86400))
    return out


PROGRAM = '''
// Runs tz.obx on every row of ROWS (zone, spec, seconds) and prints the zone or
// spec, the instant, the offset in seconds east and the abbreviation.

fn zoneOf(name: &str): tz.Zone {
    match tz.load(name.toStr()) {
        Ok(z) { return z }
        Err(e) {
            println("load " + name.toStr() + ": " + e)
            exit(1)
        }
    }
}

fn run(zone: &str, spec: &str, secText: &str) {
    let sec = strings.parseInt(secText.toStr()).unwrap()
    let z = if spec == "" { zoneOf(zone) } else { tz.fromSpec(spec.toStr()) }
    let at = z.at(sec)
    let name = if spec == "" { zone.toStr() } else { spec.toStr() }
    println(name + " " + sec.toStr() + " " + at.offset.toStr() + " " + at.abbr)
}

fn main() {
    let text: str = ROWS
    for row in strings.lines(&text) {
        match strings.cut(row, "\\t") {
            Some(a) {
                match strings.cut(a.after, "\\t") {
                    Some(b) { run(a.before, b.before, b.after) }
                    None { println("bad row") }
                }
            }
            None { println("bad row") }
        }
    }
}
'''


def main():
    data = rows()
    lines = []
    for zone, spec, sec in data:
        off, abbr = info(zone, sec)
        name = spec if spec else zone
        lines.append((name, zone, spec, sec, off, abbr))
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('// tz: the offsets and abbreviations of zones read from TZif files, and of their\n')
        f.write('// POSIX footers, checked against Python zoneinfo (generated by lib/tests/gen_tz.py).\n')
        f.write('// Each expect line: the zone name or POSIX spec, the seconds, the offset, the abbreviation.\n')
        for name, zone, spec, sec, off, abbr in lines:
            f.write('// expect: %s %d %d %s\n' % (name, sec, off, abbr))
        f.write('\nimport "tz"\nimport "strings"\n\n')
        body ='\\n'.join('%s\\t%s\\t%d' % (zone, spec, sec) for zone, spec, sec in data)
        f.write('// One row per line: zone, a tab, the POSIX spec ("" for a zone), a tab, seconds.\n')
        f.write('const ROWS: str = "%s"\n' % body)
        f.write(PROGRAM)
    print('wrote %d rows to %s' % (len(lines), OUT))


if __name__ == '__main__':
    main()
