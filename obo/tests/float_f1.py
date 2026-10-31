#!/usr/bin/env python3
"""Targeted F1 scalar-float checks.

This is a checkpoint runner, not a release gate.  It uses the worktree's
compiler and runtime; it does not require a numeric OBO_VERSION or a new
symbol-file format.  Exact literal bits are checked through the checkpoint-only
floatBits diagnostic, against Python struct/float as an independent oracle.
"""
from __future__ import annotations

import os
import random
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.environ.get("OBO", os.path.join(FORK, "obo"))
RT = FORK


def bits(literal: str) -> int:
    """Expected binary64 bits for a finite literal with f/F suffix."""
    text = literal[:-1]
    return struct.unpack(">Q", struct.pack(">d", float(text)))[0]


def signed_bits(b: int) -> int:
    return b if b < (1 << 63) else b - (1 << 64)


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=FORK, text=True, capture_output=True,
                          timeout=120, **kw)


def compile_src(src: str, d: str, name: str):
    p = os.path.join(d, name + ".obx")
    exe = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(src)
    r = run([OBO, p, "-o", exe, "--rt", RT])
    return r, exe


def run_ok(src: str, name: str, expected: list[str]) -> None:
    d = tempfile.mkdtemp(prefix="float-f1-")
    r, exe = compile_src(src, d, name)
    if r.returncode != 0:
        raise AssertionError(f"{name}: compile failed:\n{r.stderr}")
    q = run([exe, os.path.join(d, name + "-run")],
            env=dict(os.environ, OBO_CHECK="1", OBX_GC="poison"))
    if q.returncode != 0:
        raise AssertionError(f"{name}: run failed ({q.returncode}):\n{q.stderr}")
    got = q.stdout.splitlines()
    if got != expected:
        raise AssertionError(f"{name}: output {got!r} != {expected!r}")


def compile_error(src: str, name: str, needle: str) -> None:
    d = tempfile.mkdtemp(prefix="float-f1-err-")
    r, _ = compile_src(src, d, name)
    if r.returncode == 0:
        raise AssertionError(f"{name}: expected a compile error")
    if needle not in r.stderr:
        raise AssertionError(f"{name}: error did not contain {needle!r}:\n{r.stderr}")


def test_literal_bits() -> int:
    literals = [
        "0f", "-0f", "1f", "-1f", "0.5f", "1.5f",
        "1.5e3f", "1.5E+3F", "1.5e-3f",
        "9007199254740991f", "9007199254740992f",
        "9007199254740993f", "9007199254740995f",
        "123456789012345678901234567890f",
        "1.00000000000000011102230246251565404236316680908203125f",
        "2.2250738585072014e-308f", "2.2250738585072011e-308f",
        "1.7976931348623157e308f",
        "5e-324f", "4.9406564584124654e-324f",
        "2.4703282292062327e-324f", "2.4703282292062328e-324f",
        "1e-323f", "1e-324f", "1e-400f",
    ]
    lines = ["fn main() {"]
    for i, lit in enumerate(literals):
        lines.append(f"    if floatBits({lit}) != 0x{bits(lit):016x} {{ println({i}) }}")
    lines.append("    println(-1)")
    lines.append("}")
    run_ok("\n".join(lines) + "\n", "literal-bits", ["-1"])

    # A deterministic random sample exercises the compiler's actual parser.
    rng = random.Random(0xF10A7)
    lines = ["fn main() {"]
    n = 0
    for _ in range(300):
        sign = "-" if rng.randrange(2) else ""
        nd = rng.randint(1, 28)
        nf = rng.randint(0, 12)
        ds = "".join(str(rng.randrange(10)) for _ in range(nd + nf))
        text = ds[:-nf] + "." + ds[-nf:] if nf else ds
        lit = f"{sign}{text}e{rng.randint(-320, 300)}f"
        f = float(lit[:-1])
        if f in (float("inf"), float("-inf")):
            continue
        lines.append(f"    if floatBits({lit}) != 0x{bits(lit):016x} {{ println({n}) }}")
        n += 1
    lines.append("    println(-1)")
    lines.append("}")
    run_ok("\n".join(lines) + "\n", "literal-random", ["-1"])
    return len(literals) + n


