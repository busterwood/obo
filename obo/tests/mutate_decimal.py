#!/usr/bin/env python3
"""Focused mutation gate for the new decimal compiler paths (0.12 WIP).

Each mutation is one textual change in obo/src/obo.obo or in the reviewed
runtime core include.  Compiler mutants are built from seed/obo; runtime mutants
are built into a private runtime directory and the compiler is rebuilt against
it.  The decimal subset of run_fork (run files, error cases and the decimal
module tree) decides each verdict.  A mutant is caught when at least one
decimal test fails.  This is deliberately separate from the historical
mutate_fork/mutate_port catalogs, which stay unchanged; it is not a rewrite of
those gates.

    python3 obo/tests/mutate_decimal.py [-v]
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
SRC = os.path.join(FORK, 'src', 'obo.obo')
SEED = os.path.join(FORK, 'seed', 'obo')
RUNNER = os.path.join(HERE, 'run_fork.py')
RUNTIME = os.path.join(FORK, 'runtime')
RT_INC = os.path.join(RUNTIME, 'decimal24_core.inc')
CFLAGS = ['-O2', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-pic',
          '-fno-pie', '-fno-stack-protector', '-fno-asynchronous-unwind-tables',
          '-mcmodel=small', '-fno-tree-loop-distribute-patterns', '-Wall']

MUTANTS = [
    ("literal dscale ignores the explicit exponent",
     "var ds = frac - explicit\n", "var ds = frac\n"),
    ("literal explicit exponent sign is dropped",
     'if !edig { fail("a decimal exponent needs digits") }\n        if eneg { explicit = -explicit }',
     'if !edig { fail("a decimal exponent needs digits") }\n        if eneg { explicit = explicit }'),
    ("literal precision limit is 39 digits",
     'if len(sig) > 38 { fail("decimal literal has more than 38 significant digits") }',
     'if len(sig) > 39 { fail("decimal literal has more than 38 significant digits") }', 2),
    ("literal display scale is packed at bit 15",
     "((d.dscale & 0xffff) << 16)", "((d.dscale & 0xffff) << 15)"),
    ("literal exponent is packed unmasked",
     "(d.exp & 0xffff)", "d.exp"),
    ("literal zero loses its display scale",
     "return new DecLit{lo: 0, hi: 0, exp: 0, dscale: ds}",
     "return new DecLit{lo: 0, hi: 0, exp: 0, dscale: 0}"),
    ("decimal parameter offset is one word per later parameter",
     "return 16 + paramsBytes(params, i + 1)",
     "return 16 + 8 * (len(params) - 1 - i)"),
    ("decimal return destination is one word per visible parameter",
     "16 + visibleTypeParamsBytes(f.fnRec.params)",
     "16 + 8 * len(f.fnRec.params)"),
    ("decimal argument words are pushed low first",
     "let off = 16 - 8 * k", "let off = 8 * k"),
    # record fields (0.16, format 9): a decimal field is 24 bytes, three words, in the
    # record and in the symbol file
    ("decimal record field is one word (sizeOf 8)",
     'fn sizeOf(t: ^Type): int {\n    if t.kind == "decimal" { return 24 }',
     'fn sizeOf(t: ^Type): int {\n    if t.kind == "decimal" { return 8 }'),
    ("decimal record field copy writes two words",
     "fn copyDecimalToBase(src0: &Item, p: int, disp: int) {\n    var src = copyItem(src0)\n    resolve(src)\n    for off in 0..3 {",
     "fn copyDecimalToBase(src0: &Item, p: int, disp: int) {\n    var src = copyItem(src0)\n    resolve(src)\n    for off in 0..2 {"),
    ("decimal record literal field is not copied",
     "        if fty.kind == \"decimal\" {\n            copyDecimalToBase(v, p.r, foff)\n            drop(v)",
     "        if fty.kind == \"decimal\" {\n            drop(v)"),
    ("decimal compound field result is not stored",
     "                rtCall(slot, dargs, tVoid)\n                store(x, res)",
     "                rtCall(slot, dargs, tVoid)\n                drop(x)"),
    ("sym: private decimal slot read as one word",
     'if marker == "priv24" { pt = tDecimal }', 'if marker == "priv24" { pt = tInt }'),
    ("sym: reader advances every slot by one word",
     "            offset += sizeOf(pt)", "            offset += 8"),
    ("sym: private decimal slot written as priv",
     '            } else if rec.fields[i].typ.kind == "decimal" {\n                appStr(b, "priv24")',
     '            } else if rec.fields[i].typ.kind == "decimal" {\n                appStr(b, "priv")'),
    # a decimal left operand is captured before a later operand runs (the review's
    # use-after-free and wrong-value defect): each operator branch, relation and the builtins
    ("decimal left operand is not captured",
     "    let t = decimalTemp()\n    copyDecimal(t, x)",
     "    let t = x"),
    ("decimal term left operand is not captured",
     "            if o != T_STAR && o != T_PERCENT { error(\"'\" + tokName(o) + \"' is not supported for decimal; use + - * %\") }\n            x = captureDecimal(x)",
     "            if o != T_STAR && o != T_PERCENT { error(\"'\" + tokName(o) + \"' is not supported for decimal; use + - * %\") }\n            x = materialise(x)"),
    ("decimal sum left operand is not captured",
     "            if o != T_PLUS && o != T_MINUS { error(\"'\" + tokName(o) + \"' is not supported for decimal; use + - * %\") }\n            x = captureDecimal(x)",
     "            if o != T_PLUS && o != T_MINUS { error(\"'\" + tokName(o) + \"' is not supported for decimal; use + - * %\") }\n            x = materialise(x)"),
    ("decimal comparison left operand is not captured",
     "    x = captureDecimal(x)\n    noTemp(x)",
     "    noTemp(x)"),
    ("decimal builtin earlier decimal argument is not captured",
     "    if x.typ.kind == \"decimal\" { return captureDecimal(x) }\n    if isInt(x.typ) { return materialise(x) }",
     "    if isInt(x.typ) { return materialise(x) }"),
    ("decimal builtin earlier int argument is not loaded",
     "    if isInt(x.typ) { return materialise(x) }\n    return x\n}",
     "    if isInt(x.typ) { return x }\n    return x\n}"),
    ("decimal request stores scale over mode",
     "    sd.disp = at\n", "    sd.disp = at + 8\n"),
    ("decimal comparison always tests equality",
     "z.cc = relCC(o)", "z.cc = CC_E"),
    ("decimal locals get one word",
     'if t.kind == "decimal" { frame += 24 } else { frame += 8 }',
     'if t.kind == "decimal" { frame += 8 } else { frame += 8 }'),
    ("a decimal copy writes two words",
     "for off in 0..3 {\n        let r = alloc()\n        opMem(0x8B, r, mem(src.base, src.index, src.disp + 8 * off, 8), true, false)\n        opMem(0x89, r, mem(dst.base, dst.index, dst.disp + 8 * off, 8), true, false)\n        release(r)\n    }",
     "for off in 0..2 {\n        let r = alloc()\n        opMem(0x8B, r, mem(src.base, src.index, src.disp + 8 * off, 8), true, false)\n        opMem(0x89, r, mem(dst.base, dst.index, dst.disp + 8 * off, 8), true, false)\n        release(r)\n    }"),
    ("try moves only one word of a Result[decimal] payload",
     "        for off in 0..3 {\n            let dr = alloc()\n            opMem(0x8B, dr, mem(cellR, -1, 8 * off, 8), true, false)\n            opMem(0x89, dr, mem(RBP, -1, decimalDisp + 8 * off, 8), true, false)\n            release(dr)\n        }",
     "        for off in 0..1 {\n            let dr = alloc()\n            opMem(0x8B, dr, mem(cellR, -1, 8 * off, 8), true, false)\n            opMem(0x89, dr, mem(RBP, -1, decimalDisp + 8 * off, 8), true, false)\n            release(dr)\n        }"),
    ("decimal multiply calls the add helper",
     "if o == T_STAR { x = decimalCall2(RT_DEC_MUL, x, y) } else { x = decimalCall2(RT_DEC_REM, x, y) }",
     "if o == T_STAR { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_REM, x, y) }"),
    ("decimal subtraction calls the add helper",
     "if o == T_PLUS { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_SUB, x, y) }",
     "if o == T_PLUS { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_ADD, x, y) }"),
    ("decimal div Result uses the err descriptor for ok",
     "append(args, makeConst(tInt, rt.variants[0].desc))\n    return rtCall(RT_DEC_RESULT, args, rt)",
     "append(args, makeConst(tInt, rt.variants[1].desc))\n    return rtCall(RT_DEC_RESULT, args, rt)"),
    ("constant decimal scale validation is dropped",
     'if scale.mode == "const" && (scale.val < 0 || scale.val > 32768) {\n        error("decimal scale must be 0..32768")\n    }',
     'if false {\n        error("decimal scale must be 0..32768")\n    }'),
    ("constant decimal mode validation is dropped",
     'if mode.mode == "const" && (mode.val < 0 || mode.val > 7) {\n        error("decimal rounding mode must be 0..7")\n    }\n    let sv = load(scale)',
     'if false {\n        error("decimal rounding mode must be 0..7")\n    }\n    let sv = load(scale)'),
    ("decimal request stores mode over scale",
     "    md.disp = at + 8\n", "    md.disp = at\n"),
    # public decimal text compiler paths
    ("decimal::parse dispatches to format",
     "    return resultCall3(RT_DEC_PARSE, s, tDecimal)",
     "    return resultCall3(RT_DEC_FORMAT, s, tDecimal)"),
    ("decimal::format dispatches to canonical",
     "    return resultCall3(RT_DEC_FORMAT, decPtr(a), tString)",
     "    return resultCall3(RT_DEC_CANONICAL, decPtr(a), tString)"),
    ("decimal::canonical dispatches to format",
     "    return resultCall3(RT_DEC_CANONICAL, decPtr(a), tString)",
     "    return resultCall3(RT_DEC_FORMAT, decPtr(a), tString)"),
    ("decimal::check swaps precision and scale",
     "    return decimalCheck(a, p, s)", "    return decimalCheck(a, s, p)"),
    ("decimal::exponent returns dscale",
     "    return rtCall1(RT_DEC_EXPONENT, decPtr(a), tInt)",
     "    return rtCall1(RT_DEC_DSCALE, decPtr(a), tInt)"),
    ("decimal::dscale returns exponent",
     "    return rtCall1(RT_DEC_DSCALE, decPtr(a), tInt)",
     "    return rtCall1(RT_DEC_EXPONENT, decPtr(a), tInt)"),
    ("check request writes precision over scale",
     "    let pd = newItem(\"mem\", tInt)\n    pd.base = RBP\n    pd.disp = at\n    store(pd, pv)",
     "    let pd = newItem(\"mem\", tInt)\n    pd.base = RBP\n    pd.disp = at + 8\n    store(pd, pv)"),
    ("check request writes scale over precision",
     "    let sd = newItem(\"mem\", tInt)\n    sd.base = RBP\n    sd.disp = at + 8\n    store(sd, sv)",
     "    let sd = newItem(\"mem\", tInt)\n    sd.base = RBP\n    sd.disp = at\n    store(sd, sv)"),
]

# Runtime, core-include and entry-table mutants are built in a private runtime
# directory; the compiler is rebuilt from the unmutated source so the test
# programs embed the mutant runtime.  Each item is (name, file, old, new).
RUNTIME_MUTANTS = [
    # template decimal transport and escaping (0.15)
    ("template: sqlWhere omits per-predicate parentheses",
     "runtime.c",
     '        if (i > 0) open = tpl_mkstr(" AND (", 6);',
     '        if (i > 0) open = tpl_mkstr(" AND ", 5);'),
    ("template: SQL decimal text is always canonical",
     "runtime.c",
     "    int canonical = 0;",
     "    int canonical = 1;"),
    ("template: SQL decimal display does not fall back to canonical",
     "runtime.c",
     "    if (st == DEC_INEXACT) { st = dec_format_canonical_len(v, &n); canonical = 1; }\n",
     ""),
    ("template: SQL Str argument takes no string reference",
     "runtime.c",
     "    } else if (tag == 4) {\n        v[0] = rt_str_inc(value);",
     "    } else if (tag == 4) {\n        v[0] = value;"),
    ("template: spliced parameters all use the first argument",
     "runtime.c",
     "                tpl_sql_add_arg(sql, tpl_clone(((u64 *)((char *)fp->data + 8))[i - 1]));",
     "                tpl_sql_add_arg(sql, tpl_clone(((u64 *)((char *)fp->data + 8))[0]));"),
    ("template: HTML does not escape ampersand",
     "runtime.c",
     "        if (b == '&') { memcpy(q, amp, 5); q += 5; }",
     "        if (b == 0) { memcpy(q, amp, 5); q += 5; }"),
    ("template: URL scheme allow-list drops https",
     "runtime.c",
     "    return tpl_scheme_is(p, i, \"http\") || tpl_scheme_is(p, i, \"https\") ||\n           tpl_scheme_is(p, i, \"mailto\");",
     "    return tpl_scheme_is(p, i, \"http\") ||\n           tpl_scheme_is(p, i, \"mailto\");"),
    # existing arithmetic/runtime decimal mutants
    ("multiply cap is computed from operand a",
     "decimal24_core.inc",
     "int intd = rt_dec_coeff_digits(r) + (int)(int16_t)r->exp;",
     "int intd = rt_dec_coeff_digits(a) + (int)(int16_t)a->exp;"),
    ("runtime scale validation is dropped",
     "decimal24_core.inc",
     "    if (*scale < 0 || *scale > 32768) return DEC_UNSUPPORTED_RANGE;\n", ""),
    # decimal24_core.inc: public grammar and core text semantics
    ("public grammar allows a missing integer part",
     "decimal24_core.inc",
     "    if (i == int_start) return 0;              /* mandatory integer digit */",
     "    if (i == int_start) return 1;              /* mandatory integer digit */"),
    ("public grammar allows a trailing point",
     "decimal24_core.inc",
     "        if (i == frac_start) return 0;         /* '1.' is invalid */",
     "        if (i == frac_start) return 1;         /* '1.' is invalid */"),
    ("public strict wrapper is bypassed",
     "decimal24_core.inc",
     "    if (!dec_public_grammar(text, len)) return DEC_INVALID;",
     "    if (0) return DEC_INVALID;"),
    ("precision limit becomes 39 digits",
     "decimal24_core.inc",
     "    if (p->sig < 38) {",
     "    if (p->sig < 39) {"),
    ("parse dscale ignores the explicit exponent",
     "decimal24_core.inc",
     "    __int128 dscale = (__int128)frac_digits - explicit_exp;",
     "    __int128 dscale = (__int128)frac_digits;"),
    ("parse display scale clamps at 65534",
     "decimal24_core.inc",
     "    if (dscale > 65535) return DEC_DISPLAY_RANGE;",
     "    if (dscale > 65534) return DEC_DISPLAY_RANGE;"),
    ("parse exponent lower bound becomes -32767",
     "decimal24_core.inc",
     "    if (exp_final < -32768) return DEC_EXPONENT_RANGE;",
     "    if (exp_final < -32767) return DEC_EXPONENT_RANGE;"),
    ("upper rescue allows 37 coefficient zeros",
     "decimal24_core.inc",
     "        if (shift > (__int128)(38 - digits)) return DEC_EXPONENT_RANGE;",
     "        if (shift > (__int128)(37 - digits)) return DEC_EXPONENT_RANGE;"),
    ("negative zero keeps its sign",
     "decimal24_core.inc",
     "    if (!acc.started) {\n        return dec_set(out, 0, 0, 0, 0, (uint16_t)dscale);\n    }",
     "    if (!acc.started) {\n        dec_set(out, 0, 0, 0, 0, (uint16_t)dscale);\n        out->sign = (uint8_t)sign;\n        return DEC_OK;\n    }"),
    ("parse precision status becomes exponent range",
     "decimal24_core.inc",
     "    if (acc.overflow) return DEC_PRECISION_OVERFLOW;",
     "    if (acc.overflow) return DEC_EXPONENT_RANGE;"),
    ("canonical fixed threshold is 127",
     "decimal24_core.inc",
     "#define DEC_TEXT_CANON_FIXED_MAX 128u",
     "#define DEC_TEXT_CANON_FIXED_MAX 127u"),
    ("canonical keeps coefficient trailing zeros",
     "decimal24_core.inc",
     "    int sd = len - trailing;",
     "    int sd = len;"),
    ("canonical adjusted exponent is off by one",
     "decimal24_core.inc",
     "    int adj = e + len - 1;",
     "    int adj = e + len;"),
    ("format INEXACT boundary is inclusive",
     "decimal24_core.inc",
     "    if ((__int128)e + (__int128)s + (__int128)trailing < 0) return DEC_INEXACT;",
     "    if ((__int128)e + (__int128)s + (__int128)trailing <= 0) return DEC_INEXACT;"),
    ("format writes dscale-1 fraction digits",
     "decimal24_core.inc",
     "        for (int j = 0; j < (int)l.dscale; j++) {",
     "        for (int j = 0; j < (int)l.dscale - 1; j++) {"),
    ("format truncates a dropped nonzero digit",
     "decimal24_core.inc",
     "    if ((__int128)e + (__int128)s + (__int128)trailing < 0) return DEC_INEXACT;",
     "    if ((__int128)e + (__int128)s + (__int128)trailing < -1) return DEC_INEXACT;"),
    ("check trailing-zero comparison is inclusive",
     "decimal24_core.inc",
     "        if (k < 0 && (i64)trailing < -k)",
     "        if (k < 0 && (i64)trailing <= -k)"),
    ("check ignores coefficient trailing zeros",
     "decimal24_core.inc",
     "        if (k < 0 && (i64)trailing < -k)",
     "        if (k < 0 && (i64)0 < -k)"),
    ("check precision comparison is strict",
     "decimal24_core.inc",
     "        if ((i64)len + k > p)",
     "        if ((i64)len + k >= p)"),
    ("check p range includes zero",
     "decimal24_core.inc",
     "    if (p < 1 || p > 1000 || s < -1000 || s > 1000)",
     "    if (p < 0 || p > 1000 || s < -1000 || s > 1000)"),
    ("exponent returns the raw stored exponent",
     "decimal24_core.inc",
     "    return (i64)(int16_t)a->exp + (i64)trailing;",
     "    return (i64)(int16_t)a->exp;"),
    ("dscale reads the exponent field",
     "decimal24_core.inc",
     "    return (i64)a->dscale;",
     "    return (i64)(int16_t)a->exp;"),
    ("formatter length is one byte long",
     "decimal24_core.inc",
     "    size_t req = (size_t)int_digits + (s ? 1u + (size_t)s : 0u) +\n                 (v->sign ? 1u : 0u);",
     "    size_t req = (size_t)int_digits + (s ? 1u + (size_t)s : 0u) +\n                 (v->sign ? 1u : 0u) + 1u;"),
    ("text allocation failure becomes display range",
     "decimal24_core.inc",
     "    if (!sp) return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, op);",
     "    if (!sp) return rt_dec_text_err(descErr, DEC_DISPLAY_RANGE, op);"),
    ("ok cell allocation failure becomes display range",
     "decimal24_core.inc",
     "    if (!c) return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, (int)op);",
     "    if (!c) return rt_dec_text_err(descErr, DEC_DISPLAY_RANGE, (int)op);"),
    # runtime.c: the recoverable allocator and the fatal diagnostics
    ("alloc_raw loses its object-too-large reason",
     "runtime.c",
     '    if (payload > MAX_PAYLOAD) die("obx: object too large\\n");',
     '    if (payload > MAX_PAYLOAD) die("obx: out of memory\\n");'),
    ("alloc_raw loses its out-of-memory reason",
     "runtime.c",
     '    if (!p) die("obx: out of memory\\n");',
     '    if (!p) die("obx: object too large\\n");'),
    ("alloc_raw_try dies on an over-large payload",
     "runtime.c",
     "    if (payload > MAX_PAYLOAD) return 0;",
     '    if (payload > MAX_PAYLOAD) die("obx: object too large\\n");'),
]

# Decimal arrays (0.16, kind 7 and rt_append_dec). Each compiler mutant is caught by a
# run test named in its comment; each runtime mutant by a run test or by a C array case in
# tests/rt_heap_test.c, run by run_array_suite below.
ARRAY_MUTANTS = [
    # decimal_array_sizes and decimal_array_elements index every element through the stride
    ("decimal array stride is 16 bytes",
     "            opMem(0x8D, r, mem(old, old, 0, 2), true, false)   // lea r, [old + old*2]",
     "            opMem(0x8D, r, mem(old, old, 0, 1), true, false)   // lea r, [old + old*2]"),
    # decimal_array_elements: `for i in 0..len(ys) { ys[i] = ... }` keeps i in a register
    ("decimal array index is scaled in place",
     "            let r = alloc()\n            opMem(0x8D, r, mem(old, old, 0, 2), true, false)   // lea r, [old + old*2]",
     "            let r = old\n            opMem(0x8D, r, mem(old, old, 0, 2), true, false)   // lea r, [old + old*2]"),
    # decimal_array_elements: `for d in ys` copies all three words
    ("decimal for-in copies one word",
     "            for off in 0..3 {\n                let tw = alloc()",
     "            for off in 0..1 {\n                let tw = alloc()"),
]

# runtime mutants (name, file, old, new); each is caught by the named test
RUNTIME_ARRAY_MUTANTS = [
    # decimal_array_sizes: the third word of an appended decimal is lost
    ("rt_append_dec copies only two words", "runtime.c",
     "    slot[0] = w0; slot[1] = w1; slot[2] = w2;",
     "    slot[0] = w0; slot[1] = w1;"),
    # decimal_array_calls: `append(xs, xs[0])` at a full block reads the source after the old block is freed
    ("rt_append_dec reads its source after growth", "runtime.c",
     "    u64 w0 = src[0], w1 = src[1], w2 = src[2];\n    if (a->len == a->cap) grow_array(a);",
     "    if (a->len == a->cap) grow_array(a);\n    u64 w0 = src[0], w1 = src[1], w2 = src[2];"),
    # every new []decimal(0) and the C case dec_kind: the shared empty block must exist for kind 7
    ("decimal empty array block is missing", "runtime.c",
     "    if (kind == 7) return (u64)&empty_blocks[4][2];\n",
     ""),
    # C case dec_kind: the shared empty block is kind 1, so the first growth is a kind-1 block
    ("decimal empty array block is treated as kind 1", "runtime.c",
     "{6, 32, 0, 0}, {7, 32, 0, 0}",
     "{6, 32, 0, 0}, {1, 32, 0, 0}"),
    # decimal_array_* run tests free every decimal array: the kind is not walked as a descriptor
    ("rt_free walks the decimal kind as a descriptor", "runtime.c",
     "} else if (kind != 1 && kind != 4 && kind != 7) {",
     "} else if (kind != 1 && kind != 4) {"),
    # decimal_array_sizes: the element size of the decimal kind is 8
    ("decimal array elements are 8 bytes", "runtime.c",
     "u64 esize = kind == 4 ? 1 : kind == 7 ? 24 : 8;",
     "u64 esize = kind == 4 ? 1 : 8;"),
    # C case dec_clone: a clone of a decimal array maps the kind to 1 with 8-byte elements
    ("tpl_clone maps the decimal kind to 1 with esize 8", "runtime.c",
     "u64 kind = dk == 2 ? 2 : dk == 6 ? 6 : dk == 4 ? 4 : dk == 7 ? 7 : 1;",
     "u64 kind = dk == 2 ? 2 : dk == 6 ? 6 : dk == 4 ? 4 : 1;"),
]

ENTRY_MUTANTS = [
    ("entry slot 73 targets format", "    slot rt_dec_parse_text", "    slot rt_dec_format_text"),
    ("entry slot 74 targets canonical", "    slot rt_dec_format_text", "    slot rt_dec_canonical_text"),
    ("entry slot 75 targets format", "    slot rt_dec_canonical_text", "    slot rt_dec_format_text"),
    ("entry slot 76 targets parse", "    slot rt_dec_check", "    slot rt_dec_parse_text"),
    ("entry slot 77 targets dscale", "    slot rt_dec_exponent", "    slot rt_dec_dscale"),
    ("entry slot 78 targets exponent", "    slot rt_dec_dscale", "    slot rt_dec_exponent"),
    # decimal_array_*: rt_append_dec is slot 94; another builder there breaks every append
    ("entry slot 94 targets html copy", "    slot rt_append_dec", "    slot rt_tpl_html_copy"),
]


def build_runtime_mutant(work, filename, old, new):
    rt = os.path.join(work, 'rt')
    if os.path.isdir(rt):
        shutil.rmtree(rt)
    os.makedirs(rt)
    for name in ('runtime.c', 'entry.S', 'rt.ld', 'decimal24_core.inc'):
        shutil.copy(os.path.join(RUNTIME, name), rt)
    path = os.path.join(rt, filename)
    text = open(path).read()
    if text.count(old) != 1:
        return None, f"pattern occurs {text.count(old)} times in {filename}"
    open(path, 'w').write(text.replace(old, new))
    try:
        subprocess.run(['gcc', *CFLAGS, '-c', 'runtime.c', '-o', 'runtime.o'],
                       cwd=rt, check=True, capture_output=True)
        subprocess.run(['gcc', '-c', 'entry.S', '-o', 'entry.o'],
                       cwd=rt, check=True, capture_output=True)
        subprocess.run(['ld', '-T', 'rt.ld', '-o', 'runtime.elf', 'entry.o', 'runtime.o'],
                       cwd=rt, check=True, capture_output=True)
        subprocess.run(['objcopy', '-O', 'binary', '-j', '.text', 'runtime.elf',
                        'runtime_text.bin'], cwd=rt, check=True, capture_output=True)
        subprocess.run(['objcopy', '-O', 'binary', '-j', '.data', '--set-section-flags',
                        '.data=alloc,load,contents', 'runtime.elf', 'runtime_data.bin'],
                       cwd=rt, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b'').decode('utf-8', 'replace').strip()
        return None, f"runtime build failed: {err[:180]}"
    return rt, None


# The C text harness is built against the mutant runtime too, so allocator,
# formatter-bound and entry-slot failures have a direct catcher outside the
# language tests.
TEXT_CASES = [
    ('max_output', '4MiB', 0, None),
    ('try_bad', '64KiB', 0, None),
    ('try_reuse', '64KiB', 0, None),
    ('alloc_too_large', '64KiB', 2, 'obx: object too large\n'),
    ('alloc_oom', '64KiB', 2, 'obx: out of memory\n'),
    ('parse10', '64KiB', 0, None),
    ('format10', '64KiB', 0, None),
    ('canonical10', '64KiB', 0, None),
    ('format_truncate', '64KiB', 0, None),
    ('check10', '64KiB', 0, None),
    ('bad_exp_sign', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_exp_coeff', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_exp_flags', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_exp_pad', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_dscale_sign', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_dscale_coeff', '64KiB', 10, 'obo: decimal invalid value\n'),
    ('bad_format', '64KiB', 0, None),
    ('bad_canonical', '64KiB', 0, None),
    ('bad_check', '64KiB', 0, None),
]


def run_text_suite(rt, work, verbose):
    src = os.path.join(HERE, 'rt_dec_text_test.c')
    text = open(src).read()
    text = text.replace('#include "../runtime/runtime.c"',
                        '#include "%s/runtime.c"' % rt)
    mut = os.path.join(work, 'rt_dec_text_test.c')
    open(mut, 'w').write(text)
    obj = os.path.join(work, 'rt_dec_text_test.o')
    exe = os.path.join(work, 'rt_dec_text_test')
    p = subprocess.run(['gcc', *CFLAGS, '-c', mut, '-o', obj],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        if verbose: print('        text harness did not compile:', p.stderr.strip()[:160])
        return False
    p = subprocess.run(['ld', '-T', os.path.join(rt, 'rt.ld'), '-o', exe, obj],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        if verbose: print('        text harness did not link:', p.stderr.strip()[:160])
        return False
    for case, limit, want, stderr in TEXT_CASES:
        env = {k: v for k, v in os.environ.items()
               if k not in ('OBO_HEAP_LIMIT', 'OBO_CHECK', 'OBO_STATS', 'OBX_GC')}
        env['OBO_HEAP_LIMIT'] = limit
        env['OBO_CHECK'] = '1'
        env['OBX_GC'] = 'poison'
        r = subprocess.run([exe, case], capture_output=True, text=True,
                           timeout=120, env=env)
        if r.returncode != want or (stderr is not None and r.stderr != stderr):
            if verbose:
                print(f'        text case {case}: exit {r.returncode} want {want}; '
                      f'stderr {r.stderr.strip()[:120]!r}')
            return False
    return True


# The C array cases of tests/rt_heap_test.c (decimal arrays, kind 7): the empty block's
# kind, the append's source read, the clone's element size, the free walk and truncation.
ARRAY_CASES = ('dec_kind', 'dec_self', 'dec_clone', 'dec_free', 'dec_truncate')


def run_array_suite(rt, work, verbose):
    src = os.path.join(HERE, 'rt_heap_test.c')
    text = open(src).read()
    text = text.replace('#include "../runtime/runtime.c"',
                        '#include "%s/runtime.c"' % rt)
    mut = os.path.join(work, 'rt_heap_test.c')
    open(mut, 'w').write(text)
    obj = os.path.join(work, 'rt_heap_test.o')
    exe = os.path.join(work, 'rt_heap_test')
    p = subprocess.run(['gcc', *CFLAGS, '-c', mut, '-o', obj],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        if verbose: print('        array harness did not compile:', p.stderr.strip()[:160])
        return False
    p = subprocess.run(['ld', '-T', os.path.join(rt, 'rt.ld'), '-o', exe, obj],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        if verbose: print('        array harness did not link:', p.stderr.strip()[:160])
        return False
    for case in ARRAY_CASES:
        env = {k: v for k, v in os.environ.items()
               if k not in ('OBO_HEAP_LIMIT', 'OBO_CHECK', 'OBO_STATS', 'OBX_GC')}
        env['OBO_CHECK'] = '1'
        env['OBX_GC'] = 'poison'
        r = subprocess.run([exe, case], capture_output=True, text=True,
                           timeout=120, env=env)
        if r.returncode != 0:
            if verbose:
                print(f'        array case {case}: exit {r.returncode}; '
                      f'stderr {r.stderr.strip()[:120]!r}')
            return False
    return True


def run_suite(exe, rt, work, verbose, runtime_dir=None, patterns=('decimal',)):
    env = dict(os.environ, OBO=exe, OBO_RT=rt)
    for pattern in patterns:
        r = subprocess.run([sys.executable, RUNNER, '-k', pattern],
                           capture_output=True, text=True, timeout=300, env=env)
        if r.returncode != 0:
            if verbose:
                for line in (r.stdout + r.stderr).splitlines()[:8]:
                    print('        ' + line)
            return False
    rdir = runtime_dir or rt
    return run_text_suite(rdir, work, verbose) and run_array_suite(rdir, work, verbose)


def main():
    verbose = '-v' in sys.argv[1:]
    text = open(SRC).read()
    tmp = tempfile.mkdtemp(prefix='obo-mut-decimal-')
    caught = missed = broken = 0
    try:
        # The unmutated baseline must pass the same judge path; otherwise a
        # broken expectation would make every mutant look caught.
        if not run_suite(os.path.join(FORK, 'obo'), FORK, tmp, verbose,
                         runtime_dir=os.path.join(FORK, 'runtime')):
            print("BROKEN  baseline: the unmutated decimal suite or text cases failed")
            return 1
        print("baseline ok")
        for item in MUTANTS + ARRAY_MUTANTS:
            name, old, new = item[0], item[1], item[2]
            want = item[3] if len(item) > 3 else 1
            n = text.count(old)
            if n != want:
                print(f"BROKEN  {name}: pattern occurs {n} times, want {want}")
                broken += 1
                continue
            src = os.path.join(tmp, 'src.obo')
            exe = os.path.join(tmp, 'obo')
            open(src, 'w').write(text.replace(old, new))
            p = subprocess.run([SEED, src, '-o', exe, '--rt', FORK],
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                print(f"BROKEN  {name}: mutant did not compile: {p.stderr.strip()[:140]}")
                broken += 1
                continue
            if not run_suite(exe, FORK, tmp, verbose, runtime_dir=os.path.join(FORK, 'runtime')):
                caught += 1
                print(f"caught  {name}")
            else:
                missed += 1
                print(f"MISSED  {name}")
        for name, filename, old, new in RUNTIME_MUTANTS + RUNTIME_ARRAY_MUTANTS:
            rt, err = build_runtime_mutant(tmp, filename, old, new)
            if rt is None:
                print(f"BROKEN  {name}: {err}")
                broken += 1
                continue
            src = os.path.join(tmp, 'src.obo')
            exe = os.path.join(tmp, 'obo-rt')
            open(src, 'w').write(text)
            p = subprocess.run([SEED, src, '-o', exe, '--rt', rt],
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                print(f"BROKEN  {name}: compiler did not build: {p.stderr.strip()[:140]}")
                broken += 1
                continue
            pats = ('template_sql', 'template_html') if name.startswith('template:') else ('decimal',)
            if not run_suite(exe, rt, tmp, verbose, patterns=pats):
                caught += 1
                print(f"caught  {name}")
            else:
                missed += 1
                print(f"MISSED  {name}")
        for name, old, new in ENTRY_MUTANTS:
            rt, err = build_runtime_mutant(tmp, 'entry.S', old, new)
            if rt is None:
                print(f"BROKEN  {name}: {err}")
                broken += 1
                continue
            src = os.path.join(tmp, 'src.obo')
            exe = os.path.join(tmp, 'obo-rt')
            open(src, 'w').write(text)
            p = subprocess.run([SEED, src, '-o', exe, '--rt', rt],
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                print(f"BROKEN  {name}: compiler did not build: {p.stderr.strip()[:140]}")
                broken += 1
                continue
            if not run_suite(exe, rt, tmp, verbose):
                caught += 1
                print(f"caught  {name}")
            else:
                missed += 1
                print(f"MISSED  {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = len(MUTANTS) + len(ARRAY_MUTANTS) + len(RUNTIME_MUTANTS) + len(RUNTIME_ARRAY_MUTANTS) + len(ENTRY_MUTANTS)
    print(f"\n{caught} caught, {missed} missed, {broken} broken (of {total})")
    return 1 if missed or broken else 0


if __name__ == '__main__':
    sys.exit(main())
