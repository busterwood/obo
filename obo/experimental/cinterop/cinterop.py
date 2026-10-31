#!/usr/bin/env python3
"""Experimental in-process C interop for obo.

    python3 obo/experimental/cinterop/cinterop.py build SPEC APP.obx --out DIR [--obo PATH]
    python3 obo/experimental/cinterop/cinterop.py verify MANIFEST

SPEC is a small JSON file (format cinterop-proto/0). The tool generates an
ordinary .obx wrapper module, an assembly thunk file, a manifest with SHA-256
hashes, and a real system-linked non-PIE ELF. The obo compiler is invoked with
its experimental --native-relocs sidecar. This is a prototype: it extracts the
fixed obo image's load regions into sections before the final link, and the host
startup builds the initial-stack block that rt_init expects. See
../../design/native-c-interop-design.md.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OBO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_OBO = os.path.join(OBO_ROOT, 'obo')
DEFAULT_RT = OBO_ROOT

CODE_BASE = 0x401000
IMAGE_BASE = 0x400000
RODATA_BASE = 0x08000000
GLOBAL_BASE = 0x0C000000
RT_TEXT = 0x10000000
RT_DATA = 0x20000000
PLATFORM = 'x86_64-linux-gnu'
ABI = 'sysv-amd64'
FORMAT = 'cinterop-proto/0'
MAX_ARGS = 8
ARG_REGS = ('%rdi', '%rsi', '%rdx', '%rcx', '%r8', '%r9')


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
    seen = set()
    for f in funcs:
        for k in ('name', 'symbol', 'args', 'ret'):
            if k not in f:
                raise ToolError("function entry needs %r" % k)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', f['name']):
            raise ToolError("bad obo function name %r" % f['name'])
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', f['symbol']):
            raise ToolError("bad C symbol %r" % f['symbol'])
        if f['name'] in seen:
            raise ToolError("duplicate function %r" % f['name'])
        seen.add(f['name'])
        if f['ret'] not in ('i32', 'i64'):
            raise ToolError("%s: ret must be i32 or i64" % f['name'])
        if not isinstance(f['args'], list) or len(f['args']) > MAX_ARGS:
            raise ToolError("%s: args must be a list of at most %d entries" % (f['name'], MAX_ARGS))
        for a in f['args']:
            if a not in ('i32', 'i64'):
                raise ToolError("%s: args must be i32 or i64" % f['name'])
        effects = f.get('effects', ['impure'])
        if not isinstance(effects, list) or not effects:
            raise ToolError("%s: effects must be a non-empty list" % f['name'])
    for key in ('sources', 'headers', 'libs', 'cflags', 'declarations'):
        if key in spec and not isinstance(spec[key], list):
            raise ToolError("%s must be a list" % key)
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
        out.append('    return cinterop("%s"%s)' % (f['symbol'], cinterop_args(len(f['args']))))
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
    """A C translation unit that fails if a provided header declares a different
    C signature for a function than the spec. It does not prove the linked
    library's implementation; it catches spec/header arity and type mistakes."""
    if not spec.get('headers') and not spec.get('declarations'):
        return None, 'unchecked'
    out = ['/* generated signature check; not linked */']
    for h in spec.get('headers', []):
        out.append('#include "%s"' % h)
    for d in spec.get('declarations', []):
        out.append(d)
    for i, f in enumerate(spec['functions']):
        ret = c_arg(f['ret'])
        args = ', '.join(c_arg(a) for a in f['args']) or 'void'
        out.append('static %s (*obo_sig_%d)(%s) __attribute__((unused)) = %s;'
                   % (ret, i, args, f['symbol']))
    out.append('')
    return '\n'.join(out), 'checked'


def generate_manifest(spec, paths, compiler_version, toolchain):
    files = {}
    for key, path in paths.items():
        if os.path.exists(path):
            files[key] = sha256(path)
    return {
        'format': FORMAT,
        'module': spec['module'],
        'platform': spec['platform'],
        'abi': spec['abi'],
        'compiler': compiler_version,
        'toolchain': toolchain,
        'files': files,
        'symbols': [{'name': f['name'], 'symbol': f['symbol'], 'args': f['args'], 'ret': f['ret']} for f in spec['functions']],
    }


def write_text(path, text):
    with open(path, 'w', newline='\n') as f:
        f.write(text)


def align(n, a):
    return (n + a - 1) // a * a


