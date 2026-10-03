"""FBref reader that survives two page shapes the stock soccerdata reader cannot.

1. Qualifier history pages with no ``table#seasons`` (``read_seasons``).
2. Single-match competitions — Community Shield, UEFA Super Cup — whose seasons
   link straight to the match report rather than a season page. Two stock
   failures, both fixed here:
   - ``read_seasons``: FBref labels each edition by calendar year, and the
     multi-year parser reads "2021" as the season code 20-21 (consecutive
     halves) while reading every other year as a calendar year — so the 2020
     and 2021 editions collide on '2021' and '2122' vanishes. The season is
     built from the year directly instead.
   - ``read_schedule``: the stock reader follows the match page's "Scores &
     Fixtures" link, lands on FBref's matches-of-the-day page and dies on its
     missing date column (``KeyError: 'date'`` — the ADR 0011 Super Cup
     blocker). The one-row schedule is built from the match page itself.

soccerdata 1.9.0's ``read_seasons`` hard-requires ``table#seasons`` on each
competition's history page (``(html_table,) = tree.xpath(...)``). FBref renders
qualifier history pages (WC qualification ×7 confederations + play-offs, Euros/
AFCON/Asian Cup qualifying) WITHOUT that table, so season resolution crashes
before any schedule fetch — the ADR 0011 qualifier blocker.

The editions ARE on the page: one ``h2`` per edition, text leading with the
edition year, its anchor holding the edition URL. Completed editions link at
``/en/comps/<id>/<year>/…``; the in-progress edition links at the unqualified
competition URL (no year segment), which is exactly how FBref serves the
current season elsewhere — so both shapes pass straight through to
``read_schedule`` unchanged.

``FBref.read_seasons`` here tries the stock parser first and falls back to
mining those headings, returning the same ``(league, season) → [format, url]``
frame ``read_schedule`` consumes. Leagues whose pages have ``table#seasons``
never reach the fallback.
"""

from __future__ import annotations

import re

import pandas as pd
import soccerdata as sd
from lxml import html
from soccerdata._common import SeasonCode, make_game_id
from soccerdata.fbref import FBREF_API

_MATCH_URL = "/en/matches/"


def edition_headings(tree) -> list[tuple[str, str]]:
    """(year, url) per edition ``h2`` on a competition history page.

    An edition heading leads with its 4-digit year and contains the edition's
    link; nav headings ("Full Site Menu", …) match neither. The in-progress
    edition's link has no year segment — passed through as-is.
    """
    editions = []
    for heading in tree.xpath("//h2[.//a[contains(@href, '/en/comps/')]]"):
        match = re.match(r"(\d{4})\b", heading.text_content().strip())
        if match:
            editions.append((match.group(1), heading.xpath(".//a/@href")[0]))
    return editions


def _schedule_name(tree, team: dict) -> str:
    """A team's FBref *schedule* spelling, read from its stats-table caption.

    The scorebox carries the full name ("Manchester United"); schedules and
    captions carry the short one ("Manchester Utd"), and the cup path pairs
    schedule names against captions — so a scorebox name fails to pair. Falls
    back to the scorebox name when the page has no stats table for the team.
    """
    caption = tree.xpath(f"string(//table[@id='stats_{team['id']}_summary']/caption)")
    return caption.removesuffix("Player Stats Table").strip() or team["name"]


