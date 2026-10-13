"""Milestone 1, part A: ownership tracking in obo-on-obx.obx (records, the move-tracking
core, designators, assignment, calls, declarations, return, && and ||)."""
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:90])
    s = s.replace(old, new)


rep("""    params: []Type
    ret: Type
    line: int
}""", """    params: []Type
    ret: Type
    line: int
    owned: bool               // a local holding an owned value
    moved: bool               // its value has been moved away
    deferUse: bool            // a defer reads it, so it may not be moved
}""")
rep("""    readonly: bool
    width, scale, line: int
}""", """    readonly: bool
    width, scale, line: int
    root: ?Sym                // the variable a designator starts from
    deref: bool               // the designator goes through a field or element
    movedRef: bool            // a moved local named as an assignment target
}""")
rep("""    defers: []int
    nmark: int
}""", """    defers: []int
    nmark: int
    omark: int                // ownedLocals before this block
}""")
rep("""    cont: []int
    blocks: int
}""", """    cont: []int
    blocks: int
    entry: []bool             // move states when the loop was entered
}""")
rep("""const RT_SET_OUTPUT = 21""", """const RT_SET_OUTPUT = 21
const RT_FREE = 22
const RT_FINISH = 23""")

rep("""    append(blocks, new Block{syms: new []Sym(0), defers: new []int(0), nmark: len(narrowStack)})""",
    """    append(blocks, new Block{syms: new []Sym(0), defers: new []int(0), nmark: len(narrowStack),
                             omark: len(ownedLocals)})""")
rep("""fn closeBlock(terminated: bool) {
    let blk = blocks[len(blocks) - 1]
    truncate(blocks, len(blocks) - 1)
    if !terminated { callDefers(blk) }
    truncate(narrowStack, blk.nmark)
}

fn runDefers(downTo: int) {
    var i = len(blocks) - 1
    while i >= downTo {
        callDefers(blocks[i])
        i -= 1
    }
}""", """fn closeBlock(terminated: bool) {
    let blk = blocks[len(blocks) - 1]
    truncate(blocks, len(blocks) - 1)
    if !terminated {
        callDefers(blk)
        freeOwned(blk.omark, len(ownedLocals))
    }
    truncate(ownedLocals, blk.omark)
    truncate(narrowStack, blk.nmark)
}

// leaving blocks early (return, break, continue): each block's defers run,
// then its live owned locals are freed, innermost first
fn runDefers(downTo: int) {
    var top = len(ownedLocals)
    var i = len(blocks) - 1
    while i >= downTo {
        callDefers(blocks[i])
        freeOwned(blocks[i].omark, top)
        top = blocks[i].omark
        i -= 1
    }
}""")
rep("""fn openBlock() {""", """// ============================================================== ownership

var ownedLocals = new []Sym(0)
var condDepth = 0                 // inside the right operand of && or ||
var pinned: ?Sym = nil            // a local being assigned through
var userCalls = 0                 // calls to obx functions compiled so far

fn isOwned(t: Type): bool {
    if t.kind == "record" || t.kind == "closed" || t.kind == "array" { return true }
    if t.kind == "opt" { return isOwned(baseOf(t)) }
    return false
}

fn addOwnedLocal(s: Sym) {
    s.owned = true
    append(ownedLocals, s)
}

// frees the live owned locals ownedLocals[from..to), newest first
fn freeOwned(from: int, to: int) {
    var i = to - 1
    while i >= from {
        let s = ownedLocals[i]
        if !s.moved {
            let x = newItem("mem", symType(s))
            x.base = RBP
            x.disp = s.offset
            rtCall1(RT_FREE, x, tVoid)
        }
        i -= 1
    }
}

fn snapshot(): []bool {
    let out = new []bool(0)
    for s in ownedLocals { append(out, s.moved) }
    return out
}

fn restore(st: []bool) {
    for i in 0..len(st) { ownedLocals[i].moved = st[i] }
}

// the first owned local whose move state differs between a and b, or nil
fn stateDiff(a: []bool, b: []bool): ?Sym {
    for i in 0..len(a) {
        if i < len(b) && a[i] != b[i] { return ownedLocals[i] }
    }
    return nil
}

fn sameStates(a: []bool, b: []bool, line: int, col: int) {
    let d = stateDiff(a, b)
    if d != nil {
        errorAt("'" + d.name + "' is moved on one path but not another; move it on every path or none", line, col)
    }
}

// Takes ownership of x's value, which is about to be stored in a variable,
// field, element or parameter, or returned.
fn consume(x: Item) {
    if !isOwned(x.typ) || x.mode == "reg" || x.mode == "const" { return }
    let s = x.sym
    if x.mode == "mem" && s != nil {
        if s.storage != "local" { error("cannot move out of global '" + s.name + "'; use take() on an optional one") }
        if inDefer { error("cannot move '" + s.name + "' inside defer") }
        if condDepth > 0 { error("cannot move '" + s.name + "' inside && or ||") }
        if pinned == s { error("cannot move '" + s.name + "' while assigning into it") }
        if s.deferUse { error("'" + s.name + "' is used by a defer and cannot be moved") }
        s.moved = true
        return
    }
    error("cannot move out of a field or element; use take() on an optional one")
}

// an owned value that nothing owns yet must be stored, not just looked at
fn noTemp(x: Item) {
    if x.mode == "reg" && isOwned(x.typ) {
        error("store this value in a variable first: otherwise it would never be freed")
    }
}

fn openBlock() {""")

