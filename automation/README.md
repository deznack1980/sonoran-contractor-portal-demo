# CorridorIQ Daily Automation

Runs the CorridorIQ pipeline once a day and has OpenClaw write a natural-language
summary of the results. Registered as the Windows Task Scheduler task
**"CorridorIQ Daily Pipeline"**, firing daily at **6:00 AM** (wakes the machine
from sleep if needed; catches up if the scheduled time was missed).

## How it works

```
Task Scheduler (6:00 AM daily)
  -> automation/run_daily.ps1
       -> python pipeline/run.py                 (real data: ingest/analyze/rebuild/export/reports)
       -> wsl.exe -d Ubuntu -- bash automation/openclaw_summary.sh
            -> openclaw agent --local ...         (reads today's reports, writes daily_summary_<date>.md)
```

- **Pipeline step is deterministic** — it doesn't depend on the AI agent, so the
  core data refresh always happens even if the summary step has a hiccup.
- **Summary step uses OpenClaw's `--local` (embedded) mode**, not the default
  Gateway-routed mode. The Gateway path requires interactive device-pairing/
  scope approval that doesn't work in an unattended scheduled context — see
  the comment at the top of `openclaw_summary.sh`.
- **Summary step uses a dedicated session key** (`agent:main:corridoriq-daily-summary`),
  not the shared "main" chat session — reusing "main" pulled in 100k+ tokens of
  unrelated prior chat history on every call and blew through OpenAI's
  per-minute rate limit. The isolated session also keeps this daily job out of
  your regular OpenClaw chat history.
- **Dates are computed in UTC** in `openclaw_summary.sh` (`date -u`), matching
  how the Python pipeline stamps report filenames (`datetime.now(timezone.utc)`).
  Using local time here caused an off-by-one-day mismatch in the evening
  (host is Pacific, UTC-7/8).

## Output

- Fresh reports: `reports/generated/*.md` (same 4 reports the pipeline always makes)
- AI summary: `reports/generated/daily_summary_<YYYY-MM-DD>.md`
- Dashboard data: `data/exports/*.json` (dashboard.html / contractors.html pick this up automatically)
- Run logs: `logs/daily_run_<timestamp>.log` — one per run, includes full pipeline
  output and the OpenClaw agent's output. Check here first if something looks stale.

## Changing the schedule

```powershell
Set-ScheduledTask -TaskName "CorridorIQ Daily Pipeline" -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00AM)
```

## Running it manually

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dezna\OneDrive\Desktop\CorridorIQ\automation\run_daily.ps1"
```

or trigger the registered task directly:

```powershell
Start-ScheduledTask -TaskName "CorridorIQ Daily Pipeline"
```

## Known limitations

- **Task is set to "Only run when logged on"** — if the laptop is fully logged
  out (not just locked/asleep) at 6:00 AM, the task won't fire. "Wake to run"
  and "start as soon as possible after a missed run" are both enabled to
  reduce the impact of this.
- **Requires WSL's Ubuntu distro and OpenClaw to be installed and configured**
  as they were at setup time (`~/.npm-global/bin/openclaw`, `--local` mode
  working with a configured model provider). If OpenClaw's config changes,
  re-verify with `wsl -d Ubuntu -- bash automation/openclaw_summary.sh` before
  trusting the next scheduled run.
- **Only Mesa, Tempe, Gilbert, and Scottsdale, AZ are connected** to real data
  sources today — see `pipeline/config/jurisdictions.yaml` for the other 9
  jurisdictions and why each is still pending.
- **SportsModel, Energy Intelligence, and StockModel** (the other ATLAS
  projects) don't exist on disk yet, so this automation only covers
  CorridorIQ. Nothing is scheduled for the other three until they're built.
