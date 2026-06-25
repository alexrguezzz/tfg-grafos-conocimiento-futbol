from __future__ import annotations

import pandas as pd

from transform import build_stadiums


def test_stadium_query_candidates_derives_suffixes_from_name() -> None:
    candidates = build_stadiums.stadium_query_candidates("Sponsor Name Longarena")

    assert candidates[:3] == [
        "Sponsor Name Longarena",
        "Name Longarena",
        "Longarena",
    ]


def test_venue_home_team_hints_uses_most_common_home_team() -> None:
    matches = pd.DataFrame(
        [
            {"venue": "Example Stadium", "id_home_team": "Team_A"},
            {"venue": "Example Stadium", "id_home_team": "Team_A"},
            {"venue": "Example Stadium", "id_home_team": "Team_B"},
        ]
    )

    assert build_stadiums.venue_home_team_hints(matches) == {
        "example stadium": "Team_A",
    }


def test_country_matches_uses_source_country_codes_without_translated_name_table() -> None:
    assert build_stadiums.country_matches("Espa\u00f1a", "Spain", "es", "es")
    assert build_stadiums.country_matches("Deutschland", "Germany", "de", "de")
    assert build_stadiums.country_matches("United Kingdom", "United Kingdom", "gb", "gb")
    assert not build_stadiums.country_matches("Espa\u00f1a", "Spain")


def test_nominatim_result_accepts_localized_country_code_and_keeps_expected_country() -> None:
    result = build_stadiums.nominatim_result_from_item(
        {
            "osm_type": "way",
            "osm_id": 123,
            "lat": "39.4746136",
            "lon": "-0.3582370",
            "name": "",
            "address": {
                "city": "Val\u00e8ncia",
                "country": "Espa\u00f1a",
                "country_code": "es",
            },
        },
        "Spain",
        "es",
    )

    assert result["status"] == "ok"
    assert result["city"] == "Val\u00e8ncia"
    assert result["country"] == "Spain"
    assert result["id_osm"] == "way/123"


def test_grouped_stadium_rows_include_original_venue_names() -> None:
    groups = [
        {
            "venues": ["Sponsor Stadium", "Historic Ground"],
            "resolved_records": [
                {
                    "name": "Canonical Stadium",
                    "city": "Example City",
                    "country": "Example Country",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "id_wikidata": "Q1",
                }
            ],
            "unresolved": [],
        }
    ]
    venue_counts = {"Sponsor Stadium": 3, "Historic Ground": 1}

    rows, unresolved_rows, venue_to_stadium_id = build_stadiums.build_grouped_stadium_rows(
        groups,
        venue_counts,
    )

    assert unresolved_rows == []
    assert rows[0]["venue_name"] == "Sponsor Stadium | Historic Ground"
    assert venue_to_stadium_id == {
        "Sponsor Stadium": "Canonical_Stadium",
        "Historic Ground": "Canonical_Stadium",
    }


def test_resolve_stadium_uses_team_home_venue_fallback(monkeypatch) -> None:
    monkeypatch.setattr(build_stadiums, "REMOTE_LOOKUP_ENABLED", True)
    monkeypatch.setattr(build_stadiums, "is_remote_time_budget_exceeded", lambda: False)
    monkeypatch.setattr(build_stadiums, "wikidata_label_batch_search", lambda *args: {"status": "not_found"})
    monkeypatch.setattr(build_stadiums, "wikidata_search", lambda *args: {"status": "not_found"})
    monkeypatch.setattr(build_stadiums, "nominatim_search", lambda *args: {"status": "not_found"})
    monkeypatch.setattr(
        build_stadiums,
        "wikidata_team_home_venue",
        lambda *args: {
            "status": "ok",
            "name": "Resolved Home Venue",
            "city": "Example City",
            "country": "Example Country",
            "latitude": 1.0,
            "longitude": 2.0,
        },
    )

    resolved, reason = build_stadiums.resolve_stadium(
        "Unmatched Commercial Name",
        "Example Country",
        "xc",
        "Example_Team",
        {},
        {},
        {},
        {},
    )

    assert reason == ""
    assert resolved["name"] == "Resolved Home Venue"


