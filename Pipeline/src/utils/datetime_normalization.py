from __future__ import annotations

import re

import pandas as pd


ISO_DATE_PREFIX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")


def date_part(value) -> str | pd.NA:
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()
    if not text:
        return pd.NA

    match = ISO_DATE_PREFIX.match(text)
    if match:
        return match.group(1)

    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return pd.NA
    return parsed.strftime("%Y-%m-%d")


def date_part_series(values: pd.Series) -> pd.Series:
    text = values.astype("string")
    dates = text.str.extract(ISO_DATE_PREFIX.pattern, expand=False)

    missing = dates.isna()
    if missing.any():
        parsed = pd.to_datetime(values.loc[missing], errors="coerce", utc=True)
        dates.loc[missing] = parsed.dt.strftime("%Y-%m-%d")

    return dates
