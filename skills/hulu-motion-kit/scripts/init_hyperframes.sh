#!/usr/bin/env bash
set -euo pipefail

project_dir=${1:?Usage: init_hyperframes.sh <project-dir>}
command -v npx >/dev/null || { echo "Node.js and npx are required." >&2; exit 1; }

npx --yes hyperframes@0.7.64 init "$project_dir"
