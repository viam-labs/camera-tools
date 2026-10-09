#!/bin/sh

cd `dirname $0`

VENV_NAME="venv"
PYTHON="$VENV_NAME/bin/python"

if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
    "$PYTHON" -m pip install --no-index pyinstaller -q 2>/dev/null || "$PYTHON" -m pip install pyinstaller -q || exit 1
fi

"$PYTHON" -m PyInstaller --onedir --noconfirm --collect-all viam --collect-all av src/main.py
tar -czf dist/archive.tar.gz meta.json ./dist/main
echo "Archive size: $(du -h dist/archive.tar.gz | cut -f1)"
