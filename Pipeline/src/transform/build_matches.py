from pathlib import Path
import re
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.text_normalization import normalize_competition, normalize_season, normalize_team
from src.utils.season_scope import filter_target_id_seasons, filter_target_seasons, path_has_target_scope
from src.utils.datetime_normalization import date_part, date_part_series

SOFASCORE_REQUIRED_COLUMNS = ["league", "season", "game", "week", "date", "home_team", "away_team", "home_score", "away_score", "game_id"]
MATCH_TIMEZONE = "Europe/Madrid"
MATCH_STATUS_COMPLETED = "completed"
MATCH_STATUS_ABANDONED = "abandoned"


def list_raw_files(path_parts: tuple[str, ...], pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in path_parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def build_match_id(row: pd.Series) -> str:
    match_date_part = "unknown_date"
    if pd.notna(row["match_date"]):
        parsed_date = date_part(row["match_date"])
        match_date_part = parsed_date if pd.notna(parsed_date) else "unknown_date"
    competition_part = str(row.get("id_competition", "unknown_competition"))
    season_part = str(row.get("id_season", "unknown_season"))
    return (
        f"{competition_part}_{season_part}_{match_date_part}_"
        f"{row['id_home_team']}_{row['id_away_team']}"
    )


def build_match_key_from_cols(df: pd.DataFrame, date_col: str, home_col: str, away_col: str) -> pd.Series:
    date_part = date_part_series(df[date_col])
    home_part = df[home_col].astype(str).apply(normalize_team)
    away_part = df[away_col].astype(str).apply(normalize_team)
    return date_part + "|" + home_part + "|" + away_part


def build_matchup_key_from_cols(
    df: pd.DataFrame,
    competition_col: str,
    season_col: str,
    home_col: str,
    away_col: str,
) -> pd.Series:
    competition_part = df[competition_col].apply(normalize_competition)
    season_part = df[season_col].apply(normalize_season)
    home_part = df[home_col].astype(str).apply(normalize_team)
    away_part = df[away_col].astype(str).apply(normalize_team)
    return (
        competition_part.astype(str)
        + "|"
        + season_part.astype(str)
        + "|"
        + home_part.astype(str)
        + "|"
        + away_part.astype(str)
    )


def resolve_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    return next((col for col in candidates if col in df.columns), None)


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"nan", "<na>"} else text


def parse_espn_game(value: str):
    if pd.isna(value):
        return None, None, None
    text = str(value).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", text)
    if not match:
        return None, None, None
    date_str = match.group(1)
    rest = match.group(2).strip()
    parts = None
    for sep in [" - ", "-", " vs ", " v "]:
        if sep in rest:
            parts = rest.split(sep, 1)
            break
    if not parts or len(parts) != 2:
        return date_str, None, None
    return date_str, parts[0].strip(), parts[1].strip()


def parse_espn_home_game(value: str, home_team: str):
    fallback = parse_espn_game(value)
    if pd.isna(value) or pd.isna(home_team):
        return fallback

    text = str(value).strip()
    home_text = str(home_team).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$", text)
    if not match or not home_text:
        return fallback

    date_str = match.group(1)
    rest = match.group(2).strip()
    if rest.startswith(home_text):
        remainder = rest[len(home_text):]
        for sep in [" - ", "-", " vs ", " v "]:
            if remainder.startswith(sep):
                away_team = remainder[len(sep):].strip()
                if away_team:
                    return date_str, home_text, away_team
    return fallback


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "matches.csv"


