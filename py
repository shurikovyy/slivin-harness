#!/usr/bin/env bash
set -euo pipefail
PYTHON="${SLIVIN_HARNESS_PYTHON:-$HOME/Documents/sa_icover/.venv/Scripts/python.exe}"
exec "$PYTHON" "$@"
