# Season rollover — 2026-27 (`2627`)

Dated working copy of the generic rollover checklist in `CLAUDE.md` (Commands →
SEASON ROLLOVER). Verified against the DB on **2026-08-03**. Delete once the
season is running cleanly.

## Kickoffs

| Competition | First fixture | fd.co.uk key |
|---|---|---|
| Championship | **Fri 14 Aug** | `E1` |
| League One / League Two | Sat 15 Aug | `E2` / `E3` |
| Premier League | Fri 21 Aug | `E0` |

## Already verified done — do not redo

- [x] **Promoted ex-National-League club seeded.** `York` is the only club in the
      2627 fixtures with no prior history; it already has `espn_id=315` and
      `fdcouk_name='York'`. This is why `ingestion.upcoming` exits 0 instead of
      failing loud on an unknown ESPN name.
- [x] **FBref path ready for the automated tier.** All nine clubs new to their
      tier resolve, with existing player history — PL: Coventry, Hull, Ipswich;
      Championship: Bolton, Burnley, Cardiff, Lincoln, West Ham, Wolves.
- [x] **Not a blocker:** Barnet, Notts County and York have no `fbref_id`.
      League Two team data comes from fd.co.uk via `fdcouk_name`, and L1/L2
      player data is set aside — Barnet (1 season) and Notts County (3) have
      ingested fine without one.
- [x] **Automation healthy.** All three Task Scheduler jobs running daily, exit 0.

## To do, in order

- [ ] **~Sun 16 Aug — confirm 2627 league data landed.** After the first
      Championship round, `nightly` should stop skipping `E1` and start
      reporting fixtures. Check `backend/logs/nightly.log`.

- [ ] **After that, and not before — cup ingestion is safe.**
      `cups.covered_team_ids('2627')` reads PL/Championship `team_match` rows
      for the season, so until step 1 lands it returns an empty set and **every
      cup tie is filtered out — ingests nothing, silently, exit 0.** Only
      affects explicit runs (`matchday "EFL Cup"` / `"FA Cup"`); the automatic
      matchday path is leagues-only and cannot hit this.

- [ ] **Mid Aug — spot-check `backend/logs/matchday.log`.** The FBref-watchdog-
      under-Task-Scheduler path has never run with real pending work; it has
      only been verified when invoked directly and when firing with nothing to
      do. First real matchday is the first genuine test.

- [ ] **~Sat 22 Aug — confirm Premier League (`E0`) too.** PL starts a week
      after the EFL, so `E0` legitimately keeps 404ing until then.

## What actually bit (2026-08-19)

**A stale cached season index — the trap none of the checks above covered.**
`matchday` exited 1 every morning from 18 Aug, popping the failure dialog. The
watchdog reported *"remaining fixtures likely unmatched on FBref"*, which was a
guess and wrong. The real error, buried in `backfill.log`, was:

```
File "soccerdata/fbref.py", line 237, in read_seasons
    return df.loc[(slice(None), self.seasons), ["format", "url"]]
KeyError: '2627'
```

soccerdata caches `~/soccerdata/data/FBref/seasons_<league>.html`. Ours were
taken in the off-season (22 Jun – 1 Jul) and their newest entry was **2025-2026**,
so no FBref read could resolve `2627` at all. Affected Premier League,
Championship, FA Cup and EFL Cup; Champions League was already fine because UEFA
publishes next season earlier.

Fixed by moving the four stale files aside so soccerdata re-fetched them (~25s
each). Verified after: `read_seasons` returns `['2627']` and `read_schedule`
returns 552 rows. Cost: 5 days of Championship player data, recovered.

**Do this at every rollover** — it is now step 4 in CLAUDE.md's checklist. Check
any league before its first match day with:

```bash
grep -oE "20[0-9]{2}-20[0-9]{2}" "~/soccerdata/data/FBref/seasons_ENG-Championship.html" | sort -u | tail -1
```

A related trap: `test_stats.py::test_season_window_filters_to_chosen_seasons_and_ignores_n`
picked the newest season and asserted it had >1 game, which is false in the
opening rounds — it now picks the newest season with more than one game.

## Two failure modes that now announce themselves

Both used to be silent; since commit `49d9a2e` neither can pass unnoticed.

1. **fd.co.uk changes shape / goes down.** `nightly` now raises once a league
   has been playing longer than `PUBLISH_GRACE` (36h) and still returns
   nothing, so Task Scheduler fires the failure popup. **From ~17 Aug, a
   nightly failure popup most likely means fd.co.uk changed — check that
   before suspecting a bug.**

2. **York's fd.co.uk name is a guess.** If the `E3` CSV says "York City"
   rather than "York", team resolution fails, `E3` skips, and the alarm above
   fires ~36h after League Two kicks off. Fix is an alias + re-run, not a bug.

## Reactive, later

European competitions (late Aug / Sept) — new foreign clubs trip the fail-loud
alias guard by design. Add the alias and re-run.
