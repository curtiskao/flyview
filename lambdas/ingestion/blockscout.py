"""
Blockscout API client for fetching token transfer pages.

Pagination is cursor-based: each response includes next_page_params (a dict
of query params to pass on the next request). When next_page_params is None,
you've reached the oldest record.

Before relying on field names here, verify against the live endpoint:
  curl "https://explorer.flynet.org/api/v2/tokens/<address>/transfers" | python3 -m json.tool

Some Blockscout deployments vary in response shape.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://explorer.flynet.org/api/v2"
TRANSFERS_PATH = "/tokens/{address}/transfers"
REQUEST_TIMEOUT = 15  # seconds
MAX_RETRIES = 3
# Status codes that warrant a retry (rate limit + transient server errors)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def fetch_transfers_page(
    contract_address: str,
    next_page_params: dict | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict], dict | None]:
    """
    Fetch one page of transfers for a token contract.

    Returns (items, next_page_params). Pass the returned next_page_params as
    the argument on the next call. When next_page_params is None, you've
    reached the end (oldest transfer).

    Retries up to MAX_RETRIES times on network errors and retryable HTTP
    status codes, with exponential backoff.
    """
    url = BASE_URL + TRANSFERS_PATH.format(address=contract_address)
    params = next_page_params or {}
    requester = session or requests

    for attempt in range(MAX_RETRIES):
        try:
            response = requester.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code in _RETRYABLE_STATUS:
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Blockscout returned %s for %s, retrying in %ss (attempt %d/%d)",
                        response.status_code,
                        contract_address,
                        wait,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                # Last attempt — let raise_for_status() surface the error
            response.raise_for_status()

            body = response.json()
            return body.get("items", []), body.get("next_page_params")

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Blockscout request failed for %s (%s), retrying in %ss (attempt %d/%d)",
                    contract_address,
                    exc,
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(wait)
            else:
                raise

    # Unreachable, but satisfies type checker
    raise requests.exceptions.RequestException("All retries exhausted")


def _address_hash(field: dict | None) -> str:
    """
    Extract a lowercase address hash from a Blockscout address field.
    Returns the zero address string if the field is null — Blockscout uses
    null for the sender on mint transfers and the recipient on burns.
    """
    if field is None:
        return "0x0000000000000000000000000000000000000000"
    return field["hash"].lower()


def parse_transfer(item: dict) -> dict:
    """
    Normalize a raw Blockscout transfer item into a flat dict for storage.

    Verified field locations against the live explorer.flynet.org/api/v2 response:
      - item["block_number"]           — block number (int)
      - item["log_index"]              — log index within block (int)
      - item["transaction_hash"]       — tx hash
      - item["timestamp"]              — ISO 8601 string
      - item["method"]                 — contract method name (e.g. "setUserStatus", "mint")
      - item["from"]                   — sender address object, or null on mints
      - item["to"]                     — recipient address object, or null on burns
      - item["token"]["address_hash"]  — contract address (NOT "address")
      - item["token"]["type"]          — "ERC-20" or "ERC-721"
      - item["total"]["value"]         — amount string (ERC-20)
      - item["total"]["token_id"]      — token id string (ERC-721)
    """
    try:
        token = item["token"]
        total = item["total"]
        token_type = token["type"]

        record = {
            "contract_address": token["address_hash"].lower(),
            "block_number": int(item["block_number"]),
            "log_index": int(item["log_index"]),
            "tx_hash": item["transaction_hash"],
            "timestamp": item["timestamp"],
            "method": item.get("method"),
            "from_address": _address_hash(item.get("from")),
            "to_address": _address_hash(item.get("to")),
            "token_type": token_type,
        }

        if token_type == "ERC-20":
            record["value"] = total["value"]
        elif token_type == "ERC-721":
            record["token_id"] = total["token_id"]

        return record

    except (KeyError, TypeError, ValueError) as exc:
        tx = item.get("transaction_hash", "<unknown>")
        raise ValueError(f"Failed to parse transfer tx={tx}: {exc}") from exc
