#!/usr/bin/env python3
"""Experimental in-process C interop for obo.

    python3 obo/experimental/cinterop/cinterop.py build SPEC APP.obx --out DIR [--obo PATH]
    python3 obo/experimental/cinterop/cinterop.py verify MANIFEST

SPEC is a small JSON file (format cinterop-proto/1). This is a trusted-build
prototype, not a public API. It generates an assembly thunk for each scalar
function and either generates a simple .obx wrapper or copies the maintained
wrapper named by "wrapper"; it checks the typed sidecar and builds a real
system-linked non-PIE ELF. The compiler's experimental __cinterop builtin emits
a call-site arity record; this tool checks every record against the spec before
the link. Hashes are provenance/corruption checks, not a signature or a
memory-safety proof.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OBO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_OBO = os.path.join(OBO_ROOT, 'obo')
DEFAULT_RT = OBO_ROOT
RT_HOST_EXIT_SLOT = 59
RT_FOREIGN_CENSUS_SLOT = 94

CODE_BASE = 0x401000
IMAGE_BASE = 0x400000
RODATA_BASE = 0x08000000
GLOBAL_BASE = 0x0C000000
RT_TEXT = 0x10000000
RT_DATA = 0x20000000
PLATFORM = 'x86_64-linux-gnu'
ABI = 'sysv-amd64'
FORMAT_V1 = 'cinterop-proto/1'
FORMAT_V2 = 'cinterop-proto/2'
FORMATS = (FORMAT_V1, FORMAT_V2)
FORMAT = FORMAT_V1
RELOC_HEADER_V1 = 'cinterop-relocs 1'
RELOC_HEADER_V2 = 'cinterop-relocs 2'
MAX_ARGS = 8
MAX_KIND_ARGS = 12        # kinds bindings: C-parameter descriptors per function (stage (a))
MAX_KIND_INDEX = 16       # obo argument indexes: @k must be below this (the compiler checks the same bound)
ARG_REGS = ('%rdi', '%rsi', '%rdx', '%rcx', '%r8', '%r9')
EFFECTS = ('freesall', 'writeshandles')
SIG_MODES = ('header', 'declarations-only')
# Argument kinds (stage (a), design section 3.1). A binding whose args are all bare i64 is the int form,
# and its calls carry no descriptor. Any other binding uses kinds: each entry names the obo argument it
# reads as kind@k, and the call site passes the same list as a descriptor string (see nativeKindCall in
# obo/src/obo.obo). The sidecar records the list, and the tool compares it with the spec.
BYTES_KINDS = ('borrow_bytes', 'borrow_cstr')
# Return kinds. /1 keeps the int-only pair; /2 adds copy-out and typed results (stage (b)).
RET_KINDS_V1 = ('i32', 'i64')
RET_KINDS_V2 = ('void', 'i32', 'u32', 'i64', 'nonzero', 'cbytes', 'cstr_out')
POINTER_RET_KINDS = ('nonzero', 'cbytes', 'cstr_out')   # require "ret_ctype"
# A native fn call records a shape per obo argument, not an exact kind. The thunk's spec decides i32 vs
# u32 and so on; the shape only fixes the words the compiler pushes and the C parameter shape.
FN_SHAPES = ('int', 'bytes', 'ints', 'byteslist')
FN_RET_SHAPES = ('i64', 'void', 'bytes', 'optbytes')
# A foreign type needs a spec `types` entry; the interface table is generated in sidecar order.
FOREIGN_MAX = 64
# The internal host helpers a `host` relocation may name, with their fixed arity. They are not spec
# functions; the tool generates their thunks and links them like any other reloc.
HOST_HELPERS = {
    'obo_ci_foreign_own': 4,
    'obo_ci_foreign_close': 5,
    'obo_ci_foreign_is_open': 1,
    'obo_ci_foreign_census_adjust': 2,
}
# Shape a spec return kind gives a native fn call (own/own_opt are resolved through `types`).
RET_SHAPE_OF = {
    'i32': 'i64', 'i64': 'i64', 'u32': 'i64', 'nonzero': 'i64',
    'void': 'void', 'cstr_out': 'bytes', 'cbytes': 'optbytes',
}
# A const:<n> magnitude is at most 17 digits (99999999999999999); the compiler checks the same bound.
CONST_LIMIT = 99999999999999999
# The C parameters each kind expands to (the sigcheck types and the thunk's arguments). The null and const
# entries are only a count: their sigcheck type is the spec's ctypes annotation (see sig_params), because a
# null or a const:<n> may be passed for any pointer or integer parameter.
KIND_CTYPES = {
    'i64': ['long'], 'i32': ['int'], 'u32': ['unsigned int'],
    'null': ['void *'], 'const': ['void *'],
    'borrow_bytes': ['const void *', 'int'], 'borrow_cstr': ['const char *'],
    'ints:i32': ['const int *'], 'ints:u32': ['const unsigned int *'], 'ints:len': ['int'],
    'nil_bytes_list': ['const char *const *', 'const int *'],
}
# Hosted cinterop links get full RELRO and immediate binding by default. A
# missing or incompatible shared-library symbol then fails at load time instead
# of during a request. The separate freestanding runtime/seed build is not
# touched. The final ELF is re-checked by validate_final/verify_doc.
LINK_HARDENING_FLAGS = ['-Wl,-z,relro', '-Wl,-z,now', '-Wl,-z,noexecstack']
PT_DYNAMIC = 2
PT_GNU_RELRO = 0x6474e552
DT_NULL = 0
DT_BIND_NOW = 24
DT_FLAGS = 30
DT_FLAGS_1 = 0x6ffffffb
DF_BIND_NOW = 0x8
DF_1_NOW = 0x1
REQUIRED_INPUT_KEYS = {
    'spec', 'app', 'wrapper', 'shim_source', 'signature_check_source',
    'host_source', 'host_stack_source', 'host_stack_header',
    'host_abi_source', 'foreign_abi_header', 'start_source', 'link_script', 'runtime_source',
    'runtime_entry', 'runtime_link_script', 'tool', 'compiler', 'cc',
}
REQUIRED_BUILD_KEYS = REQUIRED_INPUT_KEYS | {
    'wrapper_sym', 'shim_object', 'signature_check_object', 'host_object',
    'host_stack_object', 'start_object', 'runtime_text', 'runtime_data',
    'foreign_table_source', 'foreign_table_object',
    'image', 'relocs', 'native_object', 'executable', 'input_manifest',
}


class ToolError(Exception):
    pass


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise ToolError("command failed: %s\n%s" % (' '.join(cmd), (p.stderr or p.stdout).strip()))
    return p


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def require_file(path, what):
    if not os.path.isfile(path):
        raise ToolError("%s not found: %s" % (what, path))
    return os.path.realpath(path)


def resolve_input(spec_dir, p, what):
    path = p if os.path.isabs(p) else os.path.join(spec_dir, p)
    return require_file(path, what)


def check_flag_text(value, what):
    if not isinstance(value, str) or not value:
        raise ToolError("%s entries must be non-empty strings" % what)
    if '\0' in value or '\n' in value or '\r' in value:
        raise ToolError("%s entry contains a NUL or newline: %r" % (what, value))
    return value


def _inside(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def resolve_include_dir(spec_dir, p):
    """Resolve an include directory and reject implicit relative escapes.

    A relative include is allowed only when it resolves inside the spec
    directory or the obo tree (so a package may use ../shared). An absolute
    path is explicit and is allowed, but it must be a directory. Symlink
    escapes are rejected too: realpath is checked, not just the spelling.
    """
    check_flag_text(p, 'include_dirs')
    if os.path.isabs(p):
        real = os.path.realpath(p)
    else:
        norm = os.path.normpath(p)
        if os.path.isabs(norm) or norm == os.pardir or norm.startswith(os.pardir + os.sep):
            raise ToolError("include_dirs %r escapes the spec directory; "
                            "list an absolute path explicitly" % p)
        real = os.path.realpath(os.path.join(spec_dir, p))
        allowed = (os.path.realpath(spec_dir), os.path.realpath(OBO_ROOT))
        if not any(_inside(real, root) for root in allowed):
            raise ToolError("include_dirs %r resolves outside the spec directory "
                            "and obo tree; list an absolute path explicitly" % p)
    if not os.path.isdir(real):
        raise ToolError("include directory not found: %s" % p)
    return real


def resolve_header(spec_dir, include_dirs, p, what):
    """Resolve a header by the spec directory first, then include_dirs."""
    check_flag_text(p, what)
    if os.path.isabs(p):
        return require_file(p, what)
    candidates = [os.path.join(spec_dir, p)]
    candidates += [os.path.join(d, p) for d in include_dirs]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.realpath(path)
    raise ToolError("%s not found: %s (searched the spec directory and include_dirs)"
                    % (what, p))


def include_flags(include_dirs):
    out = []
    for d in include_dirs:
        out += ['-I', d]
    return out


def path_map_flags(roots):
    """Keep absolute build roots out of object diagnostics/debug info.

    Sources and headers may live outside the spec directory (a maintained
    wrapper plus a private sysroot, or a test fixture in the repository with a
    generated spec in a temp dir). Map every root, not only include_dirs.
    """
    out = []
    seen = set()
    for root in roots:
        real = os.path.realpath(root)
        if real in seen or real == os.sep:
            continue
        seen.add(real)
        out.append('-ffile-prefix-map=%s=/cinterop-root-%d' % (real, len(seen) - 1))
    return out


def load_spec(path):
    try:
        spec = json.load(open(path))
    except (OSError, ValueError) as e:
        raise ToolError("cannot read spec %s: %s" % (path, e))
    fmt = spec.get('format')
    if fmt not in FORMATS:
        raise ToolError("spec format is %r, expected %r or %r"
                        % (fmt, FORMAT_V1, FORMAT_V2))
    if fmt == FORMAT_V2:
        mode = spec.get('signature_check')
        if mode not in SIG_MODES:
            raise ToolError("a cinterop-proto/2 spec needs \"signature_check\": \"header\" or "
                            "\"declarations-only\"")
        if mode == 'header' and not spec.get('headers'):
            raise ToolError("signature_check 'header' requires \"headers\"")
        if mode == 'declarations-only' and not spec.get('declarations'):
            raise ToolError("signature_check 'declarations-only' requires explicit \"declarations\" "
                            "(there is no header to check against)")
    if spec.get('platform') != PLATFORM:
        raise ToolError("platform is %r, expected %r" % (spec.get('platform'), PLATFORM))
    if spec.get('abi') != ABI:
        raise ToolError("abi is %r, expected %r" % (spec.get('abi'), ABI))
    mod = spec.get('module')
    if not isinstance(mod, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', mod):
        raise ToolError("module must be a C/obo identifier")
    funcs = spec.get('functions')
    if not isinstance(funcs, list) or not funcs:
        raise ToolError("functions must be a non-empty list")
    type_by_name, type_by_obo = spec_types(spec)
    seen_names = set()
    seen_symbols = set()
    for f in funcs:
        for k in ('name', 'symbol', 'args', 'ret'):
            if k not in f:
                raise ToolError("function entry needs %r" % k)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', f['name']):
            raise ToolError("bad obo function name %r" % f['name'])
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', f['symbol']):
            raise ToolError("bad C symbol %r" % f['symbol'])
        if f['name'] in seen_names:
            raise ToolError("duplicate function %r" % f['name'])
        if f['symbol'] in seen_symbols:
            raise ToolError("duplicate C symbol %r" % f['symbol'])
        seen_names.add(f['name'])
        seen_symbols.add(f['symbol'])
        ret_base, ret_type = parse_ret(f['ret'])
        if fmt == FORMAT_V1:
            if f['ret'] not in RET_KINDS_V1:
                raise ToolError("%s: ret must be i32 or i64" % f['name'])
        elif ret_base not in RET_KINDS_V2 and ret_base not in ('own', 'own_opt'):
            raise ToolError("%s: ret must be one of %s, own<C> or own_opt<C>"
                            % (f['name'], ', '.join(RET_KINDS_V2)))
        if ret_base in ('own', 'own_opt') and ret_type not in type_by_name:
            raise ToolError("%s: %s names C type %r, which is not in types"
                            % (f['name'], f['ret'], ret_type))
        if f['ret'] in POINTER_RET_KINDS:
            if 'ret_ctype' not in f:
                raise ToolError("%s: ret %r needs \"ret_ctype\", the exact C return type "
                                "(for example \"const char *\")" % (f['name'], f['ret']))
            grammar = ("an object pointer type written with '*' as [const] T *... "
                       "(a typedef that hides the pointer, a pointer to a function pointer and a "
                       "pointer to an array are refused)")
            try:
                info = parse_ctype(f['ret_ctype'])
            except ToolError as e:
                raise ToolError("%s: ret_ctype must spell %s: %s" % (f['name'], grammar, e))
            if info[0] != 'ptr':
                raise ToolError("%s: ret_ctype must spell %s, not %r"
                                % (f['name'], grammar, f['ret_ctype']))
            if '(*)' in re.sub(r'\s+', '', f['ret_ctype']):
                # The thunk treats a pointer-returning result as a data pointer in rax, never calls it.
                raise ToolError("%s: ret_ctype must spell %s, not a function pointer"
                                % (f['name'], grammar))
        if f['ret'] == 'cbytes':
            length = f.get('length')
            if not isinstance(length, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', length):
                raise ToolError("%s: cbytes needs \"length\": the C length function symbol" % f['name'])
            cap = f.get('cap')
            if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
                raise ToolError("%s: cbytes needs \"cap\": a positive integer" % f['name'])
        if not isinstance(f['args'], list) or len(f['args']) > MAX_KIND_ARGS:
            raise ToolError("%s: args must be a list of at most %d entries" % (f['name'], MAX_ARGS))
        if all(a == 'i64' for a in f['args']):
            if len(f['args']) > MAX_ARGS:
                raise ToolError("%s: args must be a list of at most %d entries" % (f['name'], MAX_ARGS))
            if 'ctypes' in f:
                raise ToolError("%s: ctypes is for argument kinds; an int-form binding (all i64) has no "
                                "null or const slot, so drop it" % f['name'])
        else:
            if any(a == 'i32' for a in f['args']):
                raise ToolError("%s: i32 arguments need an argument index: write i32@k, with a kinds descriptor "
                                "at the call" % f['name'])
            if not all(isinstance(a, str) for a in f['args']):
                raise ToolError("%s: args must be i64, or a kind such as i64@0" % f['name'])
            for a in f['args']:
                # own/out_* are return or out-slot kinds; only borrow<C> is an argument kind.
                if a.startswith('own') or a.startswith('out_own') or a.startswith('out_offset'):
                    raise ToolError("%s: %s is not an argument kind" % (f['name'], a))
            check_kinds(f['name'], f['args'])
            for a in f['args']:
                k = parse_kind(a)
                if k['name'] == 'borrow' and k['flag'] not in type_by_name:
                    raise ToolError("%s: %s names C type %r, which is not in types"
                                    % (f['name'], a, k['flag']))
            ct = f.get('ctypes')
            if ct is not None:
                if not isinstance(ct, list) or len(ct) != len(f['args']):
                    raise ToolError("%s: ctypes must be a list with one entry per argument" % f['name'])
                if not all(x is None or isinstance(x, str) for x in ct):
                    raise ToolError("%s: ctypes entries must be strings or null" % f['name'])
                check_ctypes(f['name'], f['args'], ct)
            else:
                check_ctypes(f['name'], f['args'], [None] * len(f['args']))
            if 'wrapper' not in spec:
                raise ToolError("%s: a binding with argument kinds needs a maintained wrapper "
                                "(the \"wrapper\" key)" % f['name'])
        if f['ret'] not in ('i32', 'i64') and 'wrapper' not in spec:
            raise ToolError("%s: ret %r needs a typed native fn declaration, so the binding needs a "
                            "maintained wrapper (the \"wrapper\" key)" % (f['name'], f['ret']))
        effects = f.get('effects', ['impure'])
        if not isinstance(effects, list) or any(e != 'impure' for e in effects):
            raise ToolError("%s: only the conservative 'impure' effect is supported" % f['name'])
        for bad in ('variadic', 'struct_by_value', 'tls', 'floats', 'callbacks'):
            if f.get(bad):
                raise ToolError("%s: %s is not supported by this prototype" % (f['name'], bad))
    for key in ('sources', 'headers', 'libs', 'cflags', 'declarations',
                'include_dirs'):
        if key in spec and not isinstance(spec[key], list):
            raise ToolError("%s must be a list" % key)
    for key in ('libs', 'cflags', 'include_dirs'):
        for value in spec.get(key, []):
            check_flag_text(value, key)
    if 'wrapper' in spec:
        if not isinstance(spec['wrapper'], str) or not spec['wrapper']:
            raise ToolError("wrapper must be a source path")
    if 'wrapper_sources' in spec:
        if not isinstance(spec['wrapper_sources'], list) or any(
                not isinstance(x, str) or not x for x in spec['wrapper_sources']):
            raise ToolError("wrapper_sources must be a list of source paths")
    if 'declaration_files' in spec:
        if not isinstance(spec['declaration_files'], list) or any(
                not isinstance(x, str) or not x for x in spec['declaration_files']):
            raise ToolError("declaration_files must be a list of source paths")
    if not spec.get('headers') and not spec.get('declarations'):
        raise ToolError("a header or explicit declaration is required for every binding")
    return spec


def parse_kind(tok):
    """One argument kind: name, arg (obo argument index or None), flag, val (const only)."""
    if tok == 'null':
        return {'name': 'null', 'arg': None, 'flag': None, 'val': None}
    m = re.fullmatch(r'const:(-?[0-9]+)', tok)
    if m:
        val = int(m.group(1))
        if abs(val) > CONST_LIMIT:
            raise ToolError("const value %s is too large (at most 17 digits)" % m.group(1))
        return {'name': 'const', 'arg': None, 'flag': None, 'val': val}
    m = re.fullmatch(r'borrow<([A-Za-z_][A-Za-z0-9_]*)>@([0-9]+)', tok)
    if m:
        return {'name': 'borrow', 'arg': int(m.group(2)), 'flag': m.group(1), 'val': None}
    m = re.fullmatch(r'(i64|i32|u32|borrow_bytes|borrow_cstr|ints|nil_bytes_list)@([0-9]+)(?::([a-z0-9]+))?', tok)
    if not m:
        raise ToolError("unknown argument kind %r (args must be i64, or a kind such as i64@0)" % tok)
    name, arg, flag = m.group(1), int(m.group(2)), m.group(3)
    if name == 'ints':
        if flag not in ('i32', 'u32', 'len'):
            raise ToolError("ints needs a flag: ints@%d:i32, ints@%d:u32 or ints@%d:len" % (arg, arg, arg))
    elif flag is not None:
        raise ToolError("%s takes no flag: %r" % (name, tok))
    return {'name': name, 'arg': arg, 'flag': flag, 'val': None}


def kind_key(k):
    return k['name'] + (':' + k['flag'] if k['flag'] else '')


def check_kinds(fname, args):
    """A kinds binding: every obo argument has exactly one data kind, and ints:len counts a list."""
    if len(args) > MAX_KIND_ARGS:
        raise ToolError("%s: at most %d argument kinds" % (fname, MAX_KIND_ARGS))
    kinds = [parse_kind(a) for a in args]
    for k in kinds:
        if k['arg'] is not None and k['arg'] >= MAX_KIND_INDEX:
            raise ToolError("%s: argument index @%d is out of range (indexes run from 0 to %d)"
                            % (fname, k['arg'], MAX_KIND_INDEX - 1))
    data = {}
    for k in kinds:
        if k['arg'] is None or (k['name'] == 'ints' and k['flag'] == 'len'):
            continue
        if k['arg'] in data:
            raise ToolError("%s: argument %d has two kinds" % (fname, k['arg']))
        data[k['arg']] = k
    for k in kinds:
        if k['name'] == 'ints' and k['flag'] == 'len':
            if k['arg'] not in data or data[k['arg']]['name'] not in ('ints', 'nil_bytes_list'):
                raise ToolError("%s: ints:len counts a list, and argument %d is not one" % (fname, k['arg']))
    if kinds and not data and not any(k['name'] in ('null', 'const') for k in kinds):
        raise ToolError("%s: no argument kind" % fname)
    for i in range(max(data) + 1 if data else 0):
        if i not in data:
            raise ToolError("%s: argument %d has no kind" % (fname, i))
    return kinds


# Integer types a null or const slot may be annotated with, besides pointers. A struct by value would pass
# the sigcheck and then the register-passing thunk would be wrong, so it is not on this list.
# Integer C types a null or const slot may take, with their width and signedness (x86_64 Linux). A const
# value must fit the type: unsigned types take no negative value.
C_INT_RANGE = {
    'char': (8, True), 'signed char': (8, True), 'unsigned char': (8, False),
    'short': (16, True), 'unsigned short': (16, False),
    'int': (32, True), 'unsigned int': (32, False),
    'long': (64, True), 'unsigned long': (64, False),
    'long long': (64, True), 'unsigned long long': (64, False),
    'size_t': (64, False), 'ssize_t': (64, True),
    'int32_t': (32, True), 'uint32_t': (32, False), 'int64_t': (64, True), 'uint64_t': (64, False),
}
C_INT_WORDS = ('unsigned', 'signed', 'int', 'long', 'short', 'char')
C_KEYWORDS = ('const', 'struct', 'void') + C_INT_WORDS + (
    'float', 'double', 'volatile', 'restrict', 'static', 'extern', 'inline', 'typedef', 'enum', 'union',
    'return', 'sizeof', 'bool', '_Bool')


def _c_tokens(t):
    """Tokens of a C type name, or None if the text has anything but identifiers, '*', '(', ')', ',' and
    spaces. The text goes into the sigcheck source, so nothing else may reach it."""
    if not isinstance(t, str) or len(t) > 120:
        return None
    toks = re.findall(r'[A-Za-z_][A-Za-z0-9_]*|[*(),]', t)
    if ''.join(toks) != re.sub(r'\s+', '', t):
        return None
    return toks


def _c_base(toks, i):
    """A base type at toks[i] (a leading const is allowed): (name, next index), or None."""
    n = len(toks)
    if i < n and toks[i] == 'const':
        i += 1
    if i >= n:
        return None
    if toks[i] == 'struct':
        if i + 1 < n and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', toks[i + 1]) and toks[i + 1] not in C_KEYWORDS:
            return 'struct ' + toks[i + 1], i + 2
        return None
    if toks[i] == 'void':
        return 'void', i + 1
    words = []
    while i < n and toks[i] in C_INT_WORDS:
        words.append(toks[i])
        i += 1
    if words:
        return ' '.join(words), i
    if i >= n:
        return None
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', toks[i]) and toks[i] not in C_KEYWORDS:
        return toks[i], i + 1
    return None


def _c_plain(toks, i):
    """One object type at toks[i]: (base, stars, next index), or None. A const may follow the base
    and each star. A name after the type (a declarator) is not part of it, so the caller sees it."""
    b = _c_base(toks, i)
    if b is None:
        return None
    base, j = b
    while j < len(toks) and toks[j] == 'const':
        j += 1
    stars = 0
    while j < len(toks) and toks[j] == '*':
        stars += 1
        j += 1
        while j < len(toks) and toks[j] == 'const':
            j += 1
    return base, stars, j


def _c_value_type(base, stars):
    """Classify one plain type that is a parameter, a return value or a whole slot type: ('int', name) or
    ('ptr',). A bare void is no value type; a bare integer must be in the table, so a struct by value is
    refused (the thunk passes every slot in a register)."""
    if stars > 0:
        return ('ptr',)
    if base == 'void':
        return None
    if base in C_INT_RANGE:
        return ('int', base)
    return None


def parse_ctype(t):
    """Parse one slot C type: exactly one of an integer type from C_INT_RANGE, a pointer type
    `[const] T *...` (T a struct tag or a type name, or an integer or void base), or a function-pointer
    type `R (*)(P1, P2, ...)` with plain parameter types or `(void)`. Returns ('int', name, bits, signed)
    or ('ptr',). Raises ToolError with the reason otherwise: a comma outside a parameter list, a name
    after the type, unbalanced parentheses, or two types all fail to parse to the end."""
    toks = _c_tokens(t)
    if toks is None:
        raise ToolError("%r has characters that are not part of a C type" % t)
    if not toks:
        raise ToolError("the C type is empty")
    plain = _c_plain(toks, 0)
    if plain is not None and plain[2] == len(toks):
        base, stars, _ = plain
        vt = _c_value_type(base, stars)
        if vt is None:
            raise ToolError("%r is not a pointer or an integer type (a struct by value is refused)" % t)
        if vt[0] == 'ptr':
            return ('ptr',)
        bits, signed = C_INT_RANGE[base]
        return ('int', base, bits, signed)
    # function pointer: return type, then "(*)", then the parameter list, and nothing after it
    ret = plain
    if ret is None:
        raise ToolError("%r is not one C type" % t)
    rbase, rstars, j = ret
    if not (rbase == 'void' and rstars == 0) and _c_value_type(rbase, rstars) is None:
        raise ToolError("%r has a return type that is not void, an integer or a pointer" % t)
    if toks[j:j + 4] != ['(', '*', ')', '(']:
        if j < len(toks) and toks[j] == ',':
            raise ToolError("%r has a comma outside a function parameter list: one C type only" % t)
        if j < len(toks) and toks[j] in C_KEYWORDS:
            raise ToolError("%r has two C types: one C type only" % t)
        if j < len(toks) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', toks[j]):
            raise ToolError("%r has a name after the type: a declarator is not allowed" % t)
        raise ToolError("%r is not one C type: a pointer, an integer, or R (*)(P...)" % t)
    k = j + 4
    if toks[k:k + 2] == ['void', ')']:
        k += 2
    else:
        while True:
            p = _c_plain(toks, k)
            if p is None:
                raise ToolError("%r has a parameter that is not a plain C type" % t)
            pbase, pstars, k = p
            if _c_value_type(pbase, pstars) is None:
                raise ToolError("%r has a parameter that is not a pointer or an integer type" % t)
            if k < len(toks) and toks[k] == ',':
                k += 1
                continue
            if k < len(toks) and toks[k] == ')':
                k += 1
                break
            raise ToolError("%r has unbalanced parentheses or a name after a parameter" % t)
    if k != len(toks):
        raise ToolError("%r has text after the function pointer type" % t)
    return ('ptr',)


def check_ctypes(fname, args, ct):
    """The ctypes list: one entry per argument, a C type for each null and const, and nothing else.
    A const value must fit an integer type by its width and sign."""
    kinds = [parse_kind(a) for a in args]
    for i, k in enumerate(kinds):
        if k['name'] in ('null', 'const'):
            if not ct[i]:
                raise ToolError("%s: %s needs its C parameter type: add a \"ctypes\" entry, e.g. "
                                "\"const char *\"" % (fname, args[i]))
            try:
                info = parse_ctype(ct[i])
            except ToolError as e:
                raise ToolError("%s: ctypes entry: %s" % (fname, e))
            if k['name'] == 'const' and info[0] == 'int':
                _, name, bits, signed = info
                lo = -(1 << (bits - 1)) if signed else 0
                hi = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
                if not lo <= k['val'] <= hi:
                    raise ToolError("%s: %s does not fit %s (%d..%d)"
                                    % (fname, args[i], name, lo, hi))
        elif ct[i] is not None:
            raise ToolError("%s: ctypes may only annotate null and const, not %r" % (fname, args[i]))


def kind_ctypes(k):
    """The C parameters one parsed argument kind expands to."""
    if k['name'] == 'borrow':
        return [k['flag'] + ' *']
    return KIND_CTYPES[kind_key(k)]


def sig_params(f):
    """The sigcheck parameter types: the spec's annotation for a null or const slot, the fixed type
    for every other kind. The C compiler then checks each against the header exactly."""
    if all(a == 'i64' for a in f['args']):
        return [c_arg(a) for a in f['args']]
    out = []
    for i, a in enumerate(f['args']):
        k = parse_kind(a)
        if k['name'] in ('null', 'const'):
            out.append(' '.join(f['ctypes'][i].split()))
        else:
            out += kind_ctypes(k)
    return out


def c_params(f):
    """The C parameter types of one function, in order."""
    out = []
    if all(a == 'i64' for a in f['args']):
        return [c_arg(a) for a in f['args']]
    for a in f['args']:
        out += kind_ctypes(parse_kind(a))
    return out


def parse_ret(tok):
    """Split a return kind into (base, foreign C name). own/own_opt carry a type name."""
    m = re.fullmatch(r'(own|own_opt)<([A-Za-z_][A-Za-z0-9_]*)>', tok)
    if m:
        return m.group(1), m.group(2)
    return tok, None


def spec_types(spec):
    """Validate `types` and index it by C name and by obo name."""
    types = spec.get('types', [])
    if not isinstance(types, list):
        raise ToolError("types must be a list")
    if len(types) > FOREIGN_MAX:
        raise ToolError("at most %d foreign types are supported" % FOREIGN_MAX)
    by_name = {}
    by_obo = {}
    for e in types:
        if not isinstance(e, dict):
            raise ToolError("a types entry must be an object")
        for k in ('name', 'obo', 'release', 'release_result'):
            if k not in e:
                raise ToolError("a types entry needs %r" % k)
        for k in ('name', 'obo', 'release'):
            if not isinstance(e[k], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', e[k]):
                raise ToolError("types entry %r is not a C/obo identifier" % e[k])
        if e['release_result'] not in ('void', 'int'):
            raise ToolError("%s: release_result must be 'void' or 'int'" % e['obo'])
        if e['name'] in by_name:
            raise ToolError("duplicate C type %r in types" % e['name'])
        if e['obo'] in by_obo:
            raise ToolError("duplicate obo type %r in types" % e['obo'])
        by_name[e['name']] = e
        by_obo[e['obo']] = e
    return by_name, by_obo


def c_arg(typ):
    return 'long' if typ == 'i64' else 'int'


def obo_args(n):
    return ', '.join('a%d: int' % i for i in range(n))


def cinterop_args(n):
    return ''.join(', a%d' % i for i in range(n))


def generate_wrapper(spec):
    out = ['module ' + spec['module'], '']
    for f in spec['functions']:
        out.append('pub fn %s(%s): int {' % (f['name'], obo_args(len(f['args']))))
        out.append('    return __cinterop("%s"%s)' % (f['symbol'], cinterop_args(len(f['args']))))
        out.append('}')
        out.append('')
    return '\n'.join(out)


def kinds_thunk(f):
    """Thunk body for a binding with argument kinds (stage (a), design 3.2) and the typed return
    kinds of stage (b).

    The call site pushed one word per data kind word, in argument order, so the
    first word is at the highest address. The thunk reads those words only now,
    after every argument expression ran and right before the C call. Pointers and
    lengths are made by the obo_ci_* helpers (host.c) into rbp-relative slots;
    the scratch among them is freed after the call on the normal return path.
    A contract violation exits 11 inside a helper, before the call.

    cbytes calls the value function and then the length function with the same expanded argument
    words, with no obo code in between, and copies through obo_ci_copy_out. cstr_out copies up to
    the first NUL through obo_ci_cstr_out. Every call resets rsp to the fixed frame, so the second
    cbytes call gets the same 16-byte alignment and stack arguments as the first.

    The int form (every C parameter a bare i64) is also valid here when its return is typed: the
    words are the same as the int-form thunk's, so synthesize the i64@k kinds rather than rejecting
    a bare i64 as a descriptor.
    """
    if all(a == 'i64' for a in f['args']):
        kinds = [{'name': 'i64', 'arg': i, 'flag': None, 'val': 0}
                 for i in range(len(f['args']))]
    else:
        kinds = check_kinds(f['name'], f['args'])
    data = {}
    for k in kinds:
        if k['arg'] is None or (k['name'] == 'ints' and k['flag'] == 'len'):
            continue
        data[k['arg']] = k
    m = max(data) + 1 if data else 0
    base = {}
    W = 0
    for a in range(m):
        base[a] = W
        W += 2 if data[a]['name'] in BYTES_KINDS else 1

    def word(w):
        return '%d(%%rbp)' % (16 + 8 * (W - 1 - w))

    slots = []
    body = []
    scratch = []

    def slot():
        slots.append(None)
        return '%d(%%rbp)' % (-8 * len(slots))

    def call(name, args):
        for reg, a in zip(('%rdi', '%rsi'), args):
            body.append('    %s %s, %s' % ('movabs' if a.startswith('$') else 'movq', a, reg))
        body.append('    call %s@PLT' % name)
        s_ = slot()
        body.append('    movq %%rax, %s' % s_)
        return s_

    def keep(s_):
        scratch.append(s_)
        return s_

    srcs = []
    for k in kinds:
        nm = k['name']
        if nm == 'null':
            srcs.append('$0')
        elif nm == 'const':
            srcs.append('$%d' % k['val'])
        elif nm == 'i64':
            srcs.append(word(base[k['arg']]))
        elif nm == 'i32':
            srcs.append(call('obo_ci_i32', [word(base[k['arg']])]))
        elif nm == 'u32':
            srcs.append(call('obo_ci_u32', [word(base[k['arg']])]))
        elif nm == 'borrow_bytes':
            v, fl = word(base[k['arg']]), word(base[k['arg']] + 1)
            srcs.append(call('obo_ci_bytes_ptr', [v, fl]))
            srcs.append(call('obo_ci_bytes_len', [v, fl]))
        elif nm == 'borrow_cstr':
            v, fl = word(base[k['arg']]), word(base[k['arg']] + 1)
            p = call('obo_ci_bytes_ptr', [v, fl])
            n = call('obo_ci_bytes_len', [v, fl])
            srcs.append(keep(call('obo_ci_cstr', [p, n])))
        elif nm == 'ints' and k['flag'] == 'len':
            srcs.append(call('obo_ci_count', [word(base[k['arg']])]))
        elif nm == 'borrow':
            # The call site pushed the record pointer; the helper checks it is live and yields the
            # C word (exit 7 for a nil record or a zero word).
            srcs.append(call('obo_ci_foreign_word', [word(base[k['arg']])]))
        elif nm == 'ints':
            srcs.append(keep(call('obo_ci_ints', [word(base[k['arg']]),
                                                  '$%d' % (1 if k['flag'] == 'u32' else 0)])))
        elif nm == 'nil_bytes_list':
            srcs.append(keep(call('obo_ci_list_ptrs', [word(base[k['arg']])])))
            srcs.append(keep(call('obo_ci_list_lens', [word(base[k['arg']])])))
        else:
            raise ToolError("%s: no thunk for kind %r" % (f['name'], nm))
    if len(srcs) != len(c_params(f)):
        raise ToolError("%s: kinds expand to %d C parameters, not %d"
                        % (f['name'], len(srcs), len(c_params(f))))

    def load(src, reg):
        if src.startswith('$'):
            return '    movabs %s, %s' % (src, reg)
        return '    movq %s, %s' % (src, reg)

    tail = []
    first_call = [True]

    def native_call(target):
        # Reset rsp to the fixed frame before every call but the first: stack arguments and any
        # alignment pad from an earlier call (cbytes calls twice) must not accumulate.
        if not first_call[0]:
            tail.append('    lea -$FRAME(%rbp), %rsp')
        first_call[0] = False
        stack = srcs[6:]
        if stack and len(stack) % 2 == 1:
            tail.append('    sub $8, %rsp')
        for src in reversed(stack):
            if src.startswith('$'):
                tail.append('    movabs %s, %%rax' % src)
                tail.append('    pushq %rax')
            else:
                tail.append('    pushq %s' % src)
        for i, src in enumerate(srcs[:6]):
            tail.append(load(src, ARG_REGS[i]))
        tail.append('    call %s@PLT' % target)

    ret_kind = f['ret']
    if ret_kind == 'cbytes':
        if 'length' not in f:
            raise ToolError("%s: cbytes needs a length function" % f['name'])
        native_call(f['symbol'])
        ptr = slot()
        tail.append('    movq %%rax, %s' % ptr)
        native_call(f['length'])
        tail.append('    movslq %eax, %rsi')
        tail.append('    movq %s, %%rdi' % ptr)
        tail.append('    movabs $%d, %%rdx' % f['cap'])
        tail.append('    call obo_ci_copy_out@PLT')
    elif ret_kind == 'cstr_out':
        native_call(f['symbol'])
        tail.append('    movq %rax, %rdi')
        tail.append('    call obo_ci_cstr_out@PLT')
    else:
        native_call(f['symbol'])
        if ret_kind == 'i32':
            tail.append('    movslq %eax, %rax')
        elif ret_kind == 'u32':
            tail.append('    movl %eax, %eax')
        elif ret_kind == 'nonzero':
            tail.append('    testq %rax, %rax')
            tail.append('    setne %al')
            tail.append('    movzbl %al, %eax')
        elif ret_kind == 'void':
            tail.append('    xor %eax, %eax')
        elif ret_kind.startswith('own<') or ret_kind.startswith('own_opt<'):
            pass                      # the raw C word; the compiler wraps it in a record
        elif ret_kind != 'i64':
            raise ToolError("%s: no thunk for return kind %r" % (f['name'], ret_kind))
    ret = slot()
    tail.append('    movq %%rax, %s' % ret)
    frame = (8 * len(slots) + 15) // 16 * 16
    tail = [line.replace('$FRAME', str(frame)) for line in tail]
    tail.append('    lea -%d(%%rbp), %%rsp' % frame)
    for s_ in scratch:
        tail.append('    movq %s, %%rdi' % s_)
        tail.append('    call obo_ci_free@PLT')
    tail.append('    movq %s, %%rax' % ret)
    lines = ['    push %rbp', '    mov %rsp, %rbp']
    if frame:
        lines.append('    sub $%d, %%rsp' % frame)
    return lines + body + tail + ['    leave', '    ret']


def int_thunk(symbol, n, ret):
    """The original int-form thunk: n words on obo's stack, passed in SysV registers and on the
    stack. It also serves host helpers, which all take i64-sized arguments."""
    out = ['    push %rbp', '    mov %rsp, %rbp']
    stack = list(range(6, n))
    if stack and len(stack) % 2 == 1:
        out.append('    sub $8, %rsp')
    for i in reversed(stack):
        off = 16 + 8 * (n - 1 - i)
        out.append('    pushq %d(%%rbp)' % off)
    for i in range(min(n, 6)):
        off = 16 + 8 * (n - 1 - i)
        out.append('    mov %d(%%rbp), %s' % (off, ARG_REGS[i]))
    out.append('    call %s@PLT' % symbol)
    if ret == 'i32':
        out.append('    movslq %eax, %rax')
    elif ret == 'u32':
        out.append('    movl %eax, %eax')
    out += ['    leave', '    ret']
    return out


def generate_shim(spec):
    out = ['    .text']
    for f in spec['functions']:
        n = len(f['args'])
        sym = 'obo_native_' + f['symbol']
        out += ['', '    .globl %s' % sym, '    .type %s,@function' % sym, '%s:' % sym]
        # The int-form thunk keeps its original shape when the arguments are all i64 and the result
        # is a scalar it handles. Any typed return (stage (b)/(d)) uses the kinds thunk, whose word
        # layout for i64@k is the same as the int form's.
        scalar = all(a == 'i64' for a in f['args']) and f['ret'] in ('i32', 'i64', 'u32')
        if not scalar:
            out += kinds_thunk(f)
            continue
        out += int_thunk(f['symbol'], n, f['ret'])
    out += ['', '    .section .note.GNU-stack,"",@progbits', '']
    return '\n'.join(out)


def generate_host_shim(host_names):
    """Thunks for the internal obo_ci_* host helpers used by the compiled image. They go into a
    separate object so the spec shim (and the freestanding ABI probe) stays free of host symbols."""
    out = ['    .text']
    for h in sorted(host_names):
        sym = 'obo_native_' + h
        out += ['', '    .globl %s' % sym, '    .type %s,@function' % sym, '%s:' % sym]
        out += int_thunk(h, HOST_HELPERS[h], 'i64')
    out += ['', '    .section .note.GNU-stack,"",@progbits', '']
    return '\n'.join(out)


def ret_ctype(f):
    """The exact C return type of one spec function: the fixed type for the scalar kinds, the
    author's `ret_ctype` for a pointer-returning kind, and `<C> *` for own/own_opt."""
    r = f['ret']
    base, tn = parse_ret(r)
    if base in ('own', 'own_opt'):
        return tn + ' *'
    if r == 'i64':
        return 'long'
    if r == 'i32':
        return 'int'
    if r == 'u32':
        return 'unsigned int'
    if r == 'void':
        return 'void'
    return ' '.join(f['ret_ctype'].split())


