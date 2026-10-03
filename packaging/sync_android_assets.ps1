# 把主项目里的 Python 引擎和前端资源同步进 Android 工程（Windows / PowerShell 版）。
#
# 和 sync_android_assets.sh 做同一件事，只是给 Windows 上用。
# 为什么需要这一步：Gradle 的 sourceSets 只能引用工程目录内的路径，而主项目在仓库根目录。
# 构建前复制一次，比在 Gradle 里配跨目录 srcDir 更不容易在 CI 上出路径问题。
#
# 用法：powershell -ExecutionPolicy Bypass -File packaging\sync_android_assets.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Android = Join-Path $Root "packaging\android"
$PyDst = Join-Path $Android "app\src\main\python\junshi"
$StaticDst = Join-Path $Android "app\src\main\python\app\static"

Write-Host "同步 Python 引擎 → $PyDst"
if (Test-Path $PyDst) { Remove-Item -Recurse -Force $PyDst }
New-Item -ItemType Directory -Force -Path $PyDst | Out-Null

# 只带运行需要的包；tests 是开发用的，不进 APK
foreach ($item in @("__init__.py", "paths.py", "core", "kb", "memory", "trend", "server")) {
    $src = Join-Path $Root "junshi\$item"
    if (-not (Test-Path $src)) { throw "缺少 $src" }
    Copy-Item -Recurse -Force $src $PyDst
}

# 清掉本地跑测试留下的缓存与知识库索引（索引会在手机上首次使用时重建）
Get-ChildItem -Path $PyDst -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $PyDst "kb\.index.json") -ErrorAction SilentlyContinue

Write-Host "同步前端资源 → $StaticDst"
$AppDst = Join-Path $Android "app\src\main\python\app"
if (Test-Path $AppDst) { Remove-Item -Recurse -Force $AppDst }
New-Item -ItemType Directory -Force -Path $StaticDst | Out-Null
Copy-Item -Recurse -Force (Join-Path $Root "app\static\*") $StaticDst

# Android 专属模块
Copy-Item -Force (Join-Path $Android "py_support\junshi\android_server.py") `
    (Join-Path $PyDst "android_server.py")

# 自检：关键文件一个都不能少
$must = @(
    (Join-Path $PyDst "core\engine.py"),
    (Join-Path $PyDst "kb\SKILL.md"),
    (Join-Path $PyDst "memory\profile.py"),
    (Join-Path $PyDst "server\app.py"),
    (Join-Path $PyDst "android_server.py"),
    (Join-Path $StaticDst "index.html"),
    (Join-Path $StaticDst "css\app.css"),
    (Join-Path $StaticDst "js\app.js")
)
foreach ($f in $must) {
    if (-not (Test-Path $f)) { throw "同步后仍缺少：$f" }
}

$kbCount = (Get-ChildItem -Path (Join-Path $PyDst "kb\references") -Recurse -Filter "*.md").Count
Write-Host "完成：知识库 $kbCount 份文档"
if ($kbCount -lt 40) { throw "知识库文档数异常（$kbCount），中止" }
