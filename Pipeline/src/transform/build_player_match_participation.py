from __future__ import annotations

from pathlib import Path
import ast
import re
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_audit, print_examples, print_result, print_warning  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402
from src.transform.player_normalization import load_source_key_to_player_id, map_player_ids
from src.utils.text_normalization import normalize_team
from src.utils.season_scope import path_has_target_scope
from src.utils.datetime_normalization import date_part_series


NUMERIC_COLUMNS = [
    "appearances",
    "foulsCommitted",
    "foulsSuffered",
    "ownGoals",
    "redCards",
    "yellowCards",
    "goalsConceded",
    "saves",
    "goalAssists",
    "shotsOnTarget",
    "totalGoals",
    "totalShots",
    "offsides",
    "minutes",
    "xg",
    "xg_chain",
    "xg_buildup",
    "xa",
    "keyPasses",
]

UNAVAILABLE_NULL_COLUMNS = [
    "position",
    "subIn",
    "subOut",
    *NUMERIC_COLUMNS,
]

UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS = [
    "league",
    "season",
    "game",
    "game_id",
    "date",
    "home_team",
    "away_team",
    "url",
]


def first_non_empty(series: pd.Series):
    for value in series:
        if pd.isna(value):
            continue
        text = str(value).strip() if isinstance(value, str) else value
        if text == "":
            continue
        return value
    return pd.NA


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


