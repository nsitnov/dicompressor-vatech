<#
.SYNOPSIS
    DicomPressor Vatech - PowerShell Wrapper

.DESCRIPTION
    PowerShell wrapper for the dedicated Vatech merge workflow.
    Handles folders of DICOM slices and Vatech DCM_FILE.CT archives.
#>

param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Continue"

$pythonCmd = $null
$pythonCandidates = @("python3", "python", "py")

foreach ($candidate in $pythonCandidates) {
    try {
        $version = & $candidate --version 2>&1
        if ($version -match "Python 3") {
            $pythonCmd = $candidate
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3 is required but not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3 from https://python.org" -ForegroundColor Yellow
    Write-Host "On Windows: winget install Python.Python.3.12" -ForegroundColor Cyan
    exit 1
}

$null = & $pythonCmd -c "import pydicom" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing required packages..." -ForegroundColor Yellow
    & $pythonCmd -m pip install pydicom numpy Pillow --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install required packages." -ForegroundColor Red
        Write-Host "Please run: $pythonCmd -m pip install pydicom numpy Pillow" -ForegroundColor Yellow
        exit 1
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "dicompressor-vatech.py"

if (-not (Test-Path $pythonScript)) {
    Write-Host "ERROR: Cannot find dicompressor-vatech.py in $scriptDir" -ForegroundColor Red
    exit 1
}

if ($Arguments) {
    & $pythonCmd $pythonScript @Arguments
} else {
    & $pythonCmd $pythonScript
}

exit $LASTEXITCODE