def test_wikidata_label_record_rejects_non_stadium_name_mismatch() -> None:
    chosen = build_stadiums.choose_wikidata_label_record(
        "Commercial Naming Ground",
        "Example Country",
        [
            {
                "status": "ok",
                "name": "Unrelated Person",
                "country": "",
                "latitude": 40.4465,
                "longitude": -3.71926,
                "id_wikidata": "Q999",
                "is_stadium": False,
            }
        ],
    )

    assert chosen == {"status": "not_found"}


def test_enrich_place_details_uses_reverse_geocoding_when_city_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(build_stadiums, "nominatim_search", lambda *args: {"status": "not_found"})
    monkeypatch.setattr(
        build_stadiums,
        "nominatim_reverse_search",
        lambda *args: {
            "status": "ok",
            "city": "Example City",
            "country": "Example Country",
            "id_osm": "relation/123",
        },
    )

    enriched = build_stadiums.enrich_place_details(
        "Example Ground",
        "Example Country",
        "xc",
        {
            "status": "ok",
            "name": "Example Ground",
            "city": "",
            "country": "Example Country",
            "latitude": 39.474722,
            "longitude": -0.358333,
            "id_wikidata": "Q1",
        },
        {},
    )

    assert enriched["city"] == "Example City"
    assert enriched["country"] == "Example Country"
    assert enriched["id_osm"] == "relation/123"


def test_grouped_stadium_rows_marks_incomplete_required_fields_as_unresolved() -> None:
    groups = [
        {
            "venues": ["Example Stadium", "Example"],
            "resolved_records": [
                {
                    "name": "Example Stadium",
                    "city": "",
                    "country": "Spain",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "id_wikidata": "Q1",
                }
            ],
            "unresolved": [
                ("Example Stadium", "missing_required_fields:city"),
                ("Example", "missing_required_fields:city"),
            ],
        }
    ]

    rows, unresolved_rows, venue_to_stadium_id = build_stadiums.build_grouped_stadium_rows(
        groups,
        {"Example Stadium": 2, "Example": 1},
    )

    assert pd.isna(rows[0]["city"])
    assert rows[0]["venue_name"] == "Example Stadium | Example"
    assert unresolved_rows == [
        {
            "name": "Example Stadium",
            "reason": "missing_required_fields:city",
        }
    ]
    assert venue_to_stadium_id == {
        "Example Stadium": "Example_Stadium",
        "Example": "Example_Stadium",
    }


def test_resolve_venues_retries_not_found_with_fresh_remote_budget(monkeypatch) -> None:
    attempts: dict[str, int] = {}
    budget_starts: list[bool] = []

    def fake_resolve_stadium(
        name,
        country,
        country_code,
        home_team_id,
        wikidata_cache,
        team_venue_cache,
        nominatim_cache,
        wikidata_label_index,
    ):
        attempts[name] = attempts.get(name, 0) + 1
        if attempts[name] == 1:
            return {}, "not_found"
        return {"status": "ok", "name": name, "country": country, "team": home_team_id}, ""

    monkeypatch.setattr(build_stadiums, "REMOTE_LOOKUP_ENABLED", True)
    monkeypatch.setattr(build_stadiums, "WIKIDATA_BATCH_SEARCH_ENABLED", False)
    monkeypatch.setattr(build_stadiums, "UNRESOLVED_RETRY_ROUNDS", 1)
    monkeypatch.setattr(build_stadiums, "UNRESOLVED_RETRY_BASE_SECONDS", 0.0)
    monkeypatch.setattr(build_stadiums, "UNRESOLVED_RETRY_MAX_SECONDS", 0.0)
    monkeypatch.setattr(build_stadiums.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(build_stadiums, "start_remote_time_budget", lambda: budget_starts.append(True))
    monkeypatch.setattr(build_stadiums, "fetch_wikidata_label_index", lambda *args: {})
    monkeypatch.setattr(build_stadiums, "resolve_stadium", fake_resolve_stadium)

    resolved, reasons = build_stadiums.resolve_venues(
        ["Example Stadium"],
        {"example stadium": "Spain"},
        {"example stadium": "es"},
        {"example stadium": "Example_Team"},
        {},
        {},
        {},
    )

    assert attempts["Example Stadium"] == 2
    assert len(budget_starts) == 2
    assert resolved["Example Stadium"]["status"] == "ok"
    assert resolved["Example Stadium"]["team"] == "Example_Team"
    assert reasons["Example Stadium"] == ""
