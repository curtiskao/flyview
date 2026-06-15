"""
Ingestion Lambda entry point.

Triggered by EventBridge on a schedule (every 5 min) when deployed, or run
directly via `python handler.py` for local testing.

Environment variables:
  STORAGE_BACKEND    "local" (default) or "aws"
  LOCAL_DATA_DIR     Where local backend writes files (default: ./local_data)
  MAX_PAGES          Max cursor iterations per contract per invocation (default: 50)

  AWS mode only (STORAGE_BACKEND=aws):
  TRANSFERS_TABLE    DynamoDB table name for parsed transfers
  CHECKPOINTS_TABLE  DynamoDB table name for ingestion checkpoints
  RAW_BUCKET         S3 bucket name for raw API responses
"""

import json
import logging
import os

from blockscout import fetch_transfers_page, parse_transfer
from storage import is_new_transfer
from storage_backend import StorageBackend, get_backend

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CONTRACTS = [
    # (address, label) — label used only for logging
    ("0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E", "BBST"),  # ERC-721 check-in token
    ("0x6D0FEFe3543212593cee1C8C50EAdf91aCE623b8", "FLY"),   # ERC-20 reward token
]


def _get_config() -> dict:
    return {
        "max_pages": int(os.environ.get("MAX_PAGES", "50")),
        # Table/bucket names used only by AWSStorageBackend; harmless defaults for local mode.
        "transfers_table": os.environ.get("TRANSFERS_TABLE", "flyview-transfers"),
        "checkpoints_table": os.environ.get("CHECKPOINTS_TABLE", "flyview-checkpoints"),
        "raw_bucket": os.environ.get("RAW_BUCKET", "flyview-raw"),
    }


def ingest_contract(
    contract_address: str,
    label: str,
    config: dict,
    backend: StorageBackend,
) -> dict:
    """
    Ingest new transfers for one contract. Returns a summary dict.
    """
    checkpoint = backend.get_checkpoint(contract_address)
    logger.info(
        "[%s] starting ingestion, checkpoint: block=%s log=%s",
        label,
        checkpoint.get("last_block_number", "none"),
        checkpoint.get("last_log_index", "none"),
    )

    next_page_params = None
    pages_fetched = 0
    total_written = 0
    total_skipped = 0
    latest_record = None  # highest block+log seen this run (transfers arrive newest-first)
    stop_reason = "max_pages"

    while pages_fetched < config["max_pages"]:
        items, next_page_params = fetch_transfers_page(contract_address, next_page_params)

        if not items:
            stop_reason = "empty_page"
            logger.info("[%s] empty page after %d pages, stopping", label, pages_fetched)
            break

        # Archive raw response — non-fatal; S3/file is debug-only, not source of truth.
        try:
            backend.write_raw(
                contract_address,
                {"items": items, "next_page_params": next_page_params},
            )
        except Exception:
            logger.warning("[%s] raw archive write failed (non-fatal), continuing", label, exc_info=True)

        # Parse items, skipping any that fail to parse.
        new_records = []
        caught_up = False
        for item in items:
            try:
                record = parse_transfer(item)
            except Exception:
                total_skipped += 1
                logger.warning(
                    "[%s] skipping unparseable item tx=%s",
                    label,
                    item.get("transaction_hash"),
                    exc_info=True,
                )
                continue

            if is_new_transfer(record, checkpoint):
                new_records.append(record)
                # Transfers arrive newest-first, so latest_record is set on the
                # first new item of the first page and rarely changes after that.
                if latest_record is None or (
                    record["block_number"] > latest_record["block_number"]
                    or (
                        record["block_number"] == latest_record["block_number"]
                        and record["log_index"] > latest_record["log_index"]
                    )
                ):
                    latest_record = record
            else:
                # First already-seen record means all remaining are older — stop.
                caught_up = True
                break

        if new_records:
            backend.batch_write_transfers(new_records)
            total_written += len(new_records)
            # Save checkpoint after each page so a mid-run crash is recoverable.
            backend.save_checkpoint(
                contract_address,
                latest_record["block_number"],
                latest_record["log_index"],
                latest_record["tx_hash"],
            )

        pages_fetched += 1
        logger.info(
            "[%s] page %d: %d new, %d skipped (cumulative: %d written)",
            label,
            pages_fetched,
            len(new_records),
            total_skipped,
            total_written,
        )

        if caught_up:
            stop_reason = "caught_up"
            logger.info("[%s] caught up with existing data after %d pages", label, pages_fetched)
            break

        if next_page_params is None:
            stop_reason = "end_of_history"
            logger.info("[%s] reached end of transfer history after %d pages", label, pages_fetched)
            break

    if stop_reason == "max_pages":
        logger.warning(
            "[%s] hit MAX_PAGES=%d limit — may still have unprocessed transfers",
            label,
            config["max_pages"],
        )

    return {
        "contract": label,
        "pages_fetched": pages_fetched,
        "records_written": total_written,
        "records_skipped": total_skipped,
        "stop_reason": stop_reason,
    }


def lambda_handler(event, context):
    config = _get_config()
    backend = get_backend(config)

    results = []
    for address, label in CONTRACTS:
        try:
            summary = ingest_contract(address, label, config, backend)
            results.append(summary)
            logger.info("[%s] done: %s", label, summary)
        except Exception:
            logger.exception("[%s] ingestion failed", label)
            results.append({"contract": label, "error": True})

    return {"statusCode": 200, "results": results}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
