# JSON for obx

Status: built, 2026-11-05 (branch `jq`). The library is `lib/json.obx`,
tested by `lib/tests/run/json_*.obx` and `tests/jsonsuite.py`. Its first
user is `cmd/jq.obx`, a port of jq 1.7.1. No language change was needed.

## 0. Goal

`import "json"` reads and writes JSON (RFC 8259). It should be pleasant for
programs that handle API payloads and configuration files, exact for money,
and complete enough to carry jq. jq decides every case where JSON leaves room
for choice: the key order of objects, how numbers print, and what happens to
invalid UTF-8.

```obx
import "json"

let v = try json.parse(`{"name": "obx", "tags": ["a", "b"], "price": 12.50}`)
println(v.field("name").asStr().unwrapOr("?"))     // obx
let price = v.field("price").asDecimal()            // Some(12.50), exact
for t in v.field("tags").items() { println(t.asStr().unwrap()) }

var out = json.object()
out.set("ok", json.boolean(true))
out.set("ids", json.arrayOf([json.integer(1), json.integer(2)]))
println(out.toStr())                                // {"ok":true,"ids":[1,2]}
print(json.write(&out, &json.pretty()))             // two spaces per level
```

## 1. The value

```obx
pub type Json = value closed { Null; Bool(bool); Num(Number); Str(str); Arr(Array); Obj(Object) }
pub type Number = value record { f: float; text: str }
```

**A Json is a value, with shared copy-on-write arrays and objects.** `Array`
and `Object` are handles to a counted box. `clone()` shares the box and adds
one to its count. Changing a Json whose box is shared (`set`, `push`,
`remove`, `setAt`) copies the box first. Small values are cheap to copy and
large ones are shared, so a Json can be stored, returned and passed around
without thinking about ownership. Every reader returns an owned Json:
`field`, `item` and `items()` hand out shared copies, not borrows.

This matters most for jq. jq values are immutable, and `.a.b = 1` on a large
document must not copy the whole document. With copy on write, only the path
that changes is copied, and only where its boxes are shared. jq's own `jv`
type works the same way.

The alternative is a plain owning tree (`[]Json`, a Map), as obx's other
records are. It was rejected because each `field()` would either deep-copy
the value or return a borrow. Borrows into a tree cannot outlive a change to
the tree, so jq's evaluator, which keeps values in environments and on
generator stacks, would have to copy everything.

The boxes use `intoPtr`/`fromPtr`, the same unchecked boxing as `std.Map`'s
internals. That is the one unsafe corner. It is contained in about 150
lines: `newArr`, `newObj`, `clone`, `drop` and the mutators. Leak freedom is
checked under `OBX_CHECK=1` by every json test, by the JSONTestSuite run, by
the benchmark driver and by the jq harness, on both backends.

**Review point.** A borrow-class view (`&Json` into a box) would make
`items()` and `field()` free. Today they cost a count increment and, for
`items()`, an allocated `[]Json`. `eachItem`, `eachEntry` and `withItems`
lend the elements instead. A language-level answer would be a borrow that
can be returned from a method whose receiver is borrowed; borrow-class types
(branch `borrow-types-v2`) may give one.

## 2. Objects: insertion order, lookup by key

An object is two parallel arrays, `keys: []str` and `vals: []Json`, in the
order the keys were first set. Objects with more than 16 keys also get an
open-addressing index: slots holding -1 or an entry number, a power of two
in size, at most half full, hashed with `str.hash()` (SipHash, keyed per
process, as `std.Map`).

- **Insertion order** is what jq, JavaScript and Python keep, and what
  people expect to see when a document is rewritten. Setting a key again
  keeps its place. `remove` closes the gap and rebuilds the index.
- **Small objects scan.** Most objects in practice have under 16 keys, and
  a linear scan over short strings beats hashing them. Below the threshold
  there is no index at all, so a small object costs two arrays.
- **Large objects** (a map of ids, say) find a key in O(1) through the
  index, so building an object of n keys is O(n), not O(n²).
- **Duplicate keys** in parsed text: the last value wins, at the first
  key's position, as in jq and Python.

`sortKeys` in the write options sorts at writing time. jq's `keys`,
comparison and `-S` sort by bytes, and so does `cmp()` here.

The alternatives were `std.Map` (no order), a sorted vector (jq before 1.5
sorted keys, which people disliked), and an ordered map with a linked list
(more memory, more pointers). Two arrays plus an optional index is what
Python's dict does (a compact entry array and a sparse index), scaled down.

## 3. Numbers: the literal text and a float

A parsed number keeps the text it was written with and the nearest double,
computed at parse time. jq 1.7 does the same with decNumber literals.

- **Writing** writes the text, so `12.50`, `1.000` and
  `100000000000000000001` come back exactly as written. Numbers built from
  a float (`json.number(f)`) have no text and are written with the shortest
  text that reads back as the same double.
