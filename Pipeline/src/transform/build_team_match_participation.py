from __future__ import annotations

from pathlib import Path
import re
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import normalize_team
from src.utils.season_scope import path_has_target_scope
from src.utils.datetime_normalization import date_part_series


ESPN_NUMERIC_COLUMNS = [
    "foulsCommitted",
    "yellowCards",
    "redCards",
    "offsides",
    "wonCorners",
    "saves",
    "possessionPct",
    "totalShots",
    "shotsOnTarget",
    "penaltyKickGoals",
    "penaltyKickShots",
    "accuratePasses",
    "totalPasses",
    "accurateCrosses",
    "totalCrosses",
    "totalLongBalls",
    "accurateLongBalls",
    "blockedShots",
    "effectiveTackles",
    "totalTackles",
    "interceptions",
    "totalClearance",
]

ESPN_RAW_TO_CANONICAL = {
    "fouls_committed": "foulsCommitted",
    "yellow_cards": "yellowCards",
    "red_cards": "redCards",
    "offsides": "offsides",
    "won_corners": "wonCorners",
    "saves": "saves",
    "possession_pct": "possessionPct",
    "total_shots": "totalShots",
    "shots_on_target": "shotsOnTarget",
    "penalty_kick_goals": "penaltyKickGoals",
    "penalty_kick_shots": "penaltyKickShots",
    "accurate_passes": "accuratePasses",
    "total_passes": "totalPasses",
    "accurate_crosses": "accurateCrosses",
    "total_crosses": "totalCrosses",
    "total_long_balls": "totalLongBalls",
    "accurate_long_balls": "accurateLongBalls",
    "blocked_shots": "blockedShots",
    "effective_tackles": "effectiveTackles",
    "total_tackles": "totalTackles",
    "interceptions": "interceptions",
    "total_clearance": "totalClearance",
}


def first_non_empty(series: pd.Series):
    for value in series:
        if pd.isna(value):
            continue
        text = str(value).strip() if isinstance(value, str) else value
        if text == "":
            continue
        return value
    return pd.NA


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "team_match_participation.csv"


def list_raw_files(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def parse_espn_game(value: str):
    if pd.isna(value):
        return None, None, None
    text = str(value).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", text)
    if not match:
        return None, None, None
    date_str = match.group(1)
    teams_part = match.group(2).strip()
    if "-" not in teams_part:
        return date_str, None, None
    home_team, away_team = teams_part.split("-", 1)
    return date_str, home_team.strip(), away_team.strip()


def build_match_key(date_series: pd.Series, home_series: pd.Series, away_series: pd.Series) -> pd.Series:
    date_part = date_part_series(date_series)
    home_part = home_series.astype(str).apply(normalize_team)
    away_part = away_series.astype(str).apply(normalize_team)
    return date_part + "|" + home_part + "|" + away_part


def build_base_participation(df_matches: pd.DataFrame) -> pd.DataFrame:
    required = ["id_match", "id_home_team", "id_away_team"]
    missing = [col for col in required if col not in df_matches.columns]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias en matches.csv: {missing}")

    records = []
    for _, row in df_matches.iterrows():
        match_id = row["id_match"]
        home_team_id = normalize_team(row["id_home_team"])
        away_team_id = normalize_team(row["id_away_team"])

        records.extend(
            [
                {
                    "id_teamMatchParticipation": f"{match_id}_{home_team_id}",
                    "id_match": match_id,
                    "id_team": home_team_id,
                    "isHome": True,
                },
                {
                    "id_teamMatchParticipation": f"{match_id}_{away_team_id}",
                    "id_match": match_id,
                    "id_team": away_team_id,
                    "isHome": False,
                },
            ]
        )

    return pd.DataFrame(records)


def build_espn_lookup(df_matches: pd.DataFrame) -> pd.DataFrame:
    if "matchStatus" in df_matches.columns:
        lookup_matches = df_matches[
            ~df_matches["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
        ].copy()
    else:
        lookup_matches = df_matches

    match_lookup = lookup_matches[["id_match", "date", "id_home_team", "id_away_team"]].copy()
    match_lookup["match_key"] = build_match_key(match_lookup["date"], match_lookup["id_home_team"], match_lookup["id_away_team"])
    match_lookup = match_lookup[["match_key", "id_match"]].drop_duplicates(subset=["match_key"], keep="last")

    frames: list[pd.DataFrame] = []
    for path in list_raw_files("espn", "matchsheet", pattern="read_matchsheet_*.csv"):
        df = pd.read_csv(path)
        parsed = df["game"].apply(parse_espn_game)
        df["parsed_date"] = parsed.apply(lambda item: item[0])
        df["parsed_home_team"] = parsed.apply(lambda item: item[1])
        df["parsed_away_team"] = parsed.apply(lambda item: item[2])
        df["match_key"] = build_match_key(df["parsed_date"], df["parsed_home_team"], df["parsed_away_team"])
        df = df.merge(match_lookup, on="match_key", how="inner")

        frame = pd.DataFrame()
        frame["id_teamMatchParticipation"] = df["id_match"].astype(str) + "_" + df["team"].astype(str).apply(normalize_team)
        for source_col, target_col in ESPN_RAW_TO_CANONICAL.items():
            frame[target_col] = pd.to_numeric(df[source_col], errors="coerce")
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["id_teamMatchParticipation", *ESPN_NUMERIC_COLUMNS])

    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["id_teamMatchParticipation"], keep="last")


