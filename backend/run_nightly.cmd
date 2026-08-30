@echo off
rem Nightly incremental refresh (Phase 5, tier 1 — UNATTENDED). Register in
rem Windows Task Scheduler alongside "BetStats upcoming fixtures" (StartWhenAvailable
rem so a missed night catches up). Refreshes current-season league team data
rem (football-data.co.uk) + points deductions (ESPN). PLAYER data is the
rem SUPERVISED tier (ingestion.matchday) and is NOT run here. Idempotent.
rem Battery conditions OFF on the task (2026-08-21) - see run_matchday.cmd.
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ingestion.nightly start >> logs\nightly.log
".venv\Scripts\python.exe" -m ingestion.nightly >> logs\nightly.log 2>&1
set NL_EXIT=%errorlevel%
echo [%date% %time%] exit code %NL_EXIT% >> logs\nightly.log
rem Hand Python's code back to Task Scheduler. The modal notifier used to sit
rem here and BLOCK, so cmd.exe returned 0 and Last Run Result read "success" on
rem both mornings this job actually failed. Popup removed 2026-08-30; failures
rem are now reported by `python -m ingestion.digest`, and Last Run Result is
rem finally truthful.
exit /b %NL_EXIT%
