from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pandas as pd

from src.utils.text_normalization import normalize_competition, normalize_season


TARGET_LEAGUES_ENV = "SOCCERDATA_PIPELINE_LEAGUES"
TARGET_SEASONS_ENV = "SOCCERDATA_PIPELINE_SEASONS"


def _help_requested() -> bool:
    return any(arg in {"-h", "--help"} for arg in sys.argv[1:])


def _scope_env_list(name: str) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if not raw_value:
        return ()

    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Lista JSON no valida en {name}: {raw_value}") from exc

    if not isinstance(decoded, list):
        raise RuntimeError(f"{name} must be a JSON list.")

    values = tuple(str(value).strip() for value in decoded if str(value).strip())
    if not values:
        raise RuntimeError(f"{name} must contain at least one value.")
    return values


def _file_fragment(value: str) -> str:
    return str(value).strip().replace(" ", "_").replace("/", "-")


TARGET_LEAGUES = _scope_env_list(TARGET_LEAGUES_ENV)
TARGET_SEASONS = _scope_env_list(TARGET_SEASONS_ENV)

if bool(TARGET_LEAGUES) != bool(TARGET_SEASONS) and not _help_requested():
    raise RuntimeError(
        f"{TARGET_LEAGUES_ENV} and {TARGET_SEASONS_ENV} must be set together. "
        "Leave both unset to process all raw files, or run through src/pipeline/run_pipeline.py."
    )

SCOPE_ACTIVE = bool(TARGET_LEAGUES and TARGET_SEASONS)
TARGET_COMPETITIONS = tuple(normalize_competition(league) for league in TARGET_LEAGUES)
TARGET_COMPETITION_SET = set(TARGET_COMPETITIONS)
TARGET_LEAGUE_FILE_FRAGMENTS = tuple(_file_fragment(league) for league in TARGET_LEAGUES)
TARGET_SEASON_SET = set(TARGET_SEASONS)


def _is_target_season(value: object) -> bool:
    if pd.isna(value):
        return False
    return normalize_season(value) in TARGET_SEASON_SET


def _is_target_competition(value: object) -> bool:
    if pd.isna(value):
        return False
    return normalize_competition(value) in TARGET_COMPETITION_SET


def filter_target_seasons(
    df: pd.DataFrame,
    season_column: str = "season",
    competition_column: str = "league",
) -> pd.DataFrame:
    if not SCOPE_ACTIVE:
        return df.copy()
    if season_column not in df.columns:
        return df.iloc[0:0].copy()

    mask = df[season_column].apply(_is_target_season)
    if competition_column in df.columns:
        mask = mask & df[competition_column].apply(_is_target_competition)
    return df.loc[mask].copy()


def filter_target_id_seasons(
    df: pd.DataFrame,
    season_column: str = "id_season",
    competition_column: str = "id_competition",
) -> pd.DataFrame:
    if not SCOPE_ACTIVE:
        return df.copy()
    if season_column not in df.columns:
        return df.iloc[0:0].copy()

    mask = df[season_column].astype(str).isin(TARGET_SEASON_SET)
    if competition_column in df.columns:
        mask = mask & df[competition_column].astype(str).isin(TARGET_COMPETITION_SET)
    return df.loc[mask].copy()


def _path_has_target_season(path: Path) -> bool:
    name = path.name
    return any(season in name or season.replace("-", "_") in name for season in TARGET_SEASONS)


def _path_has_target_competition(path: Path) -> bool:
    name = path.name
    return any(fragment in name for fragment in TARGET_LEAGUE_FILE_FRAGMENTS)


def path_has_target_scope(path: Path) -> bool:
    if not SCOPE_ACTIVE:
        return True
    return _path_has_target_competition(path) and _path_has_target_season(path)


def target_seasons_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id_season": list(TARGET_SEASONS),
            "name": list(TARGET_SEASONS),
            "id_understat": [season.split("-", 1)[0] for season in TARGET_SEASONS],
        }
    )
