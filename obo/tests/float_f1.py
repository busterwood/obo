#!/usr/bin/env python3
"""Targeted F1 scalar-float checks.

This is a checkpoint runner, not a release gate.  It uses the worktree's
compiler and runtime; it does not require a numeric OBO_VERSION or a new
symbol-file format.  Exact literal bits are checked through the checkpoint-only
floatBits diagnostic, against an independent integer/Fraction oracle.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.environ.get("OBO", os.path.join(FORK, "obo"))
RT = FORK


import float_conversion_vectors as fv
from float_conversion_vectors import bits_for_text
from fractions import Fraction


def bits(literal: str) -> int:
    """Expected binary64 bits for a finite literal with f/F suffix."""
    text = literal[:-1]
    b, over = bits_for_text(text)
    if over:
        raise AssertionError(f"expected finite literal: {literal}")
    if b == 0 and text.strip().startswith("-"):
        b = 1 << 63
    return b


def signed_bits(b: int) -> int:
    return b if b < (1 << 63) else b - (1 << 64)


def run(cmd, timeout=120, **kw):
    return subprocess.run(cmd, cwd=FORK, text=True, capture_output=True,
                          timeout=timeout, **kw)


def compile_src(src: str, d: str, name: str):
    p = os.path.join(d, name + ".obx")
    exe = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(src)
    r = run([OBO, p, "-o", exe, "--rt", RT, "--test-float-bits"])
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


def compile_error(src: str, name: str, needle: str, timeout: int = 120) -> None:
    d = tempfile.mkdtemp(prefix="float-f1-err-")
    p = os.path.join(d, name + ".obx")
    with open(p, "w") as f:
        f.write(src)
    r = run([OBO, p, "-o", os.path.join(d, name), "--rt", RT, "--test-float-bits"], timeout=timeout)
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
        b, over = bits_for_text(lit[:-1])
        if over:
            continue
        if b == 0 and sign == "-":
            b = 1 << 63
        lines.append(f"    if floatBits({lit}) != 0x{b:016x} {{ println({n}) }}")
        n += 1
    lines.append("    println(-1)")
    lines.append("}")
    run_ok("\n".join(lines) + "\n", "literal-random", ["-1"])
    return len(literals) + n



def test_fixed_conversion_cases() -> None:
    cases = fv.fixed_cases()
    lines = ["fn main() {"]
    for i, c in enumerate(cases):
        h = fv.sha256_text(c["text"])
        assert h == fv.FIXED_SHA[c["name"]], (c["name"], h)
        lines.append(f'    if floatBits({c["text"]}f) != 0x{c["bits"]:016x} {{ println({i}) }}')
        lines.append(f'    let r{i} = parseFloat("{c["text"]}")')
        lines.append(f'    match r{i} {{ ok(v) {{ if floatBits(v) != 0x{c["bits"]:016x} {{ println({i}) }} }} err(m,c) {{ println({i}) }} }}')
    long_cases = fv.random_cases(0x51A6, 2, long=True)
    base = len(cases)
    for k, c in enumerate(long_cases):
        i = base + k
        lines.append(f'    if floatBits({c["text"]}f) != 0x{c["bits"]:016x} {{ println({i}) }}')
        lines.append(f'    let r{i} = parseFloat("{c["text"]}")')
        lines.append(f'    match r{i} {{ ok(v) {{ if floatBits(v) != 0x{c["bits"]:016x} {{ println({i}) }} }} err(m,c) {{ println({i}) }} }}')
    lines += ["    println(-1)", "}"]
    run_ok("\n".join(lines) + "\n", "fixed-conversion", ["-1"])

    threshold = fv.exact_decimal(Fraction(2) ** 1024 - Fraction(2) ** 970)
    compile_error(f"fn main() {{ let x = {threshold}f }}\n",
                  "overflow-threshold", "float literal out of range")

    src = f'''fn main() {{
    let a = parseFloat("{threshold}")
    match a {{ ok(v) {{ println(0) }} err(m,c) {{ println(c) }} }}
    let b = parseFloat("1e18446744073709551616")
    match b {{ ok(v) {{ println(0) }} err(m,c) {{ println(c) }} }}
    let c = parseFloat("1e-18446744073709551616")
    match c {{ ok(v) {{ println(floatBits(v)) }} err(m,c) {{ println(c) }} }}
    let d = parseFloat("0e999999999999999999")
    match d {{ ok(v) {{ println(floatBits(v)) }} err(m,c) {{ println(c) }} }}
    let e = parseFloat("0.00e4002")
    match e {{ ok(v) {{ println(floatBits(v)) }} err(m,c) {{ println(c) }} }}
}}
'''
    run_ok(src, "parse-bounds", ["34", "34", "0", "0", "0"])


def test_exponent_bounds() -> None:
    for lit in ("1e18446744073709551616f", "1e18446744073709551618f",
                "1e999999999999999999999f"):
        compile_error(f"fn main() {{ let x = {lit} }}\n",
                      "huge-positive", "float literal out of range", timeout=5)
    src = r'''fn main() {
    if floatBits(1e-18446744073709551616f) != 0 { println(0) }
    if floatBits(1e-100000000000000000f) != 0 { println(1) }
    if floatBits(0e999999999999999999f) != 0 { println(2) }
    if floatBits(-0e999999999999999999f) != 0x8000000000000000 { println(3) }
    println(-1)
}
'''
    run_ok(src, "huge-negative-zero", ["-1"])


def test_int_conversions() -> None:
    vals = [0, 1, 3, 9007199254740992, 9007199254740993,
            9223372036854775807]
    lines = ["fn main() {"]
    for i, n in enumerate(vals):
        b, over = fv.bits_for_fraction(Fraction(n))
        assert not over
        lines.append(f"    if floatBits(float({n})) != 0x{b:016x} {{ println({i}) }}")
    lines.append("    println(-1)")
    lines.append("}")
    run_ok("\n".join(lines) + "\n", "int-conversions", ["-1"])


def test_separators() -> None:
    cases = [
        ("1_0f", "10"),
        ("1_000.5f", "1000.5"),
        ("1_.0f", "1.0"),
        ("1.0_f", "1.0"),
        ("1_0_f", "10"),
        ("1__0f", "10"),
        ("1_0.0_0f", "10.00"),
        ("1_0e2f", "1000"),
    ]
    # A >1200-digit fraction with separators; underscores must not be counted
    # as fraction/significant/discarded digits.
    rng = random.Random(0x5EED)
    digits = "".join(str(rng.randrange(10)) for _ in range(1300))
    plain = "1." + digits
    sep = "1." + "_".join(digits[i:i+3] for i in range(0, len(digits), 3))
    cases.append((sep + "f", plain))
    lines = ["fn main() {"]
    for i, (lit, plain) in enumerate(cases):
        b, over = fv.bits_for_text(plain)
        assert not over
        lines.append(f'    if floatBits({lit}) != 0x{b:016x} {{ println({i}) }}')
    lines.append("    println(-1)")
    lines.append("}")
    run_ok("\n".join(lines) + "\n", "separators", ["-1"])

    compile_error("fn main() { let x = 1e1_0f }\n", "exp-underscore",
                  "bad float exponent")
    compile_error("fn main() { let x = 1._0f }\n", "fraction-leading-underscore",
                  "fraction needs digits")

    src = r'''fn main() {
    let a = parseFloat("1_0")
    match a { ok(v) { println(0) } err(m,c) { println(c) } }
    let b = parseFloat("1.0_")
    match b { ok(v) { println(0) } err(m,c) { println(c) } }
}
'''
    run_ok(src, "parse-underscore", ["22", "22"])


def test_shortest_format() -> None:
    src = r'''fn main() {
    println(1.5f)
    println(0.1f)
    println(1f)
    println(-1f)
    println(1000000f)
    println(10000000f)
    println(0.000001f)
    println(0.0000001f)
    println(5.9604644775390625e-08f)
    println(1f / 16777216f)
    println(5e-324f)
    println(1.7976931348623157e308f)
    println(9007199254740993f)
    println(-0f)
    println(1f / 0f)
    println(-1f / 0f)
    println(0f / 0f)
}
'''
    expected = ["1.5", "0.1", "1", "-1", "1000000", "10000000",
                "0.000001", "1e-07", "5.960464477539063e-08",
                "5.960464477539063e-08", "5e-324",
                "1.7976931348623157e+308", "9007199254740992", "-0",
                "inf", "-inf", "nan"]
    run_ok(src, "shortest-style", expected)


def test_shortest_property() -> None:
    import struct as _struct
    rng = random.Random(0xF3A10)
    vals = [0x0000000000000000, 0x8000000000000000,
            0x0000000000000001, 0x000fffffffffffff,
            0x0010000000000000, 0x7fefffffffffffff]
    while len(vals) < 1000:
        b = rng.getrandbits(64)
        if ((b >> 52) & 0x7ff) != 0x7ff:
            vals.append(b)
    lines = ["fn main() {"]
    for i, b in enumerate(vals):
        f = _struct.unpack(">d", _struct.pack(">Q", b))[0]
        text = repr(f)
        lines.append(f'    let a{i} = parseFloat("{text}")')
        lines.append(f'    match a{i} {{ ok(v) {{ println(string(v)) }} err(m,c) {{ println("{i}") }} }}')
    lines.append("}")
    d = tempfile.mkdtemp(prefix="float-f1-short-")
    r, exe = compile_src("\n".join(lines) + "\n", d, "shortest")
    if r.returncode != 0:
        raise AssertionError(f"shortest: compile failed:\n{r.stderr}")
    q = run([exe], env=dict(os.environ, OBO_CHECK="1", OBX_GC="poison"))
    if q.returncode != 0:
        raise AssertionError(f"shortest: run failed:\n{q.stderr}")
    got = q.stdout.splitlines()
    if len(got) != len(vals):
        raise AssertionError(f"shortest: {len(got)} outputs for {len(vals)} values")
    for b, text in zip(vals, got):
        rb, over = fv.bits_for_text(text)
        if rb == 0 and text.startswith("-"):
            rb = 1 << 63
        if over or rb != b:
            raise AssertionError(f"shortest roundtrip failed for {b:016x}: {text!r}")
        # Python repr is an independent shortest-decimal reference for count.
        def sig(s):
            t = s.strip().lower()
            if "e" in t:
                t = t.split("e")[0]
            t = t.lstrip("+-").replace(".", "").lstrip("0")
            return len(t.rstrip("0")) if t else 1
        py = repr(_struct.unpack(">d", _struct.pack(">Q", b))[0])
        if sig(text) > sig(py):
            raise AssertionError(f"not shortest for {b:016x}: {text!r} vs {py!r}")


def test_constants() -> None:
    src = r'''const A = 1.5f
const B = -0f
const C = 5e-324f
fn main() {
    println(floatBits(A) == 0x3ff8000000000000)
    println(floatBits(B) == 0x8000000000000000)
    println(floatBits(C) == 0x1)
    println(A == 1.5f)
}
'''
    run_ok(src, "constants", ["true"] * 4)
    compile_error("const X = 1f + 2f\nfn main() { println(floatBits(X)) }\n",
                  "const-arithmetic", "constant value must be known")
    compile_error("fn main() { let x: ?float = nil }\n",
                  "optional-float", "only reference types can be optional")
    d = tempfile.mkdtemp(prefix="float-f1-nobits-")
    p = os.path.join(d, "nobits.obx")
    with open(p, "w") as f:
        f.write("fn main() { println(floatBits(1.5f)) }\n")
    r = run([OBO, p, "-o", os.path.join(d, "nobits"), "--rt", RT])
    if r.returncode == 0 or "internal test diagnostic" not in r.stderr:
        raise AssertionError(f"floatBits was public without the test flag:\n{r.stderr}")


def test_value_positions() -> None:
    src = r'''type Pair = ref record { a: float; b: int }
type Choice = closed { One { x: float }; Two { y: float; z: int } }
type Iface = interface {
    pure fn scale(x: float, k: int): float
    pure fn get(): float
}
type Impl = ref record { v: float }
fn (p: &Impl) scale(x: float, k: int): float { return x * float(k) }
fn (p: &Impl) get(): float { return p.v }
impl Iface for Impl
var G: float
var H: float = 2.5f
fn mix(a: int, b: float, c: int, d: float): float { return b + d }
fn main() {
    let p = new Pair{a: 1.5f, b: 2}
    println(p.a == 1.5f)
    p.a = p.a + 1f
    println(p.a == 2.5f)
    let c: Choice = new One{x: 2.5f}
    match c {
        One { println(c.x == 2.5f) }
        Two { println(false) }
    }
    let a = new []float(0)
    append(a, 1.5f)
    append(a, 2.5f)
    append(a, 3.5f)
    truncate(a, 1)
    println(len(a) == 1 && a[0] == 1.5f)
    println(G == 0f)
    println(H == 2.5f)
    let f = fn (a: int, b: float, c: int, d: float): float { return b + d }
    println(f(1, 2f, 3, 4f) == 6f)
    println(mix(1, 2f, 3, 4f) == 6f)
    let i: Iface = new Impl{v: 3f}
    println(i.scale(2f, 3) == 6f)
    println(i.get() == 3f)
    let r: Result[float] = ok(1.5f)
    match r {
        ok(v) { println(v == 1.5f) }
        err(m, c) { println(false) }
    }
}
'''
    run_ok(src, "value-positions", ["true"] * 11)


def test_module_constants() -> None:
    d = tempfile.mkdtemp(prefix="float-f1-constmod-")
    sym = os.path.join(d, "sym")
    os.makedirs(sym)
    lib = os.path.join(d, "lib.obx")
    with open(lib, "w") as f:
        f.write('''module lib
pub type Real = float
pub type Pair = ref record { a: float; b: int }
pub type Floats = []float
pub const Half = 0.5f
pub const NegZero = -0f
pub var Global: float = 1.5f
pub fn half(x: Real): Real { return x * Half }
''')
    with open(os.path.join(d, "main.obx"), "w") as f:
        f.write('''module main
import "lib.obx"
fn main() {
    println(lib::half(3f) == 1.5f)
    println(floatBits(lib::Half) == 0x3fe0000000000000)
    println(floatBits(lib::NegZero) == 0x8000000000000000)
    println(lib::Global == 1.5f)
    let x: lib::Real = 2f
    println(x == 2f)
    let p = new lib::Pair{a: 2.5f, b: 3}
    println(p.a == 2.5f)
    let a: lib::Floats = new []float(0)
    append(a, 1.5f)
    println(a[0] == 1.5f)
}
''')
    exe = os.path.join(d, "main")
    r = run([OBO, os.path.join(d, "main.obx"), "-o", exe, "--rt", RT,
             "-I", d, "--sym-dir", sym, "--test-float-bits"])
    if r.returncode != 0:
        raise AssertionError(f"constant module: compile failed:\n{r.stderr}")
    q = run([exe, os.path.join(d, "main-run")],
            env=dict(os.environ, OBO_CHECK="1", OBX_GC="poison"))
    if q.returncode != 0 or q.stdout.splitlines() != ["true"] * 7:
        raise AssertionError(f"constant module: run {q.returncode}: {q.stdout!r} {q.stderr!r}")
    text = open(os.path.join(sym, "lib.sym")).read()
    re = run([OBO, "--reemit-sym", os.path.join(sym, "lib.sym")])
    if re.returncode != 0 or re.stdout != text:
        raise AssertionError(f"constant module: reemit mismatch\n{re.stderr}")
    for line in ("type Real = float", "const Half: float = 0x3fe0000000000000",
                 "const NegZero: float = 0x8000000000000000",
                 "var Global: float", "fn half(p0: float): float"):
        if line not in text:
            raise AssertionError(f"constant module: missing {line!r}\n{text}")
    bad = os.path.join(d, "bad")
    os.makedirs(bad)
    shutil.copyfile(os.path.join(d, "main.obx"), os.path.join(bad, "main.obx"))
    badsym = text.replace("const Half: float = 0x3fe0000000000000",
                          "const Half: float = true")
    with open(os.path.join(bad, "lib.sym"), "w") as f:
        f.write(badsym)
    bad_run = run([OBO, os.path.join(bad, "main.obx"), "--check",
                   "-I", bad, "--sym-dir", bad, "--test-float-bits"])
    if bad_run.returncode == 0:
        raise AssertionError(f"malformed float constant was not rejected:\n{bad_run.stderr}")

    hidden = lib + ".hidden"
    os.rename(lib, hidden)
    try:
        c = run([OBO, os.path.join(d, "main.obx"), "--check",
                 "-I", d, "--sym-dir", sym, "--test-float-bits"])
        if c.returncode != 0:
            raise AssertionError(f"source-less check failed:\n{c.stderr}")
    finally:
        os.rename(hidden, lib)


def test_power2_shortest() -> None:
    vals = []
    for e in range(1, 2047):
        b = e << 52
        for x in (b, b + 1, b - 1):
            vals += [x, x | (1 << 63)]
    for m in range(1, 52):
        b = 1 << m
        for x in (b, b + 1, b - 1):
            if 0 <= x < (1 << 52):
                vals += [x, x | (1 << 63)]
    vals = list(dict.fromkeys(vals))
    rng = random.Random(0x3E79DA3)
    batch = 500
    for start in range(0, len(vals), batch):
        chunk = vals[start:start + batch]
        lines = ["fn main() {"]
        for j, b in enumerate(chunk):
            f = __import__("struct").unpack(">d", __import__("struct").pack(">Q", b))[0]
            lines.append(f'    let a{j} = parseFloat("{repr(f)}")')
            lines.append(f'    match a{j} {{ ok(v) {{ println(string(v)) }} err(m,c) {{ println("ERR") }} }}')
        lines.append("}")
        d = tempfile.mkdtemp(prefix="float-f1-pow2-")
        r, exe = compile_src("\n".join(lines) + "\n", d, f"p{start}")
        if r.returncode != 0:
            raise AssertionError(f"power2: compile failed: {r.stderr}")
        q = run([exe], env=dict(os.environ, OBO_CHECK="1", OBX_GC="poison"))
        out = q.stdout.splitlines()
        if q.returncode != 0 or len(out) != len(chunk):
            raise AssertionError(f"power2: run {q.returncode}: {q.stderr} {len(out)}/{len(chunk)}")
        for b, text in zip(chunk, out):
            rb, over = fv.bits_for_text(text)
            if rb == 0 and text.startswith("-"):
                rb = 1 << 63
            if over or rb != b:
                raise AssertionError(f"power2 roundtrip {b:016x}: {text!r}")
            f = __import__("struct").unpack(">d", __import__("struct").pack(">Q", b))[0]
            def sig(x):
                t = x.strip().lower().lstrip("+-")
                if t in ("nan", "inf", "infinity", "0"):
                    return 0
                m = t.split("e")[0] if "e" in t else t
                dg = m.replace(".", "").lstrip("0").rstrip("0")
                return len(dg) if dg else 0
            if sig(text) > sig(repr(f)):
                raise AssertionError(f"power2 not shortest {b:016x}: {text!r} vs {repr(f)!r}")


def _fnv1a64(data: bytes) -> int:
    h = 0xCBF29CE484222325
    for c in data:
        h ^= c
        h = (h * 0x100000001B3) & ((1 << 64) - 1)
    return h


def _rehash_sym(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("interface "):
            body = "".join(lines[i + 1:])
            lines[i] = f"interface 0x{_fnv1a64(body.encode()):016x}\n"
            return "".join(lines)
    raise AssertionError("no interface line")


def test_float_const_reader_matrix() -> None:
    d = tempfile.mkdtemp(prefix="float-f1-reader-")
    sym = os.path.join(d, "sym")
    os.makedirs(sym)
    with open(os.path.join(d, "lib.obx"), "w") as f:
        f.write("module lib\npub const Half = 0.5f\n")
    with open(os.path.join(d, "main.obx"), "w") as f:
        f.write("module main\nimport \"lib.obx\"\nfn main() { println(floatBits(lib::Half)) }\n")
    r = run([OBO, os.path.join(d, "main.obx"), "-o", os.path.join(d, "main"),
             "--rt", RT, "-I", d, "--sym-dir", sym, "--test-float-bits"])
    if r.returncode != 0:
        raise AssertionError(f"reader baseline compile: {r.stderr}")
    valid = open(os.path.join(sym, "lib.sym")).read()
    if "const Half: float = 0x3fe0000000000000" not in valid:
        raise AssertionError("reader baseline: canonical float const missing")
    bad_values = [
        "true", "1", "0x1", "0x00000000000000", "0x00000000000000000",
        "0x000000000000000G", "0X0000000000000000",
        "0x3FE0000000000000", "0x3fe0_0000000000000",
        "-0x3fe0000000000000",
    ]
    for i, bad in enumerate(bad_values):
        text = valid.replace("const Half: float = 0x3fe0000000000000",
                             "const Half: float = " + bad)
        text = _rehash_sym(text)
        bd = os.path.join(d, f"bad{i}")
        os.makedirs(bd)
        shutil.copyfile(os.path.join(d, "main.obx"), os.path.join(bd, "main.obx"))
        with open(os.path.join(bd, "lib.sym"), "w") as f:
            f.write(text)
        q = run([OBO, os.path.join(bd, "main.obx"), "--check",
                 "-I", bd, "--sym-dir", bd, "--test-float-bits"])
        if q.returncode == 0:
            raise AssertionError(f"reader accepted malformed float const {bad!r}")
        if "stale" in q.stderr.lower():
            raise AssertionError(f"reader rejected {bad!r} as stale, not structural: {q.stderr}")
    # Canonical high-bit/NaN raw bits remain accepted.
    for raw in ("0x7ff8000000000000", "0x8000000000000000", "0x0000000000000000"):
        text = valid.replace("const Half: float = 0x3fe0000000000000",
                             "const Half: float = " + raw)
        text = _rehash_sym(text)
        bd = os.path.join(d, "good_" + raw[2:6])
        os.makedirs(bd)
        shutil.copyfile(os.path.join(d, "main.obx"), os.path.join(bd, "main.obx"))
        with open(os.path.join(bd, "lib.sym"), "w") as f:
            f.write(text)
        q = run([OBO, os.path.join(bd, "main.obx"), "--check",
                 "-I", bd, "--sym-dir", bd, "--test-float-bits"])
        if q.returncode != 0:
            raise AssertionError(f"reader rejected canonical float bits {raw}: {q.stderr}")

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
             "-I", d, "--sym-dir", sym, "--test-float-bits"])
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
    expected = ["1.5", "0.1", "-0", "inf", "-inf", "nan", "1.5",
                "true", "34", "22", "true", "true", "true"]
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
        b, over = bits_for_text(text)
        if over:
            continue
        if b == 0 and text.startswith("-"):
            b = 1 << 63
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
    if "--reader-only" in sys.argv:
        test_module_constants(); total += 1
        test_float_const_reader_matrix(); total += 1
        print(f"float_f1: ok ({total} reader groups)")
        return 0
    test_literal_bits(); total += 1
    test_arithmetic(); total += 1
    test_fixed_conversion_cases(); total += 1
    test_exponent_bounds(); total += 1
    test_int_conversions(); total += 1
    test_separators(); total += 1
    test_constants(); total += 1
    test_value_positions(); total += 1
    test_module_constants(); total += 1
    test_float_const_reader_matrix(); total += 1
    test_power2_shortest(); total += 1
    test_scalars_and_lambdas(); total += 1
    test_containers(); total += 1
    test_module_signature(); total += 1
    test_print_and_parse(); total += 1
    test_shortest_format(); total += 1
    test_shortest_property(); total += 1
    test_parse_random_and_format_roundtrip(); total += 1
    test_errors(); total += 1
    print(f"float_f1: ok ({total} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
