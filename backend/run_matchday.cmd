@echo off
rem Match-day player refresh (Phase 5, tier 2 — SUPERVISED, run by hand).
rem VPN OFF, machine awake — the FBref watchdog holds off idle-sleep for the run.
rem PYTHONIOENCODING=utf-8 so foreign (European) club names don't crash cp1252.
rem
rem   run_matchday.cmd                          -> current-season leagues w/ pending work
rem   run_matchday.cmd "FA Cup" "Champions League"  -> exactly those (a cup round)
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m ingestion.matchday %*
