from __future__ import annotations

from pathlib import Path
import json
import sys
import urllib.parse
import urllib.request

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_audit, print_result  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402


CANONICAL_DIR = PROJECT_ROOT / "data" / "processed" / "canonical"
CONTEXT_DIR = PROJECT_ROOT / "data" / "processed" / "context"
CACHE_DIR = CONTEXT_DIR / "cache"
AUDIT_DIR = CONTEXT_DIR / "audit"
MATCHES_PATH = CANONICAL_DIR / "matches.csv"
STADIUMS_PATH = CANONICAL_DIR / "stadiums.csv"
OUTPUT_PATH = CANONICAL_DIR / "weather_observations.csv"
WEATHER_CACHE_PATH = CACHE_DIR / "open_meteo_cache.json"
MISSING_WEATHER_PATH = AUDIT_DIR / "missing_weather_observations.csv"

WEATHER_COLUMNS = [
    "id_weatherObservation",
    "id_match",
    "dateTime",
    "temperature",
    "precipitation",
    "rain",
    "windSpeed",
    "humidity",
]
MISSING_COLUMNS = ["id_match", "reason"]
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "TFG-SoccerData-Context/1.0 (educational project)"
MAX_WEATHER_API_ERRORS = 3


def load_json_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw_cache = json.load(handle)
    return {
        key: value
        for key, value in raw_cache.items()
        if isinstance(value, dict) and value.get("status") != "error"
    }


def save_json_cache(path: Path, cache: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)


def request_json(url: str, params: dict[str, object]) -> dict[str, object]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def first_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def parse_float(value: object) -> float | None:
    text = first_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_stadium_lookup(stadiums: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        first_text(row["id_stadium"]): row
        for _, row in stadiums.iterrows()
        if first_text(row.get("id_stadium"))
    }


def open_meteo_range(
    *,
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    cache: dict[str, dict[str, object]],
) -> dict[str, object]:
    cache_key = f"{latitude:.5f}|{longitude:.5f}|{start_date}|{end_date}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        payload = request_json(
            "https://archive-api.open-meteo.com/v1/archive",
            {
                "latitude": f"{latitude:.5f}",
                "longitude": f"{longitude:.5f}",
                "start_date": start_date,
                "end_date": end_date,
                "hourly": ",".join(
                    [
                        "temperature_2m",
                        "precipitation",
                        "rain",
                        "wind_speed_10m",
                        "relative_humidity_2m",
                    ]
                ),
                "timezone": "UTC",
            },
        )
        result = {"status": "ok", "payload": payload}
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}

    if result.get("status") != "error":
        cache[cache_key] = result
        save_json_cache(WEATHER_CACHE_PATH, cache)
    return result


def nearest_hour_observation(payload: dict[str, object], match_dt_utc: pd.Timestamp) -> dict[str, object]:
    hourly = payload.get("hourly", {})
    if not isinstance(hourly, dict):
        return {}
    times = hourly.get("time", [])
    if not times:
        return {}

    time_index = pd.to_datetime(pd.Series(times), errors="coerce", utc=True)
    if time_index.isna().all():
        return {}

    distances = (time_index - match_dt_utc).abs()
    idx = int(distances.idxmin())

    def value_at(key: str):
        values = hourly.get(key, [])
        if not isinstance(values, list) or idx >= len(values):
            return pd.NA
        return values[idx]

    return {
        "dateTime": time_index.iloc[idx].isoformat(),
        "temperature": value_at("temperature_2m"),
        "precipitation": value_at("precipitation"),
        "rain": value_at("rain"),
        "windSpeed": value_at("wind_speed_10m"),
        "humidity": value_at("relative_humidity_2m"),
    }


