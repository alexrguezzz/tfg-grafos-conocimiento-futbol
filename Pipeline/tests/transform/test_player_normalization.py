from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import uuid

import pandas as pd
import pytest

from transform import player_normalization


def test_source_player_key_ignores_numeric_placeholder_without_source_id() -> None:
    key = player_normalization.build_source_player_key(
        source="whoscored",
        raw_name="0.0",
        source_player_id="0.0",
        team="FC Lorient",
        competition="FRA-Ligue 1",
        season="2526",
    )

    assert key is None


def test_source_player_key_keeps_id_backed_player_even_if_name_is_missing() -> None:
    key = player_normalization.build_source_player_key(
        source="whoscored",
        raw_name="0.0",
        source_player_id="438297.0",
        team="FC Lorient",
        competition="FRA-Ligue 1",
        season="2526",
    )

    assert key == "whoscored:438297"


def test_source_player_keys_for_frame_leave_placeholder_as_missing() -> None:
    df = pd.DataFrame(
        [
            {
                "league": "FRA-Ligue 1",
                "season": "2526",
                "team": "FC Lorient",
                "player": "0.0",
                "player_id": "0.0",
            },
            {
                "league": "FRA-Ligue 1",
                "season": "2526",
                "team": "FC Lorient",
                "player": "Daniel Semedo",
                "player_id": "",
            },
        ]
    )

    keys = player_normalization.build_source_player_keys_for_frame(
        df,
        source="whoscored",
        player_col="player",
        source_player_id_col="player_id",
        team_col="team",
        competition_col="league",
        season_col="season",
    )

    assert pd.isna(keys.iloc[0])
    assert keys.iloc[1] == "whoscored:FRA-Ligue_1:2025-2026:FC_Lorient:daniel_semedo"


def test_map_player_ids_allows_placeholder_without_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_normalization, "load_source_key_to_player_id", lambda: {})
    df = pd.DataFrame(
        [
            {
                "league": "FRA-Ligue 1",
                "season": "2526",
                "team": "FC Lorient",
                "player": "0.0",
                "player_id": "0.0",
            }
        ]
    )

    mapped = player_normalization.map_player_ids(
        df,
        source="whoscored",
        player_col="player",
        source_player_id_col="player_id",
        team_col="team",
        competition_col="league",
        season_col="season",
    )

    assert pd.isna(mapped.iloc[0])


def test_map_player_ids_still_fails_for_real_unmapped_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_normalization, "load_source_key_to_player_id", lambda: {})
    df = pd.DataFrame(
        [
            {
                "league": "FRA-Ligue 1",
                "season": "2526",
                "team": "FC Lorient",
                "player": "Daniel Semedo",
                "player_id": "",
            }
        ]
    )

    with pytest.raises(ValueError, match="daniel_semedo"):
        player_normalization.map_player_ids(
            df,
            source="whoscored",
            player_col="player",
            source_player_id_col="player_id",
            team_col="team",
            competition_col="league",
            season_col="season",
        )


def _gemini_identity_payload() -> dict[str, object]:
    return {
        "task": "identity_resolution",
        "observed_name": "Alex",
        "source_player_keys": ["espn:ESP-La_Liga:2025-2026:Team_A:alex"],
        "source_player_ids": ["understat:1"],
        "aliases": ["Alex"],
        "competition": ["ESP-La_Liga"],
        "season": ["2025-2026"],
        "team": ["Team_A"],
        "candidates": [
            {
                "candidate_id": "understat:2",
                "names": ["Alex Example"],
                "source_player_keys": ["understat:2"],
                "source_player_ids": ["understat:2"],
                "sources": ["understat"],
                "contexts": ["ESP-La_Liga | 2025-2026 | Team_A"],
            }
        ],
    }


def test_gemini_cache_keys_separate_primary_and_retry() -> None:
    payload = _gemini_identity_payload()

    assert player_normalization._gemini_payload_cache_key(payload) != player_normalization._gemini_payload_cache_key(
        payload,
        retry=True,
    )
    assert player_normalization._gemini_secondary_payload_cache_key(
        payload
    ) != player_normalization._gemini_secondary_payload_cache_key(payload, retry=True)


