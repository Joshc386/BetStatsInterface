"""Name normalisation helpers for cross-source reconciliation.

Light, deterministic cleaning only. Genuine cross-source aliasing (football-data
display names vs FBref names) is handled by an explicit alias map introduced when
FBref ingestion lands (Phase 4) — not by fuzzy matching.
"""

from __future__ import annotations

import re
import unicodedata


# Generic club-name tokens carried by so many clubs that they cannot identify
# one. At European volume, first-word matching alone would deadlock on unrelated
# clubs ("FC Porto" vs "FC Copenhagen"). The guard compares the first NON-generic
# token instead — which also sharpens it domestically ("AFC Wimbledon" guards on
# "wimbledon", not "afc").
GENERIC_NAME_TOKENS = frozenset(
    "fc afc ac as cf sc sk fk sl ss rb cd rc sd us st 1 real sporting "
    "dinamo dynamo red inter aek".split()
)


def guard_token(name: str) -> str:
    """The first non-generic token of a club name, casefolded — the
    duplicate-spelling signature the auto-create guards compare on.

    Lives here so BOTH source resolvers share one implementation: the FBref path
    (`cups.resolve_or_create_fbref_team`, ADR 0007/0008) and the
    football-data.co.uk path (`teams.resolve_fdcouk_team`). It was FBref-only
    until 2026-08-23, when fd.co.uk spelled a relegated club differently in the
    League One CSV than the Championship one ("Sheffield Wed" / "Bradford City")
    and silently minted duplicate clubs — the exact failure this catches, on the
    one path that lacked it.
    """
    tokens = [t for t in re.split(r"[\s.]+", name) if t]
    for t in tokens:
        if t.casefold() not in GENERIC_NAME_TOKENS:
            return t.casefold()
    return tokens[0].casefold() if tokens else ""


def clean_name(name: str) -> str:
    """Collapse whitespace and strip. Deterministic and idempotent."""
    return re.sub(r"\s+", " ", str(name).strip())


# Club designators safe to drop for matching only. Deliberately NOT including
# "City"/"United"/"Town" etc. — those distinguish real clubs (Man United vs Man
# City) and are handled by the explicit alias map, never by normalisation.
_CLUB_TOKENS = {"fc", "afc"}


def normalise_for_match(name: str) -> str:
    """Aggressively fold a team name to a deterministic seam-matching key.

    Accent-fold, lowercase, drop the FC/AFC club tokens, collapse whitespace.
    For the cross-source seam matcher ONLY — never use it to store a display
    name (it is lossy). Deterministic and idempotent.
    """
    text = clean_name(name).lower()
    # accent-fold: decompose then drop combining marks (é -> e)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    tokens = [t for t in text.split() if t not in _CLUB_TOKENS]
    return " ".join(tokens)


# Standalone letters that accent-folding cannot reach. NFKD decomposes a base
# letter plus a combining mark (é -> e), but ð/ø/þ/ł are single characters with
# no decomposition, so they survive normalise_for_match untouched. That is
# exactly why the ESPN roster spike missed Andri Guðjohnsen and Lars-Jørgen
# Salvesen (ADR 0013). Player names carry these far more often than club names,
# so the fold is kept OUT of normalise_for_match — the team seam is working and
# is not worth disturbing.
_LETTER_FOLDS = str.maketrans(
    {
        "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ø": "o", "Ø": "o",
        "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "ß": "ss",
        "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ı": "i",
    }
)


def normalise_player_name(name: str) -> str:
    """Fold a player name to a deterministic seam-matching key.

    normalise_for_match plus the standalone letters above. Matcher-only and
    lossy, exactly like its base — never store the result as a display name.
    """
    return normalise_for_match(clean_name(name).translate(_LETTER_FOLDS))
