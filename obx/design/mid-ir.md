# A mid-level IR for the native backend

Status: study, 2026-11-05. Nothing in the compiler changes on this branch
(`ssa-study`). The open decisions at the end are the user's.

The question: should obx put a MIR-like linear IR between the tree IR and
`x64gen`? That means three-address instructions over virtual registers, in
basic blocks. SSA would exist only inside it, for the passes that need it,
and be taken apart again before liveness and register allocation. This is
the model of Vladimir Makarov's MIR, which this study reads closely.

Sources and measurements:

- MIR: `/home/chris/mir`, commit `dc2c763f` (MIT, Copyright 2018-2024
  Vladimir Makarov). Read only. Every MIR claim cites a file and line or a
  function name.
- MIR was built and timed only in the scratch directory
  `/tmp/claude-1000/-home-chris-obx/849ae6ad-603b-418d-9217-39a5ab4d6a51/scratchpad/mirbuild`
  (a copy of the sources, `make c2m`, and a driver `gendrv.c` that runs
  `MIR_gen` on every function of a binary MIR file at a given level).
- obx probes: on the separate branch `ssa-probe` (commit `ad0a150`, not
  for merging). `obx/compiler/probe.obx` counts the IR after `opt` and
  `loops`; `obx/compiler/ssaprobe.obx` runs a Braun et al. SSA
  construction straight from the tree. Both are switched on by
  `OBX_PROBE`.
- Self-build: the native-built compiler (sources at `14a482c`) compiling
  `obx/compiler/main.obx --native`, user-mode instructions from `perf stat`.

## 0. Summary

| question | answer |
|---|---|
| Is MIR an SSA IR? | No. A linear register IR. `-O2` builds SSA for six passes and undoes it (`mir-gen.c` 9385-9470). Liveness and allocation run on the non-SSA form. |
| What does MIR's generator cost? | 11.4K x86 instructions per MIR instruction at `-O0`, 18.0K at `-O1`, 26.5K at `-O2` (measured on c2mir.c). |
| What does x64gen cost? | About 5.8K per instruction of the same granularity (1.28G of the 4.47G self-build). |
| How much faster could obx's code get? | The native-built compiler runs at 0.81 of the gcc -O2 build's speed (cycles). On the benchmarks native already matches gcc. gcc's whole redundancy family (FRE, PRE, GCSE, DOM, DSE, LICM) is worth 5.3% of the compiler's cycles. |
| What would a mid IR cost? | 6K to 8.5K lines of obx. 6K to 10K instructions per mid instruction for the full pipeline, against x64gen's 5.8K: 0% to +20% on the self-build. At MIR's constants, +60% to +105%. |
| What pays first? | A register allocator with live-range splitting, then instruction selection, then GVN with obx's alias classes. |
| Recommendation | Not as a default pass now. If the user wants the next ~10% of run speed from the native backend, build M1 (lowering, liveness and a static allocation model, no code) and decide at its gate. Otherwise keep x64gen. |

## 1. MIR's pipeline

### 1.1 The model

MIR is a linear IR of instructions over typed local variables ("regs"), with
labels and jumps (README.md lines 30-70, MIR.md). The generator
(`mir-gen.c`, 10,085 lines, and `mir-gen-x86_64.c`, 3,217 lines) runs per
function (`generate_func_code`, driver from line 9341). SSA is a temporary
state: `build_ssa` at 9385, `undo_build_ssa` at 9470. Everything after that
works on ordinary registers. README.md line 264: "SSA form of MIR is used
before register allocation".

### 1.2 The passes, in order

Driver lines are in `mir-gen.c`. "Level" is the lowest `-O` level that runs
the pass (`optimize_level`; level 3 runs the same passes as 2: no
`optimize_level >= 3` test exists). Lines are the source section's size.

