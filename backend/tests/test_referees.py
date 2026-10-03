"""Tests for referee capture (ADR 0018).

Parsing is pure and tested on synthetic officials lines shaped exactly like
FBref's cached match pages. Identity rules (alias folding, the near-duplicate
review list) are pure too: they decide nothing a human has not confirmed.
"""

from ingestion.referees import (
    CONFIRMED_DISTINCT,
    REFEREE_ALIASES,
    canonical_referee,
    near_duplicates,
    parse_referee,
)


def _page(officials: str) -> str:
    return (
        '<div class="scorebox_meta"><div><strong><small>Venue</small></strong>: '
        "<small>Keepmoat Stadium, Doncaster</small></div>"
        f"{officials}</div>"
    )


_LINE = (
    '<div><strong><small>Officials</small></strong>: <small>'
    '<span style="display:inline-block">{ref} (Referee)</span> · '
    '<span style="display:inline-block">Ivan Stankovic (AR1)</span> · '
    '<span style="display:inline-block">Bradley Hall (4th)</span></small></div>'
)


def test_reads_only_the_referee_not_the_assistants():
    assert parse_referee(_page(_LINE.format(ref="Andy Haines"))) == "Andy Haines"


def test_unescapes_html_entities_in_a_name():
    """Foreign referees in Europe carry accents, which FBref may entity-encode."""
    html = _page(_LINE.format(ref="Istv&aacute;n Kov&#225;cs"))
    assert parse_referee(html) == "István Kovács"


def test_collapses_stray_whitespace_and_nbsp():
    html = _page(_LINE.format(ref="  Samuel&nbsp; Barrott "))
    assert parse_referee(html) == "Samuel Barrott"


def test_no_officials_line_is_none_not_an_error():
    """25 cached league/cup pages name no referee: an honest NULL."""
    assert parse_referee(_page("")) is None


def test_officials_line_without_a_referee_entry_is_none():
    html = _page(
        '<div><strong><small>Officials</small></strong>: <small>'
        '<span style="display:inline-block">Ivan Stankovic (AR1)</span></small></div>'
    )
    assert parse_referee(html) is None


def test_alias_folds_a_confirmed_second_spelling():
    """Hand-confirmed: one man printed "Sam" on 5 pages, "Samuel" on 171."""
    assert canonical_referee("Sam Allison") == "Samuel Allison"


def test_unaliased_name_passes_through_unchanged():
    assert canonical_referee("Andy Madley") == "Andy Madley"


def test_near_duplicates_flags_short_and_initialled_first_names():
    names = ["Samuel Barrott", "Sam Barrott", "S. Barrott", "Andy Haines"]
    pairs = near_duplicates(names)
    assert ("Sam Barrott", "Samuel Barrott") in pairs
    assert ("S. Barrott", "Samuel Barrott") in pairs
    assert not any("Andy Haines" in p for p in pairs)


def test_near_duplicates_flags_accent_only_differences():
    assert ("Istvan Kovacs", "István Kovács") in near_duplicates(
        ["István Kovács", "Istvan Kovacs"]
    )


def test_near_duplicates_flags_shared_stem_first_names():
    """Steve / Stephen Martin: neither name is a prefix of the other, but a
    shared 3-letter stem is enough to put the pair in front of a human."""
    assert ("Stephen Martin", "Steve Martin") in near_duplicates(
        ["Steve Martin", "Stephen Martin"]
    )


def test_near_duplicates_flags_a_dropped_middle_name():
    assert ("Artur Dias", "Artur Soares Dias") in near_duplicates(
        ["Artur Soares Dias", "Artur Dias"]
    )


def test_near_duplicates_ignores_unrelated_first_names():
    """Elliot and James Bell refereed on 22 of the same days: two men. A shared
    surname alone is not a near-duplicate (CONTEXT.md "Referee"), and listing
    every Hernandez would bury the pairs that matter."""
    assert near_duplicates(["Elliot Bell", "James Bell"]) == []
    assert near_duplicates(["Andy Madley", "Robert Madley"]) == []


def test_near_duplicates_never_flags_different_surnames():
    assert near_duplicates(["Ben Atkinson", "Ben Toner"]) == []


def test_aliases_never_chain():
    """A canonical spelling is never itself aliased onward: one lookup is final."""
    assert not set(REFEREE_ALIASES.values()) & set(REFEREE_ALIASES)


def test_confirmed_distinct_pairs_are_sorted_like_the_review_list():
    """Otherwise a ruling silently fails to suppress its pair."""
    assert all(tuple(sorted(p)) == p for p in CONFIRMED_DISTINCT)
