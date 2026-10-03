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


# --- single-match competitions (Community Shield) -------------------------

# Shaped like the 2024 Shield page: the scorebox spells "Manchester United",
# the stats-table caption (and every FBref schedule) "Manchester Utd". The home
# side has no stats table here, exercising the scorebox fallback.
_MATCH_PAGE = b"""
<html><body><div class="scorebox">
  <div><strong><a href="/en/squads/b8fd03ef/Manchester-City-Stats">Manchester City</a></strong></div>
  <div><strong><a href="/en/squads/19538871/Manchester-United-Stats">Manchester United</a></strong></div>
  <span class="venuetime" data-venue-date="2024-08-10" data-venue-time="15:00"></span>
</div>
<table id="stats_19538871_summary"><caption>Manchester Utd Player Stats Table</caption></table>
</body></html>
"""


def _offline_fbref(monkeypatch, seasons_url: str):
    """A shim reader whose season index holds one edition linking ``seasons_url``
    and whose fetches are recorded rather than made."""
    import io

    import pandas as pd
    from soccerdata._common import BaseSeleniumReader

    monkeypatch.setattr(BaseSeleniumReader, "_init_webdriver", lambda self: None)
    from ingestion.fbref_shim import FBref

    fb = FBref(leagues="ENG-Premier League", seasons=["2324"], headless=True)
    index = pd.MultiIndex.from_tuples([("ENG-Premier League", "2324")],
                                      names=["league", "season"])
    seasons = pd.DataFrame({"format": ["elimination"], "url": [seasons_url]}, index=index)
    monkeypatch.setattr(fb, "read_seasons", lambda split_up_big5=False: seasons)
    fetched = []

    def fake_get(url, filepath, **kw):
        fetched.append((url, filepath.name))
        return io.BytesIO(_MATCH_PAGE)

    monkeypatch.setattr(fb, "get", fake_get)
    return fb, fetched


def test_single_match_schedule_is_built_from_the_match_page(monkeypatch):
    """A season linking straight to a match report becomes a one-row schedule,
    fetched into match_<id>.html — the file read_player_match_stats reads, so
    the page is fetched once. Names use the schedule spelling the cup path
    pairs against the page's captions."""
    fb, fetched = _offline_fbref(
        monkeypatch, "/en/matches/923aa854/Manchester-City-Manchester-United-August-10-2024"
    )
    df = fb.read_schedule().reset_index()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["game_id"] == "923aa854"
    assert row["home_team"] == "Manchester City"  # no stats table: scorebox name
    assert row["away_team"] == "Manchester Utd"  # caption, not "Manchester United"
    assert row["date"].date().isoformat() == "2024-08-10"
    assert row["game"] == "2024-08-10 Manchester City-Manchester Utd"
    assert row["match_report"].startswith("/en/matches/923aa854/")
    assert [name for _url, name in fetched] == ["match_923aa854.html"]


def test_a_competition_with_season_pages_keeps_the_stock_schedule(monkeypatch):
    """Every other competition links seasons to a season page and must take the
    stock read_schedule untouched."""
    import soccerdata as sd

    fb, fetched = _offline_fbref(monkeypatch, "/en/comps/9/2023-2024/Stats")
    monkeypatch.setattr(sd.FBref, "read_schedule", lambda self, force_cache=False: "stock")
    assert fb.read_schedule() == "stock"
    assert fetched == []


def _seasons_page(*rows: tuple[str, str]) -> bytes:
    cells = "".join(
        f'<tr><th data-stat="year_id"><a href="{href}">{year}</a></th></tr>'
        for year, href in rows
    )
    return f'<html><body><table id="seasons"><tbody>{cells}</tbody></table></body></html>'.encode()


def _offline_seasons(monkeypatch, page: bytes, seasons: list[str]):
    """A shim reader whose competition history page is ``page``."""
    import io

    import pandas as pd
    from soccerdata._common import BaseSeleniumReader

    monkeypatch.setattr(BaseSeleniumReader, "_init_webdriver", lambda self: None)
    from ingestion.fbref_shim import FBref

    fb = FBref(leagues="ENG-Premier League", seasons=seasons, headless=True)
    leagues = pd.DataFrame({"url": ["/en/comps/602/history/x"]},
                           index=pd.Index(["ENG-Premier League"], name="league"))
    monkeypatch.setattr(fb, "read_leagues", lambda split_up_big5=False: leagues)
    monkeypatch.setattr(fb, "get", lambda url, filepath, **kw: io.BytesIO(page))
    return fb


def test_single_match_editions_map_to_the_season_they_open(monkeypatch):
    """FBref labels a single-match edition by calendar year. Stock parsing reads
    "2021" as the season code 20-21 (and 2020 also as 20-21), so the 2020 and
    2021 Community Shields collided and 2021-22 vanished. Year Y opens Y/Y+1."""
    page = _seasons_page(
        ("2021", "/en/matches/6a1fc101/Leicester-City-Manchester-City-August-7-2021"),
        ("2020", "/en/matches/1f445267/Arsenal-Liverpool-August-29-2020"),
    )
    fb = _offline_seasons(monkeypatch, page, ["2021", "2122"])
    urls = fb.read_seasons()["url"].droplevel("league")
    assert urls["2021"].startswith("/en/matches/1f445267/")
    assert urls["2122"].startswith("/en/matches/6a1fc101/")


def test_season_pages_are_not_treated_as_single_match(monkeypatch):
    """A competition whose seasons link to season pages is left to the stock
    parser."""
    page = _seasons_page(("2023-2024", "/en/comps/9/2023-2024/Stats"))
    fb = _offline_seasons(monkeypatch, page, ["2324"])
    assert fb._single_match_seasons(False) is None


_SHIELD_SEASONS = Path.home() / "soccerdata/data/FBref/seasons_ENG-Community Shield.html"
_SHIELD_2023 = Path.home() / "soccerdata/data/FBref/match_10e5c045.html"


@pytest.mark.skipif(
    not (_SHIELD_SEASONS.exists() and _SHIELD_2023.exists()),
    reason="Community Shield FBref cache not present",
)
def test_community_shield_schedule_from_real_cached_pages(monkeypatch):
    """End-to-end on the real cached season index + 2023 match page, webdriver
    no-op'd so nothing is fetched."""
    from soccerdata._common import BaseSeleniumReader

    monkeypatch.setattr(BaseSeleniumReader, "_init_webdriver", lambda self: None)
    from ingestion.fbref_shim import FBref

    fb = FBref(leagues="ENG-Community Shield", seasons=["2324"], headless=True)
    df = fb.read_schedule().reset_index()
    assert list(df["game_id"]) == ["10e5c045"]
    assert list(df["home_team"]) == ["Arsenal"]
    assert list(df["away_team"]) == ["Manchester City"]

    # every held edition resolves to its own match, opening the right season
    fb = FBref(
        leagues="ENG-Community Shield",
        seasons=["2021", "2122", "2223", "2324", "2425", "2526", "2627"],
        headless=True,
    )
    ids = {
        season: url.split("/")[3]
        for (_league, season), url in fb.read_seasons()["url"].items()
    }
    assert ids == {
        "2021": "1f445267", "2122": "6a1fc101", "2223": "341bf7a1",
        "2324": "10e5c045", "2425": "923aa854", "2526": "096e63eb",
        "2627": "d5932b03",
    }
