#!/usr/bin/env bash
# test-harness.sh — build the isolated Regmon-RE test image and run the tests.
# Wipe-and-repeat: each run builds a fresh container, tests in total isolation,
# and discards it. Nothing touches Terry's box.
#
# Usage: ./test/test-harness.sh [--build]   (--build forces a rebuild)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
IMG="regmon-re-test"

echo "==> Regmon-RE isolated test harness"
echo ""

# Build (or reuse) the image.
if [ "${1:-}" = "--build" ] || ! podman image exists "$IMG" 2>/dev/null; then
    echo "-- Building test image ($IMG) --"
    podman build -t "$IMG" -f "$HERE/test/Containerfile" "$HERE" 2>&1 | tail -4
else
    echo "-- Reusing existing image ($IMG); use --build to rebuild --"
fi

echo ""
echo "-- Running isolated tests (container discarded after) --"
# Bind-mount the repo into the container; run the test script.
# OPENROUTER_API_KEY is passed through from the environment (optional) so the
# AI-reachability test can hit the free tier without baking a secret into the
# image.
podman run --rm \
    -v "$HERE:/test-src:ro" \
    -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
    "$IMG" /test-isolated.sh

echo ""
echo "==> Test run complete. Container discarded — clean state for next run. =="
