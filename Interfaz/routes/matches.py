from __future__ import annotations

from html import escape

from flask import request

from services.utils import format_number


def register_matches_routes(app, deps) -> None:
    render_page = deps["render_page"]
    get_search = deps["get_search"]
    get_filters = deps["get_filters"]
    onboarding_complete = deps["onboarding_complete"]
    get_filtered_matches = deps["get_filtered_matches"]
    format_match_datetime = deps["format_match_datetime"]
    build_url = deps["build_url"]
    no_data_panel = deps["no_data_panel"]
    run_query = deps["run_query"]
    prefixes = deps["PREFIXES"]
    sparql_iri = deps["sparql_iri"]
    safe_bool = deps["safe_bool"]
    safe_int = deps["safe_int"]
    build_match_pitch_panel = deps["build_match_pitch_panel"]

    def link_cell(text: str, href: str, class_name: str = "table-link") -> dict[str, str]:
        return {"text": text or "-", "href": href, "class": class_name}

    def player_match_cell(text: str, href: str, is_captain: bool) -> dict[str, str]:
        if not is_captain:
            return link_cell(text, href)

        label = text or "-"
        return {
            "html": (
                '<span class="player-name-with-captain">'
                f'<a class="table-link" href="{escape(href, quote=True)}">{escape(label)}</a>'
                '<span class="captain-badge" title="Capitan" aria-label="Capitan">C</span>'
                "</span>"
            )
        }

    def stat_numeric(value: str) -> float:
        try:
            return float(str(value or "0").strip() or 0)
        except Exception:
            return 0.0

    def format_measure(value: str, decimals: int = 1, suffix: str = "") -> str:
        formatted = format_number(value, decimals, suffix)
        return "" if formatted == "-" else formatted

    def numeric_or_none(value: str) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None

    def stadium_location(row: dict[str, str]) -> str:
        parts: list[str] = []
        for key in ("stadiumCity", "stadiumCountry"):
            value = str(row.get(key, "") or "").strip()
            if value and value not in parts:
                parts.append(value)
        return ", ".join(parts)

    def precipitation_text(row: dict[str, str]) -> str:
        rain_value = numeric_or_none(row.get("rain", ""))
        precipitation_value = numeric_or_none(row.get("precipitation", ""))
        if rain_value is not None and precipitation_value is not None and rain_value == 0 and precipitation_value == 0:
            return "Sin lluvia"
        if rain_value is not None:
            return f"Lluvia {format_number(str(rain_value), 1, ' mm')}"
        if precipitation_value is not None:
            return f"Precip. {format_number(str(precipitation_value), 1, ' mm')}"
        return ""

    def weather_summary(row: dict[str, str]) -> str:
        parts: list[str] = []
        temperature = format_measure(row.get("temperature", ""), 1, " C")
        rain = precipitation_text(row)
        if temperature:
            parts.append(temperature)
        if rain:
            parts.append(rain)
        return " | ".join(parts) if parts else "-"

    def weather_card(row: dict[str, str]) -> dict[str, str]:
        temperature = format_measure(row.get("temperature", ""), 1, " C")
        humidity = format_measure(row.get("humidity", ""), 0, "%")
        wind = format_measure(row.get("windSpeed", ""), 1, " km/h")
        rain = precipitation_text(row)
        secondary_parts = []
        if humidity:
            secondary_parts.append(f"Humedad {humidity}")
        if wind:
            secondary_parts.append(f"Viento {wind}")
        if rain:
            secondary_parts.append(rain)
        return {
            "label": "Meteorologia",
            "value": temperature or "Informacion no disponible",
            "secondary": " | ".join(secondary_parts),
            "wide": True,
        }

    def score_pair(home_score: object, away_score: object) -> str:
        home_text = str(home_score or "").strip()
        away_text = str(away_score or "").strip()
        if not home_text and not away_text:
            return "-"
        return f"{format_number(home_text)} - {format_number(away_text)}"

    def match_result_label(value: object, home_label: str, away_label: str) -> str:
        text = str(value or "").strip()
        key = text.upper()
        if key in {"H", "HOME", "LOCAL"}:
            return f"Gana {home_label}"
        if key in {"A", "AWAY", "VISITANTE"}:
            return f"Gana {away_label}"
        if key in {"D", "DRAW", "EMPATE"}:
            return "Empate"
        return text or "-"

    @app.route("/matches")
    def matches():
        q = get_search()
        filters = get_filters()
        if not onboarding_complete():
            return render_page("matches", title="Partidos", subtitle="Seleccion inicial de ligas y temporadas", panels=no_data_panel())
        try:
            data = get_filtered_matches(filters, q)

            rows = [
                [
                    format_match_datetime(r.get("dateTime", ""), r.get("date", "")),
                    r.get("week", "-"),
                    link_cell(r.get("homeLabel", "-"), build_url("teams", filters, "", {"team": r.get("homeLabel", "")})),
                    link_cell(r.get("awayLabel", "-"), build_url("teams", filters, "", {"team": r.get("awayLabel", "")})),
                    f"{r.get('hs', '-')} - {r.get('as', '-')}",
                    r.get("venue", "-") or "-",
                    weather_summary(r),
                    format_number(r.get("attendance", "")),
                    {
                        "text": "Ver partido",
                        "href": build_url("match_detail", filters, q, {"match_uri": r.get("matchUri", "")}),
                    },
                ]
                for r in data
            ]

            return render_page(
                "matches",
                title="Partidos",
                subtitle="Partidos filtrados",
                headers=["Fecha y hora", "Jornada", "Local", "Visitante", "Marcador", "Estadio", "Meteorologia", "Asistencia", "Detalle"],
                rows=rows,
                panels=no_data_panel() if not rows else [],
            )
        except Exception as exc:
            return render_page("matches", title="Partidos", subtitle="Partidos filtrados", error=f"Error: {exc}", panels=no_data_panel())

    @app.route("/match")
    def match_detail():
        match_uri = request.args.get("match_uri", "").strip()
        filters = get_filters()

        if not onboarding_complete():
            return render_page(
                "matches",
                title="Detalle de partido",
                current_view="match_detail",
                subtitle="Seleccion inicial de ligas y temporadas",
                panels=no_data_panel(),
            )

        if not match_uri:
            return render_page(
                "matches",
                title="Detalle de partido",
                current_view="match_detail",
                subtitle="Vista de detalle",
                panels=no_data_panel(),
                error="No se ha indicado el partido.",
            )

        try:
            details = run_query(
                prefixes
                + f"""
                            SELECT ?date ?dateTime ?week ?homeLabel ?awayLabel ?hs ?as ?finalResult ?halftimeHomeScore ?halftimeAwayScore ?halftimeResult ?venue ?stadiumCity ?stadiumCountry ?stadiumLatitude ?stadiumLongitude ?attendance ?temperature ?humidity ?precipitation ?rain ?windSpeed ?weatherDateTime
                WHERE {{
                  BIND({sparql_iri(match_uri)} AS ?m)
                  ?m a class:Match ;
                     prop:hasTeamMatchParticipation ?homeP ;
                     prop:hasTeamMatchParticipation ?awayP .
                  OPTIONAL {{ ?m prop:date ?dateRaw . }}
                  OPTIONAL {{ ?m prop:matchDate ?legacyDate . }}
                  OPTIONAL {{ ?m prop:dateTime ?dateTimeRaw . }}
                  OPTIONAL {{ ?m prop:matchDateTime ?legacyDateTime . }}
                  OPTIONAL {{ ?m prop:matchDay ?matchDay . }}
                  OPTIONAL {{ ?m prop:week ?legacyWeek . }}
                  OPTIONAL {{ ?m prop:homeScore ?hs . }}
                  OPTIONAL {{ ?m prop:awayScore ?as . }}
                  OPTIONAL {{ ?m prop:finalResult ?finalResult . }}
                  OPTIONAL {{ ?m prop:halftimeHomeScore ?halftimeHomeScore . }}
                  OPTIONAL {{ ?m prop:halftimeAwayScore ?halftimeAwayScore . }}
                  OPTIONAL {{ ?m prop:halftimeResult ?halftimeResult . }}
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
                  BIND(COALESCE(?stadiumLabel, ?stadiumName, ?venueRaw, ?stadiumLocal) AS ?venue)
                  FILTER(BOUND(?date))

                  ?homeP prop:isHome true ; prop:correspondsToTeam ?home .
                  ?awayP prop:isHome false ; prop:correspondsToTeam ?away .
                  ?home rdfs:label ?homeLabel .
                  ?away rdfs:label ?awayLabel .
                }}
                LIMIT 1
                """
            )

            if not details:
                return render_page(
                    "matches",
                    title="Detalle de partido",
                    current_view="match_detail",
                    subtitle="Vista de detalle",
                    panels=no_data_panel(),
                    error="No se han encontrado datos para el partido seleccionado.",
                )

            row = details[0]
            row_date = format_match_datetime(row.get("dateTime", ""), row.get("date", ""))
            home_label = row.get("homeLabel", "-")
            away_label = row.get("awayLabel", "-")
            score_text = score_pair(row.get("hs", ""), row.get("as", ""))
            result_summary = {
                "home": home_label,
                "away": away_label,
                "rows": [
                    {
                        "label": "Descanso",
                        "score": score_pair(row.get("halftimeHomeScore", ""), row.get("halftimeAwayScore", "")),
                        "result": match_result_label(row.get("halftimeResult", ""), home_label, away_label),
                    },
                    {
                        "label": "Final",
                        "score": score_text,
                        "result": match_result_label(row.get("finalResult", ""), home_label, away_label),
                    },
                ],
            }
            venue_text = row.get("venue", "Estadio no disponible")
            attendance_text = row.get("attendance", "Informacion no disponible")
            venue_card = {"label": "Estadio", "value": venue_text}
            location_text = stadium_location(row)
            if location_text:
                venue_card["secondary"] = location_text
            cards = [
                {"label": "Jornada", "value": row.get("week", "-")},
                {"label": "Local", "value": home_label, "href": build_url("teams", filters, "", {"team": home_label})},
                {"label": "Visitante", "value": away_label, "href": build_url("teams", filters, "", {"team": away_label})},
                venue_card,
                weather_card(row),
                {"label": "Asistencia", "value": attendance_text},
            ]

            events_rows = run_query(
                prefixes
                + f"""
                SELECT ?event ?teamLabel ?playerLabel ?assistantLabel ?period ?type ?outcomeType ?isShot ?isGoal ?cardType ?minute ?second ?expandedMinute ?x ?y ?endX ?endY ?goalMouthY ?goalMouthZ ?qualifiers
                WHERE {{
                  BIND({sparql_iri(match_uri)} AS ?m)
                  ?m a class:Match ; prop:hasEvent ?event .
                  ?event prop:involvesTeamMatchParticipation ?eventTeamParticipation .
                  ?eventTeamParticipation prop:correspondsToTeam ?team .
                  ?team rdfs:label ?teamLabel .

                  OPTIONAL {{
                    ?event prop:involvesPlayerMatchParticipation ?eventPlayerParticipation .
                    ?eventPlayerParticipation prop:correspondsToPlayer ?player .
                    ?player rdfs:label ?playerLabel .
                  }}
                  OPTIONAL {{
                    ?event prop:involvesSecondaryPlayerMatchParticipation ?secondaryPmp .
                    ?secondaryPmp prop:correspondsToPlayer ?assistant .
                    ?assistant rdfs:label ?assistantLabel .
                  }}
                  OPTIONAL {{ ?event prop:period ?periodRaw . }}
                  OPTIONAL {{ ?event prop:eventPeriod ?legacyPeriod . }}
                  OPTIONAL {{ ?event prop:type ?typeRaw . }}
                  OPTIONAL {{ ?event prop:eventType ?legacyType . }}
                  OPTIONAL {{ ?event prop:outcomeType ?outcomeType . }}
                  OPTIONAL {{ ?event prop:isShot ?isShot . }}
                  OPTIONAL {{ ?event prop:isGoal ?isGoal . }}
                  OPTIONAL {{ ?event prop:cardType ?cardType . }}
                  OPTIONAL {{ ?event prop:minute ?minuteRaw . }}
                  OPTIONAL {{ ?event prop:eventMinute ?legacyMinute . }}
                  OPTIONAL {{ ?event prop:second ?secondRaw . }}
                  OPTIONAL {{ ?event prop:eventSecond ?legacySecond . }}
                  OPTIONAL {{ ?event prop:expandedMinute ?expandedMinuteRaw . }}
                  OPTIONAL {{ ?event prop:eventExpandedMinute ?legacyExpandedMinute . }}
                  OPTIONAL {{ ?event prop:x ?xRaw . }}
                  OPTIONAL {{ ?event prop:xCoord ?legacyX . }}
                  OPTIONAL {{ ?event prop:y ?yRaw . }}
                  OPTIONAL {{ ?event prop:yCoord ?legacyY . }}
                  OPTIONAL {{ ?event prop:endX ?endX . }}
                  OPTIONAL {{ ?event prop:endY ?endY . }}
                  OPTIONAL {{ ?event prop:goalMouthY ?goalMouthY . }}
                  OPTIONAL {{ ?event prop:goalMouthZ ?goalMouthZ . }}
                  OPTIONAL {{ ?event prop:qualifiers ?qualifiers . }}
                  BIND(COALESCE(?periodRaw, ?legacyPeriod) AS ?period)
                  BIND(COALESCE(?typeRaw, ?legacyType) AS ?type)
                  BIND(COALESCE(?minuteRaw, ?legacyMinute) AS ?minute)
                  BIND(COALESCE(?secondRaw, ?legacySecond) AS ?second)
                  BIND(COALESCE(?expandedMinuteRaw, ?legacyExpandedMinute) AS ?expandedMinute)
                  BIND(COALESCE(?xRaw, ?legacyX) AS ?x)
                  BIND(COALESCE(?yRaw, ?legacyY) AS ?y)
                  BIND(COALESCE(xsd:integer(?expandedMinute), xsd:integer(?minute), 0) AS ?minuteSort)
                  BIND(COALESCE(xsd:float(?second), 0) AS ?secondSort)
                }}
                ORDER BY ?minuteSort ?secondSort ?event
                """
            )

            events_payload: list[dict[str, object]] = []
            timeline_items: list[dict[str, str]] = []
            timeline_connectors: list[dict[str, str]] = []
            timeline_half_left = "50.00"

            def safe_float(value: object) -> float:
                try:
                    text = str(value or "").strip()
                    return float(text) if text else 0.0
                except Exception:
                    return 0.0

            def event_minute_value(item: dict[str, str]) -> int:
                minute = item.get("minute", "")
                if minute in {"", None}:
                    return 0
                return safe_int(minute)

            def event_minute_label(item: dict[str, str]) -> str:
                minute_value = event_minute_value(item)
                period = str(item.get("period", "")).strip().lower()
                event_type = str(item.get("type", "")).strip().lower()

                if event_type == "start" and period == "firsthalf" and minute_value == 0:
                    return "0'"
                if minute_value < 0 or period in {"prematch", "postgame"}:
                    return "-"
                if period == "firsthalf":
                    if minute_value < 45:
                        return f"{minute_value + 1}'"
                    return f"45+{minute_value - 44}'"
                if period == "secondhalf":
                    if minute_value < 90:
                        return f"{minute_value + 1}'"
                    return f"90+{minute_value - 89}'"
                if minute_value <= 0:
                    return "-"
                return f"{minute_value}'"

            def event_time_value(item: dict[str, str]) -> float:
                expanded_minute = str(item.get("expandedMinute", "") or "").strip()
                base_minute = safe_float(expanded_minute) if expanded_minute else safe_float(item.get("minute", ""))
                return base_minute + (safe_float(item.get("second", "")) / 60.0)

            def event_timeline_time_value(item: dict[str, str]) -> float:
                return safe_float(item.get("minute", ""))

            def event_period(item: dict[str, str]) -> str:
                return str(item.get("period", "")).strip().lower()

            timeline_icon_paths = {
                "goal": "images/events/gol.svg",
                "yellow-card": "images/events/tarjeta-amarilla.svg",
                "red-card": "images/events/tarjeta-roja.svg",
                "second-yellow-card": "images/events/tarjeta-segunda-amarilla.svg",
                "substitution": "images/events/sustitucion.svg",
                "penalty-scored": "images/events/penalti-marcado.svg",
                "penalty-missed": "images/events/penalti-fallado.svg",
                "penalty-saved": "images/events/penalti-parado.svg",
            }

            def timeline_icon_path(icon_key: str) -> str:
                return timeline_icon_paths.get(icon_key, timeline_icon_paths["goal"])

            def card_icon_key(label: str) -> str:
                label_lower = label.lower()
                if "second" in label_lower or "segunda" in label_lower:
                    return "second-yellow-card"
                if "red" in label_lower or "roja" in label_lower:
                    return "red-card"
                return "yellow-card"

            def classify_penalty(item: dict[str, str]) -> tuple[str, str, str] | None:
                event_type = str(item.get("type", "")).strip().lower()
                qualifiers = str(item.get("qualifiers", "")).strip().lower()
                if event_type not in {"goal", "savedshot", "missedshots", "shotonpost"}:
                    return None
                if "penalty" not in qualifiers:
                    return None
                if safe_bool(item.get("isGoal", "")) or event_type == "goal":
                    return ("Penalti marcado", "is-penalty is-penalty-scored", "penalty-scored")
                if event_type == "savedshot":
                    return ("Penalti parado", "is-penalty is-penalty-saved", "penalty-saved")
                return ("Penalti fallado", "is-penalty is-penalty-missed", "penalty-missed")

            def timeline_side(team: str) -> str:
                if team == home_label:
                    return "home"
                if team == away_label:
                    return "away"
                return "neutral"

            def add_timeline_item(
                item: dict[str, str],
                label: str,
                css_class: str,
                *,
                detail_parts: list[str] | None = None,
                icon_key: str = "goal",
                side: str | None = None,
            ) -> None:
                player = item.get("playerLabel", "")
                team = item.get("teamLabel", "")
                item_side = side or timeline_side(team)
                item_detail_parts = detail_parts if detail_parts is not None else [event_minute_label(item), label, player, team]
                timeline_items.append(
                    {
                        "minute": event_minute_label(item),
                        "minute_value": f"{event_time_value(item):.4f}",
                        "position_time": f"{event_timeline_time_value(item):.4f}",
                        "period": event_period(item),
                        "label": label,
                        "detail": " | ".join(part for part in item_detail_parts if part and part != "-"),
                        "class": css_class,
                        "side": item_side,
                        "icon_path": timeline_icon_path(icon_key),
                        "left": "0",
                        "lane": "0",
                    }
                )

            for item in events_rows:
                event_type = str(item.get("type", "")).strip()
                event_type_lower = event_type.lower()
                card_type = str(item.get("cardType", "")).strip()
                penalty = classify_penalty(item)

                if penalty:
                    penalty_label, penalty_class, penalty_icon = penalty
                    player = item.get("playerLabel", "")
                    team = item.get("teamLabel", "")
                    add_timeline_item(
                        item,
                        penalty_label,
                        penalty_class,
                        detail_parts=[
                            event_minute_label(item),
                            penalty_label,
                            f"Lanza: {player}" if player else "",
                            team,
                        ],
                        icon_key=penalty_icon,
                    )
                elif safe_bool(item.get("isGoal", "")):
                    add_timeline_item(item, "Gol", "is-goal", icon_key="goal")
                elif card_type or "card" in event_type_lower:
                    card_label = card_type or event_type
                    add_timeline_item(item, card_label, "is-card", icon_key=card_icon_key(card_label))
                elif event_type_lower == "substitutionoff":
                    player_out = item.get("playerLabel", "")
                    player_in = item.get("assistantLabel", "")
                    team = item.get("teamLabel", "")
                    substitution_label = "Sustituci\u00f3n"
                    add_timeline_item(
                        item,
                        substitution_label,
                        "is-sub",
                        detail_parts=[
                            event_minute_label(item),
                            substitution_label,
                            f"Entra: {player_in}" if player_in else "",
                            f"Sale: {player_out}" if player_out else "",
                            team,
                        ],
                        icon_key="substitution",
                    )
                try:
                    x_val = float(item.get("x", ""))
                    y_val = float(item.get("y", ""))
                except Exception:
                    continue

                events_payload.append(
                    {
                        "event_id": item.get("event", ""),
                        "team": item.get("teamLabel", ""),
                        "player": item.get("playerLabel", ""),
                        "assistant": item.get("assistantLabel", ""),
                        "type": item.get("type", ""),
                        "outcome_type": item.get("outcomeType", ""),
                        "is_shot": safe_bool(item.get("isShot", "")),
                        "is_goal": safe_bool(item.get("isGoal", "")),
                        "minute": int(float(item["minute"])) if item.get("minute", "") not in {"", None} else None,
                        "minute_label": event_minute_label(item),
                        "expanded_minute": int(float(item["expandedMinute"])) if item.get("expandedMinute", "") not in {"", None} else None,
                        "time_value": event_time_value(item),
                        "x": x_val,
                        "y": y_val,
                        "end_x": float(item["endX"]) if item.get("endX", "") not in {"", None} else None,
                        "end_y": float(item["endY"]) if item.get("endY", "") not in {"", None} else None,
                        "goal_mouth_y": float(item["goalMouthY"]) if item.get("goalMouthY", "") not in {"", None} else None,
                        "goal_mouth_z": float(item["goalMouthZ"]) if item.get("goalMouthZ", "") not in {"", None} else None,
                    }
                )

            if timeline_items:
                max_lanes = 5
                min_lane_gap = 6.0
                last_left_by_side_lane: dict[str, list[float]] = {}
                connector_lanes: dict[tuple[str, str], int] = {}

                first_half_end = max(
                    45.0,
                    max(
                        (
                            event_timeline_time_value(row)
                            for row in events_rows
                            if event_period(row) == "firsthalf"
                        ),
                        default=45.0,
                    ),
                )
                second_half_end = max(
                    90.0,
                    max(
                        (
                            event_timeline_time_value(row)
                            for row in events_rows
                            if event_period(row) == "secondhalf"
                        ),
                        default=90.0,
                    ),
                )
                second_half_duration = max(45.0, second_half_end - 45.0)

                def timeline_left(item: dict[str, str]) -> float:
                    period = item.get("period", "")
                    time_value = safe_float(item.get("position_time", "0"))
                    if period == "firsthalf":
                        return min(49.5, max(0.0, (time_value / first_half_end) * 50.0))
                    if period == "secondhalf":
                        elapsed_second_half = max(0.0, time_value - 45.0)
                        return max(50.5, min(100.0, 50.0 + ((elapsed_second_half / second_half_duration) * 50.0)))
                    return min(100.0, max(0.0, (time_value / 90.0) * 100.0))

                timeline_items.sort(
                    key=lambda item: (
                        {"firsthalf": 0, "secondhalf": 1}.get(item.get("period", ""), 2),
                        safe_float(item.get("position_time", "0")),
                        item.get("side", ""),
                        safe_float(item.get("minute_value", "0")),
                        item.get("label", ""),
                    )
                )
                for item in timeline_items:
                    left = timeline_left(item)
                    side = item.get("side", "neutral")
                    lane_positions = last_left_by_side_lane.setdefault(side, [-100.0] * max_lanes)
                    lane = next(
                        (index for index, previous_left in enumerate(lane_positions) if left - previous_left >= min_lane_gap),
                        min(range(max_lanes), key=lambda index: lane_positions[index]),
                    )
                    lane_positions[lane] = left
                    item["left"] = f"{left:.2f}"
                    item["lane"] = str(lane)
                    if side != "neutral":
                        connector_key = (side, item["left"])
                        connector_lanes[connector_key] = max(connector_lanes.get(connector_key, 0), lane)

                timeline_connectors = [
                    {"side": side, "left": left, "lane": str(lane)}
                    for (side, left), lane in sorted(
                        connector_lanes.items(),
                        key=lambda connector: (safe_float(connector[0][1]), connector[0][0]),
                    )
                ]

            events_payload.sort(key=lambda item: (safe_float(item.get("time_value", 0)), str(item.get("event_id", ""))))

            team_stat_rows = run_query(
                prefixes
                + f"""
                SELECT ?teamLabel ?isHome ?fouls ?yellow ?red ?offsides ?corners ?saves ?possession ?shots ?shotsOnTarget ?penaltyGoals ?penaltyShots ?passes ?accuratePasses ?crosses ?accurateCrosses ?longBalls ?accurateLongBalls ?blockedShots ?effectiveTackles ?tackles ?interceptions ?clearances ?xg ?nonPenaltyXg ?nonPenaltyXgDifference ?ppda ?deepCompletions
                WHERE {{
                  BIND({sparql_iri(match_uri)} AS ?m)
                  ?m a class:Match ; prop:hasTeamMatchParticipation ?p .
                  ?p prop:correspondsToTeam ?team ;
                     prop:isHome ?isHome .
                  ?team rdfs:label ?teamLabel .

                  OPTIONAL {{ ?p prop:foulsCommitted ?fouls . }}
                  OPTIONAL {{ ?p prop:yellowCards ?yellow . }}
                  OPTIONAL {{ ?p prop:redCards ?red . }}
                  OPTIONAL {{ ?p prop:offsides ?offsides . }}
                  OPTIONAL {{ ?p prop:wonCorners ?corners . }}
                  OPTIONAL {{ ?p prop:saves ?saves . }}
                  OPTIONAL {{ ?p prop:possessionPct ?possession . }}
                  OPTIONAL {{ ?p prop:totalShots ?shots . }}
                  OPTIONAL {{ ?p prop:shotsOnTarget ?shotsOnTarget . }}
                  OPTIONAL {{ ?p prop:penaltyKickGoals ?penaltyGoals . }}
                  OPTIONAL {{ ?p prop:penaltyKickShots ?penaltyShots . }}
                  OPTIONAL {{ ?p prop:totalPasses ?passes . }}
                  OPTIONAL {{ ?p prop:accuratePasses ?accuratePasses . }}
                  OPTIONAL {{ ?p prop:totalCrosses ?crosses . }}
                  OPTIONAL {{ ?p prop:accurateCrosses ?accurateCrosses . }}
                  OPTIONAL {{ ?p prop:totalLongBalls ?longBalls . }}
                  OPTIONAL {{ ?p prop:accurateLongBalls ?accurateLongBalls . }}
                  OPTIONAL {{ ?p prop:blockedShots ?blockedShots . }}
                  OPTIONAL {{ ?p prop:effectiveTackles ?effectiveTackles . }}
                  OPTIONAL {{ ?p prop:totalTackles ?tackles . }}
                  OPTIONAL {{ ?p prop:interceptions ?interceptions . }}
                  OPTIONAL {{ ?p prop:totalClearance ?clearances . }}
                  OPTIONAL {{ ?p prop:xg ?xg . }}
                  OPTIONAL {{ ?p prop:nonPenaltyXg ?nonPenaltyXg . }}
                  OPTIONAL {{ ?p prop:nonPenaltyXgDifference ?nonPenaltyXgDifference . }}
                  OPTIONAL {{ ?p prop:ppda ?ppda . }}
                  OPTIONAL {{ ?p prop:deepCompletions ?deepCompletions . }}
                }}
                ORDER BY DESC(?isHome)
                """
            )

            def percentage_value(numerator: object, denominator: object) -> str:
                denominator_value = stat_numeric(denominator)
                if denominator_value <= 0:
                    return ""
                return str((stat_numeric(numerator) / denominator_value) * 100.0)

            def enrich_team_stat_values(row: dict[str, str]) -> dict[str, str]:
                enriched = dict(row)
                penalty_missed = max(0.0, stat_numeric(enriched.get("penaltyShots", "")) - stat_numeric(enriched.get("penaltyGoals", "")))
                enriched["penaltyMissed"] = str(penalty_missed)
                enriched["passAccuracy"] = percentage_value(enriched.get("accuratePasses", ""), enriched.get("passes", ""))
                enriched["crossAccuracy"] = percentage_value(enriched.get("accurateCrosses", ""), enriched.get("crosses", ""))
                enriched["longPassAccuracy"] = percentage_value(enriched.get("accurateLongBalls", ""), enriched.get("longBalls", ""))
                return enriched

            stats_by_team = {item.get("teamLabel", ""): enrich_team_stat_values(item) for item in team_stat_rows}
            metric_sections = [
                (
                    "Generales",
                    [
                        ("Faltas cometidas", "fouls", 0, ""),
                        ("Amarillas", "yellow", 0, ""),
                        ("Rojas", "red", 0, ""),
                        ("Fueras de juego", "offsides", 0, ""),
                        ("Corners", "corners", 0, ""),
                        ("Paradas", "saves", 0, ""),
                        ("Posesion", "possession", 1, "%"),
                    ],
                ),
                (
                    "Ofensivas",
                    [
                        ("Tiros", "shots", 0, ""),
                        ("Tiros a puerta", "shotsOnTarget", 0, ""),
                        ("Tiros bloqueados", "blockedShots", 0, ""),
                        ("Penaltis marcados", "penaltyGoals", 0, ""),
                        ("Penaltis fallados", "penaltyMissed", 0, ""),
                        ("xG", "xg", 2, ""),
                        ("No penalti xG", "nonPenaltyXg", 2, ""),
                        ("No penalti xG dif.", "nonPenaltyXgDifference", 2, ""),
                        ("Deep completions", "deepCompletions", 0, ""),
                    ],
                ),
                (
                    "Pase",
                    [
                        ("Precision pases", "passAccuracy", 1, "%"),
                        ("Pases totales", "passes", 0, ""),
                        ("Precision centros", "crossAccuracy", 1, "%"),
                        ("Centros", "crosses", 0, ""),
                        ("Pases largos", "longBalls", 0, ""),
                        ("Precision pases largos", "longPassAccuracy", 1, "%"),
                    ],
                ),
                (
                    "Defensa y presion",
                    [
                        ("Entradas efectivas", "effectiveTackles", 0, ""),
                        ("Entradas", "tackles", 0, ""),
                        ("Intercepciones", "interceptions", 0, ""),
                        ("Despejes", "clearances", 0, ""),
                        ("PPDA", "ppda", 2, ""),
                    ],
                ),
            ]
            team_stats = {
                "home": home_label,
                "away": away_label,
                "sections": [],
            }
            for title, specs in metric_sections:
                section_lines = []
                for label, key, decimals, suffix in specs:
                    home_raw = stats_by_team.get(home_label, {}).get(key, "")
                    away_raw = stats_by_team.get(away_label, {}).get(key, "")
                    home_value = abs(stat_numeric(home_raw))
                    away_value = abs(stat_numeric(away_raw))
                    total_value = home_value + away_value
                    home_pct = 0.0 if total_value <= 0 else (home_value / total_value) * 100.0
                    away_pct = 0.0 if total_value <= 0 else (away_value / total_value) * 100.0
                    section_lines.append(
                        {
                            "label": label,
                            "home": format_number(home_raw, decimals, suffix),
                            "away": format_number(away_raw, decimals, suffix),
                            "home_pct": f"{home_pct:.2f}",
                            "away_pct": f"{away_pct:.2f}",
                        }
                    )
                team_stats["sections"].append({"title": title, "lines": section_lines})

            def player_match_order(item: dict[str, str]) -> tuple[int, int, str]:
                status = str(item.get("participationStatus", "")).strip().lower()
                if status == "titular":
                    group = 0
                elif status == "suplente":
                    group = 1
                elif status in {"no jugado", "no_jugado"}:
                    group = 2
                elif status in {"no disponible", "no_disponible", "no convocado", "no_convocado"}:
                    group = 3
                else:
                    group = 4
                return (group, -safe_int(item.get("minutes", "0")), item.get("playerLabel", ""))

            def participation_status_label(value: object) -> str:
                status = str(value or "").strip().lower()
                return {
                    "titular": "Titular",
                    "suplente": "Suplente",
                    "no jugado": "No jugado",
                    "no_jugado": "No jugado",
                    "no disponible": "No disponible",
                    "no_disponible": "No disponible",
                    "no convocado": "No disponible",
                    "no_convocado": "No disponible",
                }.get(status, str(value or "-").strip() or "-")

            def bool_label(value: object) -> str:
                text = str(value or "").strip().lower()
                if text in {"true", "1", "yes", "si", "sí"}:
                    return "Si"
                if text in {"false", "0", "no"}:
                    return "No"
                return "-"

            def text_value(value: object) -> str:
                text = str(value or "").strip()
                return text if text else "-"

            non_participation_statuses = {
                "no jugado",
                "no_jugado",
                "no disponible",
                "no_disponible",
                "no convocado",
                "no_convocado",
            }

            def player_participated(item: dict[str, str]) -> bool:
                status = str(item.get("participationStatus", "")).strip().lower()
                if status in non_participation_statuses:
                    return False
                if status == "suplente":
                    sub_in = str(item.get("subIn", "")).strip().lower()
                    return (
                        stat_numeric(item.get("minutes", "")) > 0
                        or stat_numeric(item.get("appearances", "")) > 0
                        or bool(sub_in and sub_in != "start")
                    )
                return True

            def player_metric_value(item: dict[str, str], key: str, decimals: int = 0) -> str:
                if not player_participated(item):
                    return "-"
                return format_number(item.get(key, ""), decimals)

            player_rows = run_query(
                prefixes
                + f"""
                SELECT ?teamLabel ?playerLabel ?participationStatus ?position ?isCaptain ?subIn ?subOut ?appearances ?foulsCommitted ?foulsSuffered ?ownGoals ?redCards ?yellowCards ?goalsConceded ?saves ?goalAssists ?shotsOnTarget ?totalGoals ?totalShots ?offsides ?minutes ?xg ?xgChain ?xgBuildup ?xa ?keyPasses ?reason ?status
                WHERE {{
                  BIND({sparql_iri(match_uri)} AS ?m)
                  ?m a class:Match ; prop:hasTeamMatchParticipation ?playerTeamParticipation .
                  ?playerTeamParticipation prop:hasPlayerMatchParticipation ?pmp ;
                                           prop:correspondsToTeam ?team .
                  ?pmp prop:correspondsToPlayer ?player .
                  ?team rdfs:label ?teamLabel .
                  ?player rdfs:label ?playerLabel .

                  OPTIONAL {{ ?pmp prop:participationStatus ?participationStatus . }}
                  OPTIONAL {{ ?pmp prop:position ?position . }}
                  OPTIONAL {{ ?pmp prop:isCaptain ?isCaptain . }}
                  OPTIONAL {{ ?pmp prop:subIn ?subIn . }}
                  OPTIONAL {{ ?pmp prop:subOut ?subOut . }}
                  OPTIONAL {{ ?pmp prop:appearances ?appearances . }}
                  OPTIONAL {{ ?pmp prop:foulsCommitted ?foulsCommitted . }}
                  OPTIONAL {{ ?pmp prop:foulsSuffered ?foulsSuffered . }}
                  OPTIONAL {{ ?pmp prop:ownGoals ?ownGoals . }}
                  OPTIONAL {{ ?pmp prop:redCards ?redCards . }}
                  OPTIONAL {{ ?pmp prop:yellowCards ?yellowCards . }}
                  OPTIONAL {{ ?pmp prop:goalsConceded ?goalsConceded . }}
                  OPTIONAL {{ ?pmp prop:saves ?saves . }}
                  OPTIONAL {{ ?pmp prop:goalAssists ?goalAssists . }}
                  OPTIONAL {{ ?pmp prop:shotsOnTarget ?shotsOnTarget . }}
                  OPTIONAL {{ ?pmp prop:totalGoals ?totalGoals . }}
                  OPTIONAL {{ ?pmp prop:totalShots ?totalShots . }}
                  OPTIONAL {{ ?pmp prop:offsides ?offsides . }}
                  OPTIONAL {{ ?pmp prop:minutes ?minutes . }}
                  OPTIONAL {{ ?pmp prop:xg ?xg . }}
                  OPTIONAL {{ ?pmp prop:xg_chain ?xgChain . }}
                  OPTIONAL {{ ?pmp prop:xg_buildup ?xgBuildup . }}
                  OPTIONAL {{ ?pmp prop:xa ?xa . }}
                  OPTIONAL {{ ?pmp prop:keyPasses ?keyPasses . }}
                  OPTIONAL {{ ?pmp prop:reason ?reason . }}
                  OPTIONAL {{ ?pmp prop:status ?status . }}
                  BIND(COALESCE(xsd:integer(?minutes), 0) AS ?minutesSort)
                }}
                ORDER BY ?teamLabel ?playerLabel
                """
            )
            player_stats = {
                "headers": [
                    {"text": "Jugador", "title": "Jugador"},
                    {"text": "Estado", "title": "Estado participacion"},
                    {"text": "Pos", "title": "Posicion"},
                    {"text": "Min", "title": "Minutos"},
                    {"text": "G", "title": "Goles"},
                    {"text": "A", "title": "Asistencias"},
                    {"text": "Tiros", "title": "Tiros"},
                    {"text": "A puerta", "title": "Tiros a puerta"},
                    {"text": "FJ", "title": "Fueras de juego"},
                    {"text": "xG", "title": "xG"},
                    {"text": "Chain", "title": "xGChain"},
                    {"text": "Buildup", "title": "xGBuildup"},
                    {"text": "xA", "title": "xA"},
                    {"text": "P clave", "title": "Pases clave"},
                    {"text": "FC", "title": "Faltas cometidas"},
                    {"text": "FR", "title": "Faltas sufridas"},
                    {"text": "GP", "title": "Goles en propia"},
                    {"text": "TR", "title": "Tarjetas rojas"},
                    {"text": "TA", "title": "Tarjetas amarillas"},
                    {"text": "GC", "title": "Goles concedidos"},
                    {"text": "Paradas", "title": "Paradas"},
                    {"text": "Reason", "title": "Reason"},
                    {"text": "Status", "title": "Status"},
                ],
                "header_groups": [
                    {"label": "Jugador", "span": 1},
                    {"label": "Participacion", "span": 3},
                    {"label": "Ataque", "span": 5},
                    {"label": "Esperadas y creacion", "span": 5},
                    {"label": "Faltas y disciplina", "span": 5},
                    {"label": "Porteria", "span": 2},
                    {"label": "Ausencias", "span": 2},
                ],
                "teams": [home_label, away_label],
                "rows_by_team": {home_label: [], away_label: []},
            }
            player_rows.sort(
                key=lambda item: (
                    0 if item.get("teamLabel", "") == home_label else 1 if item.get("teamLabel", "") == away_label else 2,
                    *player_match_order(item),
                )
            )
            for item in player_rows:
                team = item.get("teamLabel", "")
                if team not in player_stats["rows_by_team"]:
                    player_stats["rows_by_team"][team] = []
                    player_stats["teams"].append(team)
                player_stats["rows_by_team"][team].append(
                    [
                        player_match_cell(
                            item.get("playerLabel", "-"),
                            build_url("players", filters, "", {"player": item.get("playerLabel", "")}),
                            safe_bool(item.get("isCaptain", "")),
                        ),
                        participation_status_label(item.get("participationStatus", "")),
                        item.get("position", "-"),
                        player_metric_value(item, "minutes"),
                        player_metric_value(item, "totalGoals"),
                        player_metric_value(item, "goalAssists"),
                        player_metric_value(item, "totalShots"),
                        player_metric_value(item, "shotsOnTarget"),
                        player_metric_value(item, "offsides"),
                        player_metric_value(item, "xg", 2),
                        player_metric_value(item, "xgChain", 2),
                        player_metric_value(item, "xgBuildup", 2),
                        player_metric_value(item, "xa", 2),
                        player_metric_value(item, "keyPasses"),
                        player_metric_value(item, "foulsCommitted"),
                        player_metric_value(item, "foulsSuffered"),
                        player_metric_value(item, "ownGoals"),
                        player_metric_value(item, "redCards"),
                        player_metric_value(item, "yellowCards"),
                        player_metric_value(item, "goalsConceded"),
                        player_metric_value(item, "saves"),
                        text_value(item.get("reason", "")),
                        text_value(item.get("status", "")),
                    ]
                )

            panels = build_match_pitch_panel(events_payload)
            match_detail = {
                "timeline": timeline_items,
                "timeline_connectors": timeline_connectors,
                "timeline_half_left": timeline_half_left,
                "result_summary": result_summary,
                "team_stats": team_stats,
                "player_stats": player_stats,
            }

            return render_page(
                "matches",
                title=f"{home_label} {score_text} {away_label}",
                current_view="match_detail",
                subtitle=row_date,
                cards=cards,
                panels=panels,
                extra_context={"match_detail": match_detail},
            )
        except Exception as exc:
            return render_page(
                "matches",
                title="Detalle de partido",
                current_view="match_detail",
                subtitle="Vista de detalle",
                panels=no_data_panel(),
                error=f"Error al cargar detalle del partido: {exc}",
            )
