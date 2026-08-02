<#
.SYNOPSIS
    CorridorIQ automated morning data refresh (Sprint 6.3).

.DESCRIPTION
    Runs the full CorridorIQ pipeline once (ingest -> normalize -> lifecycle ->
    score -> company intelligence -> queues -> dashboard export -> summary) via
    `python -m pipeline.run morning_refresh`, writing a timestamped log to
    logs\morning_refresh\ and returning a proper exit code so Windows Task
    Scheduler can report success/failure.

    SECURITY: No passwords or API keys live in this script. Secrets (e.g.
    SOCRATA_APP_TOKEN) are read from the machine/user environment. Set them once
    with setx or through the Task Scheduler action's environment, never here.

.NOTES
    Exit code 0 = success. Non-zero = failure (surfaced to Task Scheduler).
#>

$ErrorActionPreference = "Stop"

# 1. Resolve the project root (this script lives in <root>\scripts).
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

# 2. Activate the project's Python environment if one is present; otherwise
#    fall back to whatever `python` is on PATH.
$Python = "python"
$VenvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    . $VenvActivate
} elseif (Test-Path (Join-Path $Root "venv\Scripts\Activate.ps1")) {
    . (Join-Path $Root "venv\Scripts\Activate.ps1")
}

# 3. Required environment variables. Do NOT hard-code secrets here — this only
#    provides safe, non-secret defaults. Real secrets come from the environment.
if (-not $env:CORRIDORIQ_ENV) { $env:CORRIDORIQ_ENV = "production" }
$env:PYTHONUTF8 = "1"

# 4. Timestamped log file.
$LogDir = Join-Path $Root "logs\morning_refresh"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "run_$Stamp.log"

"[$(Get-Date -Format o)] Starting CorridorIQ morning refresh" | Tee-Object -FilePath $LogFile

# 5. Run the master command; tee stdout+stderr into the log.
& $Python -m pipeline.run morning_refresh *>&1 | Tee-Object -FilePath $LogFile -Append
$code = $LASTEXITCODE

"[$(Get-Date -Format o)] Finished with exit code $code" | Tee-Object -FilePath $LogFile -Append

# 6. Proper success/failure exit code for Task Scheduler.
exit $code
