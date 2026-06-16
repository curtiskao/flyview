"""Tests for aggregation/storage_backend.py."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, "lambdas/aggregation")

from agg_storage import LocalAggregationBackend


# ---------------------------------------------------------------------------
# LocalAggregationBackend
# ---------------------------------------------------------------------------

def _make_local_backend(tmp_path: Path, transfers: list[dict] | None = None):
    transfers_file = tmp_path / "transfers.json"
    if transfers is not None:
        data = {f"key_{i}": t for i, t in enumerate(transfers)}
        transfers_file.write_text(json.dumps(data))
    out_dir = tmp_path / "aggregates_out"
    return LocalAggregationBackend(
        transfers_path=str(transfers_file),
        local_data_dir=str(out_dir),
    ), out_dir


def test_load_all_transfers_returns_list(tmp_path):
    transfers = [{"tx_hash": "0xabc"}, {"tx_hash": "0xdef"}]
    backend, _ = _make_local_backend(tmp_path, transfers)
    loaded = backend.load_all_transfers()
    assert len(loaded) == 2
    assert any(t["tx_hash"] == "0xabc" for t in loaded)


def test_load_all_transfers_raises_if_file_missing(tmp_path):
    backend, _ = _make_local_backend(tmp_path, transfers=None)
    with pytest.raises(FileNotFoundError):
        backend.load_all_transfers()


def test_write_diner_aggregates_creates_file(tmp_path):
    backend, out_dir = _make_local_backend(tmp_path, [])
    stats = {"0xaaa": {"fly_earned": "1000", "fly_burned": "0"}}
    backend.write_diner_aggregates(stats)
    written = json.loads((out_dir / "diners.json").read_text())
    assert written == stats


def test_write_daily_aggregates_creates_file(tmp_path):
    backend, out_dir = _make_local_backend(tmp_path, [])
    stats = {"2026-01-01": {"active_wallets": 5, "fly_minted": "500"}}
    backend.write_daily_aggregates(stats)
    written = json.loads((out_dir / "daily.json").read_text())
    assert written == stats


def test_write_top_lists_creates_both_files(tmp_path):
    backend, out_dir = _make_local_backend(tmp_path, [])
    earners = [{"rank": 1, "wallet_address": "0xaaa", "fly_earned": "9999"}]
    spenders = [{"rank": 1, "wallet_address": "0xbbb", "fly_burned": "8888"}]
    backend.write_top_lists(earners, spenders)
    assert json.loads((out_dir / "top_earners.json").read_text()) == earners
    assert json.loads((out_dir / "top_spenders.json").read_text()) == spenders


def test_write_meta_creates_file(tmp_path):
    backend, out_dir = _make_local_backend(tmp_path, [])
    meta = {"computed_at": "2026-01-01T00:00:00Z", "total_transfers": 100}
    backend.write_meta(meta)
    written = json.loads((out_dir / "meta.json").read_text())
    assert written == meta


def test_write_is_idempotent(tmp_path):
    """Writing twice should overwrite, not append."""
    backend, out_dir = _make_local_backend(tmp_path, [])
    backend.write_diner_aggregates({"0xaaa": {"fly_earned": "100"}})
    backend.write_diner_aggregates({"0xbbb": {"fly_earned": "200"}})
    written = json.loads((out_dir / "diners.json").read_text())
    assert "0xaaa" not in written
    assert "0xbbb" in written


def test_output_dir_created_if_missing(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    backend = LocalAggregationBackend(
        transfers_path=str(tmp_path / "t.json"),
        local_data_dir=str(nested),
    )
    assert nested.exists()
