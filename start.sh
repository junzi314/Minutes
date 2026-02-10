#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export LD_LIBRARY_PATH="$(python3 -c 'import nvidia.cublas, nvidia.cudnn; print(nvidia.cublas.__path__[0] + "/lib:" + nvidia.cudnn.__path__[0] + "/lib")")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec python3 "$SCRIPT_DIR/bot.py"
