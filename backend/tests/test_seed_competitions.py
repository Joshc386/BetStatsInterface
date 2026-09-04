"""Tests for the competition reference data.

The play-off rule is the one worth pinning. `players.backfill_season` routes a
schedule row whose `round` contains "play-off" to a Competition named
`f"{league} Play-offs"` — and if that competition is not seeded the row is
silently dropped into `unmatched`. That is exactly what happened to League One
and League Two: FBref carried 5 play-off fixtures per season per division, every
one with a game_id, and every backfill logged "5 unmatched" while nobody read it.

So the seeder must carry a play-offs competition for every division that HAS
play-offs, and none for the one that does not.
"""

from ingestion.seed_competitions import COMPETITIONS

_BY_NAME = {row[0]: row for row in COMPETITIONS}

# 3rd-6th contest two-legged semis and a Wembley final in each EFL division.
# The Premier League has no play-offs, so a competition for it would be fiction.
DIVISIONS_WITH_PLAYOFFS = ("Championship", "League One", "League Two")


def test_every_efl_division_has_a_playoffs_competition():
    for division in DIVISIONS_WITH_PLAYOFFS:
        name = f"{division} Play-offs"
        assert name in _BY_NAME, f"{name} not seeded — its fixtures will be dropped"


def test_playoffs_are_knockouts_that_no_csv_covers():
    """club_cup so play-off games never count as league form, and no fdcouk_key
    because football-data.co.uk does not publish the play-offs at all."""
    for division in DIVISIONS_WITH_PLAYOFFS:
        _name, ctype, country, tier, fdcouk_key, fbref_key = _BY_NAME[
            f"{division} Play-offs"
        ]
        assert ctype == "club_cup"
        assert country == "England"
        assert tier is None          # tier belongs to the league, not its play-offs
        assert fdcouk_key is None
        # No standalone FBref page either: the play-offs are sourced from THAT
        # division's own league schedule (round = "Promotion play-offs — ...").
        # A key here would send backfill_season looking for a page that is not
        # there, so its absence is load-bearing, not an oversight.
        assert fbref_key is None


def test_the_premier_league_has_no_playoffs():
    assert "Premier League Play-offs" not in _BY_NAME


def test_competition_names_are_unique():
    """The seeder upserts on name, so a duplicate would silently overwrite."""
    names = [row[0] for row in COMPETITIONS]
    assert len(names) == len(set(names))
