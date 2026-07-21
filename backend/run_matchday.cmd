@echo off
rem Match-day player refresh (Phase 5, tier 2). VPN OFF, machine awake required -
rem the FBref watchdog blocks idle-sleep for the run and self-recovers from
rem Cloudflare stalls (kill + fresh session), so this is safe to run unattended.
rem Registered in Task Scheduler as "BetStats matchday" (daily 08:00, interactive
rem logon so the headful browser can render). PYTHONIOENCODING=utf-8 so foreign
rem (European) club names don't crash cp1252.
rem
rem   run_matchday.cmd                          -> current-season leagues w/ pending work
rem   run_matchday.cmd "FA Cup" "Champions League"  -> exactly those (a cup round)
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ingestion.matchday start >> logs\matchday.log
".venv\Scripts\python.exe" -m ingestion.matchday %* >> logs\matchday.log 2>&1
set MD_EXIT=%errorlevel%
echo [%date% %time%] exit code %MD_EXIT% >> logs\matchday.log
if not %MD_EXIT%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0notify_failure.ps1" -Message "ingestion.matchday exited %MD_EXIT% - check backend\logs\matchday.log" -Title "BetStats matchday FAILED"
)
