@echo off
rem Daily failure digest (replaces notify_failure.ps1, removed 2026-08-30).
rem Registered in Task Scheduler as "BetStats digest", after squads (09:00) so
rem every job of the day is in. Writes logs\digest.txt and appends a dated copy
rem to logs\digest.log - reading one file beats reading four.
cd /d "%~dp0"
rem UTF-8: job logs carry club names and em-dashes; cp1252 mangles both.
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
echo [%date% %time%] digest >> logs\digest.log
".venv\Scripts\python.exe" -m ingestion.digest 24 >> logs\digest.log 2>&1
rem Always 0: a digest reporting failures is the digest WORKING. Exiting non-zero
rem would make this job alarm about other jobs alarming.
exit /b 0
