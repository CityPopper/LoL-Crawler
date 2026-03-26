"""Tests for pure functions in scripts/anonymize_and_upload.py (SEED-1)."""

from __future__ import annotations

import copy

from anonymize_and_upload import (
    _anon_puuid,
    _anonymize_record,
    _is_already_anonymized,
    _is_anomalous_date,
)


def test_puuid_replaced() -> None:
    """_anon_puuid returns 'anon_{16-char-hex}' for a real PUUID."""
    result = _anon_puuid("abc123-real-puuid", {}, "")
    assert result.startswith("anon_")
    hex_part = result.removeprefix("anon_")
    assert len(hex_part) == 16
    # Verify it is valid hex
    int(hex_part, 16)


def test_puuid_consistent() -> None:
    """Same PUUID always maps to same anon_ value when using the same cache."""
    cache: dict[str, str] = {}
    first = _anon_puuid("consistent-puuid", cache, "")
    second = _anon_puuid("consistent-puuid", cache, "")
    assert first == second


def test_riotid_replaced_not_removed() -> None:
    """_anonymize_record replaces riotIdGameName with Player_{hash[:8]} and riotIdTagline with Anon."""
    record = {
        "metadata": {"participants": ["puuid-1"]},
        "info": {
            "participants": [
                {
                    "puuid": "puuid-1",
                    "riotIdGameName": "OriginalName",
                    "riotIdTagline": "NA1",
                }
            ]
        },
    }
    cache: dict[str, str] = {}
    result = _anonymize_record(record, cache, "")

    participant = result["info"]["participants"][0]
    # riotIdGameName should be replaced, not removed
    assert "riotIdGameName" in participant
    assert participant["riotIdGameName"].startswith("Player_")
    assert len(participant["riotIdGameName"]) == len("Player_") + 8
    # riotIdTagline should be "Anon"
    assert participant["riotIdTagline"] == "Anon"


def test_summoner_keys_removed() -> None:
    """_anonymize_record removes summonerName and summonerId from participants."""
    record = {
        "metadata": {"participants": ["puuid-2"]},
        "info": {
            "participants": [
                {
                    "puuid": "puuid-2",
                    "summonerName": "SummonerFoo",
                    "summonerId": "enc-id-123",
                    "riotIdGameName": "Foo",
                    "riotIdTagline": "BAR",
                }
            ]
        },
    }
    cache: dict[str, str] = {}
    result = _anonymize_record(record, cache, "")

    participant = result["info"]["participants"][0]
    assert "summonerName" not in participant
    assert "summonerId" not in participant


def test_already_anonymized_detected() -> None:
    """_is_already_anonymized returns True when any participant puuid starts with 'anon_'."""
    record = {
        "info": {
            "participants": [
                {"puuid": "anon_abc123def4567890"},
                {"puuid": "real-puuid-here"},
            ]
        }
    }
    assert _is_already_anonymized(record) is True


def test_already_anonymized_false_for_real_puuids() -> None:
    """_is_already_anonymized returns False for real PUUIDs."""
    record = {
        "info": {
            "participants": [
                {"puuid": "real-puuid-1"},
                {"puuid": "real-puuid-2"},
            ]
        }
    }
    assert _is_already_anonymized(record) is False


def test_metadata_participants_anonymized() -> None:
    """_anonymize_record also replaces puuids in metadata.participants[]."""
    record = {
        "metadata": {"participants": ["puuid-A", "puuid-B"]},
        "info": {
            "participants": [
                {
                    "puuid": "puuid-A",
                    "riotIdGameName": "PlayerA",
                    "riotIdTagline": "NA1",
                },
                {
                    "puuid": "puuid-B",
                    "riotIdGameName": "PlayerB",
                    "riotIdTagline": "EUW",
                },
            ]
        },
    }
    cache: dict[str, str] = {}
    result = _anonymize_record(record, cache, "")

    for p in result["metadata"]["participants"]:
        assert p.startswith("anon_"), f"metadata participant not anonymized: {p}"


def test_anomalous_date_detection() -> None:
    """_is_anomalous_date returns True for year < 2020, False for 2024."""
    assert _is_anomalous_date("1970-01.jsonl.zst") is True
    assert _is_anomalous_date("2024-03.jsonl.zst") is False


def test_cross_record_consistency() -> None:
    """Anonymize two records sharing a PUUID; both get the same anon_ value."""
    shared_puuid = "shared-puuid-xyz"
    record_1 = {
        "metadata": {"participants": [shared_puuid]},
        "info": {
            "participants": [
                {
                    "puuid": shared_puuid,
                    "riotIdGameName": "Foo",
                    "riotIdTagline": "NA1",
                }
            ]
        },
    }
    record_2 = {
        "metadata": {"participants": [shared_puuid, "other-puuid"]},
        "info": {
            "participants": [
                {
                    "puuid": shared_puuid,
                    "riotIdGameName": "Bar",
                    "riotIdTagline": "EUW",
                },
                {
                    "puuid": "other-puuid",
                    "riotIdGameName": "Baz",
                    "riotIdTagline": "KR",
                },
            ]
        },
    }

    cache: dict[str, str] = {}
    result_1 = _anonymize_record(copy.deepcopy(record_1), cache, "")
    result_2 = _anonymize_record(copy.deepcopy(record_2), cache, "")

    anon_in_r1 = result_1["info"]["participants"][0]["puuid"]
    anon_in_r2 = result_2["info"]["participants"][0]["puuid"]
    assert anon_in_r1 == anon_in_r2

    # Also check metadata consistency
    assert result_1["metadata"]["participants"][0] == result_2["metadata"]["participants"][0]
