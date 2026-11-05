#!/usr/bin/env python3
"""The differential test for cmd/jq.obx against the real jq 1.7.1.

    python3 obx/tests/jq.py [--c] [-k PATTERN] [-v] [-j N] [--ref PATH] [--exe PATH]
                            [--jqtest] [--jqtest-file PATH] [--list]

Our jq is built with compiler/out/obxc (native, or through C with --c) and
run with OBX_CHECK=1. The reference is --ref, else $JQ_REF, else `jq` on PATH
if that is jq 1.7.1; without one the script exits 2. Build it into a scratch
directory from the release tarball (./configure --with-oniguruma=builtin
--prefix=/tmp/jq-ref; make install), never over the system's jq.

Each case runs both programs with the same arguments, standard input and
files (in a fresh directory, with TZ=UTC and LC_ALL=C) and wants the same
exit status, standard output and standard error. Two things are normalised:

  - the program's own path, which jq prints in usage and option errors, is
    replaced by `jq`;
  - the text after "Regex failure: " (Oniguruma's message; cmd/jq.obx uses
    lib/regex and words only the common errors as Oniguruma does).

PROGRAMS are run as `jq -nc PROGRAM` with no input; CLI cases give the
arguments, input and files.

--jqtest also runs jq's own tests/jq.test (fetched from GitHub into a
temporary directory at test time, or --jqtest-file) through the command line:
each test's program is run with -c on its input, and the outputs are compared
as values (jq's jv_equal: numbers by value, objects regardless of key order).
%%FAIL tests want exit status 3 and, unless IGNORE MSG, jq's first error
line. Tests that use modules (import, include, modulemeta, -L) are counted
apart, as cmd/jq.obx has no modules. The pass rate is reported; it does not
change the exit status unless --jqtest-strict.
"""
import argparse
import concurrent.futures
import decimal
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
OBXC = os.environ.get('OBXC') or os.path.join(OBX, 'compiler', 'out', 'obxc')
JQ_TEST_URL = 'https://raw.githubusercontent.com/jqlang/jq/jq-1.7.1/tests/jq.test'

