from __future__ import annotations

from flask import jsonify, request


def register_search_routes(app, deps) -> None:
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]
    filter_clauses = deps["filter_clauses"]
    text_filter = deps["text_filter"]
    build_url = deps["build_url"]

    def rank(label: str, query: str) -> tuple[int, str]:
        label_key = str(label or "").strip().lower()
        query_key = str(query or "").strip().lower()
        if label_key == query_key:
            return (0, label_key)
        if label_key.startswith(query_key):
            return (1, label_key)
        return (2, label_key)

    def suggestion_href(kind: str, label: str, filters: dict[str, object]) -> str:
        if kind == "player":
            return build_url("players", filters, "", {"player": label})
        return build_url("teams", filters, "", {"team": label})

    def fetch_entity_suggestions(query: str, filters: dict[str, object]) -> list[dict[str, str]]:
        rows = run_query(
            prefixes
            + f"""
            SELECT DISTINCT ?kind ?label
            WHERE {{
              {{
                ?tcs a class:TeamCompetitionSeason ;
                     prop:correspondsToTeam ?team ;
                     prop:belongsToCompetition ?competition ;
                     prop:belongsToSeason ?season .
                ?team rdfs:label ?label .
                ?competition rdfs:label ?competitionLabel .
                ?season rdfs:label ?seasonLabel .
                BIND("team" AS ?kind)
                {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
                {text_filter(query, '?label')}
              }}
              UNION
              {{
                ?stats a class:PlayerCompetitionSeasonStats ;
                       prop:correspondsToPlayer ?player ;
                       prop:belongsToTeamCompetitionSeason ?tcs .
                ?tcs prop:belongsToCompetition ?competition ;
                     prop:belongsToSeason ?season .
                ?player rdfs:label ?label .
                ?competition rdfs:label ?competitionLabel .
                ?season rdfs:label ?seasonLabel .
                OPTIONAL {{ ?player prop:knownAs ?knownAsRaw . }}
                OPTIONAL {{ ?player prop:fullName ?fullNameRaw . }}
                BIND(COALESCE(?knownAsRaw, "") AS ?knownAs)
                BIND(COALESCE(?fullNameRaw, "") AS ?fullName)
                BIND("player" AS ?kind)
                {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
                {text_filter(query, '?label', '?knownAs', '?fullName')}
              }}
            }}
            ORDER BY LCASE(?label) ?kind
            LIMIT 60
            """,
            timeout=5,
        )
        return [
            {
                "kind": row.get("kind", ""),
                "label": row.get("label", ""),
                "href": suggestion_href(row.get("kind", ""), row.get("label", ""), filters),
            }
            for row in rows
            if row.get("label") and row.get("kind")
        ]

    @app.route("/search/suggestions")
    def search_suggestions():
        query = request.args.get("q", "").strip()
        if len(query) < 2 or not onboarding_complete():
            return jsonify({"results": []})

        filters = get_filters()
        seen: set[tuple[str, str]] = set()
        results: list[dict[str, str]] = []

        try:
            candidates = fetch_entity_suggestions(query, filters)
        except Exception:
            return jsonify({"results": [], "error": "search_unavailable"})

        for item in sorted(candidates, key=lambda item: (rank(item.get("label", ""), query), item.get("kind", ""))):
            key = (item.get("kind", ""), item.get("label", "").strip().lower())
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= 12:
                break

        return jsonify({"results": results})
