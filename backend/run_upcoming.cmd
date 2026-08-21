@echo off
rem Daily upcoming-fixtures refresh (ADR 0009) — registered in Windows Task
rem Scheduler as "BetStats upcoming fixtures". Appends to logs\upcoming.log;
rem a non-zero exit (e.g. unknown ESPN team name -> alias work) shows there.
rem Battery conditions OFF on the task (2026-08-21) - see run_matchday.cmd.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] ingestion.upcoming start >> logs\upcoming.log
".venv\Scripts\python.exe" -m ingestion.upcoming 45 >> logs\upcoming.log 2>&1
set UP_EXIT=%errorlevel%
echo [%date% %time%] exit code %UP_EXIT% >> logs\upcoming.log
rem This task had no notifier, so when the ESPN 403 took it down on 2026-08-05 it
rem failed twice over with nothing on screen - only the nightly popped up. Same
rem alarm as the other two jobs now, and the code goes back to Task Scheduler.
if not %UP_EXIT%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0notify_failure.ps1" -Message "ingestion.upcoming exited %UP_EXIT% - check backend\logs\upcoming.log" -Title "BetStats upcoming FAILED"
)
exit /b %UP_EXIT%
