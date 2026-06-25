from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from extract_args import build_raw_output_path, parse_leagues_seasons_args
from src.pipeline.console_output import print_result  # noqa: E402


def build_output_path(leagues: list[str], seasons: list[str]):
    return build_raw_output_path("understat", "team_match_stats", "read_team_match_stats", leagues, seasons)


def main() -> None:
    args = parse_leagues_seasons_args("Extrae estadisticas de equipo por partido de Understat para una o varias ligas y temporadas.")
    import soccerdata as sd

    leagues = args.leagues
    seasons = args.seasons
    print(f"\nLeyendo estadisticas de equipo por partido desde Understat: {', '.join(leagues)} / {', '.join(seasons)}...\n")
    understat = sd.Understat(leagues=leagues, seasons=seasons)
    df = understat.read_team_match_stats().reset_index()
    output_path = build_output_path(leagues, seasons)
    df.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Filas", len(df), output_path)


if __name__ == "__main__":
    main()
