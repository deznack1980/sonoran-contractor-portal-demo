# CorridorIQ — Automated Morning Refresh (Windows Task Scheduler)

This guide schedules the CorridorIQ **morning refresh** so the dashboard is
current before the business day begins. The refresh runs one master command:

```powershell
python -m pipeline.run morning_refresh
```

The same command is used by the scheduler and by a manual admin trigger, so both
paths execute the identical pipeline.

Recommended trigger time: **weekdays at 5:00 AM** (data is ready by ~6:00 AM).

---

## 1. Prerequisites

- Python installed and on `PATH` (or a `.venv` in the project root).
- Project dependencies installed (`pip install -r requirements.txt`).
- The wrapper script: `scripts\run_morning_refresh.ps1`.

### Secrets (do NOT put these in the script)

If a source needs a token (e.g. Socrata), set it once as an environment
variable for the account that runs the task — never inside the `.ps1`:

```powershell
# Run once, in an elevated PowerShell, for the service/user account:
setx SOCRATA_APP_TOKEN "your-token-here"
```

The script reads secrets from the environment only.

---

## 2. Quick test (run it once by hand)

```powershell
cd C:\Users\dezna\OneDrive\Desktop\CorridorIQ
powershell -ExecutionPolicy Bypass -File .\scripts\run_morning_refresh.ps1
```

Confirm:

- A log file appears under `logs\morning_refresh\run_<timestamp>.log`.
- A summary appears under `reports\generated\morning_refresh_summary_<date>.md`.
- The dashboard "Morning refresh" card (admin view) shows the latest run.

---

## 3. Create the scheduled task (UI)

Open **Task Scheduler → Create Task…** (not "Basic Task").

**General**
- Name: `CorridorIQ Morning Refresh`
- Select **Run whether user is logged on or not**.
- Check **Run with highest privileges** if your Python/env requires it.

**Triggers → New…**
- Begin the task: **On a schedule**
- Settings: **Weekly**, recur every 1 week, days **Mon–Fri**
- Start time: **5:00:00 AM**
- Advanced:
  - **Stop task if it runs longer than: 1.5 hours** (see Settings below too)
- Enabled: checked

**Actions → New…**
- Action: **Start a program**
- Program/script:
  ```
  powershell.exe
  ```
- Add arguments:
  ```
  -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dezna\OneDrive\Desktop\CorridorIQ\scripts\run_morning_refresh.ps1"
  ```
- Start in:
  ```
  C:\Users\dezna\OneDrive\Desktop\CorridorIQ
  ```

**Conditions**
- **Start the task only if the following network connection is available:** Any connection.
- **Wake the computer to run this task:** checked.
- (Optional) Start only on AC power — uncheck if this is a laptop that should run on battery.

**Settings**
- **Allow task to be run on demand:** checked.
- **If the task fails, restart every:** 15 minutes, **up to 3 times**.
- **Stop the task if it runs longer than:** 1.5 hours.
- **If the running task does not end when requested, force it to stop.**
- **If the task is already running, then the following rule applies:** **Do not start a new instance.**

Click **OK** and enter the credentials for the account that will run the task.

---

## 4. Create the scheduled task (PowerShell, scripted alternative)

```powershell
$Root   = "C:\Users\dezna\OneDrive\Desktop\CorridorIQ"
$Script = Join-Path $Root "scripts\run_morning_refresh.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 5:00AM

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -RestartInterval (New-TimeSpan -Minutes 15) -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1.5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "CorridorIQ Morning Refresh" `
    -Action $Action -Trigger $Trigger -Settings $Settings `
    -RunLevel Highest -Description "Automated CorridorIQ morning data refresh."
```

> `-MultipleInstances IgnoreNew` prevents overlapping runs at the Windows level;
> the pipeline itself also refuses a second concurrent run via a database lock.

---

## 5. Verifying and monitoring

- **Logs:** `logs\morning_refresh\run_<timestamp>.log` and the structured
  `logs\morning_refresh\morning_refresh_<timestamp>.log`.
- **Summary (notification):** `reports\generated\morning_refresh_summary_<date>.md`.
- **Dashboard:** the admin "Morning refresh" card, or the API:
  - `GET /api/status/refresh` — simple status for any user.
  - `GET /api/admin/morning-refresh` — detailed status + freshness (admins).
  - `POST /api/admin/morning-refresh/run` — manual trigger (admins).
- **Task history:** Task Scheduler → the task → History tab. Exit code `0` is
  success; any non-zero is a failure.

---

## 6. Behavior notes

- **One failed jurisdiction does not stop the others.** Each source is retried
  up to 3 times for transient network errors (with backoff) and recorded
  independently; the overall run is marked `partial` when some sources fail.
- **No overlap.** If a run is already in progress, a new run is refused.
- **Incremental.** Only new/changed permits are re-scored; scoring weights and
  the 60-point threshold are unchanged.
- **Freshness.** Each jurisdiction is flagged Current / Delayed / Stale / Failed
  based on the newest source record and the last sync result.
