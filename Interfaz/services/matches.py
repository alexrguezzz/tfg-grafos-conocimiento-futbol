from __future__ import annotations

from datetime import datetime as dt_datetime

from services.utils import parse_datetime, safe_int


def split_match_name(match_name: str) -> tuple[str, str]:
    home, separator, away = str(match_name or "").partition(" vs ")
    if separator:
        return home.strip(), away.strip()
    return str(match_name or "").strip(), "-"


def get_filtered_matches(*, run_query, prefixes: str, match_scope_clauses, text_filter, filters: dict[str, object], q: str = "") -> list[dict[str, str]]:
    rows = run_query(
        prefixes
        + f"""
        SELECT ?matchUri ?date ?dateTime ?week ?matchName ?hs ?as ?venue ?stadiumCity ?stadiumCountry ?stadiumLatitude ?stadiumLongitude ?attendance ?temperature ?humidity ?precipitation ?rain ?windSpeed ?weatherDateTime
        WHERE {{
            ?m a class:Match .
            BIND(STR(?m) AS ?matchUri)
            OPTIONAL {{ ?m prop:date ?dateRaw . }}
            OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
            OPTIONAL {{ ?m prop:dateTime ?dateTimeRaw . }}
            OPTIONAL {{ ?m prop:matchDateTime ?legacyDateTime . }}
            OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
            OPTIONAL {{ ?m prop:week ?legacyWeek . }}
            OPTIONAL {{ ?m prop:name ?nameRaw . }}
            OPTIONAL {{ ?m prop:matchName ?legacyName . }}
            OPTIONAL {{ ?m rdfs:label ?matchLabel . }}
            OPTIONAL {{ ?m prop:homeScore ?hs . }}
            OPTIONAL {{ ?m prop:awayScore ?as . }}
            OPTIONAL {{ ?m prop:venue ?venueRaw . }}
            OPTIONAL {{
                {{ ?m prop:playedAt ?stadium . }}
                UNION
                {{ ?m prop:playedAtStadium ?stadium . }}
                OPTIONAL {{ ?stadium rdfs:label ?stadiumLabel . }}
                OPTIONAL {{ ?stadium prop:name ?stadiumName . }}
                OPTIONAL {{ ?stadium prop:city ?stadiumCity . }}
                OPTIONAL {{ ?stadium prop:country ?stadiumCountry . }}
                OPTIONAL {{ ?stadium prop:latitude ?stadiumLatitude . }}
                OPTIONAL {{ ?stadium prop:longitude ?stadiumLongitude . }}
                BIND(REPLACE(STR(?stadium), "^.*/", "") AS ?stadiumLocal)
            }}
            OPTIONAL {{
                ?m prop:hasWeatherObservation ?weather .
                OPTIONAL {{ ?weather prop:dateTime ?weatherDateTime . }}
                OPTIONAL {{ ?weather prop:temperature ?temperature . }}
                OPTIONAL {{ ?weather prop:humidity ?humidity . }}
                OPTIONAL {{ ?weather prop:precipitation ?precipitation . }}
                OPTIONAL {{ ?weather prop:rain ?rain . }}
                OPTIONAL {{ ?weather prop:windSpeed ?windSpeed . }}
            }}
            OPTIONAL {{ ?m prop:attendance ?attendance . }}
            BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
            BIND(COALESCE(?dateTimeRaw, ?legacyDateTime) AS ?dateTime)
            BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
            BIND(COALESCE(?nameRaw, ?legacyName, ?matchLabel, REPLACE(STR(?m), "^.*/", "")) AS ?matchName)
            BIND(COALESCE(?stadiumLabel, ?stadiumName, ?venueRaw, ?stadiumLocal) AS ?venue)
            FILTER(BOUND(?date))

            {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
            {text_filter(q, '?matchName')}
            BIND(IF(BOUND(?week), xsd:integer(?week), 999) AS ?weekSort)
        }}
        ORDER BY ?weekSort COALESCE(?dateTime, ?date) ?matchName
        """
    )

    by_match_uri: dict[str, dict[str, str]] = {}
    for row in rows:
        match_uri = row.get("matchUri", "").strip()
        if not match_uri:
            continue
        home_label, away_label = split_match_name(row.get("matchName", ""))
        row["homeLabel"] = home_label
        row["awayLabel"] = away_label
        if match_uri not in by_match_uri:
            by_match_uri[match_uri] = row
            continue

        current = by_match_uri[match_uri]
        if (not current.get("hs") and row.get("hs")) or (not current.get("as") and row.get("as")):
            by_match_uri[match_uri] = row

    rows = list(by_match_uri.values())

    def sort_key(row: dict[str, str]) -> tuple[int, dt_datetime, str, str]:
        match_dt = parse_datetime(row.get("dateTime", ""))
        if match_dt is None:
            match_dt = parse_datetime(row.get("date", "")) or dt_datetime.max

        week = safe_int(row.get("week", "0"))
        return (
            week if week > 0 else 999,
            match_dt,
            row.get("homeLabel", ""),
            row.get("awayLabel", ""),
        )

    rows.sort(key=sort_key)
    return rows
