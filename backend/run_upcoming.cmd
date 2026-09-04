@echo off
rem Daily upcoming-fixtures refresh (ADR 0009) — registered in Windows Task
rem Scheduler as "BetStats upcoming fixtures". Appends to logs\upcoming.log;
rem a non-zero exit (e.g. unknown ESPN team name -> alias work) shows there.
rem Battery conditions OFF on the task (2026-08-21) - see run_matchday.cmd.
cd /d "%~dp0"
rem UTF-8: this job now logs club display names from the ESPN team-stat write
rem (docs/adr/0015), and cp1252 would crash the run on the first accented one.
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
echo [%date% %time%] ingestion.upcoming start >> logs\upcoming.log
".venv\Scripts\python.exe" -m ingestion.upcoming 45 >> logs\upcoming.log 2>&1
set UP_EXIT=%errorlevel%
echo [%date% %time%] exit code %UP_EXIT% >> logs\upcoming.log
rem Failures surface via `python -m ingestion.digest`, not a popup. This job
rem runs up to 15x a day (5 slots x 2 retries), so a per-run modal turned one
rem standing fault into fifteen identical popups - six landed on 2026-08-27 for
rem an EFL Cup tie that had merely not been drawn yet.
exit /b %UP_EXIT%
