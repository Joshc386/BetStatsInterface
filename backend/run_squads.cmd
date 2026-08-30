@echo off
rem Daily Squad-membership refresh (ADR 0013, phase 4) - registered in Windows
rem Task Scheduler as "BetStats squads". TIER 1, UNATTENDED: ESPN only, so no
rem VPN, no headful browser, no rate limiter, no supervision. ~92 requests.
rem
rem RUNS AT 09:00, AFTER "BetStats upcoming fixtures" (07:30), and that order is
rem load-bearing: _rostered_teams() derives the club list from the Fixture slate,
rem so a promoted or relegated club only follows the season once upcoming has
rem laid this season's fixtures down. Also lands after matchday (08:00), which
rem gives the identity ladder the freshest player names to match against -
rem preferred, not required, since the 30-day union covers an unmatched name.
rem Overlapping matchday is harmless: different source, and this commits per club.
rem
rem Battery conditions OFF on the task - see run_matchday.cmd for why (laptop;
rem the Task Scheduler defaults skipped runs SILENTLY whenever it was unplugged).
rem PYTHONIOENCODING=utf-8 so foreign player names don't crash cp1252 - this job
rem prints unmatched roster names, which is exactly where the accents live.
cd /d "%~dp0"
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo [%date% %time%] ingestion.squads start >> logs\squads.log
".venv\Scripts\python.exe" -m ingestion.squads >> logs\squads.log 2>&1
set SQ_EXIT=%errorlevel%
echo [%date% %time%] exit code %SQ_EXIT% >> logs\squads.log
rem Non-zero means at least one club's roster fetch failed, which leaves that
rem Squad STALE - and a stale Squad is shown by the panel without complaint, so
rem the failure has to surface somewhere - it does, via ingestion.digest.
rem As in run_nightly.cmd: without this, cmd.exe returns 0 and Task Scheduler's
rem Last Run Result is meaningless as a health signal.
exit /b %SQ_EXIT%
