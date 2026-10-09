#!/bin/sh

cd `dirname $0`

VENV_NAME="venv"
PYTHON="$VENV_NAME/bin/python"

# The venv persists across cloud builds via a cache mount; create only when missing.
if [ ! -x "$PYTHON" ]; then
    python3 -m venv "$VENV_NAME" || { echo "This module requires Python >=3.8 with venv." >&2; exit 1; }
fi

# Reinstall only when requirements.txt changes; the hash marker lives inside venv/.
REQ_HASH=$( (sha256sum requirements.txt 2>/dev/null || shasum -a 256 requirements.txt) | awk '{print $1}')
if [ -z "$REQ_HASH" ] || [ "$(cat "$VENV_NAME/.installed" 2>/dev/null)" != "$REQ_HASH" ]; then
    # Wheelhouse first (cloud build image), PyPI fallback.
    "$PYTHON" -m pip install --no-index -r requirements.txt -q 2>/dev/null \
        || "$PYTHON" -m pip install -r requirements.txt -q || exit 1
    echo "$REQ_HASH" > "$VENV_NAME/.installed"
fi
