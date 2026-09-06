#!/bin/bash
# Run this once to (re)create the Desktop launcher on your Mac.
# Detects where this repo actually lives and bakes that path into the launcher,
# so it keeps working even if you move the repo.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cat > ~/Desktop/Start\ Personal\ CFO.command << LAUNCHER
#!/bin/bash
cd "$REPO_DIR/backend"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
  echo "Setting up Python environment (first run only)..."
  python3 -m venv venv
fi

# Install backend deps if needed
venv/bin/pip install -q -r requirements.txt

# Start backend
venv/bin/python3 main.py &
BACKEND_PID=\$!
echo "Backend started..."

# Start frontend
cd "$REPO_DIR/frontend"

if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies (first run only)..."
  npm install
fi

npx vite &
FRONTEND_PID=\$!
echo "Frontend started..."

sleep 3
open http://localhost:5173

echo ""
echo "Personal CFO is running at http://localhost:5173"
echo "Close this window to stop."
echo ""

cleanup() {
  kill \$BACKEND_PID 2>/dev/null
  kill \$FRONTEND_PID 2>/dev/null
  exit 0
}
trap cleanup INT TERM

wait
LAUNCHER

chmod +x ~/Desktop/Start\ Personal\ CFO.command
echo "Launcher fixed. Double-click 'Start Personal CFO' on your Desktop."
