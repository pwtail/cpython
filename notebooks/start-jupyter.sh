#!/usr/bin/env bash
# Jupyter server for PyCharm (Settings -> Languages & Frameworks -> Jupyter ->
# Configured Server).  The server itself only schedules work; the kernel
# "funnypy" runs the fork's interpreter from ~/.local/share/funnypy/venv.
#
# Any Jupyter server will do: the notebook's work happens in the kernel, not in
# the server process.  The kernel "funnypy" always runs the fork's interpreter.
set -euo pipefail

PORT="${PORT:-8888}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.local/share/funnypy/jupyter-token}"

mkdir -p "$(dirname "$TOKEN_FILE")"
if [ ! -s "$TOKEN_FILE" ]; then
    python3 -c 'import secrets; print(secrets.token_hex(16))' > "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

echo "Server : http://127.0.0.1:${PORT}"
echo "Token  : ${TOKEN}"
echo "Root   : ${ROOT}"
echo "Kernel : funnypy (Funny Python 3.15)"

exec python3 -m jupyterlab \
    --no-browser \
    --ip=127.0.0.1 \
    --port="$PORT" \
    --ServerApp.token="$TOKEN" \
    --ServerApp.root_dir="$ROOT"
