from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, timedelta
import email.utils
import json
import math
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_audit, print_result  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402
from src.utils.text_normalization import clean_identifier_text  # noqa: E402


CANONICAL_DIR = PROJECT_ROOT / "data" / "processed" / "canonical"
CONTEXT_DIR = PROJECT_ROOT / "data" / "processed" / "context"
CACHE_DIR = CONTEXT_DIR / "cache"
AUDIT_DIR = CONTEXT_DIR / "audit"
MATCHES_PATH = CANONICAL_DIR / "matches.csv"
OUTPUT_PATH = CANONICAL_DIR / "stadiums.csv"
WIKIDATA_CACHE_PATH = CACHE_DIR / "wikidata_stadium_cache.json"
TEAM_VENUE_CACHE_PATH = CACHE_DIR / "wikidata_team_venue_cache.json"
NOMINATIM_CACHE_PATH = CACHE_DIR / "nominatim_stadium_cache.json"
UNRESOLVED_PATH = AUDIT_DIR / "unresolved_stadiums.csv"

STADIUM_COLUMNS = [
    "id_stadium",
    "name",
    "venue_name",
    "city",
    "country",
    "latitude",
    "longitude",
    "idWikidata",
    "idOsm",
]
UNRESOLVED_COLUMNS = ["name", "reason"]


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un numero") from exc
    if value < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}")
    return value


def env_int(name: str, default: int, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero") from exc
    if value < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}")
    return value


