@echo off
rem Daily upcoming-fixtures refresh (ADR 0009) — registered in Windows Task
rem Scheduler as "BetStats upcoming fixtures". Appends to logs\upcoming.log;
rem a non-zero exit (e.g. unknown ESPN team name -> alias work) shows there.
cd /d "%~dp0"
if not exist logs mkdir logs
echo [%date% %time%] ingestion.upcoming start >> logs\upcoming.log
".venv\Scripts\python.exe" -m ingestion.upcoming 45 >> logs\upcoming.log 2>&1
echo [%date% %time%] exit code %errorlevel% >> logs\upcoming.log
