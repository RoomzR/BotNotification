#!/usr/bin/env bash
cd "$(dirname "$0")"
if [[ -x .venv_mac/bin/python ]]; then
  exec .venv_mac/bin/python run_desktop.py
elif [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python run_desktop.py
else
  exec python3 run_desktop.py
fi
