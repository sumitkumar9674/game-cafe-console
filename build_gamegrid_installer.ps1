# Build a branded Windows GameGrid installer using the existing working
# PyInstaller build script and Inno Setup 6. No application shutdown or data
# deletion is performed here.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$buildScript = Join-Path $root 'build_game_cafe.ps1'
$installerSpec = Join-Path $root 'gamegrid_setup.iss'
$exe = Join-Path $root 'dist\GameCafeConsole\GameCafeConsole.exe'
$internal = Join-Path $root 'dist\GameCafeConsole\_internal'
$logo = Join-Path $root 'game_cafe\assets\branding\app_logo.png'
$reference = Join-Path $root 'installer\assets\gamegrid_logo_reference.png'
$ico = Join-Path $root 'installer\assets\gamegrid.ico'
$outFile = Join-Path $root 'release\GameGrid-Setup-1.0.0.exe'

try {
    foreach ($file in @($buildScript, $installerSpec, $logo, $reference, $ico)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Required file missing: $file"
        }
    }

    $hashOriginal = (Get-FileHash -LiteralPath $reference -Algorithm SHA256).Hash
    $hashPackaged = (Get-FileHash -LiteralPath $logo -Algorithm SHA256).Hash
    if ($hashOriginal -ne $hashPackaged) {
        throw 'The project app_logo.png does not match the approved GameGrid logo. Verify the correct image before packaging.'
    }

    Write-Host 'Checking GameGrid is not running...' -ForegroundColor Cyan
    $running = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -ieq 'GameCafeConsole.exe' -or
        ($_.CommandLine -and $_.CommandLine -match 'run_game_cafe\.py')
    })
    if ($running.Count -gt 0) {
        $running | Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-Table -AutoSize
        throw 'GameGrid is running. End sessions and close software normally on the Default desktop before building.'
    }

    $compiler = $null
    $cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($cmd) { $compiler = $cmd.Source }
    if (-not $compiler) {
        $locations = @()
        foreach ($dir in @([Environment]::GetFolderPath('ProgramFilesX86'), [Environment]::GetFolderPath('ProgramFiles'))) {
            if ($dir) { $locations += (Join-Path $dir 'Inno Setup 6\ISCC.exe') }
        }
        foreach ($path in $locations) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $compiler = $path
                break
            }
        }
    }
    if (-not $compiler) {
        throw 'Inno Setup 6 compiler ISCC.exe was not found. Install it from https://jrsoftware.org/isinfo.php, then run this script again.'
    }

    Push-Location -LiteralPath $root
    try {
        Write-Host 'Building current GameGrid source with existing PyInstaller script...' -ForegroundColor Cyan
        $buildStart = (Get-Date).ToUniversalTime()
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $buildScript
        if ($LASTEXITCODE -ne 0) { throw "Existing app build failed ($LASTEXITCODE)." }
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Packaged GameCafeConsole.exe missing.' }
        if (-not (Test-Path -LiteralPath $internal -PathType Container)) { throw 'Packaged _internal folder missing.' }
        if ((Get-Item $exe).LastWriteTimeUtc -lt $buildStart.AddSeconds(-10)) {
            throw 'EXE may be stale. Refusing to make installer from an old build.'
        }

        Write-Host 'Compiling branded GameGrid setup installer...' -ForegroundColor Cyan
        $existingInstaller = Test-Path -LiteralPath $outFile -PathType Leaf
        if ($existingInstaller) {
            Remove-Item -LiteralPath $outFile -Force
        }
        & $compiler $installerSpec
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)." }
        if (-not (Test-Path -LiteralPath $outFile -PathType Leaf)) {
            throw 'Compiler returned successfully, but expected GameGrid Setup EXE was not found.'
        }
    }
    finally { Pop-Location }

    $output = Get-Item -LiteralPath $outFile
    Write-Host ''
    Write-Host 'SUCCESS: GameGrid Windows installer created.' -ForegroundColor Green
    Write-Host "Installer: $($output.FullName)" -ForegroundColor Green
    Write-Host "Size (MB): $([Math]::Round($output.Length / 1MB, 1))"
    Write-Host 'Copy ONLY this Setup EXE to each Windows PC, close prior instances, then run it.'
    Write-Host 'Existing %LOCALAPPDATA%\GameCafeConsole identity and SQLite data are not packaged or deleted.'
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
