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

CODE_BASE = 0x401000
IMAGE_BASE = 0x400000
RODATA_BASE = 0x08000000
GLOBAL_BASE = 0x0C000000
RT_TEXT = 0x10000000
RT_DATA = 0x20000000
PLATFORM = 'x86_64-linux-gnu'
ABI = 'sysv-amd64'
FORMAT = 'cinterop-proto/1'
RELOC_HEADER = 'cinterop-relocs 1'
MAX_ARGS = 8
ARG_REGS = ('%rdi', '%rsi', '%rdx', '%rcx', '%r8', '%r9')
EFFECTS = ('freesall', 'writeshandles')
SIG_MODES = ('header', 'declarations-only')
REQUIRED_INPUT_KEYS = {
    'spec', 'app', 'wrapper', 'shim_source', 'signature_check_source',
    'host_source', 'host_stack_source', 'host_stack_header',
    'host_abi_source', 'start_source', 'link_script', 'runtime_source',
    'runtime_entry', 'runtime_link_script', 'tool', 'compiler', 'cc',
}
REQUIRED_BUILD_KEYS = REQUIRED_INPUT_KEYS | {
    'wrapper_sym', 'shim_object', 'signature_check_object', 'host_object',
    'host_stack_object', 'start_object', 'runtime_text', 'runtime_data',
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


def load_spec(path):
    try:
        spec = json.load(open(path))
    except (OSError, ValueError) as e:
        raise ToolError("cannot read spec %s: %s" % (path, e))
    if spec.get('format') != FORMAT:
        raise ToolError("spec format is %r, expected %r" % (spec.get('format'), FORMAT))
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
        if f['ret'] not in ('i32', 'i64'):
            raise ToolError("%s: ret must be i32 or i64" % f['name'])
        if not isinstance(f['args'], list) or len(f['args']) > MAX_ARGS:
            raise ToolError("%s: args must be a list of at most %d entries" % (f['name'], MAX_ARGS))
        for a in f['args']:
            if a == 'i32':
                raise ToolError("%s: i32 arguments are not supported in this prototype; use i64" % f['name'])
            if a != 'i64':
                raise ToolError("%s: args must be i64" % f['name'])
        effects = f.get('effects', ['impure'])
        if not isinstance(effects, list) or any(e != 'impure' for e in effects):
            raise ToolError("%s: only the conservative 'impure' effect is supported" % f['name'])
        for bad in ('variadic', 'struct_by_value', 'tls', 'floats', 'callbacks'):
            if f.get(bad):
                raise ToolError("%s: %s is not supported by this prototype" % (f['name'], bad))
    for key in ('sources', 'headers', 'libs', 'cflags', 'declarations'):
        if key in spec and not isinstance(spec[key], list):
            raise ToolError("%s must be a list" % key)
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


def generate_shim(spec):
    out = ['    .text']
    for f in spec['functions']:
        n = len(f['args'])
        sym = 'obo_native_' + f['symbol']
        out += ['', '    .globl %s' % sym, '    .type %s,@function' % sym, '%s:' % sym,
                '    push %rbp', '    mov %rsp, %rbp']
        stack = list(range(6, n))
        if stack and len(stack) % 2 == 1:
            out.append('    sub $8, %rsp')
        for i in reversed(stack):
            off = 16 + 8 * (n - 1 - i)
            out.append('    pushq %d(%%rbp)' % off)
        for i in range(min(n, 6)):
            off = 16 + 8 * (n - 1 - i)
            out.append('    mov %d(%%rbp), %s' % (off, ARG_REGS[i]))
        out.append('    call %s@PLT' % f['symbol'])
        if f['ret'] == 'i32':
            out.append('    movslq %eax, %rax')
        out += ['    leave', '    ret']
    out += ['', '    .section .note.GNU-stack,"",@progbits', '']
    return '\n'.join(out)


def generate_sigcheck(spec):
    """Compile-time C type assertion. A header check is stronger than a
    declarations-only check: the latter checks the spec against the author's
    explicit declaration, not against the installed library header."""
    headers = spec.get('headers', [])
    decls = spec.get('declarations', [])
    mode = 'header' if headers else 'declarations-only'
    out = ['/* generated signature check; not linked */']
    for h in headers:
        out.append('#include "%s"' % h)
    for d in decls:
        out.append(d)
    for i, f in enumerate(spec['functions']):
        ret = c_arg(f['ret'])
        args = ', '.join(c_arg(a) for a in f['args']) or 'void'
        out.append('static %s (*obo_sig_%d)(%s) __attribute__((unused)) = %s;'
                   % (ret, i, args, f['symbol']))
    out.append('')
    return '\n'.join(out), mode


def manifest_doc(spec, paths, compiler_version, toolchain, signature_check, kind):
    files = {}
    for key, path in paths.items():
        files[key] = sha256(require_file(path, key))
    return {
        'format': FORMAT,
        'kind': kind,
        'module': spec['module'],
        'platform': spec['platform'],
        'abi': spec['abi'],
        'pointer_width': 64,
        'compiler': compiler_version,
        'toolchain': toolchain,
        'signature_check': signature_check,
        'effects': list(EFFECTS),
        'symbols': [{'name': f['name'], 'symbol': f['symbol'],
                     'args': f['args'], 'ret': f['ret']} for f in spec['functions']],
        'expected_thunks': ['obo_native_' + f['symbol'] for f in spec['functions']],
        'files': files,
        'paths': {},
    }


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


def image_segments(data):
    ph = read_phdrs(data)
    seg = {p['vaddr']: p for p in ph if p['type'] == 1}
    for v in (IMAGE_BASE, RODATA_BASE, GLOBAL_BASE, RT_TEXT, RT_DATA):
        if v not in seg:
            raise ToolError("compiler image has no PT_LOAD at %#x" % v)
    return seg


def read_relocs(path):
    if not os.path.isfile(path):
        raise ToolError("native relocation sidecar is missing: %s" % path)
    lines = [ln.strip() for ln in open(path) if ln.strip()]
    if not lines or lines[0] != RELOC_HEADER:
        raise ToolError("native relocation sidecar has no %r header" % RELOC_HEADER)
    out = []
    for line in lines[1:]:
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
        if nargs < 0 or nargs > MAX_ARGS:
            raise ToolError("native relocation arity %d out of range" % nargs)
        argtypes = [] if types == '-' else types.split(',')
        if len(argtypes) != nargs or any(t != 'i64' for t in argtypes):
            raise ToolError("native relocation argument types do not match arity: %r" % line)
        if ret != 'i64' or effects != 'freesall,writeshandles':
            raise ToolError("native relocation has an unexpected result/effect contract: %r" % line)
        out.append({'off': off, 'sym': sym, 'nargs': nargs,
                    'argtypes': argtypes, 'ret': ret, 'effects': effects})
    return out


def validate_calls(relocs, spec):
    by_sym = {f['symbol']: f for f in spec['functions']}
    seen = set()
    offsets = set()
    for r in relocs:
        if r['off'] in offsets:
            raise ToolError("duplicate native relocation offset %d" % r['off'])
        offsets.add(r['off'])
        f = by_sym.get(r['sym'])
        if f is None:
            raise ToolError("native call to %r is not in the binding spec" % r['sym'])
        if r['nargs'] != len(f['args']):
            raise ToolError("native call to %r has %d argument(s), spec says %d"
                            % (r['sym'], r['nargs'], len(f['args'])))
        if r['argtypes'] != ['i64'] * len(f['args']):
            raise ToolError("native call to %r has non-i64 argument types" % r['sym'])
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
    if doc['format'] != FORMAT:
        raise ToolError("manifest format is %r, expected %r" % (doc['format'], FORMAT))
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
        if not isinstance(sym['args'], list) or any(a != 'i64' for a in sym['args']):
            raise ToolError("manifest symbol arguments are not i64")
        if sym['ret'] not in ('i32', 'i64'):
            raise ToolError("manifest symbol result is not i32/i64")
    if sorted(doc['expected_thunks']) != sorted('obo_native_' + s['symbol'] for s in doc['symbols']):
        raise ToolError("manifest thunk list does not match its symbols")
    files = doc['files']
    paths = doc['paths']
    if not isinstance(files, dict) or not isinstance(paths, dict) or set(files) != set(paths):
        raise ToolError("manifest files/paths are missing or inconsistent")
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
    slot_lines = [line.strip() for line in entry_text.splitlines()
                  if line.strip().startswith('slot ')]
    if 'slot rt_set_host_exit' not in slot_lines:
        raise ToolError("hosted runtime entry.S has no hosted exit slot")
    host_slot = slot_lines.index('slot rt_set_host_exit')
    if host_slot != RT_HOST_EXIT_SLOT:
        raise ToolError("hosted runtime slot mismatch: entry.S puts rt_set_host_exit at %d, host_abi says %d"
                        % (host_slot, RT_HOST_EXIT_SLOT))
    if '#ifdef OBO_HOSTED_EXIT' not in entry_text or 'slot rt_set_host_exit' not in entry_text:
        raise ToolError("runtime entry.S has no hosted exit slot")
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

    sources = [resolve_input(spec_dir, p, "source") for p in spec.get('sources', [])]
    headers = [resolve_input(spec_dir, p, "header") for p in spec.get('headers', [])]
    declaration_files = [resolve_input(spec_dir, p, "declaration file")
                         for p in spec.get('declaration_files', [])]
    cflags = list(spec.get('cflags', []))
    libs = list(spec.get('libs', []))

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
    input_doc = manifest_doc(spec, input_paths, compiler_version, toolchain, sig_mode, 'input')
    input_doc['paths'] = {k: os.path.relpath(v, out) for k, v in input_paths.items()}
    write_manifest(input_manifest_path, input_doc)
    verify_doc(input_doc, out, False)   # check the inputs before compiling

    sig_o = os.path.join(out, 'sigcheck.o')
    run([cc, '-c', '-fno-pie', '-Werror', '-I', spec_dir, sig_path, '-o', sig_o])
    shim_o = os.path.join(out, 'shim.o')
    run([cc, '-c', shim, '-o', shim_o])
    src_objs = []
    for i, p in enumerate(sources):
        o = os.path.join(out, 'src%d.o' % i)
        run([cc, '-c', '-fno-pie', '-O2', *cflags, p, '-o', o])
        src_objs.append(o)
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
    validate_thunks(shim_o, spec)
    native_o = os.path.join(out, 'native.o')
    make_object(image, native_o, relocs)
    exe = os.path.join(out, args.name or 'app')
    link = [cc, '-nostartfiles', '-no-pie', '-T', os.path.join(HERE, 'link.ld'),
            '-o', exe, crt[0], crt[1], crt[2], native_o, host_o, host_stack_o, start_o,
            shim_o, *src_objs, crt[3], crt[4],
            '-Wl,--no-as-needed', '-Wl,-z,noexecstack', *libs, '-lc']
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
        'image': image,
        'relocs': relocs_path,
        'native_object': native_o,
        'executable': exe,
        'input_manifest': input_manifest_path,
    })
    for i, o in enumerate(src_objs):
        paths['source_object_%d' % i] = o
    doc = manifest_doc(spec, paths, compiler_version, toolchain, sig_mode, 'build')
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
