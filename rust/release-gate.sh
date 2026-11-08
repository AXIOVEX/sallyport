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
./rust/target/debug/syncyard-tracegen || { echo "GATE RED at step 4 (tracegen)"; exit 1; }
step "5 platform lint"
./rust/target/debug/syncyard-tracegen lint-platform --suite rust || { echo "GATE RED at step 5 (platform lint)"; exit 1; }
step "6 coverage"
rust/coverage.sh || { echo "GATE RED at step 6 (coverage)"; exit 1; }
echo
echo "GATE GREEN: all steps passed"