def generate_sigcheck(spec):
    """Compile-time C type assertion. A header check is stronger than a
    declarations-only check: the latter checks the spec against the author's
    explicit declaration, not against the installed library header."""
    headers = spec.get('headers', [])
    decls = spec.get('declarations', [])
    if spec.get('format') == FORMAT_V2:
        mode = spec['signature_check']
    else:
        mode = 'header' if headers else 'declarations-only'
    out = ['/* generated signature check; not linked */']
    # declarations-only makes no header claim: only explicit declarations are compiled.
    if mode == 'header':
        for h in headers:
            out.append('#include "%s"' % h)
    for d in decls:
        out.append(d)
    for i, f in enumerate(spec['functions']):
        ret = ret_ctype(f)
        args = ', '.join(sig_params(f)) or 'void'
        out.append('static %s (*obo_sig_%d)(%s) __attribute__((unused)) = %s;'
                   % (ret, i, args, f['symbol']))
        if f['ret'] == 'cbytes':
            # The length function takes the same C parameters and returns int (design 3.1, 3.3).
            out.append('static int (*obo_sig_%d_len)(%s) __attribute__((unused)) = %s;'
                       % (i, args, f['length']))
    for i, t in enumerate(spec.get('types', [])):
        # The release is called from the generated table wrapper with the C type pointer; check its
        # own prototype (the table wrapper itself would fail on an incompatible one too).
        ret = 'int' if t['release_result'] == 'int' else 'void'
        out.append('static %s (*obo_sig_type_%d)(%s *) __attribute__((unused)) = %s;'
                   % (ret, i, t['name'], t['release']))
    out.append('')
    return '\n'.join(out), mode


