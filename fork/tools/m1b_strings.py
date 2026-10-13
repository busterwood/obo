"""Milestone 1b: reference-counted strings in obo.obx."""
import re
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:90])
    s = s.replace(old, new)


rep("""    root: ?Sym                // the variable a designator starts from""", """    root: ?Sym                // the variable a designator starts from
    strTemp: bool             // a fresh string whose reference nothing owns yet""")
rep("""    deferUse: bool            // a defer reads it, so it may not be moved""", """    deferUse: bool            // a defer reads it, so it may not be moved
    isStr: bool               // a string local, holding one reference""")
rep("""const RT_FINISH = 23""", """const RT_FINISH = 23
const RT_STR_INC = 24
const RT_STR_DEC = 25""")

# descriptors list owned fields and string fields
rep("""fn descriptor(size: int, ptrs: []int): int {
    rodAlign()
    let addr = RODATA_BASE + len(rodata)
    rod64(size)
    rod64(len(ptrs))
    for p in ptrs { rod64(p) }
    return addr
}""", """// [size][n owned][offsets...][n strings][offsets...]: what freeing a record frees
fn descriptor(size: int, ptrs: []int, strs: []int): int {
    rodAlign()
    let addr = RODATA_BASE + len(rodata)
    rod64(size)
    rod64(len(ptrs))
    for p in ptrs { rod64(p) }
    rod64(len(strs))
    for p in strs { rod64(p) }
    return addr
}""")
rep("""    for f in rec.fields {
        if isOwned(f.typ) { append(ptrs, f.offset) }            // the fields freed with the record
    }
    rec.desc = descriptor(offset, ptrs)""", """    let strs = new []int(0)
    for f in rec.fields {
        if isOwned(f.typ) { append(ptrs, f.offset) }            // the fields freed with the record
        if f.typ.kind == "string" { append(strs, f.offset) }    // and the strings it drops
    }
    rec.desc = descriptor(offset, ptrs, strs)""")

# runtime calls drop fresh strings they were only shown
rep("""    let regs = new []int(0)
    for a in args { append(regs, load(a).r) }""", """    let regs = new []int(0)
    let temps = new []bool(0)
    for a in args {
        append(temps, a.strTemp)
        append(regs, load(a).r)
    }""")
rep("""    for r in regs { release(r) }
    if ret == tVoid { return voidItem() }
    let r = alloc()
    movRR(r, RAX)
    return makeReg(ret, r)
}""", """    var res = voidItem()
    if ret != tVoid {
        let r = alloc()
        movRR(r, RAX)
        res = makeReg(ret, r)
        res.strTemp = ret == tString            // every string a runtime call returns is fresh
    }
    for i in 0..len(regs) {
        if temps[i] { rtCall1(RT_STR_DEC, makeReg(tString, regs[i]), tVoid) }
    }
    for r in regs { release(r) }
    return res
}""")
rep("""    let r = alloc()
    movRR(r, RAX)
    return makeReg(f.ret, r)""", """    let r = alloc()
    movRR(r, RAX)
    let res = makeReg(f.ret, r)
    res.strTemp = f.ret == tString               // the caller now holds the returned reference
    return res""")

# consume: strings take a reference (or keep a fresh one); owned values move
rep("""fn consume(x: Item) {
    if !isOwned(x.typ) || x.mode == "reg" || x.mode == "const" { return }""", """fn consume(x: Item) {
    if x.typ.kind == "string" {
        // the destination will hold a reference: a fresh string hands over its
        // own, anything else gets a new one (literals need none)
        if x.strTemp {
            x.strTemp = false
        } else if x.mode != "const" {
            let r = rtCall1(RT_STR_INC, x, tString)
            x.mode = "reg"
            x.r = r.r
            x.base = -1
            x.index = -1
            x.sym = nil
            x.strTemp = false
        }
        return
    }
    if !isOwned(x.typ) || x.mode == "reg" || x.mode == "const" { return }""")
rep("""// an owned value that nothing owns yet must be stored, not just looked at""", """// A string read from a variable, field or element, about to be held while
// more code runs (which might replace it), takes a reference of its own.
fn pinStr(x: Item): Item {
    if x.typ.kind == "string" && !x.strTemp && x.mode != "const" {
        consume(x)
        x.strTemp = true
        return x
    }
    return materialise(x)
}

fn addStrLocal(s: Sym) {
    s.isStr = true
    append(ownedLocals, s)
}

// an owned value that nothing owns yet must be stored, not just looked at""")
rep("""        if !s.moved {
            let x = newItem("mem", symType(s))
            x.base = RBP
            x.disp = s.offset
            rtCall1(RT_FREE, x, tVoid)
        }""", """        if !s.moved {
            let x = newItem("mem", symType(s))
            x.base = RBP
            x.disp = s.offset
            if s.isStr {
                rtCall1(RT_STR_DEC, x, tVoid)
            } else {
                rtCall1(RT_FREE, x, tVoid)
            }
        }""")
rep("""        if !isGlobal && isOwned(tt) { addOwnedLocal(s) }""", """        if !isGlobal && isOwned(tt) { addOwnedLocal(s) }
        if !isGlobal && tt.kind == "string" { addStrLocal(s) }""")
rep("""            if isOwned(t) { addOwnedLocal(s) }""", """            if isOwned(t) { addOwnedLocal(s) }
            if t.kind == "string" { addStrLocal(s) }""")