# Programs run as `jq -nc PROGRAM`, one per line.
PROGRAMS = r'''
1|.a
"x"|.[0]
{}|.[0]
[]|.["a"]
null|.[1:2]
{}|.[1:2]
1|.[]
{}+1
[]-{}
"a"*{}
{}/1
"a"/0
1/0
1%0
5%2.5
5.5%2
[]|implode
1|keys
1|length
true|length
{} | has(1)
[] | has("a")
"abc"|test(1)
error("x")
error({"a":1})
error(null)
error
{a:1}|error
"x"|tonumber
[1]|join(",")
[{}]|join(",")
[1,2]|.[1.5]
[1,2]|.[nan]
{}|.[null]
null|.a.b
"abc"|.[1:]
[1,2,3]|.[-2:]
[] | first
"a"|ltrimstr(1)
[3,1]|sort_by(-.)
"x"|fromjson
"[1,2"|fromjson
[1]|tostring
{"a":[1,2]}|tojson
"x"|@base64d
[1,"a",null]|@csv
[[1]]|@csv
["a\tb"]|@tsv
"<&>'\""|@html
"a b/ü"|@uri
["a'b", 1]|@sh
{}|@sh
infinite
nan
[nan]|sort
nan < 1
[1,nan]|min
$__loc__
input
"abc"|split("")
1|getpath(["a"])
65|implode
"aé"|explode
"aé"|length
"aé"|utf8bytelength
"\(1)\("a")\([])"
@base64 "x\(1)y\("ü")"
limit(0;1,2)
limit(-1;1,2)
first(empty)
isempty(empty)
halt_error
"bye\n"|halt_error
{}|halt_error(1)
{"a":1}|halt_error
1 as [$a] | $a
{} as [$a] | $a
[1] as {a:$a} | $a
. as [$a, $b, {c: $c}] | [$a,$b,$c]
[[1,2]] | .[] as [$a] ?// $a | $a
[1,2,3] | .[1:] = ["x"]
[1,2,3] | .[1:] |= map(.*2)
{"a":[1,2]} | del(.a[0])
[1,2,3,4] | del(.[1,2])
{"a":1} | to_entries
[{"name":"a","value":1},{"k":"b","v":2}] | from_entries
{"a":{"b":1}} | paths
{"a":{"b":1}} | [leaf_paths]
[1,[2]] | getpath([1,0])
null | setpath([]; 1)
null | setpath(["a",1]; 1)
[1] | setpath([-1]; 5)
[1] | setpath([-2]; 5)
null | delpaths([["a"]])
{"a":1} | delpaths([[0]])
"abc" | .[0:1] = "x"
tostream
{"a":[1,{"b":2}]} | [tostream]
fromstream(([0],1),([1],2),([1]))
[1,[2,3]] | flatten
[1,[2,[3]]] | flatten(1)
[1,[2]] | flatten(-1)
"abc" | indices("b")
[1,2,1] | indices(1)
[1,2,1,2] | indices([1,2])
"a,b, c" | splits(", *")
"test" | ascii_upcase
"aBc" | test("b"; "i")
"foo bar" | match("(?<x>o+)"; "g")
"abc" | capture("(?<first>.)")
"aaa" | sub("a"; "b")
"aaa" | gsub("a"; "b")
"abab" | [scan("ab")]
"abab" | gsub("(?<x>a)"; "<\(.x)>")
1 | tostring | tonumber
"1e1000" | tonumber
"nan" | tonumber
" 1" | tonumber
"0x10" | tonumber
[1,2] | contains([1])
"foobar" | contains("bar")
{"a":[1,2,{"b":3}]} | contains({"a":[{"b":3}]})
[1,2] | inside([1,2,3])
[3,1,2] | sort
[{"a":2},{"a":1}] | sort_by(.a)
[{"a":2,"b":1},{"a":1,"b":2},{"a":2,"b":0}] | group_by(.a)
[1,2,1] | unique
[{"a":1},{"a":2}] | min_by(.a), max_by(.a)
[] | min
[1,2] | add
[] | add
["a","b"] | add
[[1],[2]] | add
[{"a":1},{"b":2}] | add
[1,null,2] | any, all
[] | any, all
[1,2] | any(. > 1), all(. > 1)
any(1,2; . == 2)
range(5)
[range(0;10;3)]
[range(5;0;-2)]
[range(0;1;0)] | length
[limit(5; range(0; 1; 0))]
[.[]?]
[1,2,3] | IN(2)
2 | IN(1,2)
[1,2] | combinations
[[1,2],[3,4]] | [combinations]
[0,1] | [combinations(2)]
{"a":1,"b":[2]} | walk(if type == "number" then .+1 else . end)
[1,[2]] | walk(if type == "array" then reverse else . end)
"abc" | reverse
null | reverse
{"a":1} | with_entries(.value += 1)
{"b":1,"a":2} | keys, keys_unsorted
[1,2] | keys
{"a":1} | has("a"), has("b")
[1] | has(0), has(1)
"a" | in({"a":1})
{"a":1} | map_values(.+1)
[1,2] | map_values(empty)
{"a":1,"b":2} | map_values(empty)
[[1,2],[3]] | map(add)
[1,2,3] | .[1:] | .[0]
[1,2,3] | .[:-1]
[1,2,3] | .[1.2:2.7]
"abcdef" | .[2:-1]
[1,2,3] | first, last, nth(1)
[range(10)] | .[2:4] = []
[range(5)] | to_entries | map(select(.value > 2)) | from_entries
[splits("a"; "g")]
"a1b2" | [match("\\d"; "g") | .offset]
@json "x\([1,"a"])"
@html "<\("<&>")>"
@sh "echo \("a b")"
@csv "\([1,2])"
[1,[2]] | @text
"é" | @base64 | ., @base64d
"YWJj" | @base64d
"YWJ" | @base64d
"!!!" | @base64d
now | type
0 | todate
"2015-03-05T23:51:47Z" | fromdate
"2015-03-05T23:51:47Z" | strptime("%Y-%m-%dT%H:%M:%SZ")
1425599507 | gmtime
1425599507 | gmtime | mktime
1425599507 | strftime("%A, %B %d, %Y %j %a %b %e %H %I %p %Z %u %w")
1425599507.9 | todate
1425599507 | gmtime | todate
[2015,2,5,23,51,47,4,63] | todate
"10:15" | strptime("%H:%M")
1e1000
[.1, 1.0, 1.10, 3e2, -0, 0.0]
[3.0, 1e2] | .[0] + .[1]
100000000000000000001
[100000000000000000001] | .[0]
100000000000000000001 | . + 0
100000000000000000001 == 100000000000000000000
[100000000000000000001, 100000000000000000000] | unique
1.000 | tostring
3 | . / 2
10 / 3
1e17 | tostring
0.1 + 0.2
[1, 2] | tojson
{"a":1.50} | tojson
1.5 | floor, ceil, round, sqrt, fabs
2 | pow(.; 10), log, exp, log2, exp10, log10
[4, 2] | pow(.[0]; .[1])
10 | significand, logb, gamma, frexp, modf
1 | ldexp(.; 3), scalb(.; 3)
1 | atan2(1; 2)
3 | j0, j1, y0, y1
"abc" | ascii
[1,2,3] | bsearch(2), bsearch(4), bsearch(0)
"a" | splits("")
"aXbXc" | split("X"; null)
"aXbxc" | split("x"; "i")
"aXbxc" | ascii_downcase | split("x")
[3,2,1] | sort_by(.; -.)
input_filename
$ENV | type
env | type
builtins | length
"x" as $x | {$x, "y": 2, "a b": 3, @base64: 4}
{"a":1} | {a, b: .a, "c": 3, (.a|tostring): 4, $__loc__}
{(1,2|tostring): (3,4)}
{a: 1} | .a as $x | {$x}
{"if": 1, "then": 2} | .if, .then
{if: 1, reduce: 2}
. as {a: $x} | $x
def f: 1; def g: f + 1; g
def f(x): x * 2; f(3)
def f($a; $b): $a + $b; f(1; 2)
def f(a; $b): a + $b + b; f(1; 2)
def fac: if . <= 1 then 1 else . * (. - 1 | fac) end; 10 | fac
def f: def g: 3; g * 2; f
def f(g): def h: g; h; f(5)
[recurse(if . < 3 then .+1 else empty end)] | length
0 | [recurse(.+1; . < 3)]
{"a":[1]} | [..]
[1,[2]] | [.. | numbers]
[1,"a",null,[],{}] | map(type)
[1,"a",null,[],{},true] | [.[] | scalars]
[1,"a",null,[],{},true] | [.[] | iterables]
label $f | 1, break $f, 2
[label $f | range(10) | ., (select(. == 3) | break $f)]
break $x
try error("x") catch .
try error catch .
[.[] | try if . > 2 then error("big") else . end catch "c"]
[1,2,3] | [.[] | try (if . == 2 then error("x") else . end)]
try (1, error("x"), 3) catch .
(try error("x") catch .) | ascii_upcase
error("x")?
[(1,2)?]
.a?.b?
1 as $x | 2 as $y | [$x, $y, $__loc__]
[1,2] | .[0] as $a | .[1] as $b | $a + $b
if . then 1 end
if empty then 1 else 2 end
[if (true, false) then 1 else 2 end]
if 1 then 2 elif 3 then 4 else 5 end
[.[]] | length
1, 2 | . * 10
1 + 2 * 3 - 4 / 2 % 3
1 as $x | 2 as $x | $x
"\(1 + 2)"
"a" * 3
"a" * 0
"abc" / "b"
{} * {"a":{"b":1}} * {"a":{"c":2}}
[1,2,3] - [2]
null + 1
1 + null
null - 1
{"a":1} + {"a":2, "b":3}
[] == []
{"a":1, "b":2} == {"b":2, "a":1}
1 < "a", [] < {}, null < false
[1,2] < [1,3]
{"a":2} < {"b":1}
{"a":2} < {"a":3}
true and null, false or 1
(true, false) and (true, false)
[true, false] | map(not)
1 // 2
null // 2
false // false
(null, false, 1) // 2
empty // 2
[(1, null, 2) // 3]
error("x") // 1
.a // error("y")
[1,2] | .[2] // 0
$x
foo
1 +
.[
{a}
"\x"
[1,2,
1 | . as [$a] | $a
"abc" | test("B"; "ix")
"a\nb" | test("a.b"; "s")
"aaa" | [match("a*?"; "g") | .length]
"abc" | [match(""; "g") | .offset]
"" | [match(""; "g")]
"test" | sub("(?<x>t)"; "\(.x|ascii_upcase)"; "g")
getpath(["a"]) = 1
.a.b |= . + 1
.a[1:3] = ["x"]
.[0] = 1
.a += 1
.a -= 1
.a *= 2
.a /= 2
.a %= 2
.a //= 1
{"a":[1,2,3]} | .a[] += 10
{"a":[1,2,3]} | .a |= map(select(. != 2))
[1,2,3] | (.[] | select(. == 2)) |= empty
{"a":1,"b":2} | with_entries(select(.value > 1))
[[1,2],[3,4]] | .[][0]
[[1,2],[3,4]] | map(.[1])
[1,2] | .[-1] = 9
[1,2] | .[5] = 9
null | .[2] = 1
{} | .a.b.c = 1
[] | .[-1] = 1
{"a":1} | to_entries[0] | keys
{"a":{"b":{"c":1}}} | [paths(type == "number")]
{"a":[{"b":1}]} | pick(.a[0].b)
[1,2,3] | pick(.[1])
{"a":1} | getpath(["a","b"])
[1,2,3] | del(.[0,2])
[1,2,3] | del(.[])
{"a":1,"b":2} | del(.a, .b)
[1,2,3] | to_entries
{} | .["a"] = 1 | .a
"abc" | .[1:2]
[1,2,3][1]
[1,2,3][]
{"a":1}.a
"x".a
.. == .
[..]
splits
@unknown
ltrimstr("a")
"abc" | ltrimstr("a"), rtrimstr("c"), startswith("a"), endswith("c")
"abc" | startswith(1)
[1,[2,[3,[4]]]] | flatten(2)
{} | flatten
"a" | ascii_downcase | explode | implode
[65, 1114112] | implode
[-1] | implode
"€" | @uri
[1,2,3] | IN(.[]; 2, 3)
[{"id":1}] | INDEX(.id)
INDEX([{"id":1},{"id":2}][]; .id)
"a" | getpath([])
{"a":[1]} | [paths]
{"a":[1]} | [paths(..)]
[{"a":1},{"a":2}] | map(.a) | add
[3,1,2] | sort | reverse
"a,b" | split(",") | join("-")
["a",1,null,true] | join(",")
[] | join(",")
["a"] | join(", ")
[[]] | join(",")
"abc" | sub("b"; "x"; "g")
{"a":"x"} | has("a") and (.a | type == "string")
@sh "\(1, 2)"
[limit(3; repeat(1))]
[first(range(1; infinite))]
first(range(10)), last(range(10)), nth(5; range(10))
nth(-1; range(10))
[while(. < 5; . + 1)]
[.[]?] | [until(. > 100; . * 2)]
0 | [until(. > 100; . + 30)]
[range(3)] | [.[] | tostring]
input_line_number
$__prog_args
getpath(["a",0,"b"])
{"a":[{"b":1}]} | getpath(["a",0,"b"])
splits("a"; null)
ascii(65)
@base32 "x"
"ab" | @base32 | ., @base32d
tojson
[1,{"a":"é"}] | tojson
"[1,{\"a\":2}]" | fromjson
"nan" | fromjson
"1 2" | fromjson
"" | fromjson
"  " | fromjson
"{\"a\":1}x" | fromjson
gsub("";"x")
"abc" | gsub("";"x")
"abc" | [splits("")]
getpath([1])
[1] | getpath(["a"])
{} | getpath([0])
[[1]] | getpath([0, 0, 0])
abs
"a" | abs
toarray
1 | toarray
have_literal_numbers
have_decnum
ltrimstr
significand
[1,2] | min_by(error("x"))
{} | keys_unsorted
"x" | error
null | error
{"a":"b"} | error
[1] | error
try error({"a":1}) catch .a
try error(null) catch .
(1,2) | error
@base64d
"=" | @base64d
"YQ==" | @base64d
"YQ" | @base64d
"Y" | @base64d
"YWJjZA=" | @base64d
"ÿ" | @base64
"a\u0000b" | @base64
[1,"a\"b",null,true,1.5] | @csv
[1,"a\tb\\c\nd\re",null,true] | @tsv
[[1]] | @tsv
[{}] | @csv
"x" | @csv
"a'b" | @sh
[1,null,true] | @sh
"<script>'\"&</script>" | @html
"a b+c=d&e/f?g:h~i-j_k.l!*'()" | @uri
[1, "x"] | @json
1 | @html
{"a":1} | @text
[1] | @base64
1 | @uri
[(1, error("x"), 2) // 3]
[(null, error("x"), 2) // 3]
[(false, error("x")) // 3]
[.[]?//3]
[limit(3;1,2,error("x"))]
path(1)
1|path(1)
path(.a|1)
[paths]
{"a":[1]}|path(..)
path(first(.a,.b))
path(limit(1;.a,.b))
{}|error
[.[] as [$a] ?// $a | $a]
[[1,2],3] | .[] as [$a] ?// $a | $a
[[3]] | .[] as [$a] ?// $a | if $a == 3 then error("y") else $a end
[1,[2]] | .[] as [$a] ?// $a | $a
{"a":1} as {a:$x} ?// [$x] | $x
path(.a[].b)
null|path(.a[1:3])
[1,2,3]|.[1:]=["x"]
[1,2,3]|del(.[0,2])
{"a":1}|has("a"),has("b")
"abc"|.[1:2]
[1,2]|.[-1.5]
[1,2]|.[1.7]
[1,2]|.[-0.5]
"abc" | .a // 1
[(null,false) // error("q")]
[(error("x"),1) // 2]
[(false, error("x")) // 2]
first(error("x"),1) // 2
def f: error("x"); f // 2
[.[]] // 2
try ("abc" | .a) // 1
(1|error) // 2
[error(null) // 2]
"\(1,2) \(3,4)"
{a: (1,2), b: (3,4)}
{(("a","b")): (1,2)}
[.[(0,1):(2,3)]]
[1,2,3]|[.[(0,1):(2,3)]]
{(1):2}
{"a":1}|[.[] = (1,2)]
[[1,2]|.[] += (10,20)]
[1,2]|path(.[]|select(.>1))
[{"a":1}]|.[0].a |= empty
[1,2,3]|(.[]|select(.>1)) |= empty
{"a":[1,2]}|path(.a|..)
reduce empty as $x (1; .)
[foreach (1,2) as $x (0; .+$x)]
[foreach (1,2) as $x (0; (.+$x), 100; [$x,.])]
[1,[2]]|getpath(["a"])
null|getpath(["a",1])
{"a":1}|getpath(["a","b"])
[limit(0;1,2)]
[limit(-1;1,2)]
[first(empty)]
[range(0;1;0)]|length
[limit(5; 1|repeat(.*2))]
[limit(5;1|repeat(.*2; . < 100))]
[1|limit(4;recurse(.+1))]
[2|recurse(. * .; . < 100)]
[1|while(. < 10; .*3)]
[1|until(. > 10; .*3)]
[1|until(. > 10; .*3, .*2)]
[1|while(. < 10; .*3, .*4)]
1|stderr
{"a":1}|debug
"x"|debug("m")
get_search_list
get_jq_origin|type
$ARGS
"a,b"|split(",";null)
[1,[2]]|implode
"abc"|test("B";"i")
"abc"|test("B";"q")
"abc"|test("(")
[match("a";"g")]
"aXbX"|[match("X";"g")|.offset]
"x"|ltrimstr(1)
1|ltrimstr("a")
[1,2]|tostring
"1.50"|tonumber
" 1"|tonumber
"nan"|tonumber
"[1]"|tonumber
{}|sort
[3,1]|min,max
{}|min
[]|min
true|contains(false)
"foobar"|contains("bar")
{"a":[1,2,{"b":3}]}|contains({"a":[{"b":3}]})
[1,2]|join(",")
[[1]]|join(",")
["a",null,1,true]|join("-")
[]|join(",")
try input catch .
[inputs]
"ab"|@base32
"MFRA===="|@base32d
"a\nb" | test("a.b"), test("a.b";"x"), test("^b";"s"), test("^b"), test("a$"), test("a$";"s")
1425599507|strflocaltime("%Z"), (gmtime|strftime("%Z")), (localtime|strftime("%Z")), (gmtime|mktime)
@json "a\("x")b\([1])"
@text "a\(1)"
{(1,2):3}
{a:1} | {a, "b":2, "c\(1)": 3, @base64: 4}
{1:2}
{(null):1, a:2}
{"a":1, (true):2}
module {a:1}; 0
module (.+1); 0
module []; 0
1 as $x | module {}; 0
. as [] | 1
[0.0001e4 == 1, 100e-2 == 1, 0.1e1 == 1, 12.5e-1 > 1.2]
[nan] | implode
"abc" | [match("( )*"; "g")]
"abc" | [match("( )*"; "gn")]
"abc" | [test("( )*"; "gn")]
"foo bar foo" | gsub("[^a-z]*(?<x>[a-z]*)"; "Z\(.x)")
"qux" | [match("u*"; "g")] | map(.offset)
'''


