#!/bin/bash
# Wrapper invoked by launchd. Activates a Python venv and runs the health
# check. Prefers a per-folder .venv, falls back to the shared one.
set -e

cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "../../.venv" ]; then
    source ../../.venv/bin/activate
fi

python health_check.py "$@"
