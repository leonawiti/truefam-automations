#!/bin/bash
# Wrapper invoked manually or by a scheduler. Activates a Python venv and
# runs the YouTube Shorts poster. Prefers a per-folder .venv, falls back
# to the shared one at the repo root.
set -e

cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "../../.venv" ]; then
    source ../../.venv/bin/activate
fi

python youtube_shorts_poster.py "$@"
