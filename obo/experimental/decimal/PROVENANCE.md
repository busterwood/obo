# Provenance

## Approved research

This experimental package implements the D1 foundations and the D2 arithmetic
contract described by:

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

The reviewed core kept its limb primitives, status values and tested D1
behavior. D2 changes are additive except for the deliberate
replacement of the old `DEC_UNSUPPORTED_EXACT` add/sub refusal by the sticky
rounded path; the exact entry points report `DEC_INEXACT` instead where the
exact result is not 38-digit representable. The import-time reviewed hashes
above remain the historical provenance of the primitive code.

Step 1 (behavior-preserving optimization) added the BSR/`10^i` digit count,
the `divq` fast paths, 19-digit chunk drop/scale and the text 19-digit split.
Step 2 added `dec24_arith.c`/`.h`, the exact/rounded contract, division,
truncating remainder and the wide-gap sticky add/sub.

Current D2 hashes:

```text
0954ca0e71b4700b8f2a58739f423bb5807a641792c678141e459b29d54742ad  dec24.c
e71df2a6c45d4909fa83f5ae3e6ef48a1368b7f2b01973a53c8924535f2cd3ff  dec24.h
4bfc67b2ff59e0600c26db8563cf202b9ddd53b8f499a9d2b2e60105be05d491  dec24_text.c
04c8b9133686cdd597a0aebbe3fec79f4443c6f79d46df03b83a51e581e5ce7b  dec24_text.h
b81ffc8fc14202be4f053f66ab0d381985fe7034bda5adf9a5cfdf006801c7cb  dec24_bind.c
bb285370458d035d0ae302d6bc15aaa1d49cc8776a7a4b2c68052f4ff194d869  dec24_bind.h
fce297916a6f1acf2af8cca81e6ed5940be42d29370d835126bebbc09a7c74da  dec24_arith.c
cf3436646008ba53837bec048c31334970ed5a0917971221c4401380225d37be  dec24_arith.h
fdb9e59998e8da736a5a19e9a72a2ace6a06839fd74bf6a5a2a35465205b9c34  tests/dec24_test.c
be222cf840a8724127233b31899786c4363d77d1e36849cf1b993967cfecb899  tests/decimal_oracle.py
78e8b0b72b3f414a9b549bcc70a24247cd69635fff8cb2927ac9887024cf3aa6  tests/arithmetic_oracle.py
bf6801d5e978fe4dd98d64e53dcf846c333e17e2370f151bc95b86c3be8d3da3  tests/dec24_arith_test.c
872725b8f574860e844f60a83b68237df91a36e451cbbf3d4c3c8c80425a42f6  tests/generate_arithmetic_vectors.py
da059b622a622923293b8a6772d13c54981b3c6fd8792d8bf65397e67d7fc044  tests/test_arithmetic_oracle.py
296040cfe86836d6df8e92b09ef2c94fbd543a6a124f15832a1c65ef65bc551e  tests/run_decimal_tests.py
36a5d1585c4966f1d1ae79f9bb0823ee32fd07126e9c15db7b44d219663507c7  tests/run_property_probes.py
```

`tests/dec24_test.c` now includes the explicit D2 replacement of the old
`12e76 + 1` refusal; the rest of the retained D1 vectors are unchanged or
extended. `tests/decimal_oracle.py` gained a wide-gap fallback that delegates
to the independent rational model instead of returning `UNSUPPORTED_EXACT`.

The focused upper-exponent repair changed only `dec24_text.c`/`.h` and tests: a
parsed value whose normalized exponent exceeds 32767 puts back the minimal
coefficient trailing zeros needed to store exponent 32767, if the result stays
within 38 digits. It adds no ABI or compiler change.

The D2 arithmetic oracle (`tests/arithmetic_oracle.py`) is independent of the C
implementation: it uses `Fraction` plus integer rounding decisions, not Python
`decimal`, and the property probes compare division, remainder and wide-gap
results against it. The retained Python `decimal` cross-check remains a
secondary D1 check only.

## Evidence

The runner writes fresh immutable evidence outside the repository:

```sh
python3 obo/experimental/decimal/tests/run_decimal_tests.py \
    --evidence /tmp/opencode/decimal-D2-<timestamp>
python3 obo/experimental/decimal/tests/run_property_probes.py --full \
    --evidence /tmp/opencode/decimal-D2-<timestamp>-probes
```

They record exact package source hashes, generated fixture hashes, build/run
commands, C and Python output, sanitizer output, `nm`/size/disassembly output
and the current git HEAD/status. Source generation does not write into the
repository working tree. The D2 benchmark harness and its logs are outside the
repository under `/tmp/opencode/decimal-D2-<timestamp>-bench`; the README
records the method and medians.

Suggested package integrity command (excluding this file while generating):

```sh
cd obo/experimental/decimal
LC_ALL=C find . -type f ! -name MANIFEST.sha256 -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum > MANIFEST.sha256
```