def test_arithmetic() -> None:
    src = r'''fn main() {
    let pz = 0f
    let nz = -0f
    let inf = 1f / pz
    let ninf = -1f / pz
    let nan = pz / pz
    println(inf == inf)
    println(ninf < 0f)
    println(isInf(inf))
    println(isInf(ninf))
    println(isNaN(nan))
    println(nan == nan)
    println(nan != nan)
    println(nan < 1f)
    println(nan <= 1f)
    println(nan > 1f)
    println(nan >= 1f)
    println(isNaN(inf - inf))
    println(isNaN(pz * inf))
    println(isInf(inf + inf))
    println(signbit(nz))
    println(pz == nz)
    let s = 5e-324f
    println(s > 0f)
    println(s + s == 1e-323f)
    println(1f + 2f == 3f)
    println(5f - 2f == 3f)
    println(2f * 3f == 6f)
    println(6f / 2f == 3f)
    println(-1.5f == -1.5f)
}
'''
    expected = ["true", "true", "true", "true", "true", "false", "true",
                "false", "false", "false", "false", "true", "true", "true",
                "true", "true", "true", "true", "true", "true", "true",
                "true", "true"]
    run_ok(src, "arithmetic", expected)


def test_scalars_and_lambdas() -> None:
    src = r'''fn add(a: float, b: float): float { return a + b }
fn inc(x: float): float { return x + 1f }
fn apply(f: fn(float): float, x: float): float { return f(x) }
fn main() {
    var x: float = 1.5f
    x = add(x, 2.25f)
    println(x == 3.75f)
    let f = fn (x: float): float { return x * 2f }
    println(f(2.5f) == 5f)
    println(apply(f, 3f) == 6f)
    println(inc(2f) == 3f)
    let g: fn(float): float = f
    println(g(4f) == 8f)
    println(float(9007199254740993) == 9007199254740992f)
    println(float(3) == 3f)
}
'''
    expected = ["true"] * 7
    run_ok(src, "scalars-lambdas", expected)



def test_containers() -> None:
    src = r'''type Box = ref record { x: float }
var G: float = 2f
fn one(): Result[float] { return ok(1.5f) }
fn main() {
    let b = new Box{x: 1.25f}
    println(b.x == 1.25f)
    b.x = b.x + 1f
    println(b.x == 2.25f)
    println(G == 2f)
    G = G * 2f
    println(G == 4f)
    let a = new []float(0)
    append(a, 1.5f)
    append(a, 2.5f)
    println(a[0] == 1.5f)
    println(a[1] == 2.5f)
    let r = one()
    match r {
        ok(v) { println(v == 1.5f) }
        err(m, c) { println(false) }
    }
}
'''
    run_ok(src, "containers", ["true"] * 7)


def test_module_signature() -> None:
    d = tempfile.mkdtemp(prefix="float-f1-mod-")
    sym = os.path.join(d, "sym")
    os.makedirs(sym)
    with open(os.path.join(d, "lib.obx"), "w") as f:
        f.write('module lib\npub fn half(x: float): float { return x / 2f }\n')
    with open(os.path.join(d, "main.obx"), "w") as f:
        f.write('module main\nimport "lib.obx"\nfn main() { println(lib::half(3f) == 1.5f) }\n')
    exe = os.path.join(d, "main")
    r = run([OBO, os.path.join(d, "main.obx"), "-o", exe, "--rt", RT,
             "-I", d, "--sym-dir", sym])
    if r.returncode != 0:
        raise AssertionError(f"module signature: compile failed:\n{r.stderr}")
    q = run([exe, os.path.join(d, "main-run")],
            env=dict(os.environ, OBO_CHECK="1", OBX_GC="poison"))
    if q.returncode != 0 or q.stdout.splitlines() != ["true"]:
        raise AssertionError(f"module signature: run {q.returncode}: {q.stdout!r} {q.stderr!r}")