def generate_foreign_table(spec, entries, mode):
    """The per-binding foreign table, in sidecar order. Each release gets a typed wrapper so host.c
    can call it through one ABI without casting a function pointer."""
    headers = spec.get('headers', [])
    decls = spec.get('declarations', [])
    out = ['/* generated foreign type table; do not edit */', '#include "foreign_abi.h"']
    if mode == 'header':
        for h in headers:
            out.append('#include "%s"' % h)
    for d in decls:
        out.append(d)
    for i, e in enumerate(entries):
        out.append('static long obo_ci_rel_%d(void *p) {' % i)
        if e['release_result'] == 'int':
            out.append('    return (long)%s((%s *)p);' % (e['release'], e['name']))
        else:
            out.append('    %s((%s *)p);' % (e['release'], e['name']))
            out.append('    return 0;')
        out.append('}')
    n = max(1, len(entries))
    out.append('const obo_ci_foreign_type obo_ci_foreign_types[%d] = {' % n)
    for i, e in enumerate(entries):
        out.append('    {"%s", obo_ci_rel_%d, %d},' % (e['obo'], i, 1 if e['release_result'] == 'int' else 0))
    if not entries:
        out.append('    {0, 0, 0},')
    out.append('};')
    out.append('const long obo_ci_foreign_type_count = %d;' % len(entries))
    out.append('')
    return '\n'.join(out)


