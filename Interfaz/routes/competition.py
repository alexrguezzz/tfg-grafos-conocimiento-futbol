from __future__ import annotations


def register_competition_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_search = deps["get_search"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]
    filter_clauses = deps["filter_clauses"]
    text_filter = deps["text_filter"]
    no_data_panel = deps["no_data_panel"]
    get_filtered_matches = deps["get_filtered_matches"]
    format_match_datetime = deps["format_match_datetime"]
    build_url = deps["build_url"]

    @app.route("/competition")
    def competition():
        q = get_search()
        filters = get_filters()
        if not onboarding_complete():
            return render_page("competition", title="Competicion", subtitle="Seleccion inicial de ligas y temporadas", panels=no_data_panel())
        try:
            data = run_query(
                prefixes
                + f"""
                SELECT ?position ?teamLabel ?pts ?mp ?w ?d ?l ?gf ?ga ?gd
                WHERE {{
                  {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                  ?tcs a class:TeamCompetitionSeason ;
                       prop:belongsToCompetition ?competition ;
                       prop:belongsToSeason ?season ;
                       prop:correspondsToTeam ?team .
                  ?team rdfs:label ?teamLabel .

                  OPTIONAL {{ ?tcs prop:position ?position . }}
                  OPTIONAL {{ ?tcs prop:points ?pts . }}
                  OPTIONAL {{ ?tcs prop:matchesPlayed ?mp . }}
                  OPTIONAL {{ ?tcs prop:wins ?w . }}
                  OPTIONAL {{ ?tcs prop:draws ?d . }}
                  OPTIONAL {{ ?tcs prop:losses ?l . }}
                  OPTIONAL {{ ?tcs prop:goalsFor ?gf . }}
                  OPTIONAL {{ ?tcs prop:goalsAgainst ?ga . }}
                  OPTIONAL {{ ?tcs prop:goalDifference ?gd . }}

                  BIND(COALESCE(xsd:integer(?position), 999) AS ?positionSort)
                  BIND(COALESCE(xsd:integer(?pts), 0) AS ?pointsSort)

                  {filter_clauses(filters, competition_var='?competition', season_var='?season')}
                  {text_filter(q, '?teamLabel')}
                }}
                ORDER BY ?positionSort DESC(?pointsSort) ?teamLabel
                LIMIT 600
                """
            )
            def team_cell(label: str) -> dict[str, str]:
                return {"text": label or "-", "href": build_url("teams", filters, "", {"team": label or ""}), "class": "table-link"}

            rows = [
                [
                    r.get("position", "-"),
                    team_cell(r.get("teamLabel", "-")),
                    r.get("pts", "0"),
                    r.get("mp", "0"),
                    r.get("w", "0"),
                    r.get("d", "0"),
                    r.get("l", "0"),
                    r.get("gf", "0"),
                    r.get("ga", "0"),
                    r.get("gd", "0"),
                ]
                for r in data
            ]

            calendar_data = get_filtered_matches(filters, q)
            calendar_rows = [
                [
                    format_match_datetime(r.get("dateTime", ""), r.get("date", "")),
                    r.get("week", "-"),
                    team_cell(r.get("homeLabel", "-")),
                    team_cell(r.get("awayLabel", "-")),
                    f"{r.get('hs', '-')} - {r.get('as', '-')}",
                    {
                        "text": "Ver",
                        "href": build_url("match_detail", filters, q, {"match_uri": r.get("matchUri", "")}),
                        "class": "btn table-btn",
                    },
                ]
                for r in calendar_data
            ]

            panels = no_data_panel() if not rows and not calendar_rows else []
            return render_page(
                "competition",
                title="Competicion",
                subtitle="Clasificacion completa y calendario",
                headers=["Pos", "Equipo", "Pts", "PJ", "G", "E", "P", "GF", "GA", "DG"],
                rows=rows,
                panels=panels,
                extra_context={
                    "calendar_headers": ["Fecha y hora", "Jornada", "Local", "Visitante", "Marcador", "Detalle"],
                    "calendar_rows": calendar_rows,
                },
            )
        except Exception as exc:
            return render_page("competition", title="Competicion", subtitle="Competiciones y temporadas", error=f"Error: {exc}", panels=no_data_panel())