def as_bool_or_none(value) -> bool | None:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def parse_qualifiers(value) -> list[dict]:
    if pd.isna(value):
        return []

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []

    try:
        parsed = ast.literal_eval(text)
    except Exception as exc:
        raise ValueError(f"No se pudieron parsear qualifiers: {text[:120]}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"Qualifiers debe ser una lista: {text[:120]}")

    return [item for item in parsed if isinstance(item, dict)]


def qualifier_name(item: dict) -> str | None:
    qualifier_type = item.get("type")
    if not isinstance(qualifier_type, dict):
        return None
    name = qualifier_type.get("displayName")
    return str(name).strip() if name is not None and str(name).strip() else None


def qualifier_value(qualifiers: list[dict], name: str):
    for item in qualifiers:
        if qualifier_name(item) == name:
            return item.get("value")
    return None


def map_existing_source_ids_to_player_ids(source: str, source_ids: pd.Series) -> pd.Series:
    lookup = load_source_key_to_player_id()
    parsed = source_ids.apply(parse_source_id).astype("string")
    keys = source + ":" + parsed.astype(str)
    keys = keys.where(parsed.notna(), pd.NA)
    return keys.map(lookup).astype("string")


def clear_unavailable_values(df: pd.DataFrame) -> pd.DataFrame:
    unavailable_mask = (
        df["participationStatus"]
        .astype("string")
        .str.strip()
        .str.lower()
        .isin(["no disponible", "no_disponible", "no convocado", "no_convocado"])
    )
    columns_to_clear = [col for col in UNAVAILABLE_NULL_COLUMNS if col in df.columns]
    df.loc[unavailable_mask, columns_to_clear] = pd.NA
    df.loc[unavailable_mask, "participationStatus"] = "no disponible"
    return df


def lineup_participation_status(df: pd.DataFrame) -> pd.Series:
    sub_in = df["sub_in"].astype("string").fillna("").str.strip().str.lower()
    appearance_values = df["appearances"] if "appearances" in df.columns else pd.Series(0, index=df.index)
    appearances = pd.to_numeric(appearance_values, errors="coerce").fillna(0)
    used_as_substitute = appearances.gt(0) | (sub_in.ne("") & sub_in.ne("start"))

    status = pd.Series("no jugado", index=df.index, dtype="string")
    status.loc[used_as_substitute] = "suplente"
    status.loc[sub_in.eq("start")] = "titular"
    return status


def list_raw_files(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def build_input_matches_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "player_match_participation.csv"


def understat_file_scope_key(path: Path, prefix: str) -> str | None:
    name = path.name
    if not name.startswith(prefix) or not name.endswith(".csv"):
        return None
    return name[len(prefix) : -4]


def matching_understat_schedule_files(player_match_files: list[Path]) -> list[Path]:
    player_match_scope_keys = {
        key
        for path in player_match_files
        if (key := understat_file_scope_key(path, "read_player_match_stats_")) is not None
    }
    if not player_match_scope_keys:
        return []

    return [
        path
        for path in list_raw_files("understat", "schedule", pattern="read_schedule_*.csv")
        if understat_file_scope_key(path, "read_schedule_") in player_match_scope_keys
    ]


def read_understat_player_match_game_ids(files: list[Path]) -> set[int]:
    game_ids: set[int] = set()
    for path in files:
        if not path.exists() or path.stat().st_size == 0:
            continue
        df = pd.read_csv(path, usecols=["game_id"], dtype="string")
        ids = pd.to_numeric(df["game_id"], errors="coerce").dropna().astype(int)
        game_ids.update(ids.tolist())
    return game_ids


def load_understat_expected_player_match_games(schedule_files: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in schedule_files:
        if not path.exists() or path.stat().st_size == 0:
            continue
        df = pd.read_csv(path, dtype="string")
        if "game_id" not in df.columns:
            raise ValueError(f"{path}: falta la columna obligatoria game_id")
        if "has_data" in df.columns:
            has_data = df["has_data"].astype("string").str.strip().str.lower().isin({"true", "1", "yes"})
            df = df.loc[has_data].copy()
        if df.empty:
            continue

        frame = pd.DataFrame()
        for column in UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS:
            frame[column] = df[column] if column in df.columns else pd.NA
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS)

    expected = pd.concat(frames, ignore_index=True)
    expected["game_id_num"] = pd.to_numeric(expected["game_id"], errors="coerce")
    expected = expected.dropna(subset=["game_id_num"]).copy()
    expected["game_id_num"] = expected["game_id_num"].astype(int)
    return expected.drop_duplicates(subset=["game_id_num"], keep="last").reset_index(drop=True)


def find_understat_player_match_coverage_gaps(
    schedule_files: list[Path],
    player_match_files: list[Path],
) -> pd.DataFrame:
    expected = load_understat_expected_player_match_games(schedule_files)
    if expected.empty:
        return pd.DataFrame(columns=UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS)

    observed_game_ids = read_understat_player_match_game_ids(player_match_files)
    missing = expected[~expected["game_id_num"].isin(observed_game_ids)].copy()
    if missing.empty:
        return pd.DataFrame(columns=UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS)
    return missing[UNDERSTAT_PLAYER_MATCH_COVERAGE_COLUMNS].reset_index(drop=True)


def format_understat_player_match_coverage_gap_example(row: pd.Series) -> str:
    return (
        f"Partido: {row.get('game', '-')}; "
        f"game_id={row.get('game_id', '-')}; "
        f"url={row.get('url', '-')}"
    )


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


def build_match_lookups(df_matches: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if "matchStatus" in df_matches.columns:
        lookup_matches = df_matches[
            ~df_matches["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
        ].copy()
    else:
        lookup_matches = df_matches

    espn_lookup = lookup_matches[["id_match", "date", "id_home_team", "id_away_team"]].copy()
    espn_lookup["match_key"] = build_match_key(espn_lookup["date"], espn_lookup["id_home_team"], espn_lookup["id_away_team"])
    espn_lookup = espn_lookup[["match_key", "id_match"]].drop_duplicates(subset=["match_key"], keep="last")

    understat_lookup = lookup_matches[["id_match", "idUnderstat"]].copy()
    understat_lookup["game_id"] = pd.to_numeric(understat_lookup["idUnderstat"], errors="coerce")
    understat_lookup = understat_lookup.dropna(subset=["game_id"])
    understat_lookup["game_id"] = understat_lookup["game_id"].astype(int)
    understat_lookup = understat_lookup[["game_id", "id_match"]].drop_duplicates(subset=["game_id"], keep="last")

    whoscored_lookup = lookup_matches[["id_match", "idWhoscored"]].copy()
    whoscored_lookup["game_id"] = pd.to_numeric(whoscored_lookup["idWhoscored"], errors="coerce")
    whoscored_lookup = whoscored_lookup.dropna(subset=["game_id"])
    whoscored_lookup["game_id"] = whoscored_lookup["game_id"].astype(int)
    whoscored_lookup = whoscored_lookup[["game_id", "id_match"]].drop_duplicates(subset=["game_id"], keep="last")

    return espn_lookup, understat_lookup, whoscored_lookup


def normalize_lineup(files: list[Path], espn_lookup: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in files:
        df = pd.read_csv(path)
        parsed = df["game"].apply(parse_espn_game)
        df["parsed_date"] = parsed.apply(lambda item: item[0])
        df["parsed_home_team"] = parsed.apply(lambda item: item[1])
        df["parsed_away_team"] = parsed.apply(lambda item: item[2])
        df["match_key"] = build_match_key(df["parsed_date"], df["parsed_home_team"], df["parsed_away_team"])
        df = df.merge(espn_lookup, on="match_key", how="inner")
        if df.empty:
            continue
        df["id_team"] = df["team"].astype(str).apply(normalize_team)

        frame = pd.DataFrame()
        frame["id_match"] = df["id_match"].astype("string")
        frame["id_team"] = df["id_team"]
        frame["id_player"] = map_player_ids(
            df,
            source="espn",
            player_col="player",
            team_col="id_team",
            competition_col="league",
            season_col="season",
        )
        frame["participationStatus"] = lineup_participation_status(df)
        frame["position"] = df["position"].astype("string")
        frame["subIn"] = df["sub_in"].astype("string")
        frame["subOut"] = pd.to_numeric(df["sub_out"], errors="coerce")
        espn_numeric_columns = {
            "appearances": "appearances",
            "fouls_committed": "foulsCommitted",
            "fouls_suffered": "foulsSuffered",
            "own_goals": "ownGoals",
            "red_cards": "redCards",
            "yellow_cards": "yellowCards",
            "goals_conceded": "goalsConceded",
            "saves": "saves",
            "goal_assists": "goalAssists",
            "shots_on_target": "shotsOnTarget",
            "total_goals": "totalGoals",
            "total_shots": "totalShots",
            "offsides": "offsides",
        }
        for source_col, target_col in espn_numeric_columns.items():
            frame[target_col] = pd.to_numeric(df[source_col], errors="coerce")
        frame["minutes"] = pd.NA
        frame["xg"] = pd.NA
        frame["xg_chain"] = pd.NA
        frame["xg_buildup"] = pd.NA
        frame["xa"] = pd.NA
        frame["keyPasses"] = pd.NA
        frame["reason"] = pd.NA
        frame["status"] = pd.NA
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def normalize_understat(files: list[Path], understat_lookup: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in files:
        df = pd.read_csv(path)
        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df = df.dropna(subset=["game_id"]).copy()
        df["game_id"] = df["game_id"].astype(int)
        df = df.merge(understat_lookup, on="game_id", how="inner")
        df["id_team"] = df["team"].astype(str).apply(normalize_team)

        frame = pd.DataFrame()
        frame["id_match"] = df["id_match"].astype("string")
        frame["id_team"] = df["id_team"]
        frame["id_player"] = map_player_ids(
            df,
            source="understat",
            player_col="player",
            source_player_id_col="player_id",
            team_col="id_team",
            competition_col="league",
            season_col="season",
        )
        frame["participationStatus"] = pd.NA
        frame["position"] = pd.NA
        frame["subIn"] = pd.NA
        frame["subOut"] = pd.NA
        for col in [
            "appearances",
            "foulsCommitted",
            "foulsSuffered",
            "ownGoals",
            "redCards",
            "yellowCards",
            "goalsConceded",
            "saves",
            "goalAssists",
            "shotsOnTarget",
            "totalGoals",
            "totalShots",
            "offsides",
        ]:
            frame[col] = pd.NA
        for source_col, target_col in {
            "minutes": "minutes",
            "xg": "xg",
            "xg_chain": "xg_chain",
            "xg_buildup": "xg_buildup",
            "xa": "xa",
            "key_passes": "keyPasses",
        }.items():
            frame[target_col] = pd.to_numeric(df[source_col], errors="coerce")
        frame["reason"] = pd.NA
        frame["status"] = pd.NA
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def normalize_missing_players(files: list[Path], whoscored_lookup: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in files:
        df = pd.read_csv(path)
        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df = df.dropna(subset=["game_id"]).copy()
        df["game_id"] = df["game_id"].astype(int)
        df = df.merge(whoscored_lookup, on="game_id", how="inner")
        df["id_team"] = df["team"].astype(str).apply(normalize_team)

        frame = pd.DataFrame()
        frame["id_match"] = df["id_match"].astype("string")
        frame["id_team"] = df["id_team"]
        frame["id_player"] = map_player_ids(
            df,
            source="whoscored",
            player_col="player",
            source_player_id_col="player_id",
            team_col="id_team",
            competition_col="league",
            season_col="season",
        )
        frame["participationStatus"] = "no disponible"
        frame["position"] = pd.NA
        frame["subIn"] = pd.NA
        frame["subOut"] = pd.NA
        for col in NUMERIC_COLUMNS:
            frame[col] = pd.NA
        frame["reason"] = df["reason"].astype("string")
        frame["status"] = df["status"].astype("string")
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def with_formation_audit_attrs(
    df: pd.DataFrame,
    unmapped_captain_count: int,
    unmapped_examples: list[str],
) -> pd.DataFrame:
    df.attrs["unmapped_formation_captain_count"] = unmapped_captain_count
    df.attrs["unmapped_formation_captain_examples"] = unmapped_examples[:10]
    return df


def normalize_formation_players(files: list[Path], whoscored_lookup: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    unmapped_captain_count = 0
    unmapped_examples: list[str] = []

    for path in files:
        df = pd.read_csv(path, usecols=["game_id", "team", "qualifiers", "type"])
        df = df[df["type"].astype("string").str.strip().eq("FormationSet")].copy()
        if df.empty:
            continue

        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df = df.dropna(subset=["game_id"]).copy()
        df["game_id"] = df["game_id"].astype(int)
        df = df.merge(whoscored_lookup, on="game_id", how="inner")
        df["id_team"] = df["team"].astype(str).apply(normalize_team)

        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            qualifiers = parse_qualifiers(row["qualifiers"])
            captain_source_id = parse_source_id(qualifier_value(qualifiers, "CaptainPlayerId"))
            if not captain_source_id:
                continue
            rows.append(
                {
                    "id_match": row["id_match"],
                    "id_team": row["id_team"],
                    "player_source_id": captain_source_id,
                    "isCaptain": True,
                }
            )

        if not rows:
            continue

        frame = pd.DataFrame(rows)
        frame["id_player"] = map_existing_source_ids_to_player_ids("whoscored", frame["player_source_id"])
        unmapped_mask = frame["id_player"].isna()
        if unmapped_mask.any():
            unmapped_captain_count += int(unmapped_mask.sum())
            unmapped_examples.extend(
                frame.loc[unmapped_mask, "player_source_id"]
                .dropna()
                .astype(str)
                .drop_duplicates()
                .head(max(0, 10 - len(unmapped_examples)))
                .tolist()
            )
        frame = frame[frame["id_player"].notna()].copy()
        frame = frame.drop(columns=["player_source_id"])
        frames.append(frame)

    if not frames:
        return with_formation_audit_attrs(pd.DataFrame(), unmapped_captain_count, unmapped_examples)
    return with_formation_audit_attrs(
        pd.concat(frames, ignore_index=True),
        unmapped_captain_count,
        unmapped_examples,
    )


def normalize_event_players(files: list[Path], whoscored_lookup: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    for path in files:
        df = pd.read_csv(path)
        df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
        df = df.dropna(subset=["game_id"]).copy()
        df["game_id"] = df["game_id"].astype(int)
        df = df.merge(whoscored_lookup, on="game_id", how="inner")
        df["id_team"] = df["team"].astype(str).apply(normalize_team)

        frame = pd.DataFrame()
        frame["id_match"] = df["id_match"].astype("string")
        frame["id_team"] = df["id_team"]
        frame["id_player"] = map_player_ids(
            df,
            source="whoscored",
            player_col="player",
            source_player_id_col="player_id",
            team_col="id_team",
            competition_col="league",
            season_col="season",
        )
        frame = frame[frame["id_player"].notna()].drop_duplicates(
            subset=["id_match", "id_player"],
            keep="last",
        )
        frame["participationStatus"] = pd.NA
        frame["position"] = pd.NA
        frame["subIn"] = pd.NA
        frame["subOut"] = pd.NA
        for col in NUMERIC_COLUMNS:
            frame[col] = pd.NA
        frame["reason"] = pd.NA
        frame["status"] = pd.NA
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_player_match_participation(df_matches: pd.DataFrame) -> pd.DataFrame:
    espn_lookup, understat_lookup, whoscored_lookup = build_match_lookups(df_matches)
    formation_players = normalize_formation_players(
        list_raw_files("whoscored", "events", pattern="read_events_*.csv"),
        whoscored_lookup,
    )
    unmapped_captain_count = int(formation_players.attrs.get("unmapped_formation_captain_count", 0))
    unmapped_captain_examples = list(formation_players.attrs.get("unmapped_formation_captain_examples", []))

    frames = [
        normalize_lineup(list_raw_files("espn", "lineup", pattern="read_lineup_*.csv"), espn_lookup),
        normalize_understat(list_raw_files("understat", "player_match_stats", pattern="read_player_match_stats_*.csv"), understat_lookup),
        normalize_missing_players(list_raw_files("whoscored", "missing_players", pattern="read_missing_players_*.csv"), whoscored_lookup),
        formation_players,
        normalize_event_players(list_raw_files("whoscored", "events", pattern="read_events_*.csv"), whoscored_lookup),
    ]
    frames = [frame for frame in frames if not frame.empty]

    if not frames:
        raise FileNotFoundError("No existen archivos raw para construir player_match_participation")

    df = pd.concat(frames, ignore_index=True)
    df = df[df["id_player"].notna()].copy()
    for column in ["isCaptain"]:
        if column not in df.columns:
            df[column] = pd.NA
    df["id_playerMatchParticipation"] = df["id_match"].astype(str) + "_" + df["id_player"].astype(str)

    aggregation = {
        "id_match": first_non_empty,
        "id_team": first_non_empty,
        "id_player": first_non_empty,
        "participationStatus": first_non_empty,
        "position": first_non_empty,
        "isCaptain": first_non_empty,
        "subIn": first_non_empty,
        "subOut": first_non_empty,
        "appearances": first_non_empty,
        "foulsCommitted": first_non_empty,
        "foulsSuffered": first_non_empty,
        "ownGoals": first_non_empty,
        "redCards": first_non_empty,
        "yellowCards": first_non_empty,
        "goalsConceded": first_non_empty,
        "saves": first_non_empty,
        "goalAssists": first_non_empty,
        "shotsOnTarget": first_non_empty,
        "totalGoals": first_non_empty,
        "totalShots": first_non_empty,
        "offsides": first_non_empty,
        "minutes": first_non_empty,
        "xg": first_non_empty,
        "xg_chain": first_non_empty,
        "xg_buildup": first_non_empty,
        "xa": first_non_empty,
        "keyPasses": first_non_empty,
        "reason": first_non_empty,
        "status": first_non_empty,
    }
    df = df.groupby("id_playerMatchParticipation", dropna=False).agg(aggregation).reset_index()
    df = clear_unavailable_values(df)
    df["isCaptain"] = df["isCaptain"].apply(as_bool_or_none).astype("boolean").fillna(False)

    ordered_columns = [
        "id_playerMatchParticipation",
        "id_match",
        "id_team",
        "id_player",
        "participationStatus",
        "position",
        "isCaptain",
        "subIn",
        "subOut",
        "appearances",
        "foulsCommitted",
        "foulsSuffered",
        "ownGoals",
        "redCards",
        "yellowCards",
        "goalsConceded",
        "saves",
        "goalAssists",
        "shotsOnTarget",
        "totalGoals",
        "totalShots",
        "offsides",
        "minutes",
        "xg",
        "xg_chain",
        "xg_buildup",
        "xa",
        "keyPasses",
        "reason",
        "status",
    ]
    df = df[ordered_columns].sort_values(by=["id_match", "id_team", "id_player"]).reset_index(drop=True)
    df.attrs["unmapped_formation_captain_count"] = unmapped_captain_count
    df.attrs["unmapped_formation_captain_examples"] = unmapped_captain_examples
    return df

def run_build() -> dict:
    parse_no_args("Construye participaciones canonicas de jugadores por partido.")
    matches_path = build_input_matches_path()
    output_path = build_output_path()
    understat_player_match_paths = list_raw_files("understat", "player_match_stats", pattern="read_player_match_stats_*.csv")
    understat_schedule_paths = matching_understat_schedule_files(understat_player_match_paths)
    understat_coverage_gaps = find_understat_player_match_coverage_gaps(
        understat_schedule_paths,
        understat_player_match_paths,
    )

    if not matches_path.exists():
        raise FileNotFoundError(f"No existe: {matches_path}")

    print("Consolidando participaciones de jugadores por partido...")
    df_matches = pd.read_csv(matches_path)
    df = build_player_match_participation(df_matches)
    df.to_csv(output_path, index=False, encoding="utf-8")

    warnings = []
    understat_gap_count = len(understat_coverage_gaps)
    if understat_gap_count:
        warning = (
            f"{understat_gap_count} partido(s) con datos en el calendario de Understat no aparecen en player_match_stats; "
            "player_match_participation se construye sin metricas avanzadas de jugador de Understat para esos partidos."
        )
        print_warning(warning)
        print_examples(
            format_understat_player_match_coverage_gap_example(row)
            for _, row in understat_coverage_gaps.head(10).iterrows()
        )
        warnings.append(warning)

    print_result("Participaciones de jugador", len(df), output_path)
    unmapped_captains = int(df.attrs.get("unmapped_formation_captain_count", 0))
    print_audit("Capitanes FormationSet sin mapping canonico", unmapped_captains)
    if unmapped_captains:
        examples = list(df.attrs.get("unmapped_formation_captain_examples", []))
        print_examples(examples)
    print_audit("Partidos Understat player_match_stats omitidos al construir player_match_participation", understat_gap_count)
    return {
        "input_files": [matches_path, *understat_schedule_paths, *understat_player_match_paths],
        "output_files": [output_path],
        "warnings": warnings,
        "metrics": {
            "rows": len(df),
            "understat_player_match_missing_matches": understat_gap_count,
            "unmapped_formation_captain_count": unmapped_captains,
        },
    }


def main() -> None:
    run_with_optional_task_result("build_player_match_participation", "transform", run_build)


if __name__ == "__main__":
    main()