def manifest_doc(spec, paths, compiler_version, toolchain, signature_check, kind,
                 include_dirs=None, include_paths=None, cflags=None,
                 header_deps=None, link_flags=None):
    files = {}
    for key, path in paths.items():
        files[key] = sha256(require_file(path, key))
    doc = {
        'format': spec.get('format', FORMAT_V1),
        'kind': kind,
        'module': spec['module'],
        'platform': spec['platform'],
        'abi': spec['abi'],
        'pointer_width': 64,
        'compiler': compiler_version,
        'toolchain': toolchain,
        'signature_check': signature_check,
        'effects': list(EFFECTS),
        'types': list(spec.get('types', [])),
        'symbols': [{'name': f['name'], 'symbol': f['symbol'],
                     'args': f['args'], 'ret': f['ret']} for f in spec['functions']],
        'expected_thunks': ['obo_native_' + f['symbol'] for f in spec['functions']],
        'files': files,
        'paths': {},
        'include_dirs': list(include_dirs or []),
        'include_paths': dict(include_paths or {}),
        'cflags': list(cflags or []),
    }
    if header_deps is not None:
        doc['header_deps'] = header_deps
    if link_flags is not None:
        doc['link_flags'] = list(link_flags)
        doc['hardening'] = {'relro': True, 'now': True, 'noexecstack': True}
    return doc


def parse_dependency_makefile(text):
    """Parse the dependency list from `cc -M` output.

    Paths are whitespace-separated with backslash escapes (notably escaped
    spaces), and line continuations are common. The target before the first
    colon is ignored; the dependency headers are returned as written.
    """
    text = text.replace('\\\n', ' ')
    if ':' not in text:
        raise ToolError("C dependency output has no target separator")
    rhs = text.split(':', 1)[1]
    tokens = []
    cur = []
    escaped = False
    for ch in rhs:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch.isspace():
            if cur:
                tokens.append(''.join(cur))
                cur = []
        else:
            cur.append(ch)
    if escaped:
        cur.append('\\')
    if cur:
        tokens.append(''.join(cur))
    return tokens


def collect_header_deps(cc, compiles, out):
    """Hash every header the compiler actually reads for the binding.

    `compiles` is (source, flags) for each real compile. The dependency scan
    must use exactly that compile's macros/optimization/include flags: a header
    selected by `__OPTIMIZE__` or a `-D` flag is part of the input only when the
    scan sees the same preprocessor state. Results from all compiles are
    unioned.
    """
    seen = {}
    for source, flags in compiles:
        p = run([cc, '-M', *flags, source])
        for dep in parse_dependency_makefile(p.stdout):
            real = os.path.realpath(dep)
            if not os.path.isfile(real):
                raise ToolError("preprocessor dependency is missing: %s" % dep)
            seen[real] = sha256(real)
    return [{'path': os.path.relpath(path, out), 'sha256': digest}
            for path, digest in sorted(seen.items())]


def write_text(path, text):
    with open(path, 'w', newline='\n') as f:
        f.write(text)


def write_manifest(path, doc):
    write_text(path, json.dumps(doc, indent=2, sort_keys=True) + '\n')


def align(n, a):
    return (n + a - 1) // a * a


