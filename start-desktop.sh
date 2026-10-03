#!/usr/bin/env bash
# 军师 Chat —— 桌面版启动器
#
# 桌面版 = 本机浏览器里的宽屏三栏界面。这个脚本只做三件事：
#   找到 Python、确认前端文件在、把服务起来并自动打开浏览器。
# 不装任何东西：整个项目只用 Python 标准库。
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "找不到 Python 3。请先安装 Python 3.10 或更新版本。" >&2
  exit 1
fi

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 版本太低（需要 3.10+）。当前：$("$PY" -V 2>&1)" >&2
  exit 1
fi

if [ ! -f app/static/index.html ]; then
  echo "前端文件缺失：app/static/index.html 不存在，项目可能没解压完整。" >&2
  exit 1
fi

PORT="${JUNSHI_PORT:-8765}"
HOST="${JUNSHI_HOST:-127.0.0.1}"

echo "正在启动军师 Chat（桌面版）…"
exec "$PY" -B main.py --host "$HOST" --port "$PORT" --open "$@"
