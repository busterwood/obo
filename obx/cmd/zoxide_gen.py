#!/usr/bin/env python3
"""Generates cmd/zoxide_init.obx from the askama templates of zoxide 0.9.9.

    python3 obx/cmd/zoxide_gen.py ~/.cargo/registry/src/*/zoxide-0.9.9/templates > obx/cmd/zoxide_init.obx

The subset of askama the templates use is translated to obx statements that
append to a StrBuf: text (with the `-` whitespace control of tags), `{{ x }}`
for a literal, a `let` constant, `cmd` or `cmd.unwrap_or("..")`, `if` with
`else if`, `match cmd` / `match hook`, `if let Some(cmd) = cmd`, and `let`.
`cfg!(windows)` is false. Anything else stops the generator.
"""
import re
import sys

SHELLS = ["bash", "elvish", "fish", "nushell", "posix", "powershell", "tcsh", "xonsh", "zsh"]
BODY = r'((?:"(?:[^"\\]|\\.)*"|.)*?)'
TAG = re.compile(r"\{\{(-?)" + BODY + r"(-?)\}\}|\{%(-?)" + BODY + r"(-?)%\}|\{#(-?)(.*?)(-?)#\}", re.S)


def tokenize(src):
    toks = []
    pos = 0
    for m in TAG.finditer(src):
        if m.start() > pos:
            toks.append(["text", src[pos:m.start()]])
        if m.group(0).startswith("{#"):
            toks.append(["comment", "", m.group(7) == "-", m.group(9) == "-"])
        elif m.group(0).startswith("{{"):
            toks.append(["expr", m.group(2).strip(), m.group(1) == "-", m.group(3) == "-"])
        else:
            toks.append(["stmt", m.group(5).strip(), m.group(4) == "-", m.group(6) == "-"])
        pos = m.end()
    if pos < len(src):
        toks.append(["text", src[pos:]])
    # whitespace control
    for i, t in enumerate(toks):
        if t[0] == "text":
            continue
        if t[2] and i > 0 and toks[i - 1][0] == "text":
            toks[i - 1][1] = toks[i - 1][1].rstrip()
        if t[3] and i + 1 < len(toks) and toks[i + 1][0] == "text":
            toks[i + 1][1] = toks[i + 1][1].lstrip()
    out = []
    for t in toks:
        if t[0] == "comment" or (t[0] == "text" and t[1] == ""):
            continue
        out.append(t)
    # askama drops one final newline of the template
    if out and out[-1][0] == "text" and out[-1][1].endswith("\n"):
        out[-1][1] = out[-1][1][:-1]
    return out


def lit(s):
    r = []
    for ch in s:
        if ch == "\n":
            r.append("\\n")
        elif ch == '"':
            r.append('\\"')
        elif ch == "\\":
            r.append("\\\\")
        elif ch == "\t":
            r.append("\\t")
        elif ord(ch) < 32:
            raise SystemExit("control character in template")
        else:
            r.append(ch)
    return '"' + "".join(r) + '"'


