#!/usr/bin/env python3
"""Generate deterministic D2 arithmetic vectors from arithmetic_oracle.py."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys

import arithmetic_oracle as ar

HERE = os.path.dirname(os.path.abspath(__file__))


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def c_u64(v: int) -> str:
    return f"0x{v:016x}ULL"


def parts(a):
    return a.sign, a.coeff & ((1 << 64) - 1), a.coeff >> 64, a.exp, a.dscale


def enc_dec(a):
    return {"sign": a.sign, "coeff": str(a.coeff), "exp": a.exp,
            "dscale": a.dscale}


def dec(sign, coeff, exp, dscale):
    assert 0 <= coeff <= ar.MAX_COEFF
    assert -32768 <= exp <= 32767
    if coeff == 0:
        sign = 0
        exp = 0
    return ar.Dec(sign, coeff, exp, dscale)


def gen_operand(rng, exp_lo=-60, exp_hi=60):
    choice = rng.randrange(8)
    if choice == 0:
        coeff = 1
    elif choice == 1:
        coeff = 10 ** rng.randrange(1, 38)
    elif choice == 2:
        coeff = ar.MAX_COEFF
    elif choice == 3:
        coeff = 10 ** 37
    else:
        coeff = rng.randrange(1, ar.MAX_COEFF + 1)
    sign = rng.randrange(2)
    exp = rng.randrange(exp_lo, exp_hi + 1)
    return dec(sign, coeff, exp, rng.randrange(0, 40))


def build_vectors():
    rng = random.Random(0xD2A11CE)
    add_v, sub_v, mul_v = [], [], []
    exact_v, div_v, rem_v = [], [], []

    def add_op(kind, a, b, mode, status, r):
        row = {"a": a, "b": b, "mode": mode, "status": status}
        if r is not None:
            row["r"] = r
        (add_v if kind == "add" else sub_v if kind == "sub"
         else mul_v).append(row)

    def add_exact(op, a, b, dscale, status, r):
        row = {"op": op, "a": a, "b": b, "dscale": dscale,
               "status": status}
        if r is not None:
            row["r"] = r
        exact_v.append(row)

    def add_div(kind, a, b, scale, precision, mode, dscale, status, r):
        row = {"kind": kind, "a": a, "b": b, "scale": scale,
               "precision": precision, "mode": mode, "dscale": dscale,
               "status": status}
        if r is not None:
            row["r"] = r
        div_v.append(row)

    def add_rem(a, b, dscale, status, r):
        row = {"a": a, "b": b, "dscale": dscale, "status": status}
        if r is not None:
            row["r"] = r
        rem_v.append(row)

    # Fixed wide-gap, power-of-ten, sign and range cases.
    fixed = [
        (dec(0, 12, 76, 0), dec(0, 1, 0, 0)),
        (dec(0, 9, 76, 0), dec(0, 1, 0, 0)),
        (dec(0, 109, 75, 0), dec(0, 1, 0, 0)),
        (dec(0, 1, 37, 0), dec(1, 1, -100, 0)),
        (dec(1, 1, 37, 0), dec(0, 1, -100, 0)),
        (dec(0, 10 ** 37, -85, 0), dec(0, 1, -365, 0)),
        (dec(1, 10 ** 37, -85, 0), dec(1, 1, -365, 0)),
        (dec(0, 1, -32729, 0), dec(1, 1, -32768, 0)),
        (dec(0, 1, -32731, 0), dec(1, 1, -32768, 0)),
        (dec(0, 5, 0, 2), dec(1, 5, 0, 1)),
        # Nearest-mode power-of-ten boundary: half of the lower decade unit.
        (dec(0, 1, 37, 0), dec(0, 9, -2, 0)),
        (dec(0, 1, 37, 0), dec(0, 4, -2, 0)),
        (dec(0, 1, 37, 0), dec(0, 5, -2, 0)),
        (dec(1, 1, 37, 0), dec(1, 9, -2, 0)),
        (dec(1, 1, 37, 0), dec(1, 4, -2, 0)),
        (dec(1, 1, 37, 0), dec(1, 5, -2, 0)),
    ]
    for a, b in fixed:
        for mode in range(8):
            st, r = ar.add_rounded(a, b, mode, max(a.dscale, b.dscale))
            add_op("add", a, b, mode, st, r)
            st, r = ar.sub_rounded(a, b, mode, max(a.dscale, b.dscale))
            add_op("sub", a, b, mode, st, r)

    # Random rounded add/sub, including gaps wider than the 38-digit window.
    for i in range(260):
        a = gen_operand(rng, -200, 200)
        b = gen_operand(rng, -200, 200)
        if i % 3 == 0:
            b = dec(b.sign, b.coeff, max(-32768, a.exp - rng.randint(39, 400)),
                    b.dscale)
        if i % 5 == 0:
            a = dec(a.sign, a.coeff, max(-32768, b.exp - rng.randint(39, 400)),
                    a.dscale)
        mode = rng.randrange(8)
        for kind, fn in (("add", ar.add_rounded), ("sub", ar.sub_rounded)):
            st, r = fn(a, b, mode, max(a.dscale, b.dscale))
            add_op(kind, a, b, mode, st, r)

    # Random rounded multiply.
    for _ in range(120):
        a = gen_operand(rng, -40, 40)
        b = gen_operand(rng, -40, 40)
        mode = rng.randrange(8)
        ds = min(a.dscale + b.dscale, 65535)
        st, r = ar.mul_rounded(a, b, mode, ds)
        add_op("mul", a, b, mode, st, r)

    # Exact add/sub/mul.
    for i in range(120):
        a = gen_operand(rng, -80, 80)
        b = gen_operand(rng, -80, 80)
        if i % 4 == 0:
            b = dec(b.sign, b.coeff, max(-32768, a.exp - rng.randint(39, 200)),
                    b.dscale)
        ds = rng.randrange(0, 65536)
        for op, fn in ((0, ar.add_exact), (1, ar.sub_exact), (2, ar.mul_exact)):
            st, r = fn(a, b, ds)
            add_exact(op, a, b, ds, st, r)

    # Exact division: terminating, nonterminating, zero, sign and range.
    div_pairs = [(1, 2), (1, 8), (1, 40), (1, 3), (1, 7), (10, 2),
                 (10 ** 37, 2), (ar.MAX_COEFF, 3), (1, ar.MAX_COEFF)]
    for ca, cb in div_pairs:
        for ds in (0, 7, 65535):
            add_exact(3, dec(0, ca, 0, 0), dec(0, cb, 0, 0), ds,
                      *ar.div_exact(dec(0, ca, 0, 0), dec(0, cb, 0, 0), ds))
    for i in range(140):
        a = gen_operand(rng, -60, 60)
        b = gen_operand(rng, -60, 60)
        if i % 5 == 0:
            b = dec(1 if i & 1 else 0, rng.choice([2, 4, 5, 8, 10, 16, 20]),
                    b.exp, b.dscale)
        ds = rng.randrange(0, 65536)
        add_exact(3, a, b, ds, *ar.div_exact(a, b, ds))

    # Rounded division: explicit scale and precision, all modes.
    fixed_div = [
        (dec(0, 1, 0, 0), dec(0, 3, 0, 0)),
        (dec(0, 2, 0, 0), dec(0, 3, 0, 0)),
        (dec(0, 1, 0, 0), dec(0, 8, 0, 0)),
        (dec(0, 1, 0, 0), dec(0, 7, 0, 0)),
        (dec(0, 10, 0, 0), dec(0, 4, 0, 0)),
        (dec(1, 1, 0, 0), dec(0, 3, 0, 0)),
        (dec(0, 9999, -2, 0), dec(0, 1, 0, 0)),
        (dec(0, 1, 100, 0), dec(0, 2, 0, 0)),
    ]
    for a, b in fixed_div:
        for scale in (-2, 0, 1, 2, 5, 10):
            for mode in range(8):
                st, r = ar.div_scale(a, b, scale, mode, 4)
                add_div(0, a, b, scale, 0, mode, 4, st, r)
        for precision in (1, 2, 3, 5, 38):
            for mode in range(8):
                st, r = ar.div_precision(a, b, precision, mode, 4)
                add_div(1, a, b, 0, precision, mode, 4, st, r)
    for i in range(300):
        a = gen_operand(rng, -50, 50)
        b = gen_operand(rng, -50, 50)
        mode = rng.randrange(8)
        if i % 2 == 0:
            scale = rng.choice([-10, -2, -1, 0, 1, 2, 3, 6, 10, 19, 38])
            st, r = ar.div_scale(a, b, scale, mode, rng.randrange(0, 65536))
            add_div(0, a, b, scale, 0, mode, r.dscale if r else 0, st, r)
        else:
            precision = rng.choice([1, 2, 3, 5, 10, 19, 20, 37, 38])
            st, r = ar.div_precision(a, b, precision, mode, rng.randrange(0, 65536))
            add_div(1, a, b, 0, precision, mode, r.dscale if r else 0, st, r)

    # Truncating remainder, including huge exponent gaps and large divisors.
    rem_pairs = [(7, 3), (-7, 3), (7, -3), (-7, -3), (15, 4), (15, -4),
                 (1, 3), (10 ** 37, 3), (1, 10 ** 37)]
    for ca, cb in rem_pairs:
        a = dec(1 if ca < 0 else 0, abs(ca), 0, 0)
        b = dec(1 if cb < 0 else 0, abs(cb), 0, 0)
        add_rem(a, b, 9, *ar.rem_trunc(a, b, 9))
    for i in range(180):
        a = gen_operand(rng, -100, 100)
        b = gen_operand(rng, -100, 100)
        if i % 7 == 0:
            a = dec(a.sign, a.coeff, 32767, a.dscale)
            b = dec(b.sign, b.coeff, -32768, b.dscale)
        ds = rng.randrange(0, 65536)
        add_rem(a, b, ds, *ar.rem_trunc(a, b, ds))

    return {
        "format": "obo-decimal-arithmetic-vectors-v1",
        "seed": hex(0xD2A11CE),
        "add": add_v, "sub": sub_v, "mul": mul_v,
        "exact": exact_v, "div": div_v, "rem": rem_v,
        "counts": {"add": len(add_v), "sub": len(sub_v), "mul": len(mul_v),
                   "exact": len(exact_v), "div": len(div_v),
                   "rem": len(rem_v)},
    }


def write_header(v, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* generated by generate_arithmetic_vectors.py; private D2 */\n")
        f.write("#ifndef DEC24_ARITH_VECTORS_GENERATED_H\n"
                "#define DEC24_ARITH_VECTORS_GENERATED_H\n")
        f.write("#include <stdint.h>\n\n")
        f.write("typedef struct { int sign_a; uint64_t alo, ahi; int ea; unsigned da; "
                "int sign_b; uint64_t blo, bhi; int eb; unsigned db; unsigned mode; "
                "int status; int sign_r; uint64_t rlo, rhi; int er; unsigned dr; } "
                "ArithOpVec;\n")
        f.write("typedef struct { unsigned op; int sign_a; uint64_t alo, ahi; int ea; "
                "unsigned da; int sign_b; uint64_t blo, bhi; int eb; unsigned db; "
                "unsigned dscale; int status; int sign_r; uint64_t rlo, rhi; int er; "
                "unsigned dr; } ArithExactVec;\n")
        f.write("typedef struct { unsigned kind; int sign_a; uint64_t alo, ahi; int ea; "
                "unsigned da; int sign_b; uint64_t blo, bhi; int eb; unsigned db; "
                "int scale; unsigned precision; unsigned mode; unsigned dscale; "
                "int status; int sign_r; uint64_t rlo, rhi; int er; unsigned dr; } "
                "ArithDivVec;\n")
        f.write("typedef struct { int sign_a; uint64_t alo, ahi; int ea; unsigned da; "
                "int sign_b; uint64_t blo, bhi; int eb; unsigned db; unsigned dscale; "
                "int status; int sign_r; uint64_t rlo, rhi; int er; unsigned dr; } "
                "ArithRemVec;\n\n")

        def emit_op(name, arr):
            f.write(f"static const ArithOpVec {name}[] = {{\n")
            for row in arr:
                a, b = row["a"], row["b"]
                r = row.get("r", ar.zero())
                sa, alo, ahi, ea, da = parts(a)
                sb, blo, bhi, eb, db = parts(b)
                sr, rlo, rhi, er, dr = parts(r)
                f.write(f"  {{{sa},{c_u64(alo)},{c_u64(ahi)},{ea},{da},"
                        f"{sb},{c_u64(blo)},{c_u64(bhi)},{eb},{db},"
                        f"{row['mode']},{row['status']},{sr},{c_u64(rlo)},"
                        f"{c_u64(rhi)},{er},{dr}}},\n")
            f.write("};\n")
            f.write(f"#define {name.upper()}_COUNT {len(arr)}u\n\n")

        emit_op("arith_add_vectors", v["add"])
        emit_op("arith_sub_vectors", v["sub"])
        emit_op("arith_mul_vectors", v["mul"])

        f.write("static const ArithExactVec arith_exact_vectors[] = {\n")
        for row in v["exact"]:
            a, b = row["a"], row["b"]
            r = row.get("r", ar.zero())
            sa, alo, ahi, ea, da = parts(a)
            sb, blo, bhi, eb, db = parts(b)
            sr, rlo, rhi, er, dr = parts(r)
            f.write(f"  {{{row['op']},{sa},{c_u64(alo)},{c_u64(ahi)},{ea},{da},"
                    f"{sb},{c_u64(blo)},{c_u64(bhi)},{eb},{db},{row['dscale']},"
                    f"{row['status']},{sr},{c_u64(rlo)},{c_u64(rhi)},{er},{dr}}},\n")
        f.write("};\n")
        f.write(f"#define ARITH_EXACT_VECTORS_COUNT {len(v['exact'])}u\n\n")

        f.write("static const ArithDivVec arith_div_vectors[] = {\n")
        for row in v["div"]:
            a, b = row["a"], row["b"]
            r = row.get("r", ar.zero())
            sa, alo, ahi, ea, da = parts(a)
            sb, blo, bhi, eb, db = parts(b)
            sr, rlo, rhi, er, dr = parts(r)
            f.write(f"  {{{row['kind']},{sa},{c_u64(alo)},{c_u64(ahi)},{ea},{da},"
                    f"{sb},{c_u64(blo)},{c_u64(bhi)},{eb},{db},{row['scale']},"
                    f"{row['precision']},{row['mode']},{row['dscale']},"
                    f"{row['status']},{sr},{c_u64(rlo)},{c_u64(rhi)},{er},{dr}}},\n")
        f.write("};\n")
        f.write(f"#define ARITH_DIV_VECTORS_COUNT {len(v['div'])}u\n\n")

        f.write("static const ArithRemVec arith_rem_vectors[] = {\n")
        for row in v["rem"]:
            a, b = row["a"], row["b"]
            r = row.get("r", ar.zero())
            sa, alo, ahi, ea, da = parts(a)
            sb, blo, bhi, eb, db = parts(b)
            sr, rlo, rhi, er, dr = parts(r)
            f.write(f"  {{{sa},{c_u64(alo)},{c_u64(ahi)},{ea},{da},"
                    f"{sb},{c_u64(blo)},{c_u64(bhi)},{eb},{db},{row['dscale']},"
                    f"{row['status']},{sr},{c_u64(rlo)},{c_u64(rhi)},{er},{dr}}},\n")
        f.write("};\n")
        f.write(f"#define ARITH_REM_VECTORS_COUNT {len(v['rem'])}u\n\n")
        f.write("#endif\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=HERE)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    v = build_vectors()
    cases_path = os.path.join(out, "arithmetic_cases.json")
    with open(cases_path, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=2, sort_keys=True,
                  default=lambda o: enc_dec(o))
        f.write("\n")
    header_path = os.path.join(out, "arithmetic_vectors_generated.h")
    write_header(v, header_path)
    result = {
        "format": "obo-decimal-arithmetic-artifacts-v1",
        "seed": v["seed"],
        "model": "arithmetic_oracle.py",
        "model_sha256": sha256(os.path.join(HERE, "arithmetic_oracle.py")),
        "corpus": "arithmetic_cases.json",
        "corpus_sha256": sha256(cases_path),
        "vectors_header": "arithmetic_vectors_generated.h",
        "vectors_header_sha256": sha256(header_path),
        "counts": v["counts"],
    }
    with open(os.path.join(out, "arithmetic_generator_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({"counts": v["counts"],
                      "corpus_sha256": result["corpus_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
