from __future__ import annotations

from pathlib import Path
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.transform.player_normalization import map_player_ids
from src.utils.text_normalization import normalize_competition, normalize_season, normalize_team
from src.utils.season_scope import filter_target_id_seasons, filter_target_seasons, path_has_target_scope


def list_raw_files(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "player_competition_season_stats.csv"


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


def build_player_competition_season_stats() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in list_raw_files("understat", "player_season_stats", pattern="read_player_season_stats_*.csv"):
        df = filter_target_seasons(pd.read_csv(path))
        if df.empty:
            continue
        frame = pd.DataFrame()
        frame["id_team"] = df["team"].astype(str).apply(normalize_team)
        frame["id_competition"] = df["league"].apply(normalize_competition)
        frame["id_season"] = df["season"].apply(normalize_season)
        frame = filter_target_id_seasons(frame)
        frame["id_player"] = map_player_ids(
            df,
            source="understat",
            player_col="player",
            source_player_id_col="player_id",
            team_col="team",
            competition_col="league",
            season_col="season",
        )
        frame["id_playerCompetitionSeasonStats"] = (
            frame["id_competition"].astype(str)
            + "_"
            + frame["id_season"].astype(str)
            + "_"
            + frame["id_player"].astype(str)
        )
        stat_columns = {
            "matches": "matches",
            "minutes": "minutes",
            "goals": "goals",
            "xg": "xg",
            "np_goals": "nonPenaltyGoals",
            "np_xg": "nonPenaltyXg",
            "assists": "assists",
            "xa": "xa",
            "shots": "shots",
            "key_passes": "keyPasses",
            "yellow_cards": "yellowCards",
            "red_cards": "redCards",
            "xg_chain": "xgChain",
            "xg_buildup": "xgBuildup",
        }
        for source_col, target_col in stat_columns.items():
            frame[target_col] = pd.to_numeric(df[source_col], errors="coerce")
        frames.append(frame)

    if not frames:
        raise FileNotFoundError("No existen archivos raw para construir player_competition_season_stats")

    df = pd.concat(frames, ignore_index=True)
    df = df[df["id_player"].notna()].copy()
    duplicate_ids = df["id_playerCompetitionSeasonStats"].duplicated(keep=False)
    if duplicate_ids.any():
        team_counts = (
            df.loc[duplicate_ids]
            .groupby("id_playerCompetitionSeasonStats", dropna=False)["id_team"]
            .nunique(dropna=False)
        )
        conflicting_ids = set(team_counts[team_counts > 1].index.astype(str))
        if conflicting_ids:
            duplicated = df.loc[
                df["id_playerCompetitionSeasonStats"].isin(conflicting_ids),
                ["id_playerCompetitionSeasonStats", "id_player", "id_team", "id_competition", "id_season"],
            ].head(20)
            raise ValueError(
                "El nuevo id_playerCompetitionSeasonStats "
                "{competition}_{season}_{player} apunta a mas de un equipo. "
                f"Ejemplos: {duplicated.to_dict(orient='records')}"
            )
    df = df.drop_duplicates(subset=["id_playerCompetitionSeasonStats"], keep="last")
    df = df.sort_values(
        by=["id_competition", "id_season", "id_team", "id_player"]
    ).reset_index(drop=True)

    ordered_columns = [
        "id_playerCompetitionSeasonStats",
        "id_player",
        "id_team",
        "id_competition",
        "id_season",
        "matches",
        "minutes",
        "goals",
        "xg",
        "nonPenaltyGoals",
        "nonPenaltyXg",
        "assists",
        "xa",
        "shots",
        "keyPasses",
        "yellowCards",
        "redCards",
        "xgChain",
        "xgBuildup",
    ]
    return df[ordered_columns]


def main() -> None:
    parse_no_args("Construye estadisticas canonicas de jugador por competicion-temporada.")
    output_path = build_output_path()

    print("Generando estadisticas canonicas de jugador por temporada...")
    df = build_player_competition_season_stats()
    df.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Estadisticas jugador-temporada", len(df), output_path)


if __name__ == "__main__":
    main()

