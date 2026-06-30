"""Name normalisation helpers for cross-source reconciliation.

Light, deterministic cleaning only. Genuine cross-source aliasing (football-data
display names vs FBref names) is handled by an explicit alias map introduced when
FBref ingestion lands (Phase 4) — not by fuzzy matching.
"""

from __future__ import annotations

import re
import unicodedata


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
