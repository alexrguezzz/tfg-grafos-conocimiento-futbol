from __future__ import annotations

from services.utils import format_number, format_numeric_plain


def register_home_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_search = deps["get_search"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    filter_clauses = deps["filter_clauses"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]
    match_scope_clauses = deps["match_scope_clauses"]
    text_filter = deps["text_filter"]
    safe_int = deps["safe_int"]
    format_match_datetime = deps["format_match_datetime"]
    build_url = deps["build_url"]
    sparql_string = deps["sparql_string"]
    no_data_panel = deps["no_data_panel"]

    def link_cell(text: str, href: str, class_name: str = "table-link", target: str = "") -> dict[str, str]:
        cell = {"text": text or "-", "href": href, "class": class_name}
        if target:
            cell["target"] = target
        return cell

    def team_cell(label: str, filters: dict[str, object]) -> dict[str, str]:
        return link_cell(label, build_url("teams", filters, "", {"team": label}))

    def player_cell(label: str, filters: dict[str, object]) -> dict[str, str]:
        return link_cell(label, build_url("players", filters, "", {"player": label}))

    def safe_fetch(fetcher, default):
        try:
            return fetcher()
        except Exception:
            return default

    def empty_standings() -> dict[str, object]:
        return {
            "headers": ["Pos", "Equipo", "Pts", "PJ", "G", "E", "P", "GF", "GA", "DG"],
            "rows": [],
        }

    def empty_scorers() -> dict[str, object]:
        return {
            "headers": ["Jugador", "Equipo", "Goles"],
            "rows": [],
        }

    def empty_assists() -> dict[str, object]:
        return {
            "headers": ["Jugador", "Equipo", "Asistencias"],
            "rows": [],
        }

    def fetch_home_kpis(filters: dict[str, object]) -> dict[str, int]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?matchCount ?goals ?scoredMatches ?homeWins ?draws ?awayWins ?teamCount ?playerCount ?yellow ?red
            WHERE {{
              {{
                SELECT (COUNT(DISTINCT ?m) AS ?matchCount)
                       (SUM(?goalValue) AS ?goals)
                       (SUM(?scoredValue) AS ?scoredMatches)
                       (SUM(?homeWinValue) AS ?homeWins)
                       (SUM(?drawValue) AS ?draws)
                       (SUM(?awayWinValue) AS ?awayWins)
                WHERE {{
                  ?m a class:Match .
                  OPTIONAL {{ ?m prop:date ?dateRaw . }}
                  OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
                  OPTIONAL {{ ?m prop:dateTime ?dateTimeRaw . }}
                  OPTIONAL {{ ?m prop:matchDateTime ?legacyDateTime . }}
                  OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
                  OPTIONAL {{ ?m prop:week ?legacyWeek . }}
                  OPTIONAL {{ ?m prop:homeScore ?hs . }}
                  OPTIONAL {{ ?m prop:awayScore ?as . }}
                  BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
                  BIND(COALESCE(?dateTimeRaw, ?legacyDateTime) AS ?dateTime)
                  BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
                  FILTER(BOUND(?date))
                  {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
                  BIND(IF(BOUND(?hs), xsd:integer(?hs), 0) AS ?homeGoals)
                  BIND(IF(BOUND(?as), xsd:integer(?as), 0) AS ?awayGoals)
                  BIND(?homeGoals + ?awayGoals AS ?goalValue)
                  BIND(IF(BOUND(?hs) && BOUND(?as), 1, 0) AS ?scoredValue)
                  BIND(IF(BOUND(?hs) && BOUND(?as) && ?homeGoals > ?awayGoals, 1, 0) AS ?homeWinValue)
                  BIND(IF(BOUND(?hs) && BOUND(?as) && ?homeGoals = ?awayGoals, 1, 0) AS ?drawValue)
                  BIND(IF(BOUND(?hs) && BOUND(?as) && ?homeGoals < ?awayGoals, 1, 0) AS ?awayWinValue)
                }}
              }}
              {{
                SELECT (COUNT(DISTINCT ?team) AS ?teamCount)
                WHERE {{
                  ?m a class:Match ;
                     prop:hasTeamMatchParticipation ?teamParticipation .
                  ?teamParticipation prop:correspondsToTeam ?team .
                  OPTIONAL {{ ?m prop:date ?dateRaw . }}
                  OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
                  OPTIONAL {{ ?m prop:dateTime ?dateTimeRaw . }}
                  OPTIONAL {{ ?m prop:matchDateTime ?legacyDateTime . }}
                  OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
                  OPTIONAL {{ ?m prop:week ?legacyWeek . }}
                  BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
                  BIND(COALESCE(?dateTimeRaw, ?legacyDateTime) AS ?dateTime)
                  BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
                  FILTER(BOUND(?date))
                  {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
                }}
              }}
              {{
                SELECT (COUNT(DISTINCT ?p) AS ?playerCount)
                       (SUM(?yellowValue) AS ?yellow)
                       (SUM(?redValue) AS ?red)
                WHERE {{
                  ?pcs a class:PlayerCompetitionSeasonStats ;
                       prop:correspondsToPlayer ?p ;
                       prop:belongsToTeamCompetitionSeason ?pcsTcs .
                  ?pcsTcs prop:belongsToCompetition ?competition ;
                          prop:belongsToSeason ?season .
                  ?competition rdfs:label ?competitionLabel .
                  ?season rdfs:label ?seasonLabel .
                  OPTIONAL {{ ?pcs prop:yellowCards ?yellowRaw . }}
                  OPTIONAL {{ ?pcs prop:redCards ?redRaw . }}
                  BIND(COALESCE(xsd:integer(?yellowRaw), 0) AS ?yellowValue)
                  BIND(COALESCE(xsd:integer(?redRaw), 0) AS ?redValue)
                  {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                  {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
                }}
              }}
            }}
            """,
            timeout=8,
        )
        row = rows[0] if rows else {}
        return {
            "match_count": safe_int(row.get("matchCount", "0")),
            "team_count": safe_int(row.get("teamCount", "0")),
            "goals": safe_int(row.get("goals", "0")),
            "scored_matches": safe_int(row.get("scoredMatches", "0")),
            "home_wins": safe_int(row.get("homeWins", "0")),
            "draws": safe_int(row.get("draws", "0")),
            "away_wins": safe_int(row.get("awayWins", "0")),
            "player_count": safe_int(row.get("playerCount", "0")),
            "yellow": safe_int(row.get("yellow", "0")),
            "red": safe_int(row.get("red", "0")),
        }

    def fetch_recent_matches(filters: dict[str, object], q: str, limit: int = 8) -> list[dict[str, str]]:
        del q
        return run_query(
            prefixes
            + f"""
            SELECT ?matchUri ?date ?dateTime ?week ?homeLabel ?awayLabel ?hs ?as
            WHERE {{
              ?m a class:Match ;
                 prop:hasTeamMatchParticipation ?homeP ;
                 prop:hasTeamMatchParticipation ?awayP .
              BIND(STR(?m) AS ?matchUri)
              OPTIONAL {{ ?m prop:date ?dateRaw . }}
              OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
              OPTIONAL {{ ?m prop:dateTime ?dateTimeRaw . }}
              OPTIONAL {{ ?m prop:matchDateTime ?legacyDateTime . }}
              OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
              OPTIONAL {{ ?m prop:week ?legacyWeek . }}
              OPTIONAL {{ ?m prop:homeScore ?hs . }}
              OPTIONAL {{ ?m prop:awayScore ?as . }}
              BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
              BIND(COALESCE(?dateTimeRaw, ?legacyDateTime) AS ?dateTime)
              BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
              FILTER(BOUND(?date))
              ?homeP prop:isHome true ; prop:correspondsToTeam ?home .
              ?awayP prop:isHome false ; prop:correspondsToTeam ?away .
              ?home rdfs:label ?homeLabel .
              ?away rdfs:label ?awayLabel .
              {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
            }}
            ORDER BY DESC(COALESCE(?dateTime, ?date)) ?homeLabel ?awayLabel
            LIMIT {limit}
            """,
            timeout=8,
        )

    def fetch_standings(filters: dict[str, object], q: str, limit: int = 5) -> dict[str, object]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?competitionLabel ?seasonLabel ?position ?teamLabel ?pts ?mp ?w ?d ?l ?gf ?ga ?gd
            WHERE {{
              ?tcs a class:TeamCompetitionSeason ;
                   prop:belongsToCompetition ?competition ;
                   prop:belongsToSeason ?season ;
                   prop:correspondsToTeam ?team .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
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

              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
              {text_filter(q, '?competitionLabel', '?seasonLabel', '?teamLabel')}
            }}
            ORDER BY ?competitionLabel DESC(?seasonLabel) ?positionSort DESC(?pointsSort) ?teamLabel
            LIMIT {limit}
            """,
            timeout=8,
        )

        return {
            "headers": ["Pos", "Equipo", "Pts", "PJ", "G", "E", "P", "GF", "GA", "DG"],
            "rows": [
                [
                    row.get("position", "-"),
                    team_cell(row.get("teamLabel", "-"), filters),
                    format_number(row.get("pts", "")),
                    format_number(row.get("mp", "")),
                    format_number(row.get("w", "")),
                    format_number(row.get("d", "")),
                    format_number(row.get("l", "")),
                    format_number(row.get("gf", "")),
                    format_number(row.get("ga", "")),
                    format_number(row.get("gd", "")),
                ]
                for row in rows
            ],
        }

    def fetch_scorers(filters: dict[str, object], q: str, limit: int | None = 5) -> dict[str, object]:
        limit_clause = f"LIMIT {limit}" if limit else ""
        rows = run_query(
            prefixes
            + f"""
            SELECT ?playerLabel ?teamLabel (SUM(?goalValue) AS ?goals)
            WHERE {{
              ?pcs a class:PlayerCompetitionSeasonStats ;
                   prop:correspondsToPlayer ?player ;
                   prop:belongsToTeamCompetitionSeason ?pcsTcs .
              ?pcsTcs prop:belongsToCompetition ?competition ;
                      prop:belongsToSeason ?season ;
                      prop:correspondsToTeam ?team .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              OPTIONAL {{ ?pcs prop:goals ?goalsRaw . }}
              ?player rdfs:label ?playerLabel .
              ?team rdfs:label ?teamLabel .
              BIND(COALESCE(xsd:integer(?goalsRaw), 0) AS ?goalValue)

              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
              {text_filter(q, '?playerLabel', '?teamLabel', '?competitionLabel', '?seasonLabel')}
            }}
            GROUP BY ?playerLabel ?teamLabel
            HAVING(SUM(?goalValue) > 0)
            ORDER BY DESC(?goals) ?playerLabel ?teamLabel
            {limit_clause}
            """,
            timeout=8,
        )
        return {
            "headers": ["Jugador", "Equipo", "Goles"],
            "rows": [
                [
                    player_cell(row.get("playerLabel", "-"), filters),
                    team_cell(row.get("teamLabel", "-"), filters),
                    row.get("goals", "0"),
                ]
                for row in rows
            ],
        }

    def fetch_assists(filters: dict[str, object], q: str, limit: int | None = 5) -> dict[str, object]:
        limit_clause = f"LIMIT {limit}" if limit else ""
        rows = run_query(
            prefixes
            + f"""
            SELECT ?playerLabel ?teamLabel (SUM(?assistValue) AS ?assists)
            WHERE {{
              ?pcs a class:PlayerCompetitionSeasonStats ;
                   prop:correspondsToPlayer ?player ;
                   prop:belongsToTeamCompetitionSeason ?pcsTcs .
              ?pcsTcs prop:belongsToCompetition ?competition ;
                      prop:belongsToSeason ?season ;
                      prop:correspondsToTeam ?team .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              OPTIONAL {{ ?pcs prop:assists ?assistsRaw . }}
              ?player rdfs:label ?playerLabel .
              ?team rdfs:label ?teamLabel .
              BIND(COALESCE(xsd:integer(?assistsRaw), 0) AS ?assistValue)

              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
              {text_filter(q, '?playerLabel', '?teamLabel', '?competitionLabel', '?seasonLabel')}
            }}
            GROUP BY ?playerLabel ?teamLabel
            HAVING(SUM(?assistValue) > 0)
            ORDER BY DESC(?assists) ?playerLabel ?teamLabel
            {limit_clause}
            """,
            timeout=8,
        )
        return {
            "headers": ["Jugador", "Equipo", "Asistencias"],
            "rows": [
                [
                    player_cell(row.get("playerLabel", "-"), filters),
                    team_cell(row.get("teamLabel", "-"), filters),
                    row.get("assists", "0"),
                ]
                for row in rows
            ],
        }

    def fetch_featured_elo(filters: dict[str, object]) -> list[dict[str, object]]:
        candidate_rows = run_query(
            prefixes
            + f"""
            SELECT DISTINCT ?teamLabel
            WHERE {{
              ?tcs a class:TeamCompetitionSeason ;
                   prop:correspondsToTeam ?team ;
                   prop:belongsToCompetition ?competition ;
                   prop:belongsToSeason ?season .
              ?team rdfs:label ?teamLabel .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
            }}
            ORDER BY LCASE(?teamLabel)
            LIMIT 40
            """,
            timeout=8,
        )

        items: list[dict[str, object]] = []
        for candidate in candidate_rows:
            candidate_label = candidate.get("teamLabel", "")
            if not candidate_label:
                continue
            rows = run_query(
                prefixes
                + f"""
                SELECT ?elo ?d
                WHERE {{
                  ?team a class:Team ; rdfs:label {sparql_string(candidate_label)} .
                  ?record a class:EloRecord ;
                          prop:correspondsToTeam ?team ;
                          prop:elo ?elo .
                  OPTIONAL {{ ?record prop:dateFrom ?dateFrom . }}
                  OPTIONAL {{ ?record prop:dateTo ?dateTo . }}
                  BIND(COALESCE(?dateFrom, ?dateTo) AS ?d)
                  FILTER(BOUND(?d))
                }}
                ORDER BY DESC(?d)
                LIMIT 1
                """,
                timeout=5,
            )
            row = rows[0] if rows else {}
            elo = row.get("elo", "")
            if not elo:
                continue
            try:
                sort_elo = float(elo)
            except Exception:
                continue
            items.append(
                {
                    "team": team_cell(candidate_label, filters),
                    "elo": format_numeric_plain(elo),
                    "sort_elo": sort_elo,
                }
            )

        items.sort(key=lambda item: (-float(item.get("sort_elo", 0)), str(item["team"].get("text", ""))))
        return [{key: value for key, value in item.items() if key != "sort_elo"} for item in items[:5]]

    def build_result_distribution(kpis: dict[str, int]) -> list[dict[str, str]]:
        counts = {
            "home": safe_int(str(kpis.get("home_wins", 0))),
            "draw": safe_int(str(kpis.get("draws", 0))),
            "away": safe_int(str(kpis.get("away_wins", 0))),
        }
        total = safe_int(str(kpis.get("scored_matches", 0))) or 1
        items = [
            ("Victorias locales", counts["home"], "is-home"),
            ("Empates", counts["draw"], "is-draw"),
            ("Victorias visitantes", counts["away"], "is-away"),
        ]
        return [
            {
                "label": label,
                "count": str(count),
                "pct": f"{(count / total) * 100:.2f}",
                "pct_label": f"{(count / total) * 100:.0f}%",
                "class": class_name,
            }
            for label, count, class_name in items
        ]

    def build_recent_matches(matches: list[dict[str, str]], filters: dict[str, object], q: str) -> dict[str, object]:
        rows = []
        for row in matches:
            rows.append(
                [
                    format_match_datetime(row.get("dateTime", ""), row.get("date", "")),
                    row.get("week", "-"),
                    team_cell(row.get("homeLabel", "-"), filters),
                    team_cell(row.get("awayLabel", "-"), filters),
                    f"{row.get('hs', '-')} - {row.get('as', '-')}",
                    link_cell(
                        "Ver",
                        build_url("match_detail", filters, q, {"match_uri": row.get("matchUri", "")}),
                        "btn table-btn",
                    ),
                ]
            )
        return {
            "headers": ["Fecha y hora", "Jornada", "Local", "Visitante", "Marcador", "Detalle"],
            "rows": rows,
        }

    @app.route("/")
    def home():
        q = get_search()
        filters = get_filters()

        if not onboarding_complete():
            return render_page(
                "home",
                title="Inicio",
                subtitle="Seleccion inicial de ligas y temporadas",
                panels=[{"title": "Seleccion inicial", "kind": "text", "text": "Configura primero las ligas y temporadas que quieres cargar."}],
            )

        try:
            kpis = safe_fetch(lambda: fetch_home_kpis(filters), {})
            recent_matches = safe_fetch(lambda: fetch_recent_matches(filters, q, limit=8), [])
            yellow_cards = safe_int(str(kpis.get("yellow", 0)))
            red_cards = safe_int(str(kpis.get("red", 0)))
            player_count = safe_int(str(kpis.get("player_count", 0)))

            cards: list[dict[str, object]] = [
                {"label": "Partidos jugados", "value": str(safe_int(str(kpis.get("match_count", 0)))), "href": build_url("matches", filters, q)},
                {"label": "Equipos", "value": str(safe_int(str(kpis.get("team_count", 0)))), "href": build_url("teams", filters, q)},
                {
                    "label": "Jugadores",
                    "value": str(player_count) if player_count > 0 else "Informacion no disponible",
                    "href": build_url("players", filters, q),
                },
                {"label": "Goles", "value": str(safe_int(str(kpis.get("goals", 0))))},
                {
                    "label": "Tarjetas",
                    "value": str(yellow_cards + red_cards),
                    "meta": [
                        {"title": "Amarillas", "class": "is-yellow-card", "value": str(yellow_cards)},
                        {"title": "Rojas", "class": "is-red-card", "value": str(red_cards)},
                    ],
                },
            ]

            if recent_matches:
                last = recent_matches[0]
                cards.append(
                    {
                        "label": "Ultimo partido",
                        "value": f"{last.get('homeLabel', '-')} {last.get('hs', '-')} - {last.get('as', '-')} {last.get('awayLabel', '-')}",
                        "secondary": format_match_datetime(last.get("dateTime", ""), last.get("date", "")),
                        "href": build_url("match_detail", filters, q, {"match_uri": last.get("matchUri", "")}),
                        "wide": True,
                    }
                )
            else:
                cards.append({"label": "Ultimo partido", "value": "Informacion no disponible", "wide": True})

            scorers = safe_fetch(lambda: fetch_scorers(filters, q, limit=5), empty_scorers())
            assists = safe_fetch(lambda: fetch_assists(filters, q, limit=5), empty_assists())

            dashboard = {
                "standings": safe_fetch(lambda: fetch_standings(filters, q, limit=5), empty_standings()),
                "standings_url": build_url("competition", filters, q),
                "scorers": scorers,
                "scorers_url": build_url("scorers", filters, q),
                "assists": assists,
                "assists_url": build_url("assists", filters, q),
                "elo": safe_fetch(lambda: fetch_featured_elo(filters), []),
                "recent_matches": build_recent_matches(recent_matches, filters, q),
                "matches_url": build_url("matches", filters, q),
                "result_distribution": build_result_distribution(kpis),
            }

            return render_page(
                "home",
                title="Inicio",
                subtitle="Resumen global",
                cards=cards,
                panels=[],
                extra_context={"dashboard": dashboard},
            )
        except Exception as exc:
            return render_page(
                "home",
                title="Inicio",
                subtitle="Resumen global",
                cards=[],
                panels=no_data_panel(),
                error=f"Error de conexion con GraphDB: {exc}",
            )

    @app.route("/assists")
    def assists():
        q = get_search()
        filters = get_filters()

        if not onboarding_complete():
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion asistentes",
                subtitle="Seleccion inicial de ligas y temporadas",
                panels=no_data_panel(),
            )

        try:
            data = fetch_assists(filters, q, limit=None)
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion asistentes",
                subtitle="Asistentes filtrados",
                headers=data["headers"],
                rows=data["rows"],
                panels=no_data_panel() if not data["rows"] else [],
            )
        except Exception as exc:
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion asistentes",
                subtitle="Asistentes filtrados",
                panels=no_data_panel(),
                error=f"Error: {exc}",
            )

    @app.route("/scorers")
    def scorers():
        q = get_search()
        filters = get_filters()

        if not onboarding_complete():
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion goleadores",
                subtitle="Seleccion inicial de ligas y temporadas",
                panels=no_data_panel(),
            )

        try:
            data = fetch_scorers(filters, q, limit=None)
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion goleadores",
                subtitle="Goleadores filtrados",
                headers=data["headers"],
                rows=data["rows"],
                panels=no_data_panel() if not data["rows"] else [],
            )
        except Exception as exc:
            return render_page(
                "home",
                current_view="scorers",
                title="Clasificacion goleadores",
                subtitle="Goleadores filtrados",
                panels=no_data_panel(),
                error=f"Error: {exc}",
            )
