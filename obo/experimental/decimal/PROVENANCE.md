# Provenance

## Approved research

This experimental package implements only the D1 foundations described by:

- `obo/design/decimal-research-2026-10-28.md`
- `obo/design/decimal-arithmetic-research-2026-10-29.md`
- `obo/design/numeric-literals-decision-2026-10-29.md`

The 16-byte/96-bit `.NET`/`System.Decimal` sketches and the `m` literal suffix
are superseded for the target design and literal policy. `decimal(p,s)` remains
a proposal; the binding helper here is mathematical validation only.

## Reviewed private prototype

The core, oracle and retained tests were copied from the reviewed repaired
private prototype, not from the immutable original tree:

```text
/tmp/opencode/decimal-foundations-fix/
```

That copy's manifest verified before import. Its relevant reviewed hashes were:

```text
8ce62954f287f4ed2b130006276e834f1990209cb5d4c086caa9834b4cadd89d  decimal/dec24.c
26fa6fbd5970a7886c8066a24520967ec58ae9a54870fd446c53e3d3a21b88da  decimal/dec24.h
940bfc6a1a9d60071a220ca980b03250c1384fb5de3d04b49e7290866d3fb6a9  decimal/dec24_test.c
4c0aada160ac6d6d043045cb63d94a723921708e1fe1fe4202116434528d09db  decimal/decimal_oracle.py
```

The original immutable prototype tree at
`/tmp/opencode/numeric-foundations` was not edited. Its manifest SHA-256 is:

```text
b2f3ad8a8bb430d3eda9e2c598a829e590066f8bd0dd11a71cbadc6423e80a1f
```

## Imported and changed in this repository

`tests/dec24_test.c` and `tests/decimal_oracle.py` are byte-identical to the
reviewed copies above. `dec24.c` and `dec24.h` retain the reviewed core and
receive only additive D1 status constants/names (`PRECISION_OVERFLOW`,
`EXPONENT_RANGE`, `DISPLAY_RANGE`, `BUFFER_SMALL`); no existing operation,
status value or tested behavior changed. Current hashes:

```text
e98d59de10eeb8eb89dc9930ec0a2b70aa0c0de68c60157649c220a1653882ec  dec24.c
d97d107eca85123c268d7cb2555b0d75b22d3db9566e457685992065f997f516  dec24.h
dae4e2f2a0231e422bf338d18c86b38c8b1ef9da193402abe0171fd637fa8e72  dec24_text.c
d7ee8793d4ddcf7078b2519e00473d5934cc00f370159aa11160f679647d4808  dec24_text.h
b81ffc8fc14202be4f053f66ab0d381985fe7034bda5adf9a5cfdf006801c7cb  dec24_bind.c
bb285370458d035d0ae302d6bc15aaa1d49cc8776a7a4b2c68052f4ff194d869  dec24_bind.h
940bfc6a1a9d60071a220ca980b03250c1384fb5de3d04b49e7290866d3fb6a9  tests/dec24_test.c
4c0aada160ac6d6d043045cb63d94a723921708e1fe1fe4202116434528d09db  tests/decimal_oracle.py
```

New D1 files are the text and binding modules, their independent Python
oracles/generators, the C test bodies, the aggregate/host/start entry points and
the evidence runner. They were written for this package; no monolithic untested
fast path was copied from the private scratch trees.

## Evidence

The runner writes fresh immutable evidence outside the repository:

```sh
python3 obo/experimental/decimal/tests/run_decimal_tests.py \
    --evidence /tmp/opencode/decimal-D1-<timestamp>
```

It records exact package source hashes, generated fixture hashes, build/run
commands, C and Python output, sanitizer output, `nm`/size/disassembly output
and the current git HEAD/status. Source generation does not write into the
repository working tree.

Suggested package integrity command (excluding this file while generating):

```sh
cd obo/experimental/decimal
LC_ALL=C find . -type f ! -name MANIFEST.sha256 -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum > MANIFEST.sha256
```
