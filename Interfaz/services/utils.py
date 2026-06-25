from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime as dt_datetime


def sparql_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sparql_iri(value: str) -> str:
    return "<" + value.replace(">", "%3E") + ">"


def safe_int(value: str) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def safe_bool(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes"}


def format_numeric_plain(value: object, empty: str = "-") -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return empty
    try:
        return format(Decimal(text), "f")
    except (InvalidOperation, ValueError):
        return text


def format_number(value: object, decimals: int = 0, suffix: str = "", missing: str = "-") -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return missing
    try:
        number = float(text)
    except Exception:
        return text
    if decimals == 0:
        return f"{int(round(number))}{suffix}"
    return f"{number:.{decimals}f}{suffix}"


def parse_datetime(value: str) -> dt_datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = dt_datetime.fromisoformat(normalized)
    except Exception:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = dt_datetime.strptime(text, fmt)
                break
            except Exception:
                continue

    if parsed is None and len(text) >= 10:
        try:
            parsed = dt_datetime.strptime(text[:10], "%Y-%m-%d")
        except Exception:
            return None

    if parsed is not None and parsed.tzinfo is not None:
        # GraphDB already stores match times normalized by the pipeline.
        # Keep the encoded wall-clock time and drop tzinfo only for sorting.
        parsed = parsed.replace(tzinfo=None)
    return parsed


def format_match_datetime(value_datetime: str, value_date: str = "") -> str:
    raw_dt = str(value_datetime or "").strip()
    raw_date = str(value_date or "").strip()
    raw_value = raw_dt or raw_date
    if not raw_value:
        return "-"

    parsed = parse_datetime(raw_value)
    if parsed is None:
        return raw_value

    has_time = bool(raw_dt and ":" in raw_dt)
    if has_time:
        return parsed.strftime("%d/%m/%Y %H:%M")
    return parsed.strftime("%d/%m/%Y")


def format_display_date(value: str) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return "-"

    parsed = parse_datetime(raw_value)
    if parsed is None:
        return raw_value
    return parsed.strftime("%d/%m/%Y")
