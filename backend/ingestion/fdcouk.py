"""football-data.co.uk reader — direct static-CSV pulls.

This source is rate-limit-free static CSV, so we read it directly with pandas
rather than through soccerdata (whose TLS-spoofing downloader proved flaky here).
The spec sanctions "direct CSV pulls where simpler". soccerdata is still used for
FBref, where its scraper / rate-limiter / cache are essential.

CSV layout (per season per division), columns we use:
  Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR,
  HS/AS (shots), HST/AST (shots on target), HF/AF (fouls),
  HC/AC (corners), HY/AY (yellows), HR/AR (reds).
"""

from __future__ import annotations

import pandas as pd

BASE_URL = "https://www.football-data.co.uk/mmz4281"


def results_url(season: str, fdcouk_key: str) -> str:
    """e.g. season='2425', fdcouk_key='E0' -> the Premier League 24/25 CSV."""
    return f"{BASE_URL}/{season}/{fdcouk_key}.csv"


def read_results(season: str, fdcouk_key: str) -> pd.DataFrame:
    """Fetch one division-season's results. Rows missing both team names dropped.

    Raises on network/parse failure — callers decide whether to skip or abort.
    """
    df = pd.read_csv(results_url(season, fdcouk_key), encoding="latin-1")
    return df.dropna(subset=["HomeTeam", "AwayTeam"]).reset_index(drop=True)
