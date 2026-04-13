<#
.SYNOPSIS
    DicomPressor Vatech Watch Script (Windows PowerShell)

.DESCRIPTION
    Watches a parent folder for new subfolders that contain either normal
    DICOM slices or Vatech DCM_FILE.CT archives.
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$WatchDir,

    [int]$IntervalSeconds = 300,

    [string]$OutputDir = "",

    [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Wrapper = Join-Path $ScriptDir "dicompressor-vatech.ps1"

if (-not (Test-Path $WatchDir -PathType Container)) {
    Write-Error "Directory not found: $WatchDir"
    exit 1
}

if (-not (Test-Path $Wrapper -PathType Leaf)) {
    Write-Error "dicompressor-vatech.ps1 not found at: $Wrapper"
    exit 1
}

if ($OutputDir -ne "" -and -not (Test-Path $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

if ($LogFile -eq "") {
    $LogFile = Join-Path $ScriptDir "dicompressor-vatech.log"
}

$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir -PathType Container)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " DicomPressor Vatech Watch Mode" -ForegroundColor Cyan
Write-Host " Watching:    $WatchDir" -ForegroundColor Cyan
Write-Host " Interval:    ${IntervalSeconds}s" -ForegroundColor Cyan
if ($OutputDir -ne "") {
    Write-Host " Output dir:  $OutputDir" -ForegroundColor Cyan
}
Write-Host " Log file:    $LogFile" -ForegroundColor Cyan
Write-Host " Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

$cmdArgs = @("-j", "--watch", $IntervalSeconds, "--log-file", $LogFile, "-f", $WatchDir)
if ($OutputDir -ne "") {
    $cmdArgs += @("--output-dir", $OutputDir)
}

& $Wrapper @cmdArgs
exit $LASTEXITCODE
