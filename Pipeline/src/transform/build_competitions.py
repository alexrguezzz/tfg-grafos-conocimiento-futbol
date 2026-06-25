from __future__ import annotations

from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import competition_display_name, normalize_competition
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


def list_raw_files(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "competitions.csv"


def build_competitions() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in list_raw_files("sofascore", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frame = pd.DataFrame(
            {
                "id_competition": df["league"].apply(normalize_competition),
                "name": df["league"].apply(competition_display_name).astype("string"),
                "idUnderstat": pd.NA,
            }
        )
        frames.append(frame)

    for path in list_raw_files("understat", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frame = pd.DataFrame(
            {
                "id_competition": df["league"].apply(normalize_competition),
                "name": df["league"].apply(competition_display_name).astype("string"),
                "idUnderstat": df["league_id"].apply(parse_source_id).astype("string"),
            }
        )
        frames.append(frame)

    if not frames:
        raise FileNotFoundError("No existen archivos raw para construir competitions")

    competitions = pd.concat(frames, ignore_index=True)
    competitions = (
        competitions.groupby("id_competition", dropna=False)
        .agg(name=("name", first_non_empty), idUnderstat=("idUnderstat", first_non_empty))
        .reset_index()
    )
    ordered_columns = ["id_competition", "name", "idUnderstat"]
    return competitions[ordered_columns].sort_values(by=["name", "id_competition"]).reset_index(drop=True)


def main() -> None:
    parse_no_args("Construye competiciones canonicas desde archivos raw, acotadas si el alcance del pipeline esta activo.")
    output_path = build_output_path()

    print("Generando catalogo canonico de competiciones...")
    competitions = build_competitions()
    competitions.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Competiciones", len(competitions), output_path)


if __name__ == "__main__":
    main()

