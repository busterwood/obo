#!/usr/bin/env python3
"""Build/run the private D1 decimal foundations and record fresh evidence.

The freestanding link uses ld with no libc, libm, libgcc or startup library.
Generated fixtures and logs go to --evidence (default /tmp/opencode/...), never
into the repository working tree.  A host ASAN/UBSAN build is a separate,
clearly labelled diagnostic and is not the freestanding artifact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CFLAGS = [
    "-O2", "-ffreestanding", "-fno-builtin", "-nostdlib", "-fno-pic",
    "-fno-pie", "-fno-stack-protector", "-fno-asynchronous-unwind-tables",
    "-mcmodel=small", "-fno-tree-loop-distribute-patterns",
    "-Wall", "-Wextra", "-Werror", "-std=c11",
]
SAN_FLAGS = [
    "-O1", "-g", "-std=c11", "-Wall", "-Wextra", "-Werror",
    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
    "-fno-omit-frame-pointer",
]
CORE = ["dec24.c", "dec24_text.c", "dec24_bind.c"]
TEST_SRC = [
    "dec24_test.c", "dec24_text_test.c", "dec24_bind_test.c", "all_test.c",
]
FORBIDDEN = (
    "__udivti3", "__udivmodti4", "__umodti3", "__divti3", "__multi3",
    "memcpy", "memset", "malloc", "free", "printf", "strtod", "pow", "sqrt",
)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], cwd: str, timeout: int = 300, env=None):
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True,
                       timeout=timeout, env=env)
    return p, time.monotonic() - t0


def nm_undef(path: str) -> list[str]:
    p, _ = run(["nm", "-u", path], ROOT)
    return [line.split()[-1] for line in p.stdout.splitlines() if line.strip()]


def source_hashes() -> dict:
    out = {}
    for d in (ROOT, HERE):
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            if name.endswith((".c", ".h", ".py", ".S", ".md")):
                out[os.path.relpath(p, ROOT)] = sha256(p)
    return out


def git_state() -> dict:
    p, _ = run(["git", "rev-parse", "HEAD"], ROOT)
    q, _ = run(["git", "status", "--short"], ROOT)
    return {"head": p.stdout.strip(), "status_short": q.stdout.splitlines()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=None)
    args = ap.parse_args()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = os.path.abspath(
        args.evidence or os.path.join("/tmp/opencode", "decimal-D1-" + stamp))
    os.makedirs(evidence, exist_ok=True)
    inc = ["-I", ROOT, "-I", HERE, "-I", evidence]

    steps = []
    generated = []
    for gen in ("generate_decimal_vectors.py", "generate_text_vectors.py",
                "generate_binding_vectors.py"):
        p, secs = run([sys.executable, gen, "--out", evidence], HERE,
                      timeout=120)
        steps.append({"cmd": [sys.executable, gen, "--out", evidence],
                      "returncode": p.returncode, "seconds": round(secs, 3),
                      "stdout": p.stdout, "stderr": p.stderr})
        generated.append(gen)
        if p.returncode != 0:
            print(p.stdout, p.stderr)
            return 1

    objs = []
    for src in CORE:
        obj = os.path.join(evidence, os.path.basename(src)[:-2] + ".o")
        cmd = ["gcc", *CFLAGS, *inc, "-c", os.path.join(ROOT, src), "-o", obj]
        p, secs = run(cmd, ROOT)
        steps.append({"cmd": cmd, "returncode": p.returncode,
                      "seconds": round(secs, 3), "stdout": p.stdout,
                      "stderr": p.stderr})
        if p.returncode != 0:
            print(p.stdout, p.stderr)
            return 1
        objs.append(obj)
    test_objs = []
    for src in TEST_SRC:
        obj = os.path.join(evidence, os.path.basename(src)[:-2] + ".o")
        cmd = ["gcc", *CFLAGS, *inc, "-c", os.path.join(HERE, src), "-o", obj]
        p, secs = run(cmd, ROOT)
        steps.append({"cmd": cmd, "returncode": p.returncode,
                      "seconds": round(secs, 3), "stdout": p.stdout,
                      "stderr": p.stderr})
        if p.returncode != 0:
            print(p.stdout, p.stderr)
            return 1
        test_objs.append(obj)

    start_o = os.path.join(evidence, "start.o")
    cmd = ["gcc", "-c", os.path.join(HERE, "start.S"), "-o", start_o]
    p, secs = run(cmd, ROOT)
    steps.append({"cmd": cmd, "returncode": p.returncode,
                  "seconds": round(secs, 3), "stdout": p.stdout,
                  "stderr": p.stderr})
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        return 1

    binary = os.path.join(evidence, "decimal_d1_test")
    cmd = ["ld", "-o", binary, start_o, *objs, *test_objs]
    p, secs = run(cmd, ROOT)
    steps.append({"cmd": cmd, "returncode": p.returncode,
                  "seconds": round(secs, 3), "stdout": p.stdout,
                  "stderr": p.stderr})
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        return 1

    p, secs = run([binary], ROOT, timeout=180)
    with open(os.path.join(evidence, "c_test_output.txt"), "w",
              encoding="utf-8") as f:
        f.write(p.stdout)
        f.write(p.stderr)
    c_checks = [int(x) for x in re.findall(r"PASS(?: [a-z]+)? checks=(\d+)",
                                           p.stdout)]
    total_checks = sum(c_checks)
    c_ok = (p.returncode == 0 and not p.stderr and
            "FAIL" not in p.stdout and len(c_checks) == 3)
    if not c_ok:
        print(p.stdout, p.stderr)
        return 1

    nm_lines = []
    nm_all = []
    nm_core = []
    nm_binary = []
    for path in [*objs, *test_objs, binary]:
        syms = nm_undef(path)
        nm_all.extend(syms)
        if path in objs:
            nm_core.extend(syms)
        if path == binary:
            nm_binary.extend(syms)
        q, _ = run(["nm", "-u", path], ROOT)
        nm_lines.append(f"# nm -u {os.path.basename(path)}\n{q.stdout}{q.stderr}")
    with open(os.path.join(evidence, "nm.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(nm_lines))
    forbidden_hits = []
    for sym in sorted(set(nm_all)):
        for bad in FORBIDDEN:
            if sym == bad or sym.startswith(bad + "@"):
                forbidden_hits.append(sym)
    if forbidden_hits or nm_binary:
        print("forbidden/undefined final symbols:", forbidden_hits, nm_binary)
        return 1

    sz, _ = run(["size", binary, *objs], ROOT)
    with open(os.path.join(evidence, "sizes.txt"), "w", encoding="utf-8") as f:
        f.write(sz.stdout)
        f.write(sz.stderr)
    dis, _ = run(["objdump", "-d", binary], ROOT)
    with open(os.path.join(evidence, "objdump_div.txt"), "w",
              encoding="utf-8") as f:
        for line in dis.stdout.splitlines():
            if "\tdiv" in line or "\tmul" in line:
                f.write(line + "\n")

    py_cmd = [sys.executable, "-m", "unittest", "-v",
              "test_decimal_oracle", "test_decimal_text_oracle",
              "test_binding_oracle"]
    py, py_secs = run(py_cmd, HERE, timeout=180)
    with open(os.path.join(evidence, "python_test_output.txt"), "w",
              encoding="utf-8") as f:
        f.write(py.stdout)
        f.write(py.stderr)

    san_bin = os.path.join(evidence, "decimal_d1_test_san")
    san_cmd = ["gcc", *SAN_FLAGS, *inc,
               *[os.path.join(ROOT, s) for s in CORE],
               *[os.path.join(HERE, s) for s in TEST_SRC],
               os.path.join(HERE, "host_main.c"), "-o", san_bin]
    san_build, san_build_secs = run(san_cmd, ROOT)
    san_out = ""
    san_ok = False
    san_secs = 0.0
    if san_build.returncode == 0:
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0",
                   UBSAN_OPTIONS="print_stacktrace=1")
        sp, san_secs = run([san_bin], ROOT, timeout=300, env=env)
        san_out = sp.stdout + sp.stderr
        san_ok = (sp.returncode == 0 and "FAIL" not in sp.stdout and
                  "ERROR:" not in san_out and "runtime error:" not in san_out)
    with open(os.path.join(evidence, "sanitizer_output.txt"), "w",
              encoding="utf-8") as f:
        f.write(san_build.stdout)
        f.write(san_build.stderr)
        f.write(san_out)

    hashes = source_hashes()
    result = {
        "format": "obo-decimal-D1-evidence-v1",
        "generated_utc": stamp,
        "git": git_state(),
        "scope": {
            "kind": "experimental private dec24 foundations",
            "not_compiler_integration": True,
            "not_public_api": True,
            "no_language_abi_or_literal_policy": True,
            "no_database_connection_or_driver": True,
            "no_division_api": True,
            "no_default_rounding": True,
        },
        "generators": generated,
        "build_steps": steps,
        "c_test_returncode": p.returncode,
        "c_test_checks_total": total_checks,
        "c_test_pass_lines": [line for line in p.stdout.splitlines()
                              if line.startswith("PASS")],
        "c_test_seconds": round(secs, 3),
        "nm_undefined_all_objects": sorted(set(nm_all)),
        "nm_undefined_core_objects": sorted(set(nm_core)),
        "nm_undefined_final_binary": sorted(set(nm_binary)),
        "forbidden_undefined_hits": forbidden_hits,
        "sizes": sz.stdout,
        "python_returncode": py.returncode,
        "python_seconds": round(py_secs, 3),
        "python_output_tail": py.stdout.splitlines()[-8:],
        "sanitizer_build_returncode": san_build.returncode,
        "sanitizer_build_seconds": round(san_build_secs, 3),
        "sanitizer_run_seconds": round(san_secs, 3),
        "sanitizer_ok": san_ok,
        "source_sha256": hashes,
        "generated_files": {
            name: sha256(os.path.join(evidence, name))
            for name in sorted(os.listdir(evidence))
            if name.endswith((".h", ".json", ".txt"))
        },
    }
    with open(os.path.join(evidence, "results.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    with open(os.path.join(evidence, "run_summary.txt"), "w",
              encoding="utf-8") as f:
        json.dump({
            "c_test_returncode": result["c_test_returncode"],
            "c_test_checks_total": total_checks,
            "c_test_pass_lines": result["c_test_pass_lines"],
            "c_test_seconds": result["c_test_seconds"],
            "python_returncode": py.returncode,
            "python_seconds": result["python_seconds"],
            "sanitizer_ok": san_ok,
            "sanitizer_build_returncode": san_build.returncode,
            "forbidden_undefined_hits": forbidden_hits,
            "nm_undefined_core_objects": result["nm_undefined_core_objects"],
            "nm_undefined_final_binary": result["nm_undefined_final_binary"],
        }, f, indent=2, sort_keys=True)
        f.write("\n")

    ok = (py.returncode == 0 and san_build.returncode == 0 and san_ok and
          not forbidden_hits)
    print(json.dumps({
        "evidence": evidence,
        "c_checks": total_checks,
        "c_pass_lines": result["c_test_pass_lines"],
        "c_seconds": result["c_test_seconds"],
        "python_returncode": py.returncode,
        "python_seconds": result["python_seconds"],
        "sanitizer_ok": san_ok,
        "sanitizer_seconds": round(san_build_secs + san_secs, 3),
        "forbidden_hits": forbidden_hits,
        "nm_undefined_final_binary": result["nm_undefined_final_binary"],
        "ok": ok,
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
