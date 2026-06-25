from __future__ import annotations

from urllib.parse import unquote

from flask import session

from config import KNOWN_LEAGUE_ASSETS
from services.utils import sparql_iri, sparql_string


def normalize_selection_value(value: str) -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ").lower()
    return " ".join(text.split())


def ensure_onboarding_boot_token(app_boot_token: str) -> None:
    current_token = str(session.get("onboarding_boot_token", ""))
    if current_token == app_boot_token:
        return

    session["onboarding_boot_token"] = app_boot_token
    clear_onboarding_selection()


def clear_onboarding_selection() -> None:
    for key in (
        "onboarding_competitions",
        "onboarding_competition_iris",
        "onboarding_seasons",
        "onboarding_season_iris",
    ):
        session.pop(key, None)


def session_values(key: str) -> list[str]:
    raw_values = session.get(key, [])
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    return [str(value).strip() for value in raw_values if str(value or "").strip()][:1]


def get_onboarding_state(app_boot_token: str) -> dict[str, list[str]]:
    ensure_onboarding_boot_token(app_boot_token)
    return {
        "competitions": session_values("onboarding_competitions"),
        "competition_iris": session_values("onboarding_competition_iris"),
        "seasons": session_values("onboarding_seasons"),
        "season_iris": session_values("onboarding_season_iris"),
    }


def onboarding_complete(app_boot_token: str) -> bool:
    state = get_onboarding_state(app_boot_token)
    return (
        bool(state["competitions"])
        and bool(state["competition_iris"])
        and bool(state["seasons"])
        and bool(state["season_iris"])
    )