def test_identity_cache_key_uses_stable_candidate_identity() -> None:
    payload = _gemini_identity_payload()
    other_payload = _gemini_identity_payload()
    other_payload["candidates"] = [
        {
            "candidate_id": "volatile-root",
            "names": ["Alex Example"],
            "source_player_keys": ["understat:2"],
            "source_player_ids": ["understat:2"],
            "sources": ["understat"],
            "contexts": ["ESP-La_Liga | 2025-2026 | Team_A"],
        }
    ]

    assert player_normalization._gemini_payload_cache_key(payload) == player_normalization._gemini_payload_cache_key(
        other_payload
    )


def test_identity_cache_key_ignores_candidate_order() -> None:
    payload = _gemini_identity_payload()
    payload["candidates"] = [
        {
            "candidate_id": "understat:2",
            "names": ["Alex Example"],
            "source_player_keys": ["understat:2"],
            "source_player_ids": ["understat:2"],
            "sources": ["understat"],
        },
        {
            "candidate_id": "whoscored:3",
            "names": ["Alex Other"],
            "source_player_keys": ["whoscored:3"],
            "source_player_ids": ["whoscored:3"],
            "sources": ["whoscored"],
        },
    ]
    other_payload = _gemini_identity_payload()
    other_payload["candidates"] = list(reversed(payload["candidates"]))

    assert player_normalization._gemini_payload_cache_key(payload) == player_normalization._gemini_payload_cache_key(
        other_payload
    )


def test_cached_identity_decision_remaps_candidate_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_PROGRESS", "false")
    old_payload = _gemini_identity_payload()
    old_payload["candidates"][0]["candidate_id"] = "old-root"
    current_payload = _gemini_identity_payload()
    current_payload["candidates"][0]["candidate_id"] = "current-root"
    candidate_fingerprint = player_normalization._candidate_fingerprint_for_id(old_payload, "old-root")
    decision = player_normalization.GeminiDecision(
        resolved=True,
        candidate_id="old-root",
        confidence=0.95,
        reason="cached",
        candidate_fingerprint=candidate_fingerprint,
    )
    cache = {player_normalization._gemini_payload_cache_key(old_payload): decision}

    cached = player_normalization.ask_gemini(current_payload, cache, log_case=False)

    assert cached is not None
    assert cached.candidate_id == "current-root"


def test_name_enrichment_cache_keys_separate_repair_reasons() -> None:
    base_payload = {
        "task": "name_enrichment",
        "known_as": "Alex",
        "full_name": "Alex Example",
        "aliases": ["Alex"],
        "source_player_keys": ["espn:ESP-La_Liga:2025-2026:Team_A:alex"],
        "competitions": ["ESP-La_Liga"],
        "seasons": ["2025-2026"],
        "teams": ["Team_A"],
        "name_enrichment_policy": player_normalization.NAME_ENRICHMENT_POLICY_VERSION,
        "requires_unique_full_name": True,
        "requires_disambiguating_full_name": True,
    }
    duplicate_full_name_payload = {
        **base_payload,
        "reason": "duplicate_full_name",
        "duplicate_full_name": "Alex Example",
    }
    duplicate_player_id_payload = {
        **base_payload,
        "reason": "duplicate_player_id",
        "duplicate_player_id": "alex_example",
    }
    verification_payload = {
        **duplicate_player_id_payload,
        "verification_stage": "strong_full_name_verification",
        "proposed_known_as": "Alex",
        "proposed_full_name": "Alexander Example",
    }

    assert player_normalization._gemini_payload_cache_key(
        duplicate_full_name_payload
    ) != player_normalization._gemini_payload_cache_key(duplicate_player_id_payload)
    assert player_normalization._gemini_payload_cache_key(
        duplicate_player_id_payload
    ) != player_normalization._gemini_payload_cache_key(verification_payload)


def test_ask_gemini_uses_retry_cache_without_primary_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_PROGRESS", "false")
    payload = _gemini_identity_payload()
    primary_decision = player_normalization.GeminiDecision(
        resolved=True,
        candidate_id="primary",
        confidence=0.5,
        reason="primary",
        model="primary-model",
    )
    retry_decision = player_normalization.GeminiDecision(
        resolved=True,
        candidate_id="retry",
        confidence=0.95,
        reason="retry",
        model="retry-model",
    )
    cache = {
        player_normalization._gemini_payload_cache_key(payload): primary_decision,
        player_normalization._gemini_payload_cache_key(payload, retry=True): retry_decision,
    }

    assert player_normalization.ask_gemini(payload, cache, log_case=False).candidate_id == "primary"
    assert player_normalization.ask_gemini(payload, cache, retry=True, log_case=False).candidate_id == "retry"
    assert (
        player_normalization.ask_gemini(
            payload,
            cache,
            retry=True,
            log_case=False,
            exclude_models={"retry-model"},
        )
        is None
    )


