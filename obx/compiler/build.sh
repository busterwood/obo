#!/bin/sh
# Builds the obx compiler, out/obxc, from the checked-in seed: only gcc is needed.
#     obx/compiler/build.sh
# out/obxc finds the library at ../../lib (obx/lib); set OBX_LIB to use another.
set -e
D=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$D/out"
gcc -O2 -w -std=gnu11 -o "$D/out/obxc" "$D/seed/obxc.c" -lm
echo "built $D/out/obxc"
