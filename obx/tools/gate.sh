#!/usr/bin/env bash
# usage: obx/tools/gate.sh quick|full
# quick: ref + conformance + unit tests, library tests, self-hosted compiler comparison.
# full:  quick, then the red-team harnesses (rounds 1-4) under ASan+UBSan.
# Builds are cached (tools/cbuild.py; OBX_NOCACHE=1 disables, OBX_CACHE sets the directory).
set -u
tier=${1:-}
case "$tier" in quick|full) ;; *) echo "usage: $0 quick|full" >&2; exit 2;; esac
R=$(cd "$(dirname "$0")/../.." && pwd)
cd "$R"
steps=(
  "ref+conformance|python3 obx/ref/run_tests.py --conformance"
  "lib|python3 obx/lib/run_tests.py -j 8"
  "compiler compare|python3 obx/compiler/run_compare.py --mode both"
)
if [ "$tier" = full ]; then
  steps+=(
    "redteam round 1|python3 obx/redteam/rt.py"
    "redteam round 2|python3 obx/redteam/round2/rt.py"
    "redteam round 3|python3 obx/redteam/round3/rt.py"
    "redteam round 4|python3 obx/redteam/round4/rt.py"
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
