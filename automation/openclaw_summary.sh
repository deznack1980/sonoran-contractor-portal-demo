#!/bin/bash
# Invoked once daily (via run_daily.ps1 -> Task Scheduler) to have OpenClaw
# read the freshly-generated CorridorIQ reports and write a natural-language
# daily summary. Runs in --local (embedded) mode deliberately -- the default
# Gateway-routed path requires interactive device-pairing/scope approval that
# does not work in an unattended scheduled context.

set -uo pipefail

OPENCLAW=~/.npm-global/bin/openclaw
REPORTS_DIR="/mnt/c/Users/dezna/OneDrive/Desktop/CorridorIQ/reports/generated"
# UTC, to match the Python pipeline's datetime.now(timezone.utc) date-stamping
# in report filenames -- using local time here would drift a day off in the
# evening (host is Pacific, UTC-7/8).
TODAY=$(date -u +%Y-%m-%d)

MESSAGE="The CorridorIQ construction-permit pipeline just finished its daily run. Read the newest files in ${REPORTS_DIR} -- specifically the most recently dated daily_arizona_plumbing_intelligence_report_*.md and highest_opportunity_projects_*.md files (match today's date, ${TODAY}, in the filename; if that date is not present, use the most recent date available). Based on their content, write a concise summary (6-10 sentences) covering: (1) how many qualifying permits/projects were found, (2) the top 3 highest-opportunity projects with permit number, city, category, and score, (3) which jurisdictions are connected vs pending per the report's coverage banner, (4) overall estimated material value / gross profit totals if visible. Save this summary as a new file at ${REPORTS_DIR}/daily_summary_${TODAY}.md with a level-1 markdown heading '# CorridorIQ Daily Summary -- ${TODAY}'. If you cannot find any report files dated today or yesterday, write a summary file stating plainly that the pipeline may not have run successfully -- never fabricate figures."

echo "=== $(date -Iseconds) starting OpenClaw daily summary ==="
"$OPENCLAW" agent --local --agent main --session-key "agent:main:corridoriq-daily-summary" --message "$MESSAGE" --timeout 300
status=$?
echo "=== $(date -Iseconds) OpenClaw daily summary finished (exit $status) ==="
exit $status
