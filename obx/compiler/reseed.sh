#!/bin/sh
# Rebuilds seed/obxc.c from the current sources and checks the fixed point:
# out/obxc builds stage 2, stage 2 builds stage 3, and the C of stages 2 and 3
# must be identical. Run it after changing the compiler (build out/obxc first,
# with build.sh, or with ref/obxc.py while ref is still the reference).
#     obx/compiler/reseed.sh
set -e
D=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export OBX_LIB="$D/../lib"
python3 "$D/gen_rt.py" >/dev/null
"$D/out/obxc" "$D/main.obx" -o "$T/s2"
"$T/s2" "$D/main.obx" --emit-c > "$T/c2.c"
"$T/s2" "$D/main.obx" -o "$T/s3"
"$T/s3" "$D/main.obx" --emit-c > "$T/c3.c"
cmp "$T/c2.c" "$T/c3.c" || { echo "reseed: no fixed point: stage 2 and stage 3 C differ" >&2; exit 1; }
cp "$T/c2.c" "$D/seed/obxc.c"
echo "seed/obxc.c: fixed point reached ($(wc -c < "$D/seed/obxc.c") bytes)"
