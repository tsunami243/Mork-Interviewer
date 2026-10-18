param(
    [switch]$SkipBuild,
    [int]$Port = 8501,
    [switch]$NoProxy
)

$ErrorActionPreference = 'Stop'

# 项目根：本脚本位于 <root>/scripts/ 下
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# ---------- 定位 Python 解释器 ----------
# 本工作区把 venv 放在项目的上一级（agentLoop/.venv），
# 这里依次尝试，避免再出现“找不到解释器”。
$candidates = @(
    (Join-Path $root '..\.venv\Scripts\python.exe'),   # <root>/../.venv   ← 当前工作区
    (Join-Path $root '.venv\Scripts\python.exe'),      # <root>/.venv      ← 独立部署
    (Join-Path $root '..\..\.venv\Scripts\python.exe') # 再上一层，兜底
)
$python = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
        $python = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if (-not $python) {
    Write-Host '未找到 Python 解释器。请先创建 venv 并安装依赖：' -ForegroundColor Red
    Write-Host '  uv venv ..\.venv'
    Write-Host '  uv pip install --python "..\.venv\Scripts\python.exe" -r requirements.txt'
    exit 1
}
Write-Host "Python: $python" -ForegroundColor DarkGray

# ---------- 绕过本机系统代理 ----------
# Windows 系统代理（如 Clash 的 127.0.0.1:7897）会被 httpx 的 trust_env 读取，
# 导致调用模型时抛 SSL: UNEXPECTED_EOF_WHILE_READING。
if (-not $NoProxy) {
    $env:NO_PROXY = 'api.deepseek.com,dashscope.aliyuncs.com,127.0.0.1,localhost'
    $env:no_proxy = $env:NO_PROXY
}

Set-Location -LiteralPath $root

# ---------- 可选：构建前端 ----------
if (-not $SkipBuild) {
    $frontend = Join-Path $root 'frontend'
    if (Test-Path -LiteralPath $frontend) {
        Push-Location -LiteralPath $frontend
        try {
            if (-not (Test-Path -LiteralPath 'node_modules')) {
                Write-Host '安装前端依赖…' -ForegroundColor Cyan
                & npm.cmd ci --registry=https://registry.npmjs.org
                if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
            }
            Write-Host '构建前端…' -ForegroundColor Cyan
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
        } finally {
            Pop-Location
        }
    }
}

# ---------- 启动服务 ----------
Write-Host ''
Write-Host "启动面试工作台: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host '按 Ctrl+C 停止' -ForegroundColor DarkGray
Write-Host ''
& $python -m uvicorn api_server:app --app-dir src --host 127.0.0.1 --port $Port