- **Reading.** `asFloat()` is the double. `asInt()` and `asDecimal()` read
  the text exactly: `asDecimal()` of `12.50` is the Decimal 12.50, not
  12.5000000000000000001. This is the finance case. `json.decimal(d)` builds
  a number from a Decimal with its exact text.
- **Comparing.** `cmp`/`eq` compare the doubles first. When the doubles are
  equal and both numbers have text, they compare the decimal values of the
  texts exactly. So `1.0 == 1` and `0.0001e4 == 1`, but
  `100000000000000000001 > 100000000000000000000`, as in jq 1.7.1.
- **jq's form.** `WriteOptions.jqNumbers` writes numbers the way jq 1.7.1
  does: the literal in decNumber's canonical form (`1E+2`, `0.00005570…`),
  and a computed double with 17 significant digits (`1e+100`). `cmd/jq`
  uses it. Without it the literal is written as given.
- **Lenient parsing** (`ParseOptions.lenient`) accepts what jq 1.7.1
  accepts: `nan`, `NaN`, `infinity`, leading zeros, a leading `+`, `1.` and
  `.5`.

## 4. Strings, escapes and UTF-8

Strings are `str` (bytes). Parsing handles every escape and joins surrogate
pairs.

- A high surrogate without a low one after it is an error, as in jq 1.7.1.
- A lone low surrogate becomes U+FFFD, as in jq.
- Invalid UTF-8 inside a string is replaced by U+FFFD using jq's rule (its
  `jvp_utf8_next`): a bad lead byte or a stray continuation byte gives one
  U+FFFD; a lead byte with the continuation bytes after it gives one; and a
  whole well-shaped sequence that is overlong, a surrogate or above U+10FFFF
  gives one.
- `ParseOptions.strictUtf8` makes all of these errors instead.
- Writing applies the same replacement, so output is always valid UTF-8.
- A string without escapes and with valid UTF-8 shares the input's bytes
  (a slice, no copy). That is the fast path of the parser.

