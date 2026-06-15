"""Tests for blockscout.py — pagination, retry, and parsing logic."""

import sys
from unittest.mock import MagicMock, call, patch

import pytest
import requests as requests_lib

sys.path.insert(0, "lambdas/ingestion")

from blockscout import fetch_transfers_page, parse_transfer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = requests_lib.exceptions.HTTPError(response=resp)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _mock_session(*responses) -> MagicMock:
    session = MagicMock()
    session.get.side_effect = list(responses)
    return session


# ---------------------------------------------------------------------------
# fetch_transfers_page — pagination
# ---------------------------------------------------------------------------

def test_fetch_first_page_passes_no_params():
    next_params = {"block_number": "100", "index": "5"}
    session = _mock_session(_mock_response({"items": [{"id": 1}], "next_page_params": next_params}))

    items, returned_next = fetch_transfers_page("0xABC", next_page_params=None, session=session)

    assert items == [{"id": 1}]
    assert returned_next == next_params
    _, kwargs = session.get.call_args
    assert kwargs["params"] == {}


def test_fetch_subsequent_page_passes_cursor():
    cursor = {"block_number": "99", "index": "3"}
    session = _mock_session(_mock_response({"items": [], "next_page_params": None}))

    fetch_transfers_page("0xABC", next_page_params=cursor, session=session)

    _, kwargs = session.get.call_args
    assert kwargs["params"] == cursor


def test_fetch_last_page_returns_none_next():
    session = _mock_session(_mock_response({"items": [{"id": 2}], "next_page_params": None}))

    _, next_params = fetch_transfers_page("0xABC", session=session)

    assert next_params is None


# ---------------------------------------------------------------------------
# fetch_transfers_page — retry logic
# ---------------------------------------------------------------------------

@patch("blockscout.time.sleep")
def test_retries_on_429(mock_sleep):
    ok_body = {"items": [{"id": 1}], "next_page_params": None}
    session = _mock_session(
        _mock_response({}, status=429),
        _mock_response(ok_body),
    )

    items, _ = fetch_transfers_page("0xABC", session=session)

    assert items == [{"id": 1}]
    assert session.get.call_count == 2
    mock_sleep.assert_called_once_with(1)  # 2**0


@patch("blockscout.time.sleep")
def test_retries_on_500(mock_sleep):
    ok_body = {"items": [], "next_page_params": None}
    session = _mock_session(
        _mock_response({}, status=500),
        _mock_response(ok_body),
    )

    fetch_transfers_page("0xABC", session=session)

    assert session.get.call_count == 2


@patch("blockscout.time.sleep")
def test_retries_on_connection_error(mock_sleep):
    ok_body = {"items": [], "next_page_params": None}
    session = MagicMock()
    session.get.side_effect = [
        requests_lib.exceptions.ConnectionError("refused"),
        _mock_response(ok_body),
    ]

    fetch_transfers_page("0xABC", session=session)

    assert session.get.call_count == 2


@patch("blockscout.time.sleep")
def test_raises_after_max_retries_on_connection_error(mock_sleep):
    session = MagicMock()
    session.get.side_effect = requests_lib.exceptions.ConnectionError("refused")

    with pytest.raises(requests_lib.exceptions.ConnectionError):
        fetch_transfers_page("0xABC", session=session)

    assert session.get.call_count == 3  # MAX_RETRIES


@patch("blockscout.time.sleep")
def test_raises_after_max_retries_on_429(mock_sleep):
    session = _mock_session(
        _mock_response({}, status=429),
        _mock_response({}, status=429),
        _mock_response({}, status=429),
    )

    with pytest.raises(requests_lib.exceptions.HTTPError):
        fetch_transfers_page("0xABC", session=session)

    assert session.get.call_count == 3


@patch("blockscout.time.sleep")
def test_does_not_retry_on_404(mock_sleep):
    session = _mock_session(_mock_response({}, status=404))

    with pytest.raises(requests_lib.exceptions.HTTPError):
        fetch_transfers_page("0xABC", session=session)

    assert session.get.call_count == 1  # no retry for non-retryable 4xx


# ---------------------------------------------------------------------------
# parse_transfer — ERC-20 / ERC-721 field extraction
# ---------------------------------------------------------------------------

def _raw_item(token_type="ERC-721", from_field=None, to_field=None, **overrides):
    base = {
        "block_number": 1234567,
        "log_index": 42,
        "transaction_hash": "0xdeadbeef",
        "timestamp": "2026-06-15T12:00:00.000000Z",
        "method": "setUserStatus",
        "from": from_field if from_field is not None else {"hash": "0xAAAA"},
        "to": to_field if to_field is not None else {"hash": "0xBBBB"},
        "token": {"address_hash": "0x8D8d", "type": token_type},
        "total": {"token_id": "99", "value": "1000000000000000000"},
    }
    base.update(overrides)
    return base


def test_parse_erc721_extracts_token_id():
    record = parse_transfer(_raw_item(token_type="ERC-721"))

    assert record["token_type"] == "ERC-721"
    assert record["token_id"] == "99"
    assert "value" not in record


def test_parse_erc20_extracts_value():
    record = parse_transfer(_raw_item(token_type="ERC-20"))

    assert record["token_type"] == "ERC-20"
    assert record["value"] == "1000000000000000000"
    assert "token_id" not in record


def test_parse_normalizes_addresses_to_lowercase():
    record = parse_transfer(_raw_item())

    assert record["from_address"] == "0xaaaa"
    assert record["to_address"] == "0xbbbb"
    assert record["contract_address"] == "0x8d8d"


def test_parse_block_and_log_are_ints():
    record = parse_transfer(_raw_item())

    assert record["block_number"] == 1234567
    assert record["log_index"] == 42


def test_parse_null_from_is_zero_address():
    """Mint transfers have null sender — must not raise."""
    item = _raw_item()
    item["from"] = None

    record = parse_transfer(item)

    assert record["from_address"] == "0x0000000000000000000000000000000000000000"


def test_parse_null_to_is_zero_address():
    """Burn transfers have null recipient — must not raise."""
    item = _raw_item()
    item["to"] = None

    record = parse_transfer(item)

    assert record["to_address"] == "0x0000000000000000000000000000000000000000"


def test_parse_missing_field_raises_value_error_with_tx_hash():
    """Malformed items raise ValueError with the tx hash for easy debugging."""
    item = _raw_item()
    del item["block_number"]

    with pytest.raises(ValueError, match="0xdeadbeef"):
        parse_transfer(item)
