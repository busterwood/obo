#!/bin/sh
# Native self-build check (design/native.md, phase 4): out/obxc builds the compiler
# natively (stage 2), stage 2 builds it natively again (stage 3); the two executables
# must be identical, and stage 2's --emit-c must equal out/obxc's.
#     obx/tools/native_selfbuild.sh
set -e
D=$(cd "$(dirname "$0")/../compiler" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export OBX_LIB="$D/../lib"
"$D/out/obxc" "$D/main.obx" --native -o "$T/s2"
"$T/s2" "$D/main.obx" --native -o "$T/s3"
cmp "$T/s2" "$T/s3" || { echo "native self-build: stage 2 and stage 3 differ" >&2; exit 1; }
"$D/out/obxc" "$D/main.obx" --emit-c > "$T/c1.c"
"$T/s2" "$D/main.obx" --emit-c > "$T/c2.c"
cmp "$T/c1.c" "$T/c2.c" || { echo "native self-build: stage 2's C differs" >&2; exit 1; }
echo "native self-build: fixed point ($(wc -c < "$T/s2") bytes)"
