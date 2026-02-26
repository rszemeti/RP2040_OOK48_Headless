$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $repoRoot '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path $python)) {
    throw "Python not found at $python. Activate/create .venv first."
}
if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller not found at $pyinstaller. Activate/create .venv and install with: pip install pyinstaller"
}

& $python scripts\make_icon.py

$iconPath = Join-Path $repoRoot 'assets\ook48.ico'
$versionPath = Join-Path $repoRoot 'assets\windows_version.txt'

if (-not (Test-Path $iconPath)) {
    throw "Icon file not found at $iconPath"
}
if (-not (Test-Path $versionPath)) {
    throw "Version info file not found at $versionPath"
}

& $pyinstaller --noconfirm --clean --onefile --windowed --name OOK48_GUI --icon $iconPath --version-file $versionPath gui\ook48_gui.py

Write-Host "Build complete: $repoRoot\dist\OOK48_GUI.exe"
