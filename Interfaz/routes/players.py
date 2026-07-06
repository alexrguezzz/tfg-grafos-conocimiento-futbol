from __future__ import annotations

from flask import request, session

from services.utils import format_number


def register_players_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_search = deps["get_search"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]
    match_scope_clauses = deps["match_scope_clauses"]
    filter_clauses = deps["filter_clauses"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    text_filter = deps["text_filter"]
    no_data_panel = deps["no_data_panel"]
    build_url = deps["build_url"]
    format_match_datetime = deps["format_match_datetime"]
    sparql_string = deps["sparql_string"]

    def link_cell(text: str, href: str, class_name: str = "table-link") -> dict[str, str]:
        return {"text": text or "-", "href": href, "class": class_name}

    def team_href(label: str, filters: dict[str, object]) -> str:
        return build_url("teams", filters, "", {"team": label})

    def player_href(label: str, filters: dict[str, object]) -> str:
        return build_url("players", filters, "", {"player": label})

    def match_href(match_uri: str, filters: dict[str, object], q: str = "") -> str:
        return build_url("match_detail", filters, q, {"match_uri": match_uri})

    def numeric(value: object) -> float:
        try:
            return float(str(value or "0").strip() or 0)
        except Exception:
            return 0.0

    def sum_numeric(*values: object) -> float:
        return sum(numeric(value) for value in values)

    def per(value: object, denominator: object, multiplier: float = 1.0) -> float | str:
        denominator_value = numeric(denominator)
        if denominator_value <= 0:
            return ""
        return (numeric(value) / denominator_value) * multiplier

    def first_present(*values: object) -> object:
        for value in values:
            if str(value or "").strip():
                return value
        return ""

    def display_metric(value: object, decimals: int = 0, suffix: str = "") -> str:
        return format_number(value, decimals, suffix)

    def player_stat_section_specs() -> list[tuple[str, list[tuple[str, str, int, str]]]]:
        return [
            (
                "Participacion",
                [
                    ("Partidos", "matches", 0, ""),
                    ("Minutos", "minutes", 0, ""),
                    ("Minutos/partido", "minutes_per_match", 1, ""),
                    ("Titularidades", "starts", 0, ""),
                    ("Suplencias", "substitute_appearances", 0, ""),
                    ("No jugados", "unused_matches", 0, ""),
                    ("No disponible", "unavailable_matches", 0, ""),
                    ("Sustituido", "subbed_off", 0, ""),
                    ("Capitanias", "captain_matches", 0, ""),
                ],
            ),
            (
                "Produccion ofensiva",
                [
                    ("Goles", "goals", 0, ""),
                    ("Goles/90", "goals_per90", 2, ""),
                    ("Asistencias", "assists", 0, ""),
                    ("Asistencias/90", "assists_per90", 2, ""),
                    ("Goles + asistencias", "goals_assists", 0, ""),
                    ("Goles + asistencias/90", "goals_assists_per90", 2, ""),
                    ("Goles sin penalti", "non_penalty_goals", 0, ""),
                    ("Goles sin penalti/90", "non_penalty_goals_per90", 2, ""),
                ],
            ),
            (
                "Metricas esperadas",
                [
                    ("xG", "xg", 2, ""),
                    ("xG/90", "xg_per90", 2, ""),
                    ("xG sin penalti", "non_penalty_xg", 2, ""),
                    ("xG sin penalti/90", "non_penalty_xg_per90", 2, ""),
                    ("xA", "xa", 2, ""),
                    ("xA/90", "xa_per90", 2, ""),
                    ("xG + xA", "xg_xa", 2, ""),
                    ("xG + xA/90", "xg_xa_per90", 2, ""),
                ],
            ),
            (
                "Finalizacion",
                [
                    ("Tiros", "shots", 0, ""),
                    ("Tiros/90", "shots_per90", 2, ""),
                    ("Tiros a puerta", "shots_on_target", 0, ""),
                    ("Tiros a puerta/90", "shots_on_target_per90", 2, ""),
                ],
            ),
            (
                "Creacion y construccion",
                [
                    ("Pases clave", "key_passes", 0, ""),
                    ("Pases clave/90", "key_passes_per90", 2, ""),
                    ("xA", "xa", 2, ""),
                    ("xA/90", "xa_per90", 2, ""),
                    ("xGChain", "xg_chain", 2, ""),
                    ("xGChain/90", "xg_chain_per90", 2, ""),
                    ("xGBuildup", "xg_buildup", 2, ""),
                    ("xGBuildup/90", "xg_buildup_per90", 2, ""),
                ],
            ),
            (
                "Disciplina",
                [
                    ("Amarillas", "yellow", 0, ""),
                    ("Amarillas/90", "yellow_per90", 2, ""),
                    ("Rojas", "red", 0, ""),
                    ("Rojas/90", "red_per90", 2, ""),
                    ("Faltas cometidas", "fouls_committed", 0, ""),
                    ("Faltas cometidas/90", "fouls_committed_per90", 2, ""),
                    ("Faltas recibidas", "fouls_suffered", 0, ""),
                    ("Faltas recibidas/90", "fouls_suffered_per90", 2, ""),
                ],
            ),
        ]

    def player_detail_filters() -> dict[str, object]:
        return {
            "competition": "all",
            "season": "all",
            "jornadas": [],
            "date_from": "",
            "date_to": "",
        }

    def kpi_subtitle() -> str:
        onboarding_seasons = [value for value in session.get("onboarding_seasons", []) if value]
        if onboarding_seasons:
            selected_season = ", ".join(onboarding_seasons)
            return f"Ficha del jugador en la temporada {selected_season}"

        return "Ficha del jugador en la temporada"

    def fetch_player_identity(player_label: str) -> dict[str, str]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?label ?knownAs ?fullName
            WHERE {{
              ?player a class:Player ; rdfs:label ?label .
              OPTIONAL {{ ?player prop:knownAs ?knownAs . }}
              OPTIONAL {{ ?player prop:fullName ?fullName . }}
              FILTER(
                ?label = {sparql_string(player_label)}
                || (BOUND(?knownAs) && ?knownAs = {sparql_string(player_label)})
                || (BOUND(?fullName) && ?fullName = {sparql_string(player_label)})
              )
            }}
            ORDER BY LCASE(?label)
            LIMIT 1
            """
        )
        if not rows:
            return {
                "label": player_label,
                "known_as": player_label,
                "full_name": player_label,
            }
        row = rows[0]
        label = row.get("label", "") or player_label
        known_as = row.get("knownAs", "") or label
        full_name = row.get("fullName", "") or known_as
        return {
            "label": label,
            "known_as": known_as,
            "full_name": full_name,
        }

    def fetch_player_kpis(player_label: str) -> dict[str, str]:
        scoped_filters = player_detail_filters()
        rows = run_query(
            prefixes
            + f"""
            SELECT (SUM(?appearanceValue) AS ?matches)
                   (SUM(?minutesValue) AS ?minutes)
                   (SUM(?goalValue) AS ?goals)
                   (SUM(?assistValue) AS ?assists)
                   (SUM(?yellowValue) AS ?yellow)
                   (SUM(?redValue) AS ?red)
            WHERE {{
              ?player a class:Player ; rdfs:label {sparql_string(player_label)} .
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              OPTIONAL {{ ?stats prop:matches ?appearanceRaw . }}
              OPTIONAL {{ ?stats prop:minutes ?minutesRaw . }}
              OPTIONAL {{ ?stats prop:goals ?goalsRaw . }}
              OPTIONAL {{ ?stats prop:assists ?assistsRaw . }}
              OPTIONAL {{ ?stats prop:yellowCards ?yellowRaw . }}
              OPTIONAL {{ ?stats prop:redCards ?redRaw . }}
              BIND(COALESCE(xsd:integer(?appearanceRaw), 0) AS ?appearanceValue)
              BIND(COALESCE(xsd:integer(?minutesRaw), 0) AS ?minutesValue)
              BIND(COALESCE(xsd:integer(?goalsRaw), 0) AS ?goalValue)
              BIND(COALESCE(xsd:integer(?assistsRaw), 0) AS ?assistValue)
              BIND(COALESCE(xsd:integer(?yellowRaw), 0) AS ?yellowValue)
              BIND(COALESCE(xsd:integer(?redRaw), 0) AS ?redValue)
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(scoped_filters, competition_var='?competition', season_var='?season')}
            }}
            """
        )
        row = rows[0] if rows else {}
        return {
            "matches": format_number(row.get("matches", "")),
            "minutes": format_number(row.get("minutes", "")),
            "goals": format_number(row.get("goals", "")),
            "assists": format_number(row.get("assists", "")),
            "yellow": format_number(row.get("yellow", "")),
            "red": format_number(row.get("red", "")),
        }

    def fetch_player_season_summary(player_label: str) -> dict[str, object]:
        scoped_filters = player_detail_filters()
        rows = run_query(
            prefixes
            + f"""
            SELECT (GROUP_CONCAT(DISTINCT ?teamLabel; separator=", ") AS ?teamLabels)
                   (GROUP_CONCAT(DISTINCT ?competitionLabel; separator=", ") AS ?competitionLabels)
                   (GROUP_CONCAT(DISTINCT ?seasonLabel; separator=", ") AS ?seasonLabels)
                   (SUM(?matchesValue) AS ?matches)
                   (SUM(?minutesValue) AS ?minutes)
                   (SUM(?goalsValue) AS ?goals)
                   (SUM(?xgValue) AS ?xg)
                   (SUM(?nonPenaltyGoalsValue) AS ?non_penalty_goals)
                   (SUM(?nonPenaltyXgValue) AS ?non_penalty_xg)
                   (SUM(?assistsValue) AS ?assists)
                   (SUM(?xaValue) AS ?xa)
                   (SUM(?shotsValue) AS ?shots)
                   (SUM(?keyPassesValue) AS ?key_passes)
                   (SUM(?yellowValue) AS ?yellow)
                   (SUM(?redValue) AS ?red)
                   (SUM(?xgChainValue) AS ?xg_chain)
                   (SUM(?xgBuildupValue) AS ?xg_buildup)
            WHERE {{
              ?player a class:Player ; rdfs:label {sparql_string(player_label)} .
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:correspondsToTeam ?team ;
                        prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              ?team rdfs:label ?teamLabel .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              OPTIONAL {{ ?stats prop:matches ?matchesRaw . }}
              OPTIONAL {{ ?stats prop:minutes ?minutesRaw . }}
              OPTIONAL {{ ?stats prop:goals ?goalsRaw . }}
              OPTIONAL {{ ?stats prop:xg ?xgRaw . }}
              OPTIONAL {{ ?stats prop:nonPenaltyGoals ?nonPenaltyGoalsRaw . }}
              OPTIONAL {{ ?stats prop:nonPenaltyXg ?nonPenaltyXgRaw . }}
              OPTIONAL {{ ?stats prop:assists ?assistsRaw . }}
              OPTIONAL {{ ?stats prop:xa ?xaRaw . }}
              OPTIONAL {{ ?stats prop:shots ?shotsRaw . }}
              OPTIONAL {{ ?stats prop:keyPasses ?keyPassesRaw . }}
              OPTIONAL {{ ?stats prop:yellowCards ?yellowRaw . }}
              OPTIONAL {{ ?stats prop:redCards ?redRaw . }}
              OPTIONAL {{ ?stats prop:xgChain ?xgChainRaw . }}
              OPTIONAL {{ ?stats prop:xgBuildup ?xgBuildupRaw . }}
              BIND(COALESCE(xsd:integer(?matchesRaw), 0) AS ?matchesValue)
              BIND(COALESCE(xsd:integer(?minutesRaw), 0) AS ?minutesValue)
              BIND(COALESCE(xsd:integer(?goalsRaw), 0) AS ?goalsValue)
              BIND(COALESCE(xsd:double(?xgRaw), 0) AS ?xgValue)
              BIND(COALESCE(xsd:integer(?nonPenaltyGoalsRaw), 0) AS ?nonPenaltyGoalsValue)
              BIND(COALESCE(xsd:double(?nonPenaltyXgRaw), 0) AS ?nonPenaltyXgValue)
              BIND(COALESCE(xsd:integer(?assistsRaw), 0) AS ?assistsValue)
              BIND(COALESCE(xsd:double(?xaRaw), 0) AS ?xaValue)
              BIND(COALESCE(xsd:integer(?shotsRaw), 0) AS ?shotsValue)
              BIND(COALESCE(xsd:integer(?keyPassesRaw), 0) AS ?keyPassesValue)
              BIND(COALESCE(xsd:integer(?yellowRaw), 0) AS ?yellowValue)
              BIND(COALESCE(xsd:integer(?redRaw), 0) AS ?redValue)
              BIND(COALESCE(xsd:double(?xgChainRaw), 0) AS ?xgChainValue)
              BIND(COALESCE(xsd:double(?xgBuildupRaw), 0) AS ?xgBuildupValue)
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(scoped_filters, competition_var='?competition', season_var='?season')}
            }}
            """
        )
        return rows[0] if rows else {}

    def fetch_player_match_summary(player_label: str) -> dict[str, object]:
        scoped_filters = player_detail_filters()
        rows = run_query(
            prefixes
            + f"""
            SELECT (COUNT(DISTINCT ?m) AS ?match_records)
                   (SUM(?appearanceValue) AS ?appearances)
                   (SUM(?minutesValue) AS ?minutes)
                   (SUM(?startsValue) AS ?starts)
                   (SUM(?substituteValue) AS ?substitute_appearances)
                   (SUM(?unusedValue) AS ?unused_matches)
                   (SUM(?unavailableValue) AS ?unavailable_matches)
                   (SUM(?subbedOffValue) AS ?subbed_off)
                   (SUM(?captainValue) AS ?captain_matches)
                   (SUM(?goalsValue) AS ?goals)
                   (SUM(?assistsValue) AS ?assists)
                   (SUM(?shotsValue) AS ?shots)
                   (SUM(?shotsOnTargetValue) AS ?shots_on_target)
                   (SUM(?xgValue) AS ?xg)
                   (SUM(?xgChainValue) AS ?xg_chain)
                   (SUM(?xgBuildupValue) AS ?xg_buildup)
                   (SUM(?xaValue) AS ?xa)
                   (SUM(?keyPassesValue) AS ?key_passes)
                   (SUM(?foulsCommittedValue) AS ?fouls_committed)
                   (SUM(?foulsSufferedValue) AS ?fouls_suffered)
                   (SUM(?yellowValue) AS ?yellow)
                   (SUM(?redValue) AS ?red)
            WHERE {{
              ?player a class:Player ; rdfs:label {sparql_string(player_label)} .
              ?m a class:Match ;
                 prop:hasTeamMatchParticipation ?pmpTeamParticipation .
              ?pmpTeamParticipation prop:hasPlayerMatchParticipation ?pmp .
              OPTIONAL {{ ?m prop:date ?dateRaw . }}
              OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
              OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
              OPTIONAL {{ ?m prop:week ?legacyWeek . }}
              BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
              BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
              FILTER(BOUND(?date))
              ?pmp prop:correspondsToPlayer ?player .
              OPTIONAL {{ ?pmp prop:participationStatus ?participationStatusRaw . }}
              OPTIONAL {{ ?pmp prop:isCaptain ?isCaptainRaw . }}
              OPTIONAL {{ ?pmp prop:subOut ?subOutRaw . }}
              OPTIONAL {{ ?pmp prop:appearances ?appearanceRaw . }}
              OPTIONAL {{ ?pmp prop:minutes ?minutesRaw . }}
              OPTIONAL {{ ?pmp prop:totalGoals ?goalsRaw . }}
              OPTIONAL {{ ?pmp prop:goalAssists ?assistsRaw . }}
              OPTIONAL {{ ?pmp prop:totalShots ?shotsRaw . }}
              OPTIONAL {{ ?pmp prop:shotsOnTarget ?shotsOnTargetRaw . }}
              OPTIONAL {{ ?pmp prop:xg ?xgRaw . }}
              OPTIONAL {{ ?pmp prop:xg_chain ?xgChainRaw . }}
              OPTIONAL {{ ?pmp prop:xg_buildup ?xgBuildupRaw . }}
              OPTIONAL {{ ?pmp prop:xa ?xaRaw . }}
              OPTIONAL {{ ?pmp prop:keyPasses ?keyPassesRaw . }}
              OPTIONAL {{ ?pmp prop:foulsCommitted ?foulsCommittedRaw . }}
              OPTIONAL {{ ?pmp prop:foulsSuffered ?foulsSufferedRaw . }}
              OPTIONAL {{ ?pmp prop:yellowCards ?yellowRaw . }}
              OPTIONAL {{ ?pmp prop:redCards ?redRaw . }}
              BIND(COALESCE(xsd:integer(?appearanceRaw), 0) AS ?appearanceValue)
              BIND(COALESCE(xsd:integer(?minutesRaw), 0) AS ?minutesValue)
              BIND(IF(BOUND(?participationStatusRaw) && LCASE(STR(?participationStatusRaw)) = "titular", 1, 0) AS ?startsValue)
              BIND(IF(BOUND(?participationStatusRaw) && LCASE(STR(?participationStatusRaw)) = "suplente", 1, 0) AS ?substituteValue)
              BIND(IF(BOUND(?participationStatusRaw) && LCASE(STR(?participationStatusRaw)) IN ("no jugado", "no_jugado"), 1, 0) AS ?unusedValue)
              BIND(IF(BOUND(?participationStatusRaw) && LCASE(STR(?participationStatusRaw)) IN ("no disponible", "no_disponible", "no convocado", "no_convocado"), 1, 0) AS ?unavailableValue)
              BIND(IF(BOUND(?subOutRaw) && LCASE(STR(?subOutRaw)) != "end", 1, 0) AS ?subbedOffValue)
              BIND(IF(BOUND(?isCaptainRaw) && ?isCaptainRaw = true, 1, 0) AS ?captainValue)
              BIND(COALESCE(xsd:integer(?goalsRaw), 0) AS ?goalsValue)
              BIND(COALESCE(xsd:integer(?assistsRaw), 0) AS ?assistsValue)
              BIND(COALESCE(xsd:double(?shotsRaw), 0) AS ?shotsValue)
              BIND(COALESCE(xsd:double(?shotsOnTargetRaw), 0) AS ?shotsOnTargetValue)
              BIND(COALESCE(xsd:double(?xgRaw), 0) AS ?xgValue)
              BIND(COALESCE(xsd:double(?xgChainRaw), 0) AS ?xgChainValue)
              BIND(COALESCE(xsd:double(?xgBuildupRaw), 0) AS ?xgBuildupValue)
              BIND(COALESCE(xsd:double(?xaRaw), 0) AS ?xaValue)
              BIND(COALESCE(xsd:double(?keyPassesRaw), 0) AS ?keyPassesValue)
              BIND(COALESCE(xsd:double(?foulsCommittedRaw), 0) AS ?foulsCommittedValue)
              BIND(COALESCE(xsd:double(?foulsSufferedRaw), 0) AS ?foulsSufferedValue)
              BIND(COALESCE(xsd:double(?yellowRaw), 0) AS ?yellowValue)
              BIND(COALESCE(xsd:double(?redRaw), 0) AS ?redValue)
              {match_scope_clauses(scoped_filters, match_var='?m', date_var='?date', week_var='?week')}
            }}
            """
        )
        return rows[0] if rows else {}

    def build_player_stats(player_label: str) -> dict[str, object]:
        season_stats = fetch_player_season_summary(player_label)
        match_stats = fetch_player_match_summary(player_label)
        stats = {
            **match_stats,
            "matches": first_present(season_stats.get("matches", ""), match_stats.get("appearances", ""), match_stats.get("match_records", "")),
            "appearances": first_present(match_stats.get("appearances", ""), season_stats.get("matches", "")),
            "minutes": first_present(season_stats.get("minutes", ""), match_stats.get("minutes", "")),
            "goals": first_present(season_stats.get("goals", ""), match_stats.get("goals", "")),
            "non_penalty_goals": season_stats.get("non_penalty_goals", ""),
            "assists": first_present(season_stats.get("assists", ""), match_stats.get("assists", "")),
            "shots": first_present(season_stats.get("shots", ""), match_stats.get("shots", "")),
            "xg": first_present(season_stats.get("xg", ""), match_stats.get("xg", "")),
            "non_penalty_xg": season_stats.get("non_penalty_xg", ""),
            "xa": first_present(season_stats.get("xa", ""), match_stats.get("xa", "")),
            "key_passes": first_present(season_stats.get("key_passes", ""), match_stats.get("key_passes", "")),
            "yellow": first_present(season_stats.get("yellow", ""), match_stats.get("yellow", "")),
            "red": first_present(season_stats.get("red", ""), match_stats.get("red", "")),
            "xg_chain": first_present(season_stats.get("xg_chain", ""), match_stats.get("xg_chain", "")),
            "xg_buildup": first_present(season_stats.get("xg_buildup", ""), match_stats.get("xg_buildup", "")),
        }
        minutes = stats.get("minutes", "")
        stats.update(
            {
                "goals_assists": sum_numeric(stats.get("goals"), stats.get("assists")),
                "xg_xa": sum_numeric(stats.get("xg"), stats.get("xa")),
                "minutes_per_match": per(stats.get("minutes"), stats.get("matches")),
                "goals_per90": per(stats.get("goals"), minutes, 90),
                "non_penalty_goals_per90": per(stats.get("non_penalty_goals"), minutes, 90),
                "assists_per90": per(stats.get("assists"), minutes, 90),
                "goals_assists_per90": per(sum_numeric(stats.get("goals"), stats.get("assists")), minutes, 90),
                "xg_per90": per(stats.get("xg"), minutes, 90),
                "non_penalty_xg_per90": per(stats.get("non_penalty_xg"), minutes, 90),
                "xa_per90": per(stats.get("xa"), minutes, 90),
                "xg_xa_per90": per(sum_numeric(stats.get("xg"), stats.get("xa")), minutes, 90),
                "shots_per90": per(stats.get("shots"), minutes, 90),
                "shots_on_target_per90": per(stats.get("shots_on_target"), minutes, 90),
                "key_passes_per90": per(stats.get("key_passes"), minutes, 90),
                "xg_chain_per90": per(stats.get("xg_chain"), minutes, 90),
                "xg_buildup_per90": per(stats.get("xg_buildup"), minutes, 90),
                "yellow_per90": per(stats.get("yellow"), minutes, 90),
                "red_per90": per(stats.get("red"), minutes, 90),
                "fouls_committed_per90": per(stats.get("fouls_committed"), minutes, 90),
                "fouls_suffered_per90": per(stats.get("fouls_suffered"), minutes, 90),
            }
        )
        return stats

    def build_player_stat_sections(stats: dict[str, object]) -> list[dict[str, object]]:
        sections: list[dict[str, object]] = []
        for title, specs in player_stat_section_specs():
            sections.append(
                {
                    "title": title,
                    "rows": [
                        [label, display_metric(stats.get(key), decimals, suffix)]
                        for label, key, decimals, suffix in specs
                    ],
                }
            )
        return sections

    def fetch_season_stats(player_label: str, filters: dict[str, object]) -> list[list[object]]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?competitionLabel ?seasonLabel ?teamLabel ?matches ?minutes ?goals ?penaltyGoals ?assists ?shots ?xg ?nonPenaltyXg ?xa ?keyPasses ?xgChain ?xgBuildup ?yellow ?red
            WHERE {{
              ?player a class:Player ; rdfs:label {sparql_string(player_label)} .
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:correspondsToTeam ?team ;
                        prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              ?team rdfs:label ?teamLabel .
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
              BIND(COALESCE(xsd:integer(?goals), 0) AS ?goalsValue)
              BIND(COALESCE(xsd:integer(?nonPenaltyGoals), 0) AS ?nonPenaltyGoalsValue)
              BIND(IF(?goalsValue > ?nonPenaltyGoalsValue, ?goalsValue - ?nonPenaltyGoalsValue, 0) AS ?penaltyGoals)
              {onboarding_resource_clauses(competition_var='?competition')}
            }}
            ORDER BY DESC(?seasonLabel) ?competitionLabel ?teamLabel
            """
        )
        return [
            [
                row.get("competitionLabel", "-"),
                row.get("seasonLabel", "-"),
                link_cell(row.get("teamLabel", "-"), team_href(row.get("teamLabel", ""), filters)),
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
            for row in rows
        ]

    def classify_participation(row: dict[str, str]) -> str:
        status = str(row.get("participationStatus", "")).strip().lower()
        minutes = float(row.get("minutes", "0") or 0)
        appearances = float(row.get("appearances", "0") or 0)
        sub_in = str(row.get("subIn", "")).strip().lower()

        if status == "titular":
            return "starter"
        if status in {"no disponible", "no_disponible", "no convocado", "no_convocado"}:
            return "unavailable"
        if status in {"no jugado", "no_jugado"}:
            return "unused"
        if status == "suplente" and (minutes > 0 or appearances > 0 or (sub_in and sub_in != "start")):
            return "bench"
        if status == "suplente":
            return "unused"
        return "all"

    def status_label(key: str) -> str:
        return {
            "starter": "Titular",
            "bench": "Suplente",
            "unused": "No jugado",
            "unavailable": "No disponible",
            "all": "-",
        }.get(key, "-")

    def fetch_player_matches(player_label: str, filters: dict[str, object]) -> dict[str, list[list[object]]]:
        scoped_filters = player_detail_filters()
        rows = run_query(
            prefixes
            + f"""
            SELECT ?matchUri ?date ?dateTime ?week ?teamLabel ?homeLabel ?awayLabel ?hs ?as ?isHome ?participationStatus ?position ?subIn ?appearances ?minutes ?reason
            WHERE {{
              ?player a class:Player ; rdfs:label {sparql_string(player_label)} .
              ?pmp prop:correspondsToPlayer ?player ;
                   prop:belongsToTeamMatchParticipation ?playerTeamParticipation .
              ?playerTeamParticipation prop:correspondsToTeam ?team .
              ?m a class:Match ;
                 prop:hasTeamMatchParticipation ?playerTeamParticipation ;
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
              BIND(?home = ?team AS ?isHome)
              ?team rdfs:label ?teamLabel .
              ?home rdfs:label ?homeLabel .
              ?away rdfs:label ?awayLabel .

              OPTIONAL {{ ?pmp prop:participationStatus ?participationStatus . }}
              OPTIONAL {{ ?pmp prop:position ?position . }}
              OPTIONAL {{ ?pmp prop:subIn ?subIn . }}
              OPTIONAL {{ ?pmp prop:appearances ?appearances . }}
              OPTIONAL {{ ?pmp prop:minutes ?minutes . }}
              OPTIONAL {{ ?pmp prop:reason ?reason . }}

              {match_scope_clauses(scoped_filters, match_var='?m', date_var='?date', week_var='?week')}
              BIND(COALESCE(?dateTime, ?date) AS ?matchSort)
            }}
            ORDER BY DESC(?matchSort)
            LIMIT 800
            """
        )
        grouped: dict[str, list[list[object]]] = {
            "all": [],
            "starter": [],
            "bench": [],
            "unused": [],
            "unavailable": [],
        }
        for row in rows:
            key = classify_participation(row)
            is_home = str(row.get("isHome", "")).lower() == "true"
            rival = row.get("awayLabel", "-") if is_home else row.get("homeLabel", "-")
            table_row = [
                format_match_datetime(row.get("dateTime", ""), row.get("date", "")),
                row.get("week", "-"),
                link_cell(row.get("teamLabel", "-"), team_href(row.get("teamLabel", ""), filters)),
                link_cell(rival, team_href(rival, filters)),
                "Local" if is_home else "Visitante",
                f"{row.get('hs', '-')} - {row.get('as', '-')}",
                status_label(key),
                format_number(row.get("minutes", "")),
                (row.get("reason") or "-") if key == "unavailable" else "-",
                link_cell("Ver", match_href(row.get("matchUri", ""), filters), "btn table-btn"),
            ]
            grouped["all"].append(table_row)
            if key in grouped:
                grouped[key].append(table_row)
        return grouped

    @app.route("/players")
    def players():
        q = get_search()
        filters = get_filters()
        player_label = request.args.get("player", "").strip()

        if not onboarding_complete():
            return render_page("players", title="Jugadores", subtitle="Seleccion inicial de ligas y temporadas", panels=no_data_panel())

        try:
            if player_label:
                player_identity = fetch_player_identity(player_label)
                lookup_label = player_identity["label"]
                kpis = fetch_player_kpis(lookup_label)
                player_stats = build_player_stats(lookup_label)
                cards = [
                    {"label": "Partidos", "value": kpis["matches"]},
                    {"label": "Minutos totales", "value": kpis["minutes"]},
                    {"label": "Goles", "value": kpis["goals"]},
                    {"label": "Asistencias", "value": kpis["assists"]},
                    {
                        "label": "Tarjetas",
                        "value": str(int(float(kpis["yellow"] or 0)) + int(float(kpis["red"] or 0))),
                        "meta": [
                            {"title": "Amarillas", "class": "is-yellow-card", "value": kpis["yellow"]},
                            {"title": "Rojas", "class": "is-red-card", "value": kpis["red"]},
                        ],
                    },
                ]
                player_detail = {
                    "known_as": player_identity["known_as"],
                    "full_name": player_identity["full_name"],
                    "stat_headers": ["Metrica", "Valor"],
                    "stat_sections": build_player_stat_sections(player_stats),
                    "season_headers": ["Competicion", "Temporada", "Equipo", "PJ", "Min", "G", "G pen.", "A", "Tiros", "xG", "xG sin pen.", "xA", "Pases clave", "xGChain", "xGBuildup", "TA", "TR"],
                    "season_rows": fetch_season_stats(lookup_label, filters),
                    "match_headers": ["Fecha y hora", "Jornada", "Equipo", "Rival", "Condicion", "Marcador", "Estado", "Min", "Motivo", "Detalle"],
                    "matches_by_status": fetch_player_matches(lookup_label, filters),
                    "status_filters": [
                        {"key": "all", "label": "Todos"},
                        {"key": "starter", "label": "Titular"},
                        {"key": "bench", "label": "Suplente"},
                        {"key": "unused", "label": "No jugados"},
                        {"key": "unavailable", "label": "No disponible"},
                    ],
                }
                return render_page(
                    "players",
                    title=player_identity["known_as"],
                    subtitle=kpi_subtitle(),
                    cards=cards,
                    panels=[],
                    extra_context={"player_detail": player_detail},
                )

            data = run_query(
                prefixes
                + f"""
                SELECT ?label
                WHERE {{
                  ?stats a class:PlayerCompetitionSeasonStats ;
                         prop:correspondsToPlayer ?p ;
                         prop:belongsToTeamCompetitionSeason ?statsTcs .
                  ?statsTcs prop:belongsToCompetition ?competition ;
                            prop:belongsToSeason ?season .
                  ?p rdfs:label ?label .
                  {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
                  {filter_clauses(filters, competition_var='?competition', season_var='?season')}
                  {text_filter(q, '?label')}
                }}
                GROUP BY ?label
                ORDER BY ?label
                """
            )
            if not data:
                return render_page(
                    "players",
                    title="Jugadores",
                    subtitle="Estado de datos",
                    panels=no_data_panel(),
                )

            rows = [[link_cell(r["label"], player_href(r["label"], filters))] for r in data]
            return render_page(
                "players",
                title="Jugadores",
                subtitle="Listado de jugadores",
                headers=["Jugador"],
                rows=rows,
            )
        except Exception as exc:
            return render_page(
                "players",
                title="Jugadores",
                subtitle="Estado de datos",
                panels=no_data_panel(),
                error=f"Error: {exc}",
            )
