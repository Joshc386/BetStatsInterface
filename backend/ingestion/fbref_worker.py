"""Subprocess worker: fetch ONE FBref resource and pickle it to a file.

Run in isolation by ingestion.fbref_fetch so the parent can hard-kill the whole
process tree (python + uc_driver + its Chrome) if a Cloudflare challenge hangs.
Never run directly in the main process — that's how the multi-hour hangs happen.

argv: <json-spec> <output-pickle-path>
spec: {"leagues": [...], "seasons": [...], "kind": "schedule"|"player_match", "match_id": <id?>}
"""

import json
import logging
import sys

logging.disable(logging.CRITICAL)


def main() -> int:
    spec = json.loads(sys.argv[1])
    out_path = sys.argv[2]

    import soccerdata as sd

    fb = sd.FBref(
        leagues=spec["leagues"],
        seasons=spec["seasons"],
        headless=spec.get("headless", True),
    )
    kind = spec["kind"]
    if kind == "schedule":
        df = fb.read_schedule()
    elif kind == "player_match":
        df = fb.read_player_match_stats(stat_type="summary", match_id=spec["match_id"])
    else:
        raise ValueError(f"unknown kind {kind!r}")

    df.to_pickle(out_path)
    print(f"OK rows={df.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
