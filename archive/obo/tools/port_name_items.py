#!/usr/bin/env python3
"""Port step: no placeholder Item is built where a name or a runtime call is compiled.

designatorT began with "var x = voidItem()" and replaced it on every path, so each
designator built an Item it never used: about 10,000 per self-compile. The name
lookup is now its own function, which returns on each path (error exits). rtCall
made a void placeholder for every call, and now makes the result only when the
routine returns a value. The order of the errors and of the register allocations
is unchanged."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""fn designatorT(target: bool): Item {
    let uline = tline
    let ucol = tcol
    let name = ident()
    let s = lookup(name)
    var x = voidItem()
    if s.kind == "builtin" {
        x = builtin(name)
    } else if s.kind == "fn" {
        if sym != T_LPAREN { error("functions can only be called") }
        x = callFn(s)
    } else if s.kind == "const" {
        x = makeConst(symType(s), s.value)
    } else if s.kind == "var" || s.kind == "let" {
        if s.storage == "local" {
            x = newItem("mem", varType(s))
            x.base = RBP
            x.disp = s.offset
        } else {
            x = newItem("mem", symType(s))
            x.disp = s.offset
        }
        x.sym = s
        x.root = s
        x.readonly = s.kind == "let"
        x.borrowed = s.isRef
        if (s.owned || s.isRefLocal) && s.moved {
            if target {
                x.movedRef = true
            } else if s.isRefLocal {
                error("'" + name + "' may point to freed memory: something under what it points into was changed; point it again first")
            } else {
                error("'" + name + "' was moved and has no value here")
            }
        }
        if inDefer && s.owned { s.deferUse = true }
    } else if s.kind == "type" && sym == T_LPAREN && (symType(s) == tInt || symType(s) == tByte || symType(s) == tString) {
        x = conversion(symType(s))
    } else {
        error("'" + name + "' is a type, not a value (use 'new " + name + "{...}' to make one)")
    }
    if sym == T_LPAREN && (s.kind == "var" || s.kind == "let" || s.kind == "const") {
""", """// the item a name stands for: a builtin, a call, a constant, a variable or a conversion
fn nameItem(s: ^Sym, name: string, target: bool): Item {
    if s.kind == "builtin" { return builtin(name) }
    if s.kind == "fn" {
        if sym != T_LPAREN { error("functions can only be called") }
        return callFn(s)
    }
    if s.kind == "const" { return makeConst(symType(s), s.value) }
    if s.kind == "var" || s.kind == "let" {
        var x = newItem("mem", symType(s))
        if s.storage == "local" {
            x.typ = varType(s)
            x.base = RBP
        }
        x.disp = s.offset
        x.sym = s
        x.root = s
        x.readonly = s.kind == "let"
        x.borrowed = s.isRef
        if (s.owned || s.isRefLocal) && s.moved {
            if target {
                x.movedRef = true
            } else if s.isRefLocal {
                error("'" + name + "' may point to freed memory: something under what it points into was changed; point it again first")
            } else {
                error("'" + name + "' was moved and has no value here")
            }
        }
        if inDefer && s.owned { s.deferUse = true }
        return x
    }
    if s.kind == "type" && sym == T_LPAREN && (symType(s) == tInt || symType(s) == tByte || symType(s) == tString) {
        return conversion(symType(s))
    }
    error("'" + name + "' is a type, not a value (use 'new " + name + "{...}' to make one)")
    return voidItem()
}

fn designatorT(target: bool): Item {
    let uline = tline
    let ucol = tcol
    let name = ident()
    let s = lookup(name)
    var x = nameItem(s, name, target)
    if sym == T_LPAREN && (s.kind == "var" || s.kind == "let" || s.kind == "const") {
""")

sub("""    popRegs(saved)
    var res = voidItem()
    if ret != tVoid {
        let r = alloc()
        movRR(r, RAX)
        res = makeReg(ret, r)
        res.strTemp = ret == tString            // every string a runtime call returns is fresh
    }
    for i in 0..len(regs) {
        if temps[i] {
            // not rtCall1: it comes later, and a forward heading would count as freeing anything
            let one = new []?Item(0)
            append(one, makeReg(tString, regs[i]))
            rtCall(RT_STR_DEC, one, tVoid)
        }
    }
    for r in regs { release(r) }
    return res
}
""", """    popRegs(saved)
    var got = -1
    if ret != tVoid {
        got = alloc()
        movRR(got, RAX)
    }
    for i in 0..len(regs) {
        if temps[i] {
            // not rtCall1: it comes later, and a forward heading would count as freeing anything
            let one = new []?Item(0)
            append(one, makeReg(tString, regs[i]))
            rtCall(RT_STR_DEC, one, tVoid)
        }
    }
    for r in regs { release(r) }
    if ret == tVoid { return voidItem() }
    let res = makeReg(ret, got)
    res.strTemp = ret == tString            // every string a runtime call returns is fresh
    return res
}
""")

open(P, 'w').write(s)
print("patched", P)
