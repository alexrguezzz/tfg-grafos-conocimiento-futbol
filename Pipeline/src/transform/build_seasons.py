from __future__ import annotations

from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import denormalize_season_label, normalize_season
from src.utils.season_scope import filter_target_seasons, path_has_target_scope, target_seasons_frame


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
    return output_dir / "seasons.csv"


def build_seasons() -> pd.DataFrame:
    frames: list[pd.DataFrame] = [target_seasons_frame()]

    for path in list_raw_files("sofascore", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frame = pd.DataFrame(
            {
                "id_season": df["season"].apply(normalize_season),
                "name": df["season"].apply(denormalize_season_label).astype("string"),
                "id_understat": pd.NA,
            }
        )
        frames.append(frame)

    for path in list_raw_files("understat", "schedule", pattern="read_schedule_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frame = pd.DataFrame(
            {
                "id_season": df["season"].apply(normalize_season),
                "name": df["season"].apply(denormalize_season_label).astype("string"),
                "id_understat": df["season_id"].apply(parse_source_id).astype("string"),
            }
        )
        frames.append(frame)

    seasons = pd.concat(frames, ignore_index=True)
    seasons = (
        seasons.groupby("id_season", dropna=False)
        .agg(name=("name", first_non_empty), id_understat=("id_understat", first_non_empty))
        .reset_index()
    )
    ordered_columns = ["id_season", "name", "id_understat"]
    return seasons[ordered_columns].sort_values(by=["id_season"]).reset_index(drop=True)


def main() -> None:
    parse_no_args("Construye temporadas canonicas desde archivos raw, acotadas si el alcance del pipeline esta activo.")
    output_path = build_output_path()

    print("Generando catalogo canonico de temporadas...")
    seasons = build_seasons()
    seasons.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Temporadas", len(seasons), output_path)


if __name__ == "__main__":
    main()