def test_load_gemini_cache_upgrades_legacy_retry_key(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _gemini_identity_payload()
    cache_dir = Path.cwd() / ".pytest_tmp_cache" / uuid.uuid4().hex
    cache_dir.mkdir(parents=True, exist_ok=False)
    cache_path = cache_dir / "player_gemini_cache.jsonl"
    try:
        record = {
            "cache_key": player_normalization._gemini_payload_cache_key(payload),
            "model": "retry-model",
            "prompt_version": player_normalization.GEMINI_PROMPT_VERSION,
            "retry": True,
            "request": payload,
            "response": {
                "resolved": True,
                "candidate_id": "retry",
                "confidence": 0.95,
                "reason": "retry",
                "full_name": None,
                "known_as": None,
            },
        }
        cache_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setattr(player_normalization, "PLAYER_GEMINI_CACHE_PATH", cache_path)
        monkeypatch.setattr(player_normalization, "LEGACY_PLAYER_GEMINI_CACHE_PATH", cache_path)

        cache = player_normalization._load_gemini_cache()

        assert player_normalization._gemini_payload_cache_key(payload) not in cache
        assert cache[player_normalization._gemini_payload_cache_key(payload, retry=True)].candidate_id == "retry"
    finally:
        cache_path.unlink(missing_ok=True)
        cache_dir.rmdir()


def _run_single_token_context_bridge(rows: list[dict[str, object]], bridge_pairs: dict[str, str]):
    observations = pd.DataFrame(rows, columns=player_normalization.OBSERVATION_COLUMNS)
    source_keys = sorted(observations["source_player_key"].astype(str).unique().tolist())
    rows_by_key = {
        str(key): group.copy()
        for key, group in observations.groupby("source_player_key", dropna=False)
    }
    dsu = player_normalization.DisjointSet(source_keys)
    key_methods: dict[str, set[str]] = defaultdict(set)
    bridges = player_normalization._apply_single_token_match_context_bridges(
        source_keys,
        rows_by_key,
        dsu,
        key_methods,
        bridge_pairs=bridge_pairs,
    )
    return bridges, dsu, key_methods


def _observation(
    source: str,
    source_player_key: str,
    observed_name: str,
    normalized_alias: str,
    positions: str,
    position_roles: str,
) -> dict[str, object]:
    return {
        "source": source,
        "source_player_key": source_player_key,
        "source_player_id": source_player_key.split(":", 1)[1] if ":" in source_player_key else "",
        "observed_name": observed_name,
        "normalized_alias": normalized_alias,
        "id_competition": "ESP-La_Liga",
        "id_season": "2025-2026",
        "id_team": "Villarreal_CF",
        "positions": positions,
        "position_roles": position_roles,
        "sample_game": "2026-05-24 Villarreal CF-Atletico de Madrid",
        "observations": 1,
    }


def _understat_match_row(
    player: str = "Alfonso",
    position: str = "DL",
    season: str = "2526",
    game: str = "2026-05-24 Villarreal CF-Atletico de Madrid",
) -> dict[str, object]:
    return {
        "league": "ESP-La Liga",
        "season": season,
        "game": game,
        "team": "Villarreal CF",
        "player": player,
        "player_id": "2521",
        "position": position,
        "minutes": "72",
    }


def _espn_lineup_row(
    player: str,
    position: str,
    appearances: str = "1.0",
    sub_in: str = "start",
    season: str = "2526",
    game: str = "2026-05-24 Villarreal CF-Atletico de Madrid",
) -> dict[str, object]:
    return {
        "league": "ESP-La Liga",
        "season": season,
        "game": game,
        "team": "Villarreal CF",
        "player": player,
        "position": position,
        "appearances": appearances,
        "sub_in": sub_in,
    }


def test_single_token_match_context_bridge_merges_unique_participant_candidate() -> None:
    pedraza_key = "espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_pedraza"
    gonzalez_key = "espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_gonzalez"
    bridge_pairs = player_normalization._single_token_match_context_bridge_pairs_from_frames(
        pd.DataFrame([_understat_match_row()]),
        pd.DataFrame(
            [
                _espn_lineup_row("Alfonso Pedraza", "Left Back"),
                _espn_lineup_row("Alfonso Gonzalez", "Substitute", appearances="0.0", sub_in=""),
            ]
        ),
    )

    bridges, dsu, key_methods = _run_single_token_context_bridge(
        [
            _observation("understat", "understat:2521", "Alfonso", "alfonso", "DL", "defender"),
            _observation(
                "espn",
                pedraza_key,
                "Alfonso Pedraza",
                "alfonso_pedraza",
                "Left Back",
                "defender",
            ),
            _observation(
                "espn",
                gonzalez_key,
                "Alfonso Gonzalez",
                "alfonso_gonzalez",
                "Substitute",
                "substitute",
            ),
        ],
        bridge_pairs,
    )

    assert bridge_pairs == {"understat:2521": pedraza_key}
    assert bridges == 1
    assert dsu.find("understat:2521") == dsu.find(pedraza_key)
    assert dsu.find("understat:2521") != dsu.find(gonzalez_key)
    assert "deterministic_single_token_match_context_bridge" in key_methods["understat:2521"]


def test_single_token_match_context_bridge_accepts_same_alias_across_season_keys() -> None:
    pairs = player_normalization._single_token_match_context_bridge_pairs_from_frames(
        pd.DataFrame(
            [
                _understat_match_row(season="2425", game="2025-05-25 Villarreal CF-Sevilla FC"),
                _understat_match_row(season="2526", game="2026-05-24 Villarreal CF-Atletico de Madrid"),
            ]
        ),
        pd.DataFrame(
            [
                _espn_lineup_row(
                    "Alfonso Pedraza",
                    "Left Back",
                    season="2425",
                    game="2025-05-25 Villarreal CF-Sevilla FC",
                ),
                _espn_lineup_row(
                    "Alfonso Pedraza",
                    "Left Back",
                    season="2526",
                    game="2026-05-24 Villarreal CF-Atletico de Madrid",
                ),
            ]
        ),
    )

    assert pairs == {"understat:2521": "espn:ESP-La_Liga:2024-2025:Villarreal_CF:alfonso_pedraza"}


def test_single_token_match_context_bridge_skips_ambiguous_participant_candidates() -> None:
    bridge_pairs = player_normalization._single_token_match_context_bridge_pairs_from_frames(
        pd.DataFrame([_understat_match_row()]),
        pd.DataFrame(
            [
                _espn_lineup_row("Alfonso Pedraza", "Left Back"),
                _espn_lineup_row("Alfonso Davies", "Left Back"),
            ]
        ),
    )
    bridges, dsu, _ = _run_single_token_context_bridge(
        [
            _observation("understat", "understat:2521", "Alfonso", "alfonso", "DL", "defender"),
            _observation(
                "espn",
                "espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_pedraza",
                "Alfonso Pedraza",
                "alfonso_pedraza",
                "Left Back",
                "defender",
            ),
            _observation(
                "espn",
                "espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_davies",
                "Alfonso Davies",
                "alfonso_davies",
                "Left Back",
                "defender",
            ),
        ],
        bridge_pairs,
    )

    assert bridge_pairs == {}
    assert bridges == 0
    assert dsu.find("understat:2521") != dsu.find("espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_pedraza")
    assert dsu.find("understat:2521") != dsu.find("espn:ESP-La_Liga:2025-2026:Villarreal_CF:alfonso_davies")


def _identity(
    *,
    known_as: str,
    full_name: str = "",
    source_player_keys: str,
    competition: str = "ESP-La_Liga",
    season: str,
    team: str,
) -> dict[str, object]:
    return {
        "id_player": "",
        "known_as": known_as,
        "full_name": full_name,
        "aliases": known_as,
        "source_player_keys": source_player_keys,
        "id_understat": "",
        "id_whoscored": "",
        "competitions": competition,
        "seasons": season,
        "teams": team,
        "resolution_method": "source_context_singleton",
        "confidence": "1.000",
        "needs_review": str(not player_normalization.has_expanded_full_name(full_name)).lower(),
    }


def _alias_row(identity_index: int, source_player_key: str, competition: str, season: str, team: str) -> dict[str, object]:
    return {
        "source": source_player_key.split(":", 1)[0],
        "source_player_key": source_player_key,
        "source_player_id": "",
        "observed_name": "",
        "normalized_alias": "",
        "id_competition": competition,
        "id_season": season,
        "id_team": team,
        "sample_game": "",
        "observations": 1,
        "_identity_index": identity_index,
        "method": "source_context_singleton",
        "confidence": "1.000",
        "needs_review": "false",
    }


def test_duplicate_player_id_context_merge_fuses_same_team_across_seasons() -> None:
    identities = pd.DataFrame(
        [
            _identity(
                known_as="Alexsandro",
                source_player_keys="espn:FRA-Ligue_1:2023-2024:LOSC_Lille:alexsandro",
                competition="FRA-Ligue_1",
                season="2023-2024",
                team="LOSC_Lille",
            ),
            _identity(
                known_as="Alexsandro",
                source_player_keys="espn:FRA-Ligue_1:2024-2025:LOSC_Lille:alexsandro",
                competition="FRA-Ligue_1",
                season="2024-2025",
                team="LOSC_Lille",
            ),
        ],
        columns=player_normalization.IDENTITY_COLUMNS,
    )
    alias_rows = pd.DataFrame(
        [
            _alias_row(0, "espn:FRA-Ligue_1:2023-2024:LOSC_Lille:alexsandro", "FRA-Ligue_1", "2023-2024", "LOSC_Lille"),
            _alias_row(1, "espn:FRA-Ligue_1:2024-2025:LOSC_Lille:alexsandro", "FRA-Ligue_1", "2024-2025", "LOSC_Lille"),
        ]
    )

    merged, merged_alias_rows, merge_count = player_normalization._merge_duplicate_player_id_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_player_id_context_merge",
    )

    assert merge_count == 1
    assert len(merged) == 1
    assert merged.iloc[0]["seasons"] == "2023-2024 | 2024-2025"
    assert "automatic_duplicate_player_id_context_merge" in merged.iloc[0]["resolution_method"]
    assert set(merged_alias_rows["_identity_index"]) == {0}