| # | pass | level | where | algorithm | lines |
|---|---|---|---|---|---:|
| 0 | simplify, inline | all | `mir.c` `MIR_link`, `process_inlines` 4013 | lowers memory operands; inlines callees of ≤ 200 insns, ≤ 50 at a call, with a growth limit (`MIR_MAX_FUNC_INLINE_GROWTH` 50; `mir.c` 3855-3869, used at 4080) | in mir.c |
| 1 | build CFG | 0 | `build_func_cfg` 1572 (driver 9371) | blocks from labels and jumps | ~400 |
| 2 | clone blocks | 2 | `clone_bbs` 2016 (9377) | duplicates small blocks to remove jumps | ~120 |
| 3 | build SSA | 2 | `build_ssa` 2676, `minimize_ssa` 2324 (9385) | Braun-style, but "first we build optimized maximal SSA, then we minimize it" (comment at 2136): phis at every multi-predecessor block, then iterative removal | ~710 (2134-2844) |
| 4 | address transformation | 2 | `transform_addrs` 2890 (9397) | locals whose address is taken go to memory, or the address insn goes; then SSA is undone and rebuilt (9398-9400) | ~215 |
| 5 | GVN | 2 | `gvn` 4932 (9411) | dominator-tree value numbering with expression hash tables (`expr_hash` 3645); constant folding and branch folding (`ccp_insns_num` 4787, branch deletion 4799-4818); redundant loads by memory availability, a forward dataflow (`calculate_memory_availability` 3537); store-to-load forwarding; alias by front-end alias names (`may_alias_p` 3454) | ~1,600 (3374-4977) |
| 6 | copy propagation | 2 | `copy_prop` 3204 (9420) | SSA copies, redundant extensions | ~315 |
| 7 | dead store elimination | 2 | `dse` 5142 (9428) | backward liveness of memory locations (`calculate_mem_live_info` 5133) | ~210 |
| 8 | SSA dead code | 2 | `ssa_dead_code_elimination` 5239 (9435) | use counts on SSA edges | ~85 |
| 9 | LICM | 2 | `build_loop_tree` 1161, `licm` 5540 (9442-9444) | preheaders (`licm_add_loop_preheaders` 5316); invariant insns, never loads; pressure-aware (`setup_loop_pressure` 1139) | ~275 |
| 10 | pressure relief | 2 | `pressure_relief` 5554 (9453) | moves insns toward their uses | ~40 |
| 11 | conventional SSA, SSA combine | 2 | `make_conventional_ssa` 2713, `ssa_combine` 5869 (9460-9465) | copies for phis; folds address arithmetic and compare+branch pairs | ~340 |
| 12 | out of SSA | 2 | `undo_build_ssa` 2787 (9470) | phis become moves | in 3 |
| 13 | jump optimization | 2 | `jump_opt` 6686 (9481) | jumps to jumps, unreachable blocks | ~125 |
| 14 | machinize | all | `target_machinize` (`mir-gen-x86_64.c` 695-1143) (9487) | ABI moves, two-operand form, fixed registers | ~450 |
| 15 | loop tree | 1 | `build_loop_tree` (9494) | natural loops, for block frequencies | ~120 |
| 16 | coalescing | 2 | `collect_moves`, `coalesce` 6982 (9498-9502) | aggressive; a conflict matrix, given up above 10,000 variables (`MIR_MAX_COALESCE_VARS` 6197: "about 8MB") | ~320 |
| 17 | liveness, live ranges | all | `calculate_func_cfg_live_info` 6187, `solve_dataflow` 1934, `build_live_ranges` 6603 (9510) | iterative bit-vector dataflow, then program-point ranges | ~750 (5935-6680) |
| 18 | register allocation | all | `reg_alloc` 8584, `assign` 7608, `split` 8527 (9512) | priority-based linear scan; below `-O2` a simplified assignment without splitting (`simplified_p` 7623); at `-O2` live-range splitting into gaps, spill/restore at their edges (`lr_gap` 7325) | ~1,420 (7232-8651) |
| 19 | combine | 1 | `combine` 9089 (9523) | after RA: substitutes defs into uses (code selection, addressing modes) | ~600 |
| 20 | dead code | 1 | `dead_code_elimination` 9251 (9528) | dead outputs after combine | ~80 |
| 21 | prologue, emission | all | `target_make_prolog_epilog` (`mir-gen-x86_64.c` 1159), `target_translate` | saves the used callee-saved registers at entry; no shrink-wrapping; pattern table (`patterns[]` 1581, ~130 entries) and encoder | ~1,850 |

So `-O0` is CFG, machinize, liveness, simple assignment, emission. `-O1` adds
the loop tree (frequencies), combine and DCE. `-O2` adds the SSA passes 2-13,
coalescing and splitting.

### 1.3 Data structures

- Instructions are a doubly linked list (`DLIST`) per function; blocks hold
  `bb_insn` wrappers. Operands are `MIR_op_t` records (register, memory with
  base, index, scale, displacement, alias names).
- Sets are bitmaps (`bitmap_t`) everywhere: liveness, memory availability,
  memory liveness, loop invariants, conflicts.
- SSA has two forms (comment at 2136): def pointers only, and def-use chains
  kept in the operand's `data` field (`ssa_edge_t`) to save memory.
- GVN uses hash tables of expressions and of memory expressions
  (`find_expr` 3673, `find_mem_expr` 3750).
- Live ranges are linked lists per variable (`live_range_t`).

### 1.4 MIR's published figures

From README.md "Current MIR Performance Data" (lines 335-410; i5-13600K,
GCC 12.3.1):

| | MIR generator | MIR interpreter | gcc -O2 | gcc -O0 |
|---|---:|---:|---:|---:|
| compile sieve | 1.0 (249 us) | 0.09 | 109 (27.1 ms) | 105 |
| run sieve | 1.0 (1.74 s) | 13.7 | 0.92 (1.6 s) | 2.28 |
| LOC | 1.0 (23.4K) | 0.48 | 103 | 103 |

