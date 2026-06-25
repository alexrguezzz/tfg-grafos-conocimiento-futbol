from __future__ import annotations

import pandas as pd
import pytest

from validation import validate_external_context


def _write_external_context_fixture(root, *, include_weather_for_stadium_match: bool = True) -> None:
    canonical = root / "canonical"
    canonical.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "id_match": "match_with_stadium",
                "id_home_team": "Team_A",
                "id_away_team": "Team_B",
                "dateTime": "2025-02-03 20:45:00+01:00",
                "homeScore": "1",
                "awayScore": "2",
                "venue": "Known Stadium",
                "id_stadium": "Known_Stadium",
                "matchStatus": "completed",
            },
            {
                "id_match": "match_without_stadium",
                "id_home_team": "Team_C",
                "id_away_team": "Team_D",
                "dateTime": "2025-02-04 20:45:00+01:00",
                "homeScore": "0",
                "awayScore": "0",
                "venue": "",
                "id_stadium": "",
                "matchStatus": "completed",
            },
        ]
    ).to_csv(canonical / "matches.csv", index=False)

    pd.DataFrame(
        [
            {
                "id_stadium": "Known_Stadium",
                "name": "Known Stadium",
                "city": "Example City",
                "country": "Example Country",
                "latitude": "40.0",
                "longitude": "-3.0",
                "idWikidata": "",
                "idOsm": "",
            }
        ]
    ).to_csv(canonical / "stadiums.csv", index=False)

    weather_rows = []
    if include_weather_for_stadium_match:
        weather_rows.append(
            {
                "id_weatherObservation": "weather_match_with_stadium",
                "id_match": "match_with_stadium",
                "dateTime": "2025-02-03 20:00:00+01:00",
                "temperature": "12",
                "precipitation": "0",
                "rain": "0",
                "windSpeed": "7",
                "humidity": "60",
            }
        )
    pd.DataFrame(
        weather_rows,
        columns=[
            "id_weatherObservation",
            "id_match",
            "dateTime",
            "temperature",
            "precipitation",
            "rain",
            "windSpeed",
            "humidity",
        ],
    ).to_csv(canonical / "weather_observations.csv", index=False)

    pd.DataFrame(
        [
            {"id_teamMatchParticipation": "tmp_1", "id_match": "match_with_stadium", "id_team": "Team_A", "isHome": "true"},
            {"id_teamMatchParticipation": "tmp_2", "id_match": "match_with_stadium", "id_team": "Team_B", "isHome": "false"},
            {"id_teamMatchParticipation": "tmp_3", "id_match": "match_without_stadium", "id_team": "Team_C", "isHome": "true"},
            {"id_teamMatchParticipation": "tmp_4", "id_match": "match_without_stadium", "id_team": "Team_D", "isHome": "false"},
        ]
    ).to_csv(canonical / "team_match_participation.csv", index=False)


def test_external_context_allows_match_without_stadium_or_weather(monkeypatch, runtime_dir) -> None:
    _write_external_context_fixture(runtime_dir)
    monkeypatch.setattr(validate_external_context, "CANONICAL_DIR", runtime_dir / "canonical")
    monkeypatch.setattr(validate_external_context, "_ARGS", object())

    result = validate_external_context.main()

    assert any("sin id_stadium" in warning for warning in result["warnings"])


def test_external_context_still_requires_weather_when_stadium_exists(monkeypatch, runtime_dir) -> None:
    _write_external_context_fixture(runtime_dir, include_weather_for_stadium_match=False)
    monkeypatch.setattr(validate_external_context, "CANONICAL_DIR", runtime_dir / "canonical")
    monkeypatch.setattr(validate_external_context, "_ARGS", object())

    with pytest.raises(ValueError, match="sin observacion meteorologica"):
        validate_external_context.main()
