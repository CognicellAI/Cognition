#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${EXAMPLE_DIR}/runtime"
DIST_DIR="${EXAMPLE_DIR}/dist"
ZIP_PATH="${DIST_DIR}/cognition-lambda-microvm-runtime.zip"

if ! command -v zip >/dev/null 2>&1; then
  echo "zip is required to package the Lambda MicroVM runtime artifact." >&2
  exit 1
fi

mkdir -p "${DIST_DIR}"
rm -f "${ZIP_PATH}"

(
  cd "${RUNTIME_DIR}"
  zip -X -r "${ZIP_PATH}" Dockerfile server.py README.md
)

echo "Wrote ${ZIP_PATH}"
