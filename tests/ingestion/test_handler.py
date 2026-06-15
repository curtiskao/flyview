"""Tests for handler.py — orchestration, error isolation, and stop conditions."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "lambdas/ingestion")

from storage_backend import StorageBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_backend(checkpoint=None) -> MagicMock:
    backend = MagicMock(spec=StorageBackend)
    backend.get_checkpoint.return_value = checkpoint or {}
    backend.write_raw.return_value = "local_data/raw/test.json"
    return backend


def _make_raw_item(block=100, log=0, token_type="ERC-721"):
    item = {
        "block_number": block,
        "log_index": log,
        "transaction_hash": f"0xtx{block}{log}",
        "timestamp": "2026-01-01T00:00:00Z",
        "method": "setUserStatus",
        "from": None,
        "to": {"hash": "0xDINER"},
        "token": {"address_hash": "0xBBST", "type": token_type},
        "total": {"token_id": "1", "value": "100"},
    }
    return item


CONFIG = {
    "max_pages": 5,
    "transfers_table": "flyview-transfers",
    "checkpoints_table": "flyview-checkpoints",
    "raw_bucket": "flyview-raw",
}


# ---------------------------------------------------------------------------
# ingest_contract — happy path
# ---------------------------------------------------------------------------

@patch("handler.fetch_transfers_page")
def test_ingest_writes_new_transfers_and_saves_checkpoint(mock_fetch):
    from handler import ingest_contract

    mock_fetch.side_effect = [
        ([_make_raw_item(block=200), _make_raw_item(block=199)], None),
    ]
    backend = _mock_backend()

    result = ingest_contract("0xBBST", "BBST", CONFIG, backend)

    assert result["records_written"] == 2
    assert result["stop_reason"] == "end_of_history"
    backend.batch_write_transfers.assert_called_once()
    backend.save_checkpoint.assert_called_once_with("0xBBST", 200, 0, "0xtx2000")


@patch("handler.fetch_transfers_page")
def test_ingest_stops_when_caught_up(mock_fetch):
    from handler import ingest_contract

    # Checkpoint at block 100, log 5 — item at block 100 log 3 is already seen
    mock_fetch.side_effect = [
        ([_make_raw_item(block=200), _make_raw_item(block=100, log=3)], {"cursor": "x"}),
    ]
    backend = _mock_backend(checkpoint={"last_block_number": 100, "last_log_index": 5})

    result = ingest_contract("0xBBST", "BBST", CONFIG, backend)

    assert result["records_written"] == 1
    assert result["stop_reason"] == "caught_up"
    assert mock_fetch.call_count == 1  # must not fetch a second page


@patch("handler.fetch_transfers_page")
def test_ingest_empty_page_stops(mock_fetch):
    from handler import ingest_contract

    mock_fetch.side_effect = [
        ([_make_raw_item(block=100)], {"cursor": "x"}),
        ([], None),
    ]
    backend = _mock_backend()

    result = ingest_contract("0xBBST", "BBST", CONFIG, backend)

    assert result["stop_reason"] == "empty_page"
    assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# ingest_contract — S3/file archive failure is non-fatal
# ---------------------------------------------------------------------------

@patch("handler.fetch_transfers_page")
def test_raw_archive_failure_does_not_stop_ingestion(mock_fetch):
    from handler import ingest_contract

    mock_fetch.side_effect = [([_make_raw_item(block=100)], None)]
    backend = _mock_backend()
    backend.write_raw.side_effect = Exception("disk full")

    result = ingest_contract("0xBBST", "BBST", CONFIG, backend)

    assert result["records_written"] == 1
    backend.batch_write_transfers.assert_called_once()


# ---------------------------------------------------------------------------
# ingest_contract — bad items skipped, good items still written
# ---------------------------------------------------------------------------

@patch("handler.fetch_transfers_page")
def test_malformed_item_is_skipped_not_fatal(mock_fetch):
    from handler import ingest_contract

    malformed = {"transaction_hash": "0xbad"}  # missing required fields
    mock_fetch.side_effect = [([malformed, _make_raw_item(block=100)], None)]
    backend = _mock_backend()

    result = ingest_contract("0xBBST", "BBST", CONFIG, backend)

    assert result["records_written"] == 1
    assert result["records_skipped"] == 1


# ---------------------------------------------------------------------------
# ingest_contract — max_pages cap
# ---------------------------------------------------------------------------

@patch("handler.fetch_transfers_page")
def test_stops_at_max_pages(mock_fetch):
    from handler import ingest_contract

    config = {**CONFIG, "max_pages": 2}
    # Always return a new cursor — simulate endless history
    mock_fetch.side_effect = [
        ([_make_raw_item(block=200 - i)], {"cursor": str(i)}) for i in range(10)
    ]
    backend = _mock_backend()

    result = ingest_contract("0xBBST", "BBST", config, backend)

    assert result["pages_fetched"] == 2
    assert result["stop_reason"] == "max_pages"


# ---------------------------------------------------------------------------
# lambda_handler — one contract failure does not abort the other
# ---------------------------------------------------------------------------

@patch("handler.get_backend")
@patch("handler.ingest_contract")
@patch("handler._get_config")
def test_lambda_handler_isolates_contract_failures(mock_config, mock_ingest, mock_get_backend):
    from handler import lambda_handler

    mock_config.return_value = CONFIG
    mock_get_backend.return_value = _mock_backend()
    mock_ingest.side_effect = [
        Exception("BBST exploded"),
        {"contract": "FLY", "pages_fetched": 1, "records_written": 5, "records_skipped": 0, "stop_reason": "caught_up"},
    ]

    result = lambda_handler({}, {})

    assert result["statusCode"] == 200
    assert result["results"][0] == {"contract": "BBST", "error": True}
    assert result["results"][1]["records_written"] == 5


# ---------------------------------------------------------------------------
# lambda_handler — uses correct backend from env
# ---------------------------------------------------------------------------

@patch("handler.ingest_contract")
@patch("handler._get_config")
def test_lambda_handler_uses_local_backend_by_default(mock_config, mock_ingest, tmp_path, monkeypatch):
    from handler import lambda_handler

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    mock_config.return_value = {**CONFIG}
    mock_ingest.return_value = {"contract": "X", "pages_fetched": 0, "records_written": 0, "records_skipped": 0, "stop_reason": "empty_page"}

    lambda_handler({}, {})

    # Verify ingest_contract received a LocalStorageBackend
    from storage_backend import LocalStorageBackend
    _, kwargs = mock_ingest.call_args_list[0]
    backend_arg = mock_ingest.call_args_list[0][0][3]  # 4th positional arg
    assert isinstance(backend_arg, LocalStorageBackend)