rep("""        if isOwned(params[i].typ) { addOwnedLocal(s) }""", """        if isOwned(params[i].typ) { addOwnedLocal(s) }
        if params[i].typ.kind == "string" { addStrLocal(s) }""")

# assignment drops the old string
rep("""                if xs != nil { xs.moved = false }
            }
            store(x, y)""", """                if xs != nil { xs.moved = false }
            } else if dtype.kind == "string" {
                resolve(x)
                let r = alloc()
                opMem(0x8B, r, memOf(x), true, false)
                rtCall1(RT_STR_DEC, makeReg(tString, r), tVoid)
            }
            store(x, y)""")
rep("""            if isOwned(x.typ) {
                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once
            } else {
                releaseItem(x)
            }""", """            if isOwned(x.typ) {
                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once
            } else if x.strTemp {
                rtCall1(RT_STR_DEC, x, tVoid)
            } else {
                releaseItem(x)
            }""")

# operands held while more code runs
rep("""        if o == T_PLUS && x.typ.kind == "string" {
            x = materialise(x)""", """        if o == T_PLUS && x.typ.kind == "string" {
            x = pinStr(x)""")
rep("""    noTemp(x)
    x = materialise(x)""", """    noTemp(x)
    x = pinStr(x)""")
rep("""        noTemp(a)
        a = materialise(a)""", """        noTemp(a)
        a = pinStr(a)""")
rep("""        p = materialise(p)
        expect(T_COMMA, "")
        var d = expression()""", """        p = pinStr(p)
        expect(T_COMMA, "")
        var d = expression()""")

# len of a fresh string
rep("""        if a.mode == "const" { return makeConst(tInt, len(rodataString(a.val))) }
        let x = load(a)""", """        if a.mode == "const" { return makeConst(tInt, len(rodataString(a.val))) }
        if a.strTemp {
            let p = load(a)
            let n = alloc()
            movRR(n, p.r)
            opReg(0x85, n, n, true, false)
            emit(0x74)
            emit(0)
            let at0 = pc()
            opMem(0x8B, n, mem(n, -1, 0, 8), true, false)
            code[at0 - 1] = byte(pc() - at0)
            rtCall1(RT_STR_DEC, p, tVoid)
            return makeReg(tInt, n)
        }
        let x = load(a)""")

# indexing: no fresh strings, and no calls in an index through a global
rep("""            next()
            let line = tline
            let kind = x.typ.kind""", """            next()
            let line = tline
            let kind = x.typ.kind
            if x.strTemp { error("store this string in a variable first") }
            let callsBefore = userCalls""")
rep("""            let i0 = expression()
            if !isInt(i0.typ) { error("array index must be int") }""", """            let i0 = expression()
            if !isInt(i0.typ) { error("array index must be int") }
            if root != nil && root.storage == "global" && userCalls != callsBefore {
                error("cannot index through global '" + root.name + "' with an index that calls a function: the call could free it")
            }""")

# for-each over strings: the loop variable holds a reference to the current element
a = s.index("fn forStat()")
b = s.index("\nfn ", a + 10)
body = s[a:b]
old = """        let v = newLocal(name, baseOf(x.typ), "let")"""
assert body.count(old) == 1
body = body.replace(old, """        let v = newLocal(name, baseOf(x.typ), "let")
        let strs = baseOf(x.typ).kind == "string"
        if strs {
            opMem(0xC7, 0, mem(RBP, -1, v.offset, 8), true, false)
            emit32(0)
            addStrLocal(v)
        }
        let vi = newItem("mem", baseOf(x.typ))
        vi.base = RBP
        vi.disp = v.offset""")
old = """        exitLink = jcc(CC_GE)
        opMem(0x8B, ra, mem(ra, -1, 16, 8), true, false)"""
assert body.count(old) == 1
body = body.replace(old, """        exitLink = jcc(CC_GE)
        if strs { rtCall1(RT_STR_DEC, vi, tVoid) }            // drop the previous element
        opMem(0x8B, ra, mem(ra, -1, 16, 8), true, false)""")
old = """        opMem(0x89, ri, mem(RBP, -1, v.offset, 8), true, false)
        release(ri)
        release(ra)"""
assert body.count(old) == 1
body = body.replace(old, """        opMem(0x89, ri, mem(RBP, -1, v.offset, 8), true, false)
        release(ri)
        release(ra)
        if strs { release(rtCall1(RT_STR_INC, vi, tString).r) }   // and hold the new one""")
s = s[:a] + body + s[b:]

# module exit: owned globals freed, string globals dropped
rep("""        if (g.kind == "var" || g.kind == "let") && isOwned(symType(g)) {
            let x = newItem("mem", symType(g))
            x.disp = g.offset
            rtCall1(RT_FREE, x, tVoid)
        }""", """        if (g.kind == "var" || g.kind == "let") && isOwned(symType(g)) {
            let x = newItem("mem", symType(g))
            x.disp = g.offset
            rtCall1(RT_FREE, x, tVoid)
        }
        if (g.kind == "var" || g.kind == "let") && symType(g).kind == "string" {
            let x = newItem("mem", tString)
            x.disp = g.offset
            rtCall1(RT_STR_DEC, x, tVoid)
        }""")
open(path, 'w').write(s)
print("strings patch applied")
