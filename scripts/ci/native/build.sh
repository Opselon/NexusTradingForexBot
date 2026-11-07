#!/usr/bin/env bash
# Build + selftest + (optional) sanitizer + fuzz for the NSE native CI engine.
# Usage: build.sh [--sanitize] [--fuzz]
set -euo pipefail
cd "$(dirname "$0")"

BUILD_DIR="build"
SANITIZE=0
FUZZ=0
for arg in "$@"; do
  case "$arg" in
    --sanitize) SANITIZE=1 ;;
    --fuzz) FUZZ=1 ;;
  esac
done

if [ "$SANITIZE" = "1" ]; then
  cmake -B build-san -DNSE_SANITIZE=ON >/dev/null
  cmake --build build-san -j"$(nproc)"
  ./build-san/engine-tests-san
  ./build-san/nse-ci-engine-san selftest
  if [ "$FUZZ" = "1" ]; then
    ./build-san/engine-tests-san --fuzz --iterations 20000
  fi
  echo "sanitizer build: CLEAN"
else
  if command -v ninja >/dev/null 2>&1; then
    cmake -B "$BUILD_DIR" -G Ninja >/dev/null
  else
    cmake -B "$BUILD_DIR" >/dev/null
  fi
  cmake --build "$BUILD_DIR" -j"$(nproc)"
  ./build/nse-ci-engine selftest
  echo "release build: OK"
fi