def read_phdrs(data):
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ToolError("not a 64-bit little-endian ELF image")
    e_phoff = struct.unpack_from('<Q', data, 0x20)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
    e_phnum = struct.unpack_from('<H', data, 0x38)[0]
    if e_phentsize != 56:
        raise ToolError("unexpected program header size %d" % e_phentsize)
    if e_phoff + e_phnum * e_phentsize > len(data):
        raise ToolError("program header table is outside the file")
    out = []
    for i in range(e_phnum):
        at = e_phoff + i * e_phentsize
        typ, flags = struct.unpack_from('<II', data, at)
        off, vaddr, paddr, filesz, memsz, al = struct.unpack_from('<QQQQQQ', data, at + 8)
        if filesz > memsz:
            raise ToolError("segment %#x has filesz %d > memsz %d" % (vaddr, filesz, memsz))
        if off + filesz > len(data):
            raise ToolError("segment %#x extends past the file" % vaddr)
        if al == 0 or (al & (al - 1)) != 0:
            raise ToolError("segment %#x has invalid alignment %d" % (vaddr, al))
        out.append({'type': typ, 'flags': flags, 'off': off, 'vaddr': vaddr,
                    'paddr': paddr, 'filesz': filesz, 'memsz': memsz, 'align': al})
    loads = [p for p in out if p['type'] == 1]
    for i in range(len(loads)):
        for j in range(i + 1, len(loads)):
            a, b = loads[i], loads[j]
            if a['vaddr'] < b['vaddr']:
                lo, hi = a, b
            else:
                lo, hi = b, a
            if lo['vaddr'] + lo['memsz'] > hi['vaddr']:
                raise ToolError("overlapping load segments at %#x and %#x" % (lo['vaddr'], hi['vaddr']))
    return out


def read_sections(data):
    """Read section names/addresses/sizes from an ELF64 image (no stripping)."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ToolError("not a 64-bit little-endian ELF image")
    e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
    e_shentsize = struct.unpack_from('<H', data, 0x3a)[0]
    e_shnum = struct.unpack_from('<H', data, 0x3c)[0]
    e_shstrndx = struct.unpack_from('<H', data, 0x3e)[0]
    if e_shoff == 0 or e_shnum == 0:
        raise ToolError("final artifact has no section table")
    if e_shentsize != 64 or e_shstrndx >= e_shnum:
        raise ToolError("final artifact has a malformed section table")
    if e_shoff + e_shnum * e_shentsize > len(data):
        raise ToolError("section table is outside the file")
    headers = []
    for i in range(e_shnum):
        at = e_shoff + i * e_shentsize
        name, typ, flags, addr, off, size, link, info, alignv, entsize = \
            struct.unpack_from('<IIQQQQIIQQ', data, at)
        if off + size > len(data) and typ != 8:       # SHT_NOBITS has no file bytes
            raise ToolError("section %d extends past the file" % i)
        headers.append({'nameoff': name, 'type': typ, 'flags': flags,
                        'addr': addr, 'off': off, 'size': size})
    strtab = headers[e_shstrndx]
    if strtab['off'] + strtab['size'] > len(data):
        raise ToolError("section name table is outside the file")
    names = data[strtab['off']:strtab['off'] + strtab['size']]
    out = []
    for s in headers:
        start = s['nameoff']
        if start >= len(names):
            raise ToolError("section name offset is outside the name table")
        end = names.find(b'\0', start)
        if end < 0:
            raise ToolError("unterminated section name")
        s['name'] = names[start:end].decode('latin-1')
        out.append(s)
    return out


def image_segments(data):
    ph = read_phdrs(data)
    seg = {p['vaddr']: p for p in ph if p['type'] == 1}
    for v in (IMAGE_BASE, RODATA_BASE, GLOBAL_BASE, RT_TEXT, RT_DATA):
        if v not in seg:
            raise ToolError("compiler image has no PT_LOAD at %#x" % v)
    return seg


def _read_relocs_v1(lines):
    """cinterop-relocs 1: off sym nargs types ret effects. Kept for sidecars written by an older
    compiler."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 6:
            raise ToolError("bad native relocation line %r" % line)
        try:
            off = int(parts[0])
            nargs = int(parts[2])
        except ValueError:
            raise ToolError("bad native relocation line %r" % line)
        sym, types, ret, effects = parts[1], parts[3], parts[4], parts[5]
        if off < 1 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', sym):
            raise ToolError("bad native relocation %r" % line)
        if nargs < 0 or nargs > MAX_KIND_ARGS:
            raise ToolError("native relocation arity %d out of range" % nargs)
        argtypes = [] if types == '-' else types.split(',')
        if len(argtypes) != nargs:
            raise ToolError("native relocation argument types do not match arity: %r" % line)
        for t in argtypes:
            if t != 'i64':
                parse_kind(t)
        if ret != 'i64' or effects != 'freesall,writeshandles':
            raise ToolError("native relocation has an unexpected result/effect contract: %r" % line)
        out.append({'off': off, 'sym': sym, 'nargs': nargs, 'argtypes': argtypes,
                    'shapes': argtypes, 'mode': 'call', 'ret': ret, 'effects': effects,
                    'version': 1})
    return out


def _read_foreign_v2(lines):
    """foreign <id> <obo-name> <c-name> lines, in first-declaration order."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or parts[0] != 'foreign':
            raise ToolError("bad foreign type line %r" % line)
        try:
            fid = int(parts[1])
        except ValueError:
            raise ToolError("bad foreign type id %r" % line)
        obo, cname = parts[2], parts[3]
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', obo) or \
                not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', cname):
            raise ToolError("bad foreign type line %r" % line)
        if fid != len(out):
            raise ToolError("foreign type ids must run from 0 in sidecar order: %r" % line)
        out.append({'id': fid, 'obo': obo, 'cname': cname})
    if len(out) > FOREIGN_MAX:
        raise ToolError("at most %d foreign types are supported" % FOREIGN_MAX)
    return out


def _read_relocs_v2(lines):
    """cinterop-relocs 2: foreign lines, then off name nargs mode shapes ret-shape effects.

    mode is 'call' (an int-form call with shapes '-', or an explicit kinds descriptor), 'fn' (a
    typed native fn call with one shape per obo argument) or 'host' (an internal obo_ci_* helper
    with a fixed arity). Every token is checked here, so a tampered or unknown shape or mode is
    refused before it can reach the linker."""
    reloc_lines = [ln for ln in lines if not ln.startswith('foreign ')]
    foreign = _read_foreign_v2([ln for ln in lines if ln.startswith('foreign ')])
    out = []
    for line in reloc_lines:
        parts = line.split()
        if len(parts) != 7:
            raise ToolError("bad cinterop-relocs 2 line %r" % line)
        name, mode, shape_text, ret, effects = parts[1], parts[3], parts[4], parts[5], parts[6]
        try:
            off = int(parts[0])
            nargs = int(parts[2])
        except ValueError:
            raise ToolError("bad native relocation line %r" % line)
        if off < 1 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ToolError("bad native relocation %r" % line)
        if nargs < 0 or nargs > MAX_KIND_ARGS:
            raise ToolError("native relocation arity %d out of range" % nargs)
        if effects != 'freesall,writeshandles':
            raise ToolError("native relocation has an unexpected effect contract: %r" % line)
        if mode not in ('call', 'fn', 'host'):
            raise ToolError("native relocation has an unknown mode %r: %r" % (mode, line))
        if mode == 'call':
            if ret != 'i64':
                raise ToolError("a call-form native relocation must have result i64: %r" % line)
            if shape_text == '-':
                shapes = []
            else:
                shapes = shape_text.split(',')
                for t in shapes:
                    parse_kind(t)
            if shape_text != '-' and len(shapes) != nargs:
                raise ToolError("native relocation argument kinds do not match arity: %r" % line)
            if shape_text == '-' and nargs > 0:
                # the int form: all i64, exactly nargs words
                shapes = ['i64'] * nargs
            out.append({'off': off, 'sym': name, 'nargs': nargs, 'argtypes': shapes,
                        'shapes': shapes, 'mode': mode, 'ret': ret, 'effects': effects,
                        'version': 2})
        elif mode == 'fn':
            if ret not in FN_RET_SHAPES and not re.fullmatch(r'(own|own_opt):[A-Za-z_][A-Za-z0-9_]*', ret):
                raise ToolError("native fn relocation has an unknown result shape %r: %r" % (ret, line))
            shapes = [] if shape_text == '-' else shape_text.split(',')
            if len(shapes) != nargs:
                raise ToolError("native fn relocation shapes do not match arity: %r" % line)
            for s in shapes:
                if s not in FN_SHAPES and not re.fullmatch(r'foreign:[A-Za-z_][A-Za-z0-9_]*', s):
                    raise ToolError("native fn relocation has an unknown shape %r: %r" % (s, line))
            out.append({'off': off, 'sym': name, 'nargs': nargs, 'argtypes': shapes,
                        'shapes': shapes, 'mode': mode, 'ret': ret, 'effects': effects,
                        'version': 2})
        else:
            if name not in HOST_HELPERS:
                raise ToolError("native relocation names an unknown host helper %r: %r" % (name, line))
            if nargs != HOST_HELPERS[name]:
                raise ToolError("host helper %r needs %d argument(s), not %d"
                                % (name, HOST_HELPERS[name], nargs))
            want_shape = ','.join(['i64'] * nargs) if nargs else '-'
            if ret != 'i64' or shape_text != want_shape:
                raise ToolError("host helper relocation has an unexpected shape/result: %r" % line)
            out.append({'off': off, 'sym': name, 'nargs': nargs, 'argtypes': [],
                        'shapes': [], 'mode': mode, 'ret': ret, 'effects': effects,
                        'version': 2})
    side = Sidecar(out)
    side.foreign = foreign
    return side


class Sidecar(list):
    """A relocation list that also carries the sidecar's foreign type table."""
    foreign = []


def read_relocs(path):
    if not os.path.isfile(path):
        raise ToolError("native relocation sidecar is missing: %s" % path)
    lines = [ln.strip() for ln in open(path) if ln.strip()]
    if not lines:
        raise ToolError("native relocation sidecar is empty")
    if lines[0] == RELOC_HEADER_V1:
        side = Sidecar(_read_relocs_v1(lines[1:]))
        side.foreign = []
        return side
    if lines[0] == RELOC_HEADER_V2:
        return _read_relocs_v2(lines[1:])
    raise ToolError("native relocation sidecar has no %r or %r header"
                    % (RELOC_HEADER_V1, RELOC_HEADER_V2))


def _fn_expected_shapes(f, spec):
    """The shape each spec function expects from a typed native fn call, one per obo argument."""
    if all(a == 'i64' for a in f['args']):
        return ['int'] * len(f['args'])
    type_by_name, _ = spec_types(spec)
    data = {}
    for a in f['args']:
        k = parse_kind(a)
        if k['arg'] is None or (k['name'] == 'ints' and k['flag'] == 'len'):
            continue
        data[k['arg']] = k
    shapes = []
    for i in range(len(data)):
        k = data[i]
        if k['name'] in ('i64', 'i32', 'u32'):
            shapes.append('int')
        elif k['name'] in BYTES_KINDS:
            shapes.append('bytes')
        elif k['name'] == 'ints':
            shapes.append('ints')
        elif k['name'] == 'nil_bytes_list':
            shapes.append('byteslist')
        elif k['name'] == 'borrow':
            t = type_by_name.get(k['flag'])
            if t is None:
                raise ToolError("%s: borrow<%s> is not in types" % (f['name'], k['flag']))
            shapes.append('foreign:' + t['obo'])
        else:
            raise ToolError("%s: %s is not supported by a typed native fn call" % (f['name'], a))
    return shapes


def _fn_expected_ret(f, spec):
    """The whole-result shape of a typed native fn call for one spec function."""
    if f['ret'] in RET_SHAPE_OF:
        return RET_SHAPE_OF[f['ret']]
    base, tn = parse_ret(f['ret'])
    if base in ('own', 'own_opt'):
        by_name, _ = spec_types(spec)
        t = by_name.get(tn)
        if t is None:
            raise ToolError("%s: %s is not in types" % (f['name'], f['ret']))
        return ('own_opt:' if base == 'own_opt' else 'own:') + t['obo']
    raise ToolError("%s: ret %r is not supported by a typed native fn call" % (f['name'], f['ret']))


def validate_foreign(foreign, spec):
    """Check the sidecar foreign table against the spec `types`, in sidecar order."""
    _, by_obo = spec_types(spec)
    out = []
    for ft in foreign:
        e = by_obo.get(ft['obo'])
        if e is None:
            raise ToolError("foreign type %r is not in the spec types" % ft['obo'])
        if e['name'] != ft['cname']:
            raise ToolError("foreign type %r is C %r in the sidecar but %r in types"
                            % (ft['obo'], ft['cname'], e['name']))
        out.append(e)
    return out