Writing escapes `"`, `\` and the control characters (`\n`, `\t` and so on,
and `\u00XX` for the rest), and DEL as `\u007f`, as jq does. With `ascii`,
every non-ASCII character becomes `\uXXXX`, with surrogate pairs above
U+FFFF (jq's `-a`).

## 5. Parsing and its errors

- `parse(s): Result[Json, Error]` reads exactly one value; trailing text is
  an error.
- `parseWith(s, options)` takes the options: `maxDepth`, default 512;
  `strictUtf8`; `lenient`.
- `decoder(s)` reads a sequence of values separated by whitespace, such as
  JSON lines or jq's input. `next()` returns `Result[Option[Json], Error]`.

The parser is recursive descent over bytes, with nesting depth bounded by
`maxDepth`.

An `Error` carries:

- the kind: `Eof`, `Syntax`, `Literal`, `Number`, `Escape`, `Unicode`,
  `Surrogate`, `Control`, `Utf8`, `Depth`, `Trailing`;
- a message;
- the byte offset of the problem;
- the line and column there, both counted from 1, the column in bytes.

`toStr()` gives `json: expected ',' or ']' at line 3, column 7`. The kinds
let a program tell truncated input (`Eof`) from bad input. `cmd/jq` keeps
jq's own messages (`jq_lib.jqParseError` follows the state machine of jq's
`jv_parse.c`) and uses the library only for the values.

## 6. Writing

- `write(&j, &options): str` returns the text.
- `writeTo(mut &sink, &j, &options)` streams to any `Sink`, which is
  `io.Writer` or `json.Buf`, so large documents do not need to be built in
  memory.
- `j.toStr()` is the compact form; `j.pretty()` uses two spaces per level.

`WriteOptions` has these fields, all of which `cmd/jq`'s options map to:

| Field | Meaning |
|---|---|
| `indent` | spaces per level; 0 writes compactly, with no spaces at all |
| `tab` | one tab per level |
| `sortKeys` | sort object keys |
| `ascii` | escape every non-ASCII character |
| `raw` | write a string at the top level as its bytes, unquoted (jq's `-r`) |
| `jqNumbers` | write numbers as jq 1.7.1 does (section 3) |

Indented output matches jq's and Python's `indent=2`: `": "` after keys,
and empty arrays and objects written as `[]` and `{}`.

## 7. Builders and accessors

| Kind | API |
|---|---|
| Make | `null()`, `boolean(b)`, `integer(n)`, `number(f)`, `decimal(d)`, `string(s)`, `numberLiteral(text)`, `arrayOf([...])`, `emptyArray()`, `object()`, `strings(&xs)` |
| Chain | `json.object().with("a", json.integer(1)).with("b", …)` |
| Read | `kind()`, `isNull/isArray/isObject`, `asBool/asStr/asInt/asFloat/asDecimal/asNumber` (Options), `size()`, `at(i)`/`item(i)` (negative from the end), `get(k)`/`field(k)` (Option / null), `has(k)`, `path(&["a", "0", "b"])`, `items()`, `keys()`, `keyAt(i)`/`valueAt(i)` |
| Visit | `eachItem`, `eachEntry`, `withItems` |
| Change | `push`, `extend`, `setAt`, `pop`, `set`, `remove` |
| Compare | `cmp` (jq's order: null < false < true < numbers < strings < arrays < objects; objects by sorted keys, then by values) and `eq` |

`field` and `item` return null for a missing key or a wrong kind, as jq's
`.k` does. Chains like `v.field("a").field("b").asStr()` therefore need one
check at the end, not one per step.

## 8. Tests

- `lib/tests/run/json_parse.obx`: values, every error kind with its
  position, invalid UTF-8 the way jq replaces it, depth limits, lenient
  numbers, and the decoder.
- `json_build.obx`: builders, copy on write, key order and the index past
  16 keys, `cmp` and `eq`, and exact number comparison, including leading
  zeros (`0.0001e4 == 1`).
- `json_write.obx`: compact and indented output, tabs, sorted keys, ASCII,
  raw, escapes, shortest doubles, and jq's number forms.
- `tests/jsonsuite.py` runs [JSONTestSuite](https://github.com/nst/JSONTestSuite).
  The suite is cloned at test time (or taken from `--suite DIR`) and not
  committed. Every file that parses must also round-trip: its compact text
  must parse to an equal value.
  - **Result, native and C:** all 95 `y_` files and all 188 `n_` files
    pass. Of the 35 `i_` files, 24 are accepted and 11 refused: invalid
    UTF-8 is accepted (replaced), and so are numbers out of a double's range
    and 500 nested arrays. The refused files are 7 with a high surrogate not
    followed by a low one, 3 UTF-16 texts, and 1 UTF-8 text with a BOM.
    `cmd/jq` strips the BOM itself, as jq does.
- `cmd/jq.obx` and its harness (`tests/jq.py`) exercise the library further
  against the real jq.

## 9. Benchmark

`python3 obx/bench/json/bench.py` generates an 80 MB file with
`gen_data.py`, using a fixed seed: one array of 182,290 API-like records
with strings (some with escapes and non-ASCII text), integers, decimals,
floats, booleans, nulls, nested objects and arrays. It produces two tables.

The machine was shared with other agents (WSL2, x86-64), so the times are
best of 3 and vary by 10–20% between runs.

### Library, in process

The text is already in memory. Times are in ms.

| step | obx native | obx C | Python 3 `json` |
|---|---:|---:|---:|
| parse | 1249 | 725 | 862 |
| compact | 830 | 345 | 1028 |
| pretty | 957 | 423 | 2951 |
| query: sum `qty` through `items()`/`field()` | 331 | 110 | 48 |
| visit: the same through `eachItem` | 226 | 91 | 48 |

- **Correctness check.** The library's compact output, with `jqNumbers`, is
  byte-for-byte identical to `jq -c .`'s.
- **Parsing.** The C build parses faster than Python's C accelerator. It
  writes 3× faster compactly and 7× faster indented: Python's indented
  writer is pure Python.
- **Native backend.** It is 1.7–2.4× slower than the C build on this
  library. That is unusual, since the native backend is ahead on the small
  benchmarks. The library's inner loops (byte scanning, `StrBuf` appends,
  enum matching with counted boxes) are where gcc's register allocation
  and inlining pay off. This is a candidate for the native performance
  list, not something to fix in the library.
- **Queries.** These are the weak spot. Each `field()` scans for the key
  and returns a counted copy, and `items()` allocates. Python's dict
  lookup and borrowed references are cheaper. The `eachItem` form helps a
  little. Borrowing accessors (the review point in section 1) would help
  most.

### jq: whole processes

Times are in seconds; output goes to a file, and every column's output must
be identical.

| filter | jq 1.7.1 | obx jq native | obx jq C |
|---|---:|---:|---:|
| `.` | 2.59 | 1.83 | 1.89 |
| `-c .` | 2.18 | 1.75 | 1.74 |
| `length` | 1.49 | 1.21 | 1.15 |
| `[.[].items[].qty] \| add` | 1.84 | 1.58 | 1.46 |
| `map(select(.active and .score > 0) \| {id, city: .address.city})` | 1.93 | 1.48 | 1.49 |
| `group_by(.address.city) \| map(…)` | 2.15 | 2.36 | 2.19 |

jq spends most of its time parsing with decNumber literals. The port is
15–30% faster on every filter except `group_by`, where jq's sort, written
in C, is a little ahead of the port's sort.
