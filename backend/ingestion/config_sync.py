"""Sync the repo's source-of-truth soccerdata league config into the active
soccerdata CONFIG_DIR (default `~/soccerdata/config`).

The repo file (`soccerdata_config/league_dict.json`) is version-controlled;
the global config dir is where soccerdata actually reads from and where the
local page cache already lives. This merges the repo entries in (repo wins on
conflicts) without clobbering other leagues already configured there.

Run:  python -m ingestion.config_sync
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_LEAGUE_DICT = Path(__file__).resolve().parent / "soccerdata_config" / "league_dict.json"


def _config_dir() -> Path:
    """The directory soccerdata reads config from."""
    from soccerdata._config import CONFIG_DIR

    return Path(CONFIG_DIR)


def sync_league_dict() -> dict[str, dict]:
    """Merge repo league entries into the global league_dict.json. Idempotent."""
    repo_entries: dict[str, dict] = json.loads(REPO_LEAGUE_DICT.read_text("utf-8"))

    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "league_dict.json"

    existing: dict[str, dict] = {}
    if target.exists():
        existing = json.loads(target.read_text("utf-8"))
        # Back up once per change, only when content would actually change.
        merged_preview = {**existing, **repo_entries}
        if merged_preview != existing:
            (config_dir / "league_dict.json.bak").write_text(
                json.dumps(existing, indent=4), "utf-8"
            )

    merged = {**existing, **repo_entries}
    target.write_text(json.dumps(merged, indent=4), "utf-8")
    return merged


if __name__ == "__main__":
    merged = sync_league_dict()
    eng = sorted(k for k in merged if k.startswith("ENG"))
    print(f"league_dict synced -> {_config_dir() / 'league_dict.json'}")
    print(f"ENG leagues now configured ({len(eng)}): {eng}")