def test_print_and_parse() -> None:
    src = r'''fn main() {
    println(1.5f)
    println(0.1f)
    println(-0f)
    println(1f / 0f)
    println(-1f / 0f)
    println(0f / 0f)
    println(string(1.5f))
    let a = parseFloat("1.5")
    match a {
        ok(v) { println(v == 1.5f) }
        err(m, c) { println(false) }
    }
    let b = parseFloat("1e309")
    match b { ok(v) { println(0) } err(m, c) { println(c) } }
    let c = parseFloat("nope")
    match c { ok(v) { println(0) } err(m, c) { println(c) } }
    let d = parseFloat("nan")
    match d { ok(v) { println(isNaN(v)) } err(m, c) { println(false) } }
    let e = parseFloat("-inf")
    match e { ok(v) { println(isInf(v) && signbit(v)) } err(m, c) { println(false) } }
    let f = parseFloat("-0")
    match f { ok(v) { println(signbit(v)) } err(m, c) { println(false) } }
}
'''
    expected = ["1.5e+00", "1.0000000000000001e-01", "-0", "inf", "-inf",
                "nan", "1.5e+00", "true", "34", "22", "true", "true", "true"]
    run_ok(src, "print-parse", expected)


def test_parse_random_and_format_roundtrip() -> None:
    rng = random.Random(0xF2A45E)
    lines = ["fn main() {"]
    n = 0
    for _ in range(250):
        sign = "-" if rng.randrange(2) else ""
        nd = rng.randint(1, 22)
        nf = rng.randint(0, 10)
        ds = "".join(str(rng.randrange(10)) for _ in range(nd + nf))
        text = ds[:-nf] + "." + ds[-nf:] if nf else ds
        es = "e" + str(rng.randint(-320, 300)) if rng.randrange(2) else ""
        text = f"{sign}{text}{es}"
        f = float(text)
        if f in (float("inf"), float("-inf")):
            continue
        b = struct.unpack(">Q", struct.pack(">d", f))[0]
        lines.append(f'    let a{n} = parseFloat("{text}")')
        lines.append(f'    match a{n} {{ ok(v) {{ if floatBits(v) != 0x{b:016x} {{ println({n}) }}; let t = string(v); let r = parseFloat(t); match r {{ ok(w) {{ if floatBits(w) != floatBits(v) {{ println({n}) }} }} err(m,c) {{ println({n}) }} }} }} err(m,c) {{ println({n}) }} }}')
        n += 1
    lines += ["    println(-1)", "}"]
    run_ok("\n".join(lines) + "\n", "parse-random", ["-1"])

def test_errors() -> None:
    compile_error("fn main() { let x = 1.5 }\n", "bare-fraction",
                  "f suffix for float")
    compile_error("fn main() { let x = 1e3 }\n", "bare-exponent",
                  "f suffix for float")
    compile_error("fn main() { let x = 1.5m }\n", "m-suffix",
                  "f suffix for float")
    compile_error("fn main() { let x = 1.f }\n", "dot-no-digits",
                  "fraction needs digits")
    compile_error("fn main() { let x = 1e }\n", "exp-no-digits",
                  "exponent needs digits")
    compile_error("fn main() { let x = 1e309f }\n", "overflow-pos",
                  "float literal out of range")
    compile_error("fn main() { let x = -1e309f }\n", "overflow-neg",
                  "float literal out of range")
    compile_error("fn main() { let x: float = 1 }\n", "implicit-int",
                  "cannot use int as float")
    compile_error("fn main() { let x: int = 1f }\n", "implicit-float",
                  "cannot use float as int")
    compile_error("fn main() { let x = 1f + 1 }\n", "mixed-left",
                  "needs two floats")
    compile_error("fn main() { let x = 1 + 1f }\n", "mixed-right",
                  "needs ints")
    # The old range spelling remains an int range.
    run_ok("fn main() { var s = 0\n for i in 1..3 { s += i }\n println(s) }\n",
           "range", ["3"])


def main() -> int:
    if not os.path.exists(OBO):
        print(f"missing compiler: {OBO}", file=sys.stderr)
        return 2
    total = 0
    test_literal_bits(); total += 1
    test_arithmetic(); total += 1
    test_scalars_and_lambdas(); total += 1
    test_containers(); total += 1
    test_module_signature(); total += 1
    test_print_and_parse(); total += 1
    test_parse_random_and_format_roundtrip(); total += 1
    test_errors(); total += 1
    print(f"float_f1: ok ({total} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
