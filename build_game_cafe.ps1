param([string]$PythonExecutable = "")

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not $PythonExecutable) {
    if (Test-Path -LiteralPath $venvPython) {
        $PythonExecutable = $venvPython
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python not found. Pass -PythonExecutable with a Python path."
        }
        $PythonExecutable = $pythonCommand.Source
    }
}

& $PythonExecutable -c "import cryptography"
if ($LASTEXITCODE -ne 0) {
    throw "cryptography is required. Install development/runtime packages with: & '$PythonExecutable' -m pip install -r requirements.txt"
}
& $PythonExecutable -c "import PySide6.QtQuick; import PySide6.QtQuickControls2; import PySide6.QtWidgets"
if ($LASTEXITCODE -ne 0) {
    throw "PySide6 Qt Quick is required. Install requirements.txt into the build environment."
}
& $PythonExecutable -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required for building. Install it explicitly with: & '$PythonExecutable' -m pip install pyinstaller"
}

Push-Location $projectRoot
try {
    & $PythonExecutable -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --add-data "game_cafe\qml;game_cafe\qml" `
        --hidden-import PySide6.QtQuickControls2 `
        --name GameCafeConsole `
        "run_game_cafe.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$outputFile = Join-Path $projectRoot "dist\GameCafeConsole\GameCafeConsole.exe"
if (-not (Test-Path -LiteralPath $outputFile)) {
    throw "Expected executable not found: $outputFile"
}
Write-Host "Built: $outputFile"
