#!/usr/bin/env bash
# The release gate composition (spec 036 section 5): the
# ordered checks a release candidate must pass, run locally
# end to end. Local-only by construction — no GitHub calls,
# no network, no billing surface (spec 036 section 7): when
# hosted CI is unavailable or billing-blocked, this script
# IS the gate, and its log is the verdict record.
#
# Order matters: format and lint first (cheap), then the
# full test suite, then traceability (the matrix must be
# green or carry only its recorded field-gated list), then
# coverage against the section 4 bars, then the platform
# lint. The first red step stops the gate and names itself.
#
# Usage: rust/release-gate.sh [--dry-run]
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"

STEPS=(
  "cargo fmt --check (rust/)"
  "cargo clippy --workspace -- -D warnings (rust/)"
  "cargo test --workspace (rust/)"
  "syncyard-tracegen matrix (specs vs suite)"
  "syncyard-tracegen lint-platform"
  "coverage verdict (rust/coverage.sh)"
)

if [ "${1:-}" = "--dry-run" ]; then
  echo "release gate composition (in order):"
  for i in "${!STEPS[@]}"; do echo "  step $((i+1)): ${STEPS[$i]}"; done
  exit 0
fi

step() { echo; echo "=== GATE STEP: $1 ==="; }

step "1 fmt"
(cd rust && cargo fmt --check) || { echo "GATE RED at step 1 (fmt)"; exit 1; }
step "2 clippy"
(cd rust && cargo clippy --workspace -- -D warnings) || { echo "GATE RED at step 2 (clippy)"; exit 1; }
step "3 test"
(cd rust && cargo test --workspace) || { echo "GATE RED at step 3 (test)"; exit 1; }
step "4 tracegen"
# The matrix may carry gaps ONLY in the named-test-missing class,
# and the missing set must be exactly the recorded field-gated
# exception list (rust/traceability/field-gated-exceptions.txt).
# Any other gap — a new missing test, an FR without a test or a
# mode, an orphan suite id — is red. An empty missing set is
# also red here by the recorded rule: if the field-gated tests
# ever land in the suite, the exception list must be retired in
# the same change, not silently outlived.
EXCEPTIONS_FILE="rust/traceability/field-gated-exceptions.txt"
TRACEGEN_TMP="$(mktemp -d)"
TRACEGEN_LOG="$TRACEGEN_TMP/tracegen.log"
./rust/target/debug/syncyard-tracegen --out-dir "$TRACEGEN_TMP/out" >"$TRACEGEN_LOG" 2>&1
TRACEGEN_STATUS=$?
cat "$TRACEGEN_LOG"
if [ "$TRACEGEN_STATUS" -ne 0 ] && [ "$TRACEGEN_STATUS" -ne 1 ]; then
  echo "GATE RED at step 4 (tracegen exited $TRACEGEN_STATUS)"
  rm -rf "$TRACEGEN_TMP"
  exit 1
fi
if ! grep -q "fr_without_test=0 fr_without_mode=0" "$TRACEGEN_LOG" \
   || ! grep -q "orphan_suite_ids=0" "$TRACEGEN_LOG"; then
  echo "GATE RED at step 4 (tracegen: a non-exception gap class is present)"
  rm -rf "$TRACEGEN_TMP"
  exit 1
fi
ACTUAL_MISSING="$(python3 - "$TRACEGEN_TMP/out/matrix.json" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    matrix = json.load(fh)
missing = sorted(
    t["id"] for fr in matrix["frs"] for t in fr["tests"] if not t["in_suite"]
)
print("\n".join(missing))
PY
)"
EXPECTED_MISSING="$(grep -E '^T[0-9]{3}-[0-9]{2}$' "$EXCEPTIONS_FILE" | sort)"
if [ "$ACTUAL_MISSING" != "$EXPECTED_MISSING" ]; then
  echo "GATE RED at step 4 (tracegen: missing set is not exactly the recorded field-gated list)"
  echo "expected (from $EXCEPTIONS_FILE):"
  echo "$EXPECTED_MISSING"
  echo "actual (from the regenerated matrix):"
  echo "$ACTUAL_MISSING"
  rm -rf "$TRACEGEN_TMP"
  exit 1
fi
rm -rf "$TRACEGEN_TMP"
echo "GATE STEP 4 PASS: tracegen gaps are exactly the recorded field-gated exceptions"
step "5 platform lint"
./rust/target/debug/syncyard-tracegen lint-platform --suite rust || { echo "GATE RED at step 5 (platform lint)"; exit 1; }
step "6 coverage"
rust/coverage.sh || { echo "GATE RED at step 6 (coverage)"; exit 1; }
echo
echo "GATE GREEN: all steps passed"
