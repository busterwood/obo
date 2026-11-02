#!/usr/bin/env python3
"""Optional seeded Fraction property probes for the experimental dec24 core.

This is a host diagnostic, not the freestanding artifact.  It builds a small
shared host library from the same C sources and checks ordering, binding and
upper-exponent canonical/display round-trips against exact Fraction models.
The default run is a quick smoke probe; --full runs the review-scale 100,000
ordering and 50,000 binding counts.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import binding_oracle as bo  # noqa: E402
import arithmetic_oracle as ar  # noqa: E402


class Dec(ctypes.Structure):
    _fields_ = [
        ("lo", ctypes.c_uint64), ("hi", ctypes.c_uint64),
        ("exp", ctypes.c_uint16), ("dscale", ctypes.c_uint16),
        ("sign", ctypes.c_uint8), ("flags", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 2),
    ]


MAXC = 10 ** 38 - 1
SEED = 0x0D1D1000


def mk(v: tuple[int, int, int, int]) -> Dec:
    sign, coeff, exp, dscale = v
    d = Dec()
    d.lo = coeff & ((1 << 64) - 1)
    d.hi = coeff >> 64
    d.exp = exp & 0xFFFF
    d.dscale = dscale
    d.sign = sign
    return d


def parts(d: Dec) -> tuple[int, int, int, int]:
    exp = d.exp if d.exp < 32768 else d.exp - 65536
    return (d.sign, d.lo | (d.hi << 64), exp, d.dscale)


def value(v: tuple[int, int, int, int]) -> Fraction:
    sign, coeff, exp, _ = v
    x = Fraction(coeff)
    x = x * (10 ** exp) if exp >= 0 else x / (10 ** (-exp))
    return -x if sign else x


def rand_dec(rng: random.Random, exp_lo: int = -60, exp_hi: int = 60):
    c = rng.choice([0, 1, 9, 10, 15, 100, 10 ** 37, MAXC,
                    rng.randrange(0, MAXC + 1)])
    sign = rng.randrange(2) if c else 0
    exp = rng.randint(exp_lo, exp_hi) if c else 0
    return (sign, c, exp, rng.randrange(0, 65536))


def build_lib(evidence: str) -> tuple[str, dict]:
    out = os.path.join(evidence, "libdec24_probe.so")
    cmd = ["gcc", "-O2", "-fPIC", "-shared", "-std=c11", "-Wall", "-Wextra",
           "-Werror", "-I", ROOT,
           os.path.join(ROOT, "dec24.c"), os.path.join(ROOT, "dec24_text.c"),
           os.path.join(ROOT, "dec24_bind.c"),
           os.path.join(ROOT, "dec24_arith.c"), "-o", out]
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    return out, {"cmd": cmd, "returncode": p.returncode,
                 "stdout": p.stdout, "stderr": p.stderr,
                 "seconds": round(time.monotonic() - t0, 3)}


def bind_api(lib):
    lib.dec_cmp.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec)]
    lib.dec_cmp.restype = ctypes.c_int
    lib.dec_parse.argtypes = [ctypes.POINTER(Dec), ctypes.c_char_p,
                              ctypes.c_size_t]
    lib.dec_parse.restype = ctypes.c_int
    lib.dec_format_canonical_len.argtypes = [ctypes.POINTER(Dec),
                                             ctypes.POINTER(ctypes.c_size_t)]
    lib.dec_format_canonical_len.restype = ctypes.c_int
    lib.dec_format_canonical.argtypes = [ctypes.POINTER(Dec), ctypes.c_char_p,
                                         ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.c_size_t)]
    lib.dec_format_canonical.restype = ctypes.c_int
    lib.dec_format_display_len.argtypes = [ctypes.POINTER(Dec),
                                           ctypes.POINTER(ctypes.c_size_t)]
    lib.dec_format_display_len.restype = ctypes.c_int
    lib.dec_format_display.argtypes = [ctypes.POINTER(Dec), ctypes.c_char_p,
                                       ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
    lib.dec_format_display.restype = ctypes.c_int
    lib.dec_bind_exact_fits.argtypes = [ctypes.POINTER(Dec), ctypes.c_int64,
                                       ctypes.c_int64]
    lib.dec_bind_exact_fits.restype = ctypes.c_int
    lib.dec_bind_quantize.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                                      ctypes.c_int64, ctypes.c_int64,
                                      ctypes.c_uint]
    lib.dec_bind_quantize.restype = ctypes.c_int
    lib.dec_add.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                            ctypes.POINTER(Dec), ctypes.c_uint]
    lib.dec_add.restype = ctypes.c_int
    lib.dec_sub.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                            ctypes.POINTER(Dec), ctypes.c_uint]
    lib.dec_sub.restype = ctypes.c_int
    for name in ("dec_add_exact", "dec_sub_exact", "dec_mul_exact",
                 "dec_div_exact", "dec_rem_trunc"):
        fn = getattr(lib, name)
        fn.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                       ctypes.POINTER(Dec), ctypes.c_uint16]
        fn.restype = ctypes.c_int
    lib.dec_div_scale.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                                  ctypes.POINTER(Dec), ctypes.c_int32,
                                  ctypes.c_uint, ctypes.c_uint16]
    lib.dec_div_scale.restype = ctypes.c_int
    lib.dec_div_precision.argtypes = [ctypes.POINTER(Dec), ctypes.POINTER(Dec),
                                      ctypes.POINTER(Dec), ctypes.c_uint,
                                      ctypes.c_uint, ctypes.c_uint16]
    lib.dec_div_precision.restype = ctypes.c_int


def format_text(lib, v, display: bool):
    d = mk(v)
    n = ctypes.c_size_t(0)
    fn_len = (lib.dec_format_display_len if display
              else lib.dec_format_canonical_len)
    fn = lib.dec_format_display if display else lib.dec_format_canonical
    st = fn_len(ctypes.byref(d), ctypes.byref(n))
    if st != 0:
        return st, None
    buf = ctypes.create_string_buffer(n.value + 1)
    out = ctypes.c_size_t(0)
    st = fn(ctypes.byref(d), buf, n.value + 1, ctypes.byref(out))
    return st, (buf.value.decode("ascii") if st == 0 else None)


def probe_ordering(lib, n: int, rng: random.Random) -> dict:
    bad = []
    t0 = time.monotonic()
    for _ in range(n):
        a, b = rand_dec(rng), rand_dec(rng)
        x, y = value(a), value(b)
        want = (x > y) - (x < y)
        got = lib.dec_cmp(ctypes.byref(mk(a)), ctypes.byref(mk(b)))
        if got != want:
            bad.append((a, b, want, got))
            if len(bad) >= 10:
                break
    return {"cases": n, "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3),
            "sample": bad}


def probe_binding(lib, n: int, rng: random.Random) -> dict:
    bad = []
    ok = 0
    t0 = time.monotonic()
    for _ in range(n):
        v = rand_dec(rng, -30, 30)
        p = rng.choice([1, 2, 3, 5, 18, 28, 38, 39, 40,
                        rng.randint(1, 60)])
        s = rng.choice([-32768, -32767, -100, -10, -2, -1, 0, 1, 2, 5,
                        10, 32767, 32768, 32769, 65535,
                        rng.randint(-100, 100)])
        mode = rng.choice(list(range(8)) + [8, 99])
        x = value(v)
        want_e = bo.exact_status(x, p, s)
        got_e = lib.dec_bind_exact_fits(ctypes.byref(mk(v)), p, s)
        if got_e != want_e:
            bad.append(("exact", v, p, s, want_e, got_e))
            continue
        want = bo.rounded_result(v[0], v[1], v[2], p, s, mode)
        out = Dec()
        ctypes.memset(ctypes.byref(out), 0xAA, ctypes.sizeof(out))
        got = lib.dec_bind_quantize(ctypes.byref(out), ctypes.byref(mk(v)),
                                    p, s, mode)
        if got != want["status"]:
            bad.append(("status", v, p, s, mode, want["status"], got))
            continue
        if got == 0:
            ok += 1
            if parts(out) != (want["sign"], want["coeff"], want["exp"],
                              want["dscale"]):
                bad.append(("value", v, p, s, mode, want, parts(out)))
        elif bytes(out) != b"\xaa" * ctypes.sizeof(out):
            bad.append(("mutated", v, p, s, mode, got))
        if len(bad) >= 10:
            break
    return {"cases": n, "ok_results": ok, "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3), "sample": bad}


def probe_upper(lib, n: int, rng: random.Random) -> dict:
    bad = []
    t0 = time.monotonic()
    cases = []
    for sign in (0, 1):
        for exp in (32767, 32766):
            for k in range(1, 38):
                cases.append((sign, 10 ** k, exp, (k * 7) % 39))
    cases.append((0, MAXC, 32767, 0))
    cases.append((1, MAXC, 32767, 65535))
    for _ in range(n):
        c = rng.choice([1, 9, 10 ** rng.randrange(1, 38),
                        rng.randrange(1, MAXC + 1), MAXC])
        cases.append((rng.randrange(2), c, rng.randrange(32700, 32768),
                      rng.randrange(0, 39)))
    for v in cases:
        st, canon = format_text(lib, v, False)
        if st != 0:
            bad.append(("canon_format", v, st))
            continue
        rd = Dec()
        ctypes.memset(ctypes.byref(rd), 0xAA, ctypes.sizeof(rd))
        st = lib.dec_parse(ctypes.byref(rd), canon.encode(),
                           len(canon.encode()))
        if st != 0 or value(parts(rd)) != value(v):
            bad.append(("canon_parse", v, canon, st, parts(rd)))
            continue
        st, disp = format_text(lib, v, True)
        if st != 0:
            bad.append(("display_format", v, st))
            continue
        st = lib.dec_parse(ctypes.byref(rd), disp.encode(), len(disp.encode()))
        if (st != 0 or value(parts(rd)) != value(v) or
                parts(rd)[3] != v[3]):
            bad.append(("display_parse", v, st, parts(rd)))
        if len(bad) >= 10:
            break
    return {"cases": len(cases), "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3), "sample": bad}


def _ar(v):
    return ar.Dec(v[0], v[1], v[2], v[3])


def _cmp_result(out: Dec, want) -> bool:
    return (parts(out)[:3] == (want.sign, want.coeff, want.exp) and
            out.dscale == want.dscale)


def probe_division(lib, n: int, rng: random.Random) -> dict:
    bad = []
    ok = 0
    t0 = time.monotonic()
    for _ in range(n):
        a = rand_dec(rng, -40, 40)
        b = rand_dec(rng, -40, 40)
        if b[1] == 0:
            b = (0, 1, 0, b[3])
        mode = rng.randrange(8)
        ds = rng.randrange(0, 65536)
        if rng.randrange(2):
            scale = rng.choice([-5, -1, 0, 1, 2, 5, 10, 19, 38])
            st, want = ar.div_scale(_ar(a), _ar(b), scale, mode, ds)
            out = Dec()
            ctypes.memset(ctypes.byref(out), 0xAA, ctypes.sizeof(out))
            got = lib.dec_div_scale(ctypes.byref(out), ctypes.byref(mk(a)),
                                    ctypes.byref(mk(b)), scale, mode, ds)
        else:
            p = rng.choice([1, 2, 3, 5, 10, 19, 20, 37, 38])
            st, want = ar.div_precision(_ar(a), _ar(b), p, mode, ds)
            out = Dec()
            ctypes.memset(ctypes.byref(out), 0xAA, ctypes.sizeof(out))
            got = lib.dec_div_precision(ctypes.byref(out), ctypes.byref(mk(a)),
                                        ctypes.byref(mk(b)), p, mode, ds)
        if got != st:
            bad.append(("div_status", a, b, mode, ds, st, got))
        elif got == 0:
            ok += 1
            if not _cmp_result(out, want):
                bad.append(("div_value", a, b, mode, ds, want, parts(out)))
        elif bytes(out) != b"\xaa" * ctypes.sizeof(out):
            bad.append(("div_mutated", a, b, mode, got))
        if len(bad) >= 10:
            break
    return {"cases": n, "ok_results": ok, "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3), "sample": bad}


def probe_remainder(lib, n: int, rng: random.Random) -> dict:
    bad = []
    t0 = time.monotonic()
    for _ in range(n):
        a = rand_dec(rng, -60, 60)
        b = rand_dec(rng, -60, 60)
        if b[1] == 0:
            b = (0, 1, 0, b[3])
        ds = rng.randrange(0, 65536)
        st, want = ar.rem_trunc(_ar(a), _ar(b), ds)
        out = Dec()
        ctypes.memset(ctypes.byref(out), 0xAA, ctypes.sizeof(out))
        got = lib.dec_rem_trunc(ctypes.byref(out), ctypes.byref(mk(a)),
                                ctypes.byref(mk(b)), ds)
        if got != st:
            bad.append(("rem_status", a, b, ds, st, got))
        elif got == 0:
            if (value(parts(out)) != ar.value(want) or
                    out.dscale != want.dscale):
                bad.append(("rem_value", a, b, ds, want, parts(out)))
        elif bytes(out) != b"\xaa" * ctypes.sizeof(out):
            bad.append(("rem_mutated", a, b, got))
        if len(bad) >= 10:
            break
    return {"cases": n, "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3), "sample": bad}


def probe_gap(lib, n: int, rng: random.Random) -> dict:
    bad = []
    t0 = time.monotonic()
    for _ in range(n):
        a = rand_dec(rng, -200, 200)
        b = rand_dec(rng, -200, 200)
        if a[1] == 0:
            a = (0, 1, a[2], a[3])
        if b[1] == 0:
            b = (0, 1, b[2], b[3])
        if rng.randrange(2):
            b = (b[0], b[1], max(-32768, a[2] - rng.randrange(39, 300)), b[3])
        else:
            a = (a[0], a[1], max(-32768, b[2] - rng.randrange(39, 300)), a[3])
        mode = rng.randrange(8)
        ds = max(a[3], b[3])
        for fn, oracle in ((lib.dec_add, ar.add_rounded),
                           (lib.dec_sub, ar.sub_rounded)):
            st, want = oracle(_ar(a), _ar(b), mode, ds)
            out = Dec()
            ctypes.memset(ctypes.byref(out), 0xAA, ctypes.sizeof(out))
            got = fn(ctypes.byref(out), ctypes.byref(mk(a)),
                     ctypes.byref(mk(b)), mode)
            if got != st:
                bad.append(("gap_status", a, b, mode, st, got))
            elif got == 0:
                if (value(parts(out)) != ar.value(want) or
                        out.dscale != want.dscale):
                    bad.append(("gap_value", a, b, mode, ar.value(want),
                                value(parts(out))))
            elif bytes(out) != b"\xaa" * ctypes.sizeof(out):
                bad.append(("gap_mutated", a, b, mode, got))
            if len(bad) >= 10:
                break
        if len(bad) >= 10:
            break
    return {"cases": n, "failures": len(bad),
            "seconds": round(time.monotonic() - t0, 3), "sample": bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--full", action="store_true",
                    help="review-scale 100000 ordering / 50000 binding")
    ap.add_argument("--ordering", type=int, default=None)
    ap.add_argument("--binding", type=int, default=None)
    ap.add_argument("--upper", type=int, default=None)
    ap.add_argument("--div", type=int, default=None)
    ap.add_argument("--rem", type=int, default=None)
    ap.add_argument("--gap", type=int, default=None)
    args = ap.parse_args()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = os.path.abspath(
        args.evidence or os.path.join("/tmp/opencode",
                                      "decimal-D1-exponent-fix-" + stamp))
    os.makedirs(evidence, exist_ok=True)
    n_order = args.ordering if args.ordering is not None else (
        100000 if args.full else 20000)
    n_bind = args.binding if args.binding is not None else (
        50000 if args.full else 10000)
    n_upper = args.upper if args.upper is not None else (
        1000 if args.full else 300)
    n_div = args.div if args.div is not None else (
        20000 if args.full else 2000)
    n_rem = args.rem if args.rem is not None else (
        10000 if args.full else 1000)
    n_gap = args.gap if args.gap is not None else (
        20000 if args.full else 2000)

    libpath, build = build_lib(evidence)
    if build["returncode"] != 0:
        print(build["stderr"])
        return 1
    lib = ctypes.CDLL(libpath)
    bind_api(lib)
    rng = random.Random(SEED)
    results = {
        "format": "obo-decimal-D1-property-probes-v1",
        "generated_utc": stamp,
        "host_only": True,
        "seed": hex(SEED),
        "mode": "full" if args.full else "quick",
        "build": build,
        "lib_sha256": hashlib.sha256(open(libpath, "rb").read()).hexdigest(),
        "ordering": probe_ordering(lib, n_order, rng),
        "binding": probe_binding(lib, n_bind, rng),
        "upper_roundtrip": probe_upper(lib, n_upper, rng),
        "division": probe_division(lib, n_div, rng),
        "remainder": probe_remainder(lib, n_rem, rng),
        "wide_gap": probe_gap(lib, n_gap, rng),
    }
    ok = all(results[k]["failures"] == 0
             for k in ("ordering", "binding", "upper_roundtrip",
                       "division", "remainder", "wide_gap"))
    results["ok"] = ok
    def json_default(o):
        if isinstance(o, ar.Dec):
            return o.parts()
        if isinstance(o, Fraction):
            return str(o)
        if isinstance(o, tuple):
            return list(o)
        raise TypeError(type(o).__name__)

    with open(os.path.join(evidence, "property_probes.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True,
                  default=json_default)
        f.write("\n")
    print(json.dumps({k: results[k] for k in
                      ("mode", "ordering", "binding", "upper_roundtrip",
                       "division", "remainder", "wide_gap", "ok")},
                     indent=2, default=json_default))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
