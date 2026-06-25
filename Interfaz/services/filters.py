from __future__ import annotations

from urllib.parse import urlencode

from flask import request, url_for

from config import SECTIONS
from services.onboarding import normalized_label_match_clause
from services.utils import safe_int, sparql_string


def get_search() -> str:
    # El buscador global ahora solo navega a fichas de equipos o jugadores.
    # Las vistas no deben usar "q" como filtro textual de sus consultas.
    return ""


def get_filters() -> dict[str, object]:
    jornadas = [j for j in request.args.getlist("jornadas") if j and j != "all"]
    return {
        "competition": request.args.get("competition", "all").strip() or "all",
        "season": request.args.get("season", "all").strip() or "all",
        "jornadas": jornadas,
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
    }


def filters_to_params(filters: dict[str, object], q: str = "") -> dict[str, object]:
    params: dict[str, object] = {}
    for key in ("competition", "season", "date_from", "date_to"):
        value = str(filters.get(key, "")).strip()
        if value and value != "all":
            params[key] = value

    jornadas = filters.get("jornadas", [])
    if isinstance(jornadas, list) and jornadas:
        params["jornadas"] = jornadas

    if q:
        params["q"] = q

    return params


def build_url(path_name: str, filters: dict[str, object], q: str = "", extra: dict[str, str] | None = None) -> str:
    params = filters_to_params(filters, q)
    if extra:
        for key, value in extra.items():
            if value:
                params[key] = value
    return url_for(path_name) + ("?" + urlencode(params, doseq=True) if params else "")


def build_nav(current: str, filters: dict[str, object], q: str) -> list[dict[str, str | bool]]:
    return [
        {
            "label": section["label"],
            "href": build_url(section["path"], filters, q),
            "active": current == section["key"],
        }
        for section in SECTIONS
    ]


def text_filter(q: str, *fields: str) -> str:
    q = q.strip()
    if not q:
        return ""
    checks = [f"CONTAINS(LCASE(STR({field})), LCASE({sparql_string(q)}))" for field in fields]
    return "FILTER(" + " || ".join(checks) + ")"


def filter_clauses(
    filters: dict[str, object],
    *,
    competition_var: str | None = None,
    season_var: str | None = None,
    competition_label: str | None = None,
    season_label: str | None = None,
    week_field: str | None = None,
    date_field: str | None = None,
) -> str:
    clauses: list[str] = []

    competition = str(filters.get("competition", "all"))
    season = str(filters.get("season", "all"))
    jornadas = filters.get("jornadas", [])
    date_from = str(filters.get("date_from", "")).strip()
    date_to = str(filters.get("date_to", "")).strip()

    if competition != "all" and competition_label:
        competition_match = normalized_label_match_clause(competition_label, competition)
        if competition_match:
            clauses.append(f"FILTER({competition_match})")
    elif competition != "all" and competition_var:
        competition_match = normalized_label_match_clause(competition_var, competition)
        if competition_match:
            clauses.append(f"FILTER({competition_match})")

    if season != "all" and season_label:
        season_match = normalized_label_match_clause(season_label, season)
        if season_match:
            clauses.append(f"FILTER({season_match})")
    elif season != "all" and season_var:
        season_match = normalized_label_match_clause(season_var, season)
        if season_match:
            clauses.append(f"FILTER({season_match})")

    if week_field and isinstance(jornadas, list) and jornadas:
        nums = [str(safe_int(j)) for j in jornadas if safe_int(j) > 0]
        if nums:
            clauses.append(f"FILTER(xsd:integer({week_field}) IN ({', '.join(nums)}))")

    if date_field and date_from:
        clauses.append(f'FILTER(xsd:date({date_field}) >= "{date_from}"^^xsd:date)')
    if date_field and date_to:
        clauses.append(f'FILTER(xsd:date({date_field}) <= "{date_to}"^^xsd:date)')

    return "\n          ".join(clauses)


def match_scope_clauses(
    filters: dict[str, object],
    *,
    onboarding_match_clauses,
    match_var: str = "?m",
    date_var: str = "?date",
    week_var: str = "?week",
) -> str:
    clauses: list[str] = []

    competition = str(filters.get("competition", "all"))
    season = str(filters.get("season", "all"))

    onboarding_filters = onboarding_match_clauses(match_var=match_var)
    if onboarding_filters:
        clauses.append(onboarding_filters)

    if competition != "all":
        competition_match = normalized_label_match_clause("?_cmpText", competition)
        clauses.append(
            f"""
            {match_var} prop:belongsToCompetition ?_cmp .
            OPTIONAL {{ ?_cmp rdfs:label ?_cmpLabel . }}
            BIND(COALESCE(?_cmpLabel, STR(?_cmp)) AS ?_cmpText)
            FILTER({competition_match})
            """
        )

    if season != "all":
        season_match = normalized_label_match_clause("?_ssnText", season)
        clauses.append(
            f"""
            {match_var} prop:belongsToSeason ?_ssn .
            OPTIONAL {{ ?_ssn rdfs:label ?_ssnLabel . }}
            BIND(COALESCE(?_ssnLabel, STR(?_ssn)) AS ?_ssnText)
            FILTER({season_match})
            """
        )

    base_filters = filter_clauses(filters, week_field=week_var, date_field=date_var)
    if base_filters:
        clauses.append(base_filters)

    return "\n              ".join(clauses)
