#Requires -Version 5.1
<#
.SYNOPSIS
    Installs Viddl for the current user on Windows.

.DESCRIPTION
    Creates a private virtual environment, installs Viddl into it, writes a
    viddl.cmd launcher under %LOCALAPPDATA%\viddl\bin, adds that folder to your
    user PATH, and registers the viddl:// link handler so the browser extension
    can open the app. Nothing else on the machine is touched, and nothing needs
    Administrator rights.

.PARAMETER Python
    A Python 3.9+ program to build the environment with. Default: py -3, then
    the python on PATH.

.PARAMETER NoPath
    Do not add %LOCALAPPDATA%\viddl\bin to your user PATH.

.PARAMETER NoProtocol
    Do not register the viddl:// link handler.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    .\install.ps1 -NoPath -NoProtocol
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$NoPath,
    [switch]$NoProtocol
)

$ErrorActionPreference = 'Stop'

function Write-Say {
    param([string]$Message)
    Write-Host $Message
}

function Stop-Install {
    param([string]$Message)
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

if ($env:OS -ne 'Windows_NT') {
    Stop-Install "This installer is for Windows. On Linux or macOS run ./install.sh."
}

$SourceDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$DataDir = if ($env:VIDDL_HOME) { $env:VIDDL_HOME } else { Join-Path $LocalAppData 'viddl' }
$Venv = Join-Path $DataDir 'venv'
$BinDir = Join-Path $DataDir 'bin'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Launcher = Join-Path $BinDir 'viddl.cmd'

# --- Python -----------------------------------------------------------------

$PythonExe = $null
$PythonArgs = @()
if ($Python) {
    $PythonExe = $Python
} else {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $PythonExe = $py.Source
        $PythonArgs = @('-3')
    }
    if (-not $PythonExe) {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python) { $PythonExe = $python.Source }
    }
}
if (-not $PythonExe) {
    Stop-Install "Viddl needs Python 3.9 or newer. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH', then run this again."
}

$versionArgs = @($PythonArgs) + @('-c', 'import platform; print(platform.python_version())')
$version = (& $PythonExe @versionArgs).Trim()
$checkArgs = @($PythonArgs) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)')
& $PythonExe @checkArgs 2>$null
if ($LASTEXITCODE -ne 0) {
    Stop-Install "Viddl needs Python 3.9 or newer, and $PythonExe is $version. Install a newer one and run this again."
}
Write-Say "Using $PythonExe ($version)"

# --- install ----------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

if (-not (Test-Path $VenvPython)) {
    Write-Say "Creating a private environment in $Venv"
    $venvArgs = @($PythonArgs) + @('-m', 'venv', $Venv)
    & $PythonExe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
        Stop-Install "Could not create the environment in $Venv."
    }
} else {
    Write-Say "Reusing the environment in $Venv"
}

Write-Say "Installing Viddl..."
# Updating pip is a nicety: a network hiccup there must not stop the install.
& $VenvPython -m pip install --quiet --upgrade pip 2>$null
& $VenvPython -m pip install --quiet $SourceDir
if ($LASTEXITCODE -ne 0) {
    Stop-Install "pip could not install Viddl. Check your internet connection and try again."
}

$Shim = Join-Path $SourceDir 'viddl.cmd'
if (-not (Test-Path $Shim)) {
    Stop-Install "viddl.cmd is missing from $SourceDir. Run this installer from the folder you downloaded."
}
Copy-Item -LiteralPath $Shim -Destination $Launcher -Force
Write-Say "Wrote $Launcher"

# --- PATH -------------------------------------------------------------------

if (-not $NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ })
    $ours = $false
    foreach ($part in $parts) {
        if ($part.TrimEnd('\') -ieq $BinDir.TrimEnd('\')) { $ours = $true }
    }
    if (-not $ours) {
        $newPath = (@($parts) + $BinDir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Say "Added $BinDir to your PATH (new terminals only)."
    } else {
        Write-Say "$BinDir is already on your PATH."
    }
    $env:Path = "$BinDir;$env:Path"
}

# --- browser hand-off -------------------------------------------------------

if (-not $NoProtocol) {
    Write-Say ""
    Write-Say "Registering the viddl:// link handler (so the extension can open Viddl)..."
    & $Launcher --register-protocol
    if ($LASTEXITCODE -ne 0) {
        Write-Say "That step needs to be done by hand - the command is printed above."
    }
}

# --- done -------------------------------------------------------------------

Write-Say ""
Write-Say "Viddl is installed."
Write-Say "  1. Open a new terminal window (so it finds the new command)."
Write-Say "  2. Run:      viddl"
Write-Say "  3. Paste any video link and pick a quality."
Write-Say ""
Write-Say "Windows Terminal gives the full-screen app its colours and characters."
Write-Say "An older console can use the plain, ASCII interface instead:"
Write-Say "    viddl --plain --quality 720 <link>"
Write-Say ""
Write-Say "The first download fetches yt-dlp into $DataDir\bin (about 3 MB)."
Write-Say "ffmpeg is only fetched if you pick MP3 or a quality that needs merging."
Write-Say "Check on everything at any time with:  viddl --doctor"
