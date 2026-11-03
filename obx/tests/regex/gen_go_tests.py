#!/usr/bin/env python3
"""Generates the regex tests in obx/lib/tests/run from Go's own regexp tests.

    python3 obx/tests/regex/gen_go_tests.py [--goroot DIR]

Go's test tables (regexp/syntax: parse_test.go, simplify_test.go,
prog_test.go; regexp: find_test.go, all_test.go, onepass_test.go) are run
through the real Go regexp: the packages are copied to a scratch module
with gen_syntax_test.go and gen_regexp_test.go added, and `go test` writes
what Go produces. The results become obx test files whose expected values
are Go's:

    regex_parse.obx          parse dumps and errors, String(), Simplify(), programs
    regex_find.obx           find_test.go: every API form, each matcher, leftmost-first and longest
    regex_api.obx            all_test.go and onepass_test.go: compile errors, replace,
                             quote, literal prefix, groups, split, one-pass, minimum length
    regex_re2search/         re2-search.txt (testdata of Go's regexp), with its runner

Needs go. The generated files are committed, so the tests run without it.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(ROOT, 'lib', 'tests', 'run')


def goroot(arg):
    if arg:
        return arg
    r = subprocess.run(['go', 'env', 'GOROOT'], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def run_gen(gr, tmp, pkg, genfile):
    src = os.path.join(gr, 'src', 'regexp', *pkg.split('/')[1:])
    dst = os.path.join(tmp, *pkg.split('/'))
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        # Not exec_test.go (it needs Go's internal packages) nor exec2_test.go (it uses exec_test.go).
        if f.endswith(".go") and f not in ("exec_test.go", "exec2_test.go"):
            shutil.copy(os.path.join(src, f), dst)
    shutil.copy(os.path.join(HERE, genfile), dst)
    out = os.path.join(tmp, pkg.replace('/', '_') + '.txt')
    env = dict(os.environ, OBX_GEN_OUT=out, GOFLAGS='-mod=mod', GOTOOLCHAIN='local')
    r = subprocess.run(['go', 'test', '-count=1', '-run', '^TestGenObx$', './' + pkg], cwd=tmp, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('go test %s failed:\n%s%s' % (pkg, r.stdout, r.stderr))
    rows = []
    for line in open(out, encoding='latin-1').read().split('\n'):
        if line:
            rows.append(line.split('\t'))
    return rows


def unhex(h):
    return bytes.fromhex(h)


def lit(b):
    """An obx string literal for the bytes b."""
    if isinstance(b, str):
        b = b.encode('latin-1')
    out = ['"']
    for c in b:
        if c == 0x22:
            out.append('\\"')
        elif c == 0x5c:
            out.append('\\\\')
        elif 0x20 <= c < 0x7f:
            out.append(chr(c))
        elif c == 0x0a:
            out.append('\\n')
        elif c == 0x09:
            out.append('\\t')
        else:
            out.append('\\x%02x' % c)
    out.append('"')
    return ''.join(out)


def compress(b):
    """Long strings (Go's limit tests) with runs of a repeated unit written
    \\x01COUNT\\x02UNIT\\x03; expand() in the test undoes it."""
    if isinstance(b, str):
        b = b.encode('latin-1')
    if len(b) < 500:
        return b
    assert not any(c in b for c in b'\x01\x02\x03'), b[:40]
    out = bytearray()
    i = 0
    while i < len(b):
        best, bl, bk = 0, 0, 0
        for L in range(1, 13):
            unit = b[i:i + L]
            if len(unit) < L:
                break
            k = 1
            while b[i + k * L:i + (k + 1) * L] == unit:
                k += 1
            if k >= 10 and L * k > best:
                best, bl, bk = L * k, L, k
        if best >= 40:
            out += b'\x01' + str(bk).encode() + b'\x02' + b[i:i + bl] + b'\x03'
            i += best
        else:
            out.append(b[i])
            i += 1
    return bytes(out)


STR_TABLES = []


def strs_table(name, items, packed=True):
    """A table of strings. Packed, the constant is NAME_Z and main() starts
    with `let NAME = expandAll(NAME_Z)`."""
    if packed:
        STR_TABLES.append(name)
        lines = ['const %s_Z: []str = [' % name]
        items = [compress(it) for it in items]
    else:
        lines = ['const %s: []str = [' % name]
    for it in items:
        lines.append('    %s,' % lit(it))
    lines.append(']')
    return '\n'.join(lines)


EXPAND = r'''
// Undoes the generator's packing of long strings: \x01COUNT\x02UNIT\x03 is
// UNIT repeated COUNT times.
fn expand(s: str): str {
    if indexOf(s, "\x01", 0) < 0 { return s }
    var b = newStrBuf()
    var i = 0
    while i < len(s) {
        if s[i] == byte(1) {
            let m = indexOf(s, "\x02", i)
            let e = indexOf(s, "\x03", m)
            b.add(repeat(s[m + 1..e], parseInt(s[i + 1..m]).unwrap()))
            i = e + 1
        } else {
            b.addByte(s[i])
            i += 1
        }
    }
    return b.toStr()
}

fn expandAll(a: &[str]): []str {
    var out: []str = []
    for s in a { append(mut &out, expand(s)) }
    return out
}
'''


def finish(parts):
    """The file, with the packed tables expanded at the start of main."""
    text = '\n'.join(parts)
    lets = ''.join('    let %s = expandAll(%s_Z)\n' % (n, n) for n in STR_TABLES)
    del STR_TABLES[:]
    assert text.count('fn main() {\n') == 1
    return text.replace('fn main() {\n', 'fn main() {\n' + lets) + EXPAND


def ints_table(name, items):
    lines = ['const %s: []int = [' % name]
    for i in range(0, len(items), 16):
        lines.append('    ' + ', '.join(str(x) for x in items[i:i + 16]) + ',')
    lines.append(']')
    return '\n'.join(lines)


def bools_table(name, items):
    return ints_table(name, [1 if x == 'true' else 0 for x in items])


HEADER = '''// Generated by obx/tests/regex/gen_go_tests.py from Go's regexp tests
// (%s; BSD-3-Clause, Copyright 2011 The Go Authors): the expected values
// are what Go %s produces. Do not edit.
'''


def gen_parse(rows, gover):
    kinds = {'parse': [], 'string': [], 'simplify': [], 'compile': []}
    for r in rows:
        kinds[r[0]].append((unhex(r[1]), int(r[2]), unhex(r[3])))
    parts = [HEADER % ('regexp/syntax parse_test.go, simplify_test.go, prog_test.go', gover)]
    n = {k: len(v) for k, v in kinds.items()}
    parts.append('// expect: parse %d string %d simplify %d compile %d' % (n['parse'], n['string'], n['simplify'], n['compile']))
    parts.append('''
import "regex_syntax" as syn
''')
    for k, short in (('parse', 'P'), ('string', 'S'), ('simplify', 'Y'), ('compile', 'C')):
        parts.append(strs_table(short + '_PAT', [x[0] for x in kinds[k]]))
        parts.append(ints_table(short + '_FLAGS', [x[1] for x in kinds[k]]))
        parts.append(strs_table(short + '_WANT', [x[2] for x in kinds[k]]))
    parts.append(r'''
fn parseOut(pat: str, flags: int): str {
    match syn.parse(pat, flags) {
        Ok(t) { return t.dump(t.root) }
        Err(e) { return "!" + e.code.toStr() + "\x00" + e.expr }
    }
}

fn stringOut(pat: str, flags: int): str {
    match syn.parse(pat, flags) {
        Ok(t) { return t.toPattern(t.root) }
        Err(e) { return "!" + e.message() }
    }
}

fn simplifyOut(pat: str, flags: int): str {
    match syn.parse(pat, flags) {
        Ok(t) {
            var tt = t
            let r = tt.simplify(tt.root)
            return tt.toPattern(r)
        }
        Err(e) { return "!" + e.message() }
    }
}

fn compileOut(pat: str, flags: int): str {
    match syn.parse(pat, flags) {
        Ok(t) {
            match syn.compile(&t, t.root, 100000000) {
                Ok(p) { return p.dump() }
                Err(e) { return "!" + e.message() }
            }
        }
        Err(e) { return "!" + e.message() }
    }
}

fn check(kind: str, pats: &[str], flags: &[int], want: &[str], f: &fn(str, int): str, fails: mut &int): int {
    for i in 0..len(pats) {
        let got = f(pats[i], flags[i])
        if got != want[i] {
            fails += 1
            println("FAIL " + kind + " " + pats[i] + " flags " + flags[i].toStr() + "\n  got  " + got + "\n  want " + want[i])
        }
    }
    return len(pats)
}

fn main() {
    var fails = 0
    let a = check("parse", &P_PAT, P_FLAGS, &P_WANT, parseOut, mut &fails)
    let b = check("string", &S_PAT, S_FLAGS, &S_WANT, stringOut, mut &fails)
    let c = check("simplify", &Y_PAT, Y_FLAGS, &Y_WANT, simplifyOut, mut &fails)
    let d = check("compile", &C_PAT, C_FLAGS, &C_WANT, compileOut, mut &fails)
    if fails > 0 {
        println(fails.toStr() + " failures")
        exit(1)
    }
    println("parse " + a.toStr() + " string " + b.toStr() + " simplify " + c.toStr() + " compile " + d.toStr())
}
''')
    return finish(parts)


def gen_find(rows, gover):
    finds = [r for r in rows if r[0] == 'find']
    parts = [HEADER % ('regexp find_test.go', gover)]
    parts.append('// expect: find %d cases ok' % len(finds))
    parts.append('''
import "regex"
''')
    parts.append(strs_table('PAT', [unhex(r[1]) for r in finds]))
    parts.append(strs_table('TEXT', [unhex(r[2]) for r in finds]))
    parts.append(strs_table('ALL', [r[3] for r in finds]))
    parts.append(strs_table('ALL_LONGEST', [r[4] for r in finds]))
    parts.append(bools_table('IS_MATCH', [r[5] for r in finds]))
    parts.append(bools_table('ONEPASS', [r[6] for r in finds]))
    parts.append(FIND_MAIN)
    return finish(parts)


FIND_MAIN = r'''
fn encode(a: &[]int): str {
    var s = ""
    for i in 0..len(a) {
        if i > 0 { s = s + "," }
        s = s + a[i].toStr()
    }
    return s
}

fn encodeAll(all: &[][]int): str {
    if len(all) == 0 { return "nil" }
    var s = ""
    for i in 0..len(all) {
        if i > 0 { s = s + ";" }
        s = s + encode(&all[i])
    }
    return s
}

// The expected matches: their slot lists.
fn decodeAll(s: str): [][]int {
    var out: [][]int = []
    if s == "nil" { return out }
    for part in split(s, ';') {
        var a: []int = []
        for x in split(part, ',') { append(mut &a, parseInt(x).unwrap()) }
        append(mut &out, a)
    }
    return out
}

fn fail(fails: mut &int, what: str, re: &regex.Regex, text: str, got: str, want: str) {
    fails += 1
    if fails <= 30 {
        println("FAIL " + what + " /" + re.pattern() + "/ " + text + "\n  got  " + got + "\n  want " + want)
    }
}

// Every API form against the expected slot lists.
fn checkAll(re: &regex.Regex, text: str, wantStr: str, isMatch: bool, fails: mut &int) {
    let want = decodeAll(wantStr)
    let all = re.allSubmatchIndex(text, -1)
    if encodeAll(&all) != wantStr { fail(fails, "allSubmatchIndex", re, text, encodeAll(&all), wantStr) }
    if re.isMatch(text) != isMatch { fail(fails, "isMatch", re, text, re.isMatch(text).toStr(), isMatch.toStr()) }
    let first = re.submatchIndex(text)
    let wantFirst = if len(want) > 0 { encode(&want[0]) } else { "" }
    if encode(&first) != wantFirst { fail(fails, "submatchIndex", re, text, encode(&first), wantFirst) }
    // find, findIndex, findAll, findAllIndex, findN
    match re.find(text) {
        Some(m) {
            if len(want) == 0 || m.start != want[0][0] || m.end != want[0][1] || m.text != text[want[0][0]..want[0][1]] {
                fail(fails, "find", re, text, m.start.toStr() + "-" + m.end.toStr(), wantFirst)
            }
        }
        None { if len(want) > 0 { fail(fails, "find", re, text, "None", wantFirst) } }
    }
    match re.findIndex(text) {
        Some(sp) { if len(want) == 0 || sp.start != want[0][0] || sp.end != want[0][1] { fail(fails, "findIndex", re, text, "", wantFirst) } }
        None { if len(want) > 0 { fail(fails, "findIndex", re, text, "None", wantFirst) } }
    }
    let ms = re.findAll(text)
    let sps = re.findAllIndex(text)
    if len(ms) != len(want) || len(sps) != len(want) {
        fail(fails, "findAll count", re, text, len(ms).toStr(), len(want).toStr())
    } else {
        for i in 0..len(ms) {
            if ms[i].start != want[i][0] || ms[i].end != want[i][1] || ms[i].text != text[want[i][0]..want[i][1]] ||
                sps[i].start != want[i][0] || sps[i].end != want[i][1] {
                fail(fails, "findAll", re, text, ms[i].start.toStr(), want[i][0].toStr())
            }
        }
    }
    for n in 0..3 {
        let k = if n < len(want) { n } else { len(want) }
        if len(re.findN(text, n)) != k { fail(fails, "findN", re, text, len(re.findN(text, n)).toStr(), k.toStr()) }
        if len(re.allSubmatchIndex(text, n)) != k { fail(fails, "allSubmatchIndex n", re, text, "", k.toStr()) }
    }
    // captures and capturesAll
    let cs = re.capturesAll(text)
    if len(cs) != len(want) {
        fail(fails, "capturesAll", re, text, len(cs).toStr(), len(want).toStr())
    } else {
        for i in 0..len(cs) {
            if cs[i].count() != re.numGroups() + 1 { fail(fails, "captures count", re, text, cs[i].count().toStr(), "") }
            for g in 0..cs[i].count() {
                let a = want[i][2 * g]
                let b = want[i][2 * g + 1]
                match cs[i].get(g) {
                    Some(m) { if m.start != a || m.end != b || m.text != text[a..b] { fail(fails, "captures get", re, text, m.start.toStr(), a.toStr()) } }
                    None { if a >= 0 { fail(fails, "captures get", re, text, "None", a.toStr()) } }
                }
            }
        }
    }
    match re.captures(text) {
        Some(c) { if len(want) == 0 || encode(&c.indexes()) != encode(&want[0]) { fail(fails, "captures", re, text, encode(&c.indexes()), wantFirst) } }
        None { if len(want) > 0 { fail(fails, "captures", re, text, "None", wantFirst) } }
    }
}

const MATCHERS: []str = ["", "pike", "backtrack"]

fn main() {
    var fails = 0
    var onepass = 0
    for i in 0..len(PAT) {
        for longest in [false, true] {
            let want = if longest { ALL_LONGEST[i] } else { ALL[i] }
            for m in MATCHERS {
                let re = regex.compileForcing(PAT[i], longest, m).unwrap()
                if m == "" && !longest {
                    let isOnePass = re.matcherFor(0) == "onepass"
                    if isOnePass != (ONEPASS[i] == 1) { fail(mut &fails, "onepass", &re, TEXT[i], isOnePass.toStr(), "") }
                    if isOnePass { onepass += 1 }
                }
                checkAll(&re, TEXT[i], want, IS_MATCH[i] == 1, mut &fails)
            }
        }
    }
    if fails > 0 {
        println(fails.toStr() + " failures")
        exit(1)
    }
    println("find " + len(PAT).toStr() + " cases ok")
}
'''


def gen_api(rows, gover):
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    parts = [HEADER % ('regexp all_test.go, onepass_test.go', gover)]
    counts = ' '.join('%s %d' % (k, len(by.get(k, []))) for k in
                      ('good', 'bad', 'replace', 'replacefunc', 'meta', 'subexp', 'split', 'onepass', 'onepassrun', 'mininputlen'))
    parts.append('// expect: ' + counts)
    parts.append('''
import "regex"
import "regex_syntax" as syn
''')
    g = by['good']
    parts.append(strs_table('GOOD', [unhex(r[1]) for r in g]))
    b = by['bad']
    parts.append(strs_table('BAD', [unhex(r[1]) for r in b]))
    parts.append(strs_table('BAD_MSG', [unhex(r[2]) for r in b]))
    rp = by['replace']
    parts.append(strs_table('R_PAT', [unhex(r[1]) for r in rp]))
    parts.append(strs_table('R_REPL', [unhex(r[2]) for r in rp]))
    parts.append(strs_table('R_IN', [unhex(r[3]) for r in rp]))
    parts.append(strs_table('R_OUT', [unhex(r[4]) for r in rp]))
    parts.append(strs_table('R_OUT_LITERAL', [unhex(r[5]) for r in rp]))
    rf = by['replacefunc']
    parts.append(strs_table('RF_PAT', [unhex(r[1]) for r in rf]))
    parts.append(strs_table('RF_IN', [unhex(r[2]) for r in rf]))
    parts.append(strs_table('RF_OUT', [unhex(r[3]) for r in rf]))
    me = by['meta']
    parts.append(strs_table('M_PAT', [unhex(r[1]) for r in me]))
    parts.append(strs_table('M_QUOTE', [unhex(r[2]) for r in me]))
    parts.append(strs_table('M_PREFIX', [unhex(r[3]) for r in me]))
    parts.append(bools_table('M_COMPLETE', [r[4] for r in me]))
    se = by['subexp']
    parts.append(strs_table('SE_PAT', [unhex(r[1]) for r in se]))
    parts.append(ints_table('SE_NUM', [int(r[2]) for r in se]))
    def items(field):
        """A string list as gen_regexp_test.go writes it: nil or N[hex,...]."""
        if field == 'nil':
            return []
        n, rest = field.split('[', 1)
        return [unhex(x) for x in rest[:-1].split(',')] if int(n) > 0 else []
    # names: "\x00"-joined
    def names(field):
        return b'\x00'.join(items(field))
    parts.append(strs_table('SE_NAMES', [names(r[3]) for r in se]))
    def idx(field):
        out = []
        for kv in field.split(','):
            k, v = kv.split('=')
            out.append(unhex(k) + b'=' + v.encode())
        return b'\x00'.join(out)
    parts.append(strs_table('SE_INDEX', [idx(r[4]) for r in se]))
    sp = by['split']
    parts.append(strs_table('SP_TEXT', [unhex(r[1]) for r in sp]))
    parts.append(strs_table('SP_PAT', [unhex(r[2]) for r in sp]))
    parts.append(ints_table('SP_N', [int(r[3]) for r in sp]))
    def pieces(field):
        if not items(field):
            return b'(none)'
        return b'|'.join(b'[' + x + b']' for x in items(field))
    parts.append(strs_table('SP_OUT', [pieces(r[4]) for r in sp]))
    op = by['onepass']
    parts.append(strs_table('OP_PAT', [unhex(r[1]) for r in op]))
    parts.append(bools_table('OP_IS', [r[2] for r in op]))
    opr = by['onepassrun']
    parts.append(strs_table('OPR_PAT', [unhex(r[1]) for r in opr]))
    parts.append(strs_table('OPR_TEXT', [unhex(r[2]) for r in opr]))
    parts.append(bools_table('OPR_IS', [r[3] for r in opr]))
    parts.append(bools_table('OPR_MATCH', [r[4] for r in opr]))
    ml = by['mininputlen']
    parts.append(strs_table('ML_PAT', [unhex(r[1]) for r in ml]))
    parts.append(ints_table('ML_LEN', [int(r[2]) for r in ml]))
    parts.append(API_MAIN % counts)
    return finish(parts)


API_MAIN = r'''
fn fail(fails: mut &int, what: str, got: str, want: str) {
    fails += 1
    println("FAIL " + what + "\n  got  " + got + "\n  want " + want)
}

fn main() {
    var fails = 0
    for p in GOOD {
        match regex.compile(p) {
            Ok(re) { if re.pattern() != p { fail(mut &fails, "pattern " + p, re.pattern(), p) } }
            Err(e) { fail(mut &fails, "good " + p, e.toStr(), "ok") }
        }
    }
    for i in 0..len(BAD) {
        match regex.compile(BAD[i]) {
            Ok(re) { fail(mut &fails, "bad " + BAD[i], "ok", BAD_MSG[i]) }
            Err(e) { if e.message() != BAD_MSG[i] { fail(mut &fails, "bad " + BAD[i], e.message(), BAD_MSG[i]) } }
        }
    }
    for i in 0..len(R_PAT) {
        let re = regex.mustCompile(R_PAT[i])
        let got = re.replaceAll(R_IN[i], R_REPL[i])
        if got != R_OUT[i] { fail(mut &fails, "replaceAll /" + R_PAT[i] + "/ " + R_REPL[i] + " " + R_IN[i], got, R_OUT[i]) }
        let lit = re.replaceAllLiteral(R_IN[i], R_REPL[i])
        if lit != R_OUT_LITERAL[i] { fail(mut &fails, "replaceAllLiteral /" + R_PAT[i] + "/ " + R_REPL[i] + " " + R_IN[i], lit, R_OUT_LITERAL[i]) }
    }
    for i in 0..len(RF_PAT) {
        let re = regex.mustCompile(RF_PAT[i])
        let got = re.replaceAllFunc(RF_IN[i], fn(s: str): str { return "x" + s + "y" })
        if got != RF_OUT[i] { fail(mut &fails, "replaceAllFunc /" + RF_PAT[i] + "/", got, RF_OUT[i]) }
    }
    for i in 0..len(M_PAT) {
        let q = regex.quote(M_PAT[i])
        if q != M_QUOTE[i] { fail(mut &fails, "quote " + M_PAT[i], q, M_QUOTE[i]) }
        let re = regex.mustCompile(M_PAT[i])
        let pre = re.literalPrefix()
        if pre.text != M_PREFIX[i] || pre.complete != (M_COMPLETE[i] == 1) {
            fail(mut &fails, "literalPrefix " + M_PAT[i], pre.text + " " + pre.complete.toStr(), M_PREFIX[i])
        }
        // A quoted pattern matches the text itself.
        let qre = regex.mustCompile(q)
        match qre.find(M_PAT[i]) {
            Some(m) { if m.start != 0 || m.end != len(M_PAT[i]) { fail(mut &fails, "quote match " + M_PAT[i], m.text, M_PAT[i]) } }
            None { fail(mut &fails, "quote match " + M_PAT[i], "None", M_PAT[i]) }
        }
    }
    for i in 0..len(SE_PAT) {
        let re = regex.mustCompile(SE_PAT[i])
        if re.numGroups() != SE_NUM[i] { fail(mut &fails, "numGroups " + SE_PAT[i], re.numGroups().toStr(), SE_NUM[i].toStr()) }
        var names = ""
        let gn = re.groupNames()
        for k in 0..len(gn) {
            if k > 0 { names = names + "\x00" }
            names = names + gn[k]
        }
        if names != SE_NAMES[i] { fail(mut &fails, "groupNames " + SE_PAT[i], names, SE_NAMES[i]) }
        for kv in split(SE_INDEX[i], '\x00') {
            let eq = indexOf(kv, "=", 0)
            let name = kv[0..eq]
            let want = parseInt(kv[eq + 1..]).unwrap()
            if re.groupIndex(name) != want { fail(mut &fails, "groupIndex " + SE_PAT[i] + " " + name, re.groupIndex(name).toStr(), want.toStr()) }
        }
    }
    for i in 0..len(SP_PAT) {
        let re = regex.mustCompile(SP_PAT[i])
        let ps = re.splitN(SP_TEXT[i], SP_N[i])
        var got = ""
        for k in 0..len(ps) {
            if k > 0 { got = got + "|" }
            got = got + "[" + ps[k] + "]"
        }
        if len(ps) == 0 { got = "(none)" }
        if got != SP_OUT[i] { fail(mut &fails, "split " + SP_TEXT[i] + " /" + SP_PAT[i] + "/ " + SP_N[i].toStr(), got, SP_OUT[i]) }
        if SP_N[i] == -1 {
            let ps2 = re.split(SP_TEXT[i])
            if len(ps2) != len(ps) { fail(mut &fails, "split -1 " + SP_PAT[i], len(ps2).toStr(), len(ps).toStr()) }
        }
    }
    for i in 0..len(OP_PAT) {
        let re = regex.mustCompile(OP_PAT[i])
        let is = re.matcherFor(0) == "onepass"
        if is != (OP_IS[i] == 1) { fail(mut &fails, "onepass " + OP_PAT[i], is.toStr(), (OP_IS[i] == 1).toStr()) }
    }
    for i in 0..len(OPR_PAT) {
        let re = regex.mustCompile(OPR_PAT[i])
        let is = re.matcherFor(0) == "onepass"
        if is != (OPR_IS[i] == 1) { fail(mut &fails, "onepass run " + OPR_PAT[i], is.toStr(), "") }
        if re.isMatch(OPR_TEXT[i]) != (OPR_MATCH[i] == 1) { fail(mut &fails, "onepass match " + OPR_PAT[i], "", OPR_TEXT[i]) }
    }
    for i in 0..len(ML_PAT) {
        let t = syn.parse(ML_PAT[i], syn.PERL).unwrap()
        let n = t.minInputLen(t.root)
        if n != ML_LEN[i] { fail(mut &fails, "minInputLen " + ML_PAT[i], n.toStr(), ML_LEN[i].toStr()) }
    }
    if fails > 0 {
        println(fails.toStr() + " failures")
        exit(1)
    }
    println("%s")
}
'''


GO_QUOTE = re.compile(r'\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[0-7]{3}|[abfnrtv\\\'"])')


def go_unquote(s):
    """Go's strconv.Unquote of a double-quoted string, to bytes."""
    assert s[0] == '"' and s[-1] == '"', s
    body = s[1:-1]
    out = bytearray()
    i = 0
    simple = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11, '\\': 92, "'": 39, '"': 34}
    while i < len(body):
        c = body[i]
        if c != '\\':
            out += c.encode('utf-8', 'surrogateescape')
            i += 1
            continue
        e = body[i + 1]
        if e in simple:
            out.append(simple[e])
            i += 2
        elif e == 'x':
            out.append(int(body[i + 2:i + 4], 16))
            i += 4
        elif e == 'u':
            out += chr(int(body[i + 2:i + 6], 16)).encode('utf-8', 'surrogatepass')
            i += 6
        elif e == 'U':
            out += chr(int(body[i + 2:i + 10], 16)).encode('utf-8')
            i += 10
        elif e in '01234567':
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            raise ValueError('bad escape in ' + s)
    return bytes(out)


