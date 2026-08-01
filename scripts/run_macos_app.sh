#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"

export MATH_HARNESS_PROJECT_ROOT="$PROJECT_ROOT"
exec swift run \
    --package-path "$PROJECT_ROOT/macos/MathHarnessApp" \
    MathHarnessApp
