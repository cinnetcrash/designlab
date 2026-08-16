<#
.SYNOPSIS
    Install DesignLab on Windows.

.DESCRIPTION
    Creates the project virtual environment, installs the Python packages,
    checks for MAFFT / Primer3 / BLAST+ and runs the offline tests.

    With -WithTools it also downloads the portable builds of MAFFT and Primer3
    into .\tools\ and adds them to PATH for the current session. Nothing is
    installed system-wide and no installer is executed: the two archives are
    unpacked and used in place, so removing .\tools\ undoes it completely.
    BLAST+ is not downloaded automatically -- NCBI ships it as an installer, and
    running a downloaded installer is your decision, not this script's.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -WithTools

.NOTES
    NOT TESTED ON WINDOWS. Written on Linux, mirroring install.sh. Please report
    what breaks: https://github.com/cinnetcrash/designlab/issues

    Execution policy, if PowerShell refuses to run this:
        powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
    [switch]$WithTools,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Write-Ok   { param($m) Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  [!]    $m" -ForegroundColor Yellow }
function Write-Bad  { param($m) Write-Host "  [fail] $m" -ForegroundColor Red }
function Write-Head { param($m) Write-Host "`n$m" -ForegroundColor Cyan }

Write-Host 'DesignLab installer (Windows)' -ForegroundColor Cyan
Write-Host "  project: $PSScriptRoot"

# --- 1. Python ------------------------------------------------------------
Write-Head '1/3  Python environment'

if (-not $Python) {
    foreach ($candidate in 'python', 'py') {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) { $Python = $found.Source; break }
    }
}
if (-not $Python) {
    Write-Bad 'No Python found. Install Python 3.11+ from python.org or the Microsoft Store,'
    Write-Bad 'making sure "Add python.exe to PATH" is ticked, then re-run this script.'
    exit 1
}

$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$tooOld = & $Python -c "import sys; print(sys.version_info < (3, 11))"
if ($tooOld -eq 'True') {
    Write-Bad "Python $version found, 3.11 or newer required."
    exit 1
}
Write-Ok "python $version ($Python)"

# Windows puts the interpreter in .venv\Scripts, every other platform in
# .venv/bin. Handling both lets the script be smoke-tested with pwsh on Linux
# instead of only ever running for the first time on a user's machine.
$onWindows = $IsWindows -or ($env:OS -eq 'Windows_NT')
$venvPython = if ($onWindows) { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
              else            { Join-Path $PSScriptRoot '.venv/bin/python' }

if (-not (Test-Path $venvPython)) {
    Write-Host '  creating .venv ...'
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Bad 'could not create .venv'; exit 1 }
}
if (-not (Test-Path $venvPython)) { Write-Bad "venv created but $venvPython is missing"; exit 1 }
Write-Ok '.venv ready'

Write-Host '  installing Python packages (this can take a few minutes) ...'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Bad 'pip install failed - see the output above'; exit 1 }
Write-Ok 'Python packages installed'

# --- 2. External programs ----------------------------------------------------
Write-Head '2/3  External programs'

$toolsDir = Join-Path $PSScriptRoot 'tools'

function Test-Tool {
    param($Binary, $Label)
    $cmd = Get-Command $Binary -ErrorAction SilentlyContinue
    if ($cmd) { Write-Ok "$Label - $($cmd.Source)"; return $true }
    Write-Bad "$Label - not on PATH"
    return $false
}

function Install-PortableTool {
    param($Name, $Url, $Destination)
    # [System.IO.Path]::GetTempPath() rather than $env:TEMP: the environment
    # variable is not guaranteed to be set, and Join-Path throws on a null path.
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) "$Name.zip"
    Write-Host "  downloading $Name from $Url"
    Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
    $hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
    Write-Host "    SHA256: $hash"
    Expand-Archive -Path $zip -DestinationPath $Destination -Force
    Remove-Item $zip -Force
    Write-Ok "$Name unpacked into $Destination"
}

