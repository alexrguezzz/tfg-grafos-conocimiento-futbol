from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.utils.text_normalization import (  # noqa: E402
    clean_identifier_text,
    normalize_competition,
    normalize_season,
    normalize_team,
)
from src.utils.season_scope import (  # noqa: E402
    SCOPE_ACTIVE,
    TARGET_COMPETITION_SET,
    TARGET_SEASON_SET,
    filter_target_id_seasons,
    filter_target_seasons,
    path_has_target_scope,
)


NORMALIZATION_DIR = PROJECT_ROOT / "data" / "processed" / "normalization"
NORMALIZATION_AUDIT_DIR = NORMALIZATION_DIR / "audit"
NORMALIZATION_CACHE_DIR = NORMALIZATION_DIR / "cache"
ENV_PATHS = [PROJECT_ROOT / ".env", PROJECT_ROOT / ".evn"]
TEAM_COMPETITION_SEASON_PATH = PROJECT_ROOT / "data" / "processed" / "canonical" / "team_competition_season.csv"
PLAYER_IDENTITIES_PATH = NORMALIZATION_DIR / "player_identities.csv"
PLAYER_ALIAS_MAP_PATH = NORMALIZATION_DIR / "player_alias_map.csv"
PLAYER_GEMINI_CACHE_PATH = NORMALIZATION_CACHE_DIR / "player_gemini_cache.jsonl"
LEGACY_PLAYER_GEMINI_CACHE_PATH = NORMALIZATION_DIR / "player_gemini_cache.jsonl"
PLAYER_REVIEW_QUEUE_PATH = NORMALIZATION_AUDIT_DIR / "player_review_queue.csv"
PLAYER_REPORT_PATH = NORMALIZATION_AUDIT_DIR / "player_normalization_report.json"
GEMINI_PROMPT_PATH = PROJECT_ROOT / "src" / "transform" / "prompts" / "player_normalization_gemini_prompt.txt"

ID_BACKED_SOURCES = {"understat", "whoscored"}
GEMINI_DEFAULT_MODEL = "gemini-3.1-flash-lite"
GEMINI_DEFAULT_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
GEMINI_DEFAULT_RETRY_MODEL = "gemini-3.5-flash"
GEMINI_DEFAULT_RETRY_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]
GEMINI_DEFAULT_MODEL_RPM_LIMITS = {
    "gemini-3.5-flash": 5.0,
    "gemini-flash-latest": 5.0,
    "gemini-3.1-flash-lite": 15.0,
    "gemini-flash-lite-latest": 15.0,
    "gemini-2.5-flash": 5.0,
    "gemini-2.5-flash-lite": 10.0,
}
GEMINI_DEFAULT_RETRY_ATTEMPTS = 2
GEMINI_DEFAULT_RETRY_BASE_SECONDS = 10
GEMINI_DEFAULT_TIMEOUT_SECONDS = 60
GEMINI_DEFAULT_MAX_LIVE_CALLS: int | None = None
GEMINI_ACCEPT_CONFIDENCE = 0.90
GEMINI_NICKNAME_ACCEPT_CONFIDENCE = 0.95
GEMINI_REJECT_CONFIDENCE = 0.90
GEMINI_PROMPT_VERSION = "player-normalization-known-full-v5"
GEMINI_COMPATIBLE_PROMPT_VERSIONS = {
    GEMINI_PROMPT_VERSION,
}
MIN_FULL_NAME_TOKENS = 2
NAME_ENRICHMENT_POLICY_VERSION = "unique-name-id-v8"

UNAVAILABLE_GEMINI_MODELS: set[str] = set()
GEMINI_LIVE_CALL_ATTEMPTS = 0
GEMINI_MODEL_LAST_REQUEST_AT: dict[str, float] = {}
GEMINI_MISSING_API_KEY_WARNED = False
GEMINI_CALL_BUDGET_WARNED = False
GEMINI_CASE_HEADER_PRINTED = False
AMBIGUOUS_SINGLE_ALIASES: set[str] | None = None

NAME_PARTICLE_TOKENS = {"da", "de", "del", "di", "dos", "van", "von"}
HOMONYM_ID_METHODS = {"automatic_contextual_homonym_id", "automatic_known_as_homonym_id"}

OBSERVATION_COLUMNS = [
    "source",
    "source_player_key",
    "source_player_id",
    "observed_name",
    "normalized_alias",
    "id_competition",
    "id_season",
    "id_team",
    "positions",
    "position_roles",
    "sample_game",
    "observations",
]

IDENTITY_COLUMNS = [
    "id_player",
    "known_as",
    "full_name",
    "aliases",
    "source_player_keys",
    "id_understat",
    "id_whoscored",
    "competitions",
    "seasons",
    "teams",
    "resolution_method",
    "confidence",
    "needs_review",
]

ALIAS_MAP_COLUMNS = [
    "source",
    "source_player_key",
    "source_player_id",
    "observed_name",
    "normalized_alias",
    "id_competition",
    "id_season",
    "id_team",
    "sample_game",
    "observations",
    "id_player",
    "known_as",
    "full_name",
    "method",
    "confidence",
    "needs_review",
]

REVIEW_COLUMNS = [
    "review_id",
    "reason",
    "source",
    "source_player_key",
    "observed_name",
    "normalized_alias",
    "id_competition",
    "id_season",
    "id_team",
    "sample_game",
    "candidates",
    "suggested_action",
]

def load_local_env() -> None:
    for path in ENV_PATHS:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


load_local_env()


