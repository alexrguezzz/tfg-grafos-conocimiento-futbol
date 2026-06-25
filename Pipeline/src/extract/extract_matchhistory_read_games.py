from pathlib import Path
import sys

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from extract_args import build_raw_output_path, parse_leagues_seasons_args
from src.pipeline.console_output import print_result  # noqa: E402


def build_output_path(leagues: list[str], seasons: list[str]):
    return build_raw_output_path("matchhistory", "games", "read_games", leagues, seasons)


def main() -> None:
    args = parse_leagues_seasons_args("Extrae partidos de MatchHistory para una o varias ligas y temporadas.")
    import soccerdata as sd

    leagues = args.leagues
    seasons = args.seasons
    print(f"\nLeyendo calendario historico desde MatchHistory: {', '.join(leagues)} / {', '.join(seasons)}...\n")
    matchhistory = sd.MatchHistory(leagues=leagues, seasons=seasons)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    matchhistory._session = session
    df = matchhistory.read_games().reset_index()
    date_col = "date" if "date" in df.columns else "Date" if "Date" in df.columns else None
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(date_col).reset_index(drop=True)
    output_path = build_output_path(leagues, seasons)
    df.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Filas", len(df), output_path)


if __name__ == "__main__":
    main()
