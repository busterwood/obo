"""Milestone 1, part B: loops, branch merging, match, take, built-ins, new,
comparisons and module exit."""
import re
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:90])
    s = s.replace(old, new)


def fn_body(name):
    m = re.search(rf'^fn {name}\(.*\{{$', s, re.M)
    assert m, name
    nxt = re.search(r'^(fn |// =|type |let |var |const )', s[m.end():], re.M)
    return m.start(), m.end() + nxt.start()


# ------------------------------------------------------------------ loops
rep("""fn loopBody(): Loop {
    let loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks)}
    append(loops, loop)
    block()
    truncate(loops, len(loops) - 1)
    return loop
}""", """// entry: the move states when the loop is entered; every iteration must end,
// and every break or continue happen, with the same states
fn loopBody(entry: []bool, line: int, col: int): Loop {
    let loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks), entry: entry}
    append(loops, loop)
    let term = block()
    truncate(loops, len(loops) - 1)
    if !term {
        let d = stateDiff(entry, snapshot())
        if d != nil {
            errorAt("'" + d.name + "' is moved inside the loop; it must have a value again by the end of each iteration", line, col)
        }
    }
    restore(entry)
    return loop
}""")
rep("""fn whileStat(): bool {
    next()
    let top = pc()""", """fn whileStat(): bool {
    let wline = tline
    let wcol = tcol
    next()
    let entry = snapshot()
    let top = pc()""")
rep("""    pushNarrowings(x.nt)
    let loop = loopBody()""", """    pushNarrowings(x.nt)
    let loop = loopBody(entry, wline, wcol)""")
rep("""    var loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: 0}""",
    """    var loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: 0, entry: new []bool(0)}""")
a, b = fn_body('forStat')
body = s[a:b]
assert body.count("        loop = loopBody()") == 2
body = body.replace("""fn forStat() {
    next()""", """fn forStat() {
    let fline = tline
    let fcol = tcol
    next()""")
body = body.replace("        loop = loopBody()", "        loop = loopBody(snapshot(), fline, fcol)")
body = body.replace("""        if x.typ.kind != "array" { error("cannot iterate over " + tname(x.typ)) }""", """        if x.typ.kind != "array" { error("cannot iterate over " + tname(x.typ)) }
        noTemp(x)
        if isOwned(baseOf(x.typ)) {
            error("iterating over owned elements needs references, which come in milestone 2; index the array instead")
        }""")
s = s[:a] + body + s[b:]
rep("""            let loop = loops[len(loops) - 1]
            runDefers(loop.blocks)""", """            let loop = loops[len(loops) - 1]
            let d = stateDiff(loop.entry, snapshot())
            if d != nil {
                error("'" + d.name + "' is moved inside the loop; it must have a value again before " + tokName(k))
            }
            runDefers(loop.blocks)""")

# ------------------------------------------------------------------- if
rep("""fn ifStat(): bool {
    next()
    let x = loadCond(expression())
    let flinks = falseJump(x)
    let mark = len(narrowStack)
    pushNarrowings(x.nt)
    let t1 = block()
    truncate(narrowStack, mark)""", """fn ifStat(): bool {
    let iline = tline
    let icol = tcol
    next()
    let x = loadCond(expression())
    let flinks = falseJump(x)
    let mark = len(narrowStack)
    let s0 = snapshot()
    pushNarrowings(x.nt)
    let t1 = block()
    let s1 = snapshot()
    restore(s0)
    truncate(narrowStack, mark)""")
rep("""        truncate(narrowStack, mark)
        fixHere1(j)
        if t1 && !t2 {""", """        truncate(narrowStack, mark)
        fixHere1(j)
        let s2 = snapshot()
        if t1 && !t2 {
            restore(s2)
        } else if t2 && !t1 {
            restore(s1)
        } else if !t1 && !t2 {
            sameStates(s1, s2, iline, icol)
        }
        if t1 && !t2 {""")
rep("""    fixHere(flinks)
    if t1 { pushNarrowings(x.nf) }
    return false""", """    fixHere(flinks)
    if !t1 { sameStates(s1, s0, iline, icol) }
    if t1 { pushNarrowings(x.nf) }
    return false""")

# ---------------------------------------------------------------- match
for name in ('intMatch', 'matchStat'):
    a, b = fn_body(name)
    body = s[a:b]
    if name == 'intMatch':
        body = body.replace("""    let at = mem(RBP, -1, slot.offset, 8)
    expect(T_LBRACE, "")""", """    let at = mem(RBP, -1, slot.offset, 8)
    let mline = tline
    let mcol = tcol
    let s0 = snapshot()
    var acc = s0
    var have = false
    expect(T_LBRACE, "")""")
    else:
        body = body.replace("""    let tag = newLocal("", tInt, "var")""", """    let tag = newLocal("", tInt, "var")
    let s0 = snapshot()
    var acc = s0
    var have = false""")
    # every arm starts from s0, and the states of arms that fall through must agree
    assert body.count("            let bt = block()\n            allTerm = allTerm && bt\n") == 1
    body = body.replace("""            let bt = block()
            allTerm = allTerm && bt
""", """            restore(s0)
            let bt = block()
            allTerm = allTerm && bt
            if !bt {
                let st = snapshot()
                if have { sameStates(acc, st, line0(), 0) }
                acc = st
                have = true
            }
""")
    assert body.count("        let bt = block()\n        allTerm = allTerm && bt\n") == 1
    body = body.replace("""        let bt = block()
        allTerm = allTerm && bt
""", """        restore(s0)
        let bt = block()
        allTerm = allTerm && bt
        if !bt {
            let st = snapshot()
            if have { sameStates(acc, st, line0(), 0) }
            acc = st
            have = true
        }
""")
    if name == 'intMatch':
        body = body.replace("""    expect(T_RBRACE, "")
    fixHere(ends)
    return allTerm && hasElse""", """    expect(T_RBRACE, "")
    if !hasElse {
        if have { sameStates(acc, s0, mline, mcol) }
        acc = s0
        have = true
    }
    if have { restore(acc) } else { restore(s0) }
    fixHere(ends)
    return allTerm && hasElse""")
    else:
        body = body.replace("""    fixHere(ends)
    return allTerm""", """    if have { restore(acc) } else { restore(s0) }
    fixHere(ends)
    return allTerm""")
    s = s[:a] + body + s[b:]
