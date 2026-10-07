param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = (Resolve-Path (Join-Path $root '..\.venv\Scripts\python.exe')).Path
Set-Location -LiteralPath $root
if (-not $SkipBuild) {
    Push-Location -LiteralPath (Join-Path $root 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci --registry=https://registry.npmjs.org
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed.' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
& $python -m uvicorn api_server:app --app-dir src --host 127.0.0.1 --port 8501