rep("""fn designator(): Item {
    let name = ident()""", """fn designatorT(target: bool): Item

fn designator(): Item { return designatorT(false) }

// target: the designator is the left side of an assignment, where a moved
// local may be given a new value
fn designatorT(target: bool): Item {
    let name = ident()""")
rep("""        x.sym = s
        x.readonly = s.kind == "let"
    } else if s.kind == "type" && sym == T_LPAREN""", """        x.sym = s
        x.root = s
        x.readonly = s.kind == "let"
        if s.owned && s.moved {
            if target {
                x.movedRef = true
            } else {
                error("'" + name + "' was moved and has no value here")
            }
        }
        if inDefer && s.owned { s.deferUse = true }
    } else if s.kind == "type" && sym == T_LPAREN""")
rep("""    while sym == T_DOT || sym == T_LBRACK {
        if x.typ.kind == "opt" { error("'" + describe(x) + "' may be nil: check it with '!= nil' first") }""", """    let root = x.root
    while sym == T_DOT || sym == T_LBRACK {
        if x.movedRef { error("'" + name + "' was moved and has no value here") }
        if x.mode == "reg" && isOwned(x.typ) {
            error("store this value in a variable first: otherwise it would never be freed")
        }
        if x.typ.kind == "opt" { error("'" + describe(x) + "' may be nil: check it with '!= nil' first") }""")
i = s.index("fn designatorT(target: bool): Item {")
j = s.index("\n    return x\n}\n", i)
s = s[:j] + "\n    if x.sym == nil && root != nil {\n        x.root = root\n        x.deref = true\n    }" + s[j:]

rep("""    noreturn = false
    let x = designator()
    if noreturn && (sym == T_SEMI || sym == T_RBRACE) { return true }""", """    noreturn = false
    let x = designatorT(true)
    if noreturn && (sym == T_SEMI || sym == T_RBRACE) { return true }
    if x.movedRef && sym != T_ASSIGN {
        let r = x.root
        if r != nil { errorAt("'" + r.name + "' was moved and has no value here", line, col) }
    }""")
rep("""        if o == T_ASSIGN {
            let y = expression()
            checkAssign(dtype, y, "assignment")
            if xs != nil && xs.storage == "local" && !assignable(x.typ, y.typ) { widen(xs) }
            store(x, y)""", """        if o == T_ASSIGN {
            let callsBefore = userCalls
            let savedPin = pinned
            let r0 = x.root
            if x.deref && r0 != nil && r0.storage == "local" { pinned = r0 }
            let y = expression()
            pinned = savedPin
            checkAssign(dtype, y, "assignment")
            consume(y)
            if x.deref && r0 != nil && r0.storage == "global" && isOwned(symType(r0)) && userCalls != callsBefore {
                errorAt("cannot assign through global '" + r0.name + "' across a call: the call could free what it points into", line, col)
            }
            if xs != nil && xs.storage == "local" && !assignable(x.typ, y.typ) { widen(xs) }
            if isOwned(dtype) {
                // free the old value first, unless the local's value was moved away
                var live = true
                if xs != nil && xs.storage == "local" { live = !xs.moved }
                if live {
                    resolve(x)
                    let r = alloc()
                    opMem(0x8B, r, memOf(x), true, false)
                    rtCall1(RT_FREE, makeReg(dtype, r), tVoid)
                }
                if xs != nil { xs.moved = false }
            }
            store(x, y)""")