def test_duplicate_player_id_context_merge_keeps_source_id_conflicts_separate() -> None:
    identities = pd.DataFrame(
        [
            _identity(
                known_as="Example Player",
                source_player_keys="understat:1",
                season="2024-2025",
                team="Example_Team",
            ),
            _identity(
                known_as="Example Player",
                source_player_keys="understat:2",
                season="2025-2026",
                team="Example_Team",
            ),
        ],
        columns=player_normalization.IDENTITY_COLUMNS,
    )
    alias_rows = pd.DataFrame(
        [
            _alias_row(0, "understat:1", "ESP-La_Liga", "2024-2025", "Example_Team"),
            _alias_row(1, "understat:2", "ESP-La_Liga", "2025-2026", "Example_Team"),
        ]
    )

    merged, merged_alias_rows, merge_count = player_normalization._merge_duplicate_player_id_identities(
        identities,
        alias_rows,
        method="automatic_duplicate_player_id_context_merge",
    )

    assert merge_count == 0
    assert len(merged) == 2
    assert set(merged_alias_rows["_identity_index"]) == {0, 1}


def test_unjustified_duplicate_full_name_rows_allows_contextual_homonyms() -> None:
    players = pd.DataFrame(
        [
            {
                "id_player": "mamadou_coulibaly_as_monaco",
                "knownAs": "Mamadou Coulibaly",
                "fullName": "Mamadou Coulibaly",
                "idUnderstat": "12285",
                "idWhoscored": "482049",
                "source_player_keys": "understat:12285 | whoscored:482049",
                "competitions": "FRA-Ligue_1",
                "teams": "AS_Monaco",
                "resolution_method": "automatic_contextual_homonym_id",
            },
            {
                "id_player": "mamadou_coulibaly_us_salernitana_1919",
                "knownAs": "Mamadou Coulibaly",
                "fullName": "Mamadou Coulibaly",
                "idUnderstat": "4903",
                "idWhoscored": "335093",
                "source_player_keys": "understat:4903 | whoscored:335093",
                "competitions": "ITA-Serie_A",
                "teams": "US_Salernitana_1919",
                "resolution_method": "automatic_contextual_homonym_id",
            },
        ]
    )

    violations = player_normalization.unjustified_duplicate_full_name_rows(players)

    assert violations.empty


