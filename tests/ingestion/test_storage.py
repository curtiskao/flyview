"""Tests for storage.py — DynamoDB helpers and checkpoint logic."""

import json
import sys
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, "lambdas/ingestion")

from storage import (
    batch_write_transfers,
    is_new_transfer,
    load_checkpoint,
    save_checkpoint,
    write_raw_to_s3,
)


# ---------------------------------------------------------------------------
# is_new_transfer
# ---------------------------------------------------------------------------

def test_new_transfer_no_checkpoint():
    record = {"block_number": 100, "log_index": 0}
    assert is_new_transfer(record, {}) is True


def test_new_transfer_higher_block():
    record = {"block_number": 200, "log_index": 0}
    checkpoint = {"last_block_number": 100, "last_log_index": 5}
    assert is_new_transfer(record, checkpoint) is True


def test_new_transfer_same_block_higher_log():
    record = {"block_number": 100, "log_index": 10}
    checkpoint = {"last_block_number": 100, "last_log_index": 5}
    assert is_new_transfer(record, checkpoint) is True


def test_already_seen_same_block_same_log():
    record = {"block_number": 100, "log_index": 5}
    checkpoint = {"last_block_number": 100, "last_log_index": 5}
    assert is_new_transfer(record, checkpoint) is False


def test_already_seen_older_block():
    record = {"block_number": 50, "log_index": 99}
    checkpoint = {"last_block_number": 100, "last_log_index": 0}
    assert is_new_transfer(record, checkpoint) is False


# ---------------------------------------------------------------------------
# DynamoDB key construction (via batch_write_transfers)
# ---------------------------------------------------------------------------

def _make_dynamodb_mock():
    batch_writer = MagicMock()
    batch_writer.__enter__ = MagicMock(return_value=batch_writer)
    batch_writer.__exit__ = MagicMock(return_value=False)
    table = MagicMock()
    table.batch_writer.return_value = batch_writer
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    return dynamodb, table, batch_writer


def test_batch_write_sk_is_zero_padded():
    dynamodb, table, batch_writer = _make_dynamodb_mock()
    record = {
        "contract_address": "0xabc",
        "block_number": 42,
        "log_index": 7,
        "tx_hash": "0x1",
        "timestamp": "2026-01-01T00:00:00Z",
        "from_address": "0xfrom",
        "to_address": "0xto",
        "token_type": "ERC-721",
        "token_id": "1",
    }

    batch_write_transfers(dynamodb, "flyview-transfers", [record])

    written = batch_writer.put_item.call_args[1]["Item"]
    assert written["pk"] == "CONTRACT#0xabc"
    assert written["sk"] == "BLOCK#0000000042#LOG#00007"


def test_batch_write_includes_all_record_fields():
    dynamodb, _, batch_writer = _make_dynamodb_mock()
    record = {
        "contract_address": "0xabc",
        "block_number": 1,
        "log_index": 0,
        "tx_hash": "0xhash",
        "timestamp": "2026-01-01T00:00:00Z",
        "from_address": "0xfrom",
        "to_address": "0xto",
        "token_type": "ERC-20",
        "value": "500",
    }

    batch_write_transfers(dynamodb, "flyview-transfers", [record])

    written = batch_writer.put_item.call_args[1]["Item"]
    assert written["value"] == "500"
    assert written["token_type"] == "ERC-20"


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

def test_load_checkpoint_returns_empty_dict_on_miss():
    dynamodb = MagicMock()
    dynamodb.Table.return_value.get_item.return_value = {}

    result = load_checkpoint(dynamodb, "flyview-checkpoints", "0xabc")
    assert result == {}


def test_load_checkpoint_returns_item():
    item = {"contract_address": "0xabc", "last_block_number": 100}
    dynamodb = MagicMock()
    dynamodb.Table.return_value.get_item.return_value = {"Item": item}

    result = load_checkpoint(dynamodb, "flyview-checkpoints", "0xabc")
    assert result == item


def test_save_checkpoint_writes_correct_fields():
    dynamodb = MagicMock()
    table = dynamodb.Table.return_value

    save_checkpoint(dynamodb, "flyview-checkpoints", "0xABC", 999, 3, "0xhash")

    written = table.put_item.call_args[1]["Item"]
    assert written["contract_address"] == "0xabc"
    assert written["last_block_number"] == 999
    assert written["last_log_index"] == 3
    assert written["last_tx_hash"] == "0xhash"
    assert "updated_at" in written


# ---------------------------------------------------------------------------
# S3 raw archive
# ---------------------------------------------------------------------------

def test_write_raw_to_s3_uses_contract_in_key():
    s3 = MagicMock()
    write_raw_to_s3(s3, "my-bucket", "0xABC", {"items": []})

    _, kwargs = s3.put_object.call_args
    assert kwargs["Bucket"] == "my-bucket"
    assert "0xabc" in kwargs["Key"]
    assert kwargs["ContentType"] == "application/json"
    # Body must be valid JSON
    json.loads(kwargs["Body"])