rep("""            if x.mode != "reg" { errorAt("this expression does nothing", line, col) }
            releaseItem(x)""", """            if x.mode != "reg" { errorAt("this expression does nothing", line, col) }
            if isOwned(x.typ) {
                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once
            } else {
                releaseItem(x)
            }""")
rep("""        checkAssign(f.params[i], a, "argument " + string(i + 1) + " of " + f.name)""",
    """        checkAssign(f.params[i], a, "argument " + string(i + 1) + " of " + f.name)
        consume(a)""")
rep("""    expect(T_RPAREN, "")
    emit(0xE8)
    emit32(0)
    let at = pc() - 4""", """    expect(T_RPAREN, "")
    userCalls += 1
    emit(0xE8)
    emit32(0)
    let at = pc() - 4""")

rep("""        checkAssign(tt, x, "initialising '" + name + "'")
        var s = gsym""", """        checkAssign(tt, x, "initialising '" + name + "'")
        consume(x)
        var s = gsym""")
rep("""        store(dst, x)
        if isGlobal { initLink = jmp() }
        declare(s)""", """        store(dst, x)
        if isGlobal { initLink = jmp() }
        declare(s)
        if !isGlobal && isOwned(tt) { addOwnedLocal(s) }""")
rep("""            let s = newLocal(name, t, kind)
            opMem(0xC7, 0, mem(RBP, -1, s.offset, 8), true, false)
            emit32(0)""", """            let s = newLocal(name, t, kind)
            opMem(0xC7, 0, mem(RBP, -1, s.offset, 8), true, false)
            emit32(0)
            if isOwned(t) { addOwnedLocal(s) }""")
rep("""        s.offset = 16 + 8 * (n - 1 - i)
        declare(s)""", """        s.offset = 16 + 8 * (n - 1 - i)
        declare(s)
        if isOwned(params[i].typ) { addOwnedLocal(s) }""")
rep("""    for f in rec.fields {
        if isRef(f.typ) { append(ptrs, f.offset) }
    }""", """    for f in rec.fields {
        if isOwned(f.typ) { append(ptrs, f.offset) }            // the fields freed with the record
    }""")

rep("""        let x0 = expression()
        checkAssign(f.ret, x0, "return value")
        let x = load(x0)
        if active {""", """        let x0 = expression()
        checkAssign(f.ret, x0, "return value")
        consume(x0)
        let x = load(x0)
        for k in blocks[fnBlock].omark..len(ownedLocals) {
            if !ownedLocals[k].moved { active = true }
        }
        if active {""")
rep("""    if f.ret == tVoid {
        if sym != T_SEMI && sym != T_RBRACE { error(f.name + " does not return a value") }
        runDefers(fnBlock)""", """    if f.ret == tVoid {
        if sym != T_SEMI && sym != T_RBRACE { error(f.name + " does not return a value") }
        runDefers(fnBlock)""")

rep("""        pushNarrowings(x.nt)
        let y = loadCond(relation())
        truncate(narrowStack, mark)""", """        pushNarrowings(x.nt)
        condDepth += 1
        let y = loadCond(relation())
        condDepth -= 1
        truncate(narrowStack, mark)""")
rep("""        pushNarrowings(x.nf)
        let y = loadCond(conjunction())
        truncate(narrowStack, mark)""", """        pushNarrowings(x.nf)
        condDepth += 1
        let y = loadCond(conjunction())
        condDepth -= 1
        truncate(narrowStack, mark)""")
open(path, 'w').write(s)
print("part A applied")
