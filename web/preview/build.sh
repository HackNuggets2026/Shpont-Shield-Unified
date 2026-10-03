#!/usr/bin/env bash
# Builds the static, read-only preview into web/dist-preview from a recorded snapshot.
#   1. a seeded gateway running (make demo), then: node preview/snapshot.mjs
#   2. bash preview/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."
taken=$(node -e 'console.log(require("./preview/snapshot/meta.json").taken_at)')
VITE_PREVIEW=1 VITE_SNAPSHOT_AT="$taken" SHIELD_BASE=./ npx tsc -b
VITE_PREVIEW=1 VITE_SNAPSHOT_AT="$taken" SHIELD_BASE=./ npx vite build --outDir dist-preview --emptyOutDir
mkdir -p dist-preview/snapshot
cp preview/snapshot/*.json dist-preview/snapshot/
echo "preview built in web/dist-preview (snapshot taken at $taken)"
