[CmdletBinding()]
param(
    [string]$EnvFile
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptDirectory)) {
    $scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $scriptDirectory '.env'
}
$repoRoot = [IO.Path]::GetFullPath((Join-Path $scriptDirectory '../../..'))
$pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
$torchLibPath = Join-Path $repoRoot '.venv/Lib/site-packages/torch/lib'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'The repository .venv is missing. Create it and install the ASR dependencies first.'
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw 'The ASR environment file is missing. Configure deploy/gpu/windows/.env first.'
}

# Read plain KEY=value data; never execute the environment file or print its contents.
$env:BIND_IP = '127.0.0.1'
$env:ASR_DEVICE = 'cuda'
$env:ASR_COMPUTE_TYPE = 'int8_float16'
$lineNumber = 0
foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
    $lineNumber++
    $entry = $line.Trim()
    if (-not $entry -or $entry.StartsWith('#')) { continue }
    if ($entry -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        throw "Invalid environment entry on line $lineNumber. Expected KEY=value."
    }
    $entryName = $Matches[1]
    $entryValue = $Matches[2].Trim()
    if ($entryValue.Length -ge 2) {
        $firstChar = $entryValue[0]
        $lastChar = $entryValue[$entryValue.Length - 1]
        if (($firstChar -eq '"' -and $lastChar -eq '"') -or
            ($firstChar -eq "'" -and $lastChar -eq "'")) {
            $entryValue = $entryValue.Substring(1, $entryValue.Length - 2)
        }
    }
    [Environment]::SetEnvironmentVariable($entryName, $entryValue, 'Process')
}

# This process is the GPU endpoint, not the medhub client of that endpoint.
$env:ASR_PROVIDER = 'faster_whisper'
$env:HF_HUB_OFFLINE = '1'
$env:PYANNOTE_METRICS_ENABLED = '0'
$env:PYTHONUNBUFFERED = '1'
if ([string]::IsNullOrWhiteSpace($env:ASR_SERVICE_TOKEN) -or $env:ASR_SERVICE_TOKEN.Length -lt 32) {
    throw 'ASR_SERVICE_TOKEN must contain at least 32 random characters.'
}
foreach ($modelKey in @('ASR_MODEL', 'DIARIZATION_MODEL')) {
    $modelPath = [Environment]::GetEnvironmentVariable($modelKey, 'Process')
    if ([string]::IsNullOrWhiteSpace($modelPath)) {
        throw "$modelKey must point to a local model directory."
    }
    if (-not (Test-Path -LiteralPath $modelPath -PathType Container)) {
        Write-Warning "$modelKey is missing on disk. /health can respond, but transcription will fail until both models are installed."
    }
}
if (-not (Test-Path -LiteralPath $torchLibPath -PathType Container)) {
    throw 'PyTorch DLL directory is missing. Install the CUDA-enabled PyTorch package in .venv.'
}
$env:PATH = $torchLibPath + [IO.Path]::PathSeparator + $env:PATH

Write-Host 'Starting the private ASR process on TCP 8090. Model readiness requires a successful /transcribe request.'
Push-Location (Join-Path $repoRoot 'backend')
try {
    & $pythonPath -m uvicorn app.asr_service:app --host $env:BIND_IP --port 8090 --workers 1 --no-access-log
    $runtimeExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $runtimeExitCode
