"""Tests for aggregator.py — pure aggregation logic."""

import sys

sys.path.insert(0, "lambdas/aggregation")

from aggregator import (
    FLY_CONTRACT,
    BBST_CONTRACT,
    compute_daily_stats,
    compute_diner_stats,
    compute_top_earners,
    compute_top_spenders,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WALLET_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
ZERO = "0x0000000000000000000000000000000000000000"

FLY_DECIMALS = 10 ** 18


def _fly_mint(wallet: str, value_fly: int, ts: str = "2026-01-01T12:00:00.000000Z") -> dict:
    return {
        "contract_address": FLY_CONTRACT,
        "block_number": 1,
        "log_index": 0,
        "tx_hash": "0x1",
        "timestamp": ts,
        "method": "mint",
        "from_address": ZERO,
        "to_address": wallet,
        "token_type": "ERC-20",
        "value": str(value_fly * FLY_DECIMALS),
    }


def _fly_burn(wallet: str, value_fly: int, ts: str = "2026-01-01T13:00:00.000000Z") -> dict:
    return {
        "contract_address": FLY_CONTRACT,
        "block_number": 2,
        "log_index": 0,
        "tx_hash": "0x2",
        "timestamp": ts,
        "method": "burn",
        "from_address": wallet,
        "to_address": ZERO,
        "token_type": "ERC-20",
        "value": str(value_fly * FLY_DECIMALS),
    }


def _bbst_mint(wallet: str, token_id: str = "42", ts: str = "2026-01-01T10:00:00.000000Z") -> dict:
    return {
        "contract_address": BBST_CONTRACT,
        "block_number": 0,
        "log_index": 0,
        "tx_hash": "0x0",
        "timestamp": ts,
        "method": "setUserStatus",
        "from_address": ZERO,
        "to_address": wallet,
        "token_type": "ERC-721",
        "token_id": token_id,
    }


# ---------------------------------------------------------------------------
# compute_diner_stats
# ---------------------------------------------------------------------------

def test_mint_accumulates_fly_earned():
    transfers = [_fly_mint(WALLET_A, 50), _fly_mint(WALLET_A, 100)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["fly_earned"] == str(150 * FLY_DECIMALS)


def test_burn_accumulates_fly_burned():
    transfers = [_fly_burn(WALLET_A, 30)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["fly_burned"] == str(30 * FLY_DECIMALS)


def test_fly_net_is_earned_minus_burned():
    transfers = [_fly_mint(WALLET_A, 100), _fly_burn(WALLET_A, 40)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["fly_net"] == str(60 * FLY_DECIMALS)


def test_fly_net_can_be_negative():
    transfers = [_fly_mint(WALLET_A, 10), _fly_burn(WALLET_A, 50)]
    stats = compute_diner_stats(transfers)
    assert int(stats[WALLET_A]["fly_net"]) < 0


def test_mint_burn_counts():
    transfers = [
        _fly_mint(WALLET_A, 10),
        _fly_mint(WALLET_A, 20),
        _fly_burn(WALLET_A, 5),
    ]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["mint_count"] == 2
    assert stats[WALLET_A]["burn_count"] == 1


def test_bbst_sets_has_bbst_and_token_id():
    transfers = [_bbst_mint(WALLET_A, token_id="99")]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["has_bbst"] is True
    assert stats[WALLET_A]["bbst_token_id"] == "99"


def test_wallet_without_bbst_has_has_bbst_false():
    transfers = [_fly_mint(WALLET_A, 50)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["has_bbst"] is False
    assert stats[WALLET_A]["bbst_token_id"] is None


def test_first_and_last_seen_timestamps():
    transfers = [
        _fly_mint(WALLET_A, 10, ts="2026-01-03T00:00:00.000000Z"),
        _fly_mint(WALLET_A, 10, ts="2026-01-01T00:00:00.000000Z"),
        _fly_burn(WALLET_A, 5, ts="2026-01-05T00:00:00.000000Z"),
    ]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["first_seen"] == "2026-01-01T00:00:00.000000Z"
    assert stats[WALLET_A]["last_seen"] == "2026-01-05T00:00:00.000000Z"


def test_multiple_wallets_are_independent():
    transfers = [_fly_mint(WALLET_A, 100), _fly_mint(WALLET_B, 200)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["fly_earned"] == str(100 * FLY_DECIMALS)
    assert stats[WALLET_B]["fly_earned"] == str(200 * FLY_DECIMALS)


def test_wallet_with_only_bbst_has_zero_fly():
    transfers = [_bbst_mint(WALLET_A)]
    stats = compute_diner_stats(transfers)
    assert stats[WALLET_A]["fly_earned"] == "0"
    assert stats[WALLET_A]["fly_burned"] == "0"


def test_empty_transfers_returns_empty():
    assert compute_diner_stats([]) == {}


def test_fly_values_are_strings():
    transfers = [_fly_mint(WALLET_A, 50)]
    stats = compute_diner_stats(transfers)
    assert isinstance(stats[WALLET_A]["fly_earned"], str)
    assert isinstance(stats[WALLET_A]["fly_burned"], str)
    assert isinstance(stats[WALLET_A]["fly_net"], str)


# ---------------------------------------------------------------------------
# compute_daily_stats
# ---------------------------------------------------------------------------

def test_daily_fly_minted_and_burned():
    transfers = [
        _fly_mint(WALLET_A, 100, ts="2026-01-01T10:00:00.000000Z"),
        _fly_burn(WALLET_A, 30, ts="2026-01-01T11:00:00.000000Z"),
    ]
    first_seen = {WALLET_A: "2026-01-01"}
    daily = compute_daily_stats(transfers, first_seen)
    assert daily["2026-01-01"]["fly_minted"] == str(100 * FLY_DECIMALS)
    assert daily["2026-01-01"]["fly_burned"] == str(30 * FLY_DECIMALS)


def test_daily_active_wallets_counts_distinct():
    transfers = [
        _fly_mint(WALLET_A, 10, ts="2026-01-01T10:00:00.000000Z"),
        _fly_mint(WALLET_A, 10, ts="2026-01-01T11:00:00.000000Z"),
        _fly_mint(WALLET_B, 10, ts="2026-01-01T12:00:00.000000Z"),
    ]
    first_seen = {WALLET_A: "2026-01-01", WALLET_B: "2026-01-01"}
    daily = compute_daily_stats(transfers, first_seen)
    assert daily["2026-01-01"]["active_wallets"] == 2


def test_daily_new_wallets():
    transfers = [
        _fly_mint(WALLET_A, 10, ts="2026-01-01T10:00:00.000000Z"),
        _fly_mint(WALLET_B, 10, ts="2026-01-02T10:00:00.000000Z"),
        _fly_mint(WALLET_A, 10, ts="2026-01-02T11:00:00.000000Z"),
    ]
    first_seen = {WALLET_A: "2026-01-01", WALLET_B: "2026-01-02"}
    daily = compute_daily_stats(transfers, first_seen)
    assert daily["2026-01-01"]["new_wallets"] == 1
    assert daily["2026-01-02"]["new_wallets"] == 1  # only WALLET_B is new


def test_daily_burn_rate():
    transfers = [
        _fly_mint(WALLET_A, 100, ts="2026-01-01T10:00:00.000000Z"),
        _fly_burn(WALLET_A, 25, ts="2026-01-01T11:00:00.000000Z"),
    ]
    first_seen = {WALLET_A: "2026-01-01"}
    daily = compute_daily_stats(transfers, first_seen)
    assert daily["2026-01-01"]["burn_rate"] == 0.25


def test_daily_burn_rate_zero_when_no_mints():
    # Edge case: burns on a day with no mints (shouldn't happen in practice,
    # but we guard against division by zero)
    transfers = [_fly_burn(WALLET_A, 50, ts="2026-01-01T10:00:00.000000Z")]
    first_seen = {WALLET_A: "2026-01-01"}
    daily = compute_daily_stats(transfers, first_seen)
    assert daily["2026-01-01"]["burn_rate"] == 0.0


def test_daily_tx_count():
    transfers = [
        _fly_mint(WALLET_A, 10, ts="2026-01-01T10:00:00.000000Z"),
        _fly_mint(WALLET_B, 10, ts="2026-01-01T11:00:00.000000Z"),
        _fly_burn(WALLET_A, 5, ts="2026-01-01T12:00:00.000000Z"),
    ]
    daily = compute_daily_stats(transfers, {WALLET_A: "2026-01-01", WALLET_B: "2026-01-01"})
    assert daily["2026-01-01"]["tx_count"] == 3


def test_bbst_transfers_ignored_in_daily_stats():
    transfers = [_bbst_mint(WALLET_A, ts="2026-01-01T10:00:00.000000Z")]
    daily = compute_daily_stats(transfers, {})
    assert "2026-01-01" not in daily


def test_daily_values_are_strings():
    transfers = [_fly_mint(WALLET_A, 50, ts="2026-01-01T10:00:00.000000Z")]
    first_seen = {WALLET_A: "2026-01-01"}
    daily = compute_daily_stats(transfers, first_seen)
    assert isinstance(daily["2026-01-01"]["fly_minted"], str)
    assert isinstance(daily["2026-01-01"]["fly_burned"], str)


# ---------------------------------------------------------------------------
# compute_top_earners / compute_top_spenders
# ---------------------------------------------------------------------------

def test_top_earners_sorted_descending():
    stats = {
        WALLET_A: {**_base_diner(WALLET_A), "fly_earned": str(100 * FLY_DECIMALS), "fly_burned": "0"},
        WALLET_B: {**_base_diner(WALLET_B), "fly_earned": str(200 * FLY_DECIMALS), "fly_burned": "0"},
    }
    for d in stats.values():
        d["fly_net"] = str(int(d["fly_earned"]) - int(d["fly_burned"]))
    result = compute_top_earners(stats, n=10)
    assert result[0]["wallet_address"] == WALLET_B
    assert result[1]["wallet_address"] == WALLET_A
    assert result[0]["rank"] == 1
    assert result[1]["rank"] == 2


def test_top_spenders_sorted_descending():
    stats = {
        WALLET_A: {**_base_diner(WALLET_A), "fly_earned": "0", "fly_burned": str(50 * FLY_DECIMALS)},
        WALLET_B: {**_base_diner(WALLET_B), "fly_earned": "0", "fly_burned": str(300 * FLY_DECIMALS)},
    }
    for d in stats.values():
        d["fly_net"] = str(int(d["fly_earned"]) - int(d["fly_burned"]))
    result = compute_top_spenders(stats, n=10)
    assert result[0]["wallet_address"] == WALLET_B
    assert result[0]["rank"] == 1


def test_top_list_respects_n():
    stats = {f"0x{i:040x}": {**_base_diner(f"0x{i:040x}"), "fly_earned": str(i), "fly_burned": "0", "fly_net": str(i)} for i in range(200)}
    result = compute_top_earners(stats, n=50)
    assert len(result) == 50


def _base_diner(wallet: str) -> dict:
    return {
        "wallet_address": wallet,
        "mint_count": 0,
        "burn_count": 0,
        "has_bbst": False,
        "bbst_token_id": None,
        "first_seen": None,
        "last_seen": None,
    }
