#!/bin/bash
# dioxengine local dev — backend (uvicorn :8000, SQLite) + frontend (vite :3001).
# The vite dev server plays the portal's role: it signs the X-Dioxycle-*
# identity headers with DIOXYCLE_AUTH_SECRET (default test-secret).
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

if [ ! -f "manifest.yaml" ] || [ ! -d "backend" ]; then
  echo -e "${RED}Run this from the project root.${NC}"; exit 1
fi

if [ ! -d "backend/venv" ]; then
  echo -e "${YELLOW}Creating Python venv…${NC}"
  python3 -m venv backend/venv
fi
backend/venv/bin/pip install -q -r backend/requirements.txt

if [ ! -d "node_modules" ]; then
  echo -e "${YELLOW}Installing dev frontend deps (panel mirror)…${NC}"
  npm install
fi

cleanup() { kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true; exit 0; }
trap cleanup SIGINT SIGTERM

(cd backend && DIOXYCLE_AUTH_SECRET=${DIOXYCLE_AUTH_SECRET:-test-secret} \
  ./venv/bin/uvicorn main:app --reload --port 8000) &
BACKEND_PID=$!
sleep 2
npm run dev &
FRONTEND_PID=$!

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "  Frontend: ${YELLOW}http://localhost:3001${NC}"
echo -e "  Backend:  ${YELLOW}http://localhost:8000${NC} (API docs: /docs)"
echo -e "${GREEN}============================================${NC}"
echo "Ctrl+C to stop."
wait
