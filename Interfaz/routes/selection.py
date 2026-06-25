from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import jsonify, redirect, request, session, url_for


def register_selection_routes(app, deps) -> None:
    ensure_onboarding_boot_token = deps["ensure_onboarding_boot_token"]
    available_onboarding_options = deps["available_onboarding_options"]
    selected_onboarding_pair = deps["selected_onboarding_pair"]

    def option_with_urls(item: dict[str, object]) -> dict[str, object]:
        logo = str(item.get("logo", "") or "")
        return {
            **item,
            "logo_url": url_for("static", filename=logo) if logo else "",
        }

    def clean_next_url(value: str) -> str:
        parsed = urlsplit(value or "")
        if parsed.scheme or parsed.netloc:
            return url_for("home")

        filtered_params = [
            (key, param_value)
            for key, param_value in parse_qsl(parsed.query, keep_blank_values=True)
            if key not in {"competition", "season", "jornadas", "date_from", "date_to"}
        ]
        query = urlencode(filtered_params, doseq=True)
        return urlunsplit(("", "", parsed.path or url_for("home"), query, parsed.fragment))

    @app.route("/selection", methods=["POST"])
    def save_selection():
        ensure_onboarding_boot_token()
        selected_competition_iri = next(
            (value for value in request.form.getlist("competitions") if value),
            "",
        )
        selected_season_iri = next(
            (value for value in request.form.getlist("seasons") if value),
            "",
        )

        selected_pair = selected_onboarding_pair(selected_competition_iri, selected_season_iri)

        if selected_pair:
            selected_league = selected_pair["league"]
            selected_season = selected_pair["season"]
            session["onboarding_competitions"] = [str(selected_league.get("label", ""))]
            session["onboarding_competition_iris"] = [selected_competition_iri]
            session["onboarding_seasons"] = [str(selected_season.get("label", ""))]
            session["onboarding_season_iris"] = [selected_season_iri]
        else:
            for key in (
                "onboarding_competitions",
                "onboarding_competition_iris",
                "onboarding_seasons",
                "onboarding_season_iris",
            ):
                session.pop(key, None)

        return redirect(clean_next_url(request.form.get("next", url_for("home"))))

    @app.route("/selection/options")
    def selection_options():
        ensure_onboarding_boot_token()
        try:
            available = available_onboarding_options()
        except Exception as exc:
            return jsonify({"leagues": [], "seasons": [], "error": str(exc)}), 503

        return jsonify(
            {
                "leagues": [option_with_urls(item) for item in available["leagues"]],
                "seasons": available["seasons"],
                "selected_competition_iris": session.get("onboarding_competition_iris", []),
                "selected_season_iris": session.get("onboarding_season_iris", []),
            }
        )
