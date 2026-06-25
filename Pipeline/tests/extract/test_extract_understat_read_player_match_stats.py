from __future__ import annotations

import pandas as pd
import pytest

from src.extract.extract_understat_read_player_match_stats import read_player_match_stats_with_failures


class FakeUnderstatReader:
    def __init__(self, frames: dict[int, pd.DataFrame], failures: set[int] | None = None) -> None:
        self.frames = frames
        self.failures = failures or set()

    def read_player_match_stats(self, match_id: int) -> pd.DataFrame:
        if match_id in self.failures:
            raise AttributeError("'list' object has no attribute 'values'")
        return self.frames.get(match_id, pd.DataFrame())


def schedule_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "league": "GER-Bundesliga",
                "season": "2425",
                "game": "2025-02-08 Equipo A-Equipo B",
                "game_id": 1,
                "date": "2025-02-08 13:30:00",
                "home_team": "Equipo A",
                "away_team": "Equipo B",
                "url": "https://understat.com/match/1",
            },
            {
                "league": "GER-Bundesliga",
                "season": "2425",
                "game": "2025-02-09 Equipo C-Equipo D",
                "game_id": 2,
                "date": "2025-02-09 13:30:00",
                "home_team": "Equipo C",
                "away_team": "Equipo D",
                "url": "https://understat.com/match/2",
            },
        ]
    )


def match_frame(game_id: int, player: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "league": "GER-Bundesliga",
                "season": "2425",
                "game": f"game-{game_id}",
                "team": "Equipo",
                "player": player,
                "game_id": game_id,
                "player_id": game_id * 100,
            }
        ]
    )


def test_read_player_match_stats_skips_failed_match_after_retry_failure() -> None:
    primary = FakeUnderstatReader(frames={1: match_frame(1, "Jugador A")}, failures={2})
    retry = FakeUnderstatReader(frames={}, failures={2})

    df, failures = read_player_match_stats_with_failures(primary, lambda: retry, schedule_frame())

    assert df["game_id"].tolist() == [1]
    assert failures["game_id"].tolist() == [2]
    assert failures.loc[0, "retry_error"] == "AttributeError"
    assert "values" in failures.loc[0, "retry_message"]


def test_read_player_match_stats_uses_retry_when_retry_succeeds() -> None:
    primary = FakeUnderstatReader(frames={1: match_frame(1, "Jugador A")}, failures={2})
    retry = FakeUnderstatReader(frames={2: match_frame(2, "Jugador B")})

    df, failures = read_player_match_stats_with_failures(primary, lambda: retry, schedule_frame())

    assert df["game_id"].tolist() == [1, 2]
    assert failures.empty


def test_read_player_match_stats_raises_when_all_matches_fail() -> None:
    primary = FakeUnderstatReader(frames={}, failures={1, 2})
    retry = FakeUnderstatReader(frames={}, failures={1, 2})

    with pytest.raises(ValueError, match="ningun partido"):
        read_player_match_stats_with_failures(primary, lambda: retry, schedule_frame())
