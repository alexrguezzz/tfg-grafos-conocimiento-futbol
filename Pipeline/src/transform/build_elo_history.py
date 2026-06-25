from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import normalize_team
from src.utils.season_scope import path_has_target_scope

RAW_REQUIRED_COLUMNS = ["from", "rank", "team", "level", "elo", "to"]


def build_raw_input_paths() -> list[Path]:
    raw_dir = PROJECT_ROOT / "data" / "raw" / "clubelo" / "team_history"
    return sorted(path for path in raw_dir.glob("read_team_history_*.csv") if path_has_target_scope(path))


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "elo_history.csv"


def validate_input_columns(df: pd.DataFrame) -> None:
    missing = [col for col in RAW_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {missing}")


def build_elo_record_id(row: pd.Series) -> str:
    date_part = "unknown_date"
    if pd.notna(row["dateFrom"]):
        date_part = pd.to_datetime(row["dateFrom"]).strftime("%Y-%m-%d")
    return f"{row['id_team']}_{date_part}"


def build_elo_history(df_raw: pd.DataFrame) -> pd.DataFrame:
    validate_input_columns(df_raw)

    df = df_raw.copy()
    if "id_team" in df.columns:
        df["id_team"] = df["id_team"].astype(str)
    elif "team_id_canonical" in df.columns:
        df["id_team"] = df["team_id_canonical"].astype(str)
    else:
        df["id_team"] = df["team"].apply(normalize_team)
    df["dateFrom"] = pd.to_datetime(df["from"], errors="coerce")
    df["dateTo"] = pd.to_datetime(df["to"], errors="coerce")

    for col in ["rank", "level", "elo"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["id_eloRecord"] = df.apply(build_elo_record_id, axis=1)

    df_elo = df[["id_eloRecord", "id_team", "dateFrom", "dateTo", "rank", "level", "elo"]].copy()
    return df_elo.sort_values(by=["id_team", "dateFrom"], na_position="last").reset_index(drop=True)


def main() -> None:
    parse_no_args("Construye el historico Elo canonico desde archivos raw de ClubElo, acotado si el alcance del pipeline esta activo.")
    input_paths = build_raw_input_paths()
    output_path = build_output_path()

    if not input_paths:
        raise FileNotFoundError("No existen archivos raw de ClubElo para construir elo_history")

    print("Consolidando historico Elo en formato canonico...")
    df_raw = pd.concat([pd.read_csv(path) for path in input_paths], ignore_index=True)
    df_elo = build_elo_history(df_raw)

    df_elo = df_elo.drop_duplicates(subset=["id_eloRecord"], keep="last")

    df_elo.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Registros Elo", len(df_elo), output_path)


if __name__ == "__main__":
    main()

