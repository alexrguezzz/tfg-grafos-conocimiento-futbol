from __future__ import annotations

import pandas as pd

from src.transform.build_player_match_participation import (
    find_understat_player_match_coverage_gaps,
    format_understat_player_match_coverage_gap_example,
    matching_understat_schedule_files,
)


def test_find_understat_player_match_coverage_gaps_detects_schedule_games_missing_from_stats(runtime_dir) -> None:
    schedule_path = runtime_dir / "read_schedule_GER-Bundesliga_2024-2025.csv"
    player_match_path = runtime_dir / "read_player_match_stats_GER-Bundesliga_2024-2025.csv"
    pd.DataFrame(
        [
            {
                "league": "GER-Bundesliga",
                "season": "2425",
                "game": "2025-02-08 Equipo A-Equipo B",
                "game_id": "27929",
                "date": "2025-02-08 13:30:00",
                "home_team": "Equipo A",
                "away_team": "Equipo B",
                "url": "https://understat.com/match/27929",
                "has_data": "True",
            },
            {
                "league": "GER-Bundesliga",
                "season": "2425",
                "game": "2025-02-09 Holstein Kiel-VfL Bochum 1848",
                "game_id": "27930",
                "date": "2025-02-09 13:30:00",
                "home_team": "Holstein Kiel",
                "away_team": "VfL Bochum 1848",
                "url": "https://understat.com/match/27930",
                "has_data": "True",
            },
        ]
    ).to_csv(schedule_path, index=False)
    pd.DataFrame([{"game_id": "27929", "player": "Jugador A"}]).to_csv(player_match_path, index=False)

    gaps = find_understat_player_match_coverage_gaps([schedule_path], [player_match_path])

    assert len(gaps) == 1
    assert gaps.loc[0, "game_id"] == "27930"
    assert "Holstein Kiel-VfL Bochum" in format_understat_player_match_coverage_gap_example(gaps.loc[0])


def test_find_understat_player_match_coverage_gaps_ignores_schedule_games_without_data(runtime_dir) -> None:
    schedule_path = runtime_dir / "read_schedule_GER-Bundesliga_2024-2025.csv"
    player_match_path = runtime_dir / "read_player_match_stats_GER-Bundesliga_2024-2025.csv"
    pd.DataFrame(
        [
            {
                "game": "2025-02-09 Holstein Kiel-VfL Bochum 1848",
                "game_id": "27930",
                "has_data": "False",
            }
        ]
    ).to_csv(schedule_path, index=False)
    pd.DataFrame([{"game_id": "27929", "player": "Jugador A"}]).to_csv(player_match_path, index=False)

    gaps = find_understat_player_match_coverage_gaps([schedule_path], [player_match_path])

    assert gaps.empty


def test_matching_understat_schedule_files_uses_player_match_scope_keys(monkeypatch, runtime_dir) -> None:
    schedule_path = runtime_dir / "read_schedule_GER-Bundesliga_2024-2025.csv"
    other_schedule_path = runtime_dir / "read_schedule_ESP-La_Liga_2024-2025.csv"
    player_match_path = runtime_dir / "read_player_match_stats_GER-Bundesliga_2024-2025.csv"
    schedule_path.write_text("game_id\n27930\n", encoding="utf-8")
    other_schedule_path.write_text("game_id\n1\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.transform.build_player_match_participation.list_raw_files",
        lambda *parts, pattern: [schedule_path, other_schedule_path],
    )

    assert matching_understat_schedule_files([player_match_path]) == [schedule_path]
