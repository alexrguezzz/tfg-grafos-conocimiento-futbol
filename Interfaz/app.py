from __future__ import annotations

from threading import Timer
import os
import secrets
import socket
import webbrowser

from flask import Flask

from config import (
    BRAND_LOGO_PATH,
    GRAPHDB_ENDPOINT,
    PREFIXES,
    SECRET_KEY,
)
from routes.compare import register_compare_routes
from routes.competition import register_competition_routes
from routes.home import register_home_routes
from routes.matches import register_matches_routes
from routes.players import register_players_routes
from routes.search import register_search_routes
from routes.selection import register_selection_routes
from routes.teams import register_teams_routes
from services.filters import (
    build_nav,
    build_url,
    filter_clauses,
    get_filters,
    get_search,
    match_scope_clauses as build_match_scope_clauses,
    text_filter,
)
from services.matches import get_filtered_matches as fetch_filtered_matches
from services.onboarding import (
    available_onboarding_options as fetch_available_onboarding_options,
    ensure_onboarding_boot_token as ensure_onboarding,
    onboarding_choices as build_onboarding_choices,
    onboarding_complete as is_onboarding_complete,
    onboarding_label_clauses as build_onboarding_label_clauses,
    onboarding_match_clauses as build_onboarding_match_clauses,
    onboarding_resource_clauses as build_onboarding_resource_clauses,
    selected_onboarding_pair as fetch_selected_onboarding_pair,
)
from services.query import get_statement_lines as fetch_statement_lines
from services.query import run_query as execute_query
from services.ui import (
    build_elo_panel as create_elo_panel,
    build_match_pitch_panel,
    no_data_panel,
    render_page_factory,
)
from services.utils import format_match_datetime, safe_bool, safe_int, sparql_iri, sparql_string

