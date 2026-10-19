#Requires -Version 5.1
<#
.SYNOPSIS
    Removes Viddl from the current user's account on Windows.

.DESCRIPTION
    Unregisters the viddl:// handler, removes the viddl.cmd launcher and the
    folder it came from, and takes %LOCALAPPDATA%\viddl\bin off your user PATH.
    The yt-dlp and ffmpeg helpers and the log are kept unless you pass -Purge.

.PARAMETER Purge
    Remove everything, including the helpers and the log.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\uninstall.ps1

.EXAMPLE
    .\uninstall.ps1 -Purge
#>
[CmdletBinding()]
param(
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

function Write-Say {
    param([string]$Message)
    Write-Host $Message
}

if ($env:OS -ne 'Windows_NT') {
    Write-Host "error: This uninstaller is for Windows. On Linux or macOS run ./uninstall.sh." -ForegroundColor Red
    exit 1
}

$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$DataDir = if ($env:VIDDL_HOME) { $env:VIDDL_HOME } else { Join-Path $LocalAppData 'viddl' }
$Venv = Join-Path $DataDir 'venv'
$BinDir = Join-Path $DataDir 'bin'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Launcher = Join-Path $BinDir 'viddl.cmd'
$ProtocolLauncher = Join-Path $DataDir 'viddl-open.cmd'

if (Test-Path $VenvPython) {
    & $VenvPython -m viddl --unregister-protocol 2>$null | Out-Null
}

if (Test-Path $Launcher) {
    Remove-Item -LiteralPath $Launcher -Force
    Write-Say "Removed $Launcher"
}

if (Test-Path $ProtocolLauncher) {
    Remove-Item -LiteralPath $ProtocolLauncher -Force
    Write-Say "Removed $ProtocolLauncher"
}

if (Test-Path $Venv) {
    Remove-Item -LiteralPath $Venv -Recurse -Force
    Write-Say "Removed $Venv"
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($userPath -split ';' | Where-Object { $_ })
$kept = @($parts | Where-Object { $_.TrimEnd('\') -ine $BinDir.TrimEnd('\') })
if ($kept.Count -ne $parts.Count) {
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    Write-Say "Took $BinDir off your PATH."
}

if ($Purge) {
    if (Test-Path $DataDir) {
        Remove-Item -LiteralPath $DataDir -Recurse -Force
        Write-Say "Removed $DataDir (yt-dlp, ffmpeg and logs)"
    }
} else {
    Write-Say ""
    Write-Say "Kept $DataDir (yt-dlp, ffmpeg and logs)."
    Write-Say "Remove that folder too with:  .\uninstall.ps1 -Purge"
}

Write-Say ""
Write-Say "The browser extension is managed by the browser: remove it from"
Write-Say "chrome://extensions if you loaded it."
