#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT_DIR/frontend"
npm ci
npm run build

cd "$ROOT_DIR/backend"
mvn -DskipTests package

echo "Build complete: backend/target/library-server.jar"

