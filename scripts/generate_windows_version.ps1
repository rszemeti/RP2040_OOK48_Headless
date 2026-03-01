param(
    [string]$OutFile = 'assets/windows_version.txt',
    [string]$Version
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Get-GitOutput {
    param([string[]]$GitArgs)
    $output = & git @GitArgs 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($output | Out-String).Trim()
}

function Parse-VersionTuple {
    param([string]$RawVersion)
    if ([string]::IsNullOrWhiteSpace($RawVersion)) {
        return $null
    }

    $match = [regex]::Match($RawVersion.Trim(), '^[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?$')
    if (-not $match.Success) {
        return $null
    }

    return @(
        [int]$match.Groups[1].Value,
        [int]$match.Groups[2].Value,
        [int]($match.Groups[3].Value -as [int]),
        [int]($match.Groups[4].Value -as [int])
    )
}

$baseVersion = $Version
if ([string]::IsNullOrWhiteSpace($baseVersion)) {
    $baseVersion = Get-GitOutput -GitArgs @('describe', '--tags', '--abbrev=0')
}

$tuple = Parse-VersionTuple -RawVersion $baseVersion
if ($null -eq $tuple) {
    $tuple = @(1, 0, 0, 0)
}

$commitCount = Get-GitOutput -GitArgs @('rev-list', '--count', 'HEAD')
if ($commitCount -match '^\d+$') {
    $tuple[3] = [Math]::Min([int]$commitCount, 65535)
}

$fileVersion = '{0}.{1}.{2}.{3}' -f $tuple[0], $tuple[1], $tuple[2], $tuple[3]
$shortSha = Get-GitOutput -GitArgs @('rev-parse', '--short', 'HEAD')
$buildDate = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')

$copyright = 'Colin Durbridge (G4EML), Robin Szemeti (G1YFG)'
if ($shortSha) {
    $copyright = "$copyright | commit $shortSha"
}

$content = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($tuple[0]), $($tuple[1]), $($tuple[2]), $($tuple[3])),
    prodvers=($($tuple[0]), $($tuple[1]), $($tuple[2]), $($tuple[3])),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'OOK48 Project Contributors'),
        StringStruct('FileDescription', 'OOK48 Serial Control GUI'),
        StringStruct('FileVersion', '$fileVersion'),
        StringStruct('InternalName', 'OOK48_GUI'),
        StringStruct('LegalCopyright', '$copyright'),
        StringStruct('OriginalFilename', 'OOK48_GUI.exe'),
        StringStruct('ProductName', 'OOK48 Headless GUI'),
        StringStruct('ProductVersion', '$fileVersion'),
        StringStruct('Comments', 'Auto-generated on $buildDate UTC')])
      ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@

$resolvedOutFile = Join-Path $repoRoot $OutFile
$outDir = Split-Path -Parent $resolvedOutFile
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

Set-Content -Path $resolvedOutFile -Encoding UTF8 -Value $content
Write-Host "Generated $resolvedOutFile with version $fileVersion"