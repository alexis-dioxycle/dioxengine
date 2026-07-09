#!/bin/bash
# dioxengine local dev — boots backend (uvicorn :5006) + frontend (vite :3001)
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

if [ ! -f "package.json" ] || [ ! -d "backend" ]; then
  echo -e "${RED}Run this from the project root.${NC}"; exit 1
fi

if [ ! -d "backend/venv" ]; then
  echo -e "${YELLOW}Creating Python venv…${NC}"
  python3 -m venv backend/venv
fi
backend/venv/bin/pip install -q -r backend/requirements.txt

if [ ! -f "backend/.env" ] && [ -f ".env.example" ]; then
  echo -e "${YELLOW}Creating backend/.env from .env.example${NC}"
  cp .env.example backend/.env
fi

if [ ! -d "node_modules" ]; then
  echo -e "${YELLOW}Installing frontend deps…${NC}"
  npm install
fi

cleanup() { kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true; exit 0; }
trap cleanup SIGINT SIGTERM

(cd backend && ./venv/bin/uvicorn main:app --reload --port 5006) &
BACKEND_PID=$!
sleep 2
npm run dev &
FRONTEND_PID=$!

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "  Frontend: ${YELLOW}http://localhost:3001${NC}"
echo -e "  Backend:  ${YELLOW}http://localhost:5006${NC} (API docs: /docs)"
echo -e "${GREEN}============================================${NC}"
echo "Ctrl+C to stop."
wait