class Case:
    def __init__(self, name, args, stdin='', files=None, env=None):
        self.name = name
        self.args = args
        self.stdin = stdin
        self.files = files or {}
        self.env = env or {}


CASES = []


def C(name, args, stdin='', files=None, env=None):
    CASES.append(Case('cli/' + name, args, stdin, files, env))


for i, p in enumerate(x for x in PROGRAMS.split('\n') if x.strip()):
    CASES.append(Case('prog/%03d %s' % (i, p[:40]), ['-nc', p]))

# ---------------------------------------------------------------- the command line

DATA = '{"a":1,"b":[1,2,{"c":"x"}],"d":null,"e":"é\\u2028"}'
C('dot', ['.'], DATA)
C('no program', [], DATA)
C('compact', ['-c', '.'], DATA)
C('tab', ['--tab', '.'], DATA)
C('indent 0', ['--indent', '0', '.'], DATA)
C('indent 1', ['--indent', '1', '.'], DATA)
C('indent 7', ['--indent', '7', '.'], DATA)
C('indent 8', ['--indent', '8', '.'], DATA)
C('indent -1', ['--indent', '-1', '.'], DATA)
C('indent missing', ['.', '--indent'], DATA)
C('indent then tab', ['--indent', '3', '--tab', '.'], DATA)
C('tab then indent', ['--tab', '--indent', '3', '.'], DATA)
C('indent then compact', ['--indent', '3', '-c', '.'], DATA)
C('ascii', ['-a', '.'], DATA)
C('ascii astral', ['-ac', '.'], '["\\ud83d\\ude00x", "\U0001F600"]')
C('ascii raw', ['-ar', '.e, .b'], DATA)
C('sort keys', ['-S', '.'], '{"b":1,"a":{"d":1,"c":[{"z":1,"y":2}]}}')
C('sort keys compact', ['-Sc', '.'], '{"b":1,"a":{"d":1,"c":2}}')
C('sort keys after keys', ['-S', '-c', 'keys_unsorted, .'], '{"b":1,"a":2}')
C('raw', ['-r', '.[]'], '["a",1,"b\\n",null,[1],{"x":"y"}]')
C('join', ['-j', '.[]'], '["a",1,"b\\n",null,[1]]')
C('raw join stacked', ['-rj', '.[]'], '["a","b"]')
C('slurp', ['-s', '.'], '1 2 [3]')
C('slurp compact', ['-sc', '.'], '1\n2\n')
C('slurp empty', ['-s', '.'], '')
C('slurp null input', ['-sn', '.'], '1 2')
C('slurp parse error', ['-s', '.'], '1 2 [')
C('raw input', ['-R', '.'], 'a\nb\n\nc')
C('raw input slurp', ['-Rs', '.'], 'a\nb\n')
C('raw input slurp empty', ['-Rs', '.'], '')
C('raw input null', ['-nR', 'input, [inputs]'], 'a\nb\nc\n')
C('raw input invalid utf8', ['-R', '.'], b'a\xffb\n')
C('raw input lines', ['-R', 'input_line_number'], 'a\nb\nc')
C('null input', ['-n', '.'], '1')
C('null input inputs', ['-nc', '[inputs]'], '1 2 3')
C('null input reduce', ['-n', 'reduce inputs as $x (0; . + $x)'], '1 2 3')
C('input pairs', ['-c', '[., input]'], '1 2 3 4')
C('input odd', ['-c', '[., input]'], '1 2 3')
C('input odd newline', ['-c', '[., input]'], '1 2 3\n')
C('input none', ['-n', 'input'], '')
C('try input', ['-n', 'try input catch .'], '')
C('input_line_number', ['input_line_number'], '1\n2\n\n3')
C('input_line_number objects', ['-c', '[., input_line_number]'], '{"a":\n1}\n[2,\n3]\n')
C('several values a line', ['-c', '.'], '1 2\n3 [4,\n5] "x"')
C('error location', ['.+1'], '1\n"a"\n2')
C('error location first', ['.+1'], '"a" 1')
C('error location last', ['.a'], '{"a":1}\n[1]\n{"a":2}')
C('error not a string', ['error({"a":1})'], '1')
C('error null', ['error(null)'], '1')
C('error exit then ok', ['if . == 1 then error("x") else . end'], '1 2')
C('error exit last', ['if . == 2 then error("x") else . end'], '1 2')
C('parse error', ['.'], '{')
C('parse error after values', ['.'], '1 2 [')
C('parse error trailing comma', ['.'], '{"a":1,}')
C('parse error extra bracket', ['.'], '[1,2]]')
C('parse error escape', ['.'], '"\\x"')
C('parse error line', ['-c', '.'], '[1,\n2,\n}')
C('parse error bare word', ['.'], 'nul')
C('nan input', ['-c', '.'], 'nan NaN -nan [nan]')
C('numbers', ['.'], '[1.000, 1e1000, -0, 0.1e-5, 100000000000000000001, 1E2, -1.5e-7, 3.0]')
C('numbers math', ['-c', 'map(. + 0)'], '[1.000, 1e1000, -0, 0.1e-5, 100000000000000000001, 1E2]')
C('infinite', ['-nc', '[infinite, -infinite, nan]'])
C('big object', ['-c', '[.[] | {k: .}]'], '[' + ','.join(str(i) for i in range(200)) + ']')
C('deep', ['-c', '.'], '[' * 300 + ']' * 300)
C('too deep', ['-c', '.'], '[' * 10001 + ']' * 10001)
C('utf8 invalid', ['-c', '.'], b'"a\xffb\xc3"')
C('utf8 surrogates', ['-c', '.'], '"\\ud83d\\ude00 \\ud800 \\udc00x"')
C('bom', ['-c', '.'], b'\xef\xbb\xbf[1]')
C('exit status null', ['-e', '.'], 'null')
C('exit status false', ['-e', '.'], 'false')
C('exit status true', ['-e', '.'], 'true')
C('exit status last wins', ['-e', '.'], '1 null')
C('exit status last true', ['-e', '.'], 'null 1')
C('exit status empty', ['-e', 'empty'], '1')
C('exit status error', ['-e', 'error("x")'], '1')
C('exit status null input', ['-en', 'null'])
C('exit status null input empty', ['-en', 'empty'])
C('exit status halt', ['-en', 'halt'])
C('halt', ['-n', '1, halt, 2'])
C('halt_error', ['-n', 'halt_error'])
C('halt_error string', ['-n', '"bye\\n"|halt_error(1)'])
C('halt_error string no newline', ['-n', '"bye"|halt_error(1)'])
C('halt_error object', ['-n', '{"a":1}|halt_error(1)'])
C('halt_error null', ['-n', 'null|halt_error(3)'])
C('halt_error code 0', ['-n', '"x\\n"|halt_error(0)'])
C('halt_error bad code', ['-n', '"x"|halt_error("a")'])
C('halt_error stops inputs', ['if . == 2 then halt_error else . end'], '1 2 3')
C('arg', ['-nc', '--arg', 'x', '1', '[$x, $ARGS.named]'])
C('argjson', ['-nc', '--argjson', 'x', '{"a":[1]}', '$x.a'])
C('arg after program', ['-nc', '[$x]', '--arg', 'x', 'y'])
C('arg first wins', ['-nc', '--arg', 'x', '1', '--arg', 'x', '2', '$x'])
C('arg shadowed', ['-n', '"a" as $x | $x', '--arg', 'x', 'y'])
C('arg missing', ['-n', '--arg'])
C('arg one', ['-n', '--arg', 'x'])
C('argjson missing', ['-n', '--argjson', 'x'])
C('argjson invalid', ['-n', '--argjson', 'a', '{'])
C('argjson two values', ['-n', '--argjson', 'a', '1 2', '$a'])
C('args', ['-nc', '$ARGS', '--args', 'a', 'b'])
C('jsonargs', ['-nc', '$ARGS', '--jsonargs', '1', '{}', '"x"'])
C('jsonargs invalid', ['-nc', '$ARGS', '--jsonargs', '1', '{'])
C('args then jsonargs', ['-nc', '$ARGS.positional', '--args', 'a', '--jsonargs', '1', '--args', 'b'])
C('args with named', ['-nc', '--arg', 'k', 'v', '$ARGS', '--args', 'a'])
C('positional before program', ['-nc', '--args', '$ARGS.positional', 'a'])
C('undefined variable', ['-n', '$nope'])
C('rawfile', ['-nc', '--rawfile', 'r', 'f.txt', '$r'], files={'f.txt': 'line1\nline2\n'})
C('slurpfile', ['-nc', '--slurpfile', 's', 'f.json', '$s'], files={'f.json': '1 [2] {"a":3}'})
C('slurpfile bad', ['-nc', '--slurpfile', 's', 'f.json', '$s'], files={'f.json': '1 ['})
C('slurpfile missing', ['-nc', '--slurpfile', 's', 'nope.json', '$s'])
C('rawfile missing', ['-nc', '--rawfile', 's', 'nope.txt', '$s'])
C('slurpfile one arg', ['-nc', '--slurpfile', 's'])
C('named', ['-nc', '--arg', 'a', '1', '--slurpfile', 'b', 'f.json', '--rawfile', 'c', 'f.json', '$ARGS.named'],
  files={'f.json': '[1]'})
