#!/usr/bin/env bash
# Start the DesignLab web app.
#   ./run.sh            → http://127.0.0.1:8090
#   PORT=9000 ./run.sh  → http://127.0.0.1:9000
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8090}"
HOST="${HOST:-127.0.0.1}"
# The project venv is preferred: the base conda env carries a starlette version
# FastAPI cannot use, and downgrading it there would affect other tools.
if [[ -x "$PWD/.venv/bin/python" ]]; then
  PYTHON="${PYTHON:-$PWD/.venv/bin/python}"
else
  PYTHON="${PYTHON:-$(command -v python3)}"
fi

for tool in mafft primer3_core blastn makeblastdb; do
  command -v "$tool" >/dev/null 2>&1 || echo "WARNING: $tool not on PATH"
done

export PYTHONPATH="$PWD/backend"
echo "DesignLab → http://${HOST}:${PORT}"
exec "$PYTHON" -m uvicorn main:app --app-dir backend --host "$HOST" --port "$PORT" "$@"