def load_sofascore(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [col for col in SOFASCORE_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en SofaScore ({path.name}): {missing}")
    
    df = df[SOFASCORE_REQUIRED_COLUMNS].copy()
    df["match_date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(MATCH_TIMEZONE)
    df["id_competition"] = df["league"].apply(normalize_competition)
    df["id_season"] = df["season"].apply(normalize_season)
    df = filter_target_id_seasons(df)
    df["id_home_team"] = df["home_team"].apply(normalize_team)
    df["id_away_team"] = df["away_team"].apply(normalize_team)
    df["name"] = df["home_team"].astype(str) + " vs " + df["away_team"].astype(str)
    df["idSofascore"] = df["game_id"].astype("string")
    df["match_key"] = build_match_key_from_cols(df, "date", "home_team", "away_team")
    df["matchup_key"] = build_matchup_key_from_cols(df, "league", "season", "home_team", "away_team")
    df["homeScore"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["awayScore"] = pd.to_numeric(df["away_score"], errors="coerce")
    df["matchDay"] = pd.to_numeric(df["week"], errors="coerce")
    df["matchStatus"] = MATCH_STATUS_COMPLETED
    return df


def is_abandoned_whoscored_match(row: pd.Series) -> bool:
    # WhoScored keeps the abandoned Nantes-Toulouse match as FT with a kickoff
    # and a first-half stop timestamp, but without a second-half start.
    started = clean_text(row.get("started_at_utc"))
    first_half_ended = clean_text(row.get("first_half_ended_at_utc"))
    second_half_started = clean_text(row.get("second_half_started_at_utc"))
    elapsed = clean_text(row.get("elapsed")).upper()
    period = clean_text(row.get("period"))
    has_score = clean_text(row.get("home_score")) != "" and clean_text(row.get("away_score")) != ""
    return bool(started and first_half_ended and not second_half_started and elapsed == "FT" and period == "0" and has_score)


def build_abandoned_whoscored_matches(
    files: list[Path],
    existing_match_keys: set[str],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    required_columns = {"league", "season", "date", "home_team", "away_team", "game_id"}

    for path in files:
        df = pd.read_csv(path)
        if not required_columns.issubset(df.columns):
            continue

        df = filter_target_seasons(df)
        if df.empty:
            continue

        df = df.copy()
        df["match_key"] = build_match_key_from_cols(df, "date", "home_team", "away_team")
        df = df[~df["match_key"].isin(existing_match_keys)].copy()
        if df.empty:
            continue

        df = df[df.apply(is_abandoned_whoscored_match, axis=1)].copy()
        if df.empty:
            continue

        df["match_date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(MATCH_TIMEZONE)
        df["id_competition"] = df["league"].apply(normalize_competition)
        df["id_season"] = df["season"].apply(normalize_season)
        df = filter_target_id_seasons(df)
        if df.empty:
            continue

        df["id_home_team"] = df["home_team"].apply(normalize_team)
        df["id_away_team"] = df["away_team"].apply(normalize_team)
        df["name"] = df["home_team"].astype(str) + " vs " + df["away_team"].astype(str)
        df["matchup_key"] = build_matchup_key_from_cols(df, "league", "season", "home_team", "away_team")
        df["homeScore"] = pd.to_numeric(df["home_score"], errors="coerce")
        df["awayScore"] = pd.to_numeric(df["away_score"], errors="coerce")
        df["matchDay"] = pd.NA
        df["idSofascore"] = pd.NA
        df["idWhoscored"] = df["game_id"].astype("string")
        df["matchStatus"] = MATCH_STATUS_ABANDONED
        for column in [
            "idUnderstat",
            "finalResult",
            "halftimeHomeScore",
            "halftimeAwayScore",
            "halftimeResult",
            "venue",
            "attendance",
        ]:
            df[column] = pd.NA
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def build_id_lookup(
    files: list[Path],
    date_candidates: list[str],
    home_candidates: list[str],
    away_candidates: list[str],
    id_candidates: list[str],
    id_name: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in files:
        df = pd.read_csv(path)
        if "game" in df.columns and "game_id" not in df.columns:
            df["game_id"] = df["game"]

        date_col = resolve_column(df, date_candidates)
        home_col = resolve_column(df, home_candidates)
        away_col = resolve_column(df, away_candidates)
        id_col = resolve_column(df, id_candidates)

        if not all([date_col, home_col, away_col, id_col]):
            continue

        df["match_key"] = build_match_key_from_cols(df, date_col, home_col, away_col)
        league_col = resolve_column(df, ["league"])
        season_col = resolve_column(df, ["season"])
        if not all([league_col, season_col]):
            continue

        df = filter_target_seasons(df, season_col)
        if df.empty:
            continue
        df["matchup_key"] = build_matchup_key_from_cols(df, league_col, season_col, home_col, away_col)
        frame = pd.DataFrame(
            {
                "match_key": df["match_key"],
                "matchup_key": df["matchup_key"],
                id_name: df[id_col],
            }
        )
        frame[id_name] = frame[id_name].astype("string")
        frame = frame.drop_duplicates(subset=["match_key"], keep="last")
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["match_key", "matchup_key", id_name])

    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates(subset=["match_key"], keep="last")


def build_matchhistory_lookup(files: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in files:
        df = pd.read_csv(path)
        date_col = resolve_column(df, ["date", "Date"])
        home_col = resolve_column(df, ["home_team", "home", "HomeTeam"])
        away_col = resolve_column(df, ["away_team", "away", "AwayTeam"])
        ftr_col = resolve_column(df, ["FTR", "ftr"])
        hthg_col = resolve_column(df, ["HTHG", "hthg"])
        htag_col = resolve_column(df, ["HTAG", "htag"])
        htr_col = resolve_column(df, ["HTR", "htr"])

        if not all([date_col, home_col, away_col, ftr_col, hthg_col, htag_col, htr_col]):
            continue

        df = df.copy()
        df["match_key"] = build_match_key_from_cols(df, date_col, home_col, away_col)
        league_col = resolve_column(df, ["league"])
        season_col = resolve_column(df, ["season"])
        if not all([league_col, season_col]):
            continue

        df = filter_target_seasons(df, season_col)
        if df.empty:
            continue
        df["matchup_key"] = build_matchup_key_from_cols(df, league_col, season_col, home_col, away_col)
        frame = pd.DataFrame(
            {
                "match_key": df["match_key"],
                "matchup_key": df["matchup_key"],
                "finalResult": df[ftr_col],
                "halftimeHomeScore": df[hthg_col],
                "halftimeAwayScore": df[htag_col],
                "halftimeResult": df[htr_col],
            }
        )
        frame = frame.drop_duplicates(subset=["match_key"], keep="last")
        frames.append(frame)

    if not frames:
        return pd.DataFrame(
            columns=[
                "match_key",
                "matchup_key",
                "finalResult",
                "halftimeHomeScore",
                "halftimeAwayScore",
                "halftimeResult",
            ]
        )

    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates(subset=["match_key"], keep="last")


def build_espn_lookup(files: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in files:
        df = pd.read_csv(path)
        df = filter_target_seasons(df)
        if df.empty:
            continue
        game_col = resolve_column(df, ["game"])
        team_col = resolve_column(df, ["team"])
        is_home_col = resolve_column(df, ["is_home", "home"])
        venue_col = resolve_column(df, ["venue", "stadium"])
        attendance_col = resolve_column(df, ["attendance", "att"])

        if not all([game_col, is_home_col, venue_col, attendance_col]):
            continue

        df["is_home"] = df[is_home_col].astype(str).str.lower().isin(["true", "1", "yes"])
        df = df[df["is_home"]].copy()

        if team_col:
            parsed = df.apply(lambda row: parse_espn_home_game(row[game_col], row[team_col]), axis=1)
        else:
            parsed = df[game_col].apply(parse_espn_game)
        df["parsed_date"] = parsed.apply(lambda x: x[0])
        df["parsed_home_team"] = parsed.apply(lambda x: x[1])
        df["parsed_away_team"] = parsed.apply(lambda x: x[2])
        df = df.dropna(subset=["parsed_date", "parsed_home_team", "parsed_away_team"])

        df["match_key"] = build_match_key_from_cols(df, "parsed_date", "parsed_home_team", "parsed_away_team")
        df["matchup_key"] = build_matchup_key_from_cols(df, "league", "season", "parsed_home_team", "parsed_away_team")
        df["attendance"] = pd.to_numeric(df[attendance_col], errors="coerce")
        df["venue"] = df[venue_col].astype("string")
        frame = df[["match_key", "matchup_key", "venue", "attendance"]].drop_duplicates(subset=["match_key"], keep="last")
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["match_key", "matchup_key", "venue", "attendance"])

    merged = pd.concat(frames, ignore_index=True)
    return merged.drop_duplicates(subset=["match_key"], keep="last")


def merge_lookup_with_fallback(
    base_df: pd.DataFrame,
    lookup_df: pd.DataFrame,
    value_columns: list[str],
) -> pd.DataFrame:
    exact_lookup = lookup_df[["match_key", *value_columns]].drop_duplicates(subset=["match_key"], keep="last")
    merged = base_df.merge(exact_lookup, on="match_key", how="left")

    missing_mask = merged[value_columns].isna().all(axis=1)
    if not missing_mask.any():
        return merged

    fallback_lookup = lookup_df[["matchup_key", *value_columns]].copy()
    fallback_lookup = fallback_lookup.drop_duplicates(subset=["matchup_key"], keep=False)
    if fallback_lookup.empty:
        return merged

    fallback_values = (
        merged.loc[missing_mask, ["matchup_key"]]
        .merge(fallback_lookup, on="matchup_key", how="left")
        .reset_index(drop=True)
    )

    for column in value_columns:
        merged.loc[missing_mask, column] = fallback_values[column].to_numpy()

    return merged


def build_matches() -> pd.DataFrame:
    sofascore_files = list_raw_files(("sofascore", "schedule"), "read_schedule_*.csv")
    if not sofascore_files:
        raise FileNotFoundError("No existen archivos raw de SofaScore schedule para construir matches")

    frames = [frame for frame in (load_sofascore(path) for path in sofascore_files) if not frame.empty]
    if not frames:
        raise FileNotFoundError("No existen archivos raw de SofaScore schedule para las temporadas objetivo")
    df = pd.concat(frames, ignore_index=True)

    understat_lookup = build_id_lookup(
        list_raw_files(("understat", "schedule"), "read_schedule_*.csv"),
        ["date", "Date"],
        ["home_team", "home", "HomeTeam"],
        ["away_team", "away", "AwayTeam"],
        ["game_id", "match_id", "id"],
        "idUnderstat",
    )
    whoscored_lookup = build_id_lookup(
        list_raw_files(("whoscored", "schedule"), "read_schedule_*.csv"),
        ["date", "Date"],
        ["home_team", "home", "HomeTeam"],
        ["away_team", "away", "AwayTeam"],
        ["game_id", "match_id", "id"],
        "idWhoscored",
    )
    matchhistory_lookup = build_matchhistory_lookup(
        list_raw_files(("matchhistory", "games"), "read_games_*.csv")
    )
    espn_lookup = build_espn_lookup(
        list_raw_files(("espn", "matchsheet"), "read_matchsheet_*.csv")
    )

    df = merge_lookup_with_fallback(df, understat_lookup, ["idUnderstat"])
    df = merge_lookup_with_fallback(df, whoscored_lookup, ["idWhoscored"])
    df = merge_lookup_with_fallback(
        df,
        matchhistory_lookup,
        ["finalResult", "halftimeHomeScore", "halftimeAwayScore", "halftimeResult"],
    )
    df = merge_lookup_with_fallback(df, espn_lookup, ["venue", "attendance"])

    abandoned_matches = build_abandoned_whoscored_matches(
        list_raw_files(("whoscored", "schedule"), "read_schedule_*.csv"),
        set(df["match_key"].dropna().astype(str)),
    )
    if not abandoned_matches.empty:
        abandoned_matches = abandoned_matches.drop(
            columns=[
                "idUnderstat",
                "finalResult",
                "halftimeHomeScore",
                "halftimeAwayScore",
                "halftimeResult",
                "venue",
                "attendance",
            ],
            errors="ignore",
        )
        abandoned_matches = merge_lookup_with_fallback(abandoned_matches, understat_lookup, ["idUnderstat"])
        abandoned_matches = merge_lookup_with_fallback(
            abandoned_matches,
            matchhistory_lookup,
            ["finalResult", "halftimeHomeScore", "halftimeAwayScore", "halftimeResult"],
        )
        abandoned_matches = merge_lookup_with_fallback(abandoned_matches, espn_lookup, ["venue", "attendance"])
        df = pd.concat([df, abandoned_matches], ignore_index=True)

    for col in [
        "idUnderstat",
        "idWhoscored",
        "finalResult",
        "halftimeHomeScore",
        "halftimeAwayScore",
        "halftimeResult",
        "venue",
        "attendance",
        "matchStatus",
    ]:
        if col not in df.columns:
            df[col] = pd.NA
    df["matchStatus"] = df["matchStatus"].fillna(MATCH_STATUS_COMPLETED)

    df["id_match"] = df.apply(build_match_id, axis=1)
    
    columns = [
        "id_match",
        "id_competition",
        "id_season",
        "id_home_team",
        "id_away_team",
        "name",
        "matchDay",
        "match_date",
        "homeScore",
        "awayScore",
        "finalResult",
        "halftimeHomeScore",
        "halftimeAwayScore",
        "halftimeResult",
        "venue",
        "attendance",
        "idSofascore",
        "idUnderstat",
        "idWhoscored",
        "matchStatus",
    ]
    
    df_matches = df[columns].copy()
    df_matches = df_matches.drop_duplicates(subset=["id_match"], keep="last")
    sorted_matches = df_matches.sort_values(by=["match_date", "id_home_team", "id_away_team"], na_position="last").reset_index(drop=True)
    sorted_matches["date"] = pd.to_datetime(sorted_matches["match_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    sorted_matches["dateTime"] = sorted_matches["match_date"]
    ordered_columns = [
        "id_match",
        "id_competition",
        "id_season",
        "id_home_team",
        "id_away_team",
        "name",
        "matchDay",
        "date",
        "dateTime",
        "homeScore",
        "awayScore",
        "finalResult",
        "halftimeHomeScore",
        "halftimeAwayScore",
        "halftimeResult",
        "venue",
        "attendance",
        "idSofascore",
        "idUnderstat",
        "idWhoscored",
        "matchStatus",
    ]
    return sorted_matches[ordered_columns]


def main() -> None:
    parse_no_args("Construye partidos canonicos desde calendarios raw y metadatos de partido, acotados si el alcance del pipeline esta activo.")
    output_path = build_output_path()
    print("Consolidando partidos en formato canonico...")
    df_matches = build_matches()

    df_matches.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Partidos", len(df_matches), output_path)


if __name__ == "__main__":
    main()