def validate_calls(relocs, spec, fmt=None):
    fmt = fmt or spec.get('format')
    validate_foreign(getattr(relocs, 'foreign', []), spec)
    by_sym = {f['symbol']: f for f in spec['functions']}
    by_name = {f['name']: f for f in spec['functions']}
    seen = set()
    offsets = set()
    for r in relocs:
        if r['off'] in offsets:
            raise ToolError("duplicate native relocation offset %d" % r['off'])
        offsets.add(r['off'])
        if r.get('mode') == 'host':
            continue
        if r.get('mode', 'call') == 'fn':
            if fmt != FORMAT_V2:
                raise ToolError("native fn call %r needs a cinterop-proto/2 spec" % r['sym'])
            # The declaration name is the spec `name`; the C symbol is deliberately not accepted here.
            f = by_name.get(r['sym'])
            if f is None:
                raise ToolError("native fn call to %r is not in the binding spec by name" % r['sym'])
            expected = _fn_expected_shapes(f, spec)
            if r['nargs'] != len(expected):
                raise ToolError("native fn call to %r has %d argument(s), spec uses %d"
                                % (r['sym'], r['nargs'], len(expected)))
            if r['shapes'] != expected:
                raise ToolError("native fn call to %r has shapes %s, the spec needs %s"
                                % (r['sym'], ','.join(r['shapes']) or '-',
                                   ','.join(expected) or '-'))
            want_ret = _fn_expected_ret(f, spec)
            if r['ret'] != want_ret:
                raise ToolError("native fn call to %r returns %s, the spec needs %s"
                                % (r['sym'], r['ret'], want_ret))
            # The relocatable object and the thunk are named by the C symbol, not the obo name.
            r['sym'] = f['symbol']
            seen.add(f['symbol'])
            continue
        f = by_sym.get(r['sym'])
        if f is None:
            raise ToolError("native call to %r is not in the binding spec" % r['sym'])
        if fmt == FORMAT_V2 and f['ret'] not in ('i32', 'i64'):
            raise ToolError("native call to %r has spec ret %r; a __cinterop call returns int, so "
                            "use a typed native fn declaration" % (r['sym'], f['ret']))
        int_form = (r.get('shapes') == '-' or
                    (r['argtypes'] and all(t == 'i64' for t in r['argtypes'])))
        if int_form:
            if not all(a == 'i64' for a in f['args']):
                raise ToolError("native call to %r is the int form, but the spec has argument kinds"
                                % r['sym'])
            if r['nargs'] != len(f['args']):
                raise ToolError("native call to %r has %d argument(s), spec says %d"
                                % (r['sym'], r['nargs'], len(f['args'])))
        else:
            if r['nargs'] != len(f['args']):
                raise ToolError("native call to %r has %d argument(s), spec says %d"
                                % (r['sym'], r['nargs'], len(f['args'])))
            if r['argtypes'] != list(f['args']):
                raise ToolError("native call to %r has argument kinds %s, the spec says %s"
                                % (r['sym'], ','.join(r['argtypes']) or '-',
                                   ','.join(f['args']) or '-'))
        seen.add(r['sym'])
    missing = set(by_sym) - seen
    if missing:
        raise ToolError("no native call for spec symbol(s): %s" % ', '.join(sorted(missing)))
    return seen


def validate_thunks(shim_object, spec):
    p = run(['nm', '-g', '--defined-only', shim_object])
    got = set()
    for line in p.stdout.splitlines():
        parts = line.split()
        if parts and parts[-1].startswith('obo_native_'):
            got.add(parts[-1])
    want = {'obo_native_' + f['symbol'] for f in spec['functions']}
    if got != want:
        raise ToolError("shim thunk set mismatch: got %s, expected %s"
                        % (sorted(got), sorted(want)))


