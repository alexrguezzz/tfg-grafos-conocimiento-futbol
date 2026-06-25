from __future__ import annotations

from html import escape

from flask import request

from services.utils import format_number, format_numeric_plain


def register_compare_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    match_scope_clauses = deps["match_scope_clauses"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    sparql_string = deps["sparql_string"]
    filter_clauses = deps["filter_clauses"]
    onboarding_resource_clauses = deps["onboarding_resource_clauses"]
    no_data_panel = deps["no_data_panel"]
    build_url = deps["build_url"]
    build_elo_panel = deps["build_elo_panel"]

    def link_cell(text: str, href: str, class_name: str = "table-link") -> dict[str, str]:
        return {"text": text or "-", "href": href, "class": class_name}

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

    def team_cell(label: str, filters: dict[str, object]) -> dict[str, str]:
        return link_cell(label, build_url("teams", filters, "", {"team": label}))

    def player_cell(label: str, filters: dict[str, object]) -> dict[str, str]:
        return link_cell(label, build_url("players", filters, "", {"player": label}))

    def compare_value_cell(
        value: object,
        other_value: object,
        decimals: int = 0,
        suffix: str = "",
        class_name: str = "",
    ) -> dict[str, str]:
        value_number = numeric(value)
        other_number = numeric(other_value)
        is_winner = value_number > other_number
        is_tie = abs(value_number - other_number) < 0.0000001
        display = escape(format_number(value, decimals, suffix))
        css_class = f"compare-metric-cell {class_name}".strip()
        value_class = "compare-metric-value is-winner" if is_winner and not is_tie else "compare-metric-value"
        diff_html = ""
        if is_winner and not is_tie:
            diff = abs(value_number - other_number)
            diff_html = f'<span class="compare-metric-diff">(+{escape(format_number(diff, decimals, suffix))})</span>'
        return {
            "html": (
                f'<span class="{css_class}">'
                f'<span class="{value_class}">{display}</span>'
                f"{diff_html}"
                "</span>"
            )
        }

    def build_metric_rows(
        stats_a: dict[str, object],
        stats_b: dict[str, object],
        specs: list[tuple[str, str, int, str]],
    ) -> list[list[object]]:
        rows: list[list[object]] = []
        for label, key, decimals, suffix in specs:
            rows.append(
                [
                    compare_value_cell(stats_a.get(key), stats_b.get(key), decimals, suffix, "is-left"),
                    label,
                    compare_value_cell(stats_b.get(key), stats_a.get(key), decimals, suffix, "is-right"),
                ]
            )
        return rows

    def build_metric_sections(
        stats_a: dict[str, object],
        stats_b: dict[str, object],
        section_specs: list[tuple[str, list[tuple[str, str, int, str]]]],
    ) -> list[dict[str, object]]:
        return [
            {"title": title, "rows": build_metric_rows(stats_a, stats_b, specs)}
            for title, specs in section_specs
            if specs
        ]

    def unique_metric_specs(
        specs: list[tuple[str, str, int, str]],
        excluded_keys: set[str] | None = None,
    ) -> list[tuple[str, str, int, str]]:
        used_keys = set(excluded_keys or set())
        unique_specs: list[tuple[str, str, int, str]] = []
        for spec in specs:
            key = spec[1]
            if key in used_keys:
                continue
            used_keys.add(key)
            unique_specs.append(spec)
        return unique_specs

    def get_team_options(filters: dict[str, object]) -> list[str]:
        rows = run_query(
            prefixes
            + f"""
            SELECT DISTINCT ?label
            WHERE {{
              ?tcs a class:TeamCompetitionSeason ;
                   prop:correspondsToTeam ?team ;
                   prop:belongsToCompetition ?competition ;
                   prop:belongsToSeason ?season .
              ?team rdfs:label ?label .
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season')}
            }}
            ORDER BY LCASE(?label)
            LIMIT 500
            """
        )
        return [r["label"] for r in rows if r.get("label")]

    def get_player_options(filters: dict[str, object]) -> list[str]:
        rows = run_query(
            prefixes
            + f"""
            SELECT DISTINCT ?label
            WHERE {{
              ?stats a class:PlayerCompetitionSeasonStats ;
                     prop:correspondsToPlayer ?player ;
                     prop:belongsToTeamCompetitionSeason ?statsTcs .
              ?statsTcs prop:belongsToCompetition ?competition ;
                        prop:belongsToSeason ?season .
              ?player rdfs:label ?label .
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season')}
            }}
            ORDER BY LCASE(?label)
            LIMIT 900
            """
        )
        return [r["label"] for r in rows if r.get("label")]

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
            return {"label": player_label, "known_as": player_label, "full_name": player_label}
        row = rows[0]
        label = row.get("label", "") or player_label
        known_as = row.get("knownAs", "") or label
        full_name = row.get("fullName", "") or known_as
        return {"label": label, "known_as": known_as, "full_name": full_name}

    def fetch_latest_elo(team_label: str) -> dict[str, object]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?elo ?rank ?level ?d
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?record a class:EloRecord ;
                      prop:correspondsToTeam ?team ;
                      prop:elo ?elo .
              OPTIONAL {{ ?record prop:rank ?rank . }}
              OPTIONAL {{ ?record prop:level ?level . }}
              OPTIONAL {{ ?record prop:dateFrom ?dateFrom . }}
              OPTIONAL {{ ?record prop:dateTo ?dateTo . }}
              BIND(COALESCE(?dateFrom, ?dateTo) AS ?d)
              FILTER(BOUND(?d))
            }}
            ORDER BY DESC(?d)
            LIMIT 1
            """
        )
        return rows[0] if rows else {}

    def fetch_team_season_stats(team_label: str, filters: dict[str, object]) -> dict[str, object]:
        rows = run_query(
            prefixes
            + f"""
            SELECT ?competitionLabel ?seasonLabel ?country ?position ?pts ?mp ?w ?d ?l ?gf ?ga ?gd
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?tcs a class:TeamCompetitionSeason ;
                   prop:correspondsToTeam ?team ;
                   prop:belongsToCompetition ?competition ;
                   prop:belongsToSeason ?season .
              ?competition rdfs:label ?competitionLabel .
              ?season rdfs:label ?seasonLabel .
              OPTIONAL {{ ?team prop:country ?country . }}
              OPTIONAL {{ ?tcs prop:position ?position . }}
              OPTIONAL {{ ?tcs prop:points ?pts . }}
              OPTIONAL {{ ?tcs prop:matchesPlayed ?mp . }}
              OPTIONAL {{ ?tcs prop:wins ?w . }}
              OPTIONAL {{ ?tcs prop:draws ?d . }}
              OPTIONAL {{ ?tcs prop:losses ?l . }}
              OPTIONAL {{ ?tcs prop:goalsFor ?gf . }}
              OPTIONAL {{ ?tcs prop:goalsAgainst ?ga . }}
              OPTIONAL {{ ?tcs prop:goalDifference ?gd . }}
              {filter_clauses(filters, competition_label='?competitionLabel', season_label='?seasonLabel')}
            }}
            ORDER BY DESC(?seasonLabel) ?competitionLabel
            LIMIT 1
            """
        )
        return rows[0] if rows else {}

    def team_season_context_clauses(season_stats: dict[str, object]) -> str:
        clauses: list[str] = []
        competition_label = str(season_stats.get("competitionLabel", "") or "").strip()
        season_label = str(season_stats.get("seasonLabel", "") or "").strip()
        if competition_label:
            clauses.append(f"FILTER(?matchCompetitionLabel = {sparql_string(competition_label)})")
        if season_label:
            clauses.append(f"FILTER(?matchSeasonLabel = {sparql_string(season_label)})")
        return "\n              ".join(clauses)

    def fetch_team_match_stats(
        team_label: str,
        filters: dict[str, object],
        season_stats: dict[str, object],
    ) -> dict[str, object]:
        rows = run_query(
            prefixes
            + f"""
            SELECT (COUNT(DISTINCT ?m) AS ?match_records)
                   (SUM(?xgValue) AS ?xg)
                   (SUM(?shotsValue) AS ?shots)
                   (SUM(?shotsOnTargetValue) AS ?shots_on_target)
                   (SUM(?cornersValue) AS ?corners)
                   (SUM(?passesValue) AS ?passes)
                   (SUM(?accuratePassesValue) AS ?accurate_passes)
                   (SUM(?crossesValue) AS ?crosses)
                   (SUM(?accurateCrossesValue) AS ?accurate_crosses)
                   (SUM(?longBallsValue) AS ?long_balls)
                   (SUM(?accurateLongBallsValue) AS ?accurate_long_balls)
                   (SUM(?tacklesValue) AS ?tackles)
                   (SUM(?interceptionsValue) AS ?interceptions)
                   (SUM(?clearancesValue) AS ?clearances)
                   (SUM(?foulsValue) AS ?fouls)
                   (SUM(?yellowValue) AS ?yellow)
                   (SUM(?redValue) AS ?red)
                   (SUM(?penaltyGoalsValue) AS ?penalty_goals)
                   (SUM(?penaltyShotsValue) AS ?penalty_shots)
                   (SUM(?oppXgValue) AS ?xg_against)
                   (SUM(?oppShotsValue) AS ?shots_conceded)
                   (SUM(?oppShotsOnTargetValue) AS ?shots_on_target_conceded)
                   (SUM(?oppPenaltyShotsValue) AS ?penalties_committed)
                   (AVG(?possessionValue) AS ?possession)
                   (AVG(?ppdaValue) AS ?ppda)
            WHERE {{
              ?team a class:Team ; rdfs:label {sparql_string(team_label)} .
              ?m a class:Match ;
                 prop:hasTeamMatchParticipation ?p ;
                 prop:hasTeamMatchParticipation ?oppP ;
                 prop:belongsToCompetition ?matchCompetition ;
                 prop:belongsToSeason ?matchSeason .
              ?matchCompetition rdfs:label ?matchCompetitionLabel .
              ?matchSeason rdfs:label ?matchSeasonLabel .
              ?p prop:correspondsToTeam ?team .
              ?oppP prop:correspondsToTeam ?opponent .
              FILTER(?oppP != ?p)

              OPTIONAL {{ ?m prop:date ?dateRaw . }}
              OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
              OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
              OPTIONAL {{ ?m prop:week ?legacyWeek . }}
              BIND(COALESCE(?dateRaw, ?legacyDate) AS ?date)
              BIND(COALESCE(?matchDay, ?legacyWeek) AS ?week)
              FILTER(BOUND(?date))

              OPTIONAL {{ ?p prop:xg ?xgRaw . }}
              OPTIONAL {{ ?p prop:totalShots ?shotsRaw . }}
              OPTIONAL {{ ?p prop:shotsOnTarget ?shotsOnTargetRaw . }}
              OPTIONAL {{ ?p prop:wonCorners ?cornersRaw . }}
              OPTIONAL {{ ?p prop:totalPasses ?passesRaw . }}
              OPTIONAL {{ ?p prop:accuratePasses ?accuratePassesRaw . }}
              OPTIONAL {{ ?p prop:totalCrosses ?crossesRaw . }}
              OPTIONAL {{ ?p prop:accurateCrosses ?accurateCrossesRaw . }}
              OPTIONAL {{ ?p prop:totalLongBalls ?longBallsRaw . }}
              OPTIONAL {{ ?p prop:accurateLongBalls ?accurateLongBallsRaw . }}
              OPTIONAL {{ ?p prop:totalTackles ?tacklesRaw . }}
              OPTIONAL {{ ?p prop:interceptions ?interceptionsRaw . }}
              OPTIONAL {{ ?p prop:totalClearance ?clearancesRaw . }}
              OPTIONAL {{ ?p prop:foulsCommitted ?foulsRaw . }}
              OPTIONAL {{ ?p prop:yellowCards ?yellowRaw . }}
              OPTIONAL {{ ?p prop:redCards ?redRaw . }}
              OPTIONAL {{ ?p prop:penaltyKickGoals ?penaltyGoalsRaw . }}
              OPTIONAL {{ ?p prop:penaltyKickShots ?penaltyShotsRaw . }}
              OPTIONAL {{ ?p prop:possessionPct ?possessionRaw . }}
              OPTIONAL {{ ?p prop:ppda ?ppdaRaw . }}
              OPTIONAL {{ ?oppP prop:xg ?oppXgRaw . }}
              OPTIONAL {{ ?oppP prop:totalShots ?oppShotsRaw . }}
              OPTIONAL {{ ?oppP prop:shotsOnTarget ?oppShotsOnTargetRaw . }}
              OPTIONAL {{ ?oppP prop:penaltyKickShots ?oppPenaltyShotsRaw . }}

              BIND(COALESCE(xsd:double(?xgRaw), 0) AS ?xgValue)
              BIND(COALESCE(xsd:double(?shotsRaw), 0) AS ?shotsValue)
              BIND(COALESCE(xsd:double(?shotsOnTargetRaw), 0) AS ?shotsOnTargetValue)
              BIND(COALESCE(xsd:double(?cornersRaw), 0) AS ?cornersValue)
              BIND(COALESCE(xsd:double(?passesRaw), 0) AS ?passesValue)
              BIND(COALESCE(xsd:double(?accuratePassesRaw), 0) AS ?accuratePassesValue)
              BIND(COALESCE(xsd:double(?crossesRaw), 0) AS ?crossesValue)
              BIND(COALESCE(xsd:double(?accurateCrossesRaw), 0) AS ?accurateCrossesValue)
              BIND(COALESCE(xsd:double(?longBallsRaw), 0) AS ?longBallsValue)
              BIND(COALESCE(xsd:double(?accurateLongBallsRaw), 0) AS ?accurateLongBallsValue)
              BIND(COALESCE(xsd:double(?tacklesRaw), 0) AS ?tacklesValue)
              BIND(COALESCE(xsd:double(?interceptionsRaw), 0) AS ?interceptionsValue)
              BIND(COALESCE(xsd:double(?clearancesRaw), 0) AS ?clearancesValue)
              BIND(COALESCE(xsd:double(?foulsRaw), 0) AS ?foulsValue)
              BIND(COALESCE(xsd:double(?yellowRaw), 0) AS ?yellowValue)
              BIND(COALESCE(xsd:double(?redRaw), 0) AS ?redValue)
              BIND(COALESCE(xsd:double(?penaltyGoalsRaw), 0) AS ?penaltyGoalsValue)
              BIND(COALESCE(xsd:double(?penaltyShotsRaw), 0) AS ?penaltyShotsValue)
              BIND(COALESCE(xsd:double(?possessionRaw), 0) AS ?possessionValue)
              BIND(COALESCE(xsd:double(?ppdaRaw), 0) AS ?ppdaValue)
              BIND(COALESCE(xsd:double(?oppXgRaw), 0) AS ?oppXgValue)
              BIND(COALESCE(xsd:double(?oppShotsRaw), 0) AS ?oppShotsValue)
              BIND(COALESCE(xsd:double(?oppShotsOnTargetRaw), 0) AS ?oppShotsOnTargetValue)
              BIND(COALESCE(xsd:double(?oppPenaltyShotsRaw), 0) AS ?oppPenaltyShotsValue)

              {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
              {team_season_context_clauses(season_stats)}
            }}
            """
        )
        return rows[0] if rows else {}

    def fetch_team_player_count(team_label: str, filters: dict[str, object]) -> int:
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
              {onboarding_resource_clauses(competition_var='?competition', season_var='?season')}
              {filter_clauses(filters, competition_var='?competition', season_var='?season')}
            }}
            """
        )
        return int(numeric(rows[0].get("players", ""))) if rows else 0

    def build_team_compare_item(team_label: str, filters: dict[str, object]) -> dict[str, object]:
        season_stats = fetch_team_season_stats(team_label, filters)
        match_stats = fetch_team_match_stats(team_label, filters, season_stats)
        elo = fetch_latest_elo(team_label)
        elo_text = format_numeric_plain(elo.get("elo", ""))
        player_count = fetch_team_player_count(team_label, filters)
        matches = season_stats.get("mp", "")
        match_records = first_present(match_stats.get("match_records", ""), matches)
        points = season_stats.get("pts", "")
        wins = season_stats.get("w", "")
        draws = season_stats.get("d", "")
        losses = season_stats.get("l", "")
        goals_for = season_stats.get("gf", "")
        goals_against = season_stats.get("ga", "")
        goal_difference = season_stats.get("gd", "")
        stats = {
            "elo": elo_text,
            "elo_rank": elo.get("rank", ""),
            "elo_level": elo.get("level", ""),
            "position": season_stats.get("position", ""),
            "players": player_count,
            "matches": matches,
            "points": points,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "goal_difference": goal_difference,
            "goals_total": sum_numeric(goals_for, goals_against),
            "points_per_match": per(points, matches),
            "points_pct": per(points, numeric(matches) * 3, 100),
            "win_pct": per(wins, matches, 100),
            "draw_pct": per(draws, matches, 100),
            "loss_pct": per(losses, matches, 100),
            "goals_for_per_match": per(goals_for, matches),
            "goals_against_per_match": per(goals_against, matches),
            "goal_difference_per_match": per(goal_difference, matches),
            "goal_ratio": per(goals_for, goals_against),
            "xg": match_stats.get("xg", ""),
            "xg_per_match": per(match_stats.get("xg"), match_records),
            "shots": match_stats.get("shots", ""),
            "shots_per_match": per(match_stats.get("shots"), match_records),
            "shots_on_target": match_stats.get("shots_on_target", ""),
            "shots_on_target_per_match": per(match_stats.get("shots_on_target"), match_records),
            "shot_accuracy": per(match_stats.get("shots_on_target"), match_stats.get("shots"), 100),
            "corners": match_stats.get("corners", ""),
            "corners_per_match": per(match_stats.get("corners"), match_records),
            "xg_against": match_stats.get("xg_against", ""),
            "xg_against_per_match": per(match_stats.get("xg_against"), match_records),
            "shots_conceded": match_stats.get("shots_conceded", ""),
            "shots_conceded_per_match": per(match_stats.get("shots_conceded"), match_records),
            "shots_on_target_conceded": match_stats.get("shots_on_target_conceded", ""),
            "shots_on_target_conceded_per_match": per(match_stats.get("shots_on_target_conceded"), match_records),
            "tackles": match_stats.get("tackles", ""),
            "tackles_per_match": per(match_stats.get("tackles"), match_records),
            "interceptions": match_stats.get("interceptions", ""),
            "interceptions_per_match": per(match_stats.get("interceptions"), match_records),
            "clearances": match_stats.get("clearances", ""),
            "clearances_per_match": per(match_stats.get("clearances"), match_records),
            "possession": match_stats.get("possession", ""),
            "passes": match_stats.get("passes", ""),
            "passes_per_match": per(match_stats.get("passes"), match_records),
            "pass_accuracy": per(match_stats.get("accurate_passes"), match_stats.get("passes"), 100),
            "crosses": match_stats.get("crosses", ""),
            "crosses_per_match": per(match_stats.get("crosses"), match_records),
            "long_balls": match_stats.get("long_balls", ""),
            "long_balls_per_match": per(match_stats.get("long_balls"), match_records),
            "long_ball_accuracy": per(match_stats.get("accurate_long_balls"), match_stats.get("long_balls"), 100),
            "ppda_per_match": match_stats.get("ppda", ""),
            "fouls": match_stats.get("fouls", ""),
            "fouls_per_match": per(match_stats.get("fouls"), match_records),
            "yellow": match_stats.get("yellow", ""),
            "yellow_per_match": per(match_stats.get("yellow"), match_records),
            "red": match_stats.get("red", ""),
            "red_per_match": per(match_stats.get("red"), match_records),
            "penalties_committed": match_stats.get("penalties_committed", ""),
            "penalties_committed_per_match": per(match_stats.get("penalties_committed"), match_records),
            "penalty_shots": match_stats.get("penalty_shots", ""),
            "penalty_shots_per_match": per(match_stats.get("penalty_shots"), match_records),
            "penalty_goals": match_stats.get("penalty_goals", ""),
            "penalty_goals_per_match": per(match_stats.get("penalty_goals"), match_records),
        }
        return {
            "label": team_label,
            "link": team_cell(team_label, filters),
            "stats": stats,
            "fields": [
                {"label": "Pais", "value": season_stats.get("country", "-") or "-"},
                {"label": "Competicion", "value": season_stats.get("competitionLabel", "-") or "-"},
                {"label": "Temporada", "value": season_stats.get("seasonLabel", "-") or "-"},
            ],
        }

    def fetch_player_season_stats(player_label: str, filters: dict[str, object]) -> dict[str, object]:
        rows = run_query(
            prefixes
            + f"""
            SELECT (GROUP_CONCAT(DISTINCT ?teamLabel; separator=", ") AS ?teamLabels)
                   (GROUP_CONCAT(DISTINCT ?positionText; separator=", ") AS ?positions)
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
              OPTIONAL {{ ?stats prop:position ?positionRaw . BIND(STR(?positionRaw) AS ?positionText) }}
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
              {filter_clauses(filters, competition_var='?competition', season_var='?season')}
            }}
            """
        )
        return rows[0] if rows else {}

    def fetch_player_match_stats(player_label: str, filters: dict[str, object]) -> dict[str, object]:
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
                   (SUM(?offsidesValue) AS ?offsides)
                   (SUM(?ownGoalsValue) AS ?own_goals)
                   (SUM(?goalsConcededValue) AS ?goals_conceded)
                   (SUM(?savesValue) AS ?saves)
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
              OPTIONAL {{ ?pmp prop:offsides ?offsidesRaw . }}
              OPTIONAL {{ ?pmp prop:ownGoals ?ownGoalsRaw . }}
              OPTIONAL {{ ?pmp prop:goalsConceded ?goalsConcededRaw . }}
              OPTIONAL {{ ?pmp prop:saves ?savesRaw . }}
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
              BIND(COALESCE(xsd:double(?offsidesRaw), 0) AS ?offsidesValue)
              BIND(COALESCE(xsd:double(?ownGoalsRaw), 0) AS ?ownGoalsValue)
              BIND(COALESCE(xsd:double(?goalsConcededRaw), 0) AS ?goalsConcededValue)
              BIND(COALESCE(xsd:double(?savesRaw), 0) AS ?savesValue)
              BIND(COALESCE(xsd:double(?yellowRaw), 0) AS ?yellowValue)
              BIND(COALESCE(xsd:double(?redRaw), 0) AS ?redValue)
              {match_scope_clauses(filters, match_var='?m', date_var='?date', week_var='?week')}
            }}
            """
        )
        return rows[0] if rows else {}

    def build_player_compare_item(player_label: str, filters: dict[str, object]) -> dict[str, object]:
        identity = fetch_player_identity(player_label)
        lookup_label = identity["label"]
        season_stats = fetch_player_season_stats(lookup_label, filters)
        match_stats = fetch_player_match_stats(lookup_label, filters)
        team_labels = str(season_stats.get("teamLabels", "")).strip()
        competition_labels = str(season_stats.get("competitionLabels", "")).strip()
        season_labels = str(season_stats.get("seasonLabels", "")).strip()
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
                "cards": sum_numeric(stats.get("yellow"), stats.get("red")),
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
                "shot_accuracy": per(stats.get("shots_on_target"), stats.get("shots"), 100),
                "xg_per_shot": per(stats.get("xg"), stats.get("shots")),
                "key_passes_per90": per(stats.get("key_passes"), minutes, 90),
                "xg_chain_per90": per(stats.get("xg_chain"), minutes, 90),
                "xg_buildup_per90": per(stats.get("xg_buildup"), minutes, 90),
                "goals_minus_xg": numeric(stats.get("goals")) - numeric(stats.get("xg")),
                "non_penalty_goals_minus_xg": numeric(stats.get("non_penalty_goals")) - numeric(stats.get("non_penalty_xg")),
                "assists_minus_xa": numeric(stats.get("assists")) - numeric(stats.get("xa")),
                "fouls_committed_per90": per(stats.get("fouls_committed"), minutes, 90),
                "fouls_suffered_per90": per(stats.get("fouls_suffered"), minutes, 90),
                "offsides_per90": per(stats.get("offsides"), minutes, 90),
                "yellow_per90": per(stats.get("yellow"), minutes, 90),
                "red_per90": per(stats.get("red"), minutes, 90),
                "saves_per90": per(stats.get("saves"), minutes, 90),
                "goals_conceded_per90": per(stats.get("goals_conceded"), minutes, 90),
                "cards_per90": per(sum_numeric(stats.get("yellow"), stats.get("red")), minutes, 90),
            }
        )
        return {
            "label": identity["known_as"],
            "link": player_cell(identity["known_as"], filters),
            "stats": stats,
            "fields": [
                {"label": "Nombre", "value": identity["known_as"] or "-"},
                {"label": "Nombre completo", "value": identity["full_name"] or "-"},
                {"label": "Equipo", "value": team_labels or "-"},
                {"label": "Competicion", "value": competition_labels or "-"},
                {"label": "Temporada", "value": season_labels or "-"},
            ],
        }

    @app.route("/compare")
    def compare():
        filters = get_filters()
        compare_type = request.args.get("compare_type", "teams").strip().lower()
        if compare_type not in {"teams", "players"}:
            compare_type = "teams"

        team_a = request.args.get("team_a", "").strip()
        team_b = request.args.get("team_b", "").strip()
        player_a = request.args.get("player_a", "").strip()
        player_b = request.args.get("player_b", "").strip()

        if not onboarding_complete():
            return render_page(
                "compare",
                title="Comparador",
                subtitle="Seleccion inicial de ligas y temporadas",
                panels=no_data_panel(),
                extra_context={
                    "compare_type": compare_type,
                    "entity_options": [],
                    "entity_a": "",
                    "entity_b": "",
                    "player_a": player_a,
                    "player_b": player_b,
                    "compare_data": None,
                },
            )

        try:
            if compare_type == "players":
                options = get_player_options(filters)
                entity_a = player_a
                entity_b = player_b
                item_builder = build_player_compare_item
                section_specs = [
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
                main_specs = [
                    ("Minutos", "minutes", 0, ""),
                    ("Participaciones directas/90", "goals_assists_per90", 2, ""),
                    ("xG/90", "xg_per90", 2, ""),
                    ("xA/90", "xa_per90", 2, ""),
                    ("Pases clave/90", "key_passes_per90", 2, ""),
                    ("xGChain/90", "xg_chain_per90", 2, ""),
                ]
                type_label = "jugadores"
                selector_label = "Jugador"
            else:
                options = get_team_options(filters)
                entity_a = team_a
                entity_b = team_b
                item_builder = build_team_compare_item
                section_specs = [
                    (
                        "Clasificacion",
                        [
                            ("Victorias", "wins", 0, ""),
                            ("Empates", "draws", 0, ""),
                            ("Derrotas", "losses", 0, ""),
                            ("Puntos", "points", 0, ""),
                            ("Goles a favor", "goals_for", 0, ""),
                            ("Goles en contra", "goals_against", 0, ""),
                        ],
                    ),
                    (
                        "Ataque",
                        [
                            ("Goles/partido", "goals_for_per_match", 2, ""),
                            ("xG/partido", "xg_per_match", 2, ""),
                            ("Tiros/partido", "shots_per_match", 2, ""),
                            ("Tiros a puerta/partido", "shots_on_target_per_match", 2, ""),
                            ("Precision de tiro", "shot_accuracy", 1, "%"),
                            ("Corners/partido", "corners_per_match", 2, ""),
                        ],
                    ),
                    (
                        "Defensa",
                        [
                            ("Goles en contra/partido", "goals_against_per_match", 2, ""),
                            ("xG concedido/partido", "xg_against_per_match", 2, ""),
                            ("Tiros concedidos/partido", "shots_conceded_per_match", 2, ""),
                            ("Tiros a puerta concedidos/partido", "shots_on_target_conceded_per_match", 2, ""),
                            ("Entradas/partido", "tackles_per_match", 2, ""),
                            ("Intercepciones/partido", "interceptions_per_match", 2, ""),
                            ("Despejes/partido", "clearances_per_match", 2, ""),
                        ],
                    ),
                    (
                        "Estilo de juego",
                        [
                            ("Posesion media", "possession", 1, "%"),
                            ("Pases/partido", "passes_per_match", 2, ""),
                            ("Precision de pase", "pass_accuracy", 1, "%"),
                            ("Centros/partido", "crosses_per_match", 2, ""),
                            ("Balones largos/partido", "long_balls_per_match", 2, ""),
                            ("Precision balones largos", "long_ball_accuracy", 1, "%"),
                            ("PPDA/partido", "ppda_per_match", 2, ""),
                        ],
                    ),
                    (
                        "Disciplina",
                        [
                            ("Faltas/partido", "fouls_per_match", 2, ""),
                            ("Amarillas/partido", "yellow_per_match", 2, ""),
                            ("Rojas/partido", "red_per_match", 2, ""),
                            ("Penaltis cometidos/partido", "penalties_committed_per_match", 2, ""),
                            ("Penaltis lanzados/partido", "penalty_shots_per_match", 2, ""),
                            ("Penaltis marcados/partido", "penalty_goals_per_match", 2, ""),
                        ],
                    ),
                ]
                main_specs = [
                    ("Posicion", "position", 0, ""),
                    ("Puntos/partido", "points_per_match", 2, ""),
                    ("Diferencia goles/partido", "goal_difference_per_match", 2, ""),
                    ("xG/partido", "xg_per_match", 2, ""),
                    ("% victorias", "win_pct", 1, "%"),
                    ("ELO actual", "elo", 1, ""),
                ]
                type_label = "equipos"
                selector_label = "Equipo"

            compare_data = None
            panels: list[dict[str, object]] = []

            if entity_a and entity_b:
                item_a = item_builder(entity_a, filters)
                item_b = item_builder(entity_b, filters)
                main_specs = unique_metric_specs(main_specs)
                detailed_sections = build_metric_sections(item_a["stats"], item_b["stats"], section_specs)
                if compare_type == "teams":
                    elo_panel = build_elo_panel([entity_a, entity_b], years=10, max_teams=2, show_extremes=True)
                    if elo_panel:
                        panels.append(elo_panel)

                compare_data = {
                    "type_label": type_label,
                    "selector_label": selector_label,
                    "item_a": item_a,
                    "item_b": item_b,
                    "main_metrics": build_metric_rows(item_a["stats"], item_b["stats"], main_specs),
                    "detailed_sections": detailed_sections,
                    "panels": panels,
                }

            return render_page(
                "compare",
                title="Comparador",
                subtitle=f"Comparacion de {type_label}",
                team_a=team_a,
                team_b=team_b,
                panels=no_data_panel() if not compare_data and (entity_a or entity_b) else [],
                extra_context={
                    "compare_type": compare_type,
                    "entity_options": options,
                    "entity_a": entity_a,
                    "entity_b": entity_b,
                    "player_a": player_a,
                    "player_b": player_b,
                    "compare_data": compare_data,
                    "compare_selector_label": selector_label,
                },
            )
        except Exception as exc:
            return render_page(
                "compare",
                title="Comparador",
                subtitle="Comparacion",
                team_a=team_a,
                team_b=team_b,
                panels=no_data_panel(),
                error=f"Error: {exc}",
                extra_context={
                    "compare_type": compare_type,
                    "entity_options": [],
                    "entity_a": team_a if compare_type == "teams" else player_a,
                    "entity_b": team_b if compare_type == "teams" else player_b,
                    "player_a": player_a,
                    "player_b": player_b,
                    "compare_data": None,
                    "compare_selector_label": "Equipo" if compare_type == "teams" else "Jugador",
                },
            )
