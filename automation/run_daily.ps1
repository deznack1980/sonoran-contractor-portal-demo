# CorridorIQ daily automation entry point.
# Invoked once per day by Windows Task Scheduler (see automation/README.md).
#
# 1. Runs the real data pipeline (deterministic -- ingest/analyze/rebuild/export/reports).
# 2. If the pipeline succeeds, invokes OpenClaw (in WSL, --local/embedded mode) once
#    to read the fresh reports and write a natural-language daily summary.
# Everything is logged to logs/ with a timestamped file per run.

# Deliberately NOT "Stop": native commands (python, wsl.exe) writing to
# stderr would otherwise be treated as terminating PowerShell errors even
# when their actual exit code is 0. Failures are detected explicitly via
# $LASTEXITCODE below instead.
$ErrorActionPreference = "Continue"

$ProjectRoot = "C:\Users\dezna\OneDrive\Desktop\CorridorIQ"
$LogDir = Join-Path $ProjectRoot "logs"
$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile = Join-Path $LogDir "daily_run_$Timestamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line
}

Write-Log "=== CorridorIQ daily run starting ==="

Set-Location $ProjectRoot
Write-Log "Running pipeline: python pipeline/run.py"
$pipelineOutput = & python pipeline/run.py 2>&1 | Out-String
Add-Content -Path $LogFile -Value $pipelineOutput
$pipelineExitCode = $LASTEXITCODE

if ($pipelineExitCode -ne 0) {
    Write-Log "Pipeline FAILED (exit code $pipelineExitCode). Skipping OpenClaw summary step."
    Write-Log "=== CorridorIQ daily run finished (pipeline failure) ==="
    exit $pipelineExitCode
}

Write-Log "Pipeline completed successfully."
Write-Log "Running OpenClaw summary step via WSL..."

$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"
$wslScript = "/mnt/c/Users/dezna/OneDrive/Desktop/CorridorIQ/automation/openclaw_summary.sh"
$summaryOutput = & wsl.exe -d Ubuntu -- bash $wslScript 2>&1 | Out-String
Add-Content -Path $LogFile -Value $summaryOutput
$summaryExitCode = $LASTEXITCODE

if ($summaryExitCode -ne 0) {
    Write-Log "OpenClaw summary step FAILED (exit code $summaryExitCode). Pipeline data is still fresh; only the AI summary is missing."
} else {
    Write-Log "OpenClaw summary step completed successfully."
}

Write-Log "=== CorridorIQ daily run finished ==="
exit 0