def make_object(image, out, relocs):
    """Turn the fixed image's PT_LOADs into a relocatable object at the same VAs."""
    data = open(image, 'rb').read()
    seg = image_segments(data)

    def region(v):
        p = seg[v]
        if p['memsz'] > (256 << 20):
            raise ToolError("segment %#x is implausibly large" % v)
        b = data[p['off']:p['off'] + p['filesz']]
        return b + b'\0' * (p['memsz'] - len(b))

    tp = seg[IMAGE_BASE]
    start = tp['off'] + (CODE_BASE - IMAGE_BASE)
    end = tp['off'] + tp['filesz']
    if start >= end:
        raise ToolError("text segment is too short for CODE_BASE")
    text = data[start:end]
    names = set()
    offsets = set()
    for r in relocs:
        off, sym = r['off'], r['sym']
        if off in offsets:
            raise ToolError("duplicate native relocation offset %d" % off)
        offsets.add(off)
        if off < 1 or off + 3 >= len(text):
            raise ToolError("native relocation offset %d is outside .obo.text" % off)
        if text[off - 1] != 0xE8:
            raise ToolError("native relocation at %d does not follow a call opcode" % off)
        if text[off:off + 4] != b'\0\0\0\0':
            raise ToolError("native relocation at %d does not point at a zero placeholder" % off)
        names.add(sym)
    sections = []

    def add(name, typ, flags, content, size=None, alignv=16, link=0, info=0, entsize=0):
        sections.append({'name': name, 'typ': typ, 'flags': flags, 'content': content,
                         'size': len(content) if size is None else size, 'align': alignv,
                         'link': link, 'info': info, 'entsize': entsize})

    add('.obo.text', 1, 6, text)
    add('.obo.rodata', 1, 2, region(RODATA_BASE))
    add('.obo.bss', 8, 3, b'', size=seg[GLOBAL_BASE]['memsz'])
    add('.obo.rttext', 1, 6, region(RT_TEXT))
    add('.obo.rtdata', 1, 3, region(RT_DATA))
    if relocs:
        add('.rela.obo.text', 4, 0, b'', alignv=8, info=1, entsize=24)
    add('.note.GNU-stack', 1, 0, b'', alignv=1)
    shstr = bytearray(b'\0')
    for s in sections:
        s['nameoff'] = len(shstr)
        shstr += s['name'].encode() + b'\0'
    shstr += b'.shstrtab\0'
    shstr_nameoff = len(shstr) - len(b'.shstrtab\0')
    shstr += b'.symtab\0'
    symtab_nameoff = len(shstr) - len(b'.symtab\0')
    shstr += b'.strtab\0'
    strtab_nameoff = len(shstr) - len(b'.strtab\0')
    strtab = bytearray(b'\0')

    def strname(s):
        off = len(strtab)
        strtab.extend(s.encode() + b'\0')
        return off

    start_nameoff = strname('_obo_start')
    native_names = sorted(names)
    native_nameoff = {s: strname('obo_native_' + s) for s in native_names}
    syms = [b'\0' * 24]
    for idx in range(1, len(sections) + 1):
        syms.append(struct.pack('<IBBHQQ', 0, 3, 0, idx, 0, 0))
    first_global = len(syms)
    syms.append(struct.pack('<IBBHQQ', start_nameoff, 0x12, 0, 1, 0, 0))
    native_index = {}
    for s in native_names:
        native_index[s] = len(syms)
        syms.append(struct.pack('<IBBHQQ', native_nameoff[s], 0x10, 0, 0, 0, 0))
    if relocs:
        rela = b''
        for r in relocs:
            rela += struct.pack('<QQq', r['off'], (native_index[r['sym']] << 32) | 4, -4)
        sections[-2]['content'] = rela
        sections[-2]['size'] = len(rela)
    body = bytearray()
    off = 64

    def place(b, al=16):
        nonlocal off
        off = align(off, al)
        while len(body) < off - 64:
            body.append(0)
        at = off
        body.extend(b)
        off += len(b)
        return at

    for s in sections:
        s['off'] = place(s['content'], s['align'])
    shstr_off = place(bytes(shstr), 1)
    symtab_off = place(b''.join(syms), 8)
    strtab_off = place(bytes(strtab), 1)
    shoff = align(off, 8)
    while len(body) < shoff - 64:
        body.append(0)
    shnum = 1 + len(sections) + 3
    shstrndx = 1 + len(sections)
    symtab_index = shstrndx + 1
    strtab_index = shstrndx + 2
    for s in sections:
        if s['typ'] == 4:
            s['link'] = symtab_index
        if s['typ'] == 2:
            s['link'] = strtab_index
    sh = [b'\0' * 64]
    for s in sections:
        sh.append(struct.pack('<IIQQQQIIQQ', s['nameoff'], s['typ'], s['flags'], 0,
                              s['off'], s['size'], s['link'], s['info'], s['align'], s['entsize']))
    sh.append(struct.pack('<IIQQQQIIQQ', shstr_nameoff, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    sh.append(struct.pack('<IIQQQQIIQQ', symtab_nameoff, 2, 0, 0, symtab_off,
                          len(b''.join(syms)), strtab_index, first_global, 8, 24))
    sh.append(struct.pack('<IIQQQQIIQQ', strtab_nameoff, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1, 0))
    body.extend(b''.join(sh))
    ehdr = bytearray(64)
    ehdr[0:4] = b'\x7fELF'
    ehdr[4] = 2
    ehdr[5] = 1
    ehdr[6] = 1
    struct.pack_into('<H', ehdr, 16, 1)       # ET_REL
    struct.pack_into('<H', ehdr, 18, 0x3e)
    struct.pack_into('<I', ehdr, 20, 1)
    struct.pack_into('<Q', ehdr, 40, shoff)
    struct.pack_into('<H', ehdr, 52, 64)
    struct.pack_into('<H', ehdr, 58, 64)
    struct.pack_into('<H', ehdr, 60, shnum)
    struct.pack_into('<H', ehdr, 62, shstrndx)
    with open(out, 'wb') as f:
        f.write(bytes(ehdr) + bytes(body))


def check_hardening(data):
    """Require full RELRO and immediate binding in a hosted cinterop link."""
    ph = read_phdrs(data)
    relro = [p for p in ph if p['type'] == PT_GNU_RELRO and p['memsz'] > 0]
    if not relro:
        raise ToolError("final artifact has no non-empty GNU_RELRO segment")
    relro_ranges = [(p['vaddr'], p['vaddr'] + p['memsz']) for p in relro]
    for section in read_sections(data):
        if section['name'] in ('.preinit_array', '.init_array', '.fini_array') \
                and section['size']:
            lo = section['addr']
            hi = lo + section['size']
            if not any(a <= lo and hi <= b for a, b in relro_ranges):
                raise ToolError("%s is outside PT_GNU_RELRO" % section['name'])
    dynamic = [p for p in ph if p['type'] == PT_DYNAMIC]
    if len(dynamic) != 1:
        raise ToolError("final artifact has no single PT_DYNAMIC segment")
    at = dynamic[0]['off']
    end = at + dynamic[0]['filesz']
    if end > len(data) or (end - at) % 16:
        raise ToolError("final artifact has a malformed PT_DYNAMIC segment")
    bind_now = False
    while at + 16 <= end:
        tag, value = struct.unpack_from('<QQ', data, at)
        at += 16
        if tag == DT_NULL:
            break
        if tag == DT_BIND_NOW:
            bind_now = True
        elif tag == DT_FLAGS and value & DF_BIND_NOW:
            bind_now = True
        elif tag == DT_FLAGS_1 and value & DF_1_NOW:
            bind_now = True
    if not bind_now:
        raise ToolError("final artifact does not request immediate binding (BIND_NOW/DF_1_NOW)")


def validate_final(exe):
    data = open(exe, 'rb').read()
    if data[:4] != b'\x7fELF' or struct.unpack_from('<H', data, 16)[0] != 2:
        raise ToolError("final artifact is not ET_EXEC")
    ph = read_phdrs(data)
    loads = {p['vaddr']: p for p in ph if p['type'] == 1}
    expected_flags = {
        CODE_BASE: 5, RODATA_BASE: 4, GLOBAL_BASE: 6, RT_TEXT: 5, RT_DATA: 6,
    }
    for v, flags in expected_flags.items():
        if v not in loads:
            raise ToolError("final artifact is missing PT_LOAD at %#x" % v)
        if loads[v]['flags'] != flags:
            raise ToolError("PT_LOAD at %#x has flags %d, expected %d"
                            % (v, loads[v]['flags'], flags))
    for p in loads.values():
        if (p['flags'] & 3) == 3:
            raise ToolError("segment at %#x is both writable and executable" % p['vaddr'])
    entry = struct.unpack_from('<Q', data, 0x18)[0]
    if not any(p['flags'] & 1 and p['vaddr'] <= entry < p['vaddr'] + p['memsz']
               for p in loads.values()):
        raise ToolError("entry point %#x is not in an executable load segment" % entry)
    stacks = [p for p in ph if p['type'] == 0x6474e551]
    if len(stacks) != 1 or (stacks[0]['flags'] & 1):
        raise ToolError("final artifact has no one non-executable GNU_STACK")
    check_hardening(data)
    return data


def resolved_libraries(exe):
    p = run(['ldd', exe])
    out = []
    seen = set()
    for line in p.stdout.splitlines():
        s = line.strip()
        if not s or s.startswith('linux-vdso'):
            continue
        if '=>' in s:
            left, right = s.split('=>', 1)
            name = left.strip()
            path = right.strip().split(' (')[0].strip()
        else:
            name = s.split(' (')[0].strip()
            path = name
        if 'not found' in s:
            raise ToolError("dynamic dependency not found: %s" % s)
        if not path.startswith('/') or not os.path.exists(path):
            continue
        real = os.path.realpath(path)
        key = name or real
        if key in seen:
            continue
        seen.add(key)
        out.append({'name': key, 'path': real, 'sha256': sha256(real)})
    return out


def verify_doc(doc, base, full):
    if not isinstance(doc, dict):
        raise ToolError("manifest is not an object")
    for key in ('format', 'kind', 'module', 'platform', 'abi', 'pointer_width',
                'compiler', 'toolchain', 'signature_check', 'effects', 'symbols',
                'expected_thunks', 'files', 'paths'):
        if key not in doc:
            raise ToolError("manifest is missing %r" % key)
    if doc['format'] not in FORMATS:
        raise ToolError("manifest format is %r, expected %r or %r"
                        % (doc['format'], FORMAT_V1, FORMAT_V2))
    if doc['kind'] not in ('input', 'build') or (full and doc['kind'] != 'build'):
        raise ToolError("manifest kind is not build")
    if doc['platform'] != PLATFORM or doc['abi'] != ABI or doc['pointer_width'] != 64:
        raise ToolError("manifest platform/ABI/pointer width does not match this build")
    if doc['signature_check'] not in SIG_MODES:
        raise ToolError("manifest signature_check is %r, not %r"
                        % (doc['signature_check'], SIG_MODES))
    if list(doc['effects']) != list(EFFECTS):
        raise ToolError("manifest effects are not the conservative contract")
    if not isinstance(doc['module'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', doc['module']):
        raise ToolError("manifest module is malformed")
    if not isinstance(doc['symbols'], list) or not isinstance(doc['expected_thunks'], list):
        raise ToolError("manifest symbols/thunks are malformed")
    for sym in doc['symbols']:
        if not isinstance(sym, dict):
            raise ToolError("manifest symbol entry is not an object")
        for k in ('name', 'symbol', 'args', 'ret'):
            if k not in sym:
                raise ToolError("manifest symbol entry is missing %r" % k)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(sym['name'])):
            raise ToolError("manifest symbol name is malformed")
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(sym['symbol'])):
            raise ToolError("manifest C symbol is malformed")
        if not isinstance(sym['args'], list) or not all(isinstance(a, str) for a in sym['args']):
            raise ToolError("manifest symbol arguments are malformed")
        if any(a != 'i64' for a in sym['args']):
            check_kinds(str(sym['name']), sym['args'])
        allowed_ret = RET_KINDS_V1 if doc['format'] == FORMAT_V1 else RET_KINDS_V2
        ret_base, ret_tn = parse_ret(sym['ret'])
        if ret_base not in allowed_ret and ret_base not in ('own', 'own_opt'):
            raise ToolError("manifest symbol result %r is not valid for %s"
                            % (sym['ret'], doc['format']))
        if ret_base in ('own', 'own_opt'):
            by_name, _ = spec_types(doc)
            if ret_tn not in by_name:
                raise ToolError("manifest symbol result %r names a type not in types" % sym['ret'])
    if sorted(doc['expected_thunks']) != sorted('obo_native_' + s['symbol'] for s in doc['symbols']):
        raise ToolError("manifest thunk list does not match its symbols")
    files = doc['files']
    paths = doc['paths']
    if not isinstance(files, dict) or not isinstance(paths, dict) or set(files) != set(paths):
        raise ToolError("manifest files/paths are missing or inconsistent")
    for key in ('include_dirs', 'cflags'):
        if not isinstance(doc.get(key), list) or any(
                not isinstance(x, str) or not x for x in doc.get(key, [])):
            raise ToolError("manifest %s is not a list of paths/flags" % key)
    include_paths = doc.get('include_paths')
    if not isinstance(include_paths, dict):
        raise ToolError("manifest include_paths is missing or malformed")
    for role, rel in include_paths.items():
        if not isinstance(role, str) or not isinstance(rel, str):
            raise ToolError("manifest include_paths entry is malformed")
        include_dir = os.path.normpath(os.path.join(base, rel)) if not os.path.isabs(rel) else rel
        if not os.path.isdir(include_dir):
            raise ToolError("include directory %s is missing" % include_dir)
    required = REQUIRED_BUILD_KEYS if full else REQUIRED_INPUT_KEYS
    missing = required - set(files)
    if missing:
        raise ToolError("manifest is missing required file role(s): %s" % ', '.join(sorted(missing)))
    # Cross-check the manifest's own symbol metadata against the hashed spec.
    # The spec hash alone would not catch a manifest that rewrites symbols and
    # thunks consistently while pointing at an unchanged spec file.
    spec_path = os.path.normpath(os.path.join(base, paths['spec']))
    if os.path.isfile(spec_path):
        spec = load_spec(spec_path)
        if doc['module'] != spec['module']:
            raise ToolError("manifest module %r does not match spec module %r"
                            % (doc['module'], spec['module']))
        want_syms = [{'name': f['name'], 'symbol': f['symbol'],
                      'args': f['args'], 'ret': f['ret']} for f in spec['functions']]
        got_syms = [{'name': s['name'], 'symbol': s['symbol'],
                     'args': s['args'], 'ret': s['ret']} for s in doc['symbols']]
        if got_syms != want_syms:
            raise ToolError("manifest symbols do not match the spec")
    for key, want in files.items():
        path = os.path.normpath(os.path.join(base, paths[key]))
        if not os.path.isfile(path):
            raise ToolError("%s is missing: %s" % (key, path))
        got = sha256(path)
        if got != want:
            raise ToolError("%s hash %s, manifest says %s" % (key, got, want))
    if full:
        if not isinstance(doc.get('resolved_libraries'), list):
            raise ToolError("build manifest is missing resolved_libraries")
        for lib in doc['resolved_libraries']:
            path = lib.get('path', '')
            if not os.path.isfile(path):
                raise ToolError("resolved library is missing: %s" % path)
            if sha256(path) != lib.get('sha256'):
                raise ToolError("resolved library changed: %s" % path)
        deps = doc.get('header_deps')
        if not isinstance(deps, list) or not deps:
            raise ToolError("build manifest is missing header_deps")
        for dep in deps:
            if not isinstance(dep, dict):
                raise ToolError("header dependency entry is not an object")
            rel = dep.get('path')
            if not isinstance(rel, str) or not rel:
                raise ToolError("header dependency path is malformed")
            path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))
            if not os.path.isfile(path):
                raise ToolError("header dependency is missing: %s" % path)
            if sha256(path) != dep.get('sha256'):
                raise ToolError("header dependency changed: %s" % path)
        link_flags = doc.get('link_flags')
        if not isinstance(link_flags, list) or any(flag not in LINK_HARDENING_FLAGS
                                                   for flag in link_flags):
            raise ToolError("build manifest link_flags are not the reviewed hardening set")
        if any(flag not in link_flags for flag in LINK_HARDENING_FLAGS):
            raise ToolError("build manifest is missing a hardening link flag")
        hardening = doc.get('hardening')
        if not isinstance(hardening, dict) or any(
                hardening.get(k) is not True for k in ('relro', 'now', 'noexecstack')):
            raise ToolError("build manifest hardening record is incomplete")
        exe = os.path.normpath(os.path.join(base, paths['executable']))
        if not os.path.isfile(exe):
            raise ToolError("final executable is missing: %s" % exe)
        validate_final(exe)


def verify_manifest(path):
    try:
        doc = json.load(open(path))
    except (OSError, ValueError) as e:
        raise ToolError("cannot read manifest: %s" % e)
    verify_doc(doc, os.path.dirname(os.path.abspath(path)), True)


HOSTED_RT_CFLAGS = [
    '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-pic',
    '-fno-pie', '-fno-stack-protector', '-fno-asynchronous-unwind-tables',
    '-mcmodel=small', '-fno-tree-loop-distribute-patterns', '-Wall',
]


def build_hosted_runtime(out, cc):
    """Build the separate -DOBO_HOSTED_EXIT runtime blobs used only by cinterop.

    The baseline freestanding runtime is untouched. The slot count is checked
    against host_abi.h so the host's fixed RT_HOST_EXIT_ADDR cannot silently
    point at the wrong table entry.
    """
    rt_src = require_file(os.path.join(OBO_ROOT, 'runtime', 'runtime.c'), 'runtime source')
    entry_src = require_file(os.path.join(OBO_ROOT, 'runtime', 'entry.S'), 'runtime entry source')
    link_script = require_file(os.path.join(OBO_ROOT, 'runtime', 'rt.ld'), 'runtime linker script')
    entry_text = open(entry_src).read()

    def hosted_slots(text):
        """The jump-table entries of the hosted build, in order, with #ifdef OBO_HOSTED_EXIT resolved."""
        lines = []
        active = True
        stack = []
        for raw in text.splitlines():
            s = raw.strip()
            if s.startswith('#ifdef') or s.startswith('#ifndef'):
                stack.append(active)
                cond = s.endswith('OBO_HOSTED_EXIT')
                if s.startswith('#ifndef'):
                    cond = not cond
                active = active and cond
            elif s == '#else':
                parent = stack[-1] if stack else True
                active = parent and not active
            elif s == '#endif':
                active = stack.pop() if stack else True
            elif s == '.text':
                break
            elif active and (s.startswith('slot ') or s.startswith('.quad ')):
                lines.append(s)
        return lines

    slot_lines = hosted_slots(entry_text)
    for slot_name, want in (('slot rt_set_host_exit', RT_HOST_EXIT_SLOT),
                            ('slot rt_set_foreign_census', RT_FOREIGN_CENSUS_SLOT)):
        if slot_name not in slot_lines:
            raise ToolError("hosted runtime entry.S has no %r slot" % slot_name.replace('slot ', ''))
        got = slot_lines.index(slot_name)
        if got != want:
            raise ToolError("hosted runtime slot mismatch: entry.S puts %s at %d, host_abi says %d"
                            % (slot_name.replace('slot ', ''), got, want))
    if '#ifdef OBO_HOSTED_EXIT' not in entry_text:
        raise ToolError("runtime entry.S has no hosted-only branch")
    ld = shutil.which('ld')
    objcopy = shutil.which('objcopy')
    if not ld or not objcopy:
        raise ToolError("hosted runtime needs ld and objcopy on PATH")
    rt_dir = os.path.join(out, 'hosted-runtime')
    os.makedirs(rt_dir, exist_ok=True)
    rt_o = os.path.join(rt_dir, 'runtime.o')
    entry_o = os.path.join(rt_dir, 'entry.o')
    rt_elf = os.path.join(rt_dir, 'runtime.elf')
    rt_text = os.path.join(rt_dir, 'runtime_text.bin')
    rt_data = os.path.join(rt_dir, 'runtime_data.bin')
    run([cc, *HOSTED_RT_CFLAGS, '-DOBO_HOSTED_EXIT', '-c', rt_src, '-o', rt_o])
    run([cc, '-c', '-DOBO_HOSTED_EXIT', entry_src, '-o', entry_o])
    run([ld, '-T', link_script, '-o', rt_elf, entry_o, rt_o])
    run([objcopy, '-O', 'binary', '-j', '.text', rt_elf, rt_text])
    run([objcopy, '-O', 'binary', '-j', '.data', '--set-section-flags',
         '.data=alloc,load,contents', rt_elf, rt_data])
    return {
        'dir': rt_dir,
        'sources': {'runtime_source': rt_src, 'runtime_entry': entry_src,
                    'runtime_link_script': link_script},
        'blobs': {'runtime_text': rt_text, 'runtime_data': rt_data},
    }


