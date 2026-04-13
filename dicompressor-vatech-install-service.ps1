<#
.SYNOPSIS
    Interactive NSSM installer for the DicomPressor Vatech Windows service.

.DESCRIPTION
    Prompts for the important paths and watch interval, then creates an auto-start
    hidden Windows service that runs dicompressor-vatech.py in watch mode.

.NOTES
    Run this script from PowerShell as Administrator.
    Requires NSSM: https://nssm.cc/
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

Write-Host "DicomPressor Vatech Windows Service Installer" -ForegroundColor Cyan
Write-Host "This creates a hidden auto-start Windows service using NSSM." -ForegroundColor Cyan
Write-Host ""

$detectedNssmCommand = Get-Command nssm.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$detectedNssm = Get-FirstExistingPath @(
    $(if ($detectedNssmCommand) { $detectedNssmCommand.Source }),
    (Join-Path $scriptDir "nssm.exe"),
    (Join-Path $scriptDir "nssm\nssm.exe"),
    "D:\nssm\nssm.exe",
    "C:\nssm\nssm.exe"
)
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
$nssmPath = Prompt-ExistingFile -Prompt "Path to nssm.exe" -Default $detectedNssm
$pythonPath = Prompt-ExistingFile -Prompt "Path to python.exe" -Default $detectedPython
$sourceDir = Prompt-Directory -Prompt "Source directory to watch" -Default $defaultSource
$outputDir = Prompt-Directory -Prompt "Output directory for merged files" -Default $defaultOutput -CreateIfMissing $true
$logFile = Prompt-FilePath -Prompt "Log file path" -Default $defaultLog

Write-Step "Service"
$serviceName = Prompt-Default -Prompt "Service name" -Default "DicomPressorVatech"
$displayName = Prompt-Default -Prompt "Display name" -Default "DicomPressor Vatech"

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

$appParameters = "`"$pythonScript`" -j --watch $intervalSeconds --log-file `"$logFile`" --output-dir `"$outputDir`" -f `"$sourceDir`""
$serviceLogBase = [System.IO.Path]::Combine(
    [System.IO.Path]::GetDirectoryName($logFile),
    [System.IO.Path]::GetFileNameWithoutExtension($logFile)
)
$stdoutLog = "$serviceLogBase.service-stdout.log"
$stderrLog = "$serviceLogBase.service-stderr.log"
$description = "DicomPressor Vatech watch service. Source=$sourceDir Output=$outputDir Interval=${intervalSeconds}s"

Write-Step "Summary"
Write-Host "NSSM:         $nssmPath"
Write-Host "Python:       $pythonPath"
Write-Host "Script:       $pythonScript"
Write-Host "Source dir:   $sourceDir"
Write-Host "Output dir:   $outputDir"
Write-Host "Log file:     $logFile"
Write-Host "Interval:     ${intervalSeconds}s"
Write-Host "Service name: $serviceName"
Write-Host "Account:      LocalSystem"
Write-Host ""

if (-not (Prompt-YesNo -Prompt "Create or update this Windows service?" -Default $true)) {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Service '$serviceName' already exists. Reinstalling it with the new settings..." -ForegroundColor Yellow
    try {
        if ($existingService.Status -ne "Stopped") {
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }
    catch {
    }
    Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("remove", $serviceName, "confirm")
    Start-Sleep -Seconds 1
}

Write-Step "Installing service"
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("install", $serviceName, $pythonPath)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppDirectory", $scriptDir)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppParameters", $appParameters)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "DisplayName", $displayName)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "Description", $description)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "ObjectName", "LocalSystem")
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "Start", "SERVICE_AUTO_START")
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppExit", "Default", "Restart")
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppStdout", $stdoutLog)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppStderr", $stderrLog)
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppRotateFiles", "1")
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppRotateOnline", "1")
Invoke-CheckedExternal -FilePath $nssmPath -Arguments @("set", $serviceName, "AppRotateBytes", "10485760")

if (Prompt-YesNo -Prompt "Start the service now?" -Default $true) {
    Start-Service -Name $serviceName
}

Write-Step "Done"
Write-Host "Service installed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Get-Service $serviceName"
Write-Host "  Restart-Service $serviceName"
Write-Host "  Stop-Service $serviceName"
Write-Host "  Get-Content `"$logFile`" -Wait"
Write-Host ""
Write-Host "If you need to remove it later:"
Write-Host "  `"$nssmPath`" remove $serviceName confirm"