$missing = @()
if (-not (Test-Tool 'mafft'        'MAFFT (multiple sequence alignment)')) { $missing += 'mafft' }
if (-not (Test-Tool 'primer3_core' 'Primer3 (primer design)'))            { $missing += 'primer3' }
if (-not (Test-Tool 'blastn'       'BLAST+ blastn (homology search)'))    { $missing += 'blast' }
if (-not (Test-Tool 'makeblastdb'  'BLAST+ makeblastdb'))                 { $missing += 'blast' }

if ($missing.Count -gt 0) {
    if ($WithTools) {
        New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
        if ($missing -contains 'primer3') {
            Install-PortableTool -Name 'primer3' -Destination $toolsDir `
                -Url 'https://github.com/primer3-org/primer3/releases/download/v2.6.1/primer3-2.6.1_exe_for_windows.zip'
        }
        if ($missing -contains 'mafft') {
            Write-Warn 'MAFFT for Windows must be downloaded manually - the file name'
            Write-Warn 'carries the version: https://mafft.cbrc.jp/alignment/software/windows.html'
            Write-Warn "Unpack the all-in-one zip into $toolsDir"
        }
        if ($missing -contains 'blast') {
            Write-Warn 'BLAST+ is distributed by NCBI as a Windows installer (.exe) or a'
            Write-Warn 'portable archive. Pick one from:'
            Write-Warn '  https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/'
            Write-Warn '  ncbi-blast-<version>+-win64.exe          (installer, adds itself to PATH)'
            Write-Warn "  ncbi-blast-<version>+-x64-win64.tar.gz   (portable; unpack into $toolsDir)"
        }
        # Everything under tools\ goes on PATH for this session.
        Get-ChildItem -Path $toolsDir -Recurse -Directory |
            Where-Object { Get-ChildItem $_.FullName -Filter '*.exe' -ErrorAction SilentlyContinue } |
            ForEach-Object { $env:PATH = "$($_.FullName);$env:PATH" }
        Write-Warn "tools\ was added to PATH for this session only. To make it permanent,"
        Write-Warn 'add the directories under tools\ to your user PATH, or start the app'
        Write-Warn 'from this same PowerShell session.'
    } else {
        Write-Warn 'Install the missing programs, then re-run:'
        Write-Host '      Primer3: https://github.com/primer3-org/primer3/releases  (..._exe_for_windows.zip)'
        Write-Host '      MAFFT:   https://mafft.cbrc.jp/alignment/software/windows.html  (all-in-one)'
        Write-Host '      BLAST+:  https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/  (win64.exe)'
        Write-Host '      (or re-run with -WithTools to fetch the portable Primer3 build)'
    }
}

# --- 3. Verify ------------------------------------------------------------
Write-Head '3/3  Verifying'

$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'
& $venvPython (Join-Path $PSScriptRoot 'tests/test_portability.py') 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Ok 'cross-platform checks pass' }
else { Write-Warn 'portability checks failed - run: .venv\Scripts\python.exe tests\test_portability.py' }

& $venvPython (Join-Path $PSScriptRoot 'tests/test_entrez_query.py') 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Ok 'query builder checks pass' }
else { Write-Warn 'query builder checks failed' }

if ((Get-Command mafft -ErrorAction SilentlyContinue) -and
    (Get-Command primer3_core -ErrorAction SilentlyContinue)) {
    & $venvPython (Join-Path $PSScriptRoot 'tests/test_core_offline.py') 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'pipeline test passes (real MAFFT and Primer3)' }
    else { Write-Warn 'pipeline test failed - run: .venv\Scripts\python.exe tests\test_core_offline.py' }
}

Write-Head 'Done.'
Write-Host '  Start the app:   .\run.ps1          -> http://127.0.0.1:8090'
Write-Host '  Different port:  .\run.ps1 -Port 9000'
Write-Host '  Optional, for faster NCBI downloads:'
Write-Host '      $env:NCBI_API_KEY = "<your key>"   # ncbi.nlm.nih.gov/account/settings'
Write-Host '      $env:NCBI_EMAIL   = "you@example.com"'