def re2_lines(path):
    """The lines of a re2-search file with each quoted string as h:HEX
    (the obx runner decodes them), comments and headers dropped."""
    import bz2
    opener = bz2.open if path.endswith('.bz2') else open
    out = []
    with opener(path, 'rt', encoding='utf-8', errors='surrogateescape', newline='\n') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.endswith('\r'):   # as Go's bufio.ScanLines
                line = line[:-1]
            if not line or line[0] == '#' or 'A' <= line[0] <= 'Z':
                continue
            if line[0] == '"':
                line = 'h:' + go_unquote(line).hex()
            out.append(line)
    return out


def gen_re2(gr, gover):
    path = os.path.join(gr, 'src', 'regexp', 'testdata', 're2-search.txt')
    lines = re2_lines(path)
    d = os.path.join(OUT, 'regex_re2search')
    os.makedirs(d, exist_ok=True)
    data = [HEADER % ('regexp testdata/re2-search.txt', gover)]
    data.append('// The lines of re2-search.txt, each quoted string as h:HEX.')
    data.append(strs_table('LINES', lines, packed=False).replace('const LINES', 'pub const LINES'))
    open(os.path.join(d, 'cases.obx'), 'w').write('\n'.join(data) + '\n')
    return len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--goroot')
    a = ap.parse_args()
    gr = goroot(a.goroot)
    gover = subprocess.run(['go', 'env', 'GOVERSION'], capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        lang = '.'.join(gover[2:].split('.')[:2])
        open(os.path.join(tmp, 'go.mod'), 'w').write('module obxgen\n\ngo %s\n' % lang)
        srows = run_gen(gr, tmp, 'regexp/syntax', 'gen_syntax_test.go')
        rrows = run_gen(gr, tmp, 'regexp', 'gen_regexp_test.go')
    open(os.path.join(OUT, 'regex_parse.obx'), 'w').write(gen_parse(srows, gover))
    open(os.path.join(OUT, 'regex_find.obx'), 'w').write(gen_find(rrows, gover))
    open(os.path.join(OUT, 'regex_api.obx'), 'w').write(gen_api(rrows, gover))
    n = gen_re2(gr, gover)
    print('regex tests from %s: %d syntax rows, %d regexp rows, %d re2-search lines' % (gover, len(srows), len(rrows), n))


if __name__ == '__main__':
    main()
