from __future__ import annotations

from datetime import datetime as dt_datetime
import unicodedata

from flask import request

from services.utils import format_display_date, format_number, format_numeric_plain, parse_datetime


def register_teams_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_search = deps["get_search"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    text_filter = deps["text_filter"]
    filter_clauses = deps["filter_clauses"]
    build_elo_panel = deps["build_elo_panel"]
    no_data_panel = deps["no_data_panel"]
    build_url = deps["build_url"]
    format_match_datetime = deps["format_match_datetime"]
    sparql_string = deps["sparql_string"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]

    def link_cell(text: str, href: str, class_name: str = "table-link") -> dict[str, str]:
        return {"text": text or "-", "href": href, "class": class_name}

    def team_href(label: str, filters: dict[str, object]) -> str:
        return build_url("teams", filters, "", {"team": label})

    def player_href(label: str, filters: dict[str, object]) -> str:
        return build_url("players", filters, "", {"player": label})

    def match_href(match_uri: str, filters: dict[str, object], q: str = "") -> str:
        return build_url("match_detail", filters, q, {"match_uri": match_uri})

    def normalize_label(value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(text.replace("_", " ").replace("-", " ").lower().split())

    def row_matches_filters(row: dict[str, str], filters: dict[str, object]) -> bool:
        competition = str(filters.get("competition", "all")).strip()
        season = str(filters.get("season", "all")).strip()
        if competition != "all" and normalize_label(row.get("competitionLabel")) != normalize_label(competition):
            return False
        if season != "all" and normalize_label(row.get("seasonLabel")) != normalize_label(season):
            return False
        return True

    def filters_with_season(filters: dict[str, object], season: str) -> dict[str, object]:
        next_filters = dict(filters)
        next_filters["season"] = season if season else "all"
        return next_filters

    def selected_available_season(
        requested: str,
        fallback: str,
        available_seasons: list[str],
        preferred_seasons: list[str] | None = None,
    ) -> str:
        if requested and requested in available_seasons:
            return requested
        if fallback and fallback != "all" and fallback in available_seasons:
            return fallback
        for season in preferred_seasons or []:
            if season in available_seasons:
                return season
        return available_seasons[0] if available_seasons else ""

    def fetch_current_elo(team_label: str) -> str:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?elo ?d
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
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
            """
        )
        if not rows:
            return "Informacion no disponible"
        return format_numeric_plain(rows[0].get("elo", ""))

    def fetch_elo_at_date(team_label: str, date_text: str) -> dict[str, str] | None:
        date_text = date_text.strip()
        if not date_text:
            return None

        rows = run_query(
            prefixes
            + f"""
            SELECT ?elo ?dateFrom ?dateTo ?d
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?record a class:EloRecord ;
                      prop:correspondsToTeam ?team ;
                      prop:elo ?elo .
              OPTIONAL {{ ?record prop:dateFrom ?dateFrom . }}
              OPTIONAL {{ ?record prop:dateTo ?dateTo . }}
              BIND(COALESCE(?dateFrom, ?dateTo) AS ?d)
              FILTER(BOUND(?d))
              FILTER(
                (
                  BOUND(?dateFrom)
                  && xsd:date(?dateFrom) <= "{date_text}"^^xsd:date
                  && (!BOUND(?dateTo) || xsd:date(?dateTo) >= "{date_text}"^^xsd:date)
                )
                || (
                  !BOUND(?dateFrom)
                  && BOUND(?dateTo)
                  && xsd:date(?dateTo) <= "{date_text}"^^xsd:date
                )
              )
            }}
            ORDER BY DESC(?d)
            LIMIT 1
            """
        )
        if not rows:
            return {"value": "Informacion no disponible"}

        row = rows[0]
        period = ""
        date_from = row.get("dateFrom", "") or row.get("d", "")
        date_to = row.get("dateTo", "")
        if date_from and date_to:
            period = f"Valor desde {format_display_date(date_from)} hasta {format_display_date(date_to)}"
        elif date_from:
            period = f"Valor desde {format_display_date(date_from)}"
        elif date_to:
            period = f"Valor hasta {format_display_date(date_to)}"
        return {
            "value": format_numeric_plain(row.get("elo", "")),
            "period": period,
        }

    def fetch_classification_records(
        team_label: str,
        filters: dict[str, object],
        *,
        include_onboarding_season: bool = True,
    ) -> list[dict[str, str]]:
        classification_filters = filters_with_season(filters, "all")
        onboarding_clauses = onboarding_resource_clauses(
            competition_var="?competition",
            season_var="?season" if include_onboarding_season else None,
        )
        return run_query(
            prefixes
            + f"""
            SELECT ?competitionLabel ?seasonLabel ?position ?pts ?mp ?w ?d ?l ?gf ?ga ?gd
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?tcs a class:TeamCompetitionSeason ;
                   prop:correspondsToTeam ?team ;
                   prop:belongsToCompetition ?competition ;
                   prop:belongsToSeason ?season .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .

              OPTIONAL {{ ?tcs prop:position ?position . }}
              OPTIONAL {{ ?tcs prop:points ?pts . }}
              OPTIONAL {{ ?tcs prop:matchesPlayed ?mp . }}
              OPTIONAL {{ ?tcs prop:wins ?w . }}
              OPTIONAL {{ ?tcs prop:draws ?d . }}
              OPTIONAL {{ ?tcs prop:losses ?l . }}
              OPTIONAL {{ ?tcs prop:goalsFor ?gf . }}
              OPTIONAL {{ ?tcs prop:goalsAgainst ?ga . }}
              OPTIONAL {{ ?tcs prop:goalDifference ?gd . }}

              {onboarding_clauses}
              {filter_clauses(classification_filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
            }}
            ORDER BY DESC(?seasonLabel) ?competitionLabel
            """
        )

    def build_classification_rows(records: list[dict[str, str]]) -> list[list[object]]:
        return [
            [
                row.get("competitionLabel", "-"),
                row.get("seasonLabel", "-"),
                row.get("position", "-"),
                format_number(row.get("pts", "")),
                format_number(row.get("mp", "")),
                format_number(row.get("w", "")),
                format_number(row.get("d", "")),
                format_number(row.get("l", "")),
                format_number(row.get("gf", "")),
                format_number(row.get("ga", "")),
                format_number(row.get("gd", "")),
            ]
            for row in records
        ]

    def available_seasons_from_records(records: list[dict[str, str]]) -> list[str]:
        return sorted({row.get("seasonLabel", "") for row in records if row.get("seasonLabel")}, reverse=True)

    def current_position_from_records(records: list[dict[str, str]], filters: dict[str, object]) -> str:
        selected_season = str(filters.get("season", "all")).strip()
        if selected_season != "all":
            for row in records:
                if row.get("seasonLabel") == selected_season:
                    return row.get("position", "-")
        return records[0].get("position", "-") if records else "Informacion no disponible"

    def fetch_squad_count(team_label: str, filters: dict[str, object]) -> int:
        rows = run_query(
            prefixes
            + f"""
            SELECT (COUNT(DISTINCT ?player) AS ?players)
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:correspondsToTeam ?team ;
                        prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              {onboarding_resource_clauses(competition_var='?competition')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
            }}
            """
        )
        if not rows:
            return 0
        try:
            return int(float(rows[0].get("players", "0") or 0))
        except Exception:
            return 0

    def fetch_squad_from_season_stats(team_label: str, filters: dict[str, object], q: str) -> list[list[object]]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?competitionLabel ?seasonLabel ?playerLabel ?matches ?minutes ?goals ?penaltyGoals ?assists ?shots ?xg ?nonPenaltyXg ?xa ?keyPasses ?xgChain ?xgBuildup ?yellow ?red
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:correspondsToTeam ?team ;
                        prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              ?player rdfs:label ?playerLabel .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .

              OPTIONAL {{ ?stats prop:matches ?matches . }}
              OPTIONAL {{ ?stats prop:minutes ?minutes . }}
              OPTIONAL {{ ?stats prop:goals ?goals . }}
              OPTIONAL {{ ?stats prop:nonPenaltyGoals ?nonPenaltyGoals . }}
              OPTIONAL {{ ?stats prop:assists ?assists . }}
              OPTIONAL {{ ?stats prop:shots ?shots . }}
              OPTIONAL {{ ?stats prop:xg ?xg . }}
              OPTIONAL {{ ?stats prop:nonPenaltyXg ?nonPenaltyXg . }}
              OPTIONAL {{ ?stats prop:xa ?xa . }}
              OPTIONAL {{ ?stats prop:keyPasses ?keyPasses . }}
              OPTIONAL {{ ?stats prop:xgChain ?xgChain . }}
              OPTIONAL {{ ?stats prop:xgBuildup ?xgBuildup . }}
              OPTIONAL {{ ?stats prop:yellowCards ?yellow . }}
              OPTIONAL {{ ?stats prop:redCards ?red . }}
              BIND(COALESCE(xsd:integer(?minutes), 0) AS ?minutesSort)
              BIND(COALESCE(xsd:integer(?goals), 0) AS ?goalsValue)
              BIND(COALESCE(xsd:integer(?nonPenaltyGoals), 0) AS ?nonPenaltyGoalsValue)
              BIND(IF(?goalsValue > ?nonPenaltyGoalsValue, ?goalsValue - ?nonPenaltyGoalsValue, 0) AS ?penaltyGoals)

              {onboarding_resource_clauses(competition_var='?competition')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season', competition_label='?competitionLabel', season_label='?seasonLabel')}
              {text_filter(q, '?playerLabel')}
            }}
            ORDER BY DESC(?seasonLabel) DESC(?minutesSort) ?playerLabel
            LIMIT 500
            """
        )
        filtered_rows = [row for row in rows if row_matches_filters(row, filters)]
        return [
            [
                link_cell(row.get("playerLabel", "-"), player_href(row.get("playerLabel", ""), filters)),
                row.get("competitionLabel", "-"),
                row.get("seasonLabel", "-"),
                format_number(row.get("matches", "")),
                format_number(row.get("minutes", "")),
                format_number(row.get("goals", "")),
                format_number(row.get("penaltyGoals", "")),
                format_number(row.get("assists", "")),
                format_number(row.get("shots", "")),
                format_number(row.get("xg", ""), 2),
                format_number(row.get("nonPenaltyXg", ""), 2),
                format_number(row.get("xa", ""), 2),
                format_number(row.get("keyPasses", "")),
                format_number(row.get("xgChain", ""), 2),
                format_number(row.get("xgBuildup", ""), 2),
                format_number(row.get("yellow", "")),
                format_number(row.get("red", "")),
            ]
            for row in filtered_rows
        ]

    def team_match_scope_clauses(filters: dict[str, object]) -> str:
        match_filters = {
            "competition": "all",
            "season": str(filters.get("season", "all")).strip() or "all",
            "jornadas": [],
            "date_from": "",
            "date_to": "",
        }
        clauses = [
            """
              ?m prop:belongsToCompetition ?_teamMatchCompetition .
              ?_teamMatchCompetition rdfs:label ?_teamMatchCompetitionLabel .
              ?m prop:belongsToSeason ?_teamMatchSeason .
              ?_teamMatchSeason rdfs:label ?_teamMatchSeasonLabel .
            """
        ]
        onboarding_filters = onboarding_resource_clauses(competition_var="?_teamMatchCompetition")
        base_filters = filter_clauses(
            match_filters,
            competition_var="?_teamMatchCompetition",
            season_var="?_teamMatchSeason",
            competition_label="?_teamMatchCompetitionLabel",
            season_label="?_teamMatchSeasonLabel",
        )
        if onboarding_filters:
            clauses.append(onboarding_filters)
        if base_filters:
            clauses.append(base_filters)
        return "\n              ".join(clauses)

    def fetch_team_matches(team_label: str, filters: dict[str, object], q: str) -> list[list[object]]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?matchUri ?date ?dateTime ?week ?homeLabel ?awayLabel ?hs ?as ?isHome
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
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
              FILTER(?home = ?team || ?away = ?team)
              BIND(?home = ?team AS ?isHome)
              ?home rdfs:label ?homeLabel .
              ?away rdfs:label ?awayLabel .

              {team_match_scope_clauses(filters)}
              {text_filter(q, '?homeLabel', '?awayLabel')}
            }}
            ORDER BY DESC(COALESCE(?dateTime, ?date)) ?homeLabel ?awayLabel
            LIMIT 500
            """
        )
        rows.sort(
            key=lambda row: parse_datetime(row.get("dateTime", "")) or parse_datetime(row.get("date", "")) or dt_datetime.min,
            reverse=True,
        )
        team_matches: list[list[object]] = []
        for row in rows:
            if normalize_label(team_label) not in {normalize_label(row.get("homeLabel")), normalize_label(row.get("awayLabel"))}:
                continue
            score = f"{row.get('hs', '-')} - {row.get('as', '-')}"
            team_matches.append(
                [
                    format_match_datetime(row.get("dateTime", ""), row.get("date", "")),
                    row.get("week", "-"),
                    link_cell(row.get("homeLabel", "-"), team_href(row.get("homeLabel", ""), filters)),
                    link_cell(row.get("awayLabel", "-"), team_href(row.get("awayLabel", ""), filters)),
                    score,
                    link_cell("Ver", match_href(row.get("matchUri", ""), filters, q), "btn table-btn"),
                ]
            )
        return team_matches

    @app.route("/teams")
    def teams():
        q = get_search()
        filters = get_filters()
        team_label = request.args.get("team", "").strip()
        elo_date = request.args.get("elo_date", "").strip()

        if not onboarding_complete():
            return render_page("teams", title="Equipos", subtitle="Seleccion inicial de ligas y temporadas", panels=no_data_panel())

        try:
            if team_label:
                classification_records = fetch_classification_records(team_label, filters)
                all_classification_records = fetch_classification_records(team_label, filters, include_onboarding_season=False)
                classification_rows = build_classification_rows(all_classification_records)
                visible_seasons = available_seasons_from_records(classification_records)
                available_seasons = available_seasons_from_records(all_classification_records) or visible_seasons
                squad_requested_season = request.args.get("squad_season", "").strip()
                selected_squad_season = selected_available_season(
                    squad_requested_season,
                    str(filters.get("season", "all")).strip(),
                    available_seasons,
                    visible_seasons,
                )
                match_requested_season = request.args.get("team_matches_season", "").strip()
                if match_requested_season == "all":
                    selected_match_season = "all"
                elif not match_requested_season:
                    selected_match_season = "all"
                else:
                    selected_match_season = selected_available_season(
                        match_requested_season,
                        str(filters.get("season", "all")).strip(),
                        available_seasons,
                        visible_seasons,
                    )
                squad_filters = filters_with_season(filters, selected_squad_season)
                match_filters = filters_with_season(filters, selected_match_season)
                current_elo = format_numeric_plain(fetch_current_elo(team_label))
                elo_at_date = fetch_elo_at_date(team_label, elo_date)
                current_position = current_position_from_records(classification_records, filters)
                squad_rows = fetch_squad_from_season_stats(team_label, squad_filters, q)
                player_names = {
                    str(row[0].get("text", "") if isinstance(row[0], dict) else row[0])
                    for row in squad_rows
                    if row
                }
                squad_count = len([name for name in player_names if name and name != "-"])
                if not squad_count:
                    squad_count = fetch_squad_count(team_label, squad_filters)

                elo_panel = build_elo_panel([team_label], years=10)
                team_detail = {
                    "classification_headers": ["Competicion", "Temporada", "Pos", "Pts", "PJ", "G", "E", "P", "GF", "GA", "DG"],
                    "classification_rows": classification_rows,
                    "elo_panel": elo_panel,
                    "elo_date": elo_date,
                    "elo_at_date": elo_at_date,
                    "season_options": available_seasons,
                    "squad_selected_season": selected_squad_season,
                    "squad_headers": ["Jugador", "Competicion", "Temporada", "PJ", "Min", "G", "G pen.", "A", "Tiros", "xG", "xG sin pen.", "xA", "Pases clave", "xGChain", "xGBuildup", "TA", "TR"],
                    "squad_rows": squad_rows,
                    "match_selected_season": selected_match_season,
                    "match_headers": ["Fecha y hora", "Jornada", "Equipo local", "Equipo visitante", "Marcador", "Detalle"],
                    "match_rows": fetch_team_matches(team_label, match_filters, q),
                }
                cards = [
                    {"label": "Jugadores", "value": str(squad_count)},
                    {"label": "ELO actual", "value": format_numeric_plain(current_elo)},
                    {"label": "Posicion en liga", "value": str(current_position)},
                ]
                return render_page(
                    "teams",
                    title=team_label,
                    subtitle="Ficha de equipo",
                    cards=cards,
                    panels=[],
                    extra_context={"team_detail": team_detail},
                )

            data = run_query(
                prefixes
                + f"""
                SELECT DISTINCT ?teamLabel
                WHERE {{
                  ?tcs a class:TeamCompetitionSeason ;
                       prop:correspondsToTeam ?team ;
                       prop:belongsToCompetition ?competition ;
                       prop:belongsToSeason ?season .
                  ?team rdfs:label ?teamLabel .
                  {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                  {filter_clauses(filters, competition_var='?competition', season_var='?season')}
                  {text_filter(q, '?teamLabel')}
                }}
                ORDER BY LCASE(?teamLabel)
                LIMIT 300
                """,
                timeout=8,
            )

            sorted_data = sorted(data, key=lambda item: normalize_label(item.get("teamLabel", "")))
            rows = [
                [
                    link_cell(r["teamLabel"], team_href(r["teamLabel"], filters)),
                ]
                for r in sorted_data
            ]
            try:
                elo_panel = build_elo_panel([r["teamLabel"] for r in sorted_data], years=5, max_teams=None)
            except Exception:
                elo_panel = None
            panels = [elo_panel] if elo_panel else [{"title": "Tendencia Elo", "kind": "text", "text": "Informacion no disponible para los ultimos 5 anos."}]

            return render_page(
                "teams",
                title="Equipos",
                subtitle="Listado de equipos",
                headers=["Equipo"],
                rows=rows,
                panels=no_data_panel() if not rows else panels,
            )
        except Exception as exc:
            return render_page("teams", title="Equipos", subtitle="Resumen por equipo", error=f"Error: {exc}", panels=no_data_panel())
