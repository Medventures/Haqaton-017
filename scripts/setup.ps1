param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envPath) { Write-Output '.env уже существует; настройки сохранены.'; exit 0 }
function New-Secret([int]$length = 32) {
    $bytes = New-Object byte[] $length
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}
$dbPassword = (New-Secret).TrimEnd('=')
$cipherKey = New-Secret
$blindIndexKey = New-Secret
$invite = (New-Secret 18).TrimEnd('=')
$template = Get-Content -LiteralPath (Join-Path $projectRoot '.env.example') -Raw
$template = $template.Replace('replace-with-random-password', $dbPassword)
$template = $template.Replace('ENCRYPTION_KEY=', "ENCRYPTION_KEY=$cipherKey")
$template = $template.Replace('INDEX_KEY=', "INDEX_KEY=$blindIndexKey")
$template = $template.Replace('REGISTRATION_CODE=', "REGISTRATION_CODE=$invite")
if ($Demo) {
    $template = $template.Replace('DEMO_MODE=false', 'DEMO_MODE=true')
    $template = $template.Replace('DEMO_PASSWORD=', "DEMO_PASSWORD=$(New-Secret 24)")
}
[IO.File]::WriteAllText($envPath, $template, [Text.UTF8Encoding]::new($false))
Write-Output '.env создан со случайными ключами. Секреты не выводятся.'
