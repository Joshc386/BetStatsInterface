@echo off
rem Match-day player refresh (Phase 5, tier 2). VPN OFF, machine awake required -
rem the FBref watchdog blocks idle-sleep for the run and self-recovers from
rem Cloudflare stalls (kill + fresh session), so this is safe to run unattended.
rem Registered in Task Scheduler as "BetStats matchday" (daily 08:00, interactive
rem logon so the headful browser can render). PYTHONIOENCODING=utf-8 so foreign
rem (European) club names don't crash cp1252.
rem Battery conditions are OFF on the task (2026-08-21): this is a laptop, and
rem the Task Scheduler defaults skipped the run whenever it was unplugged -
rem silently, since nothing FAILED - and killed a run mid-scrape on unplug. The
rem backfill commits per match and is resumable, so battery running costs nothing.
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
rem As in run_nightly.cmd: without this, cmd.exe returns 0 and Task Scheduler's
rem Last Run Result is meaningless as a health signal.
exit /b %MD_EXIT%
