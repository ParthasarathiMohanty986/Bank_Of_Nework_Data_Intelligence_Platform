$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $python = if (Test-Path '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
    if (-not $env:SETTLEMENT_API_TOKEN) {
        $env:SETTLEMENT_API_TOKEN = & $python -c "import secrets; print(secrets.token_urlsafe(32))"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host 'Dashboard: http://127.0.0.1:8000'
    Write-Host "Operations token: $env:SETTLEMENT_API_TOKEN"
    & $python -m uvicorn settlement.api:app --host 127.0.0.1 --port 8000 --no-access-log
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
