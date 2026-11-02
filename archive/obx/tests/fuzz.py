"""Differential testing: random expression programs, with the expected output
computed independently in Python. Includes bitwise operators, shifts (count
taken mod 64), byte truncation and character literals.

Covers constant folding, immediate operands, register allocation, registers
saved across calls, memory operands (locals, globals, array elements, record
fields), division and remainder semantics, 64-bit wraparound, short-circuit
evaluation order (counted through a side-effecting tick()), condition jump
chains and materialised booleans."""
import random

MIN, MAX = -2 ** 63, 2 ** 63 - 1
SPECIAL = [0, 1, 2, -1, -2, 3, 7, 10, -7, 100, -100, 127, 128, -128, -129, 255,
           2 ** 31 - 1, 2 ** 31, -2 ** 31, -2 ** 31 - 1, 2 ** 32 - 1, 2 ** 32,
           2 ** 40 + 3, 2 ** 62, MAX, MIN, MIN + 1]


def wrap(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def lit(v):
    if v == MIN:
        return "(-9223372036854775807 - 1)"
    return f"({v})" if v < 0 else str(v)


class Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        r = self.r
        self.env = {n: self.value() for n in ('a', 'b', 'c', 'd', 'g')}
        self.arr = [self.value() for _ in range(3)]
        self.rec = self.value()

    def value(self):
        r = self.r
        return r.choice(SPECIAL) if r.random() < 0.5 else r.randint(-1000, 1000)

    def leaf(self):
        r = self.r
        k = r.randrange(6)
        if k == 0:
            v = self.value()
            return lit(v), v
        if k <= 2:
            n = r.choice('abcdg')
            return n, self.env[n]
        if k == 3:
            i = r.randrange(3)
            return f"arr[{i}]", self.arr[i]
        if k == 4:
            return "rec.f", self.rec
        if r.random() < 0.3:
            ch = r.choice('az09AZ_ ~')
            return f"'{ch}'", ord(ch)
        n = r.choice('abcd')
        return f"id({n})", self.env[n]

    def int_expr(self, depth):
        r = self.r
        if depth == 0 or r.random() < 0.25:
            return self.leaf()
        k = r.randrange(11)
        if k == 0:
            t, v = self.int_expr(depth - 1)
            return f"-{t}" if not t.startswith('-') else f"-({t})", wrap(-v)
        if k == 9:
            t, v = self.int_expr(depth - 1)
            return f"~{t}" if not t.startswith('~') else f"~({t})", wrap(~v)
        if k == 10:
            t, v = self.int_expr(depth - 1)
            return f"int(byte({t}))", v & 255
        if k == 1:
            t, v = self.int_expr(depth - 1)
            return f"id({t})", v
        if k == 2:
            (t1, v1), (t2, v2) = self.int_expr(depth - 1), self.int_expr(depth - 1)
            return f"add2({t1}, {t2})", wrap(v1 + v2)
        op = r.choice(['+', '-', '*', '+', '-', '*', '/', '%', '&', '|', '^', '<<', '>>'])
        for _ in range(20):
            (t1, v1), (t2, v2) = self.int_expr(depth - 1), self.int_expr(depth - 1)
            if op in '/%' and (v2 == 0 or (v1 == MIN and v2 == -1)):
                continue
            if op == '+':
                v = wrap(v1 + v2)
            elif op == '-':
                v = wrap(v1 - v2)
            elif op == '*':
                v = wrap(v1 * v2)
            elif op == '/':
                v = tdiv(v1, v2)
            elif op == '%':
                v = v1 - tdiv(v1, v2) * v2
            elif op == '&':
                v = wrap(v1 & v2)
            elif op == '|':
                v = wrap(v1 | v2)
            elif op == '^':
                v = wrap(v1 ^ v2)
            elif op == '<<':
                v = wrap(v1 << (v2 & 63))
            else:
                v = v1 >> (v2 & 63)
            return f"({t1} {op} {t2})", v
        return self.leaf()

    def bool_expr(self, depth):
        """Returns (text, node); node is evaluated later with short-circuiting."""
        r = self.r
        if depth == 0 or r.random() < 0.3:
            k = r.randrange(5)
            if k == 0:
                b = r.random() < 0.5
                return ("true" if b else "false"), ('lit', b)
            if k == 1:
                t, n = self.bool_expr(0)
                return f"tick({t})", ('tick', n)
            (t1, v1), (t2, v2) = self.int_expr(2), self.int_expr(2)
            op = r.choice(['==', '!=', '<', '<=', '>', '>='])
            res = {'==': v1 == v2, '!=': v1 != v2, '<': v1 < v2, '<=': v1 <= v2,
                   '>': v1 > v2, '>=': v1 >= v2}[op]
            return f"{t1} {op} {t2}", ('lit', res)
        k = r.randrange(4)
        if k == 0:
            t, n = self.bool_expr(depth - 1)
            return f"!({t})", ('not', n)
        if k == 1:
            t, n = self.bool_expr(depth - 1)
            return f"tick({t})", ('tick', n)
        (t1, n1), (t2, n2) = self.bool_expr(depth - 1), self.bool_expr(depth - 1)
        op = '&&' if k == 2 else '||'
        return f"({t1} {op} {t2})", (op, n1, n2)


def evaluate(node, ticks):
    kind = node[0]
    if kind == 'lit':
        return node[1]
    if kind == 'not':
        return not evaluate(node[1], ticks)
    if kind == 'tick':
        v = evaluate(node[1], ticks)
        ticks[0] += 1
        return v
    if kind == '&&':
        return evaluate(node[1], ticks) and evaluate(node[2], ticks)
    return evaluate(node[1], ticks) or evaluate(node[2], ticks)


def program(seed, n_int=50, n_bool=30):
    g = Gen(seed)
    r = g.r
    out = []
    src = ["module fuzz",
           f"var g = {lit(g.env['g'])}",
           "var ticks = 0",
           "type R = ref record { f: int }",
           "fn id(x: int): int { return x }",
           "fn add2(x, y: int): int { return x + y }",
           "fn tick(v: bool): bool {", "    ticks += 1", "    return v", "}",
           "fn main() {"]
    for n in 'abcd':
        src.append(f"    {'let' if n in 'ac' else 'var'} {n} = {lit(g.env[n])}")
    src.append("    let arr = new []int(3)")
    for i, v in enumerate(g.arr):
        src.append(f"    arr[{i}] = {lit(v)}")
    src.append(f"    let rec = new R{{f: {lit(g.rec)}}}")
    for i in range(n_int):
        t, v = g.int_expr(r.randint(1, 4))
        kind = r.randrange(4)
        if kind == 0:
            src.append(f"    println({t})")
        elif kind == 1:
            src.append(f"    let v{i} = {t}")
            src.append(f"    println(v{i})")
        elif kind == 2:
            target = r.choice(['arr[1]', 'rec.f', 'g'])
            src.append(f"    {target} = {t}")
            src.append(f"    println({target})")
            if target == 'arr[1]':
                g.arr[1] = v
            elif target == 'rec.f':
                g.rec = v
            else:
                g.env['g'] = v
        else:
            o = r.choice(['+=', '-=', '*='])
            old = g.env['b']
            nv = wrap(old + v if o == '+=' else old - v if o == '-=' else old * v)
            src.append(f"    b {o} {t}")
            src.append(f"    println(b)")
            g.env['b'] = nv
            v = nv
        out.append(str(v))
    for i in range(n_bool):
        t, node = g.bool_expr(r.randint(1, 4))
        ticks = [0]
        v = evaluate(node, ticks)
        src.append("    ticks = 0")
        if r.random() < 0.5:
            src.append(f"    if {t} {{ println(\"T\") }} else {{ println(\"F\") }}")
            out.append("T" if v else "F")
        else:
            src.append(f"    let t{i} = {t}")
            src.append(f"    println(t{i})")
            out.append("true" if v else "false")
        src.append("    println(ticks)")
        out.append(str(ticks[0]))
    src.append("}")
    return '\n'.join(src) + '\n', out


def check(seed, build, run_exe, Fail):
    src, expect = program(seed)
    try:
        exe = build(src, f"fuzz{seed}")
    except Exception as e:
        raise Fail(f"did not compile: {e}\n(regenerate with: python3 tests/fuzz.py {seed})")
    code, out, err = run_exe(exe)
    got = out.splitlines()
    if code != 0:
        raise Fail(f"exit code {code}: {err.strip()}")
    if got != expect:
        lines = src.splitlines()
        for i, (e, gv) in enumerate(zip(expect, got)):
            if e != gv:
                prints = [l for l in lines if 'println(' in l]
                raise Fail(f"output {i + 1}: expected {e}, got {gv}\n"
                           f"near: {prints[i].strip() if i < len(prints) else '?'}\n"
                           f"(regenerate with: python3 tests/fuzz.py {seed})")
        raise Fail(f"expected {len(expect)} lines, got {len(got)}")


if __name__ == '__main__':
    import sys
    print(program(int(sys.argv[1]) if len(sys.argv) > 1 else 1)[0])
