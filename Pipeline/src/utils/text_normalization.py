from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import unicodedata


def clean_identifier_text(text: str, *, preserve_hyphen: bool = False) -> str:
    text = str(text).strip()

    for old, new in {
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "Ae",
        "œ": "oe",
        "Œ": "Oe",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
    }.items():
        text = text.replace(old, new)

    for old, new in {
        "ÃƒÂ¡": "a",
        "ÃƒÂ©": "e",
        "ÃƒÂ­": "i",
        "ÃƒÂ³": "o",
        "ÃƒÂº": "u",
        "ÃƒÂ¼": "u",
        "ÃƒÂ±": "n",
        "ÃƒÂ": "A",
        "Ãƒâ€°": "E",
        "ÃƒÂ": "I",
        "Ãƒâ€œ": "O",
        "ÃƒÅ¡": "U",
        "ÃƒÅ“": "U",
        "Ãƒâ€˜": "N",
    }.items():
        text = text.replace(old, new)

    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

    text = text.replace(" ", "_")
    if not preserve_hyphen:
        text = text.replace("-", "_")

    for ch in "/<>.,:;()'\"?Ã‚Â¿!Ã‚Â¡[]{}&#@+*=\\|":
        text = text.replace(ch, "")

    while "__" in text:
        text = text.replace("__", "_")

    return text.strip("_")


def _build_soccerdata_config_path() -> Path:
    soccerdata_base = Path(os.environ.get("SOCCERDATA_DIR", Path.home() / "soccerdata"))
    return soccerdata_base / "config" / "teamname_replacements.json"


def _normalize_team_lookup_key(text: str) -> str:
    return clean_identifier_text(text).lower()


@lru_cache(maxsize=1)
def _load_team_name_replacements() -> dict[str, str]:
    config_path = _build_soccerdata_config_path()
    if not config_path.exists():
        return {}

    with config_path.open(encoding="utf-8") as handle:
        raw_mapping = json.load(handle)

    lookup: dict[str, str] = {}
    for canonical_name, aliases in raw_mapping.items():
        candidates = [canonical_name, *(aliases or [])]
        for candidate in candidates:
            key = _normalize_team_lookup_key(candidate)
            if key:
                lookup[key] = canonical_name

    return lookup


def canonicalize_team_name(team_name: str) -> str:
    text = str(team_name).strip()
    if not text:
        return text

    lookup = _load_team_name_replacements()
    return lookup.get(_normalize_team_lookup_key(text), text)


def normalize_season(season_value: str) -> str:
    season_str = str(season_value).strip().replace("/", "-").replace("_", "-")

    if season_str.isdigit() and len(season_str) == 4:
        start = int("20" + season_str[:2])
        end = int("20" + season_str[2:])
        return f"{start}-{end}"

    return season_str


def denormalize_season_label(season_value: str) -> str:
    normalized = normalize_season(season_value)
    parts = normalized.split("-")
    if len(parts) == 2 and all(part.isdigit() and len(part) == 4 for part in parts):
        return f"{parts[0]}-{parts[1]}"
    return normalized


def normalize_competition(competition_value: str) -> str:
    return clean_identifier_text(competition_value, preserve_hyphen=True)


def competition_display_name(competition_value: str) -> str:
    text = str(competition_value or "").strip()
    if len(text) > 4 and text[:3].isalpha() and text[3] == "-":
        return text[4:].strip()
    return text


def normalize_team(team_name: str) -> str:
    return clean_identifier_text(canonicalize_team_name(team_name))
