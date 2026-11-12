#!/usr/bin/env bash
# Compatibility wrapper. The split BMI30 project is versioned as a whole system now.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/switch_bmi30_split_versions.sh" "$@"
