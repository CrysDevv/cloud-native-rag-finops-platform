# Uso: powershell -ExecutionPolicy Bypass -File .\setup.ps1 [opciones de setup.sh]
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$candidatos = @(
    (Join-Path $env:ProgramFiles "Git\bin\bash.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe")
)
$bash = $candidatos | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $bash) {
    Write-Error "No se encontró Git Bash. Instale Git for Windows: https://git-scm.com/download/win"
    exit 1
}

# Garantiza saltos de línea LF en setup.sh aunque Git haya convertido el archivo a CRLF.
$ruta = Join-Path $PSScriptRoot "setup.sh"
$texto = [System.IO.File]::ReadAllText($ruta)
if ($texto.Contains("`r")) {
    [System.IO.File]::WriteAllText($ruta, $texto.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

& $bash ./setup.sh @args
exit $LASTEXITCODE
