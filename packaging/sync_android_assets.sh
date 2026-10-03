#!/usr/bin/env bash
# 把主项目里的 Python 引擎和前端资源同步进 Android 工程。
#
# 为什么需要这一步：Gradle 的 sourceSets 只能引用工程目录内的路径，而主项目在
# 仓库根目录。与其在 Gradle 里配一堆跨目录的 srcDir（容易在 CI 上出路径问题），
# 不如在构建前做一次明确的复制——出错时日志也更好看。
#
# 本地和 GitHub Actions 都用这一个脚本，保证两边行为一致。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ANDROID="$ROOT/packaging/android"
PY_DST="$ANDROID/app/src/main/python/junshi"
STATIC_DST="$ANDROID/app/src/main/python/app/static"

echo "同步 Python 引擎 → $PY_DST"
rm -rf "$PY_DST"
mkdir -p "$PY_DST"

# 只带运行需要的包：core（引擎）、kb（人设+知识库）、memory（档案）、
# trend（趋势）、server（本地服务）、paths 和 __init__。
# tests 是给开发用的，不进 APK。
for item in __init__.py paths.py core kb memory trend server; do
  src="$ROOT/junshi/$item"
  if [ ! -e "$src" ]; then
    echo "缺少 $src" >&2
    exit 1
  fi
  cp -r "$src" "$PY_DST/"
done

# Chaquopy 不会把 .pyc/缓存带进去，但本地跑过之后可能留下，清掉更干净
find "$PY_DST" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -f "$PY_DST/kb/.index.json"        # 索引在手机上首次使用时自动重建

echo "同步前端资源 → $STATIC_DST"
rm -rf "$ANDROID/app/src/main/python/app"
mkdir -p "$STATIC_DST"
cp -r "$ROOT/app/static/." "$STATIC_DST/"

# Android 专属模块（不来自主项目，放在 py_support 里等着被复制）
cp "$ANDROID/py_support/junshi/android_server.py" "$PY_DST/android_server.py"

# 自检：关键文件一个都不能少，缺了就直接失败，别等到 APK 装到手机上才发现
for must in \
  "$PY_DST/core/engine.py" \
  "$PY_DST/kb/SKILL.md" \
  "$PY_DST/kb/references/knowledge/03-依恋理论与情绪调节.md" \
  "$PY_DST/memory/profile.py" \
  "$PY_DST/server/app.py" \
  "$PY_DST/android_server.py" \
  "$ANDROID/app/src/main/python/junshi/kb/references/practical/00-导读与使用分级.md" \
  "$STATIC_DST/index.html" \
  "$STATIC_DST/css/app.css" \
  "$STATIC_DST/js/app.js" ; do
  if [ ! -f "$must" ]; then
    echo "同步后仍缺少：$must" >&2
    exit 1
  fi
done

kb_count=$(find "$PY_DST/kb/references" -name "*.md" | wc -l | tr -d ' ')
echo "完成：知识库 $kb_count 份文档，前端 $(find "$STATIC_DST" -type f | wc -l | tr -d ' ') 个文件"
if [ "$kb_count" -lt 40 ]; then
  echo "知识库文档数异常（$kb_count），中止" >&2
  exit 1
fi