C('files', ['-c', '[., input_filename]', 'a.json', 'b.json'], files={'a.json': '1 2', 'b.json': '3\n'})
C('files line numbers', ['-c', '[., input_line_number]', 'a.json', 'b.json'],
  files={'a.json': '1\n2\n', 'b.json': '\n3\n'})
C('files slurp', ['-sc', '.', 'a.json', 'b.json'], files={'a.json': '1 2', 'b.json': '3'})
C('files raw', ['-R', '.', 'a.txt', 'b.txt'], files={'a.txt': 'x\ny\n', 'b.txt': 'z\n'})
C('file missing', ['.', 'nope.json'])
C('file missing among', ['-c', '.', 'a.json', 'nope.json', 'b.json'], files={'a.json': '1', 'b.json': '2'})
C('file error location', ['.a', 'a.json'], files={'a.json': '{"a":1}\n[2]\n'})
C('file stdin dash', ['-c', '.', '-'], '[1]')
C('from file', ['-f', 'p.jq', 'in.json'], files={'p.jq': '# a comment\n.a\n| . + 1\n', 'in.json': '{"a":1}'})
C('from file args', ['-nc', '-f', 'p.jq', '--args', 'x'], files={'p.jq': '$ARGS.positional'})
C('from file missing', ['-f', 'nope.jq'])
C('stdin by default', ['.[0]'], '[5]')
C('double dash', ['-nc', '--', '[1]'])
C('double dash dash program', ['-nc', '--args', '$ARGS.positional', '--', '-x', '--y'])
C('short options stacked', ['-nrc', '"a", [1]'])
C('short option unknown', ['-nX', '1'])
C('unknown option', ['-n', '-X'])
C('unknown long option', ['-n', '--bogus', '1'])
C('help', ['-h'])
C('help long', ['--help'])
C('version', ['--version'])
C('version short', ['-V'])
C('compile error', ['.a |'])
C('compile errors', ['[$x, $y, f]'])
C('compile error lines', ['1 +\n2 +'])
C('compile error multiline', ['.a\n| foo\n| .b'])
C('syntax keyword', ['if 1 then 2 end end'])
C('syntax unexpected', ['1 2'])
C('syntax operator', ['. == == 1'])
C('syntax object key', ['{@base64: 1}'])
C('syntax object number key', ['{1: 2}'])
C('constant key', ['{(1): 2}'])
C('break undefined', ['break $x'])
C('modules', ['import "a" as a; .'])
C('include', ['include "a"; .'])
C('loc', ['-nc', '$__loc__, {a: $__loc__}'])
C('debug', ['-c', 'debug, debug("m"), debug("a", "b")'], '[1]')
C('stderr', ['-c', 'stderr'], '{"a":"x"}')
C('stderr string', ['-c', 'stderr'], '"raw\\n"')
C('input_filename stdin', ['input_filename'], '1')
C('env', ['-nc', '$ENV.JQTEST, env.JQTEST'], env={'JQTEST': 'v'})
C('lazy first', ['-n', 'first(range(1; infinite))'])
C('lazy limit', ['-nc', '[limit(3; repeat(1))]'])
C('lazy until', ['-n', 'first(repeat(1) | select(. == 1))'])
C('lazy label', ['-nc', '[label $out | range(10) | if . == 3 then break $out else . end]'])
C('lazy inputs', ['-n', 'first(inputs)'], '1 2 [')
C('limit inputs', ['-nc', '[limit(2; inputs)]'], '1 2 3')
C('getpath paths', ['-c', '[paths(type == "number")]'], DATA)
C('to_entries', ['-c', 'to_entries, with_entries(.value |= tostring)'], '{"a":1,"b":[2]}')
C('reduce big', ['-n', 'reduce range(100000) as $i (0; . + $i)'])
C('reduce array', ['-n', 'reduce range(20000) as $i ([]; . + [$i]) | length'])
C('reduce object', ['-n', 'reduce range(20000) as $i ({}; .[$i|tostring] = $i) | length'])
C('foreach big', ['-n', '[foreach range(50000) as $i (0; . + $i)] | add'])
C('update big', ['-n', '[range(50000)] | map(. * 2) | .[] |= . + 1 | add'])
C('recursion', ['-n', 'def f: if . < 5000 then . + 1 | f else . end; 0 | f'])
C('sort big', ['-c', 'sort_by(.k) | .[0:3]'],
  '[' + ','.join('{"k":%d}' % ((i * 7919) % 1000) for i in range(1000)) + ']')
