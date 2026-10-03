#!/usr/bin/env bash
# 军师 Chat —— 移动版启动器
#
# 移动版 = 手机浏览器里的单列 + 底部标签界面（同一套服务，同一套接口，
# 靠 CSS 媒体查询切换布局，所以不存在两个分叉的版本）。
#
# 两种用法：
#   1) 直接在手机上跑（推荐，数据不出设备）：
#        ./start-mobile.sh
#      然后手机浏览器打开打印出来的地址。
#   2) 在电脑上跑、手机访问：
#        ./start-mobile.sh --lan
#      脚本会监听 0.0.0.0 并打印局域网地址，手机连同一个 Wi-Fi 打开。
#      注意：这会把界面暴露在局域网里，别人也能打开——用完记得停掉。
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

if [ ! -f app/static/index.html ]; then
  echo "前端文件缺失：app/static/index.html 不存在，项目可能没解压完整。" >&2
  exit 1
fi

HOST="127.0.0.1"
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --lan) HOST="0.0.0.0" ;;
    *) EXTRA+=("$arg") ;;
  esac
done
PORT="${JUNSHI_PORT:-8765}"

if [ "$HOST" = "0.0.0.0" ]; then
  echo "注意：将以局域网模式启动，同一网络下的其他设备也能访问界面。"
fi
echo "正在启动军师 Chat（移动版）…"
exec "$PY" -B main.py --host "$HOST" --port "$PORT" --mobile "${EXTRA[@]+"${EXTRA[@]}"}"
