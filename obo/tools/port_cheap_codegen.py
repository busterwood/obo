#!/usr/bin/env python3
"""Phase 1 of the optimization plan (FORK.md), without loop inversion: cheap code generation.

1. push imm: a constant argument of an obo call is pushed as an immediate (6A ib or
   68 id), with no register loaded first.
2. A constant argument of a runtime call goes straight to its argument register
   (mov edi, imm), so no pool register is loaded, pushed around the call and popped.
   With one push fewer, the alignment pad often goes too.
3. A function with no locals loses its 'sub rsp,0x0'. The frame size is known only
   after the body, so the 7 bytes are cut out then and the body moves back: calls out
   of the body (recorded in farCalls) and calls waiting for a later function (its
   fixups) are adjusted; jumps within the body are relative and need nothing.

The static stack depth the plan asks for was already tracked (depth): pads are emitted
only where the depth is misaligned, so that part needed no change."""
import os
import sys
P = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()


def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)


# 1. push imm
sub('''fn pop(r: int) {
    if r >= 8 { emit(0x41) }
    emit(0x58 + (r & 7))
}
''', '''fn pop(r: int) {
    if r >= 8 { emit(0x41) }
    emit(0x58 + (r & 7))
}

// push imm8 or imm32, sign-extended to 64 bits
fn pushI(v: int) {
    if fits8(v) {
        emit(0x6A)
        emit(v)
    } else {
        emit(0x68)
        emit32(v)
    }
}
''')
sub('''// op OPC REG RM W BYTE IMM | movri R V | aluri O RM V | push R | pop R
// | jcc CC T | jmp T | call T''', '''// op OPC REG RM W BYTE IMM | movri R V | aluri O RM V | push R | pushi V | pop R
// | jcc CC T | jmp T | call T''')
sub('''    } else if k == "push" {
        push(parseInt(w[1]))
''', '''    } else if k == "push" {
        push(parseInt(w[1]))
    } else if k == "pushi" {
        pushI(parseInt(w[1]))
''')
sub('''            consume(a)
        }
        let r = load(a)
        pushR(r.r)
        release(r.r)
    }''', '''            consume(a)
        }
        if a.mode == "const" && fits32(a.val) {
            pushI(a.val)                           // no register needed
            depth += 8
            drop(a)
        } else {
            let r = load(a)
            pushR(r.r)
            release(r.r)
        }
    }''')

# 2. constant arguments of runtime calls
sub('''// Calls a runtime routine with the C ABI; live registers, including the
// arguments, are pushed first so the collector sees every pointer.
fn rtCall(slot: int, args: []?Item, ret: ^Type): Item {
    let regs = new []int(0)
    let temps = new []bool(0)
    for ai in 0..len(args) {
        let a = take(args[ai])
        if a != nil {
            append(temps, a.strTemp && slot != RT_STR_DEC)    // dropping a string is its own drop
            let la = load(a)
            append(regs, la.r)
        } else {
            drop(a)
        }
    }
    let saved = usedRegs()
    pushRegs(saved)
    for i in 0..len(regs) { movRR(argReg(i), regs[i]) }''', '''var argConsts = new []int(0)      // rtCall's constant arguments, used before it calls again

// Calls a runtime routine with the C ABI; live registers, including the
// arguments, are pushed first so the collector sees every pointer. A constant
// argument needs no register: it goes straight to its argument register.
fn rtCall(slot: int, args: []?Item, ret: ^Type): Item {
    let regs = new []int(0)                 // -1 for a constant, which is in argConsts
    truncate(argConsts, 0)
    let temps = new []bool(0)
    for ai in 0..len(args) {
        let a = take(args[ai])
        if a != nil {
            append(temps, a.strTemp && slot != RT_STR_DEC)    // dropping a string is its own drop
            if a.mode == "const" && !a.strTemp {
                append(regs, -1)
                append(argConsts, a.val)
                drop(a)
            } else {
                let la = load(a)
                append(regs, la.r)
                append(argConsts, 0)
            }
        } else {
            drop(a)
        }
    }
    let saved = usedRegs()
    pushRegs(saved)
    for i in 0..len(regs) {
        if regs[i] < 0 { movRI(argReg(i), argConsts[i]) } else { movRR(argReg(i), regs[i]) }
    }''')

# 3. no 'sub rsp,0x0'
sub('''var frame = 0
var inDefer = false''', '''var frame = 0
var farCalls = new []int(0)       // where the current function's calls out of its body keep their rel32
var inDefer = false''')
sub('''fn callAbs(addr: int) {
    emit(0xE8)
    emit32(addr - (CODE_BASE + pc() + 4))
}''', '''fn callAbs(addr: int) {
    emit(0xE8)
    append(farCalls, pc())
    emit32(addr - (CODE_BASE + pc() + 4))
}''')
sub('''    if f.addr >= 0 {
        fix(at, f.addr)
    } else {
        append(f.fixups, at)
    }''', '''    if f.addr >= 0 {
        fix(at, f.addr)
        append(farCalls, at)
    } else {
        append(f.fixups, at)
    }''')
sub('''    curFn = f
    pendLine = 0
    frame = 0
    depth = 0
''', '''    curFn = f
    pendLine = 0
    frame = 0
    depth = 0
    truncate(farCalls, 0)
''')
sub('''    let size = (frame + 15) & ~15
    code[frameAt] = byte(size)
    code[frameAt + 1] = byte(size >> 8)
    code[frameAt + 2] = byte(size >> 16)
    code[frameAt + 3] = byte(size >> 24)
''', '''    let size = (frame + 15) & ~15
    if size == 0 {
        dropFrame(frameAt)
    } else {
        code[frameAt] = byte(size)
        code[frameAt + 1] = byte(size >> 8)
        code[frameAt + 2] = byte(size >> 16)
        code[frameAt + 3] = byte(size >> 24)
    }
''')
sub('''// =========================================================== declarations

''', '''// =========================================================== declarations

// A function with no locals needs no 'sub rsp': its seven bytes, which end with the
// frame size at 'at', are cut out and the body after them moves back. Jumps within the
// body are relative and move with it. A call out of it (farCalls) is now 7 bytes further
// from its target, and a call waiting for a later function has moved in its fixups.
fn dropFrame(at: int) {
    for i in at + 4..len(code) { code[i - 7] = code[i] }
    truncate(code, len(code) - 7)
    let calls = &farCalls
    for p in calls { putRel(p - 7, getRel(p - 7) + 7) }
    let gs = &globals
    for g in gs {
        if g.kind == "fn" && g.addr < 0 {
            for fi in 0..len(g.fixups) {
                if g.fixups[fi] > at { g.fixups[fi] -= 7 }
            }
        }
    }
}

''')
open(P, 'w').write(s)
print("patched", P)
