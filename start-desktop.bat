@echo off
REM 军师 Chat —— Windows 桌面版启动器（双击即可）
REM
REM 需要电脑上已装 Python 3.10+。如果不想装 Python，直接用下面那个
REM 打包好的 exe：packaging/windows 里由 GitHub Actions 产出。
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没找到 Python。请先安装 Python 3.10 或更新版本：
  echo   https://www.python.org/downloads/
  echo 安装时记得勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

if not exist "app\static\index.html" (
  echo 前端文件缺失：app\static\index.html 不存在，项目可能没解压完整。
  pause
  exit /b 1
)

echo 正在启动军师 Chat（桌面版）…
python -B main.py --port 8765 --open
if errorlevel 1 pause