def parse_phdrs(data):
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ToolError("compiler output is not a 64-bit little-endian ELF image")
    phoff = struct.unpack_from('<Q', data, 0x20)[0]
    phentsize = struct.unpack_from('<H', data, 0x36)[0]
    phnum = struct.unpack_from('<H', data, 0x38)[0]
    if phentsize != 56:
        raise ToolError("unexpected program header size %d" % phentsize)
    seg = {}
    for i in range(phnum):
        at = phoff + i * phentsize
        typ, flags = struct.unpack_from('<II', data, at)
        off, vaddr, _paddr, filesz, memsz, al = struct.unpack_from('<QQQQQQ', data, at + 8)
        if typ == 1:
            seg[vaddr] = (off, filesz, memsz, flags)
    for v in (IMAGE_BASE, RODATA_BASE, GLOBAL_BASE, RT_TEXT, RT_DATA):
        if v not in seg:
            raise ToolError("compiler image has no PT_LOAD at %#x" % v)
    return seg


def read_relocs(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            off, sym = line.split()
            off = int(off)
        except ValueError:
            raise ToolError("bad native relocation line %r" % line)
        if off < 1 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', sym):
            raise ToolError("bad native relocation %r" % line)
        out.append((off, sym))
    return out


def make_object(image, out, relocs):
    """Turn the fixed image's PT_LOADs into a relocatable object at the same VAs."""
    data = open(image, 'rb').read()
    seg = parse_phdrs(data)

    def region(v):
        off, filesz, memsz, _flags = seg[v]
        b = data[off:off + filesz]
        return b + b'\0' * (memsz - len(b))

    text = data[seg[IMAGE_BASE][0] + (CODE_BASE - IMAGE_BASE):seg[IMAGE_BASE][0] + seg[IMAGE_BASE][1]]
    names = set()
    for off, sym in relocs:
        if off < 1 or off + 3 >= len(text):
            raise ToolError("native relocation offset %d is outside .obo.text" % off)
        if text[off - 1] != 0xE8:
            raise ToolError("native relocation at %d does not follow a call opcode" % off)
        names.add(sym)
    sections = []

    def add(name, typ, flags, content, size=None, alignv=16, link=0, info=0, entsize=0):
        sections.append({'name': name, 'typ': typ, 'flags': flags, 'content': content,
                         'size': len(content) if size is None else size, 'align': alignv,
                         'link': link, 'info': info, 'entsize': entsize})

    add('.obo.text', 1, 6, text)
    add('.obo.rodata', 1, 2, region(RODATA_BASE))
    add('.obo.bss', 8, 3, b'', size=seg[GLOBAL_BASE][2])
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
        for off, sym in relocs:
            rela += struct.pack('<QQq', off, (native_index[sym] << 32) | 4, -4)
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


def build(args):
    spec_path = os.path.abspath(args.spec)
    app = os.path.abspath(args.app)
    out = os.path.abspath(args.out)
    spec = load_spec(spec_path)
    os.makedirs(out, exist_ok=True)
    symdir = os.path.join(out, 'sym')
    os.makedirs(symdir, exist_ok=True)
    obo = os.path.abspath(args.obo) if args.obo else os.environ.get('OBO', DEFAULT_OBO)
    if not os.path.exists(obo):
        raise ToolError("obo compiler not found at %s" % obo)
    wrapper = os.path.join(out, spec['module'] + '.obx')
    shim = os.path.join(out, spec['module'] + '_shim.S')
    write_text(wrapper, generate_wrapper(spec))
    write_text(shim, generate_shim(spec))
    spec_dir = os.path.dirname(spec_path)
    sigcheck, sigcheck_status = generate_sigcheck(spec)
    if sigcheck is not None:
        sig_path = os.path.join(out, 'sigcheck.c')
        write_text(sig_path, sigcheck)
        sig_o = os.path.join(out, 'sigcheck.o')
        run([args.cc, '-c', '-fno-pie', '-Werror', '-I', spec_dir, sig_path, '-o', sig_o])
    sources = []
    for s in spec.get('sources', []):
        p = s if os.path.isabs(s) else os.path.join(spec_dir, s)
        if not os.path.exists(p):
            raise ToolError("source not found: %s" % p)
        sources.append(os.path.abspath(p))
    cflags = list(spec.get('cflags', []))
    shim_o = os.path.join(out, 'shim.o')
    run([args.cc, '-c', shim, '-o', shim_o])
    src_objs = []
    for i, p in enumerate(sources):
        o = os.path.join(out, 'src%d.o' % i)
        run([args.cc, '-c', '-fno-pie', '-O2', *cflags, p, '-o', o])
        src_objs.append(o)
    host_o = os.path.join(out, 'host.o')
    start_o = os.path.join(out, 'start.o')
    run([args.cc, '-c', '-fno-pie', '-O2', os.path.join(HERE, 'host.c'), '-o', host_o])
    run([args.cc, '-c', os.path.join(HERE, 'start.S'), '-o', start_o])
    crt = []
    for name in ('crt1.o', 'crti.o', 'crtn.o'):
        p = run([args.cc, '-print-file-name=' + name]).stdout.strip()
        if not p or not os.path.exists(p):
            raise ToolError("cannot find the host %s (needed for normal CRT startup)" % name)
        crt.append(p)
    image = os.path.join(out, spec['module'] + '.image')
    relocs_path = os.path.join(out, 'native.relocs')
    cmd = [obo, app, '-o', image, '--rt', DEFAULT_RT, '-I', out, '-I', os.path.dirname(app),
           '--sym-dir', symdir, '--native-relocs', relocs_path]
    run(cmd)
    relocs = read_relocs(relocs_path)
    spec_symbols = {f['symbol'] for f in spec['functions']}
    for _off, sym in relocs:
        if sym not in spec_symbols:
            raise ToolError("compiler recorded unknown native symbol %r" % sym)
    native_o = os.path.join(out, 'native.o')
    make_object(image, native_o, relocs)
    exe = os.path.join(out, args.name or 'app')
    libs = list(spec.get('libs', []))
    link = [args.cc, '-nostartfiles', '-no-pie', '-T', os.path.join(HERE, 'link.ld'),
            '-o', exe, crt[0], crt[1], native_o, host_o, start_o, shim_o, *src_objs, crt[2],
            '-Wl,--no-as-needed', '-Wl,-z,noexecstack', *libs, '-lc']
    run(link)
    # Read back the result: this must be a fixed non-PIE executable.
    data = open(exe, 'rb').read()
    if data[:4] != b'\x7fELF' or struct.unpack_from('<H', data, 16)[0] != 2:
        raise ToolError("final artifact is not ET_EXEC")
    ph = parse_final_phdrs(data)
    expected = {CODE_BASE, RODATA_BASE, GLOBAL_BASE, RT_TEXT, RT_DATA}
    if not expected.issubset(set(ph)):
        raise ToolError("final artifact is missing an expected obo load segment")
    compiler_version = run([obo, '--version']).stdout.strip()
    toolchain = run([args.cc, '--version']).stdout.splitlines()[0].strip()
    paths = {
        'spec': spec_path,
        'wrapper': wrapper,
        'wrapper_sym': os.path.join(symdir, spec['module'] + '.sym'),
        'shim_source': shim,
        'shim_object': shim_o,
        'image': image,
        'native_object': native_o,
        'executable': exe,
    }
    for i, p in enumerate(sources):
        paths['source_%d' % i] = p
    if sigcheck is not None:
        paths['signature_check_source'] = sig_path
    manifest = generate_manifest(spec, paths, compiler_version, toolchain)
    manifest['signature_check'] = sigcheck_status
    manifest['paths'] = {k: os.path.relpath(v, out) for k, v in paths.items()}
    manifest_path = os.path.join(out, 'manifest.json')
    write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print("built %s" % exe)
    print("manifest %s" % manifest_path)
    return 0


def parse_final_phdrs(data):
    phoff = struct.unpack_from('<Q', data, 0x20)[0]
    phentsize = struct.unpack_from('<H', data, 0x36)[0]
    phnum = struct.unpack_from('<H', data, 0x38)[0]
    out = {}
    for i in range(phnum):
        at = phoff + i * phentsize
        typ = struct.unpack_from('<I', data, at)[0]
        off, vaddr, _paddr, filesz, memsz, al = struct.unpack_from('<QQQQQQ', data, at + 8)
        if typ == 1:
            out[vaddr] = (off, filesz, memsz, al)
    return out


def verify(args):
    manifest_path = os.path.abspath(args.manifest)
    try:
        manifest = json.load(open(manifest_path))
    except (OSError, ValueError) as e:
        raise ToolError("cannot read manifest: %s" % e)
    base = os.path.dirname(manifest_path)
    bad = []
    paths = manifest.get('paths', {})
    for key, want in manifest.get('files', {}).items():
        rel = paths.get(key)
        if not rel:
            bad.append("%s: no path in manifest" % key)
            continue
        path = os.path.normpath(os.path.join(base, rel))
        if not os.path.exists(path):
            bad.append("%s: missing %s" % (key, path))
            continue
        got = sha256(path)
        if got != want:
            bad.append("%s: hash %s, manifest says %s" % (key, got, want))
    if bad:
        raise ToolError("manifest verification failed:\n" + "\n".join(bad))
    print("manifest ok")


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
    v.set_defaults(func=verify)
    args = ap.parse_args()
    try:
        return args.func(args)
    except ToolError as e:
        print("cinterop: %s" % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
