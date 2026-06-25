from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))
EXTRACT_MODULE_DIR = PROJECT_ROOT / "src" / "extract"
if str(EXTRACT_MODULE_DIR) not in sys.path:
    sys.path.append(str(EXTRACT_MODULE_DIR))

from extract_args import build_raw_output_path, parse_leagues_seasons_args
from src.pipeline.console_output import print_audit, print_examples, print_result, print_warning  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402


FAILURE_COLUMNS = [
    "league",
    "season",
    "game",
    "game_id",
    "date",
    "home_team",
    "away_team",
    "url",
    "first_error",
    "first_message",
    "retry_error",
    "retry_message",
]


def build_output_path(leagues: list[str], seasons: list[str]):
    return build_raw_output_path("understat", "player_match_stats", "read_player_match_stats", leagues, seasons)


def exception_name(exc: Exception) -> str:
    return type(exc).__name__


def exception_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def row_value(row: pd.Series, column: str) -> object:
    if column not in row.index:
        return pd.NA
    return row[column]


def failure_record(row: pd.Series, first_error: Exception, retry_error: Exception) -> dict[str, object]:
    return {
        "league": row_value(row, "league"),
        "season": row_value(row, "season"),
        "game": row_value(row, "game"),
        "game_id": row_value(row, "game_id"),
        "date": row_value(row, "date"),
        "home_team": row_value(row, "home_team"),
        "away_team": row_value(row, "away_team"),
        "url": row_value(row, "url"),
        "first_error": exception_name(first_error),
        "first_message": exception_message(first_error),
        "retry_error": exception_name(retry_error),
        "retry_message": exception_message(retry_error),
    }


def format_failure_example(record: dict[str, object]) -> str:
    return (
        f"Partido: {record.get('game', '-')}; "
        f"game_id={record.get('game_id', '-')}; "
        f"url={record.get('url', '-')}; "
        f"error={record.get('retry_error', '-')}: {record.get('retry_message', '-')}"
    )


def require_non_empty_match_stats(reader: object, game_id: int) -> pd.DataFrame:
    df = reader.read_player_match_stats(match_id=game_id)
    if df is None or df.empty:
        raise ValueError(f"Understat no devolvio filas de player_match_stats para game_id={game_id}")
    return df


def concat_match_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    combined = pd.concat(frames, ignore_index=False)
    if any(name is not None for name in combined.index.names):
        return combined.reset_index()
    return combined.reset_index(drop=True)


def read_player_match_stats_with_failures(
    primary_reader: object,
    retry_reader_factory: Callable[[], object],
    schedule: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if schedule is None:
        schedule = primary_reader.read_schedule(include_matches_without_data=False).reset_index()
    elif "game_id" not in schedule.columns:
        schedule = schedule.reset_index()

    if "game_id" not in schedule.columns:
        raise ValueError("El calendario de Understat no contiene la columna game_id")

    frames: list[pd.DataFrame] = []
    failures: list[dict[str, object]] = []

    for _, row in schedule.iterrows():
        game_id_raw = row_value(row, "game_id")
        if pd.isna(game_id_raw):
            continue
        game_id = int(float(game_id_raw))

        try:
            frames.append(require_non_empty_match_stats(primary_reader, game_id))
            continue
        except Exception as first_error:
            try:
                retry_reader = retry_reader_factory()
                frames.append(require_non_empty_match_stats(retry_reader, game_id))
                continue
            except Exception as retry_error:
                failures.append(failure_record(row, first_error, retry_error))

    if not frames:
        raise ValueError("No se pudo extraer player_match_stats de Understat para ningun partido")

    return concat_match_frames(frames), pd.DataFrame(failures, columns=FAILURE_COLUMNS)


def run_extraction() -> dict:
    args = parse_leagues_seasons_args("Extrae estadisticas de jugador por partido de Understat para una o varias ligas y temporadas.")
    import soccerdata as sd

    leagues = args.leagues
    seasons = args.seasons
    print(f"\nLeyendo estadisticas de jugador por partido desde Understat: {', '.join(leagues)} / {', '.join(seasons)}...\n")
    understat = sd.Understat(leagues=leagues, seasons=seasons)
    df, failed_matches = read_player_match_stats_with_failures(
        understat,
        lambda: sd.Understat(leagues=leagues, seasons=seasons, no_cache=True),
    )
    output_path = build_output_path(leagues, seasons)
    df.to_csv(output_path, index=False, encoding="utf-8")

    failed_count = len(failed_matches)
    warnings = []
    if failed_count:
        warning = (
            f"{failed_count} partido(s) de Understat player_match_stats no se pudieron extraer "
            "tras reintento sin cache y se omiten del CSV raw."
        )
        print_warning(warning)
        print_examples(format_failure_example(row) for row in failed_matches.to_dict("records"))
        warnings.append(warning)

    print_result("Filas", len(df), output_path)
    print_audit("Partidos Understat player_match_stats omitidos", failed_count)
    return {
        "league": leagues[0] if len(leagues) == 1 else None,
        "season": seasons[0] if len(seasons) == 1 else None,
        "output_files": [output_path],
        "warnings": warnings,
        "metrics": {
            "rows": len(df),
            "failed_matches": failed_count,
        },
    }


def main() -> None:
    run_with_optional_task_result("extract_understat_read_player_match_stats", "extract", run_extraction)


if __name__ == "__main__":
    main()
