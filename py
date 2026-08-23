#!/usr/bin/env bash
set -euo pipefail

# Harness console/protocol text is UTF-8. This avoids legacy Windows
# ANSI/charmap stdout under Git Bash.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if [[ -n "${SLIVIN_HARNESS_PYTHON:-}" ]]; then
  exec "$SLIVIN_HARNESS_PYTHON" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$@"
fi

if command -v py >/dev/null 2>&1; then
  exec py -3 "$@"
fi

echo "Slivin Harness requires Python 3.11+. Set SLIVIN_HARNESS_PYTHON or put Python on PATH." >&2
exit 127