class FBref(sd.FBref):
    """``sd.FBref`` that survives history pages with no ``table#seasons`` and
    single-match competitions. Identical to ``sd.FBref`` everywhere else."""

    def _single_match_seasons(self, split_up_big5: bool) -> pd.DataFrame | None:
        """Seasons of a single-match competition, built from each edition's year.

        An edition played in calendar year Y opens season Y/Y+1 (the 2022
        Community Shield, 30 July 2022, opens 2022-23). None when any season
        links to a season page rather than a match — every multi-match
        competition, which then takes the stock parser untouched.
        """
        rows = []
        for lkey, league in self.read_leagues(split_up_big5).iterrows():
            reader = self.get(
                FBREF_API + league.url, self.data_dir / f"seasons_{lkey}.html"
            )
            links = html.parse(reader).xpath(
                "//table[@id='seasons']"
                "//th[@data-stat='year_id' or @data-stat='year']/a"
            )
            for link in links:
                if not link.get("href").startswith(_MATCH_URL):
                    return None
                year = int(link.text_content().strip())
                if self._season_code == SeasonCode.MULTI_YEAR:
                    season = f"{year % 100:02d}{(year + 1) % 100:02d}"
                else:
                    season = str(year)
                rows.append({"league": lkey, "season": season,
                             "format": "elimination", "url": link.get("href")})
        if not rows:
            return None
        df = pd.DataFrame(rows).set_index(["league", "season"]).sort_index()
        return df.loc[(slice(None), self.seasons), ["format", "url"]]

    def read_schedule(self, force_cache: bool = False) -> pd.DataFrame:
        seasons = self.read_seasons(split_up_big5=True)
        if not seasons["url"].str.startswith(_MATCH_URL).all():
            return super().read_schedule(force_cache)
        return self._single_match_schedule(seasons)

    def _single_match_schedule(self, seasons: pd.DataFrame) -> pd.DataFrame:
        """One schedule row per edition, read off the match page it links to.

        The page is cached as ``match_<id>.html`` — the very file
        ``read_player_match_stats`` reads next — so this costs no extra fetch.
        Same columns and index as the stock schedule, for the fields the cup
        path and ``read_player_match_stats`` use.
        """
        rows = []
        for (lkey, skey), season in seasons.iterrows():
            game_id = season.url.split("/")[3]  # the stock read_schedule's split
            reader = self.get(
                FBREF_API + season.url, self.data_dir / f"match_{game_id}.html"
            )
            tree = html.parse(reader)
            home, away = self._parse_teams(tree)
            (date,) = tree.xpath("//span[@class='venuetime']/@data-venue-date")
            rows.append(
                {
                    "league": lkey,
                    "season": skey,
                    "date": pd.Timestamp(str(date)),  # lxml's str subclass
                    "home_team": _schedule_name(tree, home),
                    "away_team": _schedule_name(tree, away),
                    "match_report": season.url,
                    "game_id": game_id,
                }
            )
        df = pd.DataFrame(rows)
        df["game"] = df.apply(make_game_id, axis=1)
        return df.set_index(["league", "season", "game"]).sort_index()

    def read_seasons(self, split_up_big5: bool = False) -> pd.DataFrame:
        single_match = self._single_match_seasons(split_up_big5)
        if single_match is not None:
            return single_match
        try:
            return super().read_seasons(split_up_big5)
        except ValueError as exc:
            # the stock parser's unpack of //table[@id='seasons'] found nothing
            if "not enough values to unpack" not in str(exc):
                raise
            return self._read_seasons_from_headings(split_up_big5)

    def _read_seasons_from_headings(self, split_up_big5: bool) -> pd.DataFrame:
        """Mine (season, url) from the per-edition ``h2`` headings.

        Qualifier competition formats are group + play-off hybrids; ``format``
        is set to ``"round-robin"`` throughout — ``read_schedule`` never reads
        it (only ``read_team_season_stats`` does, which internationals don't
        use).
        """
        df_leagues = self.read_leagues(split_up_big5)

        rows = []
        for lkey, league in df_leagues.iterrows():
            filepath = self.data_dir / f"seasons_{lkey}.html"
            reader = self.get(FBREF_API + league.url, filepath)
            tree = html.parse(reader)
            for season, url in edition_headings(tree):
                rows.append(
                    {"league": lkey, "season": season,
                     "format": "round-robin", "url": url}
                )

        if not rows:
            raise ValueError(
                f"no edition headings found on the seasons page(s) for "
                f"{self.leagues!r} — page shape changed?"
            )
        df = pd.DataFrame(rows)
        df["season"] = df["season"].apply(self._season_code.parse)
        df = df.drop_duplicates(subset=["league", "season"], keep="first")
        df = df.set_index(["league", "season"]).sort_index()
        return df.loc[(slice(None), self.seasons), ["format", "url"]]
