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

    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dicompressor = Join-Path $ScriptDir "dicompressor-vatech.py"
$Marker = ".dicompressor_vatech_done"

if (-not (Test-Path $WatchDir -PathType Container)) {
    Write-Error "Directory not found: $WatchDir"
    exit 1
}

if (-not (Test-Path $Dicompressor -PathType Leaf)) {
    Write-Error "dicompressor-vatech.py not found at: $Dicompressor"
    exit 1
}

if ($OutputDir -ne "" -and -not (Test-Path $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " DicomPressor Vatech Watch Mode" -ForegroundColor Cyan
Write-Host " Watching:    $WatchDir" -ForegroundColor Cyan
Write-Host " Interval:    ${IntervalSeconds}s" -ForegroundColor Cyan
Write-Host " Marker:      $Marker" -ForegroundColor Cyan
if ($OutputDir -ne "") {
Write-Host " Output dir:  $OutputDir" -ForegroundColor Cyan
}
Write-Host " Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

while ($true) {
    $NewCount = 0
    $DoneCount = 0
    $EmptyCount = 0

    $subdirs = Get-ChildItem -Path $WatchDir -Directory
    foreach ($dir in $subdirs) {
        $markerPath = Join-Path $dir.FullName $Marker
        if (Test-Path $markerPath) {
            $DoneCount++
            continue
        }

        $matchCount = @(Get-ChildItem -Path $dir.FullName -Recurse -File | Where-Object {
            $_.Extension -match '^\.(dcm|ct)$' -or $_.Name -match '\.ct\.dcm$'
        }).Count
        if ($matchCount -eq 0) {
            $EmptyCount++
            continue
        }

        $NewCount++
        $timestamp = Get-Date -Format "HH:mm:ss"
        Write-Host ""
        Write-Host "[$timestamp] NEW: $($dir.Name) ($matchCount matching files)" -ForegroundColor Green

        try {
            $cmdArgs = @("-j", "--skip-if-done")
            if ($OutputDir -ne "") {
                $cmdArgs += @("--output-dir", $OutputDir)
            }
            $cmdArgs += @("-f", $dir.FullName)
            $output = & python $Dicompressor @cmdArgs 2>&1
            $output | ForEach-Object { Write-Host "  $_" }
            Write-Host "  Done!" -ForegroundColor Green
        }
        catch {
            Write-Host "  FAILED: $_" -ForegroundColor Red
        }
    }

    $Total = $NewCount + $DoneCount + $EmptyCount
    $timestamp = Get-Date -Format "HH:mm:ss"
    if ($NewCount -eq 0) {
        Write-Host "`r[$timestamp] $Total folders ($DoneCount done, $EmptyCount empty). Next scan in ${IntervalSeconds}s..." -NoNewline
    }
    else {
        Write-Host ""
        Write-Host "[$timestamp] Processed $NewCount new folder(s). Total: $Total ($DoneCount done)" -ForegroundColor Yellow
    }

    Start-Sleep -Seconds $IntervalSeconds
}
