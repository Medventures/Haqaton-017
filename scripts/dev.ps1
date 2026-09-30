$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & "$PSScriptRoot/setup.ps1"
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose не запущен. Проверьте Docker Desktop.' }
    Write-Output 'medhub: http://localhost:5173 ; API: http://localhost:5173/api/docs'
} finally { Pop-Location }
