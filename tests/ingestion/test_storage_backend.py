"""Tests for LocalStorageBackend — file I/O, dedup, and checkpoint round-trip."""

import json
import sys

import pytest

sys.path.insert(0, "lambdas/ingestion")

from storage_backend import LocalStorageBackend


def _record(block=100, log=0, contract="0xabc"):
    return {
        "contract_address": contract,
        "block_number": block,
        "log_index": log,
        "tx_hash": f"0xtx{block}{log}",
        "timestamp": "2026-01-01T00:00:00Z",
        "from_address": "0x0000000000000000000000000000000000000000",
        "to_address": "0xdiner",
        "token_type": "ERC-721",
        "token_id": "1",
    }


# ---------------------------------------------------------------------------
# Checkpoint round-trip
# ---------------------------------------------------------------------------

def test_get_checkpoint_returns_empty_on_first_run(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    assert backend.get_checkpoint("0xabc") == {}


def test_save_and_get_checkpoint_round_trips(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.save_checkpoint("0xABC", 999, 7, "0xhash")
    cp = backend.get_checkpoint("0xABC")

    assert cp["last_block_number"] == 999
    assert cp["last_log_index"] == 7
    assert cp["last_tx_hash"] == "0xhash"
    assert "updated_at" in cp


def test_checkpoint_keys_are_lowercased(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.save_checkpoint("0xABC", 1, 0, "0x1")
    # Reading back with different casing should still find it
    cp = backend.get_checkpoint("0xabc")
    assert cp["last_block_number"] == 1


def test_multiple_contracts_store_independently(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.save_checkpoint("0xAAA", 100, 0, "0x1")
    backend.save_checkpoint("0xBBB", 200, 5, "0x2")

    assert backend.get_checkpoint("0xAAA")["last_block_number"] == 100
    assert backend.get_checkpoint("0xBBB")["last_block_number"] == 200


# ---------------------------------------------------------------------------
# Transfer writes — dedup and persistence
# ---------------------------------------------------------------------------

def test_batch_write_transfers_persists_to_file(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.batch_write_transfers([_record(block=100, log=0)])

    data = json.loads((tmp_path / "transfers.json").read_text())
    assert len(data) == 1


def test_batch_write_is_idempotent(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.batch_write_transfers([_record(block=100, log=0)])
    backend.batch_write_transfers([_record(block=100, log=0)])  # same record again

    data = json.loads((tmp_path / "transfers.json").read_text())
    assert len(data) == 1  # deduped by pk:sk key


def test_batch_write_accumulates_distinct_records(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.batch_write_transfers([_record(block=100, log=0)])
    backend.batch_write_transfers([_record(block=101, log=0)])

    data = json.loads((tmp_path / "transfers.json").read_text())
    assert len(data) == 2


def test_transfer_key_is_zero_padded_for_sort(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    backend.batch_write_transfers([_record(block=42, log=7)])

    data = json.loads((tmp_path / "transfers.json").read_text())
    key = list(data.keys())[0]
    assert "BLOCK#0000000042" in key
    assert "LOG#00007" in key


# ---------------------------------------------------------------------------
# Raw archive
# ---------------------------------------------------------------------------

def test_write_raw_creates_file(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    path = backend.write_raw("0xABC", {"items": [{"id": 1}], "next_page_params": None})

    assert (tmp_path / "raw").exists()
    content = json.loads(open(path).read())
    assert content["items"] == [{"id": 1}]


def test_write_raw_uses_contract_address_in_path(tmp_path):
    backend = LocalStorageBackend(str(tmp_path))
    path = backend.write_raw("0xABC", {})

    assert "0xabc" in path


# ---------------------------------------------------------------------------
# get_backend factory
# ---------------------------------------------------------------------------

def test_get_backend_defaults_to_local(tmp_path, monkeypatch):
    from storage_backend import get_backend
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))

    backend = get_backend({})
    assert isinstance(backend, LocalStorageBackend)


def test_get_backend_local_explicit(tmp_path, monkeypatch):
    from storage_backend import get_backend
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))

    backend = get_backend({})
    assert isinstance(backend, LocalStorageBackend)