def build_understat_lookup(df_matches: pd.DataFrame) -> pd.DataFrame:
    if "matchStatus" in df_matches.columns:
        lookup_matches = df_matches[
            ~df_matches["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
        ].copy()
    else:
        lookup_matches = df_matches

    lookup = lookup_matches[["id_match", "idUnderstat", "id_home_team", "id_away_team"]].copy()
    lookup["game_id"] = pd.to_numeric(lookup["idUnderstat"], errors="coerce")
    lookup = lookup.dropna(subset=["game_id"])
    lookup["game_id"] = lookup["game_id"].astype(int)
    lookup = lookup.drop_duplicates(subset=["game_id"], keep="last")

    frames: list[pd.DataFrame] = []
    for path in list_raw_files("understat", "team_match_stats", pattern="read_team_match_stats_*.csv"):
        df = pd.read_csv(path)
        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df = df.dropna(subset=["game_id"]).copy()
        df["game_id"] = df["game_id"].astype(int)
        df = df.merge(lookup[["game_id", "id_match"]], on="game_id", how="inner")

        home_frame = pd.DataFrame(
            {
                "id_teamMatchParticipation": df["id_match"].astype(str) + "_" + df["home_team"].astype(str).apply(normalize_team),
                "xg": pd.to_numeric(df["home_xg"], errors="coerce"),
                "nonPenaltyXg": pd.to_numeric(df["home_np_xg"], errors="coerce"),
                "nonPenaltyXgDifference": pd.to_numeric(df["home_np_xg_difference"], errors="coerce"),
                "ppda": pd.to_numeric(df["home_ppda"], errors="coerce"),
                "deepCompletions": pd.to_numeric(df["home_deep_completions"], errors="coerce"),
            }
        )
        away_frame = pd.DataFrame(
            {
                "id_teamMatchParticipation": df["id_match"].astype(str) + "_" + df["away_team"].astype(str).apply(normalize_team),
                "xg": pd.to_numeric(df["away_xg"], errors="coerce"),
                "nonPenaltyXg": pd.to_numeric(df["away_np_xg"], errors="coerce"),
                "nonPenaltyXgDifference": pd.to_numeric(df["away_np_xg_difference"], errors="coerce"),
                "ppda": pd.to_numeric(df["away_ppda"], errors="coerce"),
                "deepCompletions": pd.to_numeric(df["away_deep_completions"], errors="coerce"),
            }
        )
        frames.extend([home_frame, away_frame])

    if not frames:
        return pd.DataFrame(columns=["id_teamMatchParticipation", "xg", "nonPenaltyXg", "nonPenaltyXgDifference", "ppda", "deepCompletions"])

    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["id_teamMatchParticipation"], keep="last")


def build_team_match_participation(df_matches: pd.DataFrame) -> pd.DataFrame:
    base = build_base_participation(df_matches)
    espn_lookup = build_espn_lookup(df_matches)
    understat_lookup = build_understat_lookup(df_matches)

    df = base.merge(espn_lookup, on="id_teamMatchParticipation", how="left")
    df = df.merge(understat_lookup, on="id_teamMatchParticipation", how="left")

    for col in ESPN_NUMERIC_COLUMNS + ["xg", "nonPenaltyXg", "nonPenaltyXgDifference", "ppda", "deepCompletions"]:
        if col not in df.columns:
            df[col] = pd.NA

    aggregation = {"id_match": first_non_empty, "id_team": first_non_empty, "isHome": first_non_empty}
    for col in ESPN_NUMERIC_COLUMNS + ["xg", "nonPenaltyXg", "nonPenaltyXgDifference", "ppda", "deepCompletions"]:
        aggregation[col] = first_non_empty

    df = df.groupby("id_teamMatchParticipation", dropna=False).agg(aggregation).reset_index()

    ordered_columns = [
        "id_teamMatchParticipation",
        "id_match",
        "id_team",
        "isHome",
        *ESPN_NUMERIC_COLUMNS,
        "xg",
        "nonPenaltyXg",
        "nonPenaltyXgDifference",
        "ppda",
        "deepCompletions",
    ]
    df = df[ordered_columns].sort_values(by=["id_match", "id_team"]).reset_index(drop=True)
    return df


def main() -> None:
    parse_no_args("Construye participaciones canonicas de equipos por partido.")
    input_path = build_input_path()
    output_path = build_output_path()

    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")

    print("Consolidando participaciones de equipos por partido...")
    df_matches = pd.read_csv(input_path)
    df_participation = build_team_match_participation(df_matches)
    df_participation.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Participaciones de equipo", len(df_participation), output_path)


if __name__ == "__main__":
    main()