def build_weather_observations() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not MATCHES_PATH.exists():
        raise FileNotFoundError(f"No existe: {MATCHES_PATH}")
    if not STADIUMS_PATH.exists():
        raise FileNotFoundError(f"No existe: {STADIUMS_PATH}. Ejecuta primero build_stadiums.py.")

    matches = pd.read_csv(MATCHES_PATH, dtype="string")
    stadiums = pd.read_csv(STADIUMS_PATH, dtype="string")
    if "id_stadium" not in matches.columns:
        raise ValueError("matches.csv no contiene la columna tecnica id_stadium necesaria para enlazar estadios")

    stadium_lookup = build_stadium_lookup(stadiums)
    cache = load_json_cache(WEATHER_CACHE_PATH)
    rows: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    records_by_location_season: dict[tuple[float, float, str], list[dict[str, object]]] = {}

    for _, match in matches.iterrows():
        match_status = first_text(match.get("matchStatus")).lower()
        if match_status == "abandoned":
            continue

        match_id = first_text(match.get("id_match"))
        stadium_id = first_text(match.get("id_stadium"))
        stadium = stadium_lookup.get(stadium_id)
        if stadium is None:
            missing.append({"id_match": match_id, "reason": "stadium_not_found"})
            continue

        latitude = parse_float(stadium.get("latitude"))
        longitude = parse_float(stadium.get("longitude"))
        if latitude is None or longitude is None:
            missing.append({"id_match": match_id, "reason": "stadium_coordinates_missing"})
            continue

        match_datetime_text = first_text(match.get("dateTime")) or first_text(match.get("date"))
        match_dt = pd.to_datetime(match_datetime_text, errors="coerce", utc=True)
        if pd.isna(match_dt):
            missing.append({"id_match": match_id, "reason": "match_date_missing"})
            continue

        season_id = first_text(match.get("id_season")) or "unknown_season"
        key = (round(latitude, 5), round(longitude, 5), season_id)
        records_by_location_season.setdefault(key, []).append(
            {
                "id_match": match_id,
                "match_dt": match_dt,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    weather_api_errors = 0
    weather_api_unavailable_reason = ""
    for records in records_by_location_season.values():
        if weather_api_errors >= MAX_WEATHER_API_ERRORS:
            for record in records:
                missing.append(
                    {
                        "id_match": str(record["id_match"]),
                        "reason": weather_api_unavailable_reason or "weather_api_unavailable",
                    }
                )
            continue

        dates = [record["match_dt"].strftime("%Y-%m-%d") for record in records]
        latitude = float(records[0]["latitude"])
        longitude = float(records[0]["longitude"])
        weather = open_meteo_range(
            latitude=latitude,
            longitude=longitude,
            start_date=min(dates),
            end_date=max(dates),
            cache=cache,
        )
        if weather.get("status") != "ok":
            weather_api_errors += 1
            weather_api_unavailable_reason = first_text(weather.get("error")) or "weather_error"
            for record in records:
                missing.append({"id_match": str(record["id_match"]), "reason": weather_api_unavailable_reason})
            continue

        payload = weather.get("payload", {})
        if not isinstance(payload, dict):
            for record in records:
                missing.append({"id_match": str(record["id_match"]), "reason": "weather_payload_invalid"})
            continue

        for record in records:
            match_id = str(record["id_match"])
            match_dt = record["match_dt"]
            observation = nearest_hour_observation(payload, match_dt)
            if not observation:
                missing.append({"id_match": match_id, "reason": "weather_hour_missing"})
                continue

            rows.append(
                {
                    "id_weatherObservation": f"weather_{match_id}",
                    "id_match": match_id,
                    **observation,
                }
            )

    observations = pd.DataFrame(rows, columns=WEATHER_COLUMNS)
    missing_df = pd.DataFrame(missing, columns=MISSING_COLUMNS)
    return observations, missing_df


def run_build() -> dict:
    parse_no_args("Construye observaciones meteorologicas para partidos y estadios canonicos.")
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    print("Generando observaciones meteorologicas por partido...")
    observations, missing = build_weather_observations()
    observations.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    missing.to_csv(MISSING_WEATHER_PATH, index=False, encoding="utf-8")
    print_result("Observaciones meteorologicas", len(observations), OUTPUT_PATH)
    print_audit("Observaciones no resueltas", len(missing), MISSING_WEATHER_PATH)
    warnings = []
    if len(missing):
        warnings.append(f"{len(missing)} observacion(es) meteorologicas no resueltas")
    return {
        "input_files": [MATCHES_PATH, STADIUMS_PATH],
        "output_files": [OUTPUT_PATH, MISSING_WEATHER_PATH],
        "warnings": warnings,
        "metrics": {
            "weather_observations": len(observations),
            "missing_weather_observations": len(missing),
        },
    }


def main() -> None:
    run_with_optional_task_result("build_weather_observations", "transform", run_build)


if __name__ == "__main__":
    main()

