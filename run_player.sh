#!/usr/bin/env bash
set -euo pipefail
PLAYER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$PLAYER_DIR/.venv/bin/python" ]]; then
    echo "请先运行 python3 -m venv .venv 并安装 requirements.txt" >&2
    exit 1
fi
# A ROS shell may inject global Python packages through PYTHONPATH.
exec env -u PYTHONPATH "$PLAYER_DIR/.venv/bin/python" "$PLAYER_DIR/replay_pointcloud.py" "$@"
