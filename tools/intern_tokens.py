"""One-off refactoring: turns obxc's token kinds from strings into integer
constants, so the scanner and parser can use integer match. Run once; the
stage tests then confirm the compiler's output is unchanged."""
import re
import sys

path = sys.argv[1]
s = open(path).read()

SPECIAL = ['eof', 'ident', 'int', 'char', 'string']
KEYWORDS = ('module import pub type var let const fn return if else while for in break continue defer '
            'match ref record closed new nil true false').split()
PUNCT = [('(', 'LPAREN'), (')', 'RPAREN'), ('{', 'LBRACE'), ('}', 'RBRACE'), ('[', 'LBRACK'), (']', 'RBRACK'),
         (',', 'COMMA'), (';', 'SEMI'), (':', 'COLON'), ('.', 'DOT'), ('..', 'DOTDOT'), ('=', 'ASSIGN'),
         ('==', 'EQ'), ('!=', 'NE'), ('<', 'LT'), ('<=', 'LE'), ('>', 'GT'), ('>=', 'GE'), ('+', 'PLUS'),
         ('-', 'MINUS'), ('*', 'STAR'), ('/', 'SLASH'), ('%', 'PERCENT'), ('!', 'NOT'), ('?', 'QUESTION'),
         ('&', 'AMP'), ('|', 'BAR'), ('^', 'CARET'), ('~', 'TILDE'), ('&&', 'ANDAND'), ('||', 'OROR'),
         ('+=', 'PLUSEQ'), ('-=', 'MINUSEQ'), ('*=', 'STAREQ'), ('<<', 'SHL'), ('>>', 'SHR')]
CONST = {k: 'T_' + k.upper() for k in SPECIAL + KEYWORDS}
CONST.update({p: 'T_' + n for p, n in PUNCT})
ORDER = SPECIAL + KEYWORDS + [p for p, _ in PUNCT]


def lit_to_const(m):
    word = m.group(2)
    if word not in CONST:
        raise SystemExit(f"no token constant for {word!r} in: {m.group(0)}")
    return f'{m.group(1)}{CONST[word]}'


def functions(src, names):
    """yields (start, end) of each named top-level function"""
    for name in names:
        m = re.search(rf'^fn {name}\(', src, re.M)
        if not m:
            raise SystemExit(f"function {name} not found")
        nxt = re.search(r'^(fn |// =|type |let |var |const )', src[m.end():], re.M)
        yield m.start(), m.end() + (nxt.start() if nxt else len(src) - m.end())


def scoped(src, names, pattern, repl):
    spans = sorted(functions(src, names), reverse=True)
    for a, b in spans:
        src = src[:a] + re.sub(pattern, repl, src[a:b]) + src[b:]
    return src


# 1. comparisons of the current token, everywhere
s = re.sub(r'(\bsym (?:==|!=) )"([^"]*)"', lit_to_const, s)
# 2. token variables k and o, only in the functions where they hold tokens
TOKEN_FNS = ['factor', 'statement', 'compileModule', 'semiAfter', 'isMulOp', 'isAddOp', 'term', 'simpleExpr',
             'relation', 'assignmentOrCall', 'relCC', 'aluCode', 'fold', 'binop', 'isRel']
s = scoped(s, TOKEN_FNS, r'(\b[kos] (?:==|!=) )"([^"]*)"', lit_to_const)
# 3. expect("x", ...) and setTok("x")
s = re.sub(r'(\bexpect\()"([^"]*)"', lit_to_const, s)
s = re.sub(r'(\bsetTok\()"([^"]*)"', lit_to_const, s)
# 4. types of token variables and parameters
for old, new in [('var last = ""', 'var last = -1'), ('var sym = ""', 'var sym = -1'),
                 ('fn setTok(k: string)', 'fn setTok(k: int)'), ('fn semiAfter(k: string)', 'fn semiAfter(k: int)'),
                 ('fn expect(k: string, what: string)', 'fn expect(k: int, what: string)'),
                 ('fn isRel(s: string)', 'fn isRel(s: int)'), ('fn relCC(o: string)', 'fn relCC(o: int)'),
                 ('fn isMulOp(k: string)', 'fn isMulOp(k: int)'), ('fn isAddOp(k: string)', 'fn isAddOp(k: int)'),
                 ('fn aluCode(o: string)', 'fn aluCode(o: int)'), ('fn fold(o: string,', 'fn fold(o: int,'),
                 ('fn binop(o: string,', 'fn binop(o: int,')]:
    assert s.count(old) == 1, old
    s = s.replace(old, new)
# 5. messages that print a token kind
for old, new in [('''error("'" + o + "' needs ints")''', '''error("'" + tokName(o) + "' needs ints")'''),
                 ('''error("'" + o + "' needs an int")''', '''error("'" + tokName(o) + "' needs an int")'''),
                 ('''error("'" + k + "' outside a loop")''', '''error("'" + tokName(k) + "' outside a loop")'''),
                 ('''error("'" + k + "' needs an int")''', '''error("'" + tokName(k) + "' needs an int")'''),
                 ('        var got = sym\n', '        var got = tokName(sym)\n'),
                 ('''if len(w) == 0 { w = "'" + k + "'" }''', '''if len(w) == 0 { w = "'" + tokName(k) + "'" }'''),
                 ('        print(tline, ":", tcol, " ", sym)', '        print(tline, ":", tcol, " ", tokName(sym))'),
                 ('        varDecl(k, false)', '        varDecl(tokName(k), false)'),
                 ('            varDecl(k, true)', '            varDecl(tokName(k), true)')]:
    n = s.count(old)
    assert n >= 1, old
    s = s.replace(old, new)
# 6. scanner: keywords and punctuation produce token numbers
s = s.replace('''        sval = text(start, pos)
        if isKeyword(sval) {
            setTok(sval)
        } else {
            setTok(T_IDENT)
        }''', '''        sval = text(start, pos)
        setTok(keywordTok(sval))''')
s = s.replace('''        let p = punct2(c, peek(1))
        if len(p) > 0 {
            advance()
            advance()
            setTok(p)
        } else if isPunct1(c) {
            advance()
            setTok(text(pos - 1, pos))
        }''', '''        let p = punct2(c, peek(1))
        let p1 = punct1(c)
        if p >= 0 {
            advance()
            advance()
            setTok(p)
        } else if p1 >= 0 {
            advance()
            setTok(p1)
        }''')
# 7. the constants and the name table, ahead of the scanner
consts = '\n'.join(f'const {CONST[k]} = {i}' for i, k in enumerate(ORDER))
names = ' '.join(ORDER)
table = f'''// token kinds
{consts}

fn makeTokNames(): []string {{
    let k = new []string(0)
    let names = "{names}"
    var start = 0
    for i in 0..len(names) + 1 {{
        if i == len(names) || names[i] == ' ' {{
            append(k, substr(names, start, i))
            start = i + 1
        }}
    }}
    return k
}}

let tokNames = makeTokNames()

fn tokName(k: int): string {{ return tokNames[k] }}

'''
anchor = '// ================================================================ scanner\n'
assert s.count(anchor) == 1
s = s.replace(anchor, anchor + '\n' + table)
open(path, 'w').write(s)
left = re.findall(r'\b(?:sym|setTok\(|expect\()[^\n]{0,4}"', s)
print("done; remaining string token uses:", left)
