"""FBref reader with a ``read_seasons`` fallback for qualifier history pages.

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
from soccerdata.fbref import FBREF_API


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


class FBref(sd.FBref):
    """``sd.FBref`` that survives history pages with no ``table#seasons``."""

    def read_seasons(self, split_up_big5: bool = False) -> pd.DataFrame:
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
