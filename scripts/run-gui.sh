#!/usr/bin/env bash
# Запуск графической программы из исходников (Linux/macOS, для разработки).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
exec python3 -m omsreg "$@"