C('group unique', ['-c', 'group_by(. % 3), unique, (map({k: (. % 4)}) | unique_by(.k))'],
  '[5,3,8,1,9,2,3,5]')
C('dates', ['-c', 'todate, (todate | fromdate), gmtime, (gmtime | mktime), strftime("%A %j %U %e")'],
  '1425599507')
C('strptime', ['-c', 'strptime("%Y-%m-%dT%H:%M:%SZ"), (strptime("%Y-%m-%dT%H:%M:%SZ") | mktime)'],
  '"2015-03-05T23:51:47Z"')
C('localtime', ['-c', 'localtime, strflocaltime("%H:%M %Z"), (localtime | mktime)'], '1425599507')
C('regex', ['-c', '[match("(?<x>a)(b)?"; "g")], [scan("a.")], sub("(?<l>[a-z])"; "<\\(.l)>"; "g"), [splits(", *")]'],
  '"ab, ac,ad"')
C('regex test flags', ['-c', '[test("A"; "i"), test("a b"; "x"), test("a.b"), test("a.b"; "s")]'], '"a\\nb"')
C('regex capture', ['-c', 'capture("(?<y>\\\\d+)-(?<m>\\\\d+)")'], '"2015-03"')
C('regex unicode offsets', ['-c', '[match("b"; "g") | .offset]'], '"ébéb"')
C('formats', ['-r', '@text, @json, @html, @uri, @csv, @tsv, @sh, @base64, (@base64 | @base64d)'],
  '["a b", "<&>", 1, "x\'y\\t"]')