app = Flask(__name__)
app.secret_key = SECRET_KEY
APP_BOOT_TOKEN = secrets.token_hex(16)
DEFAULT_HOST = os.getenv("DATAGOL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("DATAGOL_PORT", "5000"))


def run_query(query: str, timeout: float | None = None) -> list[dict[str, str]]:
    return execute_query(GRAPHDB_ENDPOINT, query, timeout=timeout)


def get_statement_lines(subject_iri: str, predicate_iri: str, timeout: float | None = None) -> list[str]:
    return fetch_statement_lines(
        GRAPHDB_ENDPOINT,
        subject_iri=subject_iri,
        predicate_iri=predicate_iri,
        timeout=timeout,
    )


def ensure_onboarding_boot_token() -> None:
    ensure_onboarding(APP_BOOT_TOKEN)


def onboarding_complete() -> bool:
    return is_onboarding_complete(APP_BOOT_TOKEN)


def onboarding_choices() -> dict[str, object]:
    return build_onboarding_choices(APP_BOOT_TOKEN, run_query=run_query, prefixes=PREFIXES)


def available_onboarding_options() -> dict[str, object]:
    return fetch_available_onboarding_options(run_query, PREFIXES)


def selected_onboarding_pair(competition_iri: str, season_iri: str) -> dict[str, dict[str, object]] | None:
    return fetch_selected_onboarding_pair(
        run_query,
        PREFIXES,
        competition_iri=competition_iri,
        season_iri=season_iri,
    )


def onboarding_label_clauses(*, competition_label: str | None = None, season_label: str | None = None) -> str:
    return build_onboarding_label_clauses(
        APP_BOOT_TOKEN,
        competition_label=competition_label,
        season_label=season_label,
    )


def onboarding_resource_clauses(*, competition_var: str | None = None, season_var: str | None = None) -> str:
    return build_onboarding_resource_clauses(
        APP_BOOT_TOKEN,
        competition_var=competition_var,
        season_var=season_var,
    )


def onboarding_match_clauses(match_var: str = "?m", *, date_var: str = "?date", week_var: str = "?week") -> str:
    return build_onboarding_match_clauses(
        APP_BOOT_TOKEN,
        match_var=match_var,
        date_var=date_var,
        week_var=week_var,
    )


def match_scope_clauses(
    filters: dict[str, object],
    *,
    match_var: str = "?m",
    date_var: str = "?date",
    week_var: str = "?week",
) -> str:
    return build_match_scope_clauses(
        filters,
        onboarding_match_clauses=onboarding_match_clauses,
        match_var=match_var,
        date_var=date_var,
        week_var=week_var,
    )


def default_week_options() -> list[str]:
    return [str(week) for week in range(1, 39)]


def available_weeks() -> list[str]:
    # Las jornadas no deben bloquear el renderizado de toda la pagina. Las cinco
    # ligas cargadas trabajan con calendarios de hasta 38 jornadas.
    return default_week_options()


def get_filtered_matches(filters: dict[str, object], q: str = "") -> list[dict[str, str]]:
    return fetch_filtered_matches(
        run_query=run_query,
        prefixes=PREFIXES,
        match_scope_clauses=match_scope_clauses,
        text_filter=text_filter,
        filters=filters,
        q=q,
    )


def build_elo_panel(
    team_labels: list[str],
    years: int = 5,
    max_teams: int | None = 6,
    show_extremes: bool = False,
) -> dict[str, object] | None:
    return create_elo_panel(
        run_query=run_query,
        prefixes=PREFIXES,
        sparql_string=sparql_string,
        team_labels=team_labels,
        years=years,
        max_teams=max_teams,
        show_extremes=show_extremes,
    )


render_page = render_page_factory(
    app_boot_token=APP_BOOT_TOKEN,
    brand_logo_path=BRAND_LOGO_PATH,
    build_nav=build_nav,
    get_search=get_search,
    get_filters=get_filters,
    onboarding_choices=onboarding_choices,
    available_weeks=available_weeks,
)


register_selection_routes(
    app,
    {
        "ensure_onboarding_boot_token": ensure_onboarding_boot_token,
        "available_onboarding_options": available_onboarding_options,
        "selected_onboarding_pair": selected_onboarding_pair,
    },
)

shared_route_deps = {
    "render_page": render_page,
    "get_search": get_search,
    "get_filters": get_filters,
    "onboarding_complete": onboarding_complete,
    "run_query": run_query,
    "PREFIXES": PREFIXES,
    "no_data_panel": no_data_panel,
}

register_search_routes(
    app,
    {
        **shared_route_deps,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "filter_clauses": filter_clauses,
        "text_filter": text_filter,
        "build_url": build_url,
    },
)

register_home_routes(
    app,
    {
        **shared_route_deps,
        "filter_clauses": filter_clauses,
        "onboarding_label_clauses": onboarding_label_clauses,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "get_statement_lines": get_statement_lines,
        "match_scope_clauses": match_scope_clauses,
        "text_filter": text_filter,
        "safe_int": safe_int,
        "format_match_datetime": format_match_datetime,
        "build_url": build_url,
        "sparql_string": sparql_string,
    },
)

register_competition_routes(
    app,
    {
        **shared_route_deps,
        "onboarding_label_clauses": onboarding_label_clauses,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "filter_clauses": filter_clauses,
        "text_filter": text_filter,
        "get_filtered_matches": get_filtered_matches,
        "format_match_datetime": format_match_datetime,
        "build_url": build_url,
    },
)

register_matches_routes(
    app,
    {
        **shared_route_deps,
        "get_filtered_matches": get_filtered_matches,
        "format_match_datetime": format_match_datetime,
        "build_url": build_url,
        "sparql_iri": sparql_iri,
        "safe_bool": safe_bool,
        "safe_int": safe_int,
        "build_match_pitch_panel": build_match_pitch_panel,
    },
)

register_teams_routes(
    app,
    {
        **shared_route_deps,
        "match_scope_clauses": match_scope_clauses,
        "onboarding_label_clauses": onboarding_label_clauses,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "text_filter": text_filter,
        "filter_clauses": filter_clauses,
        "build_elo_panel": build_elo_panel,
        "build_url": build_url,
        "format_match_datetime": format_match_datetime,
        "sparql_string": sparql_string,
    },
)

register_players_routes(
    app,
    {
        **shared_route_deps,
        "match_scope_clauses": match_scope_clauses,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "filter_clauses": filter_clauses,
        "onboarding_label_clauses": onboarding_label_clauses,
        "text_filter": text_filter,
        "build_url": build_url,
        "format_match_datetime": format_match_datetime,
        "sparql_string": sparql_string,
    },
)

register_compare_routes(
    app,
    {
        **shared_route_deps,
        "match_scope_clauses": match_scope_clauses,
        "onboarding_match_clauses": onboarding_match_clauses,
        "onboarding_resource_clauses": onboarding_resource_clauses,
        "sparql_string": sparql_string,
        "filter_clauses": filter_clauses,
        "build_url": build_url,
        "build_elo_panel": build_elo_panel,
    },
)


def find_available_port(host: str, preferred_port: int, attempts: int = 20) -> int:
    for port in range(preferred_port, preferred_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                continue
            return port
    return preferred_port


if __name__ == "__main__":
    port = find_available_port(DEFAULT_HOST, DEFAULT_PORT)
    url = f"http://{DEFAULT_HOST}:{port}"
    print(f"Abriendo DataGol en {url}")
    Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=DEFAULT_HOST, port=port, debug=False)