On 15 small C benchmarks, geomean speed relative to gcc -O2 (README lines
392-409): c2m (MIR generator) 0.91, gcc -O0 0.57, cproc (QBE) 0.65, tcc
0.50, pcc 0.80, chibicc 0.30, clang -O2 1.09.

### 1.5 Measured here

c2m compiled `c2mir.c` and `mir-gen.c` to binary MIR (`c2mir.bmir`: 1,005
functions, 57,453 instructions, 124,978 after inlining at link;
`mirgen.bmir`: 1,259 functions). obx's emitted C could not be used: c2m
rejects `__int128` and `__builtin_*_overflow`. `gendrv` then ran `MIR_gen` on
every function. User-mode instructions on c2mir.bmir:

| level | instructions | minus load and link (423M) | per MIR insn after inlining |
|---|---:|---:|---:|
| none | 423M | | |
| `-O0` | 1,851M | 1,428M | 11.4K |
| `-O1` | 2,667M | 2,244M | 18.0K |
| `-O2` | 3,738M | 3,315M | 26.5K |

The SSA passes (`-O2` minus `-O1`) cost 8.6K per instruction on c2mir.c and
about 15K on mir-gen.c. Two very large functions took 40% of the `-O2` time
on c2mir.c: several passes are superlinear in function size. The `-O2`
profile is flat. The largest entries are `calculate_func_cfg_live_info`
5.0%, `build_ssa` 4.8%, `make_live_from_mem` (DSE) 3.9%, `assign` 3.3%,
`process_bb_ranges` 3.0%, `pattern_match_p` 2.6%, `coalesce` 2.5%.

### 1.6 MIR's limits, as they bear on obx