C('format strings', ['-r', '@sh "echo \\(.)", @uri "q=\\(.[0])", @json "v: \\(.)"'], '["a b", "c"]')
C('format unknown', ['-n', '@nope'])
C('tojson numbers', ['-c', 'tojson, (tojson | fromjson)'], '[1.000, 1e1000, 0.1, 1e-5, 123456789012]')
C('tostring', ['-c', 'map(tostring)'], '[1, "a", [1], {"a": null}, null, true]')
C('assignments', ['-c', '.a = 1, .b |= . + 1, .c += 2, .d //= 3, .e *= 2, .f -= 1, .g /= 2, .h %= 3'],
  '{"a":0,"b":1,"c":1,"d":null,"e":2,"f":1,"g":4,"h":5}')
C('del and paths', ['-c', 'del(.a, .b[0]), to_entries, [paths], [leaf_paths], (tostream | tostring)'],
  '{"a":1,"b":[2,3]}')
C('fromstream', ['-c', 'fromstream(tostream)'], '{"a":[1,{"b":2}],"c":"d"}')
C('getpath setpath', ['-c', 'getpath(["a","b"]), setpath(["a","b"]; 1), delpaths([["a"]])'], '{"a":{"b":0}}')
C('limit first last nth', ['-c', '[limit(2; .[])], first(.[]), last(.[]), nth(1; .[]), first, last, nth(2)'],
  '[1,2,3]')
C('ltrimstr', ['-c', 'map(ltrimstr("a") | rtrimstr("c"))'], '["abc", "a", "c", 1]')
C('splits', ['-c', 'split(", "), split(","; null), [splits("b+")]'], '"a, bb, c"')
C('ascii', ['-c', 'ascii_downcase, ascii_upcase, explode, (explode | implode)'], '"aBc É"')
C('indices', ['-c', 'indices(", "), indices(1), index(","), rindex(",")'], '"a, b, c"')
C('indices array', ['-c', 'indices(1), indices([1,2]), index(2), rindex(1)'], '[0,1,2,1,3,1,2]')
C('min max', ['-c', 'min, max, min_by(.a), max_by(.a)'], '[{"a":3},{"a":1},{"a":2}]')
C('any all', ['-c', 'any, all, any(. > 2), all(. > 0), isempty(.[]), (map(. > 1) | any)'], '[1,2,3]')
C('flatten', ['-c', 'flatten, flatten(1), flatten(0)'], '[1,[2,[3,[4]]]]')
C('flatten negative', ['-c', 'flatten(-1)'], '[1]')
C('range', ['-c', '[range(3)], [range(1;3)], [range(0;1;0.3)], [range(5;0;-2)]'], 'null')
C('walk', ['-c', 'walk(if type == "number" then . + 1 else . end)'], '[1,{"a":2,"b":[3]}]')
C('transpose combinations', ['-c', 'transpose, [combinations], [[1,2] | combinations(2)]'], '[[1,2],[3,4]]')
C('in inside', ['-c', '[.[] | in({"a":1})], ("a" | inside("abc"))'], '["a","b"]')
C('splits regex flags', ['-c', '[splits("A"; "i")]'], '"bab"')
C('test array arg', ['-c', 'test(["A", "i"])'], '"a"')
C('env object', ['-c', '$ENV | has("JQTEST")'], 'null', env={'JQTEST': '1'})
C('tojson unicode', ['-c', 'tojson'], '"é\\u0000\\u001f\\u007f\\u2028"')
C('implode errors', ['-c', 'try implode catch .'], '[1114112]')
C('error messages', ['-c', '.[] | try (. + 1) catch .'], '["a", {}, [], null, true]')
C('object construction', ['-c', '{a, b: .a, "c": 1, (.a | tostring): 2, $__loc__, @base64 "k": 3}'], '{"a":1}')
C('object generators', ['-c', '{a: (1,2), b: (3,4)}'], 'null')
C('optional', ['-c', '[.[]?], [.a?], [..?], (try error("x") catch .), [.[] | try error catch .]'], '[1,[2]]')
C('alternative destructuring', ['-c', '.[] as [$a] ?// {a: $a} ?// $a | $a'], '[[1], {"a": 2}, 3]')
C('reduce foreach patterns', ['-c', 'reduce .[] as [$a, $b] (0; . + $a * $b), [foreach .[] as {x: $x} (0; . + 1; [$x, .])]'],
  '[[1,2],[3,4]]')
