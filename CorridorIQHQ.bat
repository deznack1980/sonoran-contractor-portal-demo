@echo off
setlocal
cd /d "%~dp0"
set "PORT=8780"
set "BUILD=2026-08-15-project-material-estimates-r15.2"
set "URL=http://127.0.0.1:%PORT%/login.html?build=%BUILD%"

REM Prefer the Windows py launcher, then python.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python was not found. Install Python 3, then launch CorridorIQ again.
  pause
  exit /b 1
)

REM Always import this checkout, even when another shell has a different folder.
set "PYTHONPATH=%CD%"

REM Identify what, if anything, is already listening on CorridorIQ's port.
set "PORTAL_STATE=down"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2; if ($j.service -eq 'corridoriq-sales') { if ($j.build -eq '%BUILD%') { 'current' } else { 'stale' } } else { 'foreign' } } catch { $listener=Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($listener) { 'foreign' } else { 'down' } }"`) do set "PORTAL_STATE=%%S"

if /I "%PORTAL_STATE%"=="foreign" (
  echo Port %PORT% is being used by another program.
  echo CorridorIQ did not stop that program.
  echo Close the program using port %PORT%, then launch CorridorIQ again.
  pause
  exit /b 1
)

REM Replace an older CorridorIQ server, but never terminate an unrelated app.
if /I "%PORTAL_STATE%"=="stale" (
  echo Replacing an older CorridorIQ server with the current build...
  powershell -NoProfile -Command "try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2; if ($j.service -ne 'corridoriq-sales') { exit 2 }; Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction Stop }; exit 0 } catch { exit 1 }"
  if errorlevel 1 (
    echo The older CorridorIQ server could not be stopped safely.
    echo Close its PowerShell window, then launch CorridorIQ again.
    pause
    exit /b 1
  )
  timeout /t 2 /nobreak >nul
  set "PORTAL_STATE=down"
)

if /I not "%PORTAL_STATE%"=="current" (
  echo Starting the current CorridorIQ build on port %PORT%...
  start "CorridorIQ Portal" /min cmd /k "cd /d ""%~dp0"" && set ""PYTHONPATH=%CD%"" && %PY% -m pipeline.api.server"

  REM Wait up to 20 seconds for this exact build, not merely any HTTP page.
  powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 40; $i++) { try { $j=Invoke-RestMethod -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 1; if ($j.service -eq 'corridoriq-sales' -and $j.build -eq '%BUILD%') { $ok=$true; break } } catch {} Start-Sleep -Milliseconds 500 }; if (-not $ok) { exit 1 }"
  if errorlevel 1 (
    echo.
    echo The current CorridorIQ build did not start.
    echo Open the minimized "CorridorIQ Portal" window to see the exact error.
    pause
    exit /b 1
  )
)

start "" "%URL%"
endlocal
