param(
    [Parameter(Mandatory = $true)]
    [string]$Uf2Path,

    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

function Get-RpiRp2Drive {
    $disk = Get-CimInstance Win32_LogicalDisk | Where-Object { $_.VolumeName -eq 'RPI-RP2' } | Select-Object -First 1
    if ($null -eq $disk) {
        return $null
    }
    return $disk.DeviceID + '\\'
}

$resolvedUf2 = Resolve-Path $Uf2Path -ErrorAction Stop

if ([System.IO.Path]::GetExtension($resolvedUf2.Path).ToLowerInvariant() -ne '.uf2') {
    throw "Input file must be a .uf2 file: $($resolvedUf2.Path)"
}

Write-Host "Firmware file: $($resolvedUf2.Path)"
Write-Host ""
Write-Host "1) Hold BOOTSEL on the RP2040 board"
Write-Host "2) Plug in USB (or press RESET while holding BOOTSEL)"
Write-Host "3) Wait for RPI-RP2 USB drive"
Write-Host ""
Write-Host "Waiting up to $TimeoutSeconds seconds for RPI-RP2..."

$driveRoot = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $driveRoot = Get-RpiRp2Drive
    if ($driveRoot) { break }
    Start-Sleep -Milliseconds 500
}

if (-not $driveRoot) {
    throw "RPI-RP2 drive not detected within timeout."
}

Write-Host "Found target drive: $driveRoot"
$targetPath = Join-Path $driveRoot ([System.IO.Path]::GetFileName($resolvedUf2.Path))
Copy-Item -Path $resolvedUf2.Path -Destination $targetPath -Force

Write-Host "Firmware copied to $targetPath"
Write-Host "Board should reboot automatically into new firmware."
