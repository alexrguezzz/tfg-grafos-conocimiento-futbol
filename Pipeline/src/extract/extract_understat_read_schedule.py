import pandas as pd
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from extract_args import build_raw_output_path, parse_leagues_seasons_args
from src.pipeline.console_output import print_result  # noqa: E402


def build_output_path(leagues: list[str], seasons: list[str]):
    return build_raw_output_path("understat", "schedule", "read_schedule", leagues, seasons)


def main() -> None:
    args = parse_leagues_seasons_args("Extrae calendarios de Understat para una o varias ligas y temporadas.")
    import soccerdata as sd

    leagues = args.leagues
    seasons = args.seasons
    print(f"\nLeyendo calendario de partidos desde Understat: {', '.join(leagues)} / {', '.join(seasons)}...\n")
    understat = sd.Understat(leagues=leagues, seasons=seasons)
    df = understat.read_schedule().reset_index()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
    output_path = build_output_path(leagues, seasons)
    df.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Filas", len(df), output_path)


if __name__ == "__main__":
    main()