class Gen:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0
        self.lines = []
        self.consts = {}
        self.vars = set()

    def emit(self, depth, s):
        self.lines.append("    " * depth + s)

    def text(self, depth, s):
        parts = s.split("\n")
        for k, p in enumerate(parts):
            piece = p + ("\n" if k < len(parts) - 1 else "")
            if piece:
                self.emit(depth, "b.add(" + lit(piece) + ")")

    def cond(self, c):
        c = c.strip()
        if c == "cfg!(windows)":
            return False
        if c == "echo":
            return "o.echo"
        if c == "resolve_symlinks":
            return "o.resolveSymlinks"
        m = re.fullmatch(r"hook\s*(==|!=)\s*InitHook::(\w+)", c)
        if m:
            name = {"None": "Off", "Prompt": "Prompt", "Pwd": "Pwd"}[m.group(2)]
            return "o.hook %s Hook.%s" % (m.group(1), name)
        raise SystemExit("condition: " + c)

    def block(self, depth, stops):
        """Generates tokens until a statement starting with one of stops."""
        while self.i < len(self.toks):
            t = self.toks[self.i]
            if t[0] == "text":
                self.text(depth, t[1])
                self.i += 1
            elif t[0] == "expr":
                self.expr(depth, t[1])
                self.i += 1
            else:
                kw = t[1].split()[0]
                if kw in stops:
                    return
                self.stmt(depth, t[1])
        if stops:
            raise SystemExit("unterminated block")

    def expr(self, depth, e):
        if e in self.consts:
            self.text(depth, self.consts[e])
        elif e in self.vars:
            self.emit(depth, "b.add(%s)" % e)
        elif e == "cmd":
            self.emit(depth, "b.add(cmd)")
        elif re.fullmatch(r'cmd\.unwrap_or\("(.*)"\)', e):
            d = re.fullmatch(r'cmd\.unwrap_or\("(.*)"\)', e).group(1)
            self.emit(depth, "b.add(o.cmd.unwrapOr(%s))" % lit(d))
        elif re.fullmatch(r'"((?:[^"\\]|\\.)*)"', e):
            self.text(depth, bytes(e[1:-1], "utf8").decode("unicode_escape"))
        else:
            raise SystemExit("expression: " + e)

    def stmt(self, depth, s):
        t = self.toks[self.i]
        kw = s.split()[0]
        if kw == "let":
            m = re.fullmatch(r'let (\w+) = "((?:[^"\\]|\\.)*)"', s)
            if m and m.group(1) not in self.vars:
                self.consts[m.group(1)] = bytes(m.group(2), "utf8").decode("unicode_escape")
                self.i += 1
            elif m:
                self.emit(depth, '%s = "%s"' % (m.group(1), m.group(2)))
                self.i += 1
            else:
                m = re.fullmatch(r"let (\w+)", s)
                if not m:
                    raise SystemExit("let: " + s)
                self.vars.add(m.group(1))
                self.emit(depth, 'var %s = ""' % m.group(1))
                self.i += 1
        elif kw == "if":
            self.ifchain(depth)
        elif kw == "match":
            self.match(depth)
        else:
            raise SystemExit("statement: " + s)

    def ifchain(self, depth):
        s = self.toks[self.i][1]
        if s.startswith("if let Some(cmd) = cmd"):
            self.i += 1
            self.emit(depth, "match o.cmd {")
            self.emit(depth + 1, "Some(cmd) {")
            self.block(depth + 2, ("endif", "else"))
            self.emit(depth + 1, "}")
            if self.toks[self.i][1].split()[0] == "else":
                self.i += 1
                self.emit(depth + 1, "None {")
                self.block(depth + 2, ("endif",))
                self.emit(depth + 1, "}")
            else:
                self.emit(depth + 1, "None {}")
            self.i += 1
            self.emit(depth, "}")
            return
        first = True
        # collect branches as (cond, tokens-range) by generating into sub-generators
        emitted_any = False
        static_done = False
        branches = []  # (cond or None, lines)
        cond = self.cond(s[2:].strip())
        self.i += 1
        while True:
            saved = self.lines
            self.lines = []
            self.block(depth + 1, ("endif", "else"))
            body = self.lines
            self.lines = saved
            branches.append((cond, body))
            t = self.toks[self.i][1]
            if t == "endif":
                self.i += 1
                break
            rest = t[4:].strip()
            self.i += 1
            if rest.startswith("if "):
                cond = self.cond(rest[3:])
            else:
                cond = None
        # drop statically false branches; a static true is not used
        out = [(c, b) for c, b in branches if c is not False]
        if not out:
            return
        for k, (c, b) in enumerate(out):
            if k == 0:
                if c is None:
                    self.lines.extend(l[4:] for l in b)
                    return
                self.emit(depth, "if %s {" % c)
            elif c is None:
                self.emit(depth, "} else {")
            else:
                self.emit(depth, "} else if %s {" % c)
            self.lines.extend(b)
        self.emit(depth, "}")

    def match(self, depth):
        s = self.toks[self.i][1]
        what = s.split()[1]
        self.i += 1
        if what == "cmd":
            self.emit(depth, "match o.cmd {")
            names = {"Some": "Some(cmd)", "None": "None"}
        else:
            assert what == "hook", s
            self.emit(depth, "match o.hook {")
            names = {"InitHook::None": "Hook.Off", "InitHook::Prompt": "Hook.Prompt", "InitHook::Pwd": "Hook.Pwd"}
        # skip whitespace-only text before the first when
        while self.toks[self.i][0] == "text" and self.toks[self.i][1].strip() == "":
            self.i += 1
        while True:
            t = self.toks[self.i]
            if t[0] == "stmt" and t[1] == "endmatch":
                self.i += 1
                break
            assert t[0] == "stmt" and t[1].startswith("when "), t
            w = t[1][5:].strip()
            w = re.sub(r" with \(cmd\)$", "", w)
            self.i += 1
            self.emit(depth + 1, names[w] + " {")
            self.block(depth + 2, ("when", "endmatch"))
            self.emit(depth + 1, "}")
        self.emit(depth, "}")


def main():
    d = sys.argv[1]
    print("// Generated by cmd/zoxide_gen.py from the shell templates of zoxide 0.9.9; do not edit.")
    print("// render(shell, opts) is `zoxide init <shell>` without its final newline.")
    print()
    print("pub type Hook = value closed { Off; Prompt; Pwd }")
    print()
    print("pub type InitOpts = record {")
    print("    pub cmd: Option[str]       // the prefix of z and zi, None for --no-cmd")
    print("    pub hook: Hook")
    print("    pub echo: bool             // _ZO_ECHO=1")
    print("    pub resolveSymlinks: bool  // _ZO_RESOLVE_SYMLINKS=1")
    print("}")
    print()
    print("pub fn shells(): []str { return [%s] }" % ", ".join('"%s"' % s for s in SHELLS))
    print()
    print("pub fn render(shell: str, o: &InitOpts): str {")
    print("    var b = newStrBuf()")
    print("    match shell {")
    for s in SHELLS:
        print('        "%s" { %s(o, mut &b) }' % (s, s))
    print('        else { panic("zoxide_init: unknown shell") }')
    print("    }")
    print("    return b.toStr()")
    print("}")
    for s in SHELLS:
        src = open("%s/%s.txt" % (d, s), encoding="utf8").read()
        g = Gen(tokenize(src))
        g.block(1, ())
        print()
        print("fn %s(o: &InitOpts, b: mut &StrBuf) {" % s)
        print("\n".join(g.lines))
        print("}")


main()