def test_unjustified_duplicate_full_name_rows_rejects_unexplained_duplicates() -> None:
    players = pd.DataFrame(
        [
            {
                "id_player": "example_player_a",
                "knownAs": "Example Player",
                "fullName": "Example Player",
                "idUnderstat": "",
                "idWhoscored": "",
                "source_player_keys": "espn:ESP-La_Liga:2024-2025:Team_A:example_player",
                "competitions": "ESP-La_Liga",
                "teams": "Team_A",
                "resolution_method": "source_context_singleton",
            },
            {
                "id_player": "example_player_b",
                "knownAs": "Example Player",
                "fullName": "Example Player",
                "idUnderstat": "",
                "idWhoscored": "",
                "source_player_keys": "espn:ESP-La_Liga:2025-2026:Team_B:example_player",
                "competitions": "ESP-La_Liga",
                "teams": "Team_B",
                "resolution_method": "source_context_singleton",
            },
        ]
    )

    violations = player_normalization.unjustified_duplicate_full_name_rows(players)

    assert len(violations) == 2


def test_safe_duplicate_full_name_transfer_merge_fuses_same_known_as_without_source_conflict() -> None:
    identities = pd.DataFrame(
        [
            _identity(
                known_as="Salvi",
                full_name="Salvador Sánchez Ponce",
                source_player_keys="espn:ESP-La_Liga:2023-2024:Rayo_Vallecano:salvi",
                season="2023-2024",
                team="Rayo_Vallecano",
            ),
            _identity(
                known_as="Salvi",
                full_name="Salvador Sánchez Ponce",
                source_player_keys="espn:ESP-La_Liga:2024-2025:RCD_Espanyol:salvi",
                season="2024-2025",
                team="RCD_Espanyol",
            ),
        ],
        columns=player_normalization.IDENTITY_COLUMNS,
    )
    alias_rows = pd.DataFrame(
        [
            _alias_row(0, "espn:ESP-La_Liga:2023-2024:Rayo_Vallecano:salvi", "ESP-La_Liga", "2023-2024", "Rayo_Vallecano"),
            _alias_row(1, "espn:ESP-La_Liga:2024-2025:RCD_Espanyol:salvi", "ESP-La_Liga", "2024-2025", "RCD_Espanyol"),
        ]
    )

    merged, merged_alias_rows, merge_count = player_normalization._merge_safe_duplicate_full_name_transfers(
        identities,
        alias_rows,
        method="automatic_safe_duplicate_full_name_transfer_merge",
    )

    assert merge_count == 1
    assert len(merged) == 1
    assert merged.iloc[0]["seasons"] == "2023-2024 | 2024-2025"
    assert "automatic_safe_duplicate_full_name_transfer_merge" in merged.iloc[0]["resolution_method"]
    assert set(merged_alias_rows["_identity_index"]) == {0}


def test_safe_duplicate_full_name_transfer_merge_keeps_source_id_conflicts_separate() -> None:
    identities = pd.DataFrame(
        [
            _identity(
                known_as="Example Player",
                full_name="Example Player",
                source_player_keys="understat:1",
                season="2024-2025",
                team="Team_A",
            ),
            _identity(
                known_as="Example Player",
                full_name="Example Player",
                source_player_keys="understat:2",
                season="2025-2026",
                team="Team_B",
            ),
        ],
        columns=player_normalization.IDENTITY_COLUMNS,
    )
    alias_rows = pd.DataFrame(
        [
            _alias_row(0, "understat:1", "ESP-La_Liga", "2024-2025", "Team_A"),
            _alias_row(1, "understat:2", "ESP-La_Liga", "2025-2026", "Team_B"),
        ]
    )

    merged, merged_alias_rows, merge_count = player_normalization._merge_safe_duplicate_full_name_transfers(
        identities,
        alias_rows,
        method="automatic_safe_duplicate_full_name_transfer_merge",
    )

    assert merge_count == 0
    assert len(merged) == 2
    assert set(merged_alias_rows["_identity_index"]) == {0, 1}
