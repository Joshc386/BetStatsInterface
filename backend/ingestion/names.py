"""Name normalisation helpers for cross-source reconciliation.

Light, deterministic cleaning only. Genuine cross-source aliasing (football-data
display names vs FBref names) is handled by an explicit alias map introduced when
FBref ingestion lands (Phase 4) — not by fuzzy matching.
"""

from __future__ import annotations

import re


def clean_name(name: str) -> str:
    """Collapse whitespace and strip. Deterministic and idempotent."""
    return re.sub(r"\s+", " ", str(name).strip())