class DisjointSet:
    def __init__(self, values: list[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent.setdefault(value, value)
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> str:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        winner, loser = sorted([left_root, right_root])
        self.parent[loser] = winner
        return winner


@dataclass
class GeminiDecision:
    resolved: bool
    candidate_id: str | None
    confidence: float
    reason: str
    full_name: str | None = None
    known_as: str | None = None
    model: str = ""
    from_cache: bool = False
    candidate_fingerprint: str = ""


@dataclass
class IdentityNameHint:
    known_as: str = ""
    full_name: str = ""
    confidence: float = 1.0


def parse_source_id(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "<na>"}:
        return None
    try:
        numeric = float(text)
    except Exception:
        return text
    if not numeric.is_integer() or numeric <= 0:
        return None
    return str(int(numeric))


def _has_alphabetic_text(value) -> bool:
    if pd.isna(value):
        return False
    return any(ch.isalpha() for ch in str(value))


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "si"}


def _positive_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


def _non_negative_env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(0.0, parsed)


def gemini_progress_enabled() -> bool:
    return _env_bool("GEMINI_PROGRESS", True)


def _emit(prefix: str, message: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    text = f"{prefix} {message}"
    print(text.encode(encoding, errors="replace").decode(encoding), flush=True)


def _norm_log(message: str) -> None:
    _emit("[PlayerNorm]", message)


def _gemini_log(message: str) -> None:
    if gemini_progress_enabled():
        _emit("[Gemini]", message)


def _section(title: str) -> None:
    print()
    _norm_log("=" * 72)
    _norm_log(title)
    _norm_log("=" * 72)


def _gemini_section(title: str) -> None:
    if not gemini_progress_enabled():
        return
    print()
    _gemini_log("=" * 72)
    _gemini_log(title)
    _gemini_log("=" * 72)


def _log_value(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"nan", "<na>"} else text


def _format_log_list(values, fallback: str = "sin datos") -> str:
    if values is None:
        return fallback
    raw_values = values if isinstance(values, list) else [values]
    clean: list[str] = []
    for value in raw_values:
        text = _log_value(value)
        if text and text not in clean:
            clean.append(text)
    return ", ".join(clean) if clean else fallback


def _gemini_task_label(task: str) -> str:
    if task == "identity_resolution":
        return "[IDENTIDAD]"
    if task == "name_enrichment":
        return "[NOMBRES]"
    return "[GEMINI]"


def _gemini_decision_action(task: str, decision: GeminiDecision | None) -> str:
    if decision is None:
        return "sin_respuesta"
    if task == "identity_resolution":
        return "fusionar_con_candidato" if decision.resolved else "no_fusionar_con_candidatos"
    if task == "name_enrichment":
        return "nombre_propuesto" if decision.resolved else "nombre_no_resuelto"
    return "resuelto" if decision.resolved else "no_resuelto"


def _candidate_value(candidate: dict[str, object], *keys: str) -> str:
    values: list[str] = []
    for key in keys:
        value = candidate.get(key)
        raw_values = value if isinstance(value, list) else ([] if value is None else [value])
        for item in raw_values:
            text = _log_value(item)
            if text and text not in values:
                values.append(text)
    return _format_log_list(values)


def _gemini_log_response(task: str, source: str, decision: GeminiDecision, model: str = "") -> None:
    label = _gemini_task_label(task)
    model_text = f" modelo={model}" if model else ""
    _gemini_log(f"{label} {source}{model_text}")
    _gemini_log(f"  decision_gemini: {_gemini_decision_action(task, decision)} (resolved={decision.resolved})")
    if task == "identity_resolution":
        _gemini_log(f"  candidate_id: {decision.candidate_id or 'null'}")
    if task == "name_enrichment":
        _gemini_log("  propuesta:")
        _gemini_log(f"    known_as: {decision.known_as or 'null'}")
        _gemini_log(f"    full_name: {decision.full_name or 'null'}")
    _gemini_log(f"  confidence: {decision.confidence:.2f}")
    if decision.reason:
        _gemini_log(f"  motivo_gemini: {decision.reason}")


def _gemini_begin_case() -> None:
    global GEMINI_CASE_HEADER_PRINTED
    if not gemini_progress_enabled():
        return
    if GEMINI_CASE_HEADER_PRINTED:
        print()
    GEMINI_CASE_HEADER_PRINTED = True


def _gemini_payload_name(payload: dict[str, object]) -> str:
    for key in ["observed_name", "known_as", "full_name", "proposed_known_as", "proposed_full_name"]:
        value = _log_value(payload.get(key))
        if value:
            return value
    return "desconocido"


def _gemini_log_candidates(candidates: object) -> None:
    if isinstance(candidates, list) and candidates:
        for number, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            _gemini_log(f"    {number}. candidate_id={_log_value(candidate.get('candidate_id')) or 'null'}")
            _gemini_log(f"       nombres: {_candidate_value(candidate, 'names')}")
            _gemini_log(f"       equipos: {_candidate_value(candidate, 'teams')}")
            _gemini_log(f"       temporadas: {_candidate_value(candidate, 'seasons')}")
            _gemini_log(f"       fuentes: {_candidate_value(candidate, 'sources')}")
        return
    _gemini_log("    ninguno")


def _gemini_log_case_context(payload: dict[str, object]) -> None:
    reason = _log_value(payload.get("reason"))
    duplicate_full_name = _log_value(payload.get("duplicate_full_name"))
    duplicate_player_id = _log_value(payload.get("duplicate_player_id"))
    verification_stage = _log_value(payload.get("verification_stage"))
    proposed_known_as = _log_value(payload.get("proposed_known_as"))
    proposed_full_name = _log_value(payload.get("proposed_full_name"))

    if reason:
        _gemini_log(f"    motivo: {reason}")
    if duplicate_full_name:
        _gemini_log(f"    full_name duplicado: {duplicate_full_name}")
    if duplicate_player_id:
        _gemini_log(f"    id_player duplicado: {duplicate_player_id}")
    if verification_stage:
        _gemini_log(f"    verificacion: {verification_stage}")
    if proposed_known_as or proposed_full_name:
        _gemini_log("    propuesta_a_verificar:")
        _gemini_log(f"      known_as: {proposed_known_as or 'null'}")
        _gemini_log(f"      full_name: {proposed_full_name or 'null'}")


def _gemini_case_header(payload: dict[str, object], *, case_title: str | None = None) -> None:
    if not gemini_progress_enabled():
        return
    _gemini_begin_case()
    task = str(payload.get("task") or "identity_resolution").strip()
    label = _gemini_task_label(task)
    candidates = payload.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    _gemini_log(f"{label} {case_title or 'CASO GEMINI'}")
    _gemini_log("  Investigado:")
    _gemini_log(f"    tarea: {task}")
    _gemini_log(f"    nombre: {_gemini_payload_name(payload)}")
    _gemini_log(f"    known_as: {_log_value(payload.get('known_as')) or 'null'}")
    _gemini_log(f"    full_name: {_log_value(payload.get('full_name')) or 'null'}")
    _gemini_log(f"    equipos: {_format_log_list(_payload_list(payload, 'team', 'teams'), 'sin equipo')}")
    _gemini_log(f"    temporadas: {_format_log_list(_payload_list(payload, 'season', 'seasons'), 'sin temporada')}")
    _gemini_log(f"    fuentes: {_format_log_list(_payload_list(payload, 'source', 'sources'), 'sin fuente')}")
    _gemini_log(f"    source_keys: {_format_log_list(_payload_list(payload, 'source_player_key', 'source_player_keys'), 'sin source_keys')}")
    _gemini_log("  Contexto:")
    _gemini_log(f"    candidatos: {candidate_count}")
    _gemini_log_case_context(payload)
    _gemini_log("  Candidatos:")
    _gemini_log_candidates(candidates)


def _payload_list(payload: dict[str, object], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        iterable = value if isinstance(value, list) else ([] if value is None else [value])
        for item in iterable:
            text = _log_value(item)
            if text and text not in values:
                values.append(text)
    return values


def normalize_alias(value) -> str:
    if pd.isna(value):
        return ""
    return clean_identifier_text(str(value)).lower()


def normalize_context_competition(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return normalize_competition(text) if text else ""


def normalize_context_season(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return normalize_season(text) if text else ""


def normalize_context_team(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return normalize_team(text) if text else ""


def build_source_player_key(
    *,
    source: str,
    raw_name=None,
    source_player_id=None,
    team=None,
    competition=None,
    season=None,
) -> str | None:
    parsed_source_id = parse_source_id(source_player_id)
    if source in ID_BACKED_SOURCES and parsed_source_id:
        return f"{source}:{parsed_source_id}"
    if not _has_alphabetic_text(raw_name):
        return None
    alias = normalize_alias(raw_name)
    if not alias:
        return None
    parts = [
        source,
        normalize_context_competition(competition),
        normalize_context_season(season),
        normalize_context_team(team),
        alias,
    ]
    return ":".join(parts)


def build_source_player_keys_for_frame(
    df: pd.DataFrame,
    *,
    source: str,
    player_col: str,
    source_player_id_col: str | None = None,
    team_col: str | None = None,
    competition_col: str | None = None,
    season_col: str | None = None,
) -> pd.Series:
    keys = pd.Series(pd.NA, index=df.index, dtype="string")
    if source_player_id_col and source_player_id_col in df.columns:
        parsed_source_ids = df[source_player_id_col].apply(parse_source_id).astype("string")
        has_source_id = parsed_source_ids.notna()
        if source in ID_BACKED_SOURCES:
            keys.loc[has_source_id] = source + ":" + parsed_source_ids.loc[has_source_id].astype(str)

    fallback_mask = keys.isna()
    if player_col in df.columns:
        fallback_mask = fallback_mask & df[player_col].notna() & df[player_col].astype(str).str.strip().ne("")
    else:
        fallback_mask = pd.Series(False, index=df.index)

    if fallback_mask.any():
        fallback_df = df.loc[fallback_mask]

        def row_key(row: pd.Series) -> str | None:
            return build_source_player_key(
                source=source,
                raw_name=row.get(player_col),
                source_player_id=row.get(source_player_id_col) if source_player_id_col else None,
                team=row.get(team_col) if team_col else None,
                competition=row.get(competition_col) if competition_col else None,
                season=row.get(season_col) if season_col else None,
            )

        keys.loc[fallback_mask] = fallback_df.apply(row_key, axis=1).astype("string")
    return keys


def _split_joined_values(value: object) -> list[str]:
    if pd.isna(value):
        return []
    out: list[str] = []
    for part in str(value).split("|"):
        text = part.strip()
        if text and text.lower() != "nan" and text not in out:
            out.append(text)
    return out


def _join_values(values) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if pd.isna(value):
            continue
        text = str(value).strip()
        if not text or text.lower() == "nan":
            continue
        if text not in seen:
            out.append(text)
            seen.add(text)
    return " | ".join(sorted(out))


def _clean_position_value(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return " ".join(text.split())


def _position_roles(value: object) -> list[str]:
    text = _clean_position_value(value)
    if not text:
        return []
    normalized = normalize_alias(text)
    tokens = {token for token in normalized.split("_") if token}
    compact = normalized.replace("_", "")
    roles: list[str] = []

    def add(role: str) -> None:
        if role not in roles:
            roles.append(role)

    if tokens & {"gk", "goalkeeper", "keeper", "goalie", "portero"} or "goalkeeper" in compact:
        add("goalkeeper")
    if tokens & {"sub", "substitute", "s", "bench", "suplente"} or "substitute" in compact:
        add("substitute")
    if (
        tokens & {"d", "dc", "dl", "dr", "df", "cb", "lb", "rb", "lcb", "rcb", "defender", "back", "fullback"}
        or "defender" in compact
        or "fullback" in compact
    ):
        add("defender")
    if tokens & {"m", "mc", "ml", "mr", "dm", "cm", "lm", "rm", "am", "dmc", "amc", "midfielder"} or "midfielder" in compact:
        add("midfielder")
    if tokens & {"f", "fw", "st", "cf", "ss", "lw", "rw", "forward", "striker", "winger"} or "forward" in compact:
        add("forward")
    return [role for role in ["goalkeeper", "defender", "midfielder", "forward", "substitute"] if role in roles]


def _position_roles_value(value: object) -> str:
    return " | ".join(_position_roles(value))


def _join_position_evidence(values) -> str:
    counts: Counter[str] = Counter()
    display_by_key: dict[str, str] = {}
    for value in values:
        if pd.isna(value):
            continue
        for raw_part in str(value).split("|"):
            part = _clean_position_value(raw_part)
            if not part:
                continue
            match = re.match(r"^(.*?)\s+\((\d+)\)$", part)
            count = 1
            if match:
                part = match.group(1).strip()
                count = int(match.group(2))
            key = normalize_alias(part)
            if not key:
                continue
            counts[key] += count
            display_by_key.setdefault(key, part)
    return " | ".join(f"{display_by_key[key]} ({count})" for key, count in sorted(counts.items()))


def _first_non_empty(values) -> str:
    for value in values:
        text = _log_value(value)
        if text:
            return text
    return ""


def _read_csv_selected(path: Path, columns: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path, dtype="string", usecols=lambda col: col in set(columns))
    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA
    return df[columns]


def _target_raw_paths(*parts: str, pattern: str) -> list[Path]:
    base = PROJECT_ROOT / "data" / "raw"
    for part in parts:
        base = base / part
    return sorted(path for path in base.glob(pattern) if path_has_target_scope(path))


def _target_team_contexts() -> set[tuple[str, str, str]]:
    if not TEAM_COMPETITION_SEASON_PATH.exists():
        return set()
    columns = ["id_competition", "id_season", "id_team"]
    df = pd.read_csv(TEAM_COMPETITION_SEASON_PATH, dtype="string", usecols=columns).fillna("")
    df = filter_target_id_seasons(df)
    return {(str(row.id_competition), str(row.id_season), str(row.id_team)) for row in df.itertuples(index=False)}


def _filter_observations_to_target_team_contexts(observations: pd.DataFrame) -> pd.DataFrame:
    contexts = _target_team_contexts()
    if not contexts or observations.empty:
        return observations
    mask = observations.apply(
        lambda row: (str(row["id_competition"]), str(row["id_season"]), str(row["id_team"])) in contexts,
        axis=1,
    )
    return observations.loc[mask].copy()


def _build_observations_from_frame(
    df: pd.DataFrame,
    *,
    source: str,
    player_col: str,
    source_player_id_col: str | None,
    team_col: str,
    competition_col: str,
    season_col: str,
    game_col: str | None = None,
    position_col: str | None = None,
) -> pd.DataFrame:
    if player_col not in df.columns:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    frame = pd.DataFrame(index=df.index)
    frame["source"] = source
    frame["source_player_id"] = (
        df[source_player_id_col].apply(parse_source_id).astype("string")
        if source_player_id_col and source_player_id_col in df.columns
        else pd.Series(pd.NA, index=df.index, dtype="string")
    )
    frame["observed_name"] = df[player_col].astype("string").fillna("").str.strip()
    frame["normalized_alias"] = frame["observed_name"].apply(normalize_alias)
    frame["id_competition"] = df[competition_col].apply(normalize_context_competition)
    frame["id_season"] = df[season_col].apply(normalize_context_season)
    frame["id_team"] = df[team_col].apply(normalize_context_team)
    target_mask = (
        frame["id_season"].isin(TARGET_SEASON_SET) & frame["id_competition"].isin(TARGET_COMPETITION_SET)
        if SCOPE_ACTIVE
        else pd.Series(True, index=frame.index)
    )
    if not target_mask.any():
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    df = df.loc[target_mask].copy()
    frame = frame.loc[target_mask].copy()
    frame["sample_game"] = (
        df[game_col].astype("string").fillna("").str.strip()
        if game_col and game_col in df.columns
        else pd.Series("", index=df.index, dtype="string")
    )
    frame["positions"] = (
        df[position_col].apply(_clean_position_value).astype("string")
        if position_col and position_col in df.columns
        else pd.Series("", index=df.index, dtype="string")
    )
    frame["position_roles"] = frame["positions"].apply(_position_roles_value).astype("string")
    frame["source_player_key"] = build_source_player_keys_for_frame(
        df,
        source=source,
        player_col=player_col,
        source_player_id_col=source_player_id_col,
        team_col=team_col,
        competition_col=competition_col,
        season_col=season_col,
    )
    frame = frame[
        frame["source_player_key"].notna()
        & frame["observed_name"].notna()
        & frame["observed_name"].astype(str).str.strip().ne("")
        & frame["observed_name"].apply(_has_alphabetic_text)
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    group_cols = [
        "source",
        "source_player_key",
        "source_player_id",
        "observed_name",
        "normalized_alias",
        "id_competition",
        "id_season",
        "id_team",
    ]
    frame = (
        frame.groupby(group_cols, dropna=False)
        .agg(
            positions=("positions", _join_position_evidence),
            position_roles=("position_roles", _join_position_evidence),
            sample_game=("sample_game", _first_non_empty),
            observations=("source_player_key", "size"),
        )
        .reset_index()
    )
    return frame[OBSERVATION_COLUMNS]


def _build_related_player_observations(df: pd.DataFrame) -> pd.DataFrame:
    if "related_player_id" not in df.columns:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    frame = pd.DataFrame(index=df.index)
    frame["source"] = "whoscored"
    frame["source_player_id"] = df["related_player_id"].apply(parse_source_id).astype("string")
    frame = frame[frame["source_player_id"].notna()].copy()
    if frame.empty:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    frame["source_player_key"] = "whoscored:" + frame["source_player_id"].astype(str)
    frame["observed_name"] = ""
    frame["normalized_alias"] = ""
    frame["id_competition"] = df.loc[frame.index, "league"].apply(normalize_context_competition)
    frame["id_season"] = df.loc[frame.index, "season"].apply(normalize_context_season)
    frame["id_team"] = df.loc[frame.index, "team"].apply(normalize_context_team)
    frame["positions"] = ""
    frame["position_roles"] = ""
    if SCOPE_ACTIVE:
        frame = frame[
            frame["id_season"].isin(TARGET_SEASON_SET)
            & frame["id_competition"].isin(TARGET_COMPETITION_SET)
        ].copy()
    if frame.empty:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    frame["sample_game"] = df.loc[frame.index, "game"].astype("string").fillna("").str.strip()
    return (
        frame.groupby(
            [
                "source",
                "source_player_key",
                "source_player_id",
                "observed_name",
                "normalized_alias",
                "id_competition",
                "id_season",
                "id_team",
            ],
            dropna=False,
        )
        .agg(
            positions=("positions", _join_position_evidence),
            position_roles=("position_roles", _join_position_evidence),
            sample_game=("sample_game", _first_non_empty),
            observations=("source_player_key", "size"),
        )
        .reset_index()[OBSERVATION_COLUMNS]
    )


def collect_player_observations() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in _target_raw_paths("espn", "lineup", pattern="read_lineup_*.csv"):
        df = _read_csv_selected(
            path,
            ["league", "season", "game", "team", "player", "position", "appearances", "sub_ins", "sub_in", "sub_out"],
        )
        frames.append(
            _build_observations_from_frame(
                df,
                source="espn",
                player_col="player",
                source_player_id_col=None,
                team_col="team",
                competition_col="league",
                season_col="season",
                game_col="game",
                position_col="position",
            )
        )
    for path in _target_raw_paths("understat", "player_match_stats", pattern="read_player_match_stats_*.csv"):
        df = _read_csv_selected(path, ["league", "season", "game", "team", "player", "player_id", "position"])
        frames.append(
            _build_observations_from_frame(
                df,
                source="understat",
                player_col="player",
                source_player_id_col="player_id",
                team_col="team",
                competition_col="league",
                season_col="season",
                game_col="game",
                position_col="position",
            )
        )
    for path in _target_raw_paths("understat", "player_season_stats", pattern="read_player_season_stats_*.csv"):
        df = _read_csv_selected(path, ["league", "season", "team", "player", "player_id"])
        frames.append(
            _build_observations_from_frame(
                df,
                source="understat",
                player_col="player",
                source_player_id_col="player_id",
                team_col="team",
                competition_col="league",
                season_col="season",
            )
        )
    for path in _target_raw_paths("whoscored", "missing_players", pattern="read_missing_players_*.csv"):
        df = _read_csv_selected(path, ["league", "season", "game", "team", "player", "player_id"])
        frames.append(
            _build_observations_from_frame(
                df,
                source="whoscored",
                player_col="player",
                source_player_id_col="player_id",
                team_col="team",
                competition_col="league",
                season_col="season",
                game_col="game",
            )
        )
    for path in _target_raw_paths("whoscored", "events", pattern="read_events_*.csv"):
        df = _read_csv_selected(path, ["league", "season", "game", "team", "player", "player_id", "related_player_id"])
        frames.append(
            _build_observations_from_frame(
                df,
                source="whoscored",
                player_col="player",
                source_player_id_col="player_id",
                team_col="team",
                competition_col="league",
                season_col="season",
                game_col="game",
            )
        )
        frames.append(_build_related_player_observations(df))

    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise FileNotFoundError("No existen observaciones raw de jugadores para normalizar")
    observations = pd.concat(frames, ignore_index=True)
    observations = (
        observations.groupby(
            [
                "source",
                "source_player_key",
                "source_player_id",
                "observed_name",
                "normalized_alias",
                "id_competition",
                "id_season",
                "id_team",
            ],
            dropna=False,
        )
        .agg(
            positions=("positions", _join_position_evidence),
            position_roles=("position_roles", _join_position_evidence),
            sample_game=("sample_game", _first_non_empty),
            observations=("observations", "sum"),
        )
        .reset_index()
    )
    observations = _filter_observations_to_target_team_contexts(observations)
    if observations.empty:
        raise FileNotFoundError("No existen observaciones raw de jugadores para equipos de las ligas/temporadas objetivo")
    return observations[OBSERVATION_COLUMNS].sort_values(
        by=["source", "id_competition", "id_season", "id_team", "normalized_alias", "source_player_key"]
    ).reset_index(drop=True)


def load_previous_alias_map() -> pd.DataFrame:
    if not _previous_normalization_is_clean():
        return pd.DataFrame(columns=ALIAS_MAP_COLUMNS)
    if not PLAYER_ALIAS_MAP_PATH.exists():
        return pd.DataFrame(columns=ALIAS_MAP_COLUMNS)
    return pd.read_csv(PLAYER_ALIAS_MAP_PATH, dtype="string").fillna("")


def load_previous_identities() -> pd.DataFrame:
    if not _previous_normalization_is_clean():
        return pd.DataFrame(columns=IDENTITY_COLUMNS)
    if not PLAYER_IDENTITIES_PATH.exists():
        return pd.DataFrame(columns=IDENTITY_COLUMNS)
    return pd.read_csv(PLAYER_IDENTITIES_PATH, dtype="string").fillna("")


def _previous_normalization_is_clean() -> bool:
    if PLAYER_REVIEW_QUEUE_PATH.exists():
        try:
            review_queue = pd.read_csv(PLAYER_REVIEW_QUEUE_PATH, dtype="string").fillna("")
        except Exception:
            return False
        if not review_queue.empty:
            return False
    if PLAYER_REPORT_PATH.exists():
        try:
            with PLAYER_REPORT_PATH.open(encoding="utf-8") as handle:
                report = json.load(handle)
        except Exception:
            return False
        if int(report.get("pending_review") or 0) > 0:
            return False
    return True


def _context_set(rows: pd.DataFrame) -> set[tuple[str, str, str]]:
    return {(str(row.id_competition), str(row.id_season), str(row.id_team)) for row in rows.itertuples(index=False)}


def _has_context_overlap(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return bool(_context_set(left) & _context_set(right))


def _best_name_for_key(rows: pd.DataFrame) -> str:
    names = [str(value).strip() for value in rows["observed_name"] if str(value).strip()]
    if not names:
        return ""
    return Counter(names).most_common(1)[0][0]


def _name_tokens(name: str) -> list[str]:
    return [token for token in normalize_alias(name).split("_") if token]


def _normalized_token_signature(name: str) -> list[str]:
    return _name_tokens(name)


def _has_poor_abbreviation(name: str) -> bool:
    text = str(name).strip()
    if not text:
        return True
    if re.search(r"\b[A-ZÃÃ‰ÃÃ“ÃšÃ‘]\.\s+", text):
        return True
    if re.match(r"^[A-ZÃÃ‰ÃÃ“ÃšÃ‘]\.\s*\S+", text):
        return True
    if re.search(r"\b[\wÃÃ‰ÃÃ“ÃšÃœÃ‘Ã¡Ã©Ã­Ã³ÃºÃ¼Ã±]{1,3}\.\s*$", text):
        return True
    return False


def has_expanded_full_name(name: str) -> bool:
    return len(_name_tokens(str(name or ""))) >= MIN_FULL_NAME_TOKENS and not _has_poor_abbreviation(str(name or ""))


def is_ambiguous_single_name(name: str) -> bool:
    tokens = _name_tokens(name)
    return len(tokens) == 1 and tokens[0] in _configured_ambiguous_single_aliases()


def _is_safe_exact_identity_name(name: str) -> bool:
    tokens = _name_tokens(name)
    return len(tokens) >= 2 or (len(tokens) == 1 and not is_ambiguous_single_name(name))


def is_poor_display_name(name: str) -> bool:
    return _has_poor_abbreviation(name) or is_ambiguous_single_name(name)


def _tokens_from_normalized_alias(value: object) -> list[str]:
    if pd.isna(value):
        return []
    return [token for token in str(value).strip().split("_") if token]


def _row_context(row: object) -> tuple[str, str, str]:
    return (
        str(getattr(row, "id_competition", "")),
        str(getattr(row, "id_season", "")),
        str(getattr(row, "id_team", "")),
    )


def build_ambiguous_single_aliases(observations: pd.DataFrame) -> set[str]:
    single_contexts: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    longer_aliases_by_first_token: dict[str, set[str]] = defaultdict(set)
    longer_contexts_by_first_token: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    if observations.empty:
        return set()
    for row in observations.itertuples(index=False):
        alias_value = getattr(row, "normalized_alias", "")
        if pd.isna(alias_value) or not str(alias_value).strip():
            alias_value = normalize_alias(getattr(row, "observed_name", ""))
        tokens = _tokens_from_normalized_alias(alias_value)
        if not tokens:
            continue
        context = _row_context(row)
        if len(tokens) == 1:
            single_contexts[tokens[0]].add(context)
        else:
            longer_aliases_by_first_token[tokens[0]].add("_".join(tokens))
            longer_contexts_by_first_token[tokens[0]].add(context)
    ambiguous: set[str] = set()
    for token, contexts in single_contexts.items():
        longer_aliases = longer_aliases_by_first_token.get(token, set())
        if not longer_aliases:
            continue
        if contexts & longer_contexts_by_first_token.get(token, set()) or len(longer_aliases) >= 2:
            ambiguous.add(token)
    return ambiguous


def configure_ambiguous_single_aliases(aliases: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    global AMBIGUOUS_SINGLE_ALIASES
    AMBIGUOUS_SINGLE_ALIASES = {str(alias).strip() for alias in aliases if str(alias).strip()}
    return AMBIGUOUS_SINGLE_ALIASES


def configure_name_quality_from_observations(observations: pd.DataFrame) -> set[str]:
    return configure_ambiguous_single_aliases(build_ambiguous_single_aliases(observations))


def _load_ambiguous_single_aliases_from_report() -> set[str]:
    if not PLAYER_REPORT_PATH.exists():
        return set()
    try:
        with PLAYER_REPORT_PATH.open(encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception:
        return set()
    aliases = report.get("ambiguous_single_aliases", [])
    return {str(alias).strip() for alias in aliases if str(alias).strip()} if isinstance(aliases, list) else set()


def _configured_ambiguous_single_aliases() -> set[str]:
    global AMBIGUOUS_SINGLE_ALIASES
    if AMBIGUOUS_SINGLE_ALIASES is None:
        AMBIGUOUS_SINGLE_ALIASES = _load_ambiguous_single_aliases_from_report()
    return AMBIGUOUS_SINGLE_ALIASES


def _is_contiguous_subsequence(shorter: list[str], longer: list[str]) -> bool:
    if len(shorter) > len(longer):
        return False
    return any(longer[index : index + len(shorter)] == shorter for index in range(len(longer) - len(shorter) + 1))


def _content_name_tokens(name: str) -> list[str]:
    return [token for token in _normalized_token_signature(name) if token not in NAME_PARTICLE_TOKENS]


def _tokens_are_loose_name_variant(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if _tokens_are_name_variant(left, right):
        return True
    if left[0] != right[0]:
        return False

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 4:
        return False
    if shorter[:3] == longer[:3] and len(shorter) <= 5:
        return True
    if shorter[:4] == longer[:4]:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.78


def _has_token_set_identity_match(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    if left_set == right_set:
        return True
    shorter, longer = (left_set, right_set) if len(left_set) <= len(right_set) else (right_set, left_set)
    if len(shorter) >= 2 and shorter.issubset(longer):
        return True

    shared = left_set & right_set
    if not shared:
        return False
    if len(shared) >= 2:
        return True
    return False


def _has_cross_position_token_bridge_candidate(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    for shared_token in sorted(set(left_tokens) & set(right_tokens)):
        if len(shared_token) < 4 or is_ambiguous_single_name(shared_token):
            continue
        left_positions = [index for index, token in enumerate(left_tokens) if token == shared_token]
        right_positions = [index for index, token in enumerate(right_tokens) if token == shared_token]
        if any(
            (left_pos == len(left_tokens) - 1 and right_pos == 0)
            or (right_pos == len(right_tokens) - 1 and left_pos == 0)
            for left_pos in left_positions
            for right_pos in right_positions
        ):
            return True
    return False


def _has_safe_token_containment(left: str, right: str) -> bool:
    left_tokens = _normalized_token_signature(left)
    right_tokens = _normalized_token_signature(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    shorter, longer = (left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens) else (right_tokens, left_tokens)
    return _is_contiguous_subsequence(shorter, longer)


def _tokens_have_prefix_relation(left: str, right: str, min_prefix_length: int = 2) -> bool:
    if not left or not right or left == right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= min_prefix_length and longer.startswith(shorter)


def _tokens_are_name_variant(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    if left[0] != right[0]:
        return False
    if _tokens_have_prefix_relation(left, right):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.84


def _tokens_are_long_orthographic_variant(left: str, right: str) -> bool:
    if not left or not right or left == right:
        return False
    if min(len(left), len(right)) < 8:
        return False
    if left[:2] != right[:2]:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.78


def _has_single_token_alias_match(left: str, right: str) -> bool:
    left_tokens = _normalized_token_signature(left)
    right_tokens = _normalized_token_signature(right)
    if len(left_tokens) == 1 and len(right_tokens) > 1:
        return not is_ambiguous_single_name(left_tokens[0]) and left_tokens[0] in right_tokens
    if len(right_tokens) == 1 and len(left_tokens) > 1:
        return not is_ambiguous_single_name(right_tokens[0]) and right_tokens[0] in left_tokens
    return False


def _has_safe_token_prefix_match(left: str, right: str) -> bool:
    left_tokens = _normalized_token_signature(left)
    right_tokens = _normalized_token_signature(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2 or len(left_tokens) != len(right_tokens):
        return False
    exact_pairs = 0
    variant_pairs = 0
    for left_token, right_token in zip(left_tokens, right_tokens):
        if left_token == right_token:
            exact_pairs += 1
            continue
        if not _tokens_are_name_variant(left_token, right_token):
            return False
        variant_pairs += 1
    return exact_pairs >= 1 and variant_pairs >= 1


def _has_same_last_name_with_given_name_variant(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    if left_tokens[-1] != right_tokens[-1]:
        return False
    return _tokens_are_loose_name_variant(left_tokens[0], right_tokens[0])


def _has_single_token_name_variant_match(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) == 1 and len(right_tokens) >= 2:
        return not is_ambiguous_single_name(left_tokens[0]) and _tokens_are_loose_name_variant(left_tokens[0], right_tokens[0])
    if len(right_tokens) == 1 and len(left_tokens) >= 2:
        return not is_ambiguous_single_name(right_tokens[0]) and _tokens_are_loose_name_variant(right_tokens[0], left_tokens[0])
    return False


def _has_safe_long_orthographic_name_match(left: str, right: str) -> bool:
    left_tokens = _normalized_token_signature(left)
    right_tokens = _normalized_token_signature(right)
    if len(left_tokens) < 2 or len(left_tokens) != len(right_tokens):
        return False

    exact_pairs = 0
    variant_pairs = 0
    for left_token, right_token in zip(left_tokens, right_tokens):
        if left_token == right_token:
            exact_pairs += 1
            continue
        if not _tokens_are_long_orthographic_variant(left_token, right_token):
            return False
        variant_pairs += 1
    return exact_pairs >= 1 and variant_pairs == 1


def _has_safe_identity_name_match(left: str, right: str, *, allow_single_token: bool = True) -> bool:
    if not left or not right:
        return False
    if normalize_alias(left) == normalize_alias(right):
        return True
    if _has_safe_token_containment(left, right):
        return True
    if _has_token_set_identity_match(left, right):
        return True
    if _has_safe_token_prefix_match(left, right):
        return True
    if _has_same_last_name_with_given_name_variant(left, right):
        return True
    if _has_single_token_name_variant_match(left, right):
        return True
    return allow_single_token and _has_single_token_alias_match(left, right)


def _has_same_surname_distinct_given_name(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    if left_tokens[-1] != right_tokens[-1]:
        return False
    if _tokens_are_loose_name_variant(left_tokens[0], right_tokens[0]):
        return False
    if _has_safe_token_containment(left, right) or _has_safe_token_prefix_match(left, right):
        return False
    return True


def _names_need_gemini(observed: str, candidate: str) -> bool:
    if not observed or not candidate:
        return False
    if normalize_alias(observed) == normalize_alias(candidate):
        return True
    if _has_safe_token_containment(observed, candidate):
        return True
    if _has_token_set_identity_match(observed, candidate):
        return True
    if _has_safe_token_prefix_match(observed, candidate):
        return True
    if _has_single_token_alias_match(observed, candidate):
        return True
    if _has_single_token_name_variant_match(observed, candidate):
        return True
    if _has_same_surname_distinct_given_name(observed, candidate):
        return True
    if _has_cross_position_token_bridge_candidate(observed, candidate):
        return True
    ratio = SequenceMatcher(None, normalize_alias(observed), normalize_alias(candidate)).ratio()
    return 0.72 <= ratio < 0.96


def _source_ids_for_keys(keys: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for key in keys:
        if ":" not in key:
            continue
        source, rest = key.split(":", 1)
        if source in ID_BACKED_SOURCES and ":" not in rest:
            out[source].add(rest)
    return out


def _would_create_source_id_conflict(left_keys: list[str], right_keys: list[str]) -> bool:
    source_ids = _source_ids_for_keys(left_keys + right_keys)
    return any(len(values) > 1 for values in source_ids.values())


def _any_safe_identity_name_match(
    observed_names: list[str],
    candidate_names: list[str],
    *,
    allow_single_token: bool = True,
) -> bool:
    return any(
        _has_safe_identity_name_match(observed_name, candidate_name, allow_single_token=allow_single_token)
        for observed_name in observed_names
        for candidate_name in candidate_names
    )


def _tokens_supported_by_bridge(name_tokens: list[str], bridge_tokens: list[str]) -> bool:
    if not name_tokens or not bridge_tokens:
        return False
    return all(
        any(token == bridge_token or _tokens_are_name_variant(token, bridge_token) for bridge_token in bridge_tokens)
        for token in name_tokens
    )


def _bridge_tokens(*names: str | None) -> list[str]:
    tokens: list[str] = []
    for name in names:
        for token in _normalized_token_signature(str(name or "")):
            if token not in tokens:
                tokens.append(token)
    return tokens


def _has_gemini_full_name_bridge(
    observed_names: list[str],
    candidate_names: list[str],
    bridge_full_name: str,
    bridge_known_as: str | None,
    confidence: float,
) -> bool:
    if confidence < GEMINI_NICKNAME_ACCEPT_CONFIDENCE or not has_expanded_full_name(bridge_full_name):
        return False
    bridge_tokens = _bridge_tokens(bridge_full_name, bridge_known_as)
    if len(bridge_tokens) < 3:
        return False
    for observed_name in observed_names:
        observed_tokens = _normalized_token_signature(observed_name)
        observed_content = _content_name_tokens(observed_name)
        if len(observed_tokens) < 2 or len(observed_content) < 2:
            continue
        for candidate_name in candidate_names:
            candidate_tokens = _normalized_token_signature(candidate_name)
            candidate_content = _content_name_tokens(candidate_name)
            if len(candidate_tokens) < 2 or len(candidate_content) < 2:
                continue
            if not _tokens_supported_by_bridge(observed_tokens, bridge_tokens):
                continue
            if not _tokens_supported_by_bridge(candidate_tokens, bridge_tokens):
                continue
            shared_content = set(observed_content) & set(candidate_content)
            combined_tokens = set(observed_content) | set(candidate_content)
            if shared_content and shared_content.issubset(set(bridge_tokens)) and len(combined_tokens) >= 3:
                return True
    return False


def _is_safe_gemini_identity_merge(
    observed: str,
    candidate: str,
    confidence: float,
    observed_aliases: list[str] | None = None,
    candidate_aliases: list[str] | None = None,
    bridge_full_name: str | None = None,
    bridge_known_as: str | None = None,
) -> bool:
    observed_names = [name for name in [observed, *(observed_aliases or [])] if str(name).strip()]
    candidate_names = [name for name in [candidate, *(candidate_aliases or [])] if str(name).strip()]
    if _any_safe_identity_name_match(observed_names, candidate_names):
        return True
    if bridge_full_name and _has_gemini_full_name_bridge(
        observed_names,
        candidate_names,
        bridge_full_name,
        bridge_known_as,
        confidence,
    ):
        return True
    if any(
        _has_same_surname_distinct_given_name(observed_name, candidate_name)
        for observed_name in observed_names
        for candidate_name in candidate_names
    ):
        return False
    observed_tokens = _normalized_token_signature(observed)
    candidate_tokens = _normalized_token_signature(candidate)
    if len(observed_tokens) >= 2 and len(candidate_tokens) >= 2:
        same_first_name = observed_tokens[0] == candidate_tokens[0]
        different_last_name = observed_tokens[-1] != candidate_tokens[-1]
        last_name_similarity = SequenceMatcher(None, observed_tokens[-1], candidate_tokens[-1]).ratio()
        if same_first_name and different_last_name and last_name_similarity < 0.84 and confidence < 0.98:
            return False
    return True


def _cache_list(payload: dict[str, object], *keys: str, normalize_values: bool = False) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        iterable = value if isinstance(value, list) else ([] if value is None else [value])
        for item in iterable:
            text = _log_value(item)
            if normalize_values:
                text = normalize_alias(text)
            if text and text not in values:
                values.append(text)
    return sorted(values)


def _candidate_identity_payload(candidate: dict[str, object]) -> dict[str, object]:
    id_backed_source_ids = _id_backed_values(_cache_list(candidate, "source_player_ids"))
    if id_backed_source_ids:
        return {"id_backed_source_ids": id_backed_source_ids}
    source_player_keys = _cache_list(candidate, "source_player_keys")
    if source_player_keys:
        return {"source_player_keys": source_player_keys}
    source_player_ids = _cache_list(candidate, "source_player_ids")
    if source_player_ids:
        return {"source_player_ids": source_player_ids}
    candidate_id = _log_value(candidate.get("candidate_id"))
    if candidate_id:
        return {"candidate_id": candidate_id}
    return {"names": _cache_list(candidate, "names", normalize_values=True)}


def _candidate_identity_fingerprint(candidate: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(_candidate_identity_payload(candidate), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _candidate_fingerprint_for_id(payload: dict[str, object], candidate_id: object) -> str:
    candidate_id_text = _log_value(candidate_id)
    if not candidate_id_text:
        return ""
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if _log_value(candidate.get("candidate_id")) == candidate_id_text:
            return _candidate_identity_fingerprint(candidate)
    return ""


def _candidate_id_for_fingerprint(payload: dict[str, object], candidate_fingerprint: str) -> str:
    if not candidate_fingerprint:
        return ""
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if _candidate_identity_fingerprint(candidate) == candidate_fingerprint:
            return _log_value(candidate.get("candidate_id"))
    return ""


def _cache_candidates(payload: dict[str, object]) -> list[dict[str, object]]:
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    normalized: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        normalized.append(
            {
                "candidate_identity": _candidate_identity_payload(candidate),
                "names": _cache_list(candidate, "names", normalize_values=True),
                "source_player_ids": _cache_list(candidate, "source_player_ids"),
                "source_player_keys": _cache_list(candidate, "source_player_keys"),
                "sources": _cache_list(candidate, "sources"),
                "contexts": _cache_list(candidate, "contexts"),
            }
        )
    return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


def _id_backed_values(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _log_value(value)
        if ":" not in text:
            continue
        source, source_id = text.split(":", 1)
        if source in ID_BACKED_SOURCES and source_id and ":" not in source_id and text not in out:
            out.append(text)
    return sorted(out)


def _secondary_cache_candidates(payload: dict[str, object]) -> list[dict[str, object]] | None:
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return None

    normalized: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        source_ids = _id_backed_values(_cache_list(candidate, "source_player_ids"))
        if not source_ids:
            return None
        normalized.append(
            {
                "names": _cache_list(candidate, "names", normalize_values=True),
                "source_player_ids": source_ids,
            }
        )
    return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


def _gemini_cache_variant(payload: dict[str, object], *, retry: bool = False) -> dict[str, object]:
    task = str(payload.get("task") or "identity_resolution").strip()
    variant: dict[str, object] = {"task": task, "retry": bool(retry)}
    if task == "name_enrichment":
        variant.update(
            {
                "reason": _log_value(payload.get("reason")),
                "verification_stage": _log_value(payload.get("verification_stage")),
            }
        )
    return variant


def _gemini_cache_key_payload(payload: dict[str, object]) -> dict[str, object]:
    task = str(payload.get("task") or "identity_resolution").strip()
    if task == "identity_resolution":
        return {
            "task": task,
            "observed_name": normalize_alias(payload.get("observed_name")),
            "source_player_keys": _cache_list(payload, "source_player_keys"),
            "aliases": _cache_list(payload, "aliases", normalize_values=True),
            "competitions": _cache_list(payload, "competition", "competitions"),
            "seasons": _cache_list(payload, "season", "seasons"),
            "teams": _cache_list(payload, "team", "teams"),
            "candidates": _cache_candidates(payload),
        }
    if task == "name_enrichment":
        return {
            "task": task,
            "known_as": normalize_alias(payload.get("known_as")),
            "full_name": normalize_alias(payload.get("full_name")),
            "aliases": _cache_list(payload, "aliases", normalize_values=True),
            "source_player_keys": _cache_list(payload, "source_player_keys"),
            "id_understat": _cache_list(payload, "id_understat"),
            "id_whoscored": _cache_list(payload, "id_whoscored"),
            "competitions": _cache_list(payload, "competition", "competitions"),
            "seasons": _cache_list(payload, "season", "seasons"),
            "teams": _cache_list(payload, "team", "teams"),
            "name_enrichment_policy": _log_value(payload.get("name_enrichment_policy")),
            "verification_stage": _log_value(payload.get("verification_stage")),
            "proposed_known_as": normalize_alias(payload.get("proposed_known_as")),
            "proposed_full_name": normalize_alias(payload.get("proposed_full_name")),
            "requires_unique_full_name": bool(payload.get("requires_unique_full_name")),
            "requires_disambiguating_full_name": bool(payload.get("requires_disambiguating_full_name")),
            "duplicate_full_name": normalize_alias(payload.get("duplicate_full_name")),
            "duplicate_player_id": normalize_alias(payload.get("duplicate_player_id")),
            "reason": _log_value(payload.get("reason")),
        }
    raise ValueError(f"Tarea Gemini no soportada: {task}")


def _gemini_payload_cache_key(payload: dict[str, object], *, retry: bool = False) -> str:
    key_payload: dict[str, object] = {
        "variant": _gemini_cache_variant(payload, retry=retry),
        "payload": _gemini_cache_key_payload(payload),
    }
    return hashlib.sha256(json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _gemini_secondary_cache_key_payload(payload: dict[str, object]) -> dict[str, object] | None:
    task = str(payload.get("task") or "identity_resolution").strip()
    if task == "identity_resolution":
        source_ids = _id_backed_values(_cache_list(payload, "source_player_ids"))
        candidates = _secondary_cache_candidates(payload)
        if not source_ids or candidates is None:
            return None
        return {
            "task": task,
            "observed_name": normalize_alias(payload.get("observed_name")),
            "source_player_ids": source_ids,
            "aliases": _cache_list(payload, "aliases", normalize_values=True),
            "candidates": candidates,
        }
    if task == "name_enrichment":
        source_ids = _id_backed_values(
            [
                *(f"understat:{value}" for value in _cache_list(payload, "id_understat")),
                *(f"whoscored:{value}" for value in _cache_list(payload, "id_whoscored")),
            ]
        )
        if not source_ids:
            return None
        return {
            "task": task,
            "known_as": normalize_alias(payload.get("known_as")),
            "full_name": normalize_alias(payload.get("full_name")),
            "aliases": _cache_list(payload, "aliases", normalize_values=True),
            "source_player_ids": source_ids,
            "name_enrichment_policy": _log_value(payload.get("name_enrichment_policy")),
            "verification_stage": _log_value(payload.get("verification_stage")),
            "proposed_known_as": normalize_alias(payload.get("proposed_known_as")),
            "proposed_full_name": normalize_alias(payload.get("proposed_full_name")),
            "requires_unique_full_name": bool(payload.get("requires_unique_full_name")),
            "requires_disambiguating_full_name": bool(payload.get("requires_disambiguating_full_name")),
            "duplicate_full_name": normalize_alias(payload.get("duplicate_full_name")),
            "duplicate_player_id": normalize_alias(payload.get("duplicate_player_id")),
            "reason": _log_value(payload.get("reason")),
        }
    return None


def _gemini_secondary_payload_cache_key(payload: dict[str, object], *, retry: bool = False) -> str | None:
    secondary_payload = _gemini_secondary_cache_key_payload(payload)
    if secondary_payload is None:
        return None
    key_payload: dict[str, object] = {
        "variant": _gemini_cache_variant(payload, retry=retry),
        "secondary_payload": secondary_payload,
    }
    return hashlib.sha256(
        json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _load_gemini_prompt_template() -> str:
    if not GEMINI_PROMPT_PATH.exists():
        raise FileNotFoundError(f"No existe el prompt de Gemini: {GEMINI_PROMPT_PATH}")
    return GEMINI_PROMPT_PATH.read_text(encoding="utf-8")


def _load_gemini_cache() -> dict[str, GeminiDecision]:
    cache: dict[str, GeminiDecision] = {}
    cache_paths = [PLAYER_GEMINI_CACHE_PATH]
    if LEGACY_PLAYER_GEMINI_CACHE_PATH != PLAYER_GEMINI_CACHE_PATH:
        cache_paths.append(LEGACY_PLAYER_GEMINI_CACHE_PATH)
    for cache_path in cache_paths:
        if not cache_path.exists():
            continue
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                    stored_prompt_version = payload.get("prompt_version")
                    if stored_prompt_version not in GEMINI_COMPATIBLE_PROMPT_VERSIONS:
                        continue
                    response = payload.get("response", {})
                    request_payload = payload.get("request")
                    legacy_candidate_fingerprint = ""
                    if isinstance(request_payload, dict):
                        legacy_candidate_fingerprint = _candidate_fingerprint_for_id(
                            request_payload,
                            response.get("candidate_id"),
                        )
                    decision = GeminiDecision(
                        resolved=bool(response.get("resolved")),
                        candidate_id=response.get("candidate_id"),
                        confidence=float(response.get("confidence") or 0),
                        reason=str(response.get("reason") or ""),
                        full_name=response.get("full_name"),
                        known_as=response.get("known_as"),
                        model=str(payload.get("model") or ""),
                        from_cache=True,
                        candidate_fingerprint=str(response.get("candidate_fingerprint") or legacy_candidate_fingerprint),
                    )
                    record_retry = bool(payload.get("retry"))
                    if isinstance(request_payload, dict):
                        cache[_gemini_payload_cache_key(request_payload, retry=record_retry)] = decision
                        request_secondary_cache_key = _gemini_secondary_payload_cache_key(
                            request_payload,
                            retry=record_retry,
                        )
                        if request_secondary_cache_key:
                            cache[request_secondary_cache_key] = decision
                    else:
                        cache[payload["cache_key"]] = decision
                    if not record_retry:
                        secondary_cache_key = payload.get("secondary_cache_key")
                        if isinstance(secondary_cache_key, str) and secondary_cache_key:
                            cache[secondary_cache_key] = decision
                except Exception:
                    continue
    return cache


def _write_gemini_cache_entry(
    cache_key: str,
    model: str,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    *,
    retry: bool = False,
) -> None:
    PLAYER_GEMINI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "cache_key": cache_key,
        "secondary_cache_key": _gemini_secondary_payload_cache_key(request_payload, retry=retry),
        "model": model,
        "prompt_version": GEMINI_PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "retry": retry,
        "request": request_payload,
        "response": response_payload,
    }
    with PLAYER_GEMINI_CACHE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _candidate_gemini_models(*, retry: bool = False) -> list[str]:
    primary_env = "GEMINI_RETRY_MODEL" if retry else "GEMINI_MODEL"
    fallback_env = "GEMINI_RETRY_FALLBACK_MODELS" if retry else "GEMINI_FALLBACK_MODELS"
    default_primary = GEMINI_DEFAULT_RETRY_MODEL if retry else GEMINI_DEFAULT_MODEL
    default_fallbacks = GEMINI_DEFAULT_RETRY_FALLBACK_MODELS if retry else GEMINI_DEFAULT_FALLBACK_MODELS
    primary = os.environ.get(primary_env, default_primary).strip() or default_primary
    raw_fallbacks = os.environ.get(fallback_env, "")
    fallbacks = [item.strip() for item in raw_fallbacks.split(",") if item.strip()] or default_fallbacks
    models: list[str] = []
    for model in [primary, *fallbacks]:
        if model and model not in models:
            models.append(model)
    return models


def _gemini_decision_to_response_payload(decision: GeminiDecision) -> dict[str, object]:
    return {
        "resolved": decision.resolved,
        "candidate_id": decision.candidate_id,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "full_name": decision.full_name,
        "known_as": decision.known_as,
        "candidate_fingerprint": decision.candidate_fingerprint,
    }


def _gemini_cached_response_matches(decision: GeminiDecision | None, response_payload: dict[str, object]) -> bool:
    if decision is None:
        return False
    cached_payload = _gemini_decision_to_response_payload(decision)
    return all(cached_payload.get(key) == response_payload.get(key) for key in cached_payload)


def _decision_for_current_payload(decision: GeminiDecision, payload: dict[str, object]) -> GeminiDecision:
    if not decision.resolved or not decision.candidate_fingerprint:
        return decision
    current_candidate_id = _candidate_id_for_fingerprint(payload, decision.candidate_fingerprint)
    if not current_candidate_id or current_candidate_id == decision.candidate_id:
        return decision
    return replace(decision, candidate_id=current_candidate_id)


def _compact_gemini_cache() -> int:
    if not PLAYER_GEMINI_CACHE_PATH.exists():
        return 0
    records_by_key: dict[str, dict[str, object]] = {}
    bad_lines = 0
    with PLAYER_GEMINI_CACHE_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except Exception:
                bad_lines += 1
                continue
            request_payload = record.get("request")
            if isinstance(request_payload, dict):
                record_retry = bool(record.get("retry"))
                cache_key = _gemini_payload_cache_key(request_payload, retry=record_retry)
                record["cache_key"] = cache_key
                secondary_cache_key = _gemini_secondary_payload_cache_key(request_payload, retry=record_retry)
                if secondary_cache_key:
                    record["secondary_cache_key"] = secondary_cache_key
                else:
                    record.pop("secondary_cache_key", None)
                response = record.get("response")
                if isinstance(response, dict) and not response.get("candidate_fingerprint"):
                    candidate_fingerprint = _candidate_fingerprint_for_id(request_payload, response.get("candidate_id"))
                    if candidate_fingerprint:
                        response["candidate_fingerprint"] = candidate_fingerprint
            else:
                cache_key = str(record.get("cache_key") or "").strip()
            if not cache_key:
                bad_lines += 1
                continue
            records_by_key[cache_key] = record

    with PLAYER_GEMINI_CACHE_PATH.open(encoding="utf-8") as handle:
        total_records = sum(1 for line in handle if line.strip())
    removed = total_records - len(records_by_key)
    if removed <= 0 and bad_lines == 0:
        return 0

    tmp_path = PLAYER_GEMINI_CACHE_PATH.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for record in records_by_key.values():
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(PLAYER_GEMINI_CACHE_PATH)
    return max(0, removed) + bad_lines


def _gemini_max_live_calls() -> int | None:
    value = os.environ.get("GEMINI_MAX_LIVE_CALLS")
    if value is None or not value.strip():
        return GEMINI_DEFAULT_MAX_LIVE_CALLS
    try:
        limit = int(value)
    except ValueError:
        return GEMINI_DEFAULT_MAX_LIVE_CALLS
    return limit if limit >= 0 else GEMINI_DEFAULT_MAX_LIVE_CALLS


def _gemini_live_call_budget_available() -> bool:
    max_calls = _gemini_max_live_calls()
    return max_calls is None or GEMINI_LIVE_CALL_ATTEMPTS < max_calls


def _warn_gemini_live_call_budget_exhausted() -> None:
    global GEMINI_CALL_BUDGET_WARNED
    if GEMINI_CALL_BUDGET_WARNED:
        return
    GEMINI_CALL_BUDGET_WARNED = True
    _gemini_log("[TECNICO] GEMINI_MAX_LIVE_CALLS alcanzado; no se haran mas llamadas en directo.")


def _gemini_model_rpm_limits() -> dict[str, float]:
    limits = dict(GEMINI_DEFAULT_MODEL_RPM_LIMITS)
    raw_limits = os.environ.get("GEMINI_MODEL_RPM_LIMITS", "")
    for item in raw_limits.split(","):
        text = item.strip()
        if not text:
            continue
        separator = ":" if ":" in text else "=" if "=" in text else ""
        if not separator:
            continue
        model, value = text.split(separator, 1)
        try:
            rpm = float(value.strip())
        except ValueError:
            continue
        if model.strip() and rpm > 0:
            limits[model.strip()] = rpm
    return limits


def _throttle_gemini_model(model: str) -> None:
    configured_min = _non_negative_env_float("GEMINI_MIN_SECONDS_BETWEEN_CALLS", 0.0)
    rpm = _gemini_model_rpm_limits().get(model)
    if rpm and rpm > 0:
        configured_min = max(configured_min, 60.0 / rpm)
    if configured_min <= 0:
        return
    now = time.monotonic()
    last_request_at = GEMINI_MODEL_LAST_REQUEST_AT.get(model)
    if last_request_at is not None:
        wait_seconds = configured_min - (now - last_request_at)
        if wait_seconds > 0:
            _gemini_log("[TECNICO] Limitador RPM")
            _gemini_log(f"  modelo: {model}")
            _gemini_log(f"  espera: {wait_seconds:.1f}s")
            time.sleep(wait_seconds)
    GEMINI_MODEL_LAST_REQUEST_AT[model] = time.monotonic()


def _gemini_prompt(payload: dict[str, object]) -> str:
    template = _load_gemini_prompt_template()
    return (
        template.replace("{{PROMPT_VERSION}}", GEMINI_PROMPT_VERSION)
        .replace("{{PAYLOAD_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    )


def ask_gemini(
    payload: dict[str, object],
    cache: dict[str, GeminiDecision],
    *,
    retry: bool = False,
    log_case: bool = True,
    exclude_models: set[str] | None = None,
) -> GeminiDecision | None:
    payload_cache_key = _gemini_payload_cache_key(payload, retry=retry)
    secondary_cache_key = _gemini_secondary_payload_cache_key(payload, retry=retry)
    task = str(payload.get("task") or "identity_resolution").strip()
    excluded_models = set(exclude_models or set())
    if log_case:
        _gemini_case_header(payload)
    if payload_cache_key in cache:
        cached_decision = cache[payload_cache_key]
        if not cached_decision.model or cached_decision.model not in excluded_models:
            decision = replace(_decision_for_current_payload(cached_decision, payload), from_cache=True)
            _gemini_log_response(task, "Respuesta recuperada de cache", decision)
            return decision
    if secondary_cache_key and secondary_cache_key in cache:
        cached_decision = cache[secondary_cache_key]
        if not cached_decision.model or cached_decision.model not in excluded_models:
            decision = replace(_decision_for_current_payload(cached_decision, payload), from_cache=True)
            _gemini_log_response(task, "Respuesta recuperada de cache estable por IDs", decision)
            cache[payload_cache_key] = decision
            return decision

    api_key = os.environ.get("GEMINI_API_KEY")
    global GEMINI_MISSING_API_KEY_WARNED
    if not api_key:
        if not GEMINI_MISSING_API_KEY_WARNED:
            _gemini_log("[TECNICO] GEMINI_API_KEY no esta configurada; no se consultara Gemini en directo.")
            GEMINI_MISSING_API_KEY_WARNED = True
        return None
    if not _gemini_live_call_budget_available():
        _warn_gemini_live_call_budget_exhausted()
        return None

    retry_attempts = _positive_env_int("GEMINI_RETRY_ATTEMPTS", GEMINI_DEFAULT_RETRY_ATTEMPTS)
    retry_base_seconds = _positive_env_int("GEMINI_RETRY_BASE_SECONDS", GEMINI_DEFAULT_RETRY_BASE_SECONDS)
    timeout = _positive_env_int("GEMINI_TIMEOUT_SECONDS", GEMINI_DEFAULT_TIMEOUT_SECONDS)

    for model in _candidate_gemini_models(retry=retry):
        if model in excluded_models:
            continue
        if model in UNAVAILABLE_GEMINI_MODELS:
            continue
        request_body = {
            "contents": [{"parts": [{"text": _gemini_prompt(payload)}]}],
            "generationConfig": {"temperature": 0, "response_mime_type": "application/json"},
        }
        data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        raw = None
        for attempt in range(retry_attempts):
            global GEMINI_LIVE_CALL_ATTEMPTS
            if not _gemini_live_call_budget_available():
                _warn_gemini_live_call_budget_exhausted()
                return None
            _throttle_gemini_model(model)
            GEMINI_LIVE_CALL_ATTEMPTS += 1
            _gemini_log(f"{_gemini_task_label(task)} Llamando a Gemini")
            _gemini_log(f"  modelo: {model}")
            _gemini_log(f"  intento: {attempt + 1}/{retry_attempts}")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {400, 401, 403, 404}:
                    UNAVAILABLE_GEMINI_MODELS.add(model)
                    _gemini_log("[TECNICO] Modelo no disponible o no autorizado; probando fallback")
                    _gemini_log(f"  modelo: {model}")
                    _gemini_log(f"  HTTP: {exc.code}")
                    break
                if exc.code in {429, 500, 502, 503, 504} and attempt < retry_attempts - 1:
                    wait_seconds = retry_base_seconds * (attempt + 1)
                    _gemini_log("[TECNICO] Error temporal de Gemini; reintentando")
                    _gemini_log(f"  modelo: {model}")
                    _gemini_log(f"  HTTP: {exc.code}")
                    _gemini_log(f"  espera: {wait_seconds}s")
                    time.sleep(wait_seconds)
                    continue
                _gemini_log("[TECNICO] Modelo sin respuesta valida")
                _gemini_log(f"  modelo: {model}")
                _gemini_log(f"  HTTP: {exc.code}")
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < retry_attempts - 1:
                    wait_seconds = retry_base_seconds * (attempt + 1)
                    _gemini_log("[TECNICO] Error temporal de Gemini; reintentando")
                    _gemini_log(f"  modelo: {model}")
                    _gemini_log(f"  error: {type(exc).__name__}")
                    _gemini_log(f"  espera: {wait_seconds}s")
                    time.sleep(wait_seconds)
                    continue
                _gemini_log("[TECNICO] Modelo sin respuesta valida")
                _gemini_log(f"  modelo: {model}")
                _gemini_log(f"  error: {type(exc).__name__}")
                break
        if raw is None:
            continue
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            decision = GeminiDecision(
                resolved=bool(parsed.get("resolved")),
                candidate_id=parsed.get("candidate_id"),
                confidence=float(parsed.get("confidence") or 0),
                reason=str(parsed.get("reason") or ""),
                full_name=parsed.get("full_name"),
                known_as=parsed.get("known_as"),
                model=model,
                candidate_fingerprint=_candidate_fingerprint_for_id(payload, parsed.get("candidate_id")),
            )
        except Exception:
            _gemini_log("[TECNICO] Respuesta no parseable; probando fallback")
            _gemini_log(f"  modelo: {model}")
            continue
        response_payload = _gemini_decision_to_response_payload(decision)
        if not _gemini_cached_response_matches(cache.get(payload_cache_key), response_payload):
            _write_gemini_cache_entry(
                payload_cache_key,
                model,
                payload,
                response_payload,
                retry=retry,
            )
        cache[payload_cache_key] = replace(decision, from_cache=True)
        if secondary_cache_key:
            cache[secondary_cache_key] = replace(decision, from_cache=True)
        _gemini_log_response(task, "Respuesta Gemini guardada en cache", decision, model=model)
        return decision
    _gemini_log("[TECNICO] Sin respuesta valida de Gemini tras probar los modelos disponibles.")
    return None


def _source_key_sort_key(key: str) -> tuple[int, str]:
    source = str(key).split(":", 1)[0]
    priority = {"understat": 0, "whoscored": 1, "espn": 2}
    return priority.get(source, 99), str(key)


def _current_root_groups(source_keys: list[str], dsu: DisjointSet) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for key in source_keys:
        groups[dsu.find(key)].append(key)
    return {root: sorted(keys, key=_source_key_sort_key) for root, keys in groups.items()}


def _rows_for_keys(keys: list[str], rows_by_key: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat([rows_by_key[key] for key in keys if key in rows_by_key], ignore_index=True)


def _contexts_for_keys(keys: list[str], contexts_by_key: dict[str, set[tuple[str, str, str]]]) -> set[tuple[str, str, str]]:
    contexts: set[tuple[str, str, str]] = set()
    for key in keys:
        contexts |= contexts_by_key.get(key, set())
    return contexts


def _names_for_keys(keys: list[str], rows_by_key: dict[str, pd.DataFrame]) -> list[str]:
    names: list[str] = []
    for key in keys:
        rows = rows_by_key.get(key)
        if rows is None:
            continue
        for name in rows["observed_name"].dropna().astype(str).tolist():
            text = name.strip()
            if text and text not in names:
                names.append(text)
    return names


def _source_ids_from_rows(rows: pd.DataFrame, source: str) -> str:
    if rows.empty:
        return ""
    values = rows.loc[rows["source"].astype(str).eq(source), "source_player_id"]
    return _join_values(values)


def _source_id_list_from_rows(rows: pd.DataFrame) -> list[str]:
    values: list[str] = []
    for row in rows.itertuples(index=False):
        source = str(row.source)
        source_id = _log_value(row.source_player_id)
        if source_id:
            values.append(f"{source}:{source_id}")
    return sorted(set(values))


def _candidate_payload(
    root_keys: list[str],
    candidate_root_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    source_keys: list[str] | None = None,
    dsu: DisjointSet | None = None,
) -> dict[str, object]:
    rows = _rows_for_keys(root_keys, rows_by_key)
    names = _names_for_keys(root_keys, rows_by_key)
    candidates: list[dict[str, object]] = []
    for candidate_key in candidate_root_keys:
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu) if source_keys is not None and dsu is not None else [candidate_key]
        candidate_rows = _rows_for_keys(candidate_keys, rows_by_key)
        candidates.append(
            {
                "candidate_id": candidate_key,
                "names": _names_for_keys(candidate_keys, rows_by_key),
                "source_player_keys": candidate_keys,
                "source_player_ids": _source_id_list_from_rows(candidate_rows),
                "sources": sorted(candidate_rows["source"].dropna().astype(str).unique().tolist()),
                "teams": sorted(candidate_rows["id_team"].dropna().astype(str).unique().tolist()),
                "seasons": sorted(candidate_rows["id_season"].dropna().astype(str).unique().tolist()),
                "positions": _split_joined_values(_join_position_evidence(candidate_rows["positions"])),
                "position_roles": _split_joined_values(_join_position_evidence(candidate_rows["position_roles"])),
            }
        )
    return {
        "task": "identity_resolution",
        "observed_name": _best_name_for_key(rows),
        "aliases": names,
        "source_player_keys": root_keys,
        "source_player_ids": _source_id_list_from_rows(rows),
        "sources": sorted(rows["source"].dropna().astype(str).unique().tolist()),
        "competitions": sorted(rows["id_competition"].dropna().astype(str).unique().tolist()),
        "seasons": sorted(rows["id_season"].dropna().astype(str).unique().tolist()),
        "teams": sorted(rows["id_team"].dropna().astype(str).unique().tolist()),
        "positions": _split_joined_values(_join_position_evidence(rows["positions"])),
        "position_roles": _split_joined_values(_join_position_evidence(rows["position_roles"])),
        "candidates": candidates,
        "instruction": "Decide si el jugador observado es exactamente la misma persona que uno de los candidatos.",
    }


def _preview_identity_names(keys: list[str], rows_by_key: dict[str, pd.DataFrame]) -> tuple[str, str]:
    aliases = _names_for_keys(keys, rows_by_key)
    known_as = _choose_known_as(aliases)
    full_name = _choose_full_name(aliases)
    if not full_name and has_expanded_full_name(known_as):
        full_name = known_as
    return known_as, full_name or "pendiente_de_enriquecimiento"


def _record_identity_name_hint(
    key_name_hints: dict[str, IdentityNameHint],
    keys: list[str],
    decision: GeminiDecision,
) -> None:
    full_name = str(decision.full_name or "").strip()
    if not has_expanded_full_name(full_name):
        return
    known_as = str(decision.known_as or "").strip()
    hint = IdentityNameHint(
        known_as=known_as,
        full_name=full_name,
        confidence=decision.confidence,
    )
    for key in keys:
        current = key_name_hints.get(key)
        if current is None or hint.confidence >= current.confidence:
            key_name_hints[key] = hint


def _identity_reject_reason(
    decision: GeminiDecision,
    candidate_key: str,
    candidate_roots: list[str],
    active_keys: list[str],
    candidate_keys: list[str],
    observed_name: str,
    candidate_name: str,
    observed_aliases: list[str] | None = None,
    candidate_aliases: list[str] | None = None,
) -> str:
    if decision.confidence < GEMINI_ACCEPT_CONFIDENCE:
        return "low_confidence"
    if candidate_key not in candidate_roots:
        return "candidate_not_in_allowed_set"
    if _would_create_source_id_conflict(active_keys, candidate_keys):
        return "source_id_conflict"
    if not _is_safe_gemini_identity_merge(
        observed_name,
        candidate_name,
        decision.confidence,
        observed_aliases,
        candidate_aliases,
        decision.full_name,
        decision.known_as,
    ):
        return "local_safety_reject"
    return "accepted"


def _identity_reject_explanation(reason: str) -> str:
    explanations = {
        "low_confidence": "Gemini propone fusionar, pero la confianza no alcanza el minimo local.",
        "candidate_not_in_allowed_set": "Gemini eligio un candidate_id que no estaba en la lista valida de candidatos.",
        "source_id_conflict": "La fusion mezclaria IDs distintos de una misma fuente.",
        "local_safety_reject": "Gemini propone fusionar, pero la validacion local de nombres/contexto no lo considera suficientemente seguro.",
        "accepted": "La fusion pasa las validaciones locales.",
    }
    return explanations.get(reason, "La fusion propuesta no paso las validaciones locales.")


def _gemini_log_identity_merge(
    decision: GeminiDecision,
    observed_name: str,
    candidate_name: str,
    final_known_as: str,
    final_full_name: str,
) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: APLICADA - fusion")
    _gemini_log("  Gemini:")
    _gemini_log(f"    decision_gemini: {_gemini_decision_action('identity_resolution', decision)} (resolved={decision.resolved})")
    _gemini_log(f"    confidence: {decision.confidence:.2f}")
    _gemini_log(f"    candidate_id: {decision.candidate_id or 'null'}")
    _gemini_log("  Fusion:")
    _gemini_log(f"    observado: {observed_name}")
    _gemini_log(f"    candidato: {candidate_name}")
    _gemini_log("  Identidad resultante prevista:")
    _gemini_log(f"    known_as: {final_known_as}")
    _gemini_log(f"    full_name: {final_full_name}")
    _gemini_log("  motivo_local: Gemini resolvio mismo jugador y la fusion paso las validaciones locales.")


def _gemini_log_identity_rejected(
    decision: GeminiDecision,
    reason: str,
    observed_name: str,
    candidate_name: str,
) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: NO APLICADA - fusion rechazada")
    _gemini_log("  Gemini propuso:")
    _gemini_log(f"    decision_gemini: {_gemini_decision_action('identity_resolution', decision)} (resolved={decision.resolved})")
    _gemini_log(f"    confidence: {decision.confidence:.2f}")
    _gemini_log(f"    candidate_id: {decision.candidate_id or 'null'}")
    _gemini_log(f"    observado: {observed_name}")
    _gemini_log(f"    candidato: {candidate_name or 'no encontrado'}")
    _gemini_log(f"  motivo_local: {reason}")
    _gemini_log(f"  explicacion: {_identity_reject_explanation(reason)}")


def _gemini_log_identity_no_match(decision: GeminiDecision, observed_name: str) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: APLICADA - no fusionar")
    _gemini_log("  Gemini:")
    _gemini_log(f"    decision_gemini: {_gemini_decision_action('identity_resolution', decision)} (resolved={decision.resolved})")
    _gemini_log(f"    confidence: {decision.confidence:.2f}")
    _gemini_log("  Decision local:")
    _gemini_log(f"    observado: {observed_name}")
    _gemini_log("    accion: se mantiene como identidad independiente")


def _gemini_log_identity_low_confidence(decision: GeminiDecision | None, observed_name: str) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: NO APLICADA - baja confianza")
    if decision is None:
        _gemini_log("  Gemini: sin respuesta")
    else:
        _gemini_log("  Gemini:")
        _gemini_log(f"    decision_gemini: {_gemini_decision_action('identity_resolution', decision)} (resolved={decision.resolved})")
        _gemini_log(f"    confidence: {decision.confidence:.2f}")
        _gemini_log(f"    candidate_id: {decision.candidate_id or 'null'}")
    _gemini_log(f"  observado: {observed_name}")
    _gemini_log("  accion: se envia a cola de revision")


def _gemini_log_identity_skipped_weak_evidence(observed_name: str) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: NO APLICADA - Gemini omitido por evidencia debil")
    _gemini_log(f"  observado: {observed_name}")
    _gemini_log("  accion: se conserva como identidad independiente tras las reglas deterministas")


def _gemini_log_identity_unavailable(observed_name: str) -> None:
    _gemini_log("[IDENTIDAD] RESULTADO: NO APLICADA - sin respuesta util")
    _gemini_log(f"  observado: {observed_name}")
    _gemini_log("  accion: se envia a cola de revision")


def _append_review(
    review_rows: list[dict[str, object]],
    *,
    reason: str,
    row: pd.Series,
    candidates: list[dict[str, object]] | None = None,
    suggested_action: str = "",
) -> None:
    review_id = hashlib.sha1(
        "|".join(
            [
                reason,
                _log_value(row.get("source_player_key", "")),
                _log_value(row.get("observed_name", "")),
                _log_value(row.get("id_season", "")),
                _log_value(row.get("id_team", "")),
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]
    review_rows.append(
        {
            "review_id": review_id,
            "reason": reason,
            "source": _log_value(row.get("source", "")),
            "source_player_key": _log_value(row.get("source_player_key", "")),
            "observed_name": _log_value(row.get("observed_name", "")),
            "normalized_alias": _log_value(row.get("normalized_alias", "")),
            "id_competition": _log_value(row.get("id_competition", "")),
            "id_season": _log_value(row.get("id_season", "")),
            "id_team": _log_value(row.get("id_team", "")),
            "sample_game": _log_value(row.get("sample_game", "")),
            "candidates": json.dumps(candidates or [], ensure_ascii=False, sort_keys=True),
            "suggested_action": suggested_action,
        }
    )


TRANSIENT_REVIEW_REASONS = {
    "gemini_identity_low_confidence",
    "gemini_identity_unavailable",
}


def _prune_resolved_review_queue(review_queue: pd.DataFrame, alias_map: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if review_queue.empty or alias_map.empty:
        return review_queue, 0
    resolved_aliases = alias_map[
        alias_map["id_player"].astype(str).str.strip().ne("")
        & alias_map["needs_review"].astype(str).str.lower().eq("false")
    ]
    resolved_source_keys = {
        source_key
        for source_key in resolved_aliases["source_player_key"].dropna().astype(str).str.strip()
        if source_key
    }

    def row_is_resolved(row: pd.Series) -> bool:
        if str(row.get("reason", "")).strip() not in TRANSIENT_REVIEW_REASONS:
            return False
        source_keys = _split_joined_values(row.get("source_player_key", ""))
        return bool(source_keys) and all(source_key in resolved_source_keys for source_key in source_keys)

    resolved_mask = review_queue.apply(row_is_resolved, axis=1)
    pruned = review_queue.loc[~resolved_mask].reset_index(drop=True)
    return pruned, int(resolved_mask.sum())


def _apply_deterministic_merges(
    observations: pd.DataFrame,
    source_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
    dsu: DisjointSet,
    key_methods: dict[str, set[str]],
) -> dict[str, int]:
    counters = Counter()
    alias_groups = observations[observations["normalized_alias"].astype(str).str.strip().ne("")].groupby("normalized_alias")
    for alias, group in alias_groups:
        alias_tokens = _tokens_from_normalized_alias(alias)
        keys = sorted(group["source_player_key"].astype(str).unique().tolist(), key=_source_key_sort_key)
        if len(keys) < 2:
            continue

        id_backed_keys = [
            key
            for key in keys
            if rows_by_key[key]["source"].astype(str).isin(ID_BACKED_SOURCES).any()
        ]
        espn_keys = [key for key in keys if rows_by_key[key]["source"].astype(str).eq("espn").any()]

        if len(alias_tokens) == 1:
            keys_by_context: dict[tuple[str, str, str], list[str]] = defaultdict(list)
            for key in keys:
                contexts = contexts_by_key.get(key, set())
                if len(contexts) == 1:
                    keys_by_context[next(iter(contexts))].append(key)
            for context_keys in keys_by_context.values():
                context_keys = sorted(context_keys, key=_source_key_sort_key)
                if len(context_keys) < 2:
                    continue
                if not any(key in id_backed_keys for key in context_keys):
                    continue
                if _would_create_source_id_conflict([], context_keys):
                    continue
                roots = {dsu.find(key) for key in context_keys}
                if len(roots) < 2:
                    continue
                position_compatible = True
                for left_index, left_key in enumerate(context_keys):
                    for right_key in context_keys[left_index + 1:]:
                        if not _position_profiles_overlap(rows_by_key[left_key], rows_by_key[right_key]):
                            position_compatible = False
                            break
                    if not position_compatible:
                        break
                if not position_compatible:
                    continue
                base_key = context_keys[0]
                for key in context_keys[1:]:
                    dsu.union(base_key, key)
                for key in context_keys:
                    key_methods[key].add("deterministic_single_alias_exact_context")
                counters["single_alias_exact_context"] += len(context_keys) - 1
            continue

        if len(alias_tokens) >= 2:
            roots = sorted({dsu.find(key) for key in id_backed_keys})
            if len(roots) == 1 and id_backed_keys:
                for espn_key in espn_keys:
                    if any(_has_context_overlap(rows_by_key[espn_key], rows_by_key[id_key]) for id_key in id_backed_keys):
                        dsu.union(espn_key, id_backed_keys[0])
                        key_methods[espn_key].add("deterministic_espn_exact_alias_context")
                        counters["espn_exact_alias_context"] += 1
                continue

            if len(id_backed_keys) == 2 and not _would_create_source_id_conflict(id_backed_keys[:1], id_backed_keys[1:]):
                left, right = id_backed_keys
                if _has_context_overlap(rows_by_key[left], rows_by_key[right]):
                    dsu.union(left, right)
                    key_methods[left].add("deterministic_exact_alias_context")
                    key_methods[right].add("deterministic_exact_alias_context")
                    counters["id_backed_exact_alias_context"] += 1

        espn_by_team = defaultdict(list)
        for key in espn_keys:
            teams = sorted(rows_by_key[key]["id_team"].dropna().astype(str).unique().tolist())
            if len(teams) == 1:
                espn_by_team[teams[0]].append(key)
        for team_keys in espn_by_team.values():
            for key in team_keys[1:]:
                dsu.union(team_keys[0], key)
                key_methods[key].add("deterministic_espn_same_alias_team")
                key_methods[team_keys[0]].add("deterministic_espn_same_alias_team")
                counters["espn_same_alias_team"] += 1
    return dict(counters)


def _is_strong_transfer_alias(alias: object) -> bool:
    tokens = _tokens_from_normalized_alias(alias)
    if len(tokens) < 2:
        return False
    return not any(is_ambiguous_single_name(token) for token in tokens)


def _keys_have_id_backed_source(keys: list[str]) -> bool:
    return bool(_source_ids_for_keys(keys))


def _keys_are_espn_only(keys: list[str]) -> bool:
    return bool(keys) and all(str(key).startswith("espn:") for key in keys)


def _sources_for_keys(keys: list[str], rows_by_key: dict[str, pd.DataFrame]) -> set[str]:
    sources: set[str] = set()
    for key in keys:
        rows = rows_by_key.get(key, pd.DataFrame())
        if rows.empty or "source" not in rows.columns:
            continue
        sources.update(rows["source"].dropna().astype(str).str.strip())
    return {source for source in sources if source}


def _match_bridge_game_key(value: object) -> str:
    return normalize_alias(value)


def _match_bridge_position_rows(position: object) -> pd.DataFrame:
    text = _clean_position_value(position)
    return pd.DataFrame(
        {
            "positions": [text],
            "position_roles": [_position_roles_value(text)],
        }
    )


def _match_bridge_single_token(value: object) -> str:
    tokens = _content_name_tokens(str(value or ""))
    if len(tokens) != 1:
        return ""
    token = tokens[0]
    return token if len(token) >= 4 else ""


def _prepare_understat_match_bridge_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    frame = df.copy()
    frame["source_player_key"] = build_source_player_keys_for_frame(
        frame,
        source="understat",
        player_col="player",
        source_player_id_col="player_id",
        team_col="team",
        competition_col="league",
        season_col="season",
    )
    frame["id_competition"] = frame["league"].apply(normalize_context_competition)
    frame["id_season"] = frame["season"].apply(normalize_context_season)
    frame["id_team"] = frame["team"].apply(normalize_context_team)
    frame["game_key"] = frame["game"].apply(_match_bridge_game_key)
    frame["token"] = frame["player"].apply(_match_bridge_single_token)
    frame["minutes_value"] = pd.to_numeric(frame.get("minutes", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    return frame[
        frame["source_player_key"].notna()
        & frame["token"].astype(str).str.strip().ne("")
        & frame["minutes_value"].gt(0)
    ].copy()


def _prepare_espn_match_bridge_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    frame = df.copy()
    frame["source_player_key"] = build_source_player_keys_for_frame(
        frame,
        source="espn",
        player_col="player",
        source_player_id_col=None,
        team_col="team",
        competition_col="league",
        season_col="season",
    )
    frame["id_competition"] = frame["league"].apply(normalize_context_competition)
    frame["id_season"] = frame["season"].apply(normalize_context_season)
    frame["id_team"] = frame["team"].apply(normalize_context_team)
    frame["game_key"] = frame["game"].apply(_match_bridge_game_key)
    sub_in = frame.get("sub_in", pd.Series("", index=frame.index)).astype("string").fillna("").str.strip()
    appearances = pd.to_numeric(frame.get("appearances", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    frame["participated"] = appearances.gt(0) | sub_in.ne("")
    frame["has_expanded_name"] = frame["player"].apply(has_expanded_full_name)
    return frame[
        frame["source_player_key"].notna()
        & frame["participated"]
        & frame["has_expanded_name"]
    ].copy()


def _single_token_match_context_bridge_pairs_from_frames(
    understat_rows: pd.DataFrame,
    espn_rows: pd.DataFrame,
) -> dict[str, str]:
    understat = _prepare_understat_match_bridge_frame(understat_rows)
    espn = _prepare_espn_match_bridge_frame(espn_rows)
    if understat.empty or espn.empty:
        return {}

    context_cols = ["id_competition", "id_season", "id_team", "game_key"]
    candidates_by_context = {
        context: group.copy()
        for context, group in espn.groupby(context_cols, dropna=False)
    }
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    candidate_aliases_by_key: dict[str, set[str]] = defaultdict(set)
    ambiguous: Counter[str] = Counter()

    for row in understat.itertuples(index=False):
        weak_key = str(row.source_player_key)
        weak_position = _match_bridge_position_rows(getattr(row, "position", ""))
        if not _position_profile_tokens(weak_position):
            continue
        context = tuple(str(getattr(row, col)) for col in context_cols)
        candidates = candidates_by_context.get(context)
        if candidates is None or candidates.empty:
            continue
        token = str(row.token)
        matching_keys: list[str] = []
        for candidate in candidates.itertuples(index=False):
            candidate_name = str(getattr(candidate, "player", ""))
            if token not in _content_name_tokens(candidate_name):
                continue
            if not _position_profiles_overlap(
                weak_position,
                _match_bridge_position_rows(getattr(candidate, "position", "")),
                allow_unknown=False,
            ):
                continue
            candidate_key = str(getattr(candidate, "source_player_key", ""))
            if candidate_key and candidate_key not in matching_keys:
                matching_keys.append(candidate_key)
                candidate_aliases_by_key[candidate_key].add(normalize_alias(candidate_name))
        if len(matching_keys) == 1:
            votes[weak_key][matching_keys[0]] += 1
        elif len(matching_keys) > 1:
            ambiguous[weak_key] += 1

    pairs: dict[str, str] = {}
    for weak_key, candidate_counts in votes.items():
        candidate_aliases = {
            alias
            for candidate_key in candidate_counts
            for alias in candidate_aliases_by_key.get(candidate_key, set())
            if alias
        }
        if ambiguous[weak_key] or len(candidate_aliases) != 1:
            continue
        pairs[weak_key] = sorted(candidate_counts, key=lambda key: (-candidate_counts[key], key))[0]
    return pairs


def _collect_single_token_match_context_bridge_pairs() -> dict[str, str]:
    understat_frames = [
        _read_csv_selected(path, ["league", "season", "game", "team", "player", "player_id", "position", "minutes"])
        for path in _target_raw_paths("understat", "player_match_stats", pattern="read_player_match_stats_*.csv")
    ]
    espn_frames = [
        _read_csv_selected(path, ["league", "season", "game", "team", "player", "position", "appearances", "sub_in"])
        for path in _target_raw_paths("espn", "lineup", pattern="read_lineup_*.csv")
    ]
    if not understat_frames or not espn_frames:
        return {}
    understat_rows = pd.concat(understat_frames, ignore_index=True)
    espn_rows = pd.concat(espn_frames, ignore_index=True)
    return _single_token_match_context_bridge_pairs_from_frames(understat_rows, espn_rows)


def _apply_single_token_match_context_bridges(
    source_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    dsu: DisjointSet,
    key_methods: dict[str, set[str]],
    bridge_pairs: dict[str, str] | None = None,
) -> int:
    bridges = 0
    pairs = bridge_pairs if bridge_pairs is not None else _collect_single_token_match_context_bridge_pairs()
    for weak_key, candidate_key in sorted(pairs.items()):
        if weak_key not in dsu.parent or candidate_key not in dsu.parent:
            continue
        weak_root = dsu.find(weak_key)
        candidate_root = dsu.find(candidate_key)
        if weak_root == candidate_root:
            continue
        root_groups = _current_root_groups(source_keys, dsu)
        weak_keys = root_groups.get(weak_root, [])
        candidate_keys = root_groups.get(candidate_root, [])
        if _sources_for_keys(weak_keys, rows_by_key) != {"understat"}:
            continue
        candidate_sources = _sources_for_keys(candidate_keys, rows_by_key)
        if "understat" in candidate_sources or not (candidate_sources & {"espn", "whoscored"}):
            continue
        if _would_create_source_id_conflict(weak_keys, candidate_keys):
            continue
        dsu.union(weak_keys[0], candidate_keys[0])
        for merged_key in weak_keys + candidate_keys:
            key_methods[merged_key].add("deterministic_single_token_match_context_bridge")
        bridges += 1
    return bridges


def _contexts_share_competition(left_contexts: set[tuple[str, str, str]], right_contexts: set[tuple[str, str, str]]) -> bool:
    left_competitions, _, _ = _context_parts(left_contexts)
    right_competitions, _, _ = _context_parts(right_contexts)
    return bool(left_competitions & right_competitions)


def _apply_transfer_exact_alias_merges(
    observations: pd.DataFrame,
    source_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
    dsu: DisjointSet,
    key_methods: dict[str, set[str]],
) -> int:
    merges = 0
    alias_groups = observations[observations["normalized_alias"].astype(str).str.strip().ne("")].groupby("normalized_alias")
    for alias, group in alias_groups:
        if not _is_strong_transfer_alias(alias):
            continue
        roots = sorted({dsu.find(key) for key in group["source_player_key"].astype(str).unique().tolist()})
        if len(roots) < 2:
            continue
        root_groups = _current_root_groups(source_keys, dsu)
        id_backed_roots = [
            root
            for root in roots
            if _keys_have_id_backed_source(root_groups.get(root, []))
        ]
        if len(id_backed_roots) != 1:
            continue
        stable_root = id_backed_roots[0]
        stable_keys = root_groups.get(stable_root, [])
        stable_contexts = _contexts_for_keys(stable_keys, contexts_by_key)
        combined_keys = list(stable_keys)
        for root in roots:
            if root == stable_root:
                continue
            weak_keys = root_groups.get(root, [])
            if not _keys_are_espn_only(weak_keys):
                continue
            weak_contexts = _contexts_for_keys(weak_keys, contexts_by_key)
            if not _contexts_share_competition(stable_contexts, weak_contexts):
                continue
            if _would_create_source_id_conflict(combined_keys, weak_keys):
                continue
            if not _position_profiles_overlap(
                _rows_for_keys(stable_keys, rows_by_key),
                _rows_for_keys(weak_keys, rows_by_key),
            ):
                continue
            dsu.union(stable_keys[0], weak_keys[0])
            combined_keys.extend(weak_keys)
            for merged_key in stable_keys + weak_keys:
                key_methods[merged_key].add("deterministic_transfer_exact_alias")
            merges += 1
    return merges


def _candidate_roots_for_key(
    key: str,
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
) -> list[str]:
    active_root = dsu.find(key)
    active_keys = [candidate for candidate in source_keys if dsu.find(candidate) == active_root]
    active_rows = _rows_for_keys(active_keys, rows_by_key)
    active_names = _names_for_keys(active_keys, rows_by_key) or [_best_name_for_key(active_rows)]
    active_contexts = _contexts_for_keys(active_keys, contexts_by_key)
    candidates: list[str] = []
    for other_root, other_keys in _current_root_groups(source_keys, dsu).items():
        if other_root == active_root:
            continue
        if _would_create_source_id_conflict(active_keys, other_keys):
            continue
        other_rows = _rows_for_keys(other_keys, rows_by_key)
        other_names = _names_for_keys(other_keys, rows_by_key) or [_best_name_for_key(other_rows)]
        if not active_contexts & _contexts_for_keys(other_keys, contexts_by_key):
            continue
        if not any(
            _names_need_gemini(active_name, other_name)
            for active_name in active_names
            for other_name in other_names
        ):
            continue
        representative = sorted(other_keys, key=_source_key_sort_key)[0]
        candidates.append(representative)
    return sorted(candidates, key=_source_key_sort_key)[:5]


def _candidate_group_for_key(candidate_key: str, source_keys: list[str], dsu: DisjointSet) -> list[str]:
    candidate_root = dsu.find(candidate_key)
    return [
        key
        for key in source_keys
        if dsu.find(key) == candidate_root
    ]


def _has_high_value_single_token_bridge(left: str, right: str) -> bool:
    left_tokens = _content_name_tokens(left)
    right_tokens = _content_name_tokens(right)
    if len(left_tokens) == 1 and len(right_tokens) >= 2:
        token = left_tokens[0]
        return len(token) >= 4 and not is_ambiguous_single_name(token) and token in right_tokens
    if len(right_tokens) == 1 and len(left_tokens) >= 2:
        token = right_tokens[0]
        return len(token) >= 4 and not is_ambiguous_single_name(token) and token in left_tokens
    return False


def _has_high_value_gemini_name_evidence(active_names: list[str], candidate_names: list[str]) -> bool:
    for active_name in active_names:
        for candidate_name in candidate_names:
            if not _names_need_gemini(active_name, candidate_name):
                continue
            if _has_safe_identity_name_match(active_name, candidate_name, allow_single_token=False):
                return True
            if _has_high_value_single_token_bridge(active_name, candidate_name):
                return True
            if has_expanded_full_name(active_name) and has_expanded_full_name(candidate_name):
                if _has_safe_long_orthographic_name_match(active_name, candidate_name):
                    return True
                if _has_cross_position_token_bridge_candidate(active_name, candidate_name):
                    return True
    return False


def _has_high_value_id_backed_candidate(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
) -> bool:
    active_rows = _rows_for_keys(active_keys, rows_by_key)
    active_names = _names_for_keys(active_keys, rows_by_key) or [_best_name_for_key(active_rows)]
    for candidate_key in candidate_roots:
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu)
        if not _keys_have_id_backed_source(candidate_keys):
            continue
        candidate_rows = _rows_for_keys(candidate_keys, rows_by_key)
        candidate_names = _names_for_keys(candidate_keys, rows_by_key) or [_best_name_for_key(candidate_rows)]
        if _has_high_value_gemini_name_evidence(active_names, candidate_names):
            return True
    return False


def _should_skip_gemini_for_weak_evidence(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
) -> bool:
    if not active_keys or not candidate_roots:
        return False
    if not _keys_are_espn_only(active_keys):
        return False
    if _keys_have_id_backed_source(active_keys):
        return False
    return not _has_high_value_id_backed_candidate(active_keys, candidate_roots, source_keys, dsu, rows_by_key)



def _exact_name_context_fragment_candidates(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
) -> list[str]:
    if not candidate_roots:
        return []
    active_rows = _rows_for_keys(active_keys, rows_by_key)
    active_name = normalize_alias(_best_name_for_key(active_rows))
    active_contexts = _contexts_for_keys(active_keys, contexts_by_key)
    if not active_name or not active_contexts:
        return []
    allow_overlapping_candidate_contexts = _is_safe_exact_identity_name(active_name)

    selected: list[str] = []
    combined_keys = list(active_keys)
    used_candidate_contexts: set[tuple[str, str, str]] = set()
    for candidate_key in candidate_roots:
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu)
        candidate_rows = _rows_for_keys(candidate_keys, rows_by_key)
        candidate_name = normalize_alias(_best_name_for_key(candidate_rows))
        candidate_contexts = _contexts_for_keys(candidate_keys, contexts_by_key)
        if candidate_name != active_name:
            return []
        if not candidate_contexts or not candidate_contexts.issubset(active_contexts):
            return []
        if (
            len(candidate_roots) > 1
            and used_candidate_contexts & candidate_contexts
            and not allow_overlapping_candidate_contexts
        ):
            return []
        if _would_create_source_id_conflict(combined_keys, candidate_keys):
            return []
        used_candidate_contexts.update(candidate_contexts)
        combined_keys.extend(candidate_keys)
        selected.append(candidate_key)
    return selected


def _context_parts(contexts: set[tuple[str, str, str]]) -> tuple[set[str], set[str], set[str]]:
    competitions = {competition for competition, _, _ in contexts if competition}
    seasons = {season for _, season, _ in contexts if season}
    teams = {team for _, _, team in contexts if team}
    return competitions, seasons, teams


def _same_team_competition_continuity(
    left_contexts: set[tuple[str, str, str]],
    right_contexts: set[tuple[str, str, str]],
) -> bool:
    left_competitions, _, left_teams = _context_parts(left_contexts)
    right_competitions, _, right_teams = _context_parts(right_contexts)
    return bool(left_competitions & right_competitions) and bool(left_teams & right_teams)


def _exact_name_deterministic_candidates(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
) -> tuple[list[str], str]:
    if not candidate_roots:
        return [], ""
    active_rows = _rows_for_keys(active_keys, rows_by_key)
    active_name = normalize_alias(_best_name_for_key(active_rows))
    active_tokens = _name_tokens(_best_name_for_key(active_rows))
    active_contexts = _contexts_for_keys(active_keys, contexts_by_key)
    if not active_name or len(active_tokens) < 2 or not active_contexts:
        return [], ""

    selected: list[str] = []
    combined_keys = list(active_keys)
    reasons: set[str] = set()
    for candidate_key in candidate_roots:
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu)
        candidate_rows = _rows_for_keys(candidate_keys, rows_by_key)
        candidate_name = normalize_alias(_best_name_for_key(candidate_rows))
        candidate_contexts = _contexts_for_keys(candidate_keys, contexts_by_key)
        if candidate_name != active_name:
            return [], ""
        if _would_create_source_id_conflict(combined_keys, candidate_keys):
            return [], ""
        if active_contexts & candidate_contexts:
            reasons.add("mismo nombre y mismo contexto")
        elif candidate_contexts.issubset(active_contexts) or active_contexts.issubset(candidate_contexts):
            reasons.add("mismo nombre y contexto contenido")
        elif _same_team_competition_continuity(active_contexts, candidate_contexts):
            reasons.add("mismo nombre, equipo y competicion en temporadas distintas")
        else:
            return [], ""
        combined_keys.extend(candidate_keys)
        selected.append(candidate_key)
    return selected, " / ".join(sorted(reasons))


def _has_deterministic_name_variant(left: str, right: str) -> bool:
    if not left or not right or normalize_alias(left) == normalize_alias(right):
        return False
    if _has_safe_token_containment(left, right):
        return True
    if _has_token_set_identity_match(left, right):
        return True
    if _has_safe_token_prefix_match(left, right):
        return True
    if _has_same_last_name_with_given_name_variant(left, right):
        return True
    return _has_single_token_name_variant_match(left, right)


def _has_deterministic_orthographic_variant(left: str, right: str) -> bool:
    if not left or not right or normalize_alias(left) == normalize_alias(right):
        return False
    return _has_safe_long_orthographic_name_match(left, right)


def _position_profile_tokens(rows: pd.DataFrame) -> set[str]:
    profile: set[str] = set()
    if rows.empty:
        return profile
    for column in ["positions", "position_roles"]:
        if column not in rows.columns:
            continue
        for raw_value in rows[column].dropna().astype(str):
            for value in re.split(r"[|,;/]+", raw_value):
                value = re.sub(r"\s*\(\d+\)\s*$", "", value.strip())
                text = clean_identifier_text(value).lower()
                if not text or text == "nan":
                    continue
                if text in {"sub", "substitute", "suplente"}:
                    continue
                if text in {"gk", "goalkeeper", "portero"}:
                    profile.add("goalkeeper")
                elif text in {"d", "dc", "dl", "dr", "defender", "defensa", "back", "centre_back", "center_back"}:
                    profile.add("defender")
                elif text in {"m", "mc", "ml", "mr", "midfielder", "mediocentro", "centrocampista"}:
                    profile.add("midfielder")
                elif text in {"f", "fw", "st", "forward", "delantero", "striker"}:
                    profile.add("forward")
                elif re.fullmatch(r"d[clr]?", text):
                    profile.add("defender")
                elif re.fullmatch(r"(a|d)?m[clr]?", text):
                    profile.add("midfielder")
                elif re.fullmatch(r"fw[clr]?", text):
                    profile.add("forward")
                else:
                    profile.add(text)
    return profile


def _position_profiles_overlap(
    left_rows: pd.DataFrame,
    right_rows: pd.DataFrame,
    *,
    allow_unknown: bool = True,
) -> bool:
    left_profile = _position_profile_tokens(left_rows)
    right_profile = _position_profile_tokens(right_rows)
    if not left_profile or not right_profile:
        return allow_unknown
    return bool(left_profile & right_profile)


def _deterministic_variant_candidates(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
    matcher,
    *,
    require_position_overlap: bool = False,
    allow_unknown_position_overlap: bool = True,
) -> list[str]:
    if not candidate_roots:
        return []
    active_names = _names_for_keys(active_keys, rows_by_key)
    active_contexts = _contexts_for_keys(active_keys, contexts_by_key)
    if not active_names or not active_contexts:
        return []

    selected: list[str] = []
    combined_keys = list(active_keys)
    for candidate_key in candidate_roots:
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu)
        candidate_names = _names_for_keys(candidate_keys, rows_by_key)
        candidate_contexts = _contexts_for_keys(candidate_keys, contexts_by_key)
        if not candidate_names or not (active_contexts & candidate_contexts):
            continue
        if require_position_overlap:
            left_position_rows = _rows_for_keys(active_keys, rows_by_key)
            right_position_rows = _rows_for_keys(candidate_keys, rows_by_key)
            if not _position_profiles_overlap(
                left_position_rows,
                right_position_rows,
                allow_unknown=allow_unknown_position_overlap,
            ):
                continue
        if _would_create_source_id_conflict(combined_keys, candidate_keys):
            continue
        if not any(
            matcher(active_name, candidate_name)
            for active_name in active_names
            for candidate_name in candidate_names
        ):
            continue
        combined_keys.extend(candidate_keys)
        selected.append(candidate_key)
    return selected


def _deterministic_name_variant_candidates(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
) -> list[str]:
    return _deterministic_variant_candidates(
        active_keys,
        candidate_roots,
        source_keys,
        dsu,
        rows_by_key,
        contexts_by_key,
        _has_deterministic_name_variant,
        require_position_overlap=True,
        allow_unknown_position_overlap=False,
    )


def _deterministic_orthographic_variant_candidates(
    active_keys: list[str],
    candidate_roots: list[str],
    source_keys: list[str],
    dsu: DisjointSet,
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
) -> list[str]:
    return _deterministic_variant_candidates(
        active_keys,
        candidate_roots,
        source_keys,
        dsu,
        rows_by_key,
        contexts_by_key,
        _has_deterministic_orthographic_variant,
        require_position_overlap=True,
        allow_unknown_position_overlap=False,
    )


def _run_identity_gemini_pass(
    source_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    contexts_by_key: dict[str, set[tuple[str, str, str]]],
    dsu: DisjointSet,
    key_methods: dict[str, set[str]],
    key_confidences: dict[str, float],
    key_name_hints: dict[str, IdentityNameHint],
    gemini_cache: dict[str, GeminiDecision],
    review_rows: list[dict[str, object]],
) -> tuple[int, int, int, int, int, int, int]:
    _gemini_section("3. IDENTIDAD - PASADA 1: FUSIONES FINALES Y GEMINI")
    live_calls = 0
    cache_hits = 0
    rejected = 0
    retry_calls = 0
    gemini_bridge_merges = 0
    deterministic_exact_fragment_merges = 0
    deterministic_exact_context_merges = 0
    deterministic_name_variant_merges = 0
    deterministic_orthographic_variant_merges = 0
    skipped_weak_evidence = 0
    processed_roots: set[str] = set()
    for key in sorted(source_keys, key=_source_key_sort_key):
        active_root = dsu.find(key)
        if active_root in processed_roots:
            continue
        active_keys = [candidate for candidate in source_keys if dsu.find(candidate) == active_root]
        candidate_roots = _candidate_roots_for_key(key, source_keys, dsu, rows_by_key, contexts_by_key)
        if not candidate_roots:
            processed_roots.add(active_root)
            continue
        deterministic_fragment_candidates = _exact_name_context_fragment_candidates(
            active_keys,
            candidate_roots,
            source_keys,
            dsu,
            rows_by_key,
            contexts_by_key,
        )
        if deterministic_fragment_candidates:
            observed_name = _best_name_for_key(_rows_for_keys(active_keys, rows_by_key))
            candidate_groups = [
                _candidate_group_for_key(candidate_key, source_keys, dsu)
                for candidate_key in deterministic_fragment_candidates
            ]
            for candidate_key in deterministic_fragment_candidates:
                dsu.union(active_keys[0], candidate_key)
            for merged_key in {
                merged_key
                for group in [active_keys, *candidate_groups]
                for merged_key in group
            }:
                key_methods[merged_key].add("deterministic_exact_name_context_fragment")
            deterministic_exact_fragment_merges += len(deterministic_fragment_candidates)
            processed_roots.add(active_root)
            continue
        deterministic_exact_candidates, deterministic_exact_reason = _exact_name_deterministic_candidates(
            active_keys,
            candidate_roots,
            source_keys,
            dsu,
            rows_by_key,
            contexts_by_key,
        )
        if deterministic_exact_candidates:
            observed_name = _best_name_for_key(_rows_for_keys(active_keys, rows_by_key))
            candidate_groups = [
                _candidate_group_for_key(candidate_key, source_keys, dsu)
                for candidate_key in deterministic_exact_candidates
            ]
            for candidate_key in deterministic_exact_candidates:
                dsu.union(active_keys[0], candidate_key)
            for merged_key in {
                merged_key
                for group in [active_keys, *candidate_groups]
                for merged_key in group
            }:
                key_methods[merged_key].add("deterministic_exact_name_context")
            deterministic_exact_context_merges += len(deterministic_exact_candidates)
            processed_roots.add(active_root)
            continue
        deterministic_name_variant_candidates = _deterministic_name_variant_candidates(
            active_keys,
            candidate_roots,
            source_keys,
            dsu,
            rows_by_key,
            contexts_by_key,
        )
        if deterministic_name_variant_candidates:
            candidate_groups = [
                _candidate_group_for_key(candidate_key, source_keys, dsu)
                for candidate_key in deterministic_name_variant_candidates
            ]
            for candidate_key in deterministic_name_variant_candidates:
                dsu.union(active_keys[0], candidate_key)
            for merged_key in {
                merged_key
                for group in [active_keys, *candidate_groups]
                for merged_key in group
            }:
                key_methods[merged_key].add("deterministic_name_variant_context")
            deterministic_name_variant_merges += len(deterministic_name_variant_candidates)
            processed_roots.add(active_root)
            continue
        deterministic_orthographic_variant_candidates = _deterministic_orthographic_variant_candidates(
            active_keys,
            candidate_roots,
            source_keys,
            dsu,
            rows_by_key,
            contexts_by_key,
        )
        if deterministic_orthographic_variant_candidates:
            candidate_groups = [
                _candidate_group_for_key(candidate_key, source_keys, dsu)
                for candidate_key in deterministic_orthographic_variant_candidates
            ]
            for candidate_key in deterministic_orthographic_variant_candidates:
                dsu.union(active_keys[0], candidate_key)
            for merged_key in {
                merged_key
                for group in [active_keys, *candidate_groups]
                for merged_key in group
            }:
                key_methods[merged_key].add("deterministic_orthographic_variant_context")
            deterministic_orthographic_variant_merges += len(deterministic_orthographic_variant_candidates)
            processed_roots.add(active_root)
            continue
        if _should_skip_gemini_for_weak_evidence(active_keys, candidate_roots, source_keys, dsu, rows_by_key):
            payload = _candidate_payload(active_keys, candidate_roots, rows_by_key, source_keys, dsu)
            _gemini_case_header(payload)
            observed_name = _best_name_for_key(_rows_for_keys(active_keys, rows_by_key))
            for active_key in active_keys:
                key_methods[active_key].add("gemini_identity_skipped_weak_evidence")
            skipped_weak_evidence += 1
            _gemini_log_identity_skipped_weak_evidence(observed_name)
            processed_roots.add(active_root)
            continue
        payload = _candidate_payload(active_keys, candidate_roots, rows_by_key, source_keys, dsu)
        decision = ask_gemini(payload, gemini_cache)
        observed_name = _best_name_for_key(_rows_for_keys(active_keys, rows_by_key))
        if decision is not None:
            if decision.from_cache:
                cache_hits += 1
            else:
                live_calls += 1

        candidate_key = str(decision.candidate_id or "").strip() if decision is not None else ""
        candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu) if candidate_key in candidate_roots else []
        candidate_name = _best_name_for_key(_rows_for_keys(candidate_keys, rows_by_key)) if candidate_keys else ""
        observed_aliases = _names_for_keys(active_keys, rows_by_key)
        candidate_aliases = _names_for_keys(candidate_keys, rows_by_key)
        should_retry = decision is None
        if decision is not None:
            if decision.resolved:
                should_retry = decision.confidence < GEMINI_ACCEPT_CONFIDENCE or candidate_key not in candidate_roots
            else:
                should_retry = decision.confidence < GEMINI_REJECT_CONFIDENCE
            if decision.resolved and not should_retry and candidate_key:
                retry_reason = _identity_reject_reason(
                    decision,
                    candidate_key,
                    candidate_roots,
                    active_keys,
                    candidate_keys,
                    observed_name,
                    candidate_name,
                    observed_aliases,
                    candidate_aliases,
                )
                should_retry = retry_reason == "local_safety_reject"

        if should_retry:
            retry_decision = ask_gemini(
                payload,
                gemini_cache,
                retry=True,
                log_case=False,
                exclude_models={decision.model} if decision is not None and decision.model else None,
            )
            if retry_decision is not None:
                retry_calls += 1
                if retry_decision.from_cache:
                    cache_hits += 1
                else:
                    live_calls += 1
                decision = retry_decision
                candidate_key = str(decision.candidate_id or "").strip()
                candidate_keys = _candidate_group_for_key(candidate_key, source_keys, dsu) if candidate_key in candidate_roots else []
                candidate_name = _best_name_for_key(_rows_for_keys(candidate_keys, rows_by_key)) if candidate_keys else ""
                candidate_aliases = _names_for_keys(candidate_keys, rows_by_key)

        if decision is None:
            row = _rows_for_keys(active_keys, rows_by_key).iloc[0]
            _append_review(
                review_rows,
                reason="gemini_identity_unavailable",
                row=row,
                candidates=payload.get("candidates", []),  # type: ignore[arg-type]
                suggested_action="Reintentar con modelos disponibles o mejorar las reglas automaticas de identidad.",
            )
            _gemini_log_identity_unavailable(observed_name)
            processed_roots.add(active_root)
            continue
        bridge_supported = bool(
            decision.full_name
            and not _any_safe_identity_name_match(observed_aliases, candidate_aliases)
            and _has_gemini_full_name_bridge(
                observed_aliases,
                candidate_aliases,
                decision.full_name,
                decision.known_as,
                decision.confidence,
            )
        )
        if (
            decision.resolved
            and decision.confidence >= GEMINI_ACCEPT_CONFIDENCE
            and candidate_key in candidate_roots
            and not _would_create_source_id_conflict(active_keys, candidate_keys)
            and _is_safe_gemini_identity_merge(
                observed_name,
                candidate_name,
                decision.confidence,
                observed_aliases,
                candidate_aliases,
                decision.full_name,
                decision.known_as,
            )
        ):
            dsu.union(active_keys[0], candidate_key)
            for merged_key in active_keys + candidate_keys:
                key_methods[merged_key].add("gemini_identity_merge")
                if bridge_supported:
                    key_methods[merged_key].add("gemini_identity_bridge_full_name_merge")
                key_confidences[merged_key] = min(key_confidences[merged_key], decision.confidence)
            _record_identity_name_hint(key_name_hints, active_keys + candidate_keys, decision)
            if bridge_supported:
                gemini_bridge_merges += 1
            final_known_as, final_full_name = _preview_identity_names(active_keys + candidate_keys, rows_by_key)
            _gemini_log_identity_merge(
                decision,
                observed_name,
                candidate_name,
                final_known_as,
                final_full_name,
            )
        elif decision.resolved and candidate_key:
            rejected += 1
            reason = _identity_reject_reason(
                decision,
                candidate_key,
                candidate_roots,
                active_keys,
                candidate_keys,
                observed_name,
                candidate_name,
                observed_aliases,
                candidate_aliases,
            )
            for active_key in active_keys:
                key_methods[active_key].add(f"gemini_identity_rejected_{reason}")
                key_confidences[active_key] = min(key_confidences[active_key], GEMINI_REJECT_CONFIDENCE)
            _gemini_log_identity_rejected(decision, reason, observed_name, candidate_name)
        elif not decision.resolved and decision.confidence >= GEMINI_REJECT_CONFIDENCE:
            for active_key in active_keys:
                key_methods[active_key].add("gemini_identity_no_match")
                key_confidences[active_key] = min(key_confidences[active_key], decision.confidence)
            _gemini_log_identity_no_match(decision, observed_name)
        else:
            row = _rows_for_keys(active_keys, rows_by_key).iloc[0]
            _append_review(
                review_rows,
                reason="gemini_identity_low_confidence",
                row=row,
                candidates=payload.get("candidates", []),  # type: ignore[arg-type]
                suggested_action="Gemini no resolvio identidad con confianza suficiente.",
            )
            _gemini_log_identity_low_confidence(decision, observed_name)
        processed_roots.add(active_root)
    if (
        deterministic_exact_fragment_merges
        or deterministic_exact_context_merges
        or deterministic_name_variant_merges
        or deterministic_orthographic_variant_merges
    ):
        _norm_log("Fusiones deterministas finales antes de Gemini:")
        _norm_log(f"  - deterministic_exact_name_context_fragment: {deterministic_exact_fragment_merges}")
        _norm_log(f"  - deterministic_exact_name_context: {deterministic_exact_context_merges}")
        _norm_log(f"  - deterministic_name_variant_context: {deterministic_name_variant_merges}")
        _norm_log(f"  - deterministic_orthographic_variant_context: {deterministic_orthographic_variant_merges}")
    return (
        live_calls,
        cache_hits,
        rejected,
        gemini_bridge_merges,
        deterministic_orthographic_variant_merges,
        retry_calls,
        skipped_weak_evidence,
    )


def _name_score(name: str, count: int, all_names: list[str]) -> tuple[float, int, str]:
    text = str(name).strip()
    tokens = _name_tokens(text)
    score = float(count)
    if _has_poor_abbreviation(text):
        score -= 100
    if len(tokens) == 1:
        if any(tokens[0] in _name_tokens(other) and len(_name_tokens(other)) > 1 for other in all_names):
            score -= 2
        else:
            score += 1
    elif 2 <= len(tokens) <= 3:
        score += 4
    elif len(tokens) > 4:
        score -= 1
    if any(ord(ch) > 127 for ch in text):
        score += 0.25
    return score, -len(text), text


def _choose_known_as(names: list[str], previous: str = "") -> str:
    if previous and not _has_poor_abbreviation(previous):
        return previous
    clean_names = [name.strip() for name in names if name.strip()]
    if not clean_names:
        return "Unknown Player"
    counts = Counter(clean_names)
    return sorted(counts, key=lambda name: _name_score(name, counts[name], clean_names), reverse=True)[0]


def _full_name_supported_by_names(full_name: str, names: list[str]) -> bool:
    if not has_expanded_full_name(full_name):
        return False
    expanded_names = [name.strip() for name in names if has_expanded_full_name(name)]
    full_slug = normalize_alias(full_name)
    return any(normalize_alias(name) == full_slug for name in expanded_names)


def _choose_full_name(names: list[str], previous: str = "") -> str:
    if previous and _full_name_supported_by_names(previous, names):
        return previous
    candidates = [name.strip() for name in names if has_expanded_full_name(name)]
    if not candidates:
        return ""
    counts = Counter(candidates)
    return sorted(candidates, key=lambda name: (len(_name_tokens(name)), counts[name], len(name), name), reverse=True)[0]


def _build_previous_name_lookup(previous_alias_map: pd.DataFrame, previous_identities: pd.DataFrame) -> dict[str, dict[str, str]]:
    if previous_alias_map.empty or previous_identities.empty:
        return {}
    if "id_player" not in previous_alias_map.columns or "id_player" not in previous_identities.columns:
        return {}
    identity_lookup: dict[str, dict[str, str]] = {}
    for row in previous_identities.itertuples(index=False):
        player_id = _log_value(getattr(row, "id_player", ""))
        if not player_id:
            continue
        identity_lookup[player_id] = {
            "known_as": _log_value(getattr(row, "known_as", "")),
            "full_name": _log_value(getattr(row, "full_name", "")),
        }
    out: dict[str, dict[str, str]] = {}
    for row in previous_alias_map.itertuples(index=False):
        source_key = _log_value(getattr(row, "source_player_key", ""))
        player_id = _log_value(getattr(row, "id_player", ""))
        if source_key and player_id in identity_lookup:
            out[source_key] = identity_lookup[player_id]
    return out


def _identity_name_payload(
    row: pd.Series,
    reason: str,
    *,
    verification_stage: str = "",
    proposed_decision: GeminiDecision | None = None,
) -> dict[str, object]:
    source_keys = _split_joined_values(row.get("source_player_keys", ""))
    sources = sorted({key.split(":", 1)[0] for key in source_keys if ":" in key})
    proposed_known_as = str(proposed_decision.known_as or "").strip() if proposed_decision is not None else ""
    proposed_full_name = str(proposed_decision.full_name or "").strip() if proposed_decision is not None else ""
    return {
        "task": "name_enrichment",
        "known_as": row.get("known_as", ""),
        "full_name": row.get("full_name", ""),
        "aliases": _split_joined_values(row.get("aliases", "")),
        "source_player_keys": source_keys,
        "sources": sources,
        "id_understat": _split_joined_values(row.get("id_understat", "")),
        "id_whoscored": _split_joined_values(row.get("id_whoscored", "")),
        "competitions": _split_joined_values(row.get("competitions", "")),
        "seasons": _split_joined_values(row.get("seasons", "")),
        "teams": _split_joined_values(row.get("teams", "")),
        "name_enrichment_policy": NAME_ENRICHMENT_POLICY_VERSION,
        "verification_stage": verification_stage,
        "proposed_known_as": proposed_known_as,
        "proposed_full_name": proposed_full_name,
        "requires_unique_full_name": reason in {"duplicate_full_name", "duplicate_player_id"},
        "requires_disambiguating_full_name": reason in {"duplicate_full_name", "duplicate_player_id"},
        "allows_automatic_homonym_id": False,
        "duplicate_full_name": row.get("full_name", "") if reason == "duplicate_full_name" else "",
        "duplicate_player_id": _preliminary_player_id_for_row(row) if reason == "duplicate_player_id" else "",
        "reason": reason,
        "instruction": (
            "Si verification_stage esta informado, actua como verificador independiente de proposed_full_name: "
            "confirma exactamente ese full_name solo si es correcto, devuelve otro full_name solo si estas muy seguro, "
            "y devuelve resolved=false si el contexto no basta para verificarlo. "
            "Propon known_as y full_name completo solo si la identidad queda respaldada por el contexto dado. "
            "No uses equipo, temporada, fuente ni sufijos artificiales. "
            "Si dos jugadores distintos comparten el mismo nombre completo real, no inventes un nombre diferente; "
            "devuelve resolved=false para que el pipeline marque revision en vez de crear un id_player con IDs de fuente."
        ),
    }


def _internal_name_evidence(row: pd.Series) -> list[str]:
    evidence: list[str] = []
    for value in [
        row.get("known_as", ""),
        row.get("full_name", ""),
        *_split_joined_values(row.get("aliases", "")),
    ]:
        text = str(value or "").strip()
        if text and text.lower() != "nan" and text not in evidence:
            evidence.append(text)
    return evidence


def _name_decision_support_reason(decision: GeminiDecision | None, row: pd.Series, reason: str) -> str:
    if decision is None or not decision.resolved or decision.confidence < GEMINI_ACCEPT_CONFIDENCE:
        return ""
    full_name = str(decision.full_name or "").strip()
    known_as = str(decision.known_as or "").strip()
    if not known_as or not has_expanded_full_name(full_name):
        return ""

    expanded_evidence = [name for name in _internal_name_evidence(row) if has_expanded_full_name(name)]
    full_slug = normalize_alias(full_name)
    for evidence_name in expanded_evidence:
        if normalize_alias(evidence_name) == full_slug:
            return "exact_internal_full_name"

    if reason == "duplicate_full_name":
        if _is_duplicate_full_name_disambiguation(decision, row):
            return "duplicate_full_name_disambiguation"
        return ""

    for evidence_name in expanded_evidence:
        if _has_safe_token_containment(evidence_name, full_name):
            return "expanded_internal_alias_containment"
        if _has_safe_token_prefix_match(evidence_name, full_name):
            return "expanded_internal_alias_variant"
    return ""


def _known_as_supported_by_identity(known_as: str, row: pd.Series) -> bool:
    known_tokens = _name_tokens(known_as)
    if not known_tokens:
        return False
    known_slug = normalize_alias(known_as)
    for evidence_name in _internal_name_evidence(row):
        evidence_slug = normalize_alias(evidence_name)
        if evidence_slug == known_slug:
            return True
        evidence_tokens = _name_tokens(evidence_name)
        if len(known_tokens) == 1 and len(evidence_tokens) == 1:
            if _tokens_are_name_variant(known_tokens[0], evidence_tokens[0]):
                return True
        if _has_safe_identity_name_match(known_as, evidence_name, allow_single_token=True):
            return True
    return False


def _has_structurally_valid_name_decision(
    decision: GeminiDecision | None,
    row: pd.Series,
    reason: str,
    *,
    min_confidence: float = GEMINI_NICKNAME_ACCEPT_CONFIDENCE,
) -> bool:
    if decision is None or not decision.resolved or decision.confidence < min_confidence:
        return False
    if decision.candidate_id:
        return False
    if reason == "duplicate_full_name":
        return False
    full_name = str(decision.full_name or "").strip()
    known_as = str(decision.known_as or "").strip()
    return has_expanded_full_name(full_name) and _known_as_supported_by_identity(known_as, row)


def _same_full_name_decision(left: GeminiDecision | None, right: GeminiDecision | None) -> bool:
    if left is None or right is None:
        return False
    return bool(left.full_name and right.full_name and normalize_alias(left.full_name) == normalize_alias(right.full_name))


def _tokens_are_ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return False
    cursor = 0
    for token in haystack:
        if cursor < len(needle) and token == needle[cursor]:
            cursor += 1
    return cursor == len(needle)


def _is_duplicate_full_name_disambiguation(decision: GeminiDecision, row: pd.Series) -> bool:
    if decision.confidence < GEMINI_NICKNAME_ACCEPT_CONFIDENCE:
        return False
    base_full_name = str(row.get("full_name", "") or "").strip()
    proposed_full_name = str(decision.full_name or "").strip()
    if not has_expanded_full_name(base_full_name) or not has_expanded_full_name(proposed_full_name):
        return False
    base_tokens = _name_tokens(base_full_name)
    proposed_tokens = _name_tokens(proposed_full_name)
    if len(proposed_tokens) <= len(base_tokens):
        return False
    return _tokens_are_ordered_subsequence(base_tokens, proposed_tokens)


def _name_verification_payload(row: pd.Series, reason: str, decision: GeminiDecision) -> dict[str, object]:
    return _identity_name_payload(
        row,
        reason,
        verification_stage="strong_full_name_verification",
        proposed_decision=decision,
    )


def _common_prefix_length(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def _has_local_name_bridge(decision: GeminiDecision, row: pd.Series) -> bool:
    known_as = str(decision.known_as or "").strip()
    full_name = str(decision.full_name or "").strip()
    if not known_as or not has_expanded_full_name(full_name):
        return False
    if _name_decision_support_reason(decision, row, "missing_or_incomplete_full_name"):
        return True

    known_tokens = _name_tokens(known_as)
    full_tokens = _name_tokens(full_name)
    if not known_tokens or not full_tokens:
        return False
    known_joined = "".join(known_tokens)
    full_joined = "".join(full_tokens)
    if len(known_joined) >= 5 and full_joined.startswith(known_joined):
        return True

    for known_token in known_tokens:
        for full_token in full_tokens:
            if known_token == full_token:
                return True
            if min(len(known_token), len(full_token)) >= 4 and (known_token in full_token or full_token in known_token):
                return True
            if _tokens_are_name_variant(known_token, full_token):
                return True
            if min(len(known_token), len(full_token)) >= 5 and _common_prefix_length(known_token, full_token) >= 4:
                return True
    return False


def _has_contextual_source_evidence(row: pd.Series) -> bool:
    source_keys = _identity_source_keys(row)
    sources = {key.split(":", 1)[0] for key in source_keys if ":" in key}
    has_id_backed_source = any(
        key.split(":", 1)[0] in ID_BACKED_SOURCES and ":" not in key.split(":", 1)[1]
        for key in source_keys
        if ":" in key
    )
    has_context = bool(
        _split_joined_values(row.get("competitions", ""))
        and _split_joined_values(row.get("seasons", ""))
        and _split_joined_values(row.get("teams", ""))
    )
    return has_context and has_id_backed_source and len(sources) >= 2


def _has_verified_contextual_homonym_name_support(
    decision: GeminiDecision,
    row: pd.Series,
    reason: str,
) -> bool:
    if reason != "duplicate_player_id":
        return False
    if not _has_structurally_valid_name_decision(decision, row, reason):
        return False
    preliminary_id = _preliminary_player_id_for_row(row)
    known_as_id = _identity_name_to_id_base(row.get("known_as", "")) or "unknown_player"
    if preliminary_id != known_as_id:
        return False
    proposed_full_id = _full_name_to_id(str(decision.full_name or ""))
    if not proposed_full_id or proposed_full_id == preliminary_id:
        return False
    return _has_contextual_source_evidence(row)


def _gemini_log_name_applied(
    decision: GeminiDecision,
    before_known_as: str,
    before_full_name: str,
    after_known_as: str,
    after_full_name: str,
) -> None:
    _gemini_log("[NOMBRES] RESULTADO: APLICADA - nombre enriquecido")
    _gemini_log("  Antes:")
    _gemini_log(f"    known_as: {before_known_as or 'null'}")
    _gemini_log(f"    full_name: {before_full_name or 'null'}")
    _gemini_log("  Despues:")
    _gemini_log(f"    known_as: {after_known_as or 'null'}")
    _gemini_log(f"    full_name: {after_full_name or 'null'}")
    _gemini_log("  Gemini:")
    _gemini_log(f"    decision_gemini: {_gemini_decision_action('name_enrichment', decision)} (resolved={decision.resolved})")
    _gemini_log(f"    confidence: {decision.confidence:.2f}")


def _gemini_log_name_unavailable(before_known_as: str, before_full_name: str) -> None:
    _gemini_log("[NOMBRES] RESULTADO: NO APLICADA - sin respuesta util")
    _gemini_log("  Antes:")
    _gemini_log(f"    known_as: {before_known_as or 'null'}")
    _gemini_log(f"    full_name: {before_full_name or 'null'}")
    _gemini_log("  accion: se mantiene el nombre actual y queda pendiente de revision si falla la validacion final")


def _gemini_log_name_rejected(
    decision: GeminiDecision,
    before_known_as: str,
    before_full_name: str,
    local_reason: str = "baja confianza o full_name incompleto",
) -> None:
    _gemini_log("[NOMBRES] RESULTADO: NO APLICADA - propuesta no valida")
    _gemini_log("  Antes:")
    _gemini_log(f"    known_as: {before_known_as or 'null'}")
    _gemini_log(f"    full_name: {before_full_name or 'null'}")
    _gemini_log("  Propuesta Gemini:")
    _gemini_log(f"    known_as: {decision.known_as or 'null'}")
    _gemini_log(f"    full_name: {decision.full_name or 'null'}")
    _gemini_log(f"    confidence: {decision.confidence:.2f}")
    _gemini_log(f"  motivo_local: {local_reason}")


def _apply_name_decision(identities: pd.DataFrame, index: int, decision: GeminiDecision, method: str) -> None:
    known_as = str(decision.known_as or "").strip()
    full_name = str(decision.full_name or "").strip()
    if known_as:
        identities.at[index, "known_as"] = known_as
    if full_name:
        identities.at[index, "full_name"] = full_name
    methods = {part.strip() for part in str(identities.at[index, "resolution_method"]).split("|") if part.strip()}
    methods.add(method)
    methods.discard("needs_name_enrichment")
    identities.at[index, "resolution_method"] = " | ".join(sorted(methods))
    identities.at[index, "confidence"] = f"{min(float(identities.at[index, 'confidence']), decision.confidence):.3f}"
    identities.at[index, "needs_review"] = "false"


def _run_name_enrichment_pass(
    identities: pd.DataFrame,
    gemini_cache: dict[str, GeminiDecision],
    review_rows: list[dict[str, object]],
    *,
    title: str,
    reason: str,
    retry: bool = False,
    only_duplicate_full_names: bool = False,
    only_duplicate_player_ids: bool = False,
) -> tuple[int, int, int, int, int, int, int, int]:
    _gemini_section(title)
    live_calls = 0
    cache_hits = 0
    accepted_with_internal_support = 0
    accepted_with_model_verification = 0
    rejected_without_internal_support = 0
    rejected_after_model_verification = 0
    verification_calls = 0
    verification_cache_hits = 0
    duplicate_names = set()
    duplicate_player_ids = set()
    if only_duplicate_full_names and only_duplicate_player_ids:
        raise ValueError("No se pueden activar a la vez only_duplicate_full_names y only_duplicate_player_ids")
    if only_duplicate_full_names:
        slugs = identities["full_name"].fillna("").astype(str).map(normalize_alias)
        duplicate_names = set(slugs[slugs.ne("") & slugs.duplicated(keep=False)].tolist())
        for slug in sorted(duplicate_names):
            duplicated = identities.loc[slugs.eq(slug)]
            full_name = str(duplicated.iloc[0].get("full_name", "")).strip() if not duplicated.empty else slug
            _gemini_log("[NOMBRES] FULL_NAME DUPLICADO")
            _gemini_log(f"  full_name: {full_name}")
            _gemini_log(f"  identidades afectadas: {len(duplicated)}")
            _gemini_log("  accion: se enviaran todas a reparacion de nombres")
    if only_duplicate_player_ids:
        preliminary_ids = identities.apply(_preliminary_player_id_for_row, axis=1)
        duplicate_player_ids = set(preliminary_ids[preliminary_ids.ne("") & preliminary_ids.duplicated(keep=False)].tolist())
        for player_id in sorted(duplicate_player_ids):
            duplicated = identities.loc[preliminary_ids.eq(player_id)]
            _gemini_log("[NOMBRES] ID_PLAYER DUPLICADO")
            _gemini_log(f"  id_player preliminar: {player_id}")
            _gemini_log(f"  identidades afectadas: {len(duplicated)}")
            _gemini_log("  accion: se intentara enriquecer full_name solo para este homonimo")
    for index, row in identities.iterrows():
        full_name = str(row.get("full_name", "")).strip()
        full_slug = normalize_alias(full_name)
        needs_enrichment = not has_expanded_full_name(full_name)
        preliminary_id = _preliminary_player_id_for_row(row)
        if only_duplicate_full_names:
            needs_enrichment = full_slug in duplicate_names
        elif only_duplicate_player_ids:
            needs_enrichment = preliminary_id in duplicate_player_ids
        if not needs_enrichment:
            continue
        before_known_as = str(row.get("known_as", "")).strip()
        before_full_name = str(row.get("full_name", "")).strip()
        pass_reason = "duplicate_full_name" if only_duplicate_full_names else reason
        payload = _identity_name_payload(row, pass_reason)
        decision = ask_gemini(payload, gemini_cache, retry=retry)
        if decision is None:
            _gemini_log_name_unavailable(before_known_as, before_full_name)
            continue
        if decision.from_cache:
            cache_hits += 1
        else:
            live_calls += 1
        support_reason = _name_decision_support_reason(decision, row, pass_reason)
        if support_reason:
            accepted_with_internal_support += 1
            _apply_name_decision(identities, index, decision, f"gemini_name_enrichment_{support_reason}")
            _gemini_log_name_applied(
                decision,
                before_known_as,
                before_full_name,
                str(identities.at[index, "known_as"]).strip(),
                str(identities.at[index, "full_name"]).strip(),
            )
        else:
            accepted_decision: GeminiDecision | None = None
            accepted_method = ""
            verification_failed = False
            if _has_structurally_valid_name_decision(decision, row, pass_reason):
                if retry:
                    if _has_structurally_valid_name_decision(
                        decision,
                        row,
                        pass_reason,
                        min_confidence=max(GEMINI_NICKNAME_ACCEPT_CONFIDENCE, 0.98),
                    ):
                        accepted_decision = decision
                        accepted_method = "gemini_name_enrichment_retry_model_verified"
                else:
                    verification_decision = ask_gemini(
                        _name_verification_payload(row, pass_reason, decision),
                        gemini_cache,
                        retry=True,
                        exclude_models={decision.model} if decision.model else None,
                    )
                    if verification_decision is not None:
                        if verification_decision.from_cache:
                            verification_cache_hits += 1
                        else:
                            verification_calls += 1
                        verification_support = _name_decision_support_reason(verification_decision, row, pass_reason)
                        if verification_support:
                            accepted_decision = verification_decision
                            accepted_method = f"gemini_name_enrichment_verified_{verification_support}"
                        elif (
                            _has_structurally_valid_name_decision(verification_decision, row, pass_reason)
                            and _same_full_name_decision(decision, verification_decision)
                        ):
                            accepted_decision = decision
                            accepted_method = "gemini_name_enrichment_model_consensus"
                        elif _has_structurally_valid_name_decision(
                            verification_decision,
                            row,
                            pass_reason,
                            min_confidence=max(GEMINI_NICKNAME_ACCEPT_CONFIDENCE, 0.98),
                        ):
                            accepted_decision = verification_decision
                            accepted_method = "gemini_name_enrichment_retry_model_correction"
                    verification_failed = accepted_decision is None

            if accepted_decision is not None:
                if _has_local_name_bridge(accepted_decision, row):
                    accepted_method = f"{accepted_method}_local_name_bridge"
                elif _has_verified_contextual_homonym_name_support(accepted_decision, row, pass_reason):
                    accepted_method = f"{accepted_method}_verified_contextual_homonym"
                else:
                    accepted_decision = None
                    verification_failed = True

            if accepted_decision is not None:
                accepted_with_model_verification += 1
                _apply_name_decision(identities, index, accepted_decision, accepted_method)
                _gemini_log_name_applied(
                    accepted_decision,
                    before_known_as,
                    before_full_name,
                    str(identities.at[index, "known_as"]).strip(),
                    str(identities.at[index, "full_name"]).strip(),
                )
            elif (
                decision.resolved
                and decision.confidence >= GEMINI_ACCEPT_CONFIDENCE
                and has_expanded_full_name(str(decision.full_name or ""))
            ):
                if verification_failed:
                    rejected_after_model_verification += 1
                else:
                    rejected_without_internal_support += 1
                methods = {
                    part.strip()
                    for part in str(identities.at[index, "resolution_method"]).split("|")
                    if part.strip()
                }
                methods.add(
                    "gemini_name_rejected_verification_failed"
                    if verification_failed
                    else "gemini_name_rejected_no_internal_support"
                )
                identities.at[index, "resolution_method"] = " | ".join(sorted(methods))
                local_reject_reason = (
                    "full_name propuesto sin respaldo interno ni puente local suficiente"
                    if verification_failed
                    else "full_name propuesto sin respaldo interno suficiente"
                )
                _gemini_log_name_rejected(
                    decision,
                    before_known_as,
                    before_full_name,
                    local_reject_reason,
                )
            else:
                _gemini_log_name_rejected(decision, before_known_as, before_full_name)
    return (
        live_calls,
        cache_hits,
        accepted_with_internal_support,
        rejected_without_internal_support,
        verification_calls,
        verification_cache_hits,
        accepted_with_model_verification,
        rejected_after_model_verification,
    )


def _full_name_to_id(full_name: str) -> str:
    return clean_identifier_text(full_name).lower()


def _identity_name_to_id_base(name: object) -> str:
    return clean_identifier_text(_log_value(name)).lower()


def _source_key_to_homonym_suffix(source_key: str) -> str:
    text = str(source_key or "").strip()
    if ":" not in text:
        return ""
    source, value = text.split(":", 1)
    if source not in ID_BACKED_SOURCES or ":" in value:
        return ""
    clean_value = clean_identifier_text(value).lower()
    return f"{source}_{clean_value}" if clean_value else ""


def _contextual_homonym_suffix_from_values(teams: object = "", competitions: object = "") -> str:
    team_values = _split_joined_values(teams)
    if team_values:
        return clean_identifier_text("_".join(sorted(team_values))).lower()
    competition_values = _split_joined_values(competitions)
    if competition_values:
        return clean_identifier_text("_".join(sorted(competition_values))).lower()
    return ""


def _homonym_suffix_candidates(
    *,
    id_understat: object = "",
    id_whoscored: object = "",
    source_player_keys: object = "",
) -> list[str]:
    candidates: list[str] = []
    for source, value in [
        ("understat", id_understat),
        ("whoscored", id_whoscored),
    ]:
        for source_id in _split_joined_values(value):
            suffix = _source_key_to_homonym_suffix(f"{source}:{source_id}")
            if suffix and suffix not in candidates:
                candidates.append(suffix)
    for source_key in _split_joined_values(source_player_keys):
        suffix = _source_key_to_homonym_suffix(source_key)
        if suffix and suffix not in candidates:
            candidates.append(suffix)
    return sorted(candidates, key=lambda suffix: (0 if suffix.startswith("understat_") else 1, suffix))


def _name_based_id_from_name(base_name: object, row: pd.Series) -> str:
    return _identity_name_to_id_base(base_name) or "unknown_player"


def _preliminary_player_id_for_row(row: pd.Series) -> str:
    full_name = str(row.get("full_name", "") or "").strip()
    if has_expanded_full_name(full_name):
        return _full_name_to_id(full_name)
    return _name_based_id_from_name(row.get("known_as", ""), row)


def is_valid_player_id_for_full_name(
    player_id: object,
    full_name: object,
    *,
    known_as: object = "",
    id_understat: object = "",
    id_whoscored: object = "",
    source_player_keys: object = "",
    competitions: object = "",
    teams: object = "",
    resolution_method: object = "",
) -> bool:
    base_id = _full_name_to_id(_log_value(full_name))
    value = _log_value(player_id)
    if not base_id:
        known_base_id = _identity_name_to_id_base(known_as) or "unknown_player"
        if value == known_base_id:
            return True
        base_id = known_base_id
    elif value == base_id:
        return True

    methods = {part.strip() for part in str(resolution_method or "").split("|") if part.strip()}
    if "automatic_known_as_homonym_id" in methods and value == (_identity_name_to_id_base(known_as) or "unknown_player"):
        return True
    if "automatic_contextual_homonym_id" in methods:
        suffix = _contextual_homonym_suffix_from_values(teams, competitions)
        if suffix and value == f"{base_id}_{suffix}":
            return True
    return False


def _method_set(value: object) -> set[str]:
    return {part.strip() for part in str(value or "").split("|") if part.strip()}


def _source_keys_for_player_record(row: pd.Series) -> list[str]:
    source_keys = _split_joined_values(row.get("source_player_keys", ""))
    for source, column in [("understat", "id_understat"), ("whoscored", "id_whoscored")]:
        for source_id in _split_joined_values(row.get(column, "")):
            source_key = f"{source}:{source_id}"
            if source_key not in source_keys:
                source_keys.append(source_key)
    return sorted(source_keys, key=_source_key_sort_key)


def _player_record_from_row(row: pd.Series, identity_info: dict[str, object] | None = None) -> dict[str, object]:
    identity_info = identity_info or {}
    return {
        "id_player": _log_value(row.get("id_player", "")),
        "known_as": _first_non_empty([row.get("known_as", ""), row.get("knownAs", ""), identity_info.get("known_as", "")]),
        "full_name": _first_non_empty([row.get("full_name", ""), row.get("fullName", ""), identity_info.get("full_name", "")]),
        "id_understat": _first_non_empty([row.get("id_understat", ""), row.get("idUnderstat", ""), identity_info.get("id_understat", "")]),
        "id_whoscored": _first_non_empty([row.get("id_whoscored", ""), row.get("idWhoscored", ""), identity_info.get("id_whoscored", "")]),
        "source_player_keys": _first_non_empty([row.get("source_player_keys", ""), identity_info.get("source_player_keys", "")]),
        "competitions": _first_non_empty([row.get("competitions", ""), identity_info.get("competitions", "")]),
        "teams": _first_non_empty([row.get("teams", ""), identity_info.get("teams", "")]),
        "resolution_method": _first_non_empty([row.get("resolution_method", ""), identity_info.get("resolution_method", "")]),
    }


def _duplicate_full_name_group_is_allowed(group_rows: pd.DataFrame) -> bool:
    player_ids = [str(value or "").strip() for value in group_rows["id_player"].tolist()]
    if not all(player_ids) or len(set(player_ids)) != len(player_ids):
        return False

    for _, row in group_rows.iterrows():
        if not is_valid_player_id_for_full_name(
            row.get("id_player", ""),
            row.get("full_name", ""),
            known_as=row.get("known_as", ""),
            id_understat=row.get("id_understat", ""),
            id_whoscored=row.get("id_whoscored", ""),
            source_player_keys=row.get("source_player_keys", ""),
            competitions=row.get("competitions", ""),
            teams=row.get("teams", ""),
            resolution_method=row.get("resolution_method", ""),
        ):
            return False

    source_keys = [
        source_key
        for _, row in group_rows.iterrows()
        for source_key in _source_keys_for_player_record(row)
    ]
    has_source_id_conflict = _would_create_source_id_conflict([], source_keys)
    all_homonym_methods = all(_method_set(row.get("resolution_method", "")) & HOMONYM_ID_METHODS for _, row in group_rows.iterrows())
    return has_source_id_conflict or all_homonym_methods


def unjustified_duplicate_full_name_rows(
    players: pd.DataFrame,
    *,
    identity_info_by_id: dict[str, dict[str, object]] | None = None,
) -> pd.DataFrame:
    if players.empty:
        return pd.DataFrame()

    records: list[dict[str, object]] = []
    identity_info_by_id = identity_info_by_id or {}
    for index, row in players.iterrows():
        player_id = _log_value(row.get("id_player", ""))
        record = _player_record_from_row(row, identity_info_by_id.get(player_id, {}))
        record["_source_index"] = index
        records.append(record)
    normalized = pd.DataFrame(records)
    if normalized.empty:
        return pd.DataFrame()

    full_names = normalized["full_name"].fillna("").astype(str).str.strip()
    duplicate_full_names = full_names.ne("") & full_names.duplicated(keep=False)
    if not duplicate_full_names.any():
        return pd.DataFrame()

    violation_indices: list[int] = []
    for _, group in normalized.loc[duplicate_full_names].groupby(full_names[duplicate_full_names], sort=False):
        if not _duplicate_full_name_group_is_allowed(group):
            violation_indices.extend(group["_source_index"].astype(int).tolist())
    if not violation_indices:
        return pd.DataFrame()
    return players.loc[violation_indices].copy()


def _identity_source_keys(row: pd.Series) -> list[str]:
    return sorted(_split_joined_values(row.get("source_player_keys", "")), key=_source_key_sort_key)


def _identity_alias_names(row: pd.Series) -> list[str]:
    names: list[str] = []
    for value in [row.get("known_as", ""), row.get("full_name", ""), *_split_joined_values(row.get("aliases", ""))]:
        text = str(value or "").strip()
        if text and text.lower() != "nan" and text not in names:
            names.append(text)
    return names


def _identity_contexts_from_row(row: pd.Series) -> set[tuple[str, str, str]]:
    competitions = _split_joined_values(row.get("competitions", ""))
    seasons = _split_joined_values(row.get("seasons", ""))
    teams = _split_joined_values(row.get("teams", ""))
    if not competitions or not seasons or not teams:
        return set()
    return {(competition, season, team) for competition in competitions for season in seasons for team in teams}


def _identity_has_id_backed_source(row: pd.Series) -> bool:
    return bool(_homonym_suffix_candidates(
        id_understat=row.get("id_understat", ""),
        id_whoscored=row.get("id_whoscored", ""),
        source_player_keys=row.get("source_player_keys", ""),
    ))


def _is_weak_isolated_duplicate_row(row: pd.Series, group_rows: pd.DataFrame) -> bool:
    if _identity_has_id_backed_source(row):
        return False
    full_name_tokens = set(_name_tokens(str(row.get("full_name", ""))))
    alias_tokens = [
        tokens
        for name in _identity_alias_names(row)
        for tokens in [_name_tokens(name)]
        if tokens
    ]
    single_alias_tokens = [tokens[0] for tokens in alias_tokens if len(tokens) == 1]
    if not single_alias_tokens:
        return False
    if any(token in full_name_tokens for token in single_alias_tokens):
        return False
    row_contexts = _identity_contexts_from_row(row)
    other_contexts: set[tuple[str, str, str]] = set()
    for other_index, other_row in group_rows.iterrows():
        if other_index != row.name:
            other_contexts |= _identity_contexts_from_row(other_row)
    return bool(row_contexts and other_contexts and not (row_contexts & other_contexts))


def _can_merge_duplicate_full_name_rows(group_rows: pd.DataFrame) -> bool:
    source_keys = [
        source_key
        for _, row in group_rows.iterrows()
        for source_key in _identity_source_keys(row)
    ]
    if _would_create_source_id_conflict([], source_keys):
        return False
    if any(_is_weak_isolated_duplicate_row(row, group_rows) for _, row in group_rows.iterrows()):
        return False
    return True


def _append_identity_method(methods_text: object, method: str) -> str:
    methods = {part.strip() for part in str(methods_text or "").split("|") if part.strip()}
    methods.add(method)
    methods.discard("needs_name_enrichment")
    return " | ".join(sorted(methods))


def _merge_identity_group_rows(group_rows: pd.DataFrame, method: str) -> dict[str, object]:
    source_keys = sorted(
        {
            source_key
            for _, row in group_rows.iterrows()
            for source_key in _identity_source_keys(row)
        },
        key=_source_key_sort_key,
    )
    alias_names = [
        name
        for _, row in group_rows.iterrows()
        for name in _identity_alias_names(row)
    ]
    full_name = _choose_full_name(group_rows["full_name"].dropna().astype(str).tolist())
    if not full_name:
        full_name = _first_non_empty(group_rows["full_name"])
    preferred_known_as = [
        str(row.get("known_as", "")).strip()
        for _, row in group_rows.iterrows()
        if str(row.get("known_as", "")).strip()
        and normalize_alias(row.get("known_as", "")) != normalize_alias(full_name)
    ]
    known_as = _choose_known_as(preferred_known_as or alias_names)
    confidence_values: list[float] = []
    for value in group_rows["confidence"].dropna().astype(str):
        try:
            confidence_values.append(float(value))
        except ValueError:
            continue
    methods = {
        part.strip()
        for value in group_rows["resolution_method"].dropna().astype(str)
        for part in value.split("|")
        if part.strip()
    }
    methods.add(method)
    methods.discard("needs_name_enrichment")
    understat_ids = _join_values(
        source_key.split(":", 1)[1]
        for source_key in source_keys
        if source_key.startswith("understat:")
    )
    whoscored_ids = _join_values(
        source_key.split(":", 1)[1]
        for source_key in source_keys
        if source_key.startswith("whoscored:")
    )
    return {
        "id_player": "",
        "known_as": known_as,
        "full_name": full_name,
        "aliases": _join_values(alias_names),
        "source_player_keys": " | ".join(source_keys),
        "id_understat": understat_ids or _join_values(
            value for _, row in group_rows.iterrows() for value in _split_joined_values(row.get("id_understat", ""))
        ),
        "id_whoscored": whoscored_ids or _join_values(
            value for _, row in group_rows.iterrows() for value in _split_joined_values(row.get("id_whoscored", ""))
        ),
        "competitions": _join_values(
            value for _, row in group_rows.iterrows() for value in _split_joined_values(row.get("competitions", ""))
        ),
        "seasons": _join_values(
            value for _, row in group_rows.iterrows() for value in _split_joined_values(row.get("seasons", ""))
        ),
        "teams": _join_values(
            value for _, row in group_rows.iterrows() for value in _split_joined_values(row.get("teams", ""))
        ),
        "resolution_method": " | ".join(sorted(methods)),
        "confidence": f"{min(confidence_values or [1.0]):.3f}",
        "needs_review": str(not has_expanded_full_name(full_name)).lower(),
    }


def _merge_duplicate_full_name_identities(
    identities: pd.DataFrame,
    alias_rows: pd.DataFrame,
    *,
    method: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if identities.empty:
        return identities, alias_rows, 0
    slugs = identities["full_name"].fillna("").astype(str).map(normalize_alias)
    groups_by_slug: dict[str, list[int]] = defaultdict(list)
    for index, slug in slugs.items():
        if slug:
            groups_by_slug[slug].append(int(index))

    processed: set[int] = set()
    old_to_new: dict[int, int] = {}
    new_rows: list[dict[str, object]] = []
    merged_count = 0
    for old_index in range(len(identities)):
        if old_index in processed:
            continue
        slug = str(slugs.iloc[old_index])
        group_indices = groups_by_slug.get(slug, [old_index]) if slug else [old_index]
        group_indices = [index for index in group_indices if index not in processed]
        if len(group_indices) > 1:
            group_rows = identities.loc[group_indices]
            if _can_merge_duplicate_full_name_rows(group_rows):
                new_index = len(new_rows)
                new_rows.append(_merge_identity_group_rows(group_rows, method))
                for index in group_indices:
                    old_to_new[index] = new_index
                    processed.add(index)
                merged_count += len(group_indices) - 1
                continue
        for index in group_indices:
            new_index = len(new_rows)
            new_rows.append(identities.loc[index].to_dict())
            old_to_new[index] = new_index
            processed.add(index)

    merged_identities = pd.DataFrame(new_rows, columns=IDENTITY_COLUMNS)
    if not alias_rows.empty and "_identity_index" in alias_rows.columns:
        merged_alias_rows = alias_rows.copy()
        merged_alias_rows["_identity_index"] = merged_alias_rows["_identity_index"].astype(int).map(old_to_new)
    else:
        merged_alias_rows = alias_rows
    return merged_identities, merged_alias_rows, merged_count


def _strong_identity_name_slug(name: object) -> str:
    slug = normalize_alias(str(name or ""))
    if not slug:
        return ""
    if not _is_strong_transfer_alias(slug):
        return ""
    return slug


def _identity_contexts_from_alias_rows(alias_rows: pd.DataFrame) -> dict[int, set[tuple[str, str, str]]]:
    contexts_by_index: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
    if alias_rows.empty or "_identity_index" not in alias_rows.columns:
        return contexts_by_index
    for _, row in alias_rows.iterrows():
        try:
            identity_index = int(row.get("_identity_index", -1))
        except (TypeError, ValueError):
            continue
        competition = str(row.get("id_competition", "") or "").strip()
        season = str(row.get("id_season", "") or "").strip()
        team = str(row.get("id_team", "") or "").strip()
        if competition and season and team:
            contexts_by_index[identity_index].add((competition, season, team))
    return contexts_by_index


def _contexts_are_same_team_competition(
    left_contexts: set[tuple[str, str, str]],
    right_contexts: set[tuple[str, str, str]],
) -> bool:
    for left_competition, _, left_team in left_contexts:
        if not left_competition or not left_team:
            continue
        for right_competition, _, right_team in right_contexts:
            if left_competition == right_competition and left_team == right_team:
                return True
    return False


def _identity_contexts_for_index(
    identities: pd.DataFrame,
    index: int,
    contexts_by_index: dict[int, set[tuple[str, str, str]]],
) -> set[tuple[str, str, str]]:
    contexts = contexts_by_index.get(index, set())
    if contexts:
        return contexts
    if index not in identities.index:
        return set()
    return _identity_contexts_from_row(identities.loc[index])


def _identity_indices_have_context_compatibility(
    identities: pd.DataFrame,
    left_index: int,
    right_index: int,
    contexts_by_index: dict[int, set[tuple[str, str, str]]],
) -> bool:
    left_contexts = _identity_contexts_for_index(identities, left_index, contexts_by_index)
    right_contexts = _identity_contexts_for_index(identities, right_index, contexts_by_index)
    if not left_contexts or not right_contexts:
        return False
    if left_contexts & right_contexts:
        return True
    return _contexts_are_same_team_competition(left_contexts, right_contexts)


def _identity_source_id_pairs(row: pd.Series) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for source_key in _identity_source_keys(row):
        if ":" not in source_key:
            continue
        source, raw_id = source_key.split(":", 1)
        if source in ID_BACKED_SOURCES and raw_id and ":" not in raw_id:
            pairs.add((source, raw_id))
    return pairs


def _identity_indices_share_global_source_id(
    identities: pd.DataFrame,
    left_index: int,
    right_index: int,
) -> bool:
    if left_index not in identities.index or right_index not in identities.index:
        return False
    return bool(_identity_source_id_pairs(identities.loc[left_index]) & _identity_source_id_pairs(identities.loc[right_index]))


def _identity_indices_source_keys(identities: pd.DataFrame, indices: list[int]) -> list[str]:
    source_keys = [
        source_key
        for _, row in identities.loc[indices].iterrows()
        for source_key in _identity_source_keys(row)
    ]
    return source_keys


def _can_merge_safe_identity_name_pair(
    identities: pd.DataFrame,
    left_index: int,
    right_index: int,
    contexts_by_index: dict[int, set[tuple[str, str, str]]],
) -> bool:
    source_keys = _identity_indices_source_keys(identities, [left_index, right_index])
    if _would_create_source_id_conflict([], source_keys):
        return False
    if not any(_identity_has_id_backed_source(identities.loc[index]) for index in [left_index, right_index]):
        return False
    if _identity_indices_share_global_source_id(identities, left_index, right_index):
        return True
    return _identity_indices_have_context_compatibility(identities, left_index, right_index, contexts_by_index)


def _merge_safe_identity_name_matches(
    identities: pd.DataFrame,
    alias_rows: pd.DataFrame,
    *,
    method: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if identities.empty:
        return identities, alias_rows, 0
    groups_by_slug: dict[str, set[int]] = defaultdict(set)
    for index, row in identities.iterrows():
        for name in _identity_alias_names(row):
            slug = _strong_identity_name_slug(name)
            if slug:
                groups_by_slug[slug].add(int(index))

    contexts_by_index = _identity_contexts_from_alias_rows(alias_rows)
    identity_keys = [str(index) for index in range(len(identities))]
    dsu = DisjointSet(identity_keys)
    for indices in groups_by_slug.values():
        group_indices = sorted(indices)
        if len(group_indices) < 2:
            continue
        for left_pos, left_index in enumerate(group_indices):
            for right_index in group_indices[left_pos + 1:]:
                left_root = dsu.find(str(left_index))
                right_root = dsu.find(str(right_index))
                if left_root == right_root:
                    continue
                left_group = [int(index) for index in identity_keys if dsu.find(index) == left_root]
                right_group = [int(index) for index in identity_keys if dsu.find(index) == right_root]
                combined_keys = _identity_indices_source_keys(identities, left_group + right_group)
                if _would_create_source_id_conflict([], combined_keys):
                    continue
                if not _can_merge_safe_identity_name_pair(identities, left_index, right_index, contexts_by_index):
                    continue
                dsu.union(left_root, right_root)

    root_groups: dict[str, list[int]] = defaultdict(list)
    for index in range(len(identities)):
        root_groups[dsu.find(str(index))].append(index)

    old_to_new: dict[int, int] = {}
    new_rows: list[dict[str, object]] = []
    merged_count = 0
    processed: set[int] = set()
    for old_index in range(len(identities)):
        if old_index in processed:
            continue
        group_indices = sorted(root_groups[dsu.find(str(old_index))])
        new_index = len(new_rows)
        if len(group_indices) > 1:
            group_rows = identities.loc[group_indices]
            new_rows.append(_merge_identity_group_rows(group_rows, method))
            merged_count += len(group_indices) - 1
        else:
            new_rows.append(identities.loc[old_index].to_dict())
        for index in group_indices:
            old_to_new[index] = new_index
            processed.add(index)

    if merged_count == 0:
        return identities, alias_rows, 0
    merged_identities = pd.DataFrame(new_rows, columns=IDENTITY_COLUMNS)
    if not alias_rows.empty and "_identity_index" in alias_rows.columns:
        merged_alias_rows = alias_rows.copy()
        merged_alias_rows["_identity_index"] = merged_alias_rows["_identity_index"].astype(int).map(old_to_new)
    else:
        merged_alias_rows = alias_rows
    return merged_identities, merged_alias_rows, merged_count


def _identity_duplicate_player_id_name_slug(row: pd.Series) -> str:
    full_name = str(row.get("full_name", "") or "").strip()
    if has_expanded_full_name(full_name):
        return normalize_alias(full_name)
    return normalize_alias(row.get("known_as", ""))


def _can_merge_duplicate_player_id_pair(
    identities: pd.DataFrame,
    left_index: int,
    right_index: int,
    contexts_by_index: dict[int, set[tuple[str, str, str]]],
) -> bool:
    if left_index not in identities.index or right_index not in identities.index:
        return False
    left_row = identities.loc[left_index]
    right_row = identities.loc[right_index]
    left_player_id = _preliminary_player_id_for_row(left_row)
    right_player_id = _preliminary_player_id_for_row(right_row)
    if not left_player_id or left_player_id != right_player_id:
        return False
    if _would_create_source_id_conflict([], _identity_indices_source_keys(identities, [left_index, right_index])):
        return False
    left_name_slug = _identity_duplicate_player_id_name_slug(left_row)
    right_name_slug = _identity_duplicate_player_id_name_slug(right_row)
    if not left_name_slug or left_name_slug != right_name_slug:
        return False
    if _identity_indices_share_global_source_id(identities, left_index, right_index):
        return True
    if not _identity_indices_have_context_compatibility(identities, left_index, right_index, contexts_by_index):
        return False
    left_full_name = str(left_row.get("full_name", "") or "").strip()
    right_full_name = str(right_row.get("full_name", "") or "").strip()
    if (
        has_expanded_full_name(left_full_name)
        and has_expanded_full_name(right_full_name)
        and normalize_alias(left_full_name) == normalize_alias(right_full_name)
    ):
        return True
    left_known_as = normalize_alias(left_row.get("known_as", ""))
    right_known_as = normalize_alias(right_row.get("known_as", ""))
    return bool(left_known_as and left_known_as == right_known_as)


def _merge_duplicate_player_id_identities(
    identities: pd.DataFrame,
    alias_rows: pd.DataFrame,
    *,
    method: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if identities.empty:
        return identities, alias_rows, 0
    preliminary_ids = identities.apply(_preliminary_player_id_for_row, axis=1)
    groups_by_id: dict[str, set[int]] = defaultdict(set)
    for index, player_id in preliminary_ids.items():
        player_id_text = str(player_id or "").strip()
        if player_id_text:
            groups_by_id[player_id_text].add(int(index))

    contexts_by_index = _identity_contexts_from_alias_rows(alias_rows)
    identity_keys = [str(index) for index in range(len(identities))]
    dsu = DisjointSet(identity_keys)
    for indices in groups_by_id.values():
        group_indices = sorted(indices)
        if len(group_indices) < 2:
            continue
        for left_pos, left_index in enumerate(group_indices):
            for right_index in group_indices[left_pos + 1:]:
                left_root = dsu.find(str(left_index))
                right_root = dsu.find(str(right_index))
                if left_root == right_root:
                    continue
                left_group = [int(index) for index in identity_keys if dsu.find(index) == left_root]
                right_group = [int(index) for index in identity_keys if dsu.find(index) == right_root]
                combined_keys = _identity_indices_source_keys(identities, left_group + right_group)
                if _would_create_source_id_conflict([], combined_keys):
                    continue
                if not _can_merge_duplicate_player_id_pair(identities, left_index, right_index, contexts_by_index):
                    continue
                dsu.union(left_root, right_root)

    root_groups: dict[str, list[int]] = defaultdict(list)
    for index in range(len(identities)):
        root_groups[dsu.find(str(index))].append(index)

    old_to_new: dict[int, int] = {}
    new_rows: list[dict[str, object]] = []
    merged_count = 0
    processed: set[int] = set()
    for old_index in range(len(identities)):
        if old_index in processed:
            continue
        group_indices = sorted(root_groups[dsu.find(str(old_index))])
        new_index = len(new_rows)
        if len(group_indices) > 1:
            group_rows = identities.loc[group_indices]
            new_rows.append(_merge_identity_group_rows(group_rows, method))
            merged_count += len(group_indices) - 1
        else:
            new_rows.append(identities.loc[old_index].to_dict())
        for index in group_indices:
            old_to_new[index] = new_index
            processed.add(index)

    if merged_count == 0:
        return identities, alias_rows, 0
    merged_identities = pd.DataFrame(new_rows, columns=IDENTITY_COLUMNS)
    if not alias_rows.empty and "_identity_index" in alias_rows.columns:
        merged_alias_rows = alias_rows.copy()
        merged_alias_rows["_identity_index"] = merged_alias_rows["_identity_index"].astype(int).map(old_to_new)
    else:
        merged_alias_rows = alias_rows
    return merged_identities, merged_alias_rows, merged_count


def _identity_seasons_from_row(row: pd.Series) -> set[str]:
    return set(_split_joined_values(row.get("seasons", "")))


def _identity_seasons_are_disjoint(left_row: pd.Series, right_row: pd.Series) -> bool:
    left_seasons = _identity_seasons_from_row(left_row)
    right_seasons = _identity_seasons_from_row(right_row)
    return bool(left_seasons and right_seasons and not (left_seasons & right_seasons))


def _can_merge_safe_duplicate_full_name_transfer_pair(
    identities: pd.DataFrame,
    left_index: int,
    right_index: int,
    contexts_by_index: dict[int, set[tuple[str, str, str]]],
) -> bool:
    if left_index not in identities.index or right_index not in identities.index:
        return False
    left_row = identities.loc[left_index]
    right_row = identities.loc[right_index]
    left_full_name = str(left_row.get("full_name", "") or "").strip()
    right_full_name = str(right_row.get("full_name", "") or "").strip()
    if not has_expanded_full_name(left_full_name) or normalize_alias(left_full_name) != normalize_alias(right_full_name):
        return False
    left_known_as = normalize_alias(left_row.get("known_as", ""))
    right_known_as = normalize_alias(right_row.get("known_as", ""))
    if not left_known_as or left_known_as != right_known_as:
        return False
    source_keys = _identity_indices_source_keys(identities, [left_index, right_index])
    if _would_create_source_id_conflict([], source_keys):
        return False
    if _identity_indices_have_context_compatibility(identities, left_index, right_index, contexts_by_index):
        return True
    return _identity_seasons_are_disjoint(left_row, right_row)


def _merge_safe_duplicate_full_name_transfers(
    identities: pd.DataFrame,
    alias_rows: pd.DataFrame,
    *,
    method: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if identities.empty:
        return identities, alias_rows, 0

    groups_by_full_name: dict[str, set[int]] = defaultdict(set)
    for index, row in identities.iterrows():
        full_name_slug = normalize_alias(row.get("full_name", ""))
        known_as_slug = normalize_alias(row.get("known_as", ""))
        if full_name_slug and known_as_slug:
            groups_by_full_name[f"{full_name_slug}|{known_as_slug}"].add(int(index))

    contexts_by_index = _identity_contexts_from_alias_rows(alias_rows)
    identity_keys = [str(index) for index in range(len(identities))]
    dsu = DisjointSet(identity_keys)
    for indices in groups_by_full_name.values():
        group_indices = sorted(indices)
        if len(group_indices) < 2:
            continue
        for left_pos, left_index in enumerate(group_indices):
            for right_index in group_indices[left_pos + 1:]:
                left_root = dsu.find(str(left_index))
                right_root = dsu.find(str(right_index))
                if left_root == right_root:
                    continue
                left_group = [int(index) for index in identity_keys if dsu.find(index) == left_root]
                right_group = [int(index) for index in identity_keys if dsu.find(index) == right_root]
                combined_keys = _identity_indices_source_keys(identities, left_group + right_group)
                if _would_create_source_id_conflict([], combined_keys):
                    continue
                if not _can_merge_safe_duplicate_full_name_transfer_pair(
                    identities,
                    left_index,
                    right_index,
                    contexts_by_index,
                ):
                    continue
                dsu.union(left_root, right_root)

    root_groups: dict[str, list[int]] = defaultdict(list)
    for index in range(len(identities)):
        root_groups[dsu.find(str(index))].append(index)

    old_to_new: dict[int, int] = {}
    new_rows: list[dict[str, object]] = []
    merged_count = 0
    processed: set[int] = set()
    for old_index in range(len(identities)):
        if old_index in processed:
            continue
        group_indices = sorted(root_groups[dsu.find(str(old_index))])
        new_index = len(new_rows)
        if len(group_indices) > 1:
            group_rows = identities.loc[group_indices]
            new_rows.append(_merge_identity_group_rows(group_rows, method))
            merged_count += len(group_indices) - 1
        else:
            new_rows.append(identities.loc[old_index].to_dict())
        for index in group_indices:
            old_to_new[index] = new_index
            processed.add(index)

    if merged_count == 0:
        return identities, alias_rows, 0
    merged_identities = pd.DataFrame(new_rows, columns=IDENTITY_COLUMNS)
    if not alias_rows.empty and "_identity_index" in alias_rows.columns:
        merged_alias_rows = alias_rows.copy()
        merged_alias_rows["_identity_index"] = merged_alias_rows["_identity_index"].astype(int).map(old_to_new)
    else:
        merged_alias_rows = alias_rows
    return merged_identities, merged_alias_rows, merged_count


def _duplicate_repair_suffix_supported_by_known_as(row: pd.Series) -> bool:
    full_name = str(row.get("full_name", "") or "").strip()
    known_as = str(row.get("known_as", "") or "").strip()
    if not has_expanded_full_name(full_name) or not has_expanded_full_name(known_as):
        return False
    full_tokens = _name_tokens(full_name)
    known_tokens = _name_tokens(known_as)
    if not _tokens_are_ordered_subsequence(known_tokens, full_tokens):
        return False

    alias_token_sets = [
        set(_name_tokens(alias))
        for alias in _split_joined_values(row.get("aliases", ""))
        if has_expanded_full_name(alias)
    ]
    if not alias_token_sets:
        return False
    known_token_set = set(known_tokens)
    for full_token in full_tokens:
        if full_token not in known_token_set:
            continue
        if any(full_token not in alias_tokens for alias_tokens in alias_token_sets):
            return True
    return False


def _fallback_full_name_after_rejected_duplicate_repair(row: pd.Series) -> str:
    for candidate in [row.get("known_as", ""), *_split_joined_values(row.get("aliases", ""))]:
        text = str(candidate or "").strip()
        if has_expanded_full_name(text):
            return text
    return ""


def _revert_conflicting_duplicate_name_repairs(identities: pd.DataFrame) -> int:
    if identities.empty:
        return 0
    if "needs_review" in identities.columns:
        identities["needs_review"] = identities["needs_review"].astype(object)
    reverted = 0
    while True:
        slugs = identities["full_name"].fillna("").astype(str).map(normalize_alias)
        duplicate_slugs = [slug for slug, count in Counter(slugs[slugs.ne("")]).items() if count > 1]
        changed = False
        for slug in duplicate_slugs:
            group_indices = [int(index) for index, value in slugs.items() if value == slug]
            group_rows = identities.loc[group_indices]
            source_keys = [
                source_key
                for _, row in group_rows.iterrows()
                for source_key in _identity_source_keys(row)
            ]
            if not _would_create_source_id_conflict([], source_keys):
                continue
            for index in group_indices:
                method = str(identities.at[index, "resolution_method"] or "")
                if "gemini_name_enrichment_duplicate_full_name_disambiguation" not in method:
                    continue
                if "gemini_name_rejected_duplicate_collision" in method:
                    continue
                row = identities.loc[index]
                if _duplicate_repair_suffix_supported_by_known_as(row):
                    continue
                fallback_full_name = _fallback_full_name_after_rejected_duplicate_repair(row)
                identities.at[index, "full_name"] = fallback_full_name
                identities.at[index, "needs_review"] = str(not has_expanded_full_name(fallback_full_name)).lower()
                identities.at[index, "resolution_method"] = _append_identity_method(
                    method,
                    "gemini_name_rejected_duplicate_collision",
                )
                reverted += 1
                changed = True
        if not changed:
            break
    return reverted


def _base_player_id_for_identity_row(row: pd.Series) -> str:
    full_name = str(row.get("full_name", "") or "").strip()
    if has_expanded_full_name(full_name):
        return _full_name_to_id(full_name)
    return _name_based_id_from_name(row.get("known_as", ""), row)


def _apply_duplicate_player_id_disambiguation(identities: pd.DataFrame) -> int:
    if identities.empty or "id_player" not in identities.columns:
        return 0
    changed = 0
    while True:
        duplicate_ids = [
            player_id
            for player_id, count in Counter(identities["id_player"].fillna("").astype(str)).items()
            if player_id and count > 1
        ]
        if not duplicate_ids:
            break
        changed_this_round = False
        for player_id in sorted(duplicate_ids):
            group_indices = [int(index) for index, value in identities["id_player"].items() if str(value) == player_id]
            group_rows = identities.loc[group_indices]
            known_ids = [
                _identity_name_to_id_base(row.get("known_as", "")) or "unknown_player"
                for _, row in group_rows.iterrows()
            ]
            if all(known_ids) and len(set(known_ids)) == len(known_ids):
                for index, known_id in zip(group_indices, known_ids):
                    previous_id = str(identities.at[index, "id_player"])
                    identities.at[index, "id_player"] = known_id
                    identities.at[index, "resolution_method"] = _append_identity_method(
                        identities.at[index, "resolution_method"],
                        "automatic_known_as_homonym_id",
                    )
                    identities.at[index, "needs_review"] = "false"
                    if previous_id != known_id:
                        changed += 1
                    changed_this_round = True
                continue

            contextual_ids: list[str] = []
            for _, row in group_rows.iterrows():
                base_id = _base_player_id_for_identity_row(row)
                suffix = _contextual_homonym_suffix_from_values(row.get("teams", ""), row.get("competitions", ""))
                contextual_ids.append(f"{base_id}_{suffix}" if base_id and suffix else "")
            if all(contextual_ids) and len(set(contextual_ids)) == len(contextual_ids):
                for index, contextual_id in zip(group_indices, contextual_ids):
                    previous_id = str(identities.at[index, "id_player"])
                    identities.at[index, "id_player"] = contextual_id
                    identities.at[index, "resolution_method"] = _append_identity_method(
                        identities.at[index, "resolution_method"],
                        "automatic_contextual_homonym_id",
                    )
                    identities.at[index, "needs_review"] = "false"
                    if previous_id != contextual_id:
                        changed += 1
                    changed_this_round = True
        if not changed_this_round:
            break
    return changed


def _finalize_ids_from_full_name(identities: pd.DataFrame, review_rows: list[dict[str, object]]) -> tuple[list[str], int]:
    errors: list[str] = []
    for index, row in identities.iterrows():
        full_name = str(row.get("full_name", "") or "").strip()
        if has_expanded_full_name(full_name):
            identities.at[index, "id_player"] = _full_name_to_id(full_name)
            continue
        identities.at[index, "id_player"] = _name_based_id_from_name(row.get("known_as", ""), row)
        identities.at[index, "needs_review"] = "false"
        identities.at[index, "resolution_method"] = _append_identity_method(
            identities.at[index, "resolution_method"],
            "automatic_name_based_id_without_verified_full_name",
        )
    automatic_homonym_id_disambiguations = _apply_duplicate_player_id_disambiguation(identities)
    missing = identities["id_player"].astype(str).str.strip().eq("")
    if missing.any():
        for _, row in identities.loc[missing].iterrows():
            _append_review(
                review_rows,
                reason="missing_stable_player_id",
                row=pd.Series(
                    {
                        "source_player_key": row.get("source_player_keys", ""),
                        "observed_name": row.get("known_as", ""),
                        "normalized_alias": normalize_alias(row.get("known_as", "")),
                        "id_competition": row.get("competitions", ""),
                        "id_season": row.get("seasons", ""),
                        "id_team": row.get("teams", ""),
                    }
                ),
                suggested_action="No se pudo derivar un id_player estable desde full_name, known_as ni source_player_keys.",
            )
        errors.append("Hay jugadores sin id_player estable.")

    for player_id, group in identities.groupby("id_player", dropna=False):
        base_id = str(player_id or "").strip()
        if not base_id or len(group) < 2:
            continue
        examples = group[["known_as", "full_name", "source_player_keys"]].head(30).to_dict(orient="records")
        for _, row in group.iterrows():
            _append_review(
                review_rows,
                reason="duplicate_player_id",
                row=pd.Series(
                    {
                        "source_player_key": row.get("source_player_keys", ""),
                        "observed_name": row.get("known_as", ""),
                        "normalized_alias": normalize_alias(row.get("full_name", "")),
                        "id_competition": row.get("competitions", ""),
                        "id_season": row.get("seasons", ""),
                        "id_team": row.get("teams", ""),
                    }
                ),
                suggested_action=(
                    "Resolver con fusion automatica o enriquecer full_name; no se permiten sufijos de ID de fuente en id_player."
                ),
            )
        errors.append(f"Hay id_player duplicados que requieren fusion o full_name desambiguado. Ejemplos: {examples}")

    duplicated_ids = identities["id_player"].duplicated(keep=False)
    if duplicated_ids.any():
        examples = identities.loc[duplicated_ids, ["id_player", "known_as", "full_name", "source_player_keys"]].head(30).to_dict(orient="records")
        errors.append(f"Hay id_player duplicados despues de aplicar la politica sin sufijos de fuente. Ejemplos: {examples}")
    return errors, automatic_homonym_id_disambiguations


def _identity_rows_from_clusters(
    source_keys: list[str],
    rows_by_key: dict[str, pd.DataFrame],
    dsu: DisjointSet,
    key_methods: dict[str, set[str]],
    key_confidences: dict[str, float],
    key_name_hints: dict[str, IdentityNameHint],
    previous_name_by_source_key: dict[str, dict[str, str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    identity_rows: list[dict[str, object]] = []
    alias_rows: list[dict[str, object]] = []
    for _, keys in sorted(_current_root_groups(source_keys, dsu).items(), key=lambda item: sorted(item[1])[0]):
        cluster_rows = _rows_for_keys(keys, rows_by_key)
        aliases = _names_for_keys(keys, rows_by_key)
        hinted_known_names = [
            hint.known_as
            for key in keys
            for hint in [key_name_hints.get(key)]
            if hint is not None and hint.known_as
        ]
        hinted_full_names = [
            hint.full_name
            for key in keys
            for hint in [key_name_hints.get(key)]
            if hint is not None and hint.full_name
        ]
        previous_known = _first_non_empty(previous_name_by_source_key.get(key, {}).get("known_as", "") for key in keys)
        previous_full = _first_non_empty(previous_name_by_source_key.get(key, {}).get("full_name", "") for key in keys)
        known_as = _choose_known_as([*aliases, *hinted_known_names], previous_known)
        full_name = _choose_full_name([*aliases, *hinted_full_names], previous_full)
        if not full_name and has_expanded_full_name(known_as):
            full_name = known_as
        methods = sorted({method for key in keys for method in key_methods.get(key, set())})
        if not methods:
            methods = ["source_id_singleton"] if any(key.startswith(("understat:", "whoscored:")) for key in keys) else ["source_context_singleton"]
        if not has_expanded_full_name(full_name):
            methods.append("needs_name_enrichment")
        confidence = min([key_confidences.get(key, 1.0) for key in keys] or [1.0])
        identity_rows.append(
            {
                "id_player": "",
                "known_as": known_as,
                "full_name": full_name,
                "aliases": _join_values(cluster_rows["observed_name"]),
                "source_player_keys": " | ".join(sorted(keys, key=_source_key_sort_key)),
                "id_understat": _source_ids_from_rows(cluster_rows, "understat"),
                "id_whoscored": _source_ids_from_rows(cluster_rows, "whoscored"),
                "competitions": _join_values(cluster_rows["id_competition"]),
                "seasons": _join_values(cluster_rows["id_season"]),
                "teams": _join_values(cluster_rows["id_team"]),
                "resolution_method": " | ".join(sorted(set(methods))),
                "confidence": f"{confidence:.3f}",
                "needs_review": str(not has_expanded_full_name(full_name)).lower(),
            }
        )
        identity_index = len(identity_rows) - 1
        for row in cluster_rows.itertuples(index=False):
            alias_rows.append(
                {
                    "source": row.source,
                    "source_player_key": row.source_player_key,
                    "source_player_id": row.source_player_id,
                    "observed_name": row.observed_name,
                    "normalized_alias": row.normalized_alias,
                    "id_competition": row.id_competition,
                    "id_season": row.id_season,
                    "id_team": row.id_team,
                    "sample_game": row.sample_game,
                    "observations": row.observations,
                    "_identity_index": identity_index,
                    "method": identity_rows[-1]["resolution_method"],
                    "confidence": identity_rows[-1]["confidence"],
                    "needs_review": identity_rows[-1]["needs_review"],
                }
            )
    return pd.DataFrame(identity_rows, columns=IDENTITY_COLUMNS), pd.DataFrame(alias_rows)


def _build_alias_map(alias_rows: pd.DataFrame, identities: pd.DataFrame) -> pd.DataFrame:
    if alias_rows.empty:
        return pd.DataFrame(columns=ALIAS_MAP_COLUMNS)
    rows: list[dict[str, object]] = []
    for _, row in alias_rows.iterrows():
        identity = identities.iloc[int(row["_identity_index"])]
        rows.append(
            {
                "source": row.get("source", ""),
                "source_player_key": row.get("source_player_key", ""),
                "source_player_id": row.get("source_player_id", ""),
                "observed_name": row.get("observed_name", ""),
                "normalized_alias": row.get("normalized_alias", ""),
                "id_competition": row.get("id_competition", ""),
                "id_season": row.get("id_season", ""),
                "id_team": row.get("id_team", ""),
                "sample_game": row.get("sample_game", ""),
                "observations": row.get("observations", ""),
                "id_player": identity["id_player"],
                "known_as": identity["known_as"],
                "full_name": identity["full_name"],
                "method": identity["resolution_method"],
                "confidence": identity["confidence"],
                "needs_review": identity["needs_review"],
            }
        )
    return pd.DataFrame(rows, columns=ALIAS_MAP_COLUMNS)


def _min_confidence(values) -> str:
    confidences: list[float] = []
    for value in values:
        text = _log_value(value)
        if not text:
            continue
        try:
            confidences.append(float(text))
        except ValueError:
            continue
    return f"{min(confidences):.3f}" if confidences else ""


def _any_true(values) -> str:
    return str(any(_log_value(value).lower() == "true" for value in values)).lower()


def _sum_observations(values) -> int:
    total = 0
    for value in values:
        text = _log_value(value)
        if not text:
            continue
        try:
            total += int(float(text))
        except ValueError:
            continue
    return total


def _consolidate_alias_map(alias_map: pd.DataFrame) -> pd.DataFrame:
    if alias_map.empty:
        return pd.DataFrame(columns=ALIAS_MAP_COLUMNS)

    player_counts = alias_map.groupby("source_player_key", dropna=False)["id_player"].nunique()
    conflicts = player_counts[player_counts > 1]
    if not conflicts.empty:
        examples = (
            alias_map[alias_map["source_player_key"].isin(conflicts.index)]
            .sort_values(["source_player_key", "id_player"])
            [["source_player_key", "id_player", "known_as", "full_name"]]
            .head(20)
            .to_dict(orient="records")
        )
        raise ValueError(f"Hay source_player_key asignados a varios jugadores: {examples}")

    rows: list[dict[str, object]] = []
    for source_key, group in alias_map.groupby("source_player_key", dropna=False, sort=False):
        rows.append(
            {
                "source": _first_non_empty(group["source"]),
                "source_player_key": source_key,
                "source_player_id": _first_non_empty(group["source_player_id"]),
                "observed_name": _join_values(group["observed_name"]),
                "normalized_alias": _join_values(group["normalized_alias"]),
                "id_competition": _join_values(group["id_competition"]),
                "id_season": _join_values(group["id_season"]),
                "id_team": _join_values(group["id_team"]),
                "sample_game": _first_non_empty(group["sample_game"]),
                "observations": _sum_observations(group["observations"]),
                "id_player": _first_non_empty(group["id_player"]),
                "known_as": _first_non_empty(group["known_as"]),
                "full_name": _first_non_empty(group["full_name"]),
                "method": _join_values(group["method"]),
                "confidence": _min_confidence(group["confidence"]),
                "needs_review": _any_true(group["needs_review"]),
            }
        )
    return pd.DataFrame(rows, columns=ALIAS_MAP_COLUMNS)


def normalize_players() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    global GEMINI_LIVE_CALL_ATTEMPTS, GEMINI_MODEL_LAST_REQUEST_AT, GEMINI_MISSING_API_KEY_WARNED, GEMINI_CALL_BUDGET_WARNED
    global GEMINI_CASE_HEADER_PRINTED
    GEMINI_LIVE_CALL_ATTEMPTS = 0
    GEMINI_MODEL_LAST_REQUEST_AT = {}
    GEMINI_MISSING_API_KEY_WARNED = False
    GEMINI_CALL_BUDGET_WARNED = False
    GEMINI_CASE_HEADER_PRINTED = False

    NORMALIZATION_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZATION_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    _section("1. OBSERVACIONES DE JUGADORES")
    observations = collect_player_observations()
    ambiguous_single_aliases = configure_name_quality_from_observations(observations)
    source_keys = sorted(observations["source_player_key"].dropna().astype(str).unique().tolist(), key=_source_key_sort_key)
    _norm_log(f"Observaciones agregadas: {len(observations)}")
    _norm_log(f"Source keys unicos antes de normalizar: {len(source_keys)}")
    _norm_log(f"Aliases de un solo token ambiguos: {len(ambiguous_single_aliases)}")

    dsu = DisjointSet(source_keys)
    rows_by_key = {str(key): group.copy() for key, group in observations.groupby("source_player_key", dropna=False)}
    contexts_by_key = {key: _context_set(rows) for key, rows in rows_by_key.items()}
    key_methods: dict[str, set[str]] = defaultdict(set)
    key_confidences: dict[str, float] = defaultdict(lambda: 1.0)
    key_name_hints: dict[str, IdentityNameHint] = {}
    review_rows: list[dict[str, object]] = []

    previous_alias_map = load_previous_alias_map()
    previous_identities = load_previous_identities()
    previous_name_by_source_key = _build_previous_name_lookup(previous_alias_map, previous_identities)

    _section("2. FUSIONES DETERMINISTAS SIN GEMINI")
    deterministic_counts = _apply_deterministic_merges(observations, source_keys, rows_by_key, contexts_by_key, dsu, key_methods)
    deterministic_transfer_exact_alias_merges = _apply_transfer_exact_alias_merges(
        observations,
        source_keys,
        rows_by_key,
        contexts_by_key,
        dsu,
        key_methods,
    )
    if deterministic_transfer_exact_alias_merges:
        deterministic_counts["transfer_exact_alias"] = deterministic_transfer_exact_alias_merges
    deterministic_single_token_context_bridges = _apply_single_token_match_context_bridges(
        source_keys,
        rows_by_key,
        dsu,
        key_methods,
    )
    if deterministic_single_token_context_bridges:
        deterministic_counts["single_token_match_context_bridge"] = deterministic_single_token_context_bridges
    clusters_after_deterministic = len(_current_root_groups(source_keys, dsu))
    _norm_log(f"Clusters tras reglas deterministas: {clusters_after_deterministic}")
    for name, count in sorted(deterministic_counts.items()):
        _norm_log(f"  - {name}: {count}")

    gemini_cache = _load_gemini_cache()
    (
        identity_calls,
        identity_cache_hits,
        identity_rejected,
        gemini_identity_bridge_merges,
        deterministic_orthographic_variant_merges,
        identity_retry_calls,
        identity_skipped_weak_evidence,
    ) = _run_identity_gemini_pass(
        source_keys,
        rows_by_key,
        contexts_by_key,
        dsu,
        key_methods,
        key_confidences,
        key_name_hints,
        gemini_cache,
        review_rows,
    )

    _section("4. CONSTRUCCION INICIAL DE IDENTIDADES")
    identities, alias_rows = _identity_rows_from_clusters(
        source_keys,
        rows_by_key,
        dsu,
        key_methods,
        key_confidences,
        key_name_hints,
        previous_name_by_source_key,
    )
    _norm_log(f"Identidades iniciales: {len(identities)}")
    missing_full_name_count = int(identities["needs_review"].astype(str).str.lower().eq("true").sum())
    _norm_log(f"Identidades con full_name incompleto: {missing_full_name_count}")

    _section("5. FULL_NAME INCOMPLETO")
    _norm_log("No se llama a Gemini para completar apodos o full_name incompletos sin respaldo interno.")
    _norm_log("Se conservara known_as y se generara id_player desde normalize(known_as) cuando full_name no este verificado.")
    (
        name_calls,
        name_cache_hits,
        name_accepted,
        name_rejected_no_support,
        name_verification_calls,
        name_verification_cache_hits,
        name_verified_accepted,
        name_verification_rejected,
    ) = (0, 0, 0, 0, 0, 0, 0, 0)
    (
        retry_calls,
        retry_cache_hits,
        retry_name_accepted,
        retry_name_rejected_no_support,
        retry_verification_calls,
        retry_verification_cache_hits,
        retry_verified_accepted,
        retry_verification_rejected,
    ) = (0, 0, 0, 0, 0, 0, 0, 0)

    identities, alias_rows, duplicate_full_name_merges = _merge_duplicate_full_name_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_full_name_merge",
    )
    if duplicate_full_name_merges:
        _norm_log(f"Fusiones automaticas por full_name antes de reparar colisiones: {duplicate_full_name_merges}")

    (
        collision_calls,
        collision_cache_hits,
        collision_name_accepted,
        collision_name_rejected_no_support,
        collision_verification_calls,
        collision_verification_cache_hits,
        collision_verified_accepted,
        collision_verification_rejected,
    ) = _run_name_enrichment_pass(
        identities,
        gemini_cache,
        review_rows,
        title="6. FULL_NAME DUPLICADO - REPARACION CON GEMINI",
        reason="duplicate_full_name",
        retry=True,
        only_duplicate_full_names=True,
    )
    conflicting_duplicate_name_repairs_reverted = _revert_conflicting_duplicate_name_repairs(identities)
    if conflicting_duplicate_name_repairs_reverted:
        _norm_log(
            "Reparaciones de full_name revertidas por colision con IDs estables: "
            f"{conflicting_duplicate_name_repairs_reverted}"
        )
    identities, alias_rows, post_collision_duplicate_full_name_merges = _merge_duplicate_full_name_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_full_name_merge_after_repair",
    )
    if post_collision_duplicate_full_name_merges:
        _norm_log(f"Fusiones automaticas por full_name despues de reparar colisiones: {post_collision_duplicate_full_name_merges}")
    identities, alias_rows, post_collision_alias_name_merges = _merge_safe_identity_name_matches(
        identities,
        alias_rows,
        method="automatic_safe_identity_name_merge_after_repair",
    )
    if post_collision_alias_name_merges:
        _norm_log(f"Fusiones automaticas por alias/nombre despues de reparar colisiones: {post_collision_alias_name_merges}")
    identities, alias_rows, pre_duplicate_id_context_merges = _merge_duplicate_player_id_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_player_id_context_merge_before_repair",
    )
    if pre_duplicate_id_context_merges:
        _norm_log(
            "Fusiones automaticas por id_player repetido antes de llamar a Gemini: "
            f"{pre_duplicate_id_context_merges}"
        )

    (
        duplicate_id_calls,
        duplicate_id_cache_hits,
        duplicate_id_name_accepted,
        duplicate_id_name_rejected_no_support,
        duplicate_id_verification_calls,
        duplicate_id_verification_cache_hits,
        duplicate_id_verified_accepted,
        duplicate_id_verification_rejected,
    ) = _run_name_enrichment_pass(
        identities,
        gemini_cache,
        review_rows,
        title="7. ID_PLAYER DUPLICADO - REPARACION CON GEMINI",
        reason="duplicate_player_id",
        only_duplicate_player_ids=True,
    )
    identities, alias_rows, post_duplicate_id_full_name_merges = _merge_duplicate_full_name_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_full_name_merge_after_id_repair",
    )
    if post_duplicate_id_full_name_merges:
        _norm_log(
            "Fusiones automaticas por full_name despues de reparar id_player: "
            f"{post_duplicate_id_full_name_merges}"
        )
    identities, alias_rows, safe_duplicate_full_name_transfer_merges = _merge_safe_duplicate_full_name_transfers(
        identities,
        alias_rows,
        method="automatic_safe_duplicate_full_name_transfer_merge",
    )
    if safe_duplicate_full_name_transfer_merges:
        _norm_log(
            "Fusiones automaticas seguras por full_name/known_as sin conflicto de IDs: "
            f"{safe_duplicate_full_name_transfer_merges}"
        )
    identities, alias_rows, duplicate_player_id_context_merges = _merge_duplicate_player_id_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_player_id_context_merge",
    )
    if duplicate_player_id_context_merges:
        _norm_log(
            "Fusiones automaticas por id_player repetido con contexto compatible: "
            f"{duplicate_player_id_context_merges}"
        )

    _section("8. VALIDACION FINAL DE NOMBRES E IDS")
    final_errors, automatic_homonym_id_disambiguations = _finalize_ids_from_full_name(identities, review_rows)
    alias_map = _consolidate_alias_map(_build_alias_map(alias_rows, identities))
    review_queue = pd.DataFrame(review_rows, columns=REVIEW_COLUMNS).drop_duplicates(subset=["review_id"], keep="last")
    review_queue, review_rows_pruned_after_resolution = _prune_resolved_review_queue(review_queue, alias_map)

    identities = identities[IDENTITY_COLUMNS].sort_values(by=["known_as", "full_name", "id_player"]).reset_index(drop=True)
    alias_map = alias_map[ALIAS_MAP_COLUMNS].sort_values(
        by=["source", "id_competition", "id_season", "id_team", "normalized_alias", "source_player_key"]
    ).reset_index(drop=True)

    raw_events_rows = 0
    for path in _target_raw_paths("whoscored", "events", pattern="read_events_*.csv"):
        raw_events_rows += len(filter_target_seasons(pd.read_csv(path, usecols=["game_id", "season"])))
    compacted_cache_entries = _compact_gemini_cache()

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "players_before_source_keys": len(source_keys),
        "players_after": len(identities),
        "aliases_resolved": len(alias_map),
        "aliases_unresolved": int(alias_map["id_player"].astype(str).str.strip().eq("").sum()) if not alias_map.empty else 0,
        "pending_review": len(review_queue),
        "raw_whoscored_event_rows": raw_events_rows,
        "gemini_calls": (
            identity_calls
            + name_calls
            + retry_calls
            + collision_calls
            + duplicate_id_calls
            + name_verification_calls
            + retry_verification_calls
            + collision_verification_calls
            + duplicate_id_verification_calls
        ),
        "gemini_cache_hits": (
            identity_cache_hits
            + name_cache_hits
            + retry_cache_hits
            + collision_cache_hits
            + duplicate_id_cache_hits
            + name_verification_cache_hits
            + retry_verification_cache_hits
            + collision_verification_cache_hits
            + duplicate_id_verification_cache_hits
        ),
        "gemini_live_call_attempts": GEMINI_LIVE_CALL_ATTEMPTS,
        "gemini_cache_compacted_entries": compacted_cache_entries,
        "gemini_identity_rejected": identity_rejected,
        "gemini_identity_retry_calls": identity_retry_calls,
        "gemini_identity_bridge_full_name_merges": gemini_identity_bridge_merges,
        "gemini_identity_skipped_weak_evidence": identity_skipped_weak_evidence,
        "deterministic_orthographic_variant_merges": deterministic_orthographic_variant_merges,
        "deterministic_transfer_exact_alias_merges": deterministic_transfer_exact_alias_merges,
        "deterministic_single_token_match_context_bridges": deterministic_single_token_context_bridges,
        "missing_full_name_general_enrichment_skipped": missing_full_name_count,
        "name_enrichment_gemini_calls": name_calls,
        "name_enrichment_gemini_cache_hits": name_cache_hits,
        "name_enrichment_retry_calls": retry_calls,
        "name_enrichment_retry_cache_hits": retry_cache_hits,
        "name_enrichment_verification_calls": (
            name_verification_calls
            + retry_verification_calls
            + collision_verification_calls
            + duplicate_id_verification_calls
        ),
        "name_enrichment_verification_cache_hits": (
            name_verification_cache_hits
            + retry_verification_cache_hits
            + collision_verification_cache_hits
            + duplicate_id_verification_cache_hits
        ),
        "name_collision_repair_calls": collision_calls,
        "name_collision_repair_cache_hits": collision_cache_hits,
        "name_collision_repair_accepted": collision_name_accepted + collision_verified_accepted,
        "name_collision_repair_rejected": collision_name_rejected_no_support + collision_verification_rejected,
        "name_collision_repair_reverted_duplicate_collision": conflicting_duplicate_name_repairs_reverted,
        "duplicate_player_id_repair_calls": duplicate_id_calls,
        "duplicate_player_id_repair_cache_hits": duplicate_id_cache_hits,
        "duplicate_player_id_repair_accepted": duplicate_id_name_accepted + duplicate_id_verified_accepted,
        "duplicate_player_id_repair_rejected": duplicate_id_name_rejected_no_support + duplicate_id_verification_rejected,
        "name_enrichment_accepted_internal_support": (
            name_accepted + retry_name_accepted + collision_name_accepted + duplicate_id_name_accepted
        ),
        "name_enrichment_accepted_model_verification": (
            name_verified_accepted
            + retry_verified_accepted
            + collision_verified_accepted
            + duplicate_id_verified_accepted
        ),
        "name_enrichment_rejected_no_internal_support": (
            name_rejected_no_support
            + retry_name_rejected_no_support
            + collision_name_rejected_no_support
            + duplicate_id_name_rejected_no_support
        ),
        "name_enrichment_rejected_after_model_verification": (
            name_verification_rejected
            + retry_verification_rejected
            + collision_verification_rejected
            + duplicate_id_verification_rejected
        ),
        "automatic_duplicate_full_name_merges": duplicate_full_name_merges,
        "automatic_duplicate_full_name_merges_after_repair": post_collision_duplicate_full_name_merges,
        "automatic_safe_identity_name_merges_after_repair": post_collision_alias_name_merges,
        "automatic_duplicate_player_id_context_merges_before_repair": pre_duplicate_id_context_merges,
        "automatic_duplicate_full_name_merges_after_id_repair": post_duplicate_id_full_name_merges,
        "automatic_safe_duplicate_full_name_transfer_merges": safe_duplicate_full_name_transfer_merges,
        "automatic_duplicate_player_id_context_merges": duplicate_player_id_context_merges,
        "automatic_homonym_id_disambiguations": automatic_homonym_id_disambiguations,
        "review_rows_pruned_after_resolution": review_rows_pruned_after_resolution,
        "ambiguous_single_aliases": sorted(ambiguous_single_aliases),
        "ambiguous_single_aliases_count": len(ambiguous_single_aliases),
        "methods": identities["resolution_method"].value_counts().to_dict(),
        "id_policy": (
            "id_player = normalize(full_name) when full_name is verified; otherwise normalize(known_as). "
            "If that collides between distinct homonyms, use a deterministic name/context homonym id. "
            "Source IDs are not allowed in id_player."
        ),
    }

    _norm_log(f"Identidades finales: {len(identities)}")
    _norm_log(f"Aliases/contextos mapeados: {len(alias_map)}")
    _norm_log(f"Casos en revision: {len(review_queue)}")
    _norm_log(f"Llamadas Gemini en directo contabilizadas: {report['gemini_calls']}")
    _norm_log(f"Respuestas Gemini desde cache: {report['gemini_cache_hits']}")
    if final_errors:
        report["final_errors"] = final_errors
        review_queue.to_csv(PLAYER_REVIEW_QUEUE_PATH, index=False, encoding="utf-8")
        with PLAYER_REPORT_PATH.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        for error in final_errors:
            _norm_log(f"ERROR FINAL: {error}")
        raise ValueError("La normalizacion de jugadores no cumple la politica final. Revisa player_review_queue.csv.")

    identities.to_csv(PLAYER_IDENTITIES_PATH, index=False, encoding="utf-8")
    alias_map.to_csv(PLAYER_ALIAS_MAP_PATH, index=False, encoding="utf-8")
    review_queue.to_csv(PLAYER_REVIEW_QUEUE_PATH, index=False, encoding="utf-8")
    with PLAYER_REPORT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return identities, alias_map, review_queue, report


def load_source_key_to_player_id() -> dict[str, str]:
    if not PLAYER_ALIAS_MAP_PATH.exists():
        raise FileNotFoundError(f"No existe {PLAYER_ALIAS_MAP_PATH}. Ejecuta primero normalize_players.py.")
    df = pd.read_csv(PLAYER_ALIAS_MAP_PATH, dtype="string").fillna("")
    lookup: dict[str, str] = {}
    for row in df.itertuples(index=False):
        source_key = str(getattr(row, "source_player_key", "")).strip()
        player_id = str(getattr(row, "id_player", "")).strip()
        if source_key and player_id:
            lookup[source_key] = player_id
    return lookup


def map_player_ids(
    df: pd.DataFrame,
    *,
    source: str,
    player_col: str,
    source_player_id_col: str | None = None,
    team_col: str | None = None,
    competition_col: str | None = None,
    season_col: str | None = None,
) -> pd.Series:
    lookup = load_source_key_to_player_id()
    source_keys = build_source_player_keys_for_frame(
        df,
        source=source,
        player_col=player_col,
        source_player_id_col=source_player_id_col,
        team_col=team_col,
        competition_col=competition_col,
        season_col=season_col,
    )
    mapped = source_keys.map(lookup).astype("string")
    missing_mask = source_keys.notna() & mapped.isna()
    if missing_mask.any():
        missing = source_keys.loc[missing_mask].drop_duplicates().head(10).tolist()
        raise ValueError("Hay jugadores sin mapping canonico. Ejecuta normalize_players.py. " f"Ejemplos: {missing}")
    return mapped


def map_source_ids_to_player_ids(source: str, source_ids: pd.Series) -> pd.Series:
    lookup = load_source_key_to_player_id()
    parsed = source_ids.apply(parse_source_id).astype("string")
    keys = source + ":" + parsed.astype(str)
    keys = keys.where(parsed.notna(), pd.NA)
    mapped = keys.map(lookup).astype("string")
    missing_mask = keys.notna() & mapped.isna()
    if missing_mask.any():
        missing = keys.loc[missing_mask].drop_duplicates().head(10).tolist()
        raise ValueError("Hay source_player_id relacionados sin mapping canonico. " f"Ejemplos: {missing}")
    return mapped

def main() -> None:
    parse_no_args("Utilidades compartidas de normalizacion de jugadores. Ejecuta normalize_players.py para generar artefactos.")


if __name__ == "__main__":
    main()

