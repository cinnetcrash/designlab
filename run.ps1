<#
.SYNOPSIS
    Start Primer Designer on Windows.

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Port 9000

.NOTES
    NOT TESTED ON WINDOWS. It was written on Linux and mirrors run.sh, which is
    exercised continuously. Treat a failure here as a bug to report, not as a
    problem with your machine.

    If PowerShell refuses to run the script, it is the execution policy, not the
    script:
        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
    or run it once with:
        powershell -ExecutionPolicy Bypass -File .\run.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8090 }),
    [string]$BindHost = $(if ($env:HOST) { $env:HOST } else { '127.0.0.1' })
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# The project venv is preferred: a system-wide Python may carry package versions
# this app cannot use, and installing into it could break other tools.
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    Write-Warning ".venv not found - falling back to the Python on PATH. Run install.ps1 first for a clean setup."
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue).Source }
    if (-not $python) { throw 'No Python found on PATH. Install Python 3.11+ first.' }
}

foreach ($tool in 'mafft', 'primer3_core', 'blastn', 'makeblastdb') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Warning "$tool is not on PATH - /api/health will report it as missing."
    }
}

$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'

Write-Host "Primer Designer -> http://${BindHost}:${Port}" -ForegroundColor Cyan
& $python -m uvicorn main:app --app-dir backend --host $BindHost --port $Port