- It is built for a JIT: code goes to executable memory with absolute
  addresses (`MIR_gen`, 9571, returns the code's address). It has no relocatable
  output for an ELF writer.
- It is C and uses libc. The native obx compiler is freestanding (no libc,
  no gcc at build time).
- Alias information is only the front end's alias names on memory operands
  (`may_alias_p` 3454). LICM never moves an instruction with a memory operand, a call or a division (`loop_invariant_p` 5340).
- Overflow is `ADDO`/`SUBO`/`MULO` followed by `BO`/`BNO` (MIR.md 365-449).
  There is no notion of a check whose trap must stay in place.
- Coalescing gives up above 10K variables. Some passes are superlinear in
  function size (above).
- No shrink-wrapping: the used callee-saved registers are saved at entry.

## 2. Where obx stands

### 2.1 The native path today

`main.obx` (`buildNative`): `loops.optimize(opt.optimize(p))`, then
`x64gen` and `x64.obx` (the assembler), then `elf.obx`. See
design/native.md sections 1 and 4.

Profile of the self-build (4,473M instructions; perf samples mapped with the
native map file):

| part | share | instructions |
|---|---:|---:|
| x64gen | 25.8% | ~1.15G |
| loops | 15.2% | ~0.68G |
| check | 9.1% | |
| opt | 7.3% | ~0.33G |
| std (maps, strings) | 7.2% | |
| syntax, lower, emit, sema, ... | the rest | |
| x64 (assembler) | 2.8% | ~0.13G |

The hottest function is `x64gen.Gen.scan` at 5.6% (the register scan). No
function of the generated code is above 6%.

### 2.2 The IR's shape (probe, `OBX_PROBE=1`)

After `opt` and `loops`, the self-build has:

| what | count |
|---|---:|
| functions | 1,131 |
| IR nodes | 493,732 |
| statements | 90,634 |
| non-leaf expressions (operations) | 161,765 |
| blocks (if, loop, switch arms) | 28,704 |
| locals | 51,159 |
| loops | 1,692 |
| calls (all) / runtime calls | 31,249 / 23,422 |
| bounds checks / other checks | 5,066 / 2,362 |
| loads through pointers | 84,651 |
| pure load expressions repeated in a function | 51,798 (an upper bound for GVN) |

Function size: median 69 operations, largest 3,057. The 59 functions of 500
or more hold 31.5% of the operations; the 553 functions with a loop hold
64.6%.

### 2.3 The gap

The same compiler sources built by gcc -O2 (from the seed's C) and by the
native backend, each compiling `main.obx --native` (5 runs, a loaded
machine, ±1.5% on cycles):

| build | instructions | cycles |
|---|---:|---:|
| gcc -O2 | 3,756M | 1,948M |
| gcc -O2 without FRE, PRE, GCSE, DOM, DSE, LICM | 3,856M (+2.7%) | 2,052M (+5.3%) |
| gcc -O1 | 6,309M | 3,607M |
| native | 4,387M (+16.8%) | 2,412M (+23.8%) |

(`-fno-tree-fre -fno-tree-pre -fno-gcse -fno-tree-dominator-opts
-fno-tree-dse -fno-dse -fno-tree-loop-im -fno-move-loop-invariants`. `-O1`
is much worse because it inlines almost nothing, and obx's C leans on small
inline helpers: not a useful baseline.)

So:

- The whole gap on the compiler is 24% of cycles. gcc's redundancy and
  invariant-motion family is about 5 points of it. The rest is register
  allocation, instruction selection, scheduling and layout.
- On the benchmarks native is already level with gcc or ahead (PERFORMANCE.md:
  0.54 to 1.00 of the C build's time, maps 1.37). A mid IR would gain
  little there.
- MIR's own code reaches 0.91 of gcc on its benchmarks. A MIR-class backend
  could plausibly take the compiler from 0.81 to 0.88-0.92 of gcc: 8% to
  12% of its cycles. That is the size of the prize.

The profile says where it is (design/native.md section 8, perf13-perf18):
stack stores 7.4% and stack loads 5.6% of executed instructions,
callee-saved pushes and pops about 4%, 589 of 1,314 functions save all five
callee-saved registers. perf16 found why local fixes fail: "the generator
evaluates into rax", so a chain of loads destroys its own intermediate
values and nothing survives to the next statement.

### 2.4 Why add-on passes keep failing

"Judging a pass" (PERFORMANCE.md) keeps a pass when the run suite gains
≥ 1% and the compile suite costs ≤ 1%. "An analysis needs to cost under
about 10M instructions (0.2%) to win." 10M over the self-build's 221K mid
instructions (2.3 below) is 45 instructions per instruction. Every pass
in section 1 costs 100 to 1,000 times that in MIR. cse-experiment (+2%),
perf12 (+3.7%), perf16 (+0.9%) and perf17 (+2.8%) all failed this way.

A mid IR cannot pass as one more pass. It can only pay as a replacement of
the part of x64gen it takes over (1.15G today), with code that is enough
better to pay back the difference.

## 3. Mapping MIR's model onto obx

### 3.1 Input and placement

Input: the tree IR after `opt.optimize` and `loops.optimize`, per function,
in `buildNative` where `x64gen` takes it now. Inlining, value-record
splitting, header hoisting and the range facts stay on the tree at first:
they work, and they are paid for.

Output: machine instructions through `x64.obx`, as now. The ABI, frames,
trap stubs, runtime calls, constants and the link stay x64gen's.

Per function, either generator can run. Both follow the same ABI, so a
program can mix them. That allows a gradual start (functions with loops
first, or the ones the mid IR can lower) and a fallback switch for
bisecting (`OBX_MID=0`, and a per-function selector).

### 3.2 The instructions

Flat arrays per function, reused between functions (no linked lists, no
per-instruction allocation). An instruction is a value record of ints:
`op`, `ty`, `dst`, `a`, `b`, `c`, `block`, plus a side table for call
arguments and phi operands. Virtual registers are numbered per function.
Example:

```
fn sum(a: &[]int): int {
    var s = 0
    for i in 0..len(a) { s += a[i] }
    return s
}
```

after `loops` (header hoisted, bounds check proved), in SSA:

```
b0:  v1 = load.i64 [p0+8]        ; a.len      class: []int header, len
     v2 = load.ptr [p0+0]        ; a.data     class: []int header, data
     jmp b1
b1:  s1 = phi 0, s2
     i1 = phi 0, i2
     br.lt i1, v1, b2, b3
b2:  v3 = load.i64 [v2+i1*8]     ; class: element of []int
     s2 = addo s1, v3, trap t0   ; checked: overflow traps with t0's message
     i2 = add i1, 1              ; the for's i++ (proved by loops)
     jmp b1
b3:  ret s1
```

| group | ops | notes |
|---|---|---|
| constants, copies | `const`, `fconst`, `mov` | |
| integer | `add sub mul div rem and or xor shl shr sar neg not` | wrapping; 8 to 64 bits by `ty` |
| checked | `addo subo mulo` with a trap block; `chkidx i, n` returns i | the trap is an edge to a cold no-return block with the message (`w`) |
| compare, branch | `cmp.cc`, `br.cc a, b, then, else`, `jmp`, `switch` (table) | fused at selection |
| conversions | `sext zext trunc i2f f2i` | |
| float | `fadd fsub fmul fdiv fcmp` | f64 |
| memory | `load.ty [base+index*scale+disp]`, `store.ty`, `addr slot` | each with an alias class (3.6) |
| aggregates | `copy.n dst, src`, `fill.n` | records that stay in memory |
| calls | `call f, args`, `calli`, `rtcall k, args` | runtime calls carry their write set |
| exits | `ret`, `trap` | |
| SSA only | `phi` | |

`chkidx` returning its index is the one idea MIR does not have. A load
indexed by the result depends on the check, so no pass can move the load
above its check. The trap-order rule of `loops.obx` ("nothing changes which
traps happen, where or in what order") then holds by construction. A check
dominated by an identical check (same operands) is redundant: GVN removes
it, and nothing observable changes.

### 3.3 Structured control flow: CFG and SSA for almost nothing

The tree IR has no `goto`: `if`, `while`, `for`, `switch`/`case`, `break`,
`continue`, `return` (ir.obx `S_*` 40-52). Every CFG is reducible, and the
lowering knows each block's predecessors from the structure.

| step | MIR | obx with structured input |
|---|---|---|
| CFG | rebuilt from labels (`build_func_cfg` 1572) | made by the lowering |
| loop tree | natural loops found (`build_loop_tree` 1161) | the loop statements are the loops |
| dominators | dataflow (`calculate_dominators` 3442) | the if's block, the loop header, the switch block; a break-only exit needs one intersection of its predecessors |
| SSA | maximal phis at every join, then iterative minimization (`build_ssa` 2676, `minimize_ssa` 2324) | Braun et al. 2013 on the fly: an if's join is sealed at once, a loop header after the body and step, an exit after the body. Trivial phis go recursively; on reducible CFGs that gives minimal SSA |
| liveness | iterative dataflow (`solve_dataflow` 1934) | still dataflow, but in reverse postorder on a reducible CFG it settles in loop depth + 2 passes |

`ssaprobe.obx` does the Braun construction on the whole self-build:

| what | count |
|---|---:|
| mid instructions (each non-leaf expression, frame load, store) | 221,287 |
| blocks | 29,610 (7.5 instructions each) |
| SSA variables (scalar locals never address-taken) | 39,984 of 51,159 locals |
| variable reads | 262,277 |
| phis made / left after trivial-phi removal | 55,084 / 18,729 |
| cost | 306M instructions (4,473M to 4,780M): 1,385 per instruction |

The probe is deliberately naive. About half its time is its own code, the
other half `Map[int,int]` lookups, `malloc`/`memcpy` and freeing per-value
arrays. Current definitions in a per-variable array (the structured walk
visits blocks in order) and phi operands in one flat pool should cut it to
300-550 per instruction (65-120M).

### 3.4 obx constructs in the mid IR

| construct | today | in the mid IR |
|---|---|---|
| bounds checks | `rt_idx(i, n, w)`, inlined by x64gen | `chkidx` (3.2) |
| overflow checks | `rt_add_int(a, b, w)` and kin, `add; jo stub` | `addo`/`subo`/`mulo` + trap block |
| proved checks | `loops` already rewrote them (`rt_wadd`, plain i) | plain `add`, plain index |
| refcounts | `rt_str_dup`, drops, drop glue calls | calls. Their write set is the count word and the heap (the runtime write table of `loops.obx`), so GVN keeps loads of a string's bytes and length across them |
| str values | frame slots | frame slots at first; splitting a `str` into registers (perf15's first item) becomes a scalar-replacement step on the mid IR later |
| array headers | `a.data`, `a.len` loads, hoisted by `loops` | loads with a header class; GVN reuses them within the loop-free parts too |
| value records | split by `opt` into field locals | the field locals are vregs; the records left are frame slots, members are loads and stores at constant offsets |
| borrows | pointer parameters and locals declared to an address | pointer vregs; their roots give alias facts (3.6) |
| runtime calls | inlined helpers in x64gen (4757-5290) | `rtcall`s; the inline ones become selection templates |
| 128-bit values | x64gen handles them | not lowered at first: such functions stay with x64gen |
| inline asm, odd forms | x64gen | same rule: x64gen takes any function the lowering refuses |

### 3.5 What it replaces

`x64gen.obx` is 5,684 lines. By section:

| section | lines | with a mid IR |
|---|---:|---|
| build, types, frames | ~290 | kept |
| functions (genFunc, allocRegs) | ~510 | replaced by liveness and RA |
| simplification of the IR (scan, sites) | ~730 | replaced (lowering, copy propagation) |
| loads read again (remat) | ~150 | replaced (RA rematerialization) |
| statements, memory, initializers, expressions | ~2,190 | replaced by lowering and selection |
| calls | ~290 | moved into lowering and machinize |
| functions generated in place | ~330 | replaced (opt's inlining, or inlining on the mid IR) |
| inlined runtime functions | ~530 | kept as selection templates |
| constants, link | ~390 | kept |

About 3,900 lines replaced. `x64.obx` (946) and `elf.obx` (410) stay.
`loops.obx` (2,404) stays at first. Later, its header hoisting could become
LICM with alias classes, and its range facts sparse ranges on SSA. That
would be perf17's analysis at SSA cost instead of per-statement fact sets.

### 3.6 Alias information obx has and MIR does not

MIR gets alias names from the C front end and treats everything else as may
alias. obx's IR is typed and its language restricts aliasing:

- Every member access names a (struct, offset, size, type). Two accesses
  overlap only if their objects can be the same and their ranges meet.
  `loops.obx` already uses exactly this.
- Element accesses carry the element type; header accesses (`data`, `len`)
  are their own classes.
- Globals cannot be borrowed (SPEC 5.2, 10). A borrow parameter never points
  into a global, so a global store never touches a load through a borrow.
- At a call, a mutable borrow cannot overlap another argument's borrow or
  move (SPEC 10 rule 3). Inside the callee a `mut &T` parameter does not
  alias the other borrow parameters. Rule 3 also allows a shared read of a
  place held by an enclosing call's mutable borrow, so this must be checked
  against the checker before anything relies on it.
- A frozen root (SPEC 10 rule 4) cannot be written while a `let` borrow of it
  is in scope. That is invariance for free over the borrow's block.
- Calls carry write sets: program functions by summary, runtime functions by
  `loops.obx`'s table. "Precise effects are expensive" (PERFORMANCE.md): the
  summaries must be computed once per program and shared.
- Address-taken locals are known exactly (`E_ADDR`/`E_DECAY` roots).

With these, GVN can remove loads across stores and calls that MIR must
treat as clobbers. That is most of the 51,798 repeated pure loads.

## 4. Which passes first

Ranked for obx by the gap they close (2.3) against their cost.

| rank | pass | closes | evidence | needs SSA |
|---:|---|---|---|---|
| 1 | linear-scan RA with splitting, all 14 GPRs, caller-saved registers for values not live across calls, move coalescing by hints | stack loads and stores (13%), callee-saved saves (4%), the "evaluates into rax" wall | 2.3, perf16, perf18 | no |
| 2 | selection on the mid IR: addressing modes, compare and branch, `lea`, immediates | moves and reloads around every operation | gcc's mix: twice the reg-reg moves | no |
| 3 | GVN with alias classes: redundant loads, checks, headers | repeated loads and checks across statements | 51.8K repeated loads; gcc ablation 5.3%; cse-experiment's 1,063 reuses in straight lines | yes |
| 4 | copy propagation, DCE | cleanup after lowering and GVN | needed for 3 to pay | yes |
| 5 | shrink-wrapping through split ranges | saves on paths that need no registers | 589 of 1,314 functions save all five; perf19 tries it in x64gen now | no |
| 6 | LICM of loads with alias classes | what `loops.obx` already hoists | overlap | yes |
| 7 | sparse range facts | checks `loops` cannot reach (perf17's) | fib -23% cycles on perf17 | yes |
| 8 | DSE, block cloning, pressure relief, jump threading | small | MIR has them; no obx evidence | some |

1 and 2 together are MIR's `-O1` (no SSA at all). That is the first point
where a mid IR can produce code better than x64gen's. 3 and 4 are the
reason to build SSA, and they come after.

## 5. Cost

### 5.1 Lines

| part | obx lines (estimate) | MIR counterpart |
|---|---:|---|
| mid IR data, printer, verifier | 400-600 | parts of `mir.c` |
| lowering from the tree (checks, calls, records, switch) | 1,200-1,600 | (c2m's job) |
| CFG, dominators, loop tree from the structure | 150-250 | 1161, 1572, 3442 |
| Braun SSA, conventional SSA, out of SSA | 350-500 | 2134-2844 |
| GVN, memory classes, checks | 800-1,200 | 3374-4977 |
| copy propagation, DCE | 200-300 | 3062-3374, 5191-5273 |
| liveness, live ranges | 300-500 | 5935-6680 |
| coalescing hints (no matrix) | 150-300 | 6805-7125 |
| linear scan with splitting, rewrite | 900-1,400 | 7125-8651 |
| machinize, selection, emission through x64.obx | 1,500-2,200 | x86_64 695-2187, combine 8651-9245 |
| total | 6,000-8,500 | 13,300 (with JIT, debug and lazy-version code) |

Against that, about 3,900 lines of x64gen go once the mid IR covers every
function.

### 5.2 Compile time

Per mid instruction, in instructions of the compiler (estimates; the
measured anchors are x64gen 5.8K, MIR 11.4K/18.0K/26.5K, the naive Braun
probe 1.4K, and one tree walk in `opt` or `loops` at about 0.45K):

| part | estimate |
|---|---:|
| lowering | 600-1,000 |
| SSA construction (tuned) | 300-550 |
| GVN, copy propagation, DCE | 800-1,500 |
| out of SSA | 200-400 |
| liveness and ranges | 500-1,000 |
| RA with splitting | 1,500-3,000 |
| selection and emission | 2,000-3,000 |
| full pipeline | 6K-10K |
| without the SSA passes (MIR `-O1` shape) | 4.6K-8.4K |

On the self-build (221K mid instructions) the full pipeline is 1.3G-2.2G
against x64gen's 1.28G (with the assembler): +0 to +0.9G, 0% to +20% of
the self-build, with `opt` and `loops` kept as they are. At MIR's own
constants it would be +2.7G (`-O1`) to +4.6G (`-O2`): +60% to +105%.

The compile suite sees (1 + cost) × (1 - gain), the gain being the faster
compiler. With a gain of 8-12% the full pipeline nets between about -10%
and +10%. That is why it has to be measured, not argued.

Per function: the median function (69 operations, ~95 mid instructions) at
8K is under 1M instructions (~0.3 ms). The largest (3,057 operations) is
~30M if every pass is linear. MIR's two huge functions show what happens
when passes are not: every pass must be linear or n log n, with a size
above which a function falls back to the simple allocator or to x64gen.

### 5.3 Memory

One function at a time, arrays reused. The largest function is about 4,000
mid instructions: at 48 bytes each, 200 KB; liveness bitsets of 500 blocks
by 4,000 vregs are 250 KB. No conflict matrix (MIR's is up to 8 MB). The
tree IR itself (494K nodes of 64 bytes, 32 MB) stays the largest structure.

### 5.4 Correctness risk and testing

High: this is half a backend. What contains it:

- Per-function choice with x64gen as the fallback. `OBX_MID=0` turns it off;
  a selector by function name or index bisects a miscompile to one function
  automatically (build, run the failing test, halve the set).
- A verifier under `OBX_CHECK=1`: every block ends in one terminator, phi
  arity equals predecessor count, defs dominate uses, types match, trap
  blocks are no-return, no check moved or removed except by a dominating
  identical one.
- A printer (`OBX_MID=print`) for every stage, as `OBX_NATIVE_IR` today.
- The existing gate: native against C on 1,616 programs, the red-team
  corpora, the generated value-record and array-loop programs, the native
  self-build and its fixed point.
- New generated tests aimed at the RA: functions with more live values than
  registers, values live across calls, nested loops with splits at their
  edges, switch fallthrough.
- Optionally an interpreter for the mid IR (MIR has one), to test lowering
  apart from allocation. About 600 lines; only if miscompiles prove hard to
  place.

## 6. Staged plan

Each stage is a branch with a gate. Code goes to `main` only after a stage's
gate and a review.

**M0 (done, `ssa-probe`).** The IR counts and the Braun construction cost
above. Not for merging.

**M1: lowering and a static allocation model. No code generated.**
`mid.obx`: the data structure, lowering of every function the shape allows
(the rest flagged), the verifier and the printer; then liveness and a linear
scan with splitting that only decides, and reports, per function: values in
registers, spills and reloads weighted by loop depth (×8 per level, as
x64gen weighs), callee-saved registers used. x64gen reports the same for
its own choice (it knows its slots and saves). About 2,000 lines.

Gate M1:
- lowers ≥ 95% of the self-build's functions, and the gate's programs, with
  the verifier clean;
- lowering + liveness + allocation cost ≤ 3K instructions per mid
  instruction (≤ 660M on the self-build);
- the model predicts ≥ 30% fewer loop-weighted stack accesses and ≥ 30%
  fewer callee-saved saves than x64gen.

**M2: the first code.** Selection and emission from the allocated mid IR,
per function, functions with loops first, then all. No SSA (MIR's `-O1`).
Gate M2 (perfsuite against x64gen alone):
- the whole gate passes with `OBX_MID=1`;
- the run suite gains ≥ 3% (geomean, cycles agreeing) or the compiler's own
  self-build cycles gain ≥ 5%;
- the compile suite costs ≤ the budget the user sets (OPEN DECISION 3;
  suggested +3%).

**M3: SSA inside.** Braun construction, GVN with alias classes and checks,
copy propagation, DCE, conventional SSA and out of SSA, before machinize.
Each pass judged by "Judging a pass" against M2.

**M4: consolidation.** x64gen's replaced sections go once the mid IR takes
every function. Shrink-wrapping through split ranges. Then, one at a time
and each judged: LICM replacing `loops`'s hoisting, sparse range facts
(perf17's retry), `str` in registers (perf15).

**Stop criterion.** Stop, keep x64gen and shelve the branch with its numbers
in PERFORMANCE.md's "Tried and shelved" if:
- M1 misses its cost gate, or its model predicts less than 30% fewer
  weighted stack accesses (a real allocator would not then pay for
  selection and emission);
- M2 misses its run gain after one tuning round;
- any stage runs past twice its line estimate before its gate passes.

## 7. Alternatives

| option | what | for | against |
|---|---|---|---|
| A. local tree work | keep x64gen. Shrink-wrapping (perf19 now), caller-saved registers for locals not live across calls, a few values kept out of rax within one statement | cheap; each is judged alone | each one hits the 10M budget wall (2.4); the "evaluates into rax" design caps what remains |
| B. MIR itself | emit MIR from the tree IR, link MIR's C, generate with `MIR_gen` | proven code quality (0.91 of gcc) | C with libc in a freestanding self-hosted compiler; JIT output, no relocatable code for the ELF writer; no trap semantics; +60% to +105% self-build at its constants (1.5) |
| B'. translate MIR's algorithms | write the mid IR in obx after MIR's passes (RA, splitting, GVN) | a tested design to follow | MIT: keep MIR's copyright and permission notice in each file that translates its code, and add MIR to NOTICE. Its data structures (lists, bitmaps everywhere) are the slow part and should not be copied |
| C. QBE | SSA IR in, assembly text out | small (C, about ten thousand lines) | needs an assembler and linker; code at 0.65 of gcc (cproc, README) is below what native already reaches; C again |
| D. LLVM | | best code | a huge C++ dependency; compile time in gcc's class (gcc -O2 is 109× MIR on sieve, README) |
| E. the C backend | `obxc` without `--native`: C through gcc -O2 | exists today; it is the peak-speed option (the native-built compiler takes 24% more cycles than this build) | needs gcc; 17 s for the compiler |

E matters for the decision. obx already has a peak-speed path. The native
backend's case is compile speed and no toolchain. A mid IR trades some of
the first for run speed; the question is how much of each the user wants
from `--native`.

## 8. Recommendation

1. Do not add a mid IR as a default pass now. The prize on large programs
   is 8-12% of cycles; on the benchmarks native already matches gcc. The
   estimated compile cost runs from a wash to +20%, and MIR's constants say
   +60% or more. Under "Judging a pass" it is likely to fail as a default.
2. If the user wants more run speed from `--native` than tree-level work can
   give (2.4 says that work is nearly exhausted), the route is the MIR
   model: a linear IR with vregs, the allocator first (rank 1 and 2), SSA
   only for GVN and its cleanup afterwards. Start with M1. It costs about
   2,000 lines, generates no code and answers the two unknowns: the cost
   per instruction in obx, and how much stack traffic a real allocator
   would remove. Decide on M2 from M1's numbers.
3. Behind an optimization level, the trade becomes the user's per build:
   MIR's own answer (`-O0` to `-O2`). That changes the gates (OPEN
   DECISION 1).
4. Whatever the decision: the structured tree makes the SSA side cheap
   (sealed Braun, dominators and loops from the structure). The cost is in
   liveness, allocation and selection, as in MIR.

## 9. OPEN DECISIONS

1. **Levels.** Should a mid IR be the default `--native`, or only behind an
   optimization level (`--native -O2`, `OBX_OPT=2`) with x64gen as the fast
   default? Recommended: a level, if built at all.
2. **Go or stop.** Build M1 now, after perf19 and the current work, or not at
   all? Recommended: M1 only if run speed from `--native` is a goal for the
   coming months; otherwise stop here.
3. **Budget.** "Judging a pass" (≤ 1% compile cost) is written for add-on
   passes. What may a backend replacement cost on the compile suite: 0%, 3%,
   or more behind a level? Suggested: +3% at the default level, more behind
   `-O2`.
4. **Replace or add.** Should the mid IR replace x64gen in the end (M4), or
   stay a second generator for the functions where it wins (loops, large
   functions)? Recommended: replace, to keep one generator; two cost twice
   the maintenance.
5. **Write or translate.** Write it in obx after MIR's algorithms (MIT,
   notice kept per file and in NOTICE) or from the papers (Braun et al.
   2013; Poletto and Sarkar's linear scan; Wimmer and Mössenböck 2005 for
   interval splitting)?
   Recommended: the papers for SSA and GVN, MIR as the reference for
   splitting and its x86-64 patterns.
6. **Allocation on SSA or not.** MIR allocates after leaving SSA. An SSA
   allocator (chordal graphs, Hack) is the other road. Recommended: MIR's
   model; it lets M2 run without SSA.
7. **The tree passes.** Keep `opt` and `loops` on the tree (recommended for
   M1-M3), or plan to move hoisting and the range facts into the mid IR (M4,
   each judged)?

## Reproducing

On branch `ssa-probe` (`ad0a150`), from `obx/compiler`:

    OBX_LIB=../lib ./out/obxc main.obx --native -o probec
    OBX_PROBE=1 ./probec main.obx --native -o x 2> counts.tsv
    OBX_PROBE=ssa perf stat -e instructions:u ./probec main.obx --native -o x
    perf stat -e instructions:u ./probec main.obx --native -o x     # the baseline

MIR, in a scratch copy only (`make c2m`; then `c2m -c file.c` and the
`gendrv.c` driver of the scratch directory, built against `libmir.a`):

    perf stat -e instructions:u ./gendrv c2mir.bmir 2     # level 2; -1: load and link only

The gcc variants: `gcc -O2 -w -std=gnu11 seed/obxc.c -lm`, with the `-fno-*`
flags of 2.3 for the second row.
