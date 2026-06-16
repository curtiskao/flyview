"""
Aggregation Lambda entry point.

Triggered by EventBridge on a schedule (after ingestion) when deployed, or
run directly via `python handler.py` for local testing.

Environment variables:
  STORAGE_BACKEND        "local" (default) or "aws"
  TOP_LIST_SIZE          How many wallets in top earner/spender lists (default: 100)

  Local mode:
  TRANSFERS_DATA_PATH    Path to ingestion transfers.json
                         Default: ../ingestion/local_data/transfers.json
  LOCAL_DATA_DIR         Where to write aggregation outputs
                         Default: ./local_data

  AWS mode:
  TRANSFERS_TABLE        DynamoDB table name for parsed transfers
  AGGREGATES_TABLE       DynamoDB table name for aggregation outputs
"""

import json
import logging
import os
from datetime import datetime, timezone

from aggregator import (
    compute_daily_stats,
    compute_diner_stats,
    compute_top_earners,
    compute_top_spenders,
)
from agg_storage import get_backend

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_config() -> dict:
    return {
        "top_list_size": int(os.environ.get("TOP_LIST_SIZE", "100")),
        "transfers_table": os.environ.get("TRANSFERS_TABLE", "flyview-transfers"),
        "aggregates_table": os.environ.get("AGGREGATES_TABLE", "flyview-aggregates"),
    }


def lambda_handler(event, context):
    config = _get_config()
    backend = get_backend(config)

    logger.info("Loading transfers...")
    transfers = backend.load_all_transfers()
    logger.info("Loaded %d transfers", len(transfers))

    logger.info("Computing diner stats...")
    diner_stats = compute_diner_stats(transfers)
    logger.info("Computed stats for %d wallets", len(diner_stats))

    # Extract first-seen date per wallet for new-wallet counting in daily stats
    diner_first_seen = {
        wallet: d["first_seen"][:10]
        for wallet, d in diner_stats.items()
        if d["first_seen"]
    }

    logger.info("Computing daily stats...")
    daily_stats = compute_daily_stats(transfers, diner_first_seen)
    logger.info("Computed stats for %d days", len(daily_stats))

    top_n = config["top_list_size"]
    top_earners = compute_top_earners(diner_stats, n=top_n)
    top_spenders = compute_top_spenders(diner_stats, n=top_n)
    logger.info("Computed top %d earners and spenders", top_n)

    meta = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "total_transfers": len(transfers),
        "total_diners": len(diner_stats),
        "total_days": len(daily_stats),
        "top_list_size": top_n,
    }

    logger.info("Writing aggregates...")
    backend.write_diner_aggregates(diner_stats)
    backend.write_daily_aggregates(daily_stats)
    backend.write_top_lists(top_earners, top_spenders)
    backend.write_meta(meta)

    logger.info("Aggregation complete: %s", meta)
    return {"statusCode": 200, "meta": meta}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
