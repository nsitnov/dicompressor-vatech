<#
.SYNOPSIS
    Interactive auto-start installer for the DicomPressor Vatech Windows watcher.

.DESCRIPTION
    Prompts for the important paths and watch interval, then creates a hidden
    auto-start background task via the built-in Windows Task Scheduler.

.NOTES
    Run this script from PowerShell as Administrator.
    No external service wrapper is required.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Prompt-Default {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )

    $suffix = if ($Default) { " [$Default]" } else { "" }
    $answer = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $Default
    }
    return $answer.Trim()
}

function Prompt-YesNo {
    param(
        [string]$Prompt,
        [bool]$Default = $true
    )

    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $answer = Read-Host "$Prompt $hint"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $Default
        }

        switch ($answer.Trim().ToLowerInvariant()) {
            "y" { return $true }
            "yes" { return $true }
            "n" { return $false }
            "no" { return $false }
            default { Write-Host "Please answer y or n." -ForegroundColor Yellow }
        }
    }
}

function Normalize-PathInput {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue.Trim())
    return $expanded.Trim('"')
}

function Get-FirstExistingPath {
    param([string[]]$Candidates)

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return ""
}

function Get-DetectedPythonPath {
    $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyCmd) {
        try {
            $resolved = & $pyCmd.Source -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $resolved = ($resolved | Select-Object -First 1).Trim()
                if ($resolved -and (Test-Path -LiteralPath $resolved -PathType Leaf)) {
                    return (Resolve-Path -LiteralPath $resolved).Path
                }
            }
        }
        catch {
        }
    }

    return Get-FirstExistingPath @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
}

function Prompt-ExistingFile {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )

    while ($true) {
        $value = Normalize-PathInput (Prompt-Default -Prompt $Prompt -Default $Default)
        if (-not $value) {
            Write-Host "A file path is required." -ForegroundColor Yellow
            continue
        }
        if (Test-Path -LiteralPath $value -PathType Leaf) {
            return (Resolve-Path -LiteralPath $value).Path
        }
        Write-Host "File not found: $value" -ForegroundColor Yellow
    }
}

function Prompt-Directory {
    param(
        [string]$Prompt,
        [string]$Default = "",
        [bool]$CreateIfMissing = $false
    )

    while ($true) {
        $value = Normalize-PathInput (Prompt-Default -Prompt $Prompt -Default $Default)
        if (-not $value) {
            Write-Host "A directory path is required." -ForegroundColor Yellow
            continue
        }

        if (Test-Path -LiteralPath $value -PathType Container) {
            return (Resolve-Path -LiteralPath $value).Path
        }

        if ($CreateIfMissing -and (Prompt-YesNo -Prompt "Directory does not exist. Create it?" -Default $true)) {
            New-Item -ItemType Directory -Path $value -Force | Out-Null
            return (Resolve-Path -LiteralPath $value).Path
        }

        Write-Host "Directory not found: $value" -ForegroundColor Yellow
    }
}

function Prompt-FilePath {
    param(
        [string]$Prompt,
        [string]$Default = ""
    )

    while ($true) {
        $value = Normalize-PathInput (Prompt-Default -Prompt $Prompt -Default $Default)
        if (-not $value) {
            Write-Host "A file path is required." -ForegroundColor Yellow
            continue
        }

        $parent = Split-Path -Parent $value
        if (-not $parent) {
            Write-Host "Please enter a full file path." -ForegroundColor Yellow
            continue
        }

        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            if (Prompt-YesNo -Prompt "Parent directory does not exist. Create it?" -Default $true) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
            else {
                continue
            }
        }

        return $value
    }
}

