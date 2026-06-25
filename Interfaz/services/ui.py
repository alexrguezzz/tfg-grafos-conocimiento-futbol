from __future__ import annotations

from datetime import date, timedelta
from html import escape
import json

from flask import render_template, request

from services.utils import format_display_date


def current_page_url() -> str:
    full_path = request.full_path or request.path
    return full_path[:-1] if full_path.endswith("?") else full_path


def no_data_panel() -> list[dict[str, object]]:
    return [{"title": "Estado", "kind": "text", "text": "Informacion no disponible"}]


def build_elo_panel(
    *,
    run_query,
    prefixes: str,
    sparql_string,
    team_labels: list[str],
    years: int = 5,
    max_teams: int | None = 6,
    show_extremes: bool = False,
) -> dict[str, object] | None:
    selected_teams = [label for label in dict.fromkeys(team_labels) if label]
    if max_teams is not None:
        selected_teams = selected_teams[:max_teams]
    if not selected_teams:
        return None

    date_limit = (date.today() - timedelta(days=365 * years)).isoformat()
    values_clause = " ".join(sparql_string(label) for label in selected_teams)

    elo_rows = run_query(
        prefixes
        + f"""
        SELECT ?teamLabel ?d ?elo
        WHERE {{
          VALUES ?teamLabel {{ {values_clause} }}
          ?team a class:Team ; rdfs:label ?teamLabel .
          ?record a class:EloRecord ;
                  prop:correspondsToTeam ?team ;
                  prop:elo ?elo .
          OPTIONAL {{ ?record prop:dateFrom ?dateFrom . }}
          OPTIONAL {{ ?record prop:dateTo ?dateTo . }}
          BIND(COALESCE(?dateFrom, ?dateTo) AS ?d)
          FILTER(BOUND(?d))
          FILTER(xsd:date(?d) >= "{date_limit}"^^xsd:date)
        }}
        ORDER BY ?teamLabel ?d
        """
    )

    grouped: dict[str, list[tuple[date, float]]] = {team: [] for team in selected_teams}
    for row in elo_rows:
        team = row.get("teamLabel", "")
        raw_date = row.get("d", "")
        if team not in grouped or len(raw_date) < 10:
            continue
        try:
            point_date = date.fromisoformat(raw_date[:10])
            elo_value = float(row.get("elo", "0"))
        except Exception:
            continue
        grouped[team].append((point_date, elo_value))

    series: dict[str, list[tuple[date, float]]] = {}
    for team, points in grouped.items():
        if len(points) >= 2:
            series[team] = points

    if not series:
        return None

    all_dates = [point_date for points in series.values() for point_date, _ in points]
    all_elos = [value for points in series.values() for _, value in points]

    min_date = min(all_dates)
    max_date = max(all_dates)
    min_elo = min(all_elos)
    max_elo = max(all_elos)

    plot_min_date = min_date
    plot_max_date = max_date

    min_ord = plot_min_date.toordinal()
    max_ord = plot_max_date.toordinal()
    if min_ord == max_ord:
        max_ord = min_ord + 1

    if min_elo == max_elo:
        min_elo -= 20.0
        max_elo += 20.0
    else:
        pad = max((max_elo - min_elo) * 0.08, 15.0)
        min_elo -= pad
        max_elo += pad

    width, height = 1040, 430
    margin_left, margin_right, margin_top, margin_bottom = 62, 22, 22, 52
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    def x_coord(point_date: date) -> float:
        ratio = (point_date.toordinal() - min_ord) / float(max_ord - min_ord)
        return margin_left + ratio * plot_w

    def y_coord(value: float) -> float:
        ratio = (value - min_elo) / float(max_elo - min_elo)
        return margin_top + (1.0 - ratio) * plot_h

    palette = [
        "#0a66c2",
        "#0d9488",
        "#ef4444",
        "#f59e0b",
        "#7c3aed",
        "#2563eb",
        "#db2777",
        "#16a34a",
        "#dc2626",
        "#0891b2",
        "#9333ea",
        "#ca8a04",
        "#475569",
        "#059669",
        "#e11d48",
        "#0284c7",
        "#854d0e",
        "#4f46e5",
        "#65a30d",
        "#be123c",
    ]
    svg_parts: list[str] = [
        f'<svg class="elo-svg" viewBox="0 0 {width} {height}" role="img" aria-label="Evolucion Elo ultimos {years} anos">',
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_w}" height="{plot_h}" fill="transparent"/>',
    ]

    for idx in range(6):
        y = margin_top + (plot_h * idx / 5.0)
        y_value = max_elo - ((max_elo - min_elo) * idx / 5.0)
        svg_parts.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" stroke="rgba(80,101,132,0.24)" stroke-width="1"/>'
        )
        svg_parts.append(
            f'<text x="{margin_left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="currentColor">{int(round(y_value))}</text>'
        )

    tick_years = list(range(plot_min_date.year, plot_max_date.year + 1))
    for tick_year in tick_years:
        tick_date = date(tick_year, 1, 1)
        if tick_date < plot_min_date or tick_date > plot_max_date:
            continue
        x = x_coord(tick_date)
        svg_parts.append(
            f'<line x1="{x:.2f}" y1="{margin_top}" x2="{x:.2f}" y2="{height - margin_bottom}" stroke="rgba(80,101,132,0.18)" stroke-width="1"/>'
        )
        svg_parts.append(
            f'<text x="{x:.2f}" y="{height - margin_bottom + 18}" text-anchor="middle" font-size="11" fill="currentColor">{tick_date.strftime("%Y")}</text>'
        )

    def format_elo_value(value: float) -> str:
        return f"{value:.1f}".rstrip("0").rstrip(".")

    legend_items: list[str] = []
    extreme_items: list[str] = []
    team_count = len(series)
    default_visible = min(3, team_count)  # Show first 3 teams by default
    
    for idx, team in enumerate(series.keys()):
        team_id = f"team-{idx}"
        is_visible = idx < default_visible
        color = palette[idx % len(palette)]
        points_str = " ".join(f"{x_coord(d):.2f},{y_coord(v):.2f}" for d, v in series[team])
        last_date, last_elo = series[team][-1]
        visibility_style = "" if is_visible else "display:none"
        
        svg_parts.append(
            f'<g data-team="{escape(team_id)}" style="{visibility_style}"><polyline points="{points_str}" fill="none" stroke="{color}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for point_date, elo_value in series[team]:
            point_x = x_coord(point_date)
            point_y = y_coord(elo_value)
            svg_parts.append(
                f'<circle class="elo-point-dot" cx="{point_x:.2f}" cy="{point_y:.2f}" r="2.7" fill="{color}" opacity="0" pointer-events="none"/>'
            )
            svg_parts.append(
                f'<circle class="elo-hit-point" tabindex="0" data-elo-team="{escape(team)}" data-team-id="{escape(team_id)}" data-elo-color="{color}" data-elo-date="{escape(format_display_date(point_date.isoformat()))}" data-elo-elo="{str(elo_value)}" cx="{point_x:.2f}" cy="{point_y:.2f}" r="7.5" fill="transparent"/>'
            )
        svg_parts.append(
            f'<circle cx="{x_coord(last_date):.2f}" cy="{y_coord(last_elo):.2f}" r="3.2" fill="{color}" pointer-events="none"/></g>'
        )
        legend_items.append(
            f'<span class="elo-item" data-team-id="{escape(team_id)}" data-team-active="{str(is_visible).lower()}"><span class="elo-swatch" style="background:{color};"></span>{escape(team)}</span>'
        )
        if show_extremes:
            max_date_point, max_elo_point = max(series[team], key=lambda point: point[1])
            min_date_point, min_elo_point = min(series[team], key=lambda point: point[1])
            extreme_items.append(
                '<div class="elo-extreme-item">'
                f'<span class="elo-extreme-team"><span class="elo-swatch" style="background:{color};"></span>{escape(team)}</span>'
                '<span class="elo-extreme-values">'
                f'<span>Maximo<strong>{escape(format_elo_value(max_elo_point))}</strong><small>{escape(format_display_date(max_date_point.isoformat()))}</small></span>'
                f'<span>Minimo<strong>{escape(format_elo_value(min_elo_point))}</strong><small>{escape(format_display_date(min_date_point.isoformat()))}</small></span>'
                '</span>'
                '</div>'
            )

    svg_parts.append("</svg>")
    legend_html = f'<div class="elo-legend">{"".join(legend_items)}</div>' if len(series) > 1 and not show_extremes else ""
    extremes_html = f'<div class="elo-extremes">{"".join(extreme_items)}</div>' if extreme_items else ""
    chart_html = (
        '<div class="elo-chart" data-elo-chart>'
        + "".join(svg_parts)
        + legend_html
        + extremes_html
        + "</div>"
    )

    return {
        "title": f"Tendencia Elo (ultimos {years} anos)",
        "kind": "html",
        "html": chart_html,
    }


def build_match_pitch_panel(events: list[dict[str, object]]) -> list[dict[str, object]]:
    pass_events = [item for item in events if str(item.get("type", "")).lower() == "pass"]
    shot_events = [item for item in events if item.get("is_shot") is True]

    pass_events_json = json.dumps(pass_events, ensure_ascii=True).replace("</", "<\\/")
    shot_events_json = json.dumps(shot_events, ensure_ascii=True).replace("</", "<\\/")

    pass_html = f"""
    <div class="event-widget pass-widget" data-pass-widget>
        <p class="event-note">Campo completo de pases con filtros independientes por equipo, jugador y resultado.</p>
        <div class="event-toolbar">
            <label class="event-field">
                <span>Equipo</span>
                <select data-pass-team>
                    <option value="all">Todos</option>
                </select>
            </label>
            <label class="event-field">
                <span>Jugador</span>
                <select data-pass-player>
                    <option value="all">Todos</option>
                </select>
            </label>
            <label class="event-field">
                <span>Resultado</span>
                <select data-pass-outcome>
                    <option value="all">Todos</option>
                    <option value="completed">Completado</option>
                    <option value="failed">Fallido</option>
                </select>
            </label>
        </div>
        <div class="pitch-wrap pitch-wrap-large">
            <svg class="pass-pitch-svg" viewBox="0 0 1000 620" role="img" aria-label="Mapa de pases"></svg>
        </div>
        <script type="application/json" data-pass-data>{pass_events_json}</script>
    </div>
    """

    shot_html = f"""
    <div class="event-widget shot-widget" data-shot-widget>
        <div class="shot-panel-head">
            <p class="event-note">Media cancha en ataque y porteria de destino para el disparo seleccionado.</p>
            <div class="shot-legend" data-shot-legend></div>
        </div>
        <div class="event-toolbar">
            <label class="event-field">
                <span>Equipo</span>
                <select data-shot-team>
                    <option value="all">Todos</option>
                </select>
            </label>
            <label class="event-field">
                <span>Jugador</span>
                <select data-shot-player>
                    <option value="all">Todos</option>
                </select>
            </label>
            <label class="event-field">
                <span>Resultado</span>
                <select data-shot-result>
                    <option value="all">Todos</option>
                    <option value="goals">Gol</option>
                    <option value="non_goals">No gol</option>
                </select>
            </label>
        </div>
        <div class="shot-layout">
            <div class="shot-map-wrap">
                <svg class="shot-pitch-svg" viewBox="0 0 680 520" role="img" aria-label="Mapa de disparos"></svg>
            </div>
            <div class="shot-goal-column">
                <div class="shot-goal-wrap">
                    <svg class="shot-goal-svg" viewBox="0 0 320 380" role="img" aria-label="Porteria del disparo seleccionado"></svg>
                </div>
                <dl class="shot-detail" data-shot-detail></dl>
            </div>
        </div>
        <script type="application/json" data-shot-data>{shot_events_json}</script>
    </div>
    """

    return [
        {
            "title": "Mapa de pases",
            "kind": "html",
            "html": pass_html,
        },
        {
            "title": "Mapa de disparos",
            "kind": "html",
            "html": shot_html,
        },
    ]


def render_page_factory(
    *,
    app_boot_token: str,
    brand_logo_path: str,
    build_nav,
    get_search,
    get_filters,
    onboarding_choices,
    available_weeks,
):
    def render_page(
        current: str,
        *,
        title: str,
        current_view: str | None = None,
        subtitle: str = "",
        cards: list[dict[str, str]] | None = None,
        headers: list[str] | None = None,
        rows: list[list[object]] | None = None,
        panels: list[dict[str, object]] | None = None,
        team_a: str = "",
        team_b: str = "",
        error: str = "",
        extra_context: dict[str, object] | None = None,
    ):
        q = get_search()
        filters = get_filters()
        onboarding = onboarding_choices()

        context = {
            "page_title": title,
            "app_name": "DataGol",
            "app_boot_token": app_boot_token,
            "brand_logo_path": brand_logo_path,
            "current_section": current,
            "current_view": current_view or current,
            "title": title,
            "subtitle": subtitle,
            "q": q,
            "nav_items": build_nav(current, filters, q),
            "filter_state": filters,
            "onboarding": onboarding,
            "onboarding_required": not onboarding["complete"],
            "current_page_url": current_page_url(),
            "competition_options": ["all", *onboarding["competition_options"]],
            "season_options": ["all", *onboarding["season_options"]],
            "jornadas_options": available_weeks(),
            "cards": cards or [],
            "headers": headers or [],
            "rows": rows or [],
            "panels": panels or [],
            "team_a": team_a,
            "team_b": team_b,
            "error": error,
        }
        if extra_context:
            context.update(extra_context)

        return render_template("page.html", **context)

    return render_page
