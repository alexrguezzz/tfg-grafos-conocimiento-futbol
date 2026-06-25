from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import normalize_season, normalize_competition, normalize_team
from src.utils.season_scope import filter_target_id_seasons, path_has_target_scope

RAW_REQUIRED_COLUMNS = ["league", "season", "team", "MP", "W", "D", "L", "GF", "GA", "GD", "Pts"]


def build_raw_input_paths() -> list[Path]:
    raw_dir = PROJECT_ROOT / "data" / "raw" / "sofascore" / "league_table"
    return sorted(path for path in raw_dir.glob("read_league_table_*.csv") if path_has_target_scope(path))


def build_output_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    output_dir = project_root / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "team_competition_season.csv"


def validate_input_columns(df: pd.DataFrame) -> None:
    missing = [col for col in RAW_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {missing}")


def build_team_competition_season_id(row: pd.Series) -> str:
    return f"{row['id_competition']}_{row['id_season']}_{row['id_team']}"


def build_team_competition_season(df_raw: pd.DataFrame) -> pd.DataFrame:
    validate_input_columns(df_raw)

    df = df_raw[RAW_REQUIRED_COLUMNS].copy().reset_index(drop=True)
    df["id_competition"] = df["league"].apply(normalize_competition)
    df["id_season"] = df["season"].apply(normalize_season)
    df = filter_target_id_seasons(df)
    df["id_team"] = df["team"].apply(normalize_team)

    stat_columns = {
        "MP": "matchesPlayed",
        "W": "wins",
        "D": "draws",
        "L": "losses",
        "GF": "goalsFor",
        "GA": "goalsAgainst",
        "GD": "goalDifference",
        "Pts": "points",
    }
    for source_col, target_col in stat_columns.items():
        df[target_col] = pd.to_numeric(df[source_col], errors="coerce")

    df["position"] = df.groupby(["id_competition", "id_season"]).cumcount() + 1
    df["id_teamCompetitionSeason"] = df.apply(build_team_competition_season_id, axis=1)

    df_tcs = df[[
        "id_teamCompetitionSeason", "id_team", "id_competition",
        "id_season", "position", "matchesPlayed", "wins", "draws", "losses",
        "goalsFor", "goalsAgainst", "goalDifference", "points"
    ]].copy()
    return df_tcs


def main() -> None:
    parse_no_args("Construye registros canonicos equipo-competicion-temporada desde tablas raw de SofaScore, acotados si el alcance del pipeline esta activo.")
    input_paths = build_raw_input_paths()
    output_path = build_output_path()

    if not input_paths:
        raise FileNotFoundError("No existen archivos raw de league_table para construir team_competition_season")

    print("Vinculando equipos con competiciones y temporadas...")
    df_raw = pd.concat([pd.read_csv(path) for path in input_paths], ignore_index=True)
    df_tcs = build_team_competition_season(df_raw)

    df_tcs = df_tcs.drop_duplicates(subset=["id_teamCompetitionSeason"], keep="last")
    df_tcs = df_tcs.sort_values(
        by=["id_competition", "id_season", "position", "id_team"],
        na_position="last",
    ).reset_index(drop=True)

    df_tcs.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Equipos por competicion-temporada", len(df_tcs), output_path)


if __name__ == "__main__":
    main()