function Invoke-CheckedExternal {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

function Get-ExistingScheduledTask {
    param([string]$TaskName)

    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Register-DicompressorScheduledTask {
    param(
        [string]$TaskName,
        [string]$Description,
        [string]$PythonPath,
        [string]$ScriptDir,
        [string]$AppParameters
    )

    $action = New-ScheduledTaskAction -Execute $PythonPath -Argument $AppParameters -WorkingDirectory $ScriptDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 99 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -Hidden

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $Description `
        -Force | Out-Null
}

if (-not (Test-Administrator)) {
    Write-Host "Run this script from PowerShell as Administrator." -ForegroundColor Red
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir "dicompressor-vatech.py"
$requirementsFile = Join-Path $scriptDir "requirements.txt"

if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
    Write-Host "Cannot find dicompressor-vatech.py in $scriptDir" -ForegroundColor Red
    exit 1
}

Write-Host "DicomPressor Vatech Windows Startup Task Installer" -ForegroundColor Cyan
Write-Host "This creates a hidden auto-start background task using Windows Task Scheduler." -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    Write-Host "Windows Task Scheduler cmdlets are not available on this machine." -ForegroundColor Red
    exit 1
}

$detectedPython = Get-DetectedPythonPath

$defaultSource = if (Test-Path -LiteralPath "D:\VatechDatabase\FMData\Files" -PathType Container) {
    "D:\VatechDatabase\FMData\Files"
} else {
    ""
}
$defaultOutput = "D:\Vatech\Merged"
$defaultLog = if (Test-Path -LiteralPath "D:\Vatech\Logs" -PathType Container) {
    "D:\Vatech\Logs\dicompressor-vatech.log"
} else {
    Join-Path $scriptDir "dicompressor-vatech.log"
}

Write-Step "Paths"
$pythonPath = Prompt-ExistingFile -Prompt "Path to python.exe" -Default $detectedPython
$sourceDir = Prompt-Directory -Prompt "Source directory to watch" -Default $defaultSource
$outputDir = Prompt-Directory -Prompt "Output directory for merged files" -Default $defaultOutput -CreateIfMissing $true
$logFile = Prompt-FilePath -Prompt "Log file path" -Default $defaultLog

Write-Step "Task"
$taskName = Prompt-Default -Prompt "Task name" -Default "DicomPressorVatech"

$parsedInterval = 0
while ($true) {
    $intervalRaw = Prompt-Default -Prompt "Watch interval in seconds" -Default "300"
    if ([int]::TryParse($intervalRaw, [ref]$parsedInterval) -and $parsedInterval -gt 0) {
        $intervalSeconds = $parsedInterval
        break
    }
    Write-Host "Enter a positive number of seconds." -ForegroundColor Yellow
}

Write-Step "Python dependencies"
$depCheck = & $pythonPath -c "import pydicom, numpy, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf)) {
        Write-Host "requirements.txt not found in $scriptDir" -ForegroundColor Red
        exit 1
    }

    if (Prompt-YesNo -Prompt "Required Python packages are missing. Install them now?" -Default $true) {
        Invoke-CheckedExternal -FilePath $pythonPath -Arguments @("-m", "pip", "install", "-r", $requirementsFile)
    }
    else {
        Write-Host "Install the Python requirements first and run the installer again." -ForegroundColor Red
        exit 1
    }
}

$serviceLogBase = [System.IO.Path]::Combine(
    [System.IO.Path]::GetDirectoryName($logFile),
    [System.IO.Path]::GetFileNameWithoutExtension($logFile)
)
$scanStateFile = "$serviceLogBase.scan-state.json"
$appParameters = "`"$pythonScript`" -j --watch $intervalSeconds --log-file `"$logFile`" --scan-state-file `"$scanStateFile`" --output-dir `"$outputDir`" -f `"$sourceDir`""
$description = "DicomPressor Vatech watch task. Source=$sourceDir Output=$outputDir Interval=${intervalSeconds}s"

Write-Step "Summary"
Write-Host "Python:          $pythonPath"
Write-Host "Script:          $pythonScript"
Write-Host "Source dir:      $sourceDir"
Write-Host "Output dir:      $outputDir"
Write-Host "Log file:        $logFile"
Write-Host "Scan state file: $scanStateFile"
Write-Host "Interval:        ${intervalSeconds}s"
Write-Host "Task name:       $taskName"
Write-Host "Task account:    SYSTEM"
Write-Host ""

if (-not (Prompt-YesNo -Prompt "Create or update this startup task?" -Default $true)) {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

$existingTask = Get-ExistingScheduledTask -TaskName $taskName
if ($existingTask) {
    Write-Host "Task '$taskName' already exists. Re-registering it with the new settings..." -ForegroundColor Yellow
    try {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    catch {
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Start-Sleep -Seconds 1
}

Write-Step "Registering scheduled task"
Register-DicompressorScheduledTask `
    -TaskName $taskName `
    -Description $description `
    -PythonPath $pythonPath `
    -ScriptDir $scriptDir `
    -AppParameters $appParameters

if (Prompt-YesNo -Prompt "Start the task now?" -Default $true) {
    Start-ScheduledTask -TaskName $taskName
}

Write-Step "Done"
Write-Host "Scheduled task installed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Get-ScheduledTask -TaskName $taskName"
Write-Host "  Get-ScheduledTaskInfo -TaskName $taskName"
Write-Host "  Start-ScheduledTask -TaskName $taskName"
Write-Host "  Stop-ScheduledTask -TaskName $taskName"
Write-Host "  Get-Content `"$logFile`" -Wait"
Write-Host ""
Write-Host "If you need to remove it later:"
Write-Host "  Unregister-ScheduledTask -TaskName `"$taskName`" -Confirm:`$false"
