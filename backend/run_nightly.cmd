@echo off
rem Nightly incremental refresh (Phase 5, tier 1 — UNATTENDED). Register in
rem Windows Task Scheduler alongside "BetStats upcoming fixtures" (StartWhenAvailable
rem so a missed night catches up). Refreshes current-season league team data
rem (football-data.co.uk) + points deductions (ESPN). PLAYER data is the
rem SUPERVISED tier (ingestion.matchday) and is NOT run here. Idempotent.
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ingestion.nightly start >> logs\nightly.log
".venv\Scripts\python.exe" -m ingestion.nightly >> logs\nightly.log 2>&1
set NL_EXIT=%errorlevel%
echo [%date% %time%] exit code %NL_EXIT% >> logs\nightly.log
if not %NL_EXIT%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0notify_failure.ps1" -Message "ingestion.nightly exited %NL_EXIT% - check backend\logs\nightly.log" -Title "BetStats nightly FAILED"
)
