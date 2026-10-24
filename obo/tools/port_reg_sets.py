#!/usr/bin/env python3
"""Port step: a set of registers is a bit mask, not an array.

Saving and restoring the registers in use built three arrays per call (the set in
use, the free list copied from the pool, and the argument registers of each
runtime call), and rtCall made about 2,800 of those calls on a self-compile. Bit r
of a set is register r. Order is kept: registers are pushed in pool order and
popped in reverse, and the free list is refilled in place in pool order, so the
code is the same. copyInts is no longer used and goes."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""fn inUse(): []int {
    let out = new []int(0)
    let poolRefs = &pool
    for r in poolRefs {
        if !containsInt(free, r) { append(out, r) }
    }
    return out
}
""", """// the registers in use, as a set: bit r is set for register r
fn usedRegs(): int {
    var set = 0
    let poolRefs = &pool
    for r in poolRefs {
        if !containsInt(free, r) { set = set | (1 << r) }
    }
    return set
}

// the registers not in busy are free, in pool order
fn setFree(busy: int) {
    truncate(free, 0)
    let poolRefs = &pool
    for r in poolRefs {
        if (busy & (1 << r)) == 0 { append(free, r) }
    }
}

// pushes the registers in the set, in pool order; popRegs pops them in reverse
fn pushRegs(set: int) {
    let poolRefs = &pool
    for r in poolRefs {
        if (set & (1 << r)) != 0 { pushR(r) }
    }
}

fn popRegs(set: int) {
    var i = len(pool) - 1
    while i >= 0 {
        if (set & (1 << pool[i])) != 0 { popR(pool[i]) }
        i -= 1
    }
}

// the C argument registers, in order
fn argReg(i: int): int {
    if i == 0 { return RDI }
    if i == 1 { return RSI }
    if i == 2 { return RDX }
    return RCX
}
""")

sub("""fn saveRegs(): []int {
    let saved = inUse()
    for r in saved { pushR(r) }
    free = copyInts(pool)
    return saved
}

fn restoreRegs(saved: &[]int) {
    var i = len(saved) - 1
    while i >= 0 {
        popR(saved[i])
        i -= 1
    }
    let f = new []int(0)
    let poolRefs = &pool
    for r in poolRefs {
        if !containsInt(saved, r) { append(f, r) }
    }
    free = f
}
""", """// the registers in use are pushed, and all of them are free for the callee
fn saveRegs(): int {
    let saved = usedRegs()
    pushRegs(saved)
    setFree(0)
    return saved
}

// the saved registers are popped, and the ones that were not in use are free again
fn restoreRegs(saved: int) {
    popRegs(saved)
    setFree(saved)
}
""")

sub("""    let saved = inUse()
    for r in saved { pushR(r) }
    let abi = new []int(0)
    append(abi, RDI)
    append(abi, RSI)
    append(abi, RDX)
    append(abi, RCX)
    for i in 0..len(regs) { movRR(abi[i], regs[i]) }
""", """    let saved = usedRegs()
    pushRegs(saved)
    for i in 0..len(regs) { movRR(argReg(i), regs[i]) }
""")

sub("""    var i = len(saved) - 1
    while i >= 0 {
        popR(saved[i])
        i -= 1
    }
    var res = voidItem()
""", """    popRegs(saved)
    var res = voidItem()
""")

sub("""    free = copyInts(pool)
    append(universe, newSym("type", "int", tInt))""", """    let poolRefs = &pool
    for r in poolRefs { append(free, r) }
    append(universe, newSym("type", "int", tInt))""")

sub("""fn copyInts(a: &[]int): []int {
    let out = new []int(0)
    for v in a { append(out, v) }
    return out
}

""", "")

open(P, 'w').write(s)
print("patched", P)
