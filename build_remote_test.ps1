param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$sourceFile = Join-Path $projectRoot "experiments\remote_console_lab.py"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not $PythonExecutable) {
    if (Test-Path -LiteralPath $venvPython) {
        $PythonExecutable = $venvPython
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Pass its path with -PythonExecutable."
        }
        $PythonExecutable = $pythonCommand.Source
    }
}

if (-not (Test-Path -LiteralPath $sourceFile)) {
    throw "Source file not found: $sourceFile"
}

Write-Host "Checking PyInstaller with: $PythonExecutable"
& $PythonExecutable -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw @"
PyInstaller is not available for this Python interpreter.
Install the development-only build dependency yourself, then rerun this script:

    & '$PythonExecutable' -m pip install pyinstaller
"@
}

Write-Host "Building console-enabled one-file executable..."
Push-Location $projectRoot
try {
    & $PythonExecutable -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name GameCafeRemoteTest `
        $sourceFile
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$outputFile = Join-Path $projectRoot "dist\GameCafeRemoteTest.exe"
if (-not (Test-Path -LiteralPath $outputFile)) {
    throw "Build finished without the expected output: $outputFile"
}

Write-Host "Build complete: $outputFile"