HTTP_TIMEOUT_SECONDS = env_float("STADIUM_HTTP_TIMEOUT_SECONDS", 8.0, minimum=1.0)
MAX_HTTP_ATTEMPTS = env_int("STADIUM_MAX_HTTP_ATTEMPTS", 3, minimum=1)
HTTP_RETRY_BASE_SECONDS = env_float("STADIUM_HTTP_RETRY_BASE_SECONDS", 10.0)
HTTP_RETRY_MAX_SECONDS = env_float("STADIUM_HTTP_RETRY_MAX_SECONDS", 60.0)
UNRESOLVED_RETRY_ROUNDS = env_int("STADIUM_UNRESOLVED_RETRY_ROUNDS", 2)
UNRESOLVED_RETRY_BASE_SECONDS = env_float("STADIUM_UNRESOLVED_RETRY_BASE_SECONDS", 60.0)
UNRESOLVED_RETRY_MAX_SECONDS = env_float("STADIUM_UNRESOLVED_RETRY_MAX_SECONDS", 120.0)
MAX_WIKIDATA_FALLBACK_CANDIDATES = env_int("STADIUM_MAX_WIKIDATA_FALLBACK_CANDIDATES", 3, minimum=1)
MAX_NOMINATIM_FALLBACK_CANDIDATES = env_int("STADIUM_MAX_NOMINATIM_FALLBACK_CANDIDATES", 2, minimum=1)
MAX_WIKIDATA_BATCH_CANDIDATES = env_int("STADIUM_MAX_WIKIDATA_BATCH_CANDIDATES", 1, minimum=1)
WIKIDATA_ENTITY_BATCH_SIZE = env_int("STADIUM_WIKIDATA_ENTITY_BATCH_SIZE", 50, minimum=1)
WIKIDATA_SEARCH_LIMIT = env_int("STADIUM_WIKIDATA_SEARCH_LIMIT", 5, minimum=1)
REMOTE_TIME_BUDGET_SECONDS = env_float("STADIUM_REMOTE_TIME_BUDGET_SECONDS", 900.0, minimum=1.0)
REMOTE_LOOKUP_ENABLED = os.getenv("STADIUM_ENABLE_REMOTE_LOOKUP", "1").strip().lower() not in {"0", "false", "no"}
WIKIDATA_BATCH_SEARCH_ENABLED = (
    REMOTE_LOOKUP_ENABLED
    and os.getenv("STADIUM_WIKIDATA_BATCH_SEARCH_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
)
USER_AGENT = "TFG-SoccerData-Context/1.0 (educational project)"
CACHE_VERSION = 2
NOMINATIM_CACHE_VERSION = 4
CACHE_NOT_FOUND_TTL_DAYS = 14
TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
STADIUM_COORDINATE_DUPLICATE_THRESHOLD_METERS = 200.0
SERVICE_MIN_INTERVAL_SECONDS = {
    "www.wikidata.org": env_float("STADIUM_WIKIDATA_API_INTERVAL_SECONDS", 1.2),
    "nominatim.openstreetmap.org": env_float("STADIUM_NOMINATIM_INTERVAL_SECONDS", 1.0),
}
WIKIDATA_SEARCH_LANGUAGES = ["en"]
WIKIDATA_LABEL_LANGUAGES = ["en", "es", "fr", "de", "it", "ca"]
LAST_REQUEST_AT_BY_HOST: dict[str, float] = {}
REMOTE_DEADLINE_AT: float | None = None
REQUEST_STATS: dict[str, float] = {
    "http_attempts": 0.0,
    "http_retries": 0.0,
    "retry_sleep_seconds": 0.0,
    "service_sleep_seconds": 0.0,
    "unresolved_retry_sleep_seconds": 0.0,
    "batch_wikidata_queries": 0.0,
    "batch_wikidata_records": 0.0,
    "batch_wikidata_hits": 0.0,
    "remote_time_budget_exceeded": 0.0,
}
def cache_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_cache_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def expected_cache_version(path: Path) -> int:
    return NOMINATIM_CACHE_VERSION if path == NOMINATIM_CACHE_PATH else CACHE_VERSION


def is_current_cache_entry(value: object, expected_version: int = CACHE_VERSION) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("status") == "error" or value.get("cache_version") != expected_version:
        return False
    if value.get("status") != "not_found":
        return True

    cached_at = parse_cache_timestamp(value.get("cached_at"))
    if cached_at is None:
        return False
    return datetime.now(timezone.utc) - cached_at <= timedelta(days=CACHE_NOT_FOUND_TTL_DAYS)


def load_json_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw_cache = json.load(handle)
    expected_version = expected_cache_version(path)
    return {
        key: value
        for key, value in raw_cache.items()
        if is_current_cache_entry(value, expected_version)
    }


def save_json_cache(path: Path, cache: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)


def add_request_stat(name: str, amount: float = 1.0) -> None:
    REQUEST_STATS[name] = REQUEST_STATS.get(name, 0.0) + amount


def start_remote_time_budget() -> None:
    global REMOTE_DEADLINE_AT
    REMOTE_DEADLINE_AT = time.monotonic() + REMOTE_TIME_BUDGET_SECONDS


def remote_time_remaining() -> float | None:
    if REMOTE_DEADLINE_AT is None:
        return None
    return REMOTE_DEADLINE_AT - time.monotonic()


def ensure_remote_time_budget() -> None:
    remaining = remote_time_remaining()
    if remaining is not None and remaining <= 0:
        add_request_stat("remote_time_budget_exceeded")
        raise TimeoutError("stadium remote lookup time budget exceeded")


def is_remote_time_budget_exceeded() -> bool:
    remaining = remote_time_remaining()
    return remaining is not None and remaining <= 0


def wait_for_service_slot(url: str) -> None:
    ensure_remote_time_budget()
    host = urllib.parse.urlparse(url).netloc.lower()
    min_interval = SERVICE_MIN_INTERVAL_SECONDS.get(host, 0)
    if min_interval <= 0:
        return

    now = time.monotonic()
    last_request_at = LAST_REQUEST_AT_BY_HOST.get(host)
    if last_request_at is not None:
        wait_seconds = last_request_at + min_interval - now
        if wait_seconds > 0:
            remaining = remote_time_remaining()
            if remaining is not None:
                wait_seconds = min(wait_seconds, max(0.0, remaining))
            add_request_stat("service_sleep_seconds", wait_seconds)
            time.sleep(wait_seconds)
            ensure_remote_time_budget()
    LAST_REQUEST_AT_BY_HOST[host] = time.monotonic()


def retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if not retry_after:
        return None
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def retry_wait_seconds(attempt: int, exc: Exception) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = retry_after_seconds(exc)
        if retry_after is not None:
            return min(retry_after, HTTP_RETRY_MAX_SECONDS)
    return min(HTTP_RETRY_BASE_SECONDS * (2**attempt), HTTP_RETRY_MAX_SECONDS)


def remote_error_reason(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "rate_limited"
        if exc.code in TRANSIENT_HTTP_STATUS_CODES:
            return f"transient_http_{exc.code}"
        return f"http_error_{exc.code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return f"urlopen_error: {exc.reason}"
    return str(exc)


def request_json(url: str, params: dict[str, object]) -> dict[str, object] | list[object]:
    query = urllib.parse.urlencode(params)
    data = None
    request_url = f"{url}?{query}"
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if len(request_url) > 1800:
        request_url = url
        data = query.encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(
        request_url,
        data=data,
        headers=headers,
    )
    last_error: Exception | None = None
    for attempt in range(MAX_HTTP_ATTEMPTS):
        ensure_remote_time_budget()
        wait_for_service_slot(url)
        try:
            add_request_stat("http_attempts")
            timeout = HTTP_TIMEOUT_SECONDS
            remaining = remote_time_remaining()
            if remaining is not None:
                timeout = max(1.0, min(timeout, remaining))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_STATUS_CODES or attempt >= MAX_HTTP_ATTEMPTS - 1:
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= MAX_HTTP_ATTEMPTS - 1:
                raise
        wait_seconds = retry_wait_seconds(attempt, last_error)
        remaining = remote_time_remaining()
        if remaining is not None:
            wait_seconds = min(wait_seconds, max(0.0, remaining))
        add_request_stat("http_retries")
        add_request_stat("retry_sleep_seconds", wait_seconds)
        time.sleep(wait_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("No se pudo completar la peticion HTTP")


def first_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "<na>", "none"} else text


def first_non_empty(values: list[str]) -> str:
    for value in values:
        text = first_text(value)
        if text:
            return text
    return ""


def parse_float(value: object) -> float | None:
    text = first_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def stadium_id_from_name(name: str) -> str:
    normalized = clean_identifier_text(name)
    return normalized or "unknown_stadium"


def stadium_query_candidates(name: str) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        text = first_text(value)
        lower_values = {candidate.lower() for candidate in candidates}
        if text and text.lower() not in lower_values:
            candidates.append(text)

    base_names: list[str] = []

    def add_base(value: str) -> None:
        text = first_text(value)
        lower_values = {base_name.lower() for base_name in base_names}
        if text and text.lower() not in lower_values:
            base_names.append(text)

    add_base(first_text(name))

    tokens = [token for token in first_text(name).replace("-", " ").split() if token]
    if len(tokens) >= 3:
        for start in range(1, len(tokens)):
            suffix_tokens = tokens[start:]
            if len(suffix_tokens) == 1 and len(suffix_tokens[0]) < 8:
                continue
            add_base(" ".join(suffix_tokens))

    for base_name in base_names:
        add(base_name)

    for base_name in base_names:
        lowered = base_name.strip().lower()
        if lowered and not lowered.startswith(("estadio", "estadi", "stadium", "el ")):
            add(f"Estadio {base_name}")
            add(f"{base_name} stadium")
            add(f"{base_name} football stadium")

    return candidates


def competition_country(competition_id: str) -> str:
    if competition_id == "ESP-La_Liga":
        return "Spain"
    if competition_id == "ENG-Premier_League":
        return "United Kingdom"
    if competition_id == "FRA-Ligue_1":
        return "France"
    if competition_id == "GER-Bundesliga":
        return "Germany"
    if competition_id == "ITA-Serie_A":
        return "Italy"
    return ""


def competition_country_code(competition_id: str) -> str:
    if competition_id == "ESP-La_Liga":
        return "es"
    if competition_id == "ENG-Premier_League":
        return "gb"
    if competition_id == "FRA-Ligue_1":
        return "fr"
    if competition_id == "GER-Bundesliga":
        return "de"
    if competition_id == "ITA-Serie_A":
        return "it"
    return ""


def venue_country_hints(matches: pd.DataFrame) -> dict[str, str]:
    hints: dict[str, str] = {}
    if not {"venue", "id_competition"}.issubset(matches.columns):
        return hints
    for venue, group in matches.groupby("venue", dropna=False):
        venue_name = first_text(venue)
        if not venue_name:
            continue
        countries = {
            competition_country(first_text(value))
            for value in group["id_competition"].dropna().astype(str)
        }
        countries = {country for country in countries if country}
        if len(countries) == 1:
            hints[venue_name.lower()] = next(iter(countries))
    return hints


def venue_country_code_hints(matches: pd.DataFrame) -> dict[str, str]:
    hints: dict[str, str] = {}
    if not {"venue", "id_competition"}.issubset(matches.columns):
        return hints
    for venue, group in matches.groupby("venue", dropna=False):
        venue_name = first_text(venue)
        if not venue_name:
            continue
        country_codes = {
            competition_country_code(first_text(value))
            for value in group["id_competition"].dropna().astype(str)
        }
        country_codes = {country_code for country_code in country_codes if country_code}
        if len(country_codes) == 1:
            hints[venue_name.lower()] = next(iter(country_codes))
    return hints


def venue_home_team_hints(matches: pd.DataFrame) -> dict[str, str]:
    hints: dict[str, str] = {}
    if not {"venue", "id_home_team"}.issubset(matches.columns):
        return hints

    for venue, group in matches.groupby("venue", dropna=False):
        venue_name = first_text(venue)
        if not venue_name:
            continue
        teams = [
            first_text(value)
            for value in group["id_home_team"].dropna().astype(str)
            if first_text(value)
        ]
        if teams:
            hints[venue_name.lower()] = most_common_text(teams)
    return hints


def normalize_country_text(value: object) -> str:
    text = first_text(value).lower()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(text.replace("_", " ").split())


def normalize_country(value: object) -> str:
    return normalize_country_text(value)


def normalize_country_code(value: object) -> str:
    return first_text(value).strip().lower()


def country_matches(
    actual: object,
    expected: object,
    actual_code: object = "",
    expected_code: object = "",
) -> bool:
    expected_country = normalize_country(expected)
    expected_country_code = normalize_country_code(expected_code)
    if not expected_country:
        return True
    actual_country_code = normalize_country_code(actual_code)
    if actual_country_code and expected_country_code:
        return actual_country_code == expected_country_code
    actual_country = normalize_country(actual)
    return not actual_country or actual_country == expected_country


def country_match_penalty(
    actual: object,
    expected: object,
    actual_code: object = "",
    expected_code: object = "",
) -> int:
    expected_country = normalize_country(expected)
    expected_country_code = normalize_country_code(expected_code)
    if not expected_country:
        return 0
    actual_country_code = normalize_country_code(actual_code)
    if actual_country_code and expected_country_code:
        return 0 if actual_country_code == expected_country_code else 2
    actual_country = normalize_country(actual)
    if actual_country == expected_country:
        return 0
    if not actual_country:
        return 1
    return 2


def wikidata_cache_key(name: str, country: str) -> str:
    base = name.strip().lower()
    country_text = normalize_country(country)
    return f"{base}|country={country_text}" if country_text else base


def stadium_name_keys_overlap(first: object, second: object) -> bool:
    first_keys = set(stadium_lookup_keys(first_text(first)))
    second_keys = set(stadium_lookup_keys(first_text(second)))
    return bool(first_keys and second_keys and first_keys & second_keys)


def wikidata_record_is_usable(
    name: str,
    country: str,
    record: dict[str, object],
) -> bool:
    if record.get("status") != "ok":
        return False
    if country_match_penalty(record.get("country"), country) == 2:
        return False
    return bool(record.get("is_stadium")) or stadium_name_keys_overlap(name, record.get("name"))


def wikidata_search_result_is_usable(name: str, item: dict[str, object]) -> bool:
    context = dict(item)
    context["matched_query"] = name
    return search_result_is_stadium(context) or stadium_name_keys_overlap(name, item.get("label"))


def wikidata_cached_result(
    name: str,
    country: str,
    cache: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    cache_key = wikidata_cache_key(name, country)
    if cache_key in cache:
        cached = cache[cache_key]
        if cached.get("status") == "ok" and not wikidata_record_is_usable(name, country, cached):
            return None
        return cached

    legacy_key = name.strip().lower()
    if legacy_key in cache and country_matches(cache[legacy_key].get("country"), country):
        cached = cache[legacy_key]
        if cached.get("status") == "ok" and not wikidata_record_is_usable(name, country, cached):
            return None
        return cached
    return None


def cache_wikidata_result(
    name: str,
    country: str,
    result: dict[str, object],
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    cache_record = dict(result)
    for transient_key in ["candidate_rank", "search_rank", "matched_query"]:
        cache_record.pop(transient_key, None)
    cache_record["cache_version"] = CACHE_VERSION
    cache_record["cached_at"] = cache_timestamp()
    cache[wikidata_cache_key(name, country)] = cache_record
    save_json_cache(WIKIDATA_CACHE_PATH, cache)
    return cache_record


def wikidata_search(name: str, country: str, cache: dict[str, dict[str, object]]) -> dict[str, object]:
    cached_result = wikidata_cached_result(name, country, cache)
    if cached_result is not None:
        return cached_result

    result: dict[str, object] = {"status": "not_found"}
    try:
        for language in ["en", "es"]:
            candidates = [
                item
                for item in wikidata_search_api(name, language)
                if wikidata_search_result_is_usable(name, item)
            ]
            qids = [
                str(item.get("id"))
                for item in candidates
                if isinstance(item, dict) and str(item.get("id", "")).startswith("Q")
            ]
            if not qids:
                continue
            result = wikidata_entity_details(qids, country)
            if result.get("status") == "ok":
                result_qid = first_text(result.get("id_wikidata"))
                result["is_stadium"] = any(
                    first_text(item.get("id")) == result_qid
                    and wikidata_search_result_is_usable(name, item)
                    and search_result_is_stadium({**item, "matched_query": name})
                    for item in candidates
                    if isinstance(item, dict)
                )
            if result.get("status") == "ok" and not wikidata_record_is_usable(name, country, result):
                result = {"status": "not_found"}
            if result.get("status") == "ok":
                break
    except Exception as exc:
        result = {"status": "error", "error": remote_error_reason(exc)}

    if result.get("status") != "error":
        result = cache_wikidata_result(name, country, result, cache)
    return result


def wikidata_entity_details(qids: list[str], expected_country: str = "") -> dict[str, object]:
    entities = wikidata_entities_api(qids)
    try:
        reference_labels = reference_label_lookup(entities)
    except Exception:
        reference_labels = {}
    candidate_records = [
        record
        for qid in qids
        if (entity := entities.get(qid))
        if (record := wikidata_api_record(qid, entity, reference_labels))
    ]
    if candidate_records:
        return sorted(
            candidate_records,
            key=lambda record: (
                0 if country_matches(record.get("country"), expected_country) else 1,
            ),
        )[0]
    return {"status": "not_found"}


def team_query_candidates(team_id: str) -> list[str]:
    team_name = first_text(team_id).replace("_", " ")
    candidates: list[str] = []

    def add(value: str) -> None:
        text = first_text(value)
        lower_values = {candidate.lower() for candidate in candidates}
        if text and text.lower() not in lower_values:
            candidates.append(text)

    add(team_name)
    lowered = team_name.lower()
    if lowered and not any(marker in lowered for marker in [" fc", " cf", " afc", " club"]):
        add(f"{team_name} football club")
        add(f"{team_name} soccer club")
    return candidates


def team_venue_cache_key(team_id: str, country: str) -> str:
    base = first_text(team_id).strip().lower()
    country_text = normalize_country(country)
    return f"{base}|country={country_text}" if country_text else base


def cache_team_venue_result(
    team_id: str,
    country: str,
    result: dict[str, object],
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    cache_record = dict(result)
    cache_record["cache_version"] = CACHE_VERSION
    cache_record["cached_at"] = cache_timestamp()
    cache[team_venue_cache_key(team_id, country)] = cache_record
    save_json_cache(TEAM_VENUE_CACHE_PATH, cache)
    return cache_record


def wikidata_team_home_venue(
    team_id: str,
    country: str,
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    if not first_text(team_id):
        return {"status": "not_found"}

    cached_result = cache.get(team_venue_cache_key(team_id, country))
    if cached_result is not None and cached_result.get("status") == "ok":
        return cached_result

    errors: list[str] = []
    search_languages = list(dict.fromkeys([*WIKIDATA_SEARCH_LANGUAGES, "es", "fr", "de", "it"]))

    for query in team_query_candidates(team_id):
        for language in search_languages:
            try:
                search_results = wikidata_search_api(query, language)
                team_qids = [
                    first_text(item.get("id"))
                    for item in search_results
                    if first_text(item.get("id")).startswith("Q")
                ]
                if not team_qids:
                    continue

                team_entities = wikidata_entities_api(team_qids)
                venue_qids: list[str] = []
                for team_qid in team_qids:
                    team_entity = team_entities.get(team_qid)
                    if not team_entity:
                        continue
                    for venue_qid in wikidata_claim_entity_ids(team_entity, "P115"):
                        if venue_qid not in venue_qids:
                            venue_qids.append(venue_qid)

                if not venue_qids:
                    continue

                venue_entities = wikidata_entities_api(venue_qids)
                try:
                    reference_labels = reference_label_lookup(venue_entities)
                except Exception:
                    reference_labels = {}
                venue_records = [
                    record
                    for venue_qid in venue_qids
                    if (entity := venue_entities.get(venue_qid))
                    if (record := wikidata_api_record(venue_qid, entity, reference_labels))
                ]
                if venue_records:
                    result = sorted(
                        venue_records,
                        key=lambda record: (
                            0 if country_matches(record.get("country"), country) else 1,
                        ),
                    )[0]
                    return cache_team_venue_result(team_id, country, result, cache)
            except Exception as exc:
                errors.append(remote_error_reason(exc))

    if errors:
        return {"status": "error", "error": first_non_empty(errors)}
    return {"status": "not_found"}


def stadium_lookup_keys(value: str) -> list[str]:
    base = clean_identifier_text(value).lower()
    if not base:
        return []

    keys = [base]

    def add(key: str) -> None:
        text = key.strip("_")
        if text and text not in keys:
            keys.append(text)

    for prefix in ["estadio_", "estadi_", "stadium_", "stade_", "stadio_"]:
        if base.startswith(prefix):
            add(base[len(prefix):])
    for suffix in ["_football_stadium", "_stadium"]:
        if base.endswith(suffix):
            add(base.removesuffix(suffix))
    return keys


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def wikidata_search_api(query: str, language: str) -> list[dict[str, object]]:
    payload = request_json(
        "https://www.wikidata.org/w/api.php",
        {
            "action": "wbsearchentities",
            "search": query,
            "language": language,
            "format": "json",
            "limit": WIKIDATA_SEARCH_LIMIT,
        },
    )
    if not isinstance(payload, dict):
        return []
    results = payload.get("search", [])
    return [item for item in results if isinstance(item, dict)]


def wikidata_entities_api(qids: list[str]) -> dict[str, dict[str, object]]:
    entities: dict[str, dict[str, object]] = {}
    for qid_chunk in chunked(qids, WIKIDATA_ENTITY_BATCH_SIZE):
        payload = request_json(
            "https://www.wikidata.org/w/api.php",
            {
                "action": "wbgetentities",
                "ids": "|".join(qid_chunk),
                "props": "labels|claims",
                "languages": "|".join(WIKIDATA_LABEL_LANGUAGES),
                "format": "json",
            },
        )
        if not isinstance(payload, dict):
            continue
        raw_entities = payload.get("entities", {})
        if not isinstance(raw_entities, dict):
            continue
        for qid, entity in raw_entities.items():
            if isinstance(entity, dict) and not entity.get("missing"):
                entities[str(qid)] = entity
    return entities


def wikidata_entity_label(entity: dict[str, object]) -> str:
    labels = entity.get("labels", {})
    if not isinstance(labels, dict):
        return ""
    for language in WIKIDATA_LABEL_LANGUAGES:
        label = labels.get(language)
        if isinstance(label, dict) and first_text(label.get("value")):
            return first_text(label.get("value"))
    for label in labels.values():
        if isinstance(label, dict) and first_text(label.get("value")):
            return first_text(label.get("value"))
    return ""


def wikidata_claim_values(entity: dict[str, object], property_id: str) -> list[object]:
    claims = entity.get("claims", {})
    if not isinstance(claims, dict):
        return []
    raw_claims = claims.get(property_id, [])
    if not isinstance(raw_claims, list):
        return []

    values: list[object] = []
    for claim in raw_claims:
        if not isinstance(claim, dict):
            continue
        mainsnak = claim.get("mainsnak", {})
        if not isinstance(mainsnak, dict):
            continue
        datavalue = mainsnak.get("datavalue", {})
        if not isinstance(datavalue, dict):
            continue
        value = datavalue.get("value")
        if value is not None:
            values.append(value)
    return values


def wikidata_claim_entity_ids(entity: dict[str, object], property_id: str) -> list[str]:
    ids: list[str] = []
    for value in wikidata_claim_values(entity, property_id):
        if not isinstance(value, dict):
            continue
        raw_id = value.get("id")
        if first_text(raw_id):
            ids.append(first_text(raw_id))
            continue
        numeric_id = value.get("numeric-id")
        if first_text(numeric_id):
            ids.append(f"Q{numeric_id}")
    return ids


def wikidata_claim_coordinate(entity: dict[str, object]) -> tuple[float | None, float | None]:
    for value in wikidata_claim_values(entity, "P625"):
        if not isinstance(value, dict):
            continue
        try:
            latitude = float(value.get("latitude"))
            longitude = float(value.get("longitude"))
        except (TypeError, ValueError):
            continue
        return latitude, longitude
    return None, None


def search_result_is_stadium(context: dict[str, object]) -> bool:
    text = " ".join(
        first_text(context.get(key)).lower()
        for key in ["label", "description", "matched_query"]
    )
    return any(
        marker in text
        for marker in [
            "stadium",
            "stade",
            "stadio",
            "estadio",
            "arena",
            "football ground",
            "sports venue",
        ]
    )


def wikidata_api_record(
    qid: str,
    entity: dict[str, object],
    reference_labels: dict[str, str],
) -> dict[str, object] | None:
    lat, lon = wikidata_claim_coordinate(entity)
    if lat is None or lon is None:
        return None

    city_qid = first_non_empty(wikidata_claim_entity_ids(entity, "P131"))
    country_qid = first_non_empty(wikidata_claim_entity_ids(entity, "P17"))
    return {
        "status": "ok",
        "cache_version": CACHE_VERSION,
        "id_wikidata": qid,
        "name": wikidata_entity_label(entity),
        "city": reference_labels.get(city_qid, ""),
        "country": reference_labels.get(country_qid, ""),
        "latitude": lat,
        "longitude": lon,
    }


def reference_label_lookup(entities: dict[str, dict[str, object]]) -> dict[str, str]:
    reference_qids: set[str] = set()
    for entity in entities.values():
        reference_qids.update(wikidata_claim_entity_ids(entity, "P131"))
        reference_qids.update(wikidata_claim_entity_ids(entity, "P17"))

    reference_entities = wikidata_entities_api(sorted(reference_qids)) if reference_qids else {}
    return {
        qid: wikidata_entity_label(entity)
        for qid, entity in reference_entities.items()
        if wikidata_entity_label(entity)
    }


def fetch_wikidata_label_index(
    venues: list[str],
    country_hints: dict[str, str],
    wikidata_cache: dict[str, dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    if not WIKIDATA_BATCH_SEARCH_ENABLED:
        return {}

    index: dict[str, list[dict[str, object]]] = {venue.lower(): [] for venue in venues}
    qid_contexts: dict[str, list[dict[str, object]]] = {}

    for venue in venues:
        if is_remote_time_budget_exceeded():
            break
        country = country_hints.get(venue.lower(), "")
        cached_result = wikidata_cached_result(venue, country, wikidata_cache)
        if cached_result is not None and cached_result.get("status") == "ok":
            continue
        candidates = stadium_query_candidates(venue)[:MAX_WIKIDATA_BATCH_CANDIDATES]
        for candidate_rank, candidate in enumerate(candidates):
            for language in WIKIDATA_SEARCH_LANGUAGES:
                try:
                    add_request_stat("batch_wikidata_queries")
                    search_results = wikidata_search_api(candidate, language)
                except Exception:
                    continue
                for search_rank, item in enumerate(search_results):
                    qid = first_text(item.get("id"))
                    if not qid.startswith("Q"):
                        continue
                    qid_contexts.setdefault(qid, []).append(
                        {
                            "venue": venue,
                            "candidate_rank": candidate_rank,
                            "search_rank": search_rank,
                            "matched_query": candidate,
                            "label": first_text(item.get("label")),
                            "description": first_text(item.get("description")),
                            "language": language,
                        }
                    )

    if not qid_contexts:
        return index

    try:
        entities = wikidata_entities_api(sorted(qid_contexts))
    except Exception:
        return index

    try:
        reference_labels = reference_label_lookup(entities)
    except Exception:
        reference_labels = {}

    for qid, contexts in qid_contexts.items():
        entity = entities.get(qid)
        if not entity:
            continue
        record = wikidata_api_record(qid, entity, reference_labels)
        if not record:
            continue
        add_request_stat("batch_wikidata_records")

        for context in contexts:
            venue_key = first_text(context.get("venue")).lower()
            indexed_record = dict(record)
            indexed_record["candidate_rank"] = int(context.get("candidate_rank", 99))
            indexed_record["search_rank"] = int(context.get("search_rank", 99))
            indexed_record["matched_query"] = first_text(context.get("matched_query"))
            indexed_record["is_stadium"] = search_result_is_stadium(context)
            if indexed_record not in index.get(venue_key, []):
                index.setdefault(venue_key, []).append(indexed_record)

    return index


def choose_wikidata_label_record(
    name: str,
    country: str,
    records: list[dict[str, object]],
) -> dict[str, object]:
    usable_records = [
        record
        for record in records
        if wikidata_record_is_usable(name, country, record)
    ]
    if not usable_records:
        return {"status": "not_found"}

    venue_keys = set(stadium_lookup_keys(name))

    def rank(record: dict[str, object]) -> tuple[int, int, int, int, int]:
        record_keys = set(stadium_lookup_keys(first_text(record.get("name"))))
        country_penalty = country_match_penalty(record.get("country"), country)
        stadium_penalty = 0 if record.get("is_stadium") else 1
        exact_penalty = 0 if venue_keys & record_keys else 1
        candidate_rank = int(record.get("candidate_rank", 99))
        search_rank = int(record.get("search_rank", 99))
        return (country_penalty, stadium_penalty, exact_penalty, candidate_rank, search_rank)

    chosen = sorted(usable_records, key=rank)[0]
    add_request_stat("batch_wikidata_hits")
    return dict(chosen)


def wikidata_label_batch_search(
    name: str,
    country: str,
    index: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    return choose_wikidata_label_record(name, country, index.get(name.lower(), []))


def nominatim_city_from_address(address: dict[str, object]) -> str:
    return first_non_empty(
        [
            address.get("city", ""),
            address.get("town", ""),
            address.get("municipality", ""),
            address.get("village", ""),
        ]
    )


def nominatim_result_from_item(
    item: dict[str, object],
    country: str,
    country_code: str = "",
) -> dict[str, object]:
    address = item.get("address", {})
    if not isinstance(address, dict):
        address = {}
    actual_country = address.get("country", "")
    actual_country_code = address.get("country_code", "")
    if not country_matches(actual_country, country, actual_country_code, country_code):
        return {"status": "not_found"}

    try:
        latitude = float(item["lat"])
        longitude = float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return {"status": "not_found"}

    return {
        "status": "ok",
        "cache_version": NOMINATIM_CACHE_VERSION,
        "id_osm": f"{item.get('osm_type', '')}/{item.get('osm_id', '')}".strip("/"),
        "name": item.get("name", ""),
        "city": nominatim_city_from_address(address),
        "country": country or actual_country,
        "latitude": latitude,
        "longitude": longitude,
    }


def nominatim_search(
    name: str,
    country: str,
    country_code: str,
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    query = f"{name}, {country}" if country else name
    cache_key = f"{query}|country_code={normalize_country_code(country_code)}".strip().lower()
    if cache_key in cache:
        return cache[cache_key]

    result: dict[str, object] = {"status": "not_found"}
    try:
        payload = request_json(
            "https://nominatim.openstreetmap.org/search",
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
            },
        )
        if isinstance(payload, list) and payload:
            item = payload[0]
            if isinstance(item, dict):
                result = nominatim_result_from_item(item, country, country_code)
    except Exception as exc:
        result = {"status": "error", "error": remote_error_reason(exc)}

    if result.get("status") != "error":
        result = dict(result)
        result["cache_version"] = NOMINATIM_CACHE_VERSION
        result["cached_at"] = cache_timestamp()
        cache[cache_key] = result
        save_json_cache(NOMINATIM_CACHE_PATH, cache)
    return result


def nominatim_reverse_search(
    latitude: float,
    longitude: float,
    country: str,
    country_code: str,
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    cache_key = f"reverse:{latitude:.6f},{longitude:.6f}|country_code={normalize_country_code(country_code)}"
    if cache_key in cache:
        return cache[cache_key]

    result: dict[str, object] = {"status": "not_found"}
    try:
        payload = request_json(
            "https://nominatim.openstreetmap.org/reverse",
            {
                "lat": f"{latitude:.6f}",
                "lon": f"{longitude:.6f}",
                "format": "jsonv2",
                "addressdetails": 1,
            },
        )
        if isinstance(payload, dict):
            result = nominatim_result_from_item(payload, country, country_code)
    except Exception as exc:
        result = {"status": "error", "error": remote_error_reason(exc)}

    if result.get("status") != "error":
        result = dict(result)
        result["cache_version"] = NOMINATIM_CACHE_VERSION
        result["cached_at"] = cache_timestamp()
        cache[cache_key] = result
        save_json_cache(NOMINATIM_CACHE_PATH, cache)
    return result


def merge_place_details(
    base: dict[str, object],
    details: dict[str, object],
) -> dict[str, object]:
    merged = dict(base)
    for key in ["name", "city", "country", "latitude", "longitude", "id_wikidata", "id_osm"]:
        if first_text(details.get(key)) and not first_text(merged.get(key)):
            merged[key] = details.get(key)
    return merged


def missing_required_place_fields(resolved: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for key in ["name", "city", "country"]:
        if not first_text(resolved.get(key)):
            missing.append(key)
    if parse_float(resolved.get("latitude")) is None:
        missing.append("latitude")
    if parse_float(resolved.get("longitude")) is None:
        missing.append("longitude")
    return missing


def place_requires_enrichment(resolved: dict[str, object]) -> bool:
    return any(key in missing_required_place_fields(resolved) for key in ["city", "country"])


def resolve_stadium(
    name: str,
    country: str,
    country_code: str,
    home_team_id: str,
    wikidata_cache: dict[str, dict[str, object]],
    team_venue_cache: dict[str, dict[str, object]],
    nominatim_cache: dict[str, dict[str, object]],
    wikidata_label_index: dict[str, list[dict[str, object]]],
) -> tuple[dict[str, object], str]:
    if not REMOTE_LOOKUP_ENABLED:
        return {}, "remote_lookup_disabled"
    if is_remote_time_budget_exceeded():
        add_request_stat("remote_time_budget_exceeded")
        return {}, "remote_time_budget_exceeded"

    errors: list[str] = []
    incomplete_results: list[tuple[dict[str, object], str]] = []

    def finalize_resolved(resolved: dict[str, object]) -> dict[str, object] | None:
        enriched = enrich_place_details(name, country, country_code, resolved, nominatim_cache)
        missing_fields = missing_required_place_fields(enriched)
        if not missing_fields:
            return enriched
        reason = f"missing_required_fields:{','.join(missing_fields)}"
        incomplete_results.append((enriched, reason))
        errors.append(reason)
        return None

    cached_wikidata = wikidata_cached_result(name, country, wikidata_cache)
    if cached_wikidata is not None and cached_wikidata.get("status") == "ok":
        finalized = finalize_resolved(cached_wikidata)
        if finalized is not None:
            return finalized, ""

    wikidata_label = wikidata_label_batch_search(name, country, wikidata_label_index)
    if wikidata_label.get("status") == "ok":
        cached_wikidata = cache_wikidata_result(name, country, wikidata_label, wikidata_cache)
        finalized = finalize_resolved(cached_wikidata)
        if finalized is not None:
            return finalized, ""

    candidates = stadium_query_candidates(name)

    for query in candidates[:MAX_WIKIDATA_FALLBACK_CANDIDATES]:
        wikidata = wikidata_search(query, country, wikidata_cache)
        if wikidata.get("status") == "ok":
            finalized = finalize_resolved(wikidata)
            if finalized is not None:
                return finalized, ""
        if wikidata.get("error"):
            errors.append(str(wikidata.get("error")))

    for query in candidates[:MAX_NOMINATIM_FALLBACK_CANDIDATES]:
        nominatim = nominatim_search(query, country, country_code, nominatim_cache)
        if nominatim.get("status") == "ok":
            finalized = finalize_resolved(nominatim)
            if finalized is not None:
                return finalized, ""
        if nominatim.get("error"):
            errors.append(str(nominatim.get("error")))

    team_venue = wikidata_team_home_venue(home_team_id, country, team_venue_cache)
    if team_venue.get("status") == "ok":
        finalized = finalize_resolved(team_venue)
        if finalized is not None:
            return finalized, ""
    if team_venue.get("error"):
        errors.append(f"team_venue_{team_venue.get('error')}")

    if incomplete_results:
        return incomplete_results[0]
    reason = first_non_empty([*errors, "not_found"])
    return {}, reason


def enrich_place_details(
    name: str,
    country: str,
    country_code: str,
    resolved: dict[str, object],
    nominatim_cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    enriched = dict(resolved)
    city = first_text(enriched.get("city"))
    if city.startswith("Q"):
        enriched["city"] = ""

    if place_requires_enrichment(enriched):
        for query_name in dict.fromkeys([name, first_text(enriched.get("name"))]):
            if not first_text(query_name):
                continue
            nominatim = nominatim_search(query_name, country, country_code, nominatim_cache)
            if nominatim.get("status") == "ok":
                enriched = merge_place_details(enriched, nominatim)
            if not place_requires_enrichment(enriched):
                break

    if place_requires_enrichment(enriched):
        coordinates = stadium_coordinates(enriched)
        if coordinates is not None:
            latitude, longitude = coordinates
            nominatim = nominatim_reverse_search(latitude, longitude, country, country_code, nominatim_cache)
            if nominatim.get("status") == "ok":
                enriched = merge_place_details(enriched, nominatim)

    if first_text(country) and not first_text(enriched.get("country")):
        enriched["country"] = country
    return enriched


def is_retryable_resolution_reason(reason: str) -> bool:
    text = reason.lower().strip()
    return bool(text and text != "remote_lookup_disabled")


def unresolved_retry_wait_seconds(retry_round: int) -> float:
    return min(UNRESOLVED_RETRY_BASE_SECONDS * (2**retry_round), UNRESOLVED_RETRY_MAX_SECONDS)


def stadium_external_identity(resolved: dict[str, object]) -> tuple[str, str] | None:
    wikidata_id = first_text(resolved.get("id_wikidata"))
    if wikidata_id:
        return "wikidata", wikidata_id

    osm_id = first_text(resolved.get("id_osm"))
    if osm_id:
        return "osm", osm_id
    return None


def stadium_coordinates(resolved: dict[str, object]) -> tuple[float, float] | None:
    latitude = parse_float(resolved.get("latitude"))
    longitude = parse_float(resolved.get("longitude"))
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def coordinate_distance_meters(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    first_lat, first_lon = first
    second_lat, second_lon = second
    earth_radius_meters = 6_371_000.0
    lat_delta = math.radians(second_lat - first_lat)
    lon_delta = math.radians(second_lon - first_lon)
    first_lat_rad = math.radians(first_lat)
    second_lat_rad = math.radians(second_lat)
    haversine = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(first_lat_rad) * math.cos(second_lat_rad) * math.sin(lon_delta / 2) ** 2
    )
    return 2 * earth_radius_meters * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def find_coordinate_group(
    groups: list[dict[str, object]],
    coordinates: tuple[float, float],
) -> int | None:
    for index, group in enumerate(groups):
        resolved_records = group.get("resolved_records", [])
        if not isinstance(resolved_records, list):
            continue
        for resolved in resolved_records:
            if not isinstance(resolved, dict):
                continue
            existing_coordinates = stadium_coordinates(resolved)
            if existing_coordinates is None:
                continue
            if coordinate_distance_meters(existing_coordinates, coordinates) <= STADIUM_COORDINATE_DUPLICATE_THRESHOLD_METERS:
                return index
    return None


def most_common_text(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = first_text(value)
        if text:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return sorted(counts, key=lambda value: (-counts[value], value.lower()))[0]


def most_common_venue(venues: list[str], venue_counts: dict[str, int]) -> str:
    return sorted(
        venues,
        key=lambda venue: (-venue_counts.get(venue, 0), venue.lower()),
    )[0]


def choose_group_name(group: dict[str, object], venue_counts: dict[str, int]) -> str:
    resolved_records = [
        record
        for record in group.get("resolved_records", [])
        if isinstance(record, dict)
    ]
    wikidata_names = [
        first_text(record.get("name"))
        for record in resolved_records
        if first_text(record.get("id_wikidata"))
        and first_text(record.get("name"))
        and not first_text(record.get("name")).startswith("Q")
    ]
    resolved_names = [
        first_text(record.get("name"))
        for record in resolved_records
        if first_text(record.get("name")) and not first_text(record.get("name")).startswith("Q")
    ]
    venues = [
        venue
        for venue in group.get("venues", [])
        if isinstance(venue, str) and first_text(venue)
    ]
    return (
        most_common_text(wikidata_names)
        or most_common_text(resolved_names)
        or most_common_venue(venues, venue_counts)
    )


def group_first_value(group: dict[str, object], key: str) -> object:
    resolved_records = group.get("resolved_records", [])
    if not isinstance(resolved_records, list):
        return pd.NA
    for resolved in resolved_records:
        if isinstance(resolved, dict) and first_text(resolved.get(key)):
            return resolved.get(key)
    return pd.NA


def unique_stadium_id(name: str, used_ids: set[str]) -> str:
    stadium_id = stadium_id_from_name(name)
    if stadium_id not in used_ids:
        used_ids.add(stadium_id)
        return stadium_id

    suffix = 2
    while f"{stadium_id}_{suffix}" in used_ids:
        suffix += 1
    stadium_id = f"{stadium_id}_{suffix}"
    used_ids.add(stadium_id)
    return stadium_id


def build_stadium_groups(
    venues: list[str],
    resolved_by_venue: dict[str, dict[str, object]],
    reason_by_venue: dict[str, str],
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    group_by_external_identity: dict[tuple[str, str], int] = {}

    for venue in venues:
        resolved = resolved_by_venue.get(venue, {})
        reason = reason_by_venue.get(venue, "")
        identity = stadium_external_identity(resolved)
        group_index = group_by_external_identity.get(identity) if identity else None

        if group_index is None:
            coordinates = stadium_coordinates(resolved)
            if coordinates is not None:
                group_index = find_coordinate_group(groups, coordinates)

        if group_index is None:
            group_index = len(groups)
            groups.append({"venues": [], "resolved_records": [], "unresolved": []})

        group = groups[group_index]
        group["venues"].append(venue)
        if resolved:
            group["resolved_records"].append(resolved)
        if reason:
            group["unresolved"].append((venue, reason))
        if identity:
            group_by_external_identity[identity] = group_index

    return groups


def build_grouped_stadium_rows(
    groups: list[dict[str, object]],
    venue_counts: dict[str, int],
) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, str]]:
    rows: list[dict[str, object]] = []
    unresolved_rows: list[dict[str, str]] = []
    venue_to_stadium_id: dict[str, str] = {}
    used_ids: set[str] = set()

    for group in groups:
        name = choose_group_name(group, venue_counts)
        stadium_id = unique_stadium_id(name, used_ids)
        venue_names = [
            venue
            for venue in sorted(
                group.get("venues", []),
                key=lambda value: (-venue_counts.get(str(value), 0), str(value).lower()),
            )
            if isinstance(venue, str) and first_text(venue)
        ]

        for venue in venue_names:
            venue_to_stadium_id[venue] = stadium_id

        row = {
            "id_stadium": stadium_id,
            "name": name,
            "venue_name": " | ".join(venue_names),
            "city": group_first_value(group, "city"),
            "country": group_first_value(group, "country"),
            "latitude": group_first_value(group, "latitude"),
            "longitude": group_first_value(group, "longitude"),
            "idWikidata": group_first_value(group, "id_wikidata"),
            "idOsm": group_first_value(group, "id_osm"),
        }
        rows.append(row)

        missing_fields = stadium_row_missing_required_fields(row)
        group_missing_reason = ""
        if missing_fields:
            group_missing_reason = f"missing_required_fields:{','.join(missing_fields)}"
            add_unresolved_row(unresolved_rows, first_text(row["name"]) or stadium_id, group_missing_reason)

        unresolved_values = group.get("unresolved", [])
        if isinstance(unresolved_values, list):
            for unresolved in unresolved_values:
                if not isinstance(unresolved, tuple) or len(unresolved) != 2:
                    continue
                venue, reason = unresolved
                if first_text(reason) == group_missing_reason:
                    continue
                add_unresolved_row(unresolved_rows, str(venue), str(reason))

    return rows, unresolved_rows, venue_to_stadium_id


def attach_stadium_ids_to_matches(
    matches: pd.DataFrame,
    venue_to_stadium_id: dict[str, str],
) -> pd.DataFrame:
    updated = matches.copy()
    id_stadium = updated["venue"].apply(
        lambda value: venue_to_stadium_id.get(first_text(value), pd.NA)
    )

    if "id_stadium" in updated.columns:
        updated = updated.drop(columns=["id_stadium"])

    venue_position = list(updated.columns).index("venue") + 1
    updated.insert(venue_position, "id_stadium", id_stadium)
    return updated


def stadium_row_missing_required_fields(row: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for column in ["name", "city", "country"]:
        if not first_text(row.get(column)):
            missing.append(column)
    for column in ["latitude", "longitude"]:
        if parse_float(row.get(column)) is None:
            missing.append(column)
    return missing


def add_unresolved_row(
    unresolved_rows: list[dict[str, str]],
    name: object,
    reason: object,
) -> None:
    row = {"name": first_text(name), "reason": first_text(reason)}
    if row["name"] and row["reason"] and row not in unresolved_rows:
        unresolved_rows.append(row)


def timing_config_metrics() -> dict[str, float | int]:
    return {
        "stadium_http_timeout_seconds": HTTP_TIMEOUT_SECONDS,
        "stadium_max_http_attempts": MAX_HTTP_ATTEMPTS,
        "stadium_http_retry_base_seconds": HTTP_RETRY_BASE_SECONDS,
        "stadium_http_retry_max_seconds": HTTP_RETRY_MAX_SECONDS,
        "stadium_unresolved_retry_rounds": UNRESOLVED_RETRY_ROUNDS,
        "stadium_unresolved_retry_base_seconds": UNRESOLVED_RETRY_BASE_SECONDS,
        "stadium_unresolved_retry_max_seconds": UNRESOLVED_RETRY_MAX_SECONDS,
        "stadium_max_wikidata_fallback_candidates": MAX_WIKIDATA_FALLBACK_CANDIDATES,
        "stadium_max_nominatim_fallback_candidates": MAX_NOMINATIM_FALLBACK_CANDIDATES,
        "stadium_max_wikidata_batch_candidates": MAX_WIKIDATA_BATCH_CANDIDATES,
        "stadium_wikidata_entity_batch_size": WIKIDATA_ENTITY_BATCH_SIZE,
        "stadium_remote_time_budget_seconds": REMOTE_TIME_BUDGET_SECONDS,
        "stadium_remote_lookup_enabled": int(REMOTE_LOOKUP_ENABLED),
        "stadium_wikidata_batch_search_enabled": int(WIKIDATA_BATCH_SEARCH_ENABLED),
        "stadium_wikidata_api_interval_seconds": SERVICE_MIN_INTERVAL_SECONDS["www.wikidata.org"],
        "stadium_nominatim_interval_seconds": SERVICE_MIN_INTERVAL_SECONDS["nominatim.openstreetmap.org"],
    }


def request_stats_metrics() -> dict[str, float | int]:
    return {
        "stadium_http_attempts": int(REQUEST_STATS["http_attempts"]),
        "stadium_http_retries": int(REQUEST_STATS["http_retries"]),
        "stadium_retry_sleep_seconds": round(REQUEST_STATS["retry_sleep_seconds"], 3),
        "stadium_service_sleep_seconds": round(REQUEST_STATS["service_sleep_seconds"], 3),
        "stadium_unresolved_retry_sleep_seconds": round(REQUEST_STATS["unresolved_retry_sleep_seconds"], 3),
        "stadium_batch_wikidata_queries": int(REQUEST_STATS["batch_wikidata_queries"]),
        "stadium_batch_wikidata_records": int(REQUEST_STATS["batch_wikidata_records"]),
        "stadium_batch_wikidata_hits": int(REQUEST_STATS["batch_wikidata_hits"]),
        "stadium_remote_time_budget_exceeded": int(REQUEST_STATS["remote_time_budget_exceeded"]),
    }


def resolve_venues(
    venues: list[str],
    country_hints: dict[str, str],
    country_code_hints: dict[str, str],
    home_team_hints: dict[str, str],
    wikidata_cache: dict[str, dict[str, object]],
    team_venue_cache: dict[str, dict[str, object]],
    nominatim_cache: dict[str, dict[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    resolved_by_venue: dict[str, dict[str, object]] = {}
    reason_by_venue: dict[str, str] = {}
    if REMOTE_LOOKUP_ENABLED:
        start_remote_time_budget()
    wikidata_label_index = (
        fetch_wikidata_label_index(venues, country_hints, wikidata_cache)
        if REMOTE_LOOKUP_ENABLED
        else {}
    )

    for venue in venues:
        country = country_hints.get(venue.lower(), "")
        country_code = country_code_hints.get(venue.lower(), "")
        home_team_id = home_team_hints.get(venue.lower(), "")
        resolved, reason = resolve_stadium(
            venue,
            country,
            country_code,
            home_team_id,
            wikidata_cache,
            team_venue_cache,
            nominatim_cache,
            wikidata_label_index,
        )
        resolved_by_venue[venue] = resolved
        reason_by_venue[venue] = reason

    for retry_round in range(UNRESOLVED_RETRY_ROUNDS):
        retry_venues = [
            venue
            for venue in venues
            if reason_by_venue.get(venue)
            and is_retryable_resolution_reason(reason_by_venue[venue])
        ]
        if not retry_venues:
            break

        wait_seconds = unresolved_retry_wait_seconds(retry_round)
        
        add_request_stat("unresolved_retry_sleep_seconds", wait_seconds)
        time.sleep(wait_seconds)
        if REMOTE_LOOKUP_ENABLED:
            start_remote_time_budget()

        for venue in retry_venues:
            country = country_hints.get(venue.lower(), "")
            country_code = country_code_hints.get(venue.lower(), "")
            home_team_id = home_team_hints.get(venue.lower(), "")
            resolved, reason = resolve_stadium(
                venue,
                country,
                country_code,
                home_team_id,
                wikidata_cache,
                team_venue_cache,
                nominatim_cache,
                wikidata_label_index,
            )
            resolved_by_venue[venue] = resolved
            reason_by_venue[venue] = reason

    return resolved_by_venue, reason_by_venue


def build_stadiums() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not MATCHES_PATH.exists():
        raise FileNotFoundError(f"No existe: {MATCHES_PATH}")

    matches = pd.read_csv(MATCHES_PATH, dtype="string")
    if "venue" not in matches.columns:
        raise ValueError("matches.csv no contiene la columna tecnica venue necesaria para resolver estadios")

    venue_counts: dict[str, int] = {}
    for value in matches["venue"].dropna().astype(str):
        venue = first_text(value)
        if venue:
            venue_counts[venue] = venue_counts.get(venue, 0) + 1

    venues = sorted(
        {
            first_text(value)
            for value in matches["venue"].dropna().astype(str)
            if first_text(value)
        }
    )
    wikidata_cache = load_json_cache(WIKIDATA_CACHE_PATH)
    team_venue_cache = load_json_cache(TEAM_VENUE_CACHE_PATH)
    nominatim_cache = load_json_cache(NOMINATIM_CACHE_PATH)
    country_hints = venue_country_hints(matches)
    country_code_hints = venue_country_code_hints(matches)
    home_team_hints = venue_home_team_hints(matches)
    resolved_by_venue, reason_by_venue = resolve_venues(
        venues,
        country_hints,
        country_code_hints,
        home_team_hints,
        wikidata_cache,
        team_venue_cache,
        nominatim_cache,
    )

    groups = build_stadium_groups(venues, resolved_by_venue, reason_by_venue)
    rows, unresolved_rows, venue_to_stadium_id = build_grouped_stadium_rows(groups, venue_counts)
    stadiums = pd.DataFrame(rows, columns=STADIUM_COLUMNS)
    unresolved = pd.DataFrame(unresolved_rows, columns=UNRESOLVED_COLUMNS)
    matches_with_stadium_ids = attach_stadium_ids_to_matches(matches, venue_to_stadium_id)
    return stadiums, unresolved, matches_with_stadium_ids


def run_build() -> dict:
    parse_no_args("Construye el contexto canonico de estadios desde partidos canonicos.")
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    print("Resolviendo estadios y contexto geografico...")
    stadiums, unresolved, matches = build_stadiums()
    matches.to_csv(MATCHES_PATH, index=False, encoding="utf-8")
    stadiums.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    unresolved.to_csv(UNRESOLVED_PATH, index=False, encoding="utf-8")
    print_result("Estadios", len(stadiums), OUTPUT_PATH)
    print_audit("Estadios no resueltos", len(unresolved), UNRESOLVED_PATH)
    warnings = []
    if len(unresolved):
        warnings.append(f"{len(unresolved)} estadio(s) no resueltos")
    return {
        "input_files": [MATCHES_PATH],
        "output_files": [MATCHES_PATH, OUTPUT_PATH, UNRESOLVED_PATH],
        "warnings": warnings,
        "metrics": {
            "stadiums": len(stadiums),
            "unresolved_stadiums": len(unresolved),
            **timing_config_metrics(),
            **request_stats_metrics(),
        },
    }


def main() -> None:
    run_with_optional_task_result("build_stadiums", "transform", run_build)


if __name__ == "__main__":
    main()
