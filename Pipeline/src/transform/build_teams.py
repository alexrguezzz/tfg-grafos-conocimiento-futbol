from __future__ import annotations

from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import canonicalize_team_name, normalize_team
from src.utils.season_scope import filter_target_seasons, path_has_target_scope


def parse_source_id(value) -> str | None:
    if pd.isna(value):
        return None

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None

    try:
        return str(int(float(text)))
    except Exception:
        return text


def first_non_empty(series: pd.Series):
    for value in series:
        if pd.isna(value):
            continue
        text = str(value).strip() if isinstance(value, str) else value
        if text == "":
            continue
        return value
    return pd.NA


TEAM_COLUMNS = ["id_team", "name", "teamCode", "country", "idUnderstat", "idWhoscored"]


def list_raw_files(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "teams.csv"


def canonical_name_series(series: pd.Series) -> pd.Series:
    return series.astype("string").map(canonicalize_team_name)


def build_teams() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in list_raw_files("sofascore", "league_table", pattern="read_league_table_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frames.append(
            pd.DataFrame(
                    {
                        "id_team": df["team"].apply(normalize_team),
                        "name": canonical_name_series(df["team"]),
                        "teamCode": pd.NA,
                        "country": pd.NA,
                        "idUnderstat": pd.NA,
                        "idWhoscored": pd.NA,
                    }
                )
            )

    for path in list_raw_files("sofascore", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        for side in ["home", "away"]:
            frames.append(
                pd.DataFrame(
                    {
                        "id_team": df[f"{side}_team"].apply(normalize_team),
                        "name": canonical_name_series(df[f"{side}_team"]),
                        "teamCode": pd.NA,
                        "country": pd.NA,
                        "idUnderstat": pd.NA,
                        "idWhoscored": pd.NA,
                    }
                )
            )

    for path in list_raw_files("understat", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        for side in ["home", "away"]:
            frames.append(
                pd.DataFrame(
                    {
                        "id_team": df[f"{side}_team"].apply(normalize_team),
                        "name": canonical_name_series(df[f"{side}_team"]),
                        "teamCode": df[f"{side}_team_code"].astype("string"),
                        "country": pd.NA,
                        "idUnderstat": df[f"{side}_team_id"].apply(parse_source_id).astype("string"),
                        "idWhoscored": pd.NA,
                    }
                )
            )

    for path in list_raw_files("whoscored", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        for side in ["home", "away"]:
            frames.append(
                pd.DataFrame(
                    {
                        "id_team": df[f"{side}_team"].apply(normalize_team),
                        "name": canonical_name_series(df[f"{side}_team"]),
                        "teamCode": pd.NA,
                        "country": df[f"{side}_team_country_name"].astype("string"),
                        "idUnderstat": pd.NA,
                        "idWhoscored": df[f"{side}_team_id"].apply(parse_source_id).astype("string"),
                    }
                )
            )

    if not frames:
        raise FileNotFoundError("No existen archivos raw para construir teams")

    teams = pd.concat(frames, ignore_index=True)
    teams = (
        teams.groupby("id_team", dropna=False)
        .agg(
            name=("name", first_non_empty),
            teamCode=("teamCode", first_non_empty),
            country=("country", first_non_empty),
            idUnderstat=("idUnderstat", first_non_empty),
            idWhoscored=("idWhoscored", first_non_empty),
        )
        .reset_index()
    )
    return teams[TEAM_COLUMNS].sort_values(by=["name", "id_team"]).reset_index(drop=True)


def main() -> None:
    parse_no_args("Construye equipos canonicos desde archivos raw, acotados si el alcance del pipeline esta activo.")
    output_path = build_output_path()

    print("Generando catalogo canonico de equipos...")
    teams = build_teams()
    teams.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Equipos", len(teams), output_path)


if __name__ == "__main__":
    main()

