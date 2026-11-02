#!/usr/bin/env bash
# usage: obx/tools/gate.sh quick|full
# quick: the C seed's fixed point (it also builds compiler/out/obxc from the seed, the compiler
#        every later step uses), the conformance suite, the compiler tests, the library tests,
#        the native backend against the C backend (run + lib tests) and the native self-build.
# full:  quick, then the red-team harnesses (rounds 1-4) under ASan+UBSan, then the native
#        backend on the red-team corpora.
# Builds are cached (tools/cbuild.py; OBX_NOCACHE=1 disables, OBX_CACHE sets the directory).
set -u
tier=${1:-}
case "$tier" in quick|full) ;; *) echo "usage: $0 quick|full" >&2; exit 2;; esac
R=$(cd "$(dirname "$0")/../.." && pwd)
cd "$R"
steps=(
  "seed fixed point|python3 obx/tools/seed_check.py"
  "conformance|python3 obx/tests/run.py"
  "compiler tests|python3 obx/tests/compiler/run_tests.py"
  "lib|python3 obx/lib/run_tests.py -j 8"
  "native lib+run|python3 obx/tests/run_native.py --lib"
  "native selfbuild|bash obx/tools/native_selfbuild.sh"
)
if [ "$tier" = full ]; then
  steps+=(
    "redteam round 1|python3 obx/redteam/rt.py"
    "redteam round 2|python3 obx/redteam/round2/rt.py"
    "redteam round 3|python3 obx/redteam/round3/rt.py"
    "redteam round 4|python3 obx/redteam/round4/rt.py"
    "native redteam|python3 obx/tests/run_native.py --lib --redteam"
  )
fi
log=$(mktemp -d); status=0; summary=(); t0=$SECONDS
for st in "${steps[@]}"; do
  name=${st%%|*}; cmd=${st#*|}; t=$SECONDS
  echo ">> $name: $cmd"
  bash -c "$cmd" > "$log/out.txt" 2>&1; rc=$?
  tail -n 3 "$log/out.txt"
  # the red-team runners exit 0 whatever they find: any class but OK is a failure
  if [[ $name == redteam* ]] && grep -Eq '^[A-Z]+ +[A-Za-z0-9_]+:' "$log/out.txt"; then rc=1; grep -E '^[A-Z]+ +[A-Za-z0-9_]+:' "$log/out.txt" | head -20; fi
  [ $rc -ne 0 ] && { status=1; [[ $name != redteam* ]] && tail -n 30 "$log/out.txt"; }
  summary+=("$(printf '%-18s %-5s %4ds' "$name" "$([ $rc -eq 0 ] && echo ok || echo FAIL)" $((SECONDS - t)))")
done
echo; echo "== $tier gate"; printf '%s\n' "${summary[@]}"
echo "total $((SECONDS - t0))s: $([ $status -eq 0 ] && echo PASS || echo FAIL)"
rm -rf "$log"; exit $status