rep("""fn intMatch(""", """// the line for errors reported at the current token
fn line0(): int { return 0 }

fn intMatch(""")

# -------------------------------------------------------------- built-ins
rep("""    for n in names { append(universe, newSym("builtin", n, nil)) }""",
    """    for n in names { append(universe, newSym("builtin", n, nil)) }
    append(universe, newSym("builtin", "take", nil))""")
rep("""    if name == "append" || name == "truncate" || name == "substr" {""", """    if name == "take" {
        // takes the value out of an optional variable, field or element, leaving nil
        expect(T_LPAREN, "")
        if sym != T_IDENT { error("take needs a variable, field or element") }
        let x = designator()
        expect(T_RPAREN, "")
        if (x.mode != "mem" && x.mode != "elem") || x.typ.kind != "opt" || !isOwned(x.typ) {
            error("take needs an optional variable, field or element holding an owned value")
        }
        if x.readonly { error("take cannot change '" + describe(x) + "': it is immutable") }
        resolve(x)
        let r = alloc()
        opMem(0x8B, r, memOf(x), true, false)
        opMem(0xC7, 0, memOf(x), true, false)
        emit32(0)
        releaseItem(x)
        return makeReg(x.typ, r)
    }
    if name == "append" || name == "truncate" || name == "substr" {""")
rep("""        if a.typ.kind != want { error(name + " needs " + article + " " + want + ", not " + tname(a.typ)) }
        a = materialise(a)""", """        if a.typ.kind != want { error(name + " needs " + article + " " + want + ", not " + tname(a.typ)) }
        noTemp(a)
        a = materialise(a)""")
rep("""            checkAssign(baseOf(a.typ), b, "appended value")""", """            checkAssign(baseOf(a.typ), b, "appended value")
            consume(b)""")
rep("""    if a.typ.kind != "array" { error("len needs a string or array, not " + tname(a.typ)) }""",
    """    if a.typ.kind != "array" { error("len needs a string or array, not " + tname(a.typ)) }
    noTemp(a)""")
rep("""            error("writeFile needs a []byte, not " + tname(d.typ))
        }""", """            error("writeFile needs a []byte, not " + tname(d.typ))
        }
        noTemp(d)""")
rep("""        if a.typ.kind != "array" || baseOf(a.typ).kind != "byte" {
            error("string() needs an int or a []byte, not " + tname(a.typ))
        }""", """        if a.typ.kind != "array" || baseOf(a.typ).kind != "byte" {
            error("string() needs an int or a []byte, not " + tname(a.typ))
        }
        noTemp(a)""")

# ------------------------------------------------------------------ new
rep("""        checkAssign(f.typ, v, "field '" + fname + "'")""", """        checkAssign(f.typ, v, "field '" + fname + "'")
        consume(v)""")
rep("""        var kind = 1
        if b.kind == "byte" {
            kind = 4
        } else if isRef(b) {
            kind = 2
        }""", """        var kind = 1
        if b.kind == "byte" {
            kind = 4
        } else if b.kind == "string" {
            kind = 6                                  // strings are not owned in M1
        } else if isRef(b) {
            kind = 2
        }""")

# ---------------------------------------------------------- comparisons
rep("""    var ysym: ?Sym = nil
    if y.mode == "mem" { ysym = y.sym }""", """    var ysym: ?Sym = nil
    if y.mode == "mem" { ysym = y.sym }
    noTemp(x)
    noTemp(y)""")

# ------------------------------------------------------------ module end
rep("""    fixHere1(initLink)
    emit(0xE8)
    emit32(m.addr - (pc() + 4))
    emit(0x31)
    emit(0xFF)
    callAbs(RT_TEXT + 8 * RT_EXIT)""", """    fixHere1(initLink)
    emit(0xE8)
    emit32(m.addr - (pc() + 4))
    depth = 0
    for g in globals {
        if (g.kind == "var" || g.kind == "let") && isOwned(symType(g)) {
            let x = newItem("mem", symType(g))
            x.disp = g.offset
            rtCall1(RT_FREE, x, tVoid)
        }
    }
    callAbs(RT_TEXT + 8 * RT_FINISH)""")
open(path, 'w').write(s)
print("part B applied")
