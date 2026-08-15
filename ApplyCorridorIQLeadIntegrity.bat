@echo off
setlocal
cd /d "%~dp0"
set "PORT=8780"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python was not found. Install Python 3, then try again.
  pause
  exit /b 1
)

set "PYTHONPATH=%CD%"
set "PORTAL_STATE=down"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2; if ($j.service -eq 'corridoriq-sales') { 'corridoriq' } else { 'foreign' } } catch { $listener=Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($listener) { 'foreign' } else { 'down' } }"`) do set "PORTAL_STATE=%%S"

if /I "%PORTAL_STATE%"=="foreign" (
  echo Port %PORT% is being used by another program.
  echo Lead Integrity did not stop that program.
  pause
  exit /b 1
)

if /I "%PORTAL_STATE%"=="corridoriq" (
  echo Stopping CorridorIQ safely before the database correction...
  powershell -NoProfile -Command "try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2; if ($j.service -ne 'corridoriq-sales') { exit 2 }; $pids=@(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique); $pids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; $released=$false; for ($i=0; $i -lt 40; $i++) { $listener=Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if (-not $listener) { $released=$true; break }; Start-Sleep -Milliseconds 500 }; if (-not $released) { exit 3 }; exit 0 } catch { exit 1 }"
  if errorlevel 1 (
    echo CorridorIQ could not be stopped safely.
    echo Close its PowerShell window and run this file again.
    pause
    exit /b 1
  )
)

echo.
echo Applying Release 11 Lead Integrity...
echo A full SQLite backup will be created before any records change.
echo This may take several minutes.
echo.
%PY% "scripts\apply_lead_integrity.py" --apply
if errorlevel 1 (
  echo.
  echo Lead Integrity did not complete successfully.
  if errorlevel 2 (
    echo The safety check stopped before database changes or a new backup.
  ) else (
    echo Review the message above.
    echo If this run created a backup, it remains in pipeline\db\backups.
  )
  pause
  exit /b 1
)

echo.
echo Lead Integrity completed successfully.
echo Starting CorridorIQ...
start "" "%CD%\CorridorIQHQ.bat"
endlocal