def resource_id_from_iri(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    return unquote(text.rsplit("/", 1)[-1]) if text else ""


def display_label_from_resource_id(value: str) -> str:
    text = resource_id_from_iri(value)
    if len(text) > 4 and text[:3].isalpha() and text[3] in {"-", "_"}:
        text = text[4:]
    return text.replace("_", " ").strip()


def option_label(raw_label: str, iri: str) -> str:
    label = str(raw_label or "").strip()
    resource_id = resource_id_from_iri(iri)
    if not label:
        return display_label_from_resource_id(iri) or resource_id
    if normalize_selection_value(label) == normalize_selection_value(resource_id):
        return display_label_from_resource_id(iri) or label
    return label


def fallback_for_label(label: str) -> str:
    words = [word for word in str(label or "").replace("_", " ").replace("-", " ").split() if word]
    initials = "".join(word[0] for word in words).upper()
    return (initials[:3] or str(label or "?")[:2].upper() or "?")


def known_league_asset(label: str, iri: str) -> dict[str, str]:
    normalized_label = normalize_selection_value(label)
    resource_id = resource_id_from_iri(iri)

    for asset in KNOWN_LEAGUE_ASSETS:
        if normalize_selection_value(asset["label"]) == normalized_label or asset["resource_id"] == resource_id:
            return {
                "logo": asset.get("logo", ""),
                "fallback": asset.get("fallback", fallback_for_label(label)),
            }

    return {"logo": "", "fallback": fallback_for_label(label)}


def season_sort_key(label: str) -> tuple[int, int, str]:
    parts = str(label or "").replace("/", "-").split("-")
    years = [int(part) for part in parts if part.isdigit()]
    start = years[0] if years else 0
    end = years[1] if len(years) > 1 else start
    return (start, end, str(label or ""))


def available_onboarding_options(run_query, prefixes: str) -> dict[str, list[dict[str, object]]]:
    rows = run_query(
        prefixes
        + """
        SELECT DISTINCT ?competition ?competitionLabel ?season ?seasonLabel
        WHERE {
          ?item prop:belongsToCompetition ?competition ;
                prop:belongsToSeason ?season .
          OPTIONAL { ?competition rdfs:label ?competitionName . }
          OPTIONAL { ?season rdfs:label ?seasonName . }
          BIND(REPLACE(STR(?competition), "^.*/", "") AS ?competitionLocal)
          BIND(REPLACE(STR(?season), "^.*/", "") AS ?seasonLocal)
          BIND(COALESCE(?competitionName, ?competitionLocal) AS ?competitionLabel)
          BIND(COALESCE(?seasonName, ?seasonLocal) AS ?seasonLabel)
        }
        ORDER BY LCASE(STR(?competitionLabel)) DESC(STR(?seasonLabel))
        """,
        timeout=8,
    )

    competitions: dict[str, dict[str, object]] = {}
    seasons: dict[str, dict[str, object]] = {}
    competition_seasons: dict[str, set[str]] = {}
    season_competitions: dict[str, set[str]] = {}

    for row in rows:
        competition_iri = str(row.get("competition", "")).strip()
        season_iri = str(row.get("season", "")).strip()
        if not competition_iri or not season_iri:
            continue

        competition_label = option_label(row.get("competitionLabel", ""), competition_iri)
        season_label = option_label(row.get("seasonLabel", ""), season_iri)
        asset = known_league_asset(competition_label, competition_iri)

        competitions.setdefault(
            competition_iri,
            {
                "label": competition_label,
                "iri": competition_iri,
                "logo": asset["logo"],
                "fallback": asset["fallback"],
                "season_iris": [],
            },
        )
        seasons.setdefault(
            season_iri,
            {
                "label": season_label,
                "iri": season_iri,
                "competition_iris": [],
            },
        )
        competition_seasons.setdefault(competition_iri, set()).add(season_iri)
        season_competitions.setdefault(season_iri, set()).add(competition_iri)

    for competition_iri, option in competitions.items():
        option["season_iris"] = sorted(
            competition_seasons.get(competition_iri, set()),
            key=lambda item: season_sort_key(str(seasons.get(item, {}).get("label", ""))),
            reverse=True,
        )

    for season_iri, option in seasons.items():
        option["competition_iris"] = sorted(
            season_competitions.get(season_iri, set()),
            key=lambda item: normalize_selection_value(str(competitions.get(item, {}).get("label", ""))),
        )

    return {
        "leagues": sorted(
            competitions.values(),
            key=lambda item: normalize_selection_value(str(item.get("label", ""))),
        ),
        "seasons": sorted(
            seasons.values(),
            key=lambda item: season_sort_key(str(item.get("label", ""))),
            reverse=True,
        ),
    }


def selected_onboarding_pair(
    run_query,
    prefixes: str,
    *,
    competition_iri: str,
    season_iri: str,
) -> dict[str, dict[str, object]] | None:
    if not competition_iri or not season_iri:
        return None

    rows = run_query(
        prefixes
        + f"""
        SELECT ?competitionLabel ?seasonLabel
        WHERE {{
          VALUES (?competition ?season) {{ ({sparql_iri(competition_iri)} {sparql_iri(season_iri)}) }}
          ?item prop:belongsToCompetition ?competition ;
                prop:belongsToSeason ?season .
          OPTIONAL {{ ?competition rdfs:label ?competitionName . }}
          OPTIONAL {{ ?season rdfs:label ?seasonName . }}
          BIND(REPLACE(STR(?competition), "^.*/", "") AS ?competitionLocal)
          BIND(REPLACE(STR(?season), "^.*/", "") AS ?seasonLocal)
          BIND(COALESCE(?competitionName, ?competitionLocal) AS ?competitionLabel)
          BIND(COALESCE(?seasonName, ?seasonLocal) AS ?seasonLabel)
        }}
        LIMIT 1
        """,
        timeout=5,
    )
    if not rows:
        return None

    row = rows[0]
    competition_label = option_label(row.get("competitionLabel", ""), competition_iri)
    season_label = option_label(row.get("seasonLabel", ""), season_iri)
    asset = known_league_asset(competition_label, competition_iri)
    return {
        "league": {
            "label": competition_label,
            "iri": competition_iri,
            "logo": asset["logo"],
            "fallback": asset["fallback"],
            "season_iris": [season_iri],
        },
        "season": {
            "label": season_label,
            "iri": season_iri,
            "competition_iris": [competition_iri],
        },
    }


def selection_filter_clause(field_expr: str, selected_values: list[str]) -> str:
    if not selected_values:
        return ""

    checks = [
        f'CONTAINS(LCASE(REPLACE(REPLACE(STR({field_expr}), "_", " "), "-", " ")), {sparql_string(normalize_selection_value(value))})'
        for value in selected_values
    ]
    return "FILTER(" + " || ".join(checks) + ")"


def values_clause(field_expr: str, selected_iris: list[str]) -> str:
    iris = [sparql_iri(value) for value in selected_iris if value]
    if not field_expr or not iris:
        return ""
    return f"VALUES {field_expr} {{ {' '.join(iris)} }}"


def normalized_label_match_clause(field_expr: str, value: str) -> str:
    normalized_value = normalize_selection_value(value)
    if not normalized_value:
        return ""
    return (
        "CONTAINS("
        f'LCASE(REPLACE(REPLACE(STR({field_expr}), "_", " "), "-", " ")), '
        f"{sparql_string(normalized_value)}"
        ")"
    )


def selected_competition_iris(state: dict[str, list[str]]) -> list[str]:
    return [value for value in state.get("competition_iris", []) if value]


def selected_season_iris(state: dict[str, list[str]]) -> list[str]:
    return [value for value in state.get("season_iris", []) if value]


def onboarding_resource_clauses(
    app_boot_token: str,
    *,
    competition_var: str | None = None,
    season_var: str | None = None,
) -> str:
    state = get_onboarding_state(app_boot_token)
    clauses: list[str] = []

    if competition_var:
        clause = values_clause(competition_var, selected_competition_iris(state))
        if clause:
            clauses.append(clause)

    if season_var:
        clause = values_clause(season_var, selected_season_iris(state))
        if clause:
            clauses.append(clause)

    return "\n          ".join(clauses)


def onboarding_label_clauses(
    app_boot_token: str,
    *,
    competition_label: str | None = None,
    season_label: str | None = None,
) -> str:
    state = get_onboarding_state(app_boot_token)
    clauses: list[str] = []

    if competition_label and state["competitions"]:
        clauses.append(selection_filter_clause(competition_label, state["competitions"]))

    if season_label and state["seasons"]:
        clauses.append(selection_filter_clause(season_label, state["seasons"]))

    return "\n          ".join(clauses)


def onboarding_match_clauses(
    app_boot_token: str,
    match_var: str = "?m",
    *,
    date_var: str = "?date",
    week_var: str = "?week",
) -> str:
    del date_var, week_var
    state = get_onboarding_state(app_boot_token)
    clauses: list[str] = []

    competition_values = values_clause("?_baseCompetition", selected_competition_iris(state))
    if competition_values:
        clauses.append(
            f"""
            {competition_values}
            {match_var} prop:belongsToCompetition ?_baseCompetition .
            """
        )

    season_values = values_clause("?_baseSeason", selected_season_iris(state))
    if season_values:
        clauses.append(
            f"""
            {season_values}
            {match_var} prop:belongsToSeason ?_baseSeason .
            """
        )

    return "\n              ".join(clauses)


def selected_option(label: str, iri: str, *, kind: str) -> dict[str, object]:
    if kind == "league":
        asset = known_league_asset(label, iri)
        return {
            "label": label,
            "iri": iri,
            "logo": asset["logo"],
            "fallback": asset["fallback"],
            "season_iris": session_values("onboarding_season_iris"),
        }
    return {
        "label": label,
        "iri": iri,
        "competition_iris": session_values("onboarding_competition_iris"),
    }


def onboarding_choices(app_boot_token: str, run_query, prefixes: str) -> dict[str, object]:
    state = get_onboarding_state(app_boot_token)
    complete = onboarding_complete(app_boot_token)

    if complete:
        leagues = [
            selected_option(state["competitions"][0], state["competition_iris"][0], kind="league")
        ]
        seasons = [
            selected_option(state["seasons"][0], state["season_iris"][0], kind="season")
        ]
        error = ""
    else:
        try:
            available = available_onboarding_options(run_query, prefixes)
            leagues = available["leagues"]
            seasons = available["seasons"]
            error = ""
        except Exception as exc:
            leagues = []
            seasons = []
            error = f"No se pudieron consultar las competiciones y temporadas en GraphDB: {exc}"

    competition_choices = state["competitions"] if complete else [
        str(item.get("label", "")) for item in leagues if item.get("label")
    ]
    season_choices = state["seasons"] if complete else [
        str(item.get("label", "")) for item in seasons if item.get("label")
    ]

    return {
        "leagues": leagues,
        "seasons": seasons,
        "competition_options": list(dict.fromkeys(competition_choices)),
        "season_options": list(dict.fromkeys(season_choices)),
        "selected_competitions": state["competitions"],
        "selected_competition_iris": state["competition_iris"],
        "selected_seasons": state["seasons"],
        "selected_season_iris": state["season_iris"],
        "complete": complete,
        "error": error,
    }