C('label nested', ['-c', '[label $a | label $b | 1, break $b, 2], [label $a | (1, break $a), 2]'], 'null')
C('def closures', ['-c', 'def f(g): [g, g]; def h($x): $x + 1; f(.[]), h(10), (def x: 3; x)'], '[1,2]')
C('recursive def', ['-c', 'def fac: if . <= 1 then 1 else . * (. - 1 | fac) end; map(fac)'], '[1,5,10]')
C('getpath invalid', ['-c', 'try path(.a | tostring) catch .'], '{"a":1}')
C('sort mixed', ['-c', 'sort'], '[{"a":1}, [1], "b", 2, true, false, null, "a", 1, {"a":0}]')
C('equality', ['-c', '[1 == 1.0, "a" < "b", [1] < [1,0], {} == {}, null < false, {"a":1} < {"b":0}]'], 'null')
C('arithmetic', ['-c', '[1 + 2, 5 - 3, 2 * 3, 7 / 2, 7 % 3, -7 % 3, 5 % -2, 1e300 * 1e10, .5]'], 'null')
C('string multiply divide', ['-c', '["ab" * 3, "ab" * 0, "a,b" / ",", {"a":{"b":1}} * {"a":{"c":2}}]'], 'null')
C('math', ['-c', '[pow(2;10), log2(8), (16|sqrt), (1|exp|log), (-1.5|fabs, floor, ceil, round), (10|significand), (2.5|trunc)]'], 'null')
C('math frexp', ['-c', '[8|frexp], [3.5|modf], (5|lgamma_r), ([1,2,3]|ldexp(.[0];.[1]))'], 'null')
C('splits empty', ['-c', '[splits("")]'], '"abc"')
C('tonumber', ['-c', '.[] | try tonumber catch .'], '["1", "1.50", " 2", "x", "1e1000", "-0", "0x10"]')
C('getpath null', ['-c', 'getpath(["a",0,"b"])'], 'null')
C('has errors', ['-c', 'try has(0) catch ., try has("a") catch .'], '{"a":1}')
C('keys', ['-c', 'keys, keys_unsorted, length, utf8bytelength? // "n/a"'], '{"b":1,"a":2}')
C('utf8bytelength', ['-c', 'utf8bytelength'], '"aé😀"')
C('length kinds', ['-c', 'map(length)'], '[null, true, -5, "é", [1], {"a":1}]')
C('splits array input', ['-c', 'try split(",") catch .'], '[1]')
C('error with object', ['-c', 'try error({"a":1}) catch .a'], 'null')
C('error no message', ['error'], 'null')
C('error message kinds', ['error'], '{"a":1}')
C('getpath error', ['-c', 'try getpath(["a","b"]) catch .'], '{"a":1}')
C('isvalid ltrimstr', ['-c', '[.[] | isvalid(tonumber)]'], '["1", "x"]')
C('splits limit', ['-c', '[limit(2; splits(","))]'], '"a,b,c"')
C('ascii output object keys', ['-ac', '.'], '{"é":"ü"}')
C('sort keys unicode', ['-Sc', '.'], '{"b":1,"é":2,"a":3,"B":4}')
C('getpath on numbers', ['-c', 'try getpath([0]) catch .'], '1')
C('builtins count', ['-n', 'builtins | length'])
C('input then error', ['-n', 'input, error("x")'], '1')
C('min_by empty', ['-c', 'min_by(.), max_by(.), min, max'], '[]')
C('tojson nan', ['-c', '[nan] | tojson, (nan < 1), ([nan] | sort), (nan | isnan)'], 'null')
C('splits on null input', ['-c', 'try splits("a") catch .'], 'null')
C('test non-string', ['-c', 'try test("a") catch .'], '1')
C('invalid regex', ['-c', 'try test("(") catch .'], '"a"')
C('ascii raw string', ['-ra', '.'], '"é"')
C('tostream', ['-c', 'tostream'], '{"a":[1,2]}')
C('truncate_stream', ['-c', '[1|truncate_stream([[0],1],[[1,0],2],[[1,0]],[[1]])]'], 'null')
C('getpath streaming', ['-c', '[paths(..)]'], '[[1]]')
C('splits capture names', ['-c', '[match("(?<a>x)|(?<b>y)"; "g") | .captures | map(.name, .string)]'], '"xy"')
C('ltrimstr non string', ['-c', 'ltrimstr("a")'], '1')
C('significand', ['-c', 'map(significand)'], '[0, 1, 8, 0.1]')
C('gamma', ['-c', 'map(gamma, lgamma, tgamma)'], '[1, 4.5]')
C('drem ldexp scalb', ['-c', '[drem(10;3), ldexp(1;4), scalb(1;4), scalbln(1;4), nearbyint(2.5), logb(8)]'], 'null')
C('pow10 exp10', ['-c', '[exp10(2), exp2(3), (8|cbrt)]'], 'null')
C('todate fraction', ['-c', 'todate'], '1425599507.9')
C('gmtime fraction', ['-c', 'gmtime'], '1425599507.25')
C('mktime errors', ['-c', 'try mktime catch .'], '"x"')
C('strftime errors', ['-c', 'try strftime("%Y") catch ., try strftime(1) catch .'], '"x"')
C('tojson of objects', ['-c', 'tojson'], '{"a":[1,{"b":null}],"c":"\\""}')
C('fromjson errors', ['-c', '.[] | try fromjson catch .'], '["[1,", "{\\"a\\":1} x", "nan", "01", ""]')
C('implode', ['-c', 'implode'], '[65, 233, 128512]')
C('splits by regex with groups', ['-c', '[splits("(,)")]'], '"a,b"')
C('sub with named captures', ['-c', 'sub("(?<x>b)"; "[\\(.x)]"), gsub("(?<x>[ab])"; "\\(.x)\\(.x)"), gsub(""; "-")'], '"abc"')
C('gsub empty matches', ['-c', 'gsub("x*"; "-")'], '"abc"')
C('ascii_downcase non string', ['-c', 'try ascii_downcase catch .'], '1')
C('join numbers', ['-c', 'join(", ")'], '[1, null, "a", true]')
C('add', ['-c', 'add, (map([.]) | add), ([] | add)'], '[1,2,3]')
C('add objects', ['-c', 'add'], '[{"a":1},{"b":2},{"a":3}]')
C('add strings', ['-c', 'add'], '["a","b",null]')
C('splits of nothing', ['-c', '[.[] | tostring]'], '[]')
C('any short circuit', ['-c', 'any(.[]; . == 1), all(.[]; . == 1)'], '[1, error]')
C('isempty error', ['-c', 'isempty(error("x"))'], 'null')
C('error in path', ['-c', 'try path(error("x")) catch .'], 'null')
C('try nested', ['-c', 'try (try error("x") catch error("y")) catch .'], 'null')
C('error break', ['-c', '[.[] | try if . == 2 then error("x") else . end catch "c"]'], '[1,2,3]')
C('optional error object', ['-c', '[.[] | (1 / .)?]'], '[1, 0, 2]')
C('limit nested', ['-c', '[limit(2; limit(3; .[]))]'], '[1,2,3,4]')
C('first nested label', ['-c', 'first(first(.[]), 5)'], '[1,2]')
C('splits null', ['-c', 'splits(",")?'], 'null')
C('inputs in function', ['-nc', 'def f: [inputs]; f'], '1 2')
C('input in reduce', ['-nc', 'reduce range(2) as $i ([]; . + [input])'], '"a" "b" "c"')
C('getpath input_line_number', ['-c', 'input_line_number'], '1\n2')
C('ltrimstr long input', ['-r', 'ltrimstr("x")'], '"' + 'xy' * 5000 + '"')
C('long string', ['-c', 'length'], '"' + 'é' * 70000 + '"')
C('long line input', ['-c', 'length'], '[' + ','.join(['1'] * 30000) + ']')
C('many values', ['-c', '. * 2'], ' '.join(str(i) for i in range(3000)))
C('many lines error', ['-c', 'if . == 2500 then error("e") else empty end'], '\n'.join(str(i) for i in range(3000)))


# ---------------------------------------------------------------- running

def norm(b, exe):
    s = b.decode('utf-8', 'replace')
    s = s.replace(exe, 'jq')
    s = re.sub(r'Regex failure: [^\n"]*', 'Regex failure: <message>', s)
    return s