def build(args):
    spec_path = os.path.abspath(args.spec)
    app = os.path.abspath(args.app)
    out = os.path.abspath(args.out)
    spec = load_spec(spec_path)
    spec_dir = os.path.dirname(spec_path)
    require_file(app, "application source")
    os.makedirs(out, exist_ok=True)
    symdir = os.path.join(out, 'sym')
    os.makedirs(symdir, exist_ok=True)
    obo = require_file(os.path.abspath(args.obo) if args.obo
                       else os.environ.get('OBO', DEFAULT_OBO), "obo compiler")
    cc = args.cc if os.path.isabs(args.cc) else shutil.which(args.cc)
    if not cc:
        raise ToolError("C compiler not found: %s" % args.cc)
    cc = require_file(cc, "C compiler")
    hosted_rt = build_hosted_runtime(out, cc)

    wrapper = os.path.join(out, spec['module'] + '.obx')
    shim = os.path.join(out, spec['module'] + '_shim.S')
    wrapper_source = None
    if spec.get('wrapper'):
        # A maintained package may supply its ordinary obo wrapper. Copy it into
        # the private build directory so imports are deterministic; the original
        # input remains part of the provenance record.
        wrapper_source = resolve_input(spec_dir, spec['wrapper'], "wrapper source")
        shutil.copyfile(wrapper_source, wrapper)
    else:
        write_text(wrapper, generate_wrapper(spec))
    extra_wrappers = []
    for i, src in enumerate(spec.get('wrapper_sources', [])):
        path = resolve_input(spec_dir, src, "extra wrapper source")
        dest = os.path.join(out, os.path.basename(path))
        shutil.copyfile(path, dest)
        extra_wrappers.append((i, path, dest))
    write_text(shim, generate_shim(spec))
    sigcheck, sig_mode = generate_sigcheck(spec)
    sig_path = os.path.join(out, 'sigcheck.c')
    write_text(sig_path, sigcheck)

    include_dirs = [resolve_include_dir(spec_dir, p)
                    for p in spec.get('include_dirs', [])]
    sources = [resolve_input(spec_dir, p, "source") for p in spec.get('sources', [])]
    headers = [resolve_header(spec_dir, include_dirs, p, "header")
               for p in spec.get('headers', [])]
    declaration_files = [resolve_input(spec_dir, p, "declaration file")
                         for p in spec.get('declaration_files', [])]
    cflags = list(spec.get('cflags', []))
    libs = list(spec.get('libs', []))
    inc_flags = include_flags(include_dirs)
    map_roots = [spec_dir, os.path.dirname(app), *include_dirs,
                 *[os.path.dirname(p) for p in sources],
                 *[os.path.dirname(p) for p in headers]]
    map_flags = path_map_flags(map_roots)
    include_paths = {'include_%d' % i: os.path.relpath(path, out)
                     for i, path in enumerate(include_dirs)}

    input_paths = {
        'spec': spec_path,
        'app': app,
        'wrapper': wrapper,
        'shim_source': shim,
        'signature_check_source': sig_path,
        'host_source': os.path.join(HERE, 'host.c'),
        'host_stack_source': os.path.join(HERE, 'host_stack.c'),
        'host_stack_header': os.path.join(HERE, 'host_stack.h'),
        'host_abi_source': os.path.join(OBO_ROOT, 'runtime', 'host_abi.h'),
        'foreign_abi_header': os.path.join(HERE, 'foreign_abi.h'),
        'start_source': os.path.join(HERE, 'start.S'),
        'link_script': os.path.join(HERE, 'link.ld'),
        'tool': os.path.realpath(__file__),
        'compiler': obo,
        'cc': cc,
    }
    input_paths.update(hosted_rt['sources'])
    if wrapper_source is not None:
        input_paths['wrapper_source'] = wrapper_source
    for i, src, dest in extra_wrappers:
        input_paths['wrapper_source_%d' % i] = src
        input_paths['wrapper_extra_%d' % i] = dest
    for i, p in enumerate(sources):
        input_paths['source_%d' % i] = p
    for i, p in enumerate(headers):
        input_paths['header_%d' % i] = p
    for i, p in enumerate(declaration_files):
        input_paths['declaration_%d' % i] = p
    compiler_version = run([obo, '--version']).stdout.strip()
    toolchain = run([cc, '--version']).stdout.splitlines()[0].strip()
    input_manifest_path = os.path.join(out, 'input-manifest.json')
    input_doc = manifest_doc(spec, input_paths, compiler_version, toolchain, sig_mode,
                             'input', include_dirs=spec.get('include_dirs', []),
                             include_paths=include_paths, cflags=cflags)
    input_doc['paths'] = {k: os.path.relpath(v, out) for k, v in input_paths.items()}
    write_manifest(input_manifest_path, input_doc)
    verify_doc(input_doc, out, False)   # check the inputs before compiling

    # The signature check and every binding source see the same preprocessor
    # state: -fno-pie, -O2, include dirs and cflags. A private sysroot therefore
    # works in header mode, and a flag or __OPTIMIZE__ branch that changes a
    # prototype cannot validate one declaration and compile another.
    source_flags = ['-fno-pie', '-O2', '-I', spec_dir, *inc_flags, *map_flags, *cflags]
    sig_flags = ['-fno-pie', '-O2', '-Werror', '-I', spec_dir, *inc_flags, *map_flags, *cflags]
    sig_o = os.path.join(out, 'sigcheck.o')
    run([cc, '-c', *sig_flags, sig_path, '-o', sig_o])
    shim_o = os.path.join(out, 'shim.o')
    run([cc, '-c', shim, '-o', shim_o])
    src_objs = []
    for i, p in enumerate(sources):
        o = os.path.join(out, 'src%d.o' % i)
        run([cc, '-c', *source_flags, p, '-o', o])
        src_objs.append(o)
    header_deps = collect_header_deps(
        cc, [(sig_path, sig_flags), *[(p, source_flags) for p in sources]], out)
    host_o = os.path.join(out, 'host.o')
    host_stack_o = os.path.join(out, 'host_stack.o')
    start_o = os.path.join(out, 'start.o')
    run([cc, '-c', '-fno-pie', '-O2', os.path.join(HERE, 'host.c'), '-o', host_o])
    run([cc, '-c', '-fno-pie', '-O2', os.path.join(HERE, 'host_stack.c'), '-o', host_stack_o])
    run([cc, '-c', os.path.join(HERE, 'start.S'), '-o', start_o])
    crt = []
    for name in ('crt1.o', 'crti.o', 'crtbegin.o', 'crtend.o', 'crtn.o'):
        p = run([cc, '-print-file-name=' + name]).stdout.strip()
        if not p or not os.path.exists(p):
            raise ToolError("cannot find the host %s (needed for normal CRT startup)" % name)
        crt.append(p)

    image = os.path.join(out, spec['module'] + '.image')
    relocs_path = os.path.join(out, 'native.relocs')
    run([obo, app, '-o', image, '--rt', hosted_rt['dir'], '-I', out, '-I', os.path.dirname(app),
         '--sym-dir', symdir, '--native-relocs', relocs_path])
    relocs = read_relocs(relocs_path)
    validate_calls(relocs, spec)
    foreign_entries = validate_foreign(getattr(relocs, 'foreign', []), spec)
    foreign_src = os.path.join(out, 'foreign_table.c')
    write_text(foreign_src, generate_foreign_table(spec, foreign_entries, sig_mode))
    foreign_o = os.path.join(out, 'foreign_table.o')
    run([cc, '-c', '-I', HERE, *sig_flags, foreign_src, '-o', foreign_o])
    validate_thunks(shim_o, spec)
    host_names = sorted({r['sym'] for r in relocs if r.get('mode') == 'host'})
    host_shim = os.path.join(out, 'host_shim.S')
    host_shim_o = None
    if host_names:
        write_text(host_shim, generate_host_shim(host_names))
        host_shim_o = os.path.join(out, 'host_shim.o')
        run([cc, '-c', host_shim, '-o', host_shim_o])
        p = run(['nm', '-g', '--defined-only', host_shim_o])
        got = set()
        for line in p.stdout.splitlines():
            parts = line.split()
            if parts and parts[-1].startswith('obo_native_'):
                got.add(parts[-1])
        want = {'obo_native_' + h for h in host_names}
        if got != want:
            raise ToolError("host thunk set mismatch: got %s, expected %s"
                            % (sorted(got), sorted(want)))
    native_o = os.path.join(out, 'native.o')
    make_object(image, native_o, relocs)
    exe = os.path.join(out, args.name or 'app')
    host_objs = [host_shim_o] if host_shim_o else []
    link = [cc, '-nostartfiles', '-no-pie', '-T', os.path.join(HERE, 'link.ld'),
            '-o', exe, crt[0], crt[1], crt[2], native_o, host_o, host_stack_o, start_o,
            shim_o, *host_objs, foreign_o, *src_objs, crt[3], crt[4],
            '-Wl,--no-as-needed', *LINK_HARDENING_FLAGS, *libs, '-lc']
    run(link)
    validate_final(exe)

    paths = dict(input_paths)
    paths.update({
        'wrapper_sym': os.path.join(symdir, spec['module'] + '.sym'),
        'shim_object': shim_o,
        'signature_check_object': sig_o,
        'host_object': host_o,
        'host_stack_object': host_stack_o,
        'start_object': start_o,
        'runtime_text': hosted_rt['blobs']['runtime_text'],
        'runtime_data': hosted_rt['blobs']['runtime_data'],
        'foreign_table_source': foreign_src,
        'foreign_table_object': foreign_o,
        'image': image,
        'relocs': relocs_path,
        'native_object': native_o,
        'executable': exe,
        'input_manifest': input_manifest_path,
    })
    if host_shim_o:
        paths['host_shim_source'] = host_shim
        paths['host_shim_object'] = host_shim_o
    for i, o in enumerate(src_objs):
        paths['source_object_%d' % i] = o
    doc = manifest_doc(spec, paths, compiler_version, toolchain, sig_mode, 'build',
                       include_dirs=spec.get('include_dirs', []),
                       include_paths=include_paths, cflags=cflags,
                       header_deps=header_deps, link_flags=LINK_HARDENING_FLAGS)
    doc['paths'] = {k: os.path.relpath(v, out) for k, v in paths.items()}
    doc['resolved_libraries'] = resolved_libraries(exe)
    manifest_path = os.path.join(out, 'manifest.json')
    write_manifest(manifest_path, doc)
    verify_manifest(manifest_path)
    print("built %s" % exe)
    print("manifest %s" % manifest_path)
    return 0


def verify_cmd(args):
    verify_manifest(args.manifest)
    print('manifest ok')
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('spec')
    b.add_argument('app')
    b.add_argument('--out', required=True)
    b.add_argument('--obo')
    b.add_argument('--cc', default=os.environ.get('CC', 'gcc'))
    b.add_argument('--name', default='app')
    b.set_defaults(func=build)
    v = sub.add_parser('verify')
    v.add_argument('manifest')
    v.set_defaults(func=verify_cmd)
    args = ap.parse_args()
    try:
        return args.func(args) or 0
    except ToolError as e:
        print("cinterop: %s" % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
