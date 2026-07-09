"""Tests for the qualifier ``read_seasons`` shim (ADR 0011 update 2026-07-09).

The pure heading-mining is tested on synthetic HTML. One integration test runs
the full fallback against the real cached FBref qualifier page (present on this
machine since the 2026-07-07 spike) with the webdriver no-op'd — skipped
cleanly if the cache is ever absent, never fetching.
"""

from pathlib import Path

import pytest
from lxml import html

from ingestion.fbref_shim import edition_headings

_PAGE = """
<html><body>
  <h2><a href="/en/comps/">Competitions</a></h2>
  <h2>2026 FIFA World Cup Qualification – UEFA
      <a href="/en/comps/6/WCQ----UEFA-M-Stats">stats</a></h2>
  <h2>2022 FIFA World Cup Qualification – UEFA
      <a href="/en/comps/6/2022/2022-WCQ----UEFA-M-Stats">stats</a></h2>
  <h2>Full Site Menu <a href="/en/comps/6/history/WCQ----UEFA-M-Seasons">x</a></h2>
  <h2>2018 heading with no comps link <a href="/en/other/thing">x</a></h2>
</body></html>
"""


def test_edition_headings_mines_year_and_url():
    """An edition h2 leads with its year and links the edition page; the
    in-progress edition's unqualified (year-less) URL passes through as-is."""
    editions = edition_headings(html.fromstring(_PAGE).getroottree())
    assert editions == [
        ("2026", "/en/comps/6/WCQ----UEFA-M-Stats"),
        ("2022", "/en/comps/6/2022/2022-WCQ----UEFA-M-Stats"),
    ]


_CACHED = (
    Path.home() / "soccerdata/data/FBref/seasons_INT-World Cup Qualification UEFA.html"
)


@pytest.mark.skipif(not _CACHED.exists(), reason="FBref seasons cache not present")
def test_read_seasons_falls_back_on_real_qualifier_page(monkeypatch):
    """End-to-end fallback on the real cached page (no table#seasons): stock
    read_seasons raises, the shim resolves the requested edition to its URL.
    The webdriver is no-op'd — everything this touches is cached."""
    from soccerdata._common import BaseSeleniumReader

    monkeypatch.setattr(BaseSeleniumReader, "_init_webdriver", lambda self: None)
    from ingestion.fbref_shim import FBref

    fb = FBref(
        leagues="INT-World Cup Qualification UEFA", seasons=["2022"], headless=True
    )
    df = fb.read_seasons()
    assert len(df) == 1
    ((league, season),) = df.index
    assert league == "INT-World Cup Qualification UEFA"
    assert season == "2022"
    assert df.iloc[0]["url"] == "/en/comps/6/2022/2022-WCQ----UEFA-M-Stats"
