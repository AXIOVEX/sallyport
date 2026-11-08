#!/usr/bin/env bash
# Spec 036 section 4 coverage gate: measure per-crate line
# coverage of the Rust workspace with the LLVM tools and
# take the verdict from syncyard-tracegen's coverage mode
# (bars: contract 95, daemon 90, cli 85, agent 85, tray 80).
#
# The measurement flow is the manual LLVM pipeline (the
# same one the P7 baseline used): build the tests with
# coverage instrumentation into a separate target dir, run
# them, merge the profiles, export JSON, verdict. No GitHub,
# no network, no billing surface (spec 036 section 7).
#
# Usage: rust/coverage.sh [export-json-out]
# Exit: the tracegen verdict's exit code.
set -u
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"

ROOT="$(pwd)"
WORK="${SYNCYARD_COV_WORK:-$ROOT/../covwork}"
TARGET="$ROOT/../target-cov"
OUT="${1:-$WORK/coverage-export.json}"
mkdir -p "$WORK" "$TARGET"

LLVM_BIN="$(rustc --print sysroot)/lib/rustlib/x86_64-unknown-linux-gnu/bin"
if [ ! -x "$LLVM_BIN/llvm-profdata" ]; then
  echo "coverage: llvm-tools-preview component is required (rustup component add llvm-tools-preview)" >&2
  exit 2
fi

export LLVM_PROFILE_FILE="$WORK/prof-%p-%m.profraw"
export CARGO_TARGET_DIR="$TARGET"
export RUSTFLAGS="-C instrument-coverage"
export LLVM_PROFILE_FILE

echo "== building instrumented tests =="
cargo test --workspace --no-run || exit 2
rm -f "$WORK"/*.profdata
echo "== running instrumented tests =="
cargo test --workspace > "$WORK/test-run.log" 2>&1 || { echo "coverage: test run failed, see $WORK/test-run.log" >&2; exit 1; }

"$LLVM_BIN/llvm-profdata" merge -sparse "$WORK"/*.profraw -o "$WORK/merged.profdata" || exit 2
OBJECTS=$(find "$TARGET/debug/deps" -maxdepth 1 -type f -executable ! -name "*.so" | sed 's/^/-object /' | tr '\n' ' ')
# shellcheck disable=SC2086
"$LLVM_BIN/llvm-cov" export $OBJECTS -instr-profile "$WORK/merged.profdata" -format text > "$OUT" || exit 2
echo "== verdict (bars: contract 95, daemon 90, cli 85, agent 85, tray 80) =="
# The verdict runs uninstrumented, against the normal target dir.
unset RUSTFLAGS CARGO_TARGET_DIR LLVM_PROFILE_FILE
cargo run -q -p syncyard-tracegen -- coverage --report "$OUT"
