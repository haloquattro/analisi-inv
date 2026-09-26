#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
fail() { printf '\nErrore: %s\n' "$1"; read -r -p "Premi Invio per chiudere…" _; exit 1; }
trap 'fail "Avvio interrotto. Leggi il messaggio qui sopra."' ERR
# Riusa prima il venv locale, poi cerca un Python compatibile anche fuori dal PATH del Finder.
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  radar_python=""
  for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
      radar_python="$candidate"
      break
    fi
  done
  [ -n "$radar_python" ] || fail "Installa Python 3.10 o successivo da https://www.python.org/downloads/macos/ e riapri questo file."
  "$radar_python" -m venv .venv
fi
# Usa direttamente il Python del venv: funziona anche dopo aver spostato la cartella.
radar_venv_python="$PWD/.venv/bin/python"
if [ -f .env ]; then
  # Only supported KEY=VALUE settings; never execute the configuration as shell code.
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in
      COINGECKO_API_KEY|RADAR_DATA_DIR|SCAN_INTERVAL_SECONDS) export "$key=$value" ;;
    esac
  done < .env
fi
# Installa nuovamente solo se cambiano i requisiti o manca una libreria necessaria.
requirements_hash="$(shasum -a 256 requirements.txt | awk '{print $1}')"
if [ ! -f .venv/requirements.sha256 ] || [ "$(cat .venv/requirements.sha256)" != "$requirements_hash" ] || ! "$radar_venv_python" -c 'import fastapi, uvicorn, yfinance, pandas, sqlalchemy, requests' >/dev/null 2>&1; then
  "$radar_venv_python" -m pip install --upgrade pip
  "$radar_venv_python" -m pip install -r requirements.txt
  printf '%s' "$requirements_hash" > .venv/requirements.sha256
fi
# Un secondo doppio clic apre il server esistente senza creare un processo duplicato.
if curl -fsS --max-time 2 http://localhost:8000/api/health 2>/dev/null | "$radar_venv_python" -c 'import sys,json; assert json.load(sys.stdin).get("app") == "analisi-inv"' 2>/dev/null; then
  open http://localhost:8000 || printf "Apri il browser su http://localhost:8000\n"
  exit 0
fi
"$radar_venv_python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",8000)); s.close()' || fail "La porta 8000 è occupata da un’altra applicazione. Chiudila e riprova."
"$radar_venv_python" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
# Il launcher mantiene il server attivo e lo arresta quando termina.
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
trap 'exit 0' INT TERM
for attempt in {1..60}; do
  if curl -fsS --max-time 1 http://localhost:8000/api/health >/dev/null 2>&1; then
    open http://localhost:8000 || printf "Apri il browser su http://localhost:8000\n"
    printf '\nanalisi inv attivo. Lascia aperta questa finestra. Premi Ctrl+C per fermare il server.\n'
    wait "$server_pid"
    exit 0
  fi
  kill -0 "$server_pid" 2>/dev/null || fail "Il server non si è avviato."
  sleep 1
done
fail "Il server non risponde entro 60 secondi."
