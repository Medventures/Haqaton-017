[CmdletBinding()]
param(
    [string]$Model = 'qwen/qwen3.5-9b',
    [ValidateRange(4096, 16384)]
    [int]$ContextLength = 16384
)

$ErrorActionPreference = 'Stop'
$lmsCommand = Get-Command lms -ErrorAction SilentlyContinue
if ($lmsCommand) {
    $lmsPath = $lmsCommand.Source
}
else {
    $lmsPath = Join-Path $env:USERPROFILE '.lmstudio/bin/lms.exe'
}
if (-not (Test-Path -LiteralPath $lmsPath -PathType Leaf)) {
    throw 'LM Studio CLI was not found. Install or enable lms before loading the model.'
}

# Keep this alias loaded: it cannot be restored by JIT after TTL expiration.
# The HTTP server is configured separately in LM Studio (TCP 1234).
Write-Warning 'This local LLM profile is experimental: synthetic clinical fidelity checks did not pass. See WINDOWS.md.'
Write-Host "Loading $Model as medhub-llm (context $ContextLength, one parallel request, no TTL)."
& $lmsPath load $Model --identifier medhub-llm --gpu max --context-length $ContextLength --parallel 1 --yes
exit $LASTEXITCODE
