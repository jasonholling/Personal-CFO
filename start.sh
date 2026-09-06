#!/bin/bash
# Resolve the repo directory from this script's location, so it works
# no matter where the repo lives.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR/backend"

if [ ! -d "venv" ]; then
  echo "Setting up Python environment..."
  # Prefer Apple's bundled Python 3.9 over whatever `python3` resolves to
  # (e.g. a newer Homebrew install) — pydantic==2.7.0 has no prebuilt wheel
  # for very new Python versions and fails building from source (needs a
  # Rust toolchain and hits a pyo3/Python-3.14 incompatibility as of 2026).
  PY_BIN="python3"
  if [ -x /usr/bin/python3 ]; then PY_BIN=/usr/bin/python3; fi
  "$PY_BIN" -m venv venv
fi

echo "Checking dependencies..."
venv/bin/pip install -q -r requirements.txt

echo "Starting backend..."
venv/bin/python3 -m uvicorn main:app \
  --host 127.0.0.1 \
  --port 8000 &
BACKEND_PID=$!

cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "Starting frontend..."
npx vite &
FRONTEND_PID=$!

sleep 3
open http://localhost:5173
echo ""
echo "Personal CFO running at http://localhost:5173"
echo "Press Ctrl+C to stop."

cleanup() { kill $BACKEND_PID 2>/dev/null; kill $FRONTEND_PID 2>/dev/null; exit 0; }
trap cleanup INT TERM
wait