class Runner:
    def __init__(self, ours, ref, verbose):
        self.ours = ours
        self.ref = ref
        self.verbose = verbose

    def run1(self, exe, case, d):
        env = dict(os.environ)
        env.update({'TZ': 'UTC', 'LC_ALL': 'C'})
        env.pop('JQ_COLORS', None)
        if exe == self.ours:
            env['OBX_CHECK'] = '1'
        env.update(case.env)
        stdin = case.stdin if isinstance(case.stdin, bytes) else case.stdin.encode()
        try:
            r = subprocess.run([exe] + case.args, input=stdin, env=env, cwd=d, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return ('timeout', '', '')
        return (r.returncode, norm(r.stdout, exe), norm(r.stderr, exe))

    def run_case(self, case):
        d = tempfile.mkdtemp(prefix='jqcase')
        try:
            for name, text in case.files.items():
                with open(os.path.join(d, name), 'wb') as f:
                    f.write(text if isinstance(text, bytes) else text.encode())
            a = self.run1(self.ours, case, d)
            b = self.run1(self.ref, case, d)
            if a != b:
                return 'differs:\n  ours:      %r\n  reference: %r' % (a, b)
            return None
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- jq.test

def dec(text):
    return json.loads(text, parse_int=decimal.Decimal, parse_float=decimal.Decimal,
                      parse_constant=lambda c: None)


def equal(a, b):
    # jq's jv_equal: numbers by value, arrays in order, objects by keys
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) == type(b) and a == b
    if isinstance(a, decimal.Decimal) and isinstance(b, decimal.Decimal):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return type(a) == type(b) and a == b


def parse_jqtest(text):
    """The tests of a jq.test file: (line, program, input, expected, fail, check_msg)."""
    lines = text.split('\n')
    tests = []
    i = 0
    fail = False
    check = False

    def skip(l):
        s = l.lstrip(' \t')
        return s == '' or s.startswith('#')
    while i < len(lines):
        l = lines[i]
        i += 1
        if skip(l):
            continue
        if l in ('%%FAIL', '%%FAIL IGNORE MSG'):
            fail = True
            check = l == '%%FAIL'
            continue
        prog = l
        start = i
        if fail:
            msg = lines[i] if i < len(lines) else ''
            i += 1
            while i < len(lines) and lines[i] != '':
                i += 1
            tests.append((start, prog, None, msg, True, check))
            fail = check = False
            continue
        inp = lines[i] if i < len(lines) else ''
        i += 1
        exp = []
        while i < len(lines) and not skip(lines[i]):
            exp.append(lines[i])
            i += 1
        tests.append((start, prog, inp, exp, False, False))
    return tests


MODULE_WORDS = ('import ', 'include ', 'modulemeta', 'get_search_list', '$__prog_args')


def run_jqtest(exe, t, d):
    line, prog, inp, exp, fail, check = t
    env = dict(os.environ)
    env.update({'TZ': 'UTC', 'LC_ALL': 'C'})
    try:
        r = subprocess.run([exe, '-c', prog], input=(inp or 'null').encode(), env=env, cwd=d,
                           capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return 'timeout'
    err = r.stderr.decode('utf-8', 'replace')
    if fail:
        if r.returncode != 3:
            return 'compiled (exit %d)' % r.returncode
        if check:
            first = next((x for x in err.split('\n') if x.startswith('jq: error')), '')
            if first != exp:
                return 'message %r, want %r' % (first, exp)
        return None
    outs = [x for x in r.stdout.decode('utf-8', 'replace').split('\n') if x != '']
    try:
        want = [dec(x) for x in exp]
    except ValueError as e:
        return 'bad expected value: %s' % e
    got = []
    for x in outs:
        try:
            got.append(dec(x))
        except ValueError:
            return 'unparsable output %r' % x
    if len(got) < len(want):
        return 'insufficient results: %r (stderr %r)' % (outs, err[:200])
    for a, b in zip(got, want):
        if not equal(a, b):
            return 'got %r, want %r' % (outs, exp)
    if len(got) > len(want):
        return 'superfluous results: %r' % outs[len(want):]
    return None


def jqtest(path, ours, ref, jobs, verbose):
    if path:
        text = open(path, encoding='utf-8').read()
    else:
        with urllib.request.urlopen(JQ_TEST_URL, timeout=60) as f:
            text = f.read().decode('utf-8')
    tests = parse_jqtest(text)
    d = tempfile.mkdtemp(prefix='jqtest')
    try:
        def both(t):
            return run_jqtest(ours, t, d), run_jqtest(ref, t, d)
        with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
            res = list(ex.map(both, tests))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    passed = ref_passed = modules = mod_passed = 0
    failed = []
    for t, (a, b) in zip(tests, res):
        is_mod = any(w in t[1] for w in MODULE_WORDS)
        if is_mod:
            modules += 1
            mod_passed += a is None
            continue
        ref_passed += b is None
        if a is None:
            passed += 1
        else:
            failed.append((t, a, b))
    n = len(tests) - modules
    for t, a, b in failed:
        if verbose or b is None:
            print('jq.test line %d: %s\n    %s%s' % (t[0], t[1], a, '' if b is None else '  (the reference fails too: %s)' % b))
    print('jq.test: %d of %d passed (%.1f%%); the reference through the same runner: %d of %d; '
          'module tests apart: %d of %d passed' % (passed, n, 100.0 * passed / max(n, 1), ref_passed, n,
                                                    mod_passed, modules))
    return passed == ref_passed


def build(c_backend, out):
    argv = [OBXC, os.path.join(OBX, 'cmd', 'jq.obx')] + ([] if c_backend else ['--native']) + ['-o', out]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('build failed:\n' + r.stdout + r.stderr)


def find_ref(arg):
    for c in (arg, os.environ.get('JQ_REF'), shutil.which('jq')):
        if c and os.path.isfile(c):
            r = subprocess.run([c, '--version'], capture_output=True, text=True)
            if r.stdout.strip() == 'jq-1.7.1':
                return os.path.abspath(c)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--c', action='store_true', help='build through C instead of native')
    ap.add_argument('-k', default='')
    ap.add_argument('-v', action='store_true')
    ap.add_argument('-j', type=int, default=8)
    ap.add_argument('--ref')
    ap.add_argument('--exe', help='a built jq to test instead of building')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--jqtest', action='store_true', help="also run jq's tests/jq.test, fetched from GitHub")
    ap.add_argument('--jqtest-file', help='a local copy of jq.test (implies --jqtest)')
    ap.add_argument('--jqtest-strict', action='store_true', help='fail when jq.test passes fewer than the reference')
    a = ap.parse_args()
    if a.list:
        for c in CASES:
            print(c.name)
        return 0
    ref = find_ref(a.ref)
    if not ref:
        print('no reference jq 1.7.1: pass --ref, set JQ_REF or put jq 1.7.1 on PATH', file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix='jqbuild')
    try:
        ours = a.exe or os.path.join(tmp, 'jq')
        if not a.exe:
            build(a.c, ours)
        ours = os.path.abspath(ours)
        r = Runner(ours, ref, a.v)
        cases = [c for c in CASES if a.k in c.name]
        fails = 0
        with concurrent.futures.ThreadPoolExecutor(a.j) as ex:
            for c, res in zip(cases, ex.map(r.run_case, cases)):
                if res:
                    fails += 1
                    print('FAIL %s: %s' % (c.name, res))
                elif a.v:
                    print('ok   %s' % c.name)
        print('%d passed, %d failed (%s build, reference %s)' % (len(cases) - fails, fails, 'C' if a.c else 'native', ref))
        ok = fails == 0
        if a.jqtest or a.jqtest_file:
            same = jqtest(a.jqtest_file, ours, ref, a.j, a.v)
            if a.jqtest_strict and not same:
                ok = False
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
