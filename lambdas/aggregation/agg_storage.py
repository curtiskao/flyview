"""
StorageBackend abstraction for aggregation storage operations.

Two implementations:
  LocalAggregationBackend  — reads transfers from a JSON file, writes aggregates
                             to JSON files under ./local_data/
  AWAAggregationBackend    — scans DynamoDB transfers table, writes to aggregates table

Selected at runtime via STORAGE_BACKEND env var (default: "local").

Environment variables:
  STORAGE_BACKEND        "local" (default) or "aws"
  TRANSFERS_DATA_PATH    Path to ingestion transfers.json (local mode only)
                         Default: ../ingestion/local_data/transfers.json
  LOCAL_DATA_DIR         Directory for aggregation outputs (local mode only)
                         Default: ./local_data

  AWS mode only:
  TRANSFERS_TABLE        DynamoDB table name containing parsed transfers
  AGGREGATES_TABLE       DynamoDB table name for aggregated outputs

Local file layout (under LOCAL_DATA_DIR):
  diners.json        {wallet_address: {fly_earned, fly_burned, ...}}
  daily.json         {date: {active_wallets, new_wallets, fly_minted, ...}}
  top_earners.json   [{rank, wallet_address, fly_earned, ...}, ...]
  top_spenders.json  [{rank, wallet_address, fly_burned, ...}, ...]
  meta.json          {computed_at, total_transfers, total_diners, total_days}

DynamoDB aggregates table schema:
  pk="DINER#{wallet}"        sk="STATS"            — per-diner stats
  pk="DAILY#{date}"          sk="STATS"            — per-day stats
  pk="LEADERBOARD#EARNERS"   sk="RANK#{rank:05d}"  — top earners
  pk="LEADERBOARD#SPENDERS"  sk="RANK#{rank:05d}"  — top spenders
  pk="META"                  sk="AGGREGATION"      — run metadata
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TRANSFERS_PATH = "../ingestion/local_data/transfers.json"
_DEFAULT_LOCAL_DATA_DIR = "./local_data"


class AggregationStorageBackend(ABC):
    @abstractmethod
    def load_all_transfers(self) -> list[dict]:
        """Return all stored transfer records."""

    @abstractmethod
    def write_diner_aggregates(self, stats: dict[str, dict]) -> None:
        """Persist per-diner aggregated stats."""

    @abstractmethod
    def write_daily_aggregates(self, stats: dict[str, dict]) -> None:
        """Persist per-day network aggregates."""

    @abstractmethod
    def write_top_lists(
        self,
        top_earners: list[dict],
        top_spenders: list[dict],
    ) -> None:
        """Persist top-earner and top-spender ranked lists."""

    @abstractmethod
    def write_meta(self, meta: dict) -> None:
        """Persist run metadata (computed_at, counts, etc.)."""


class LocalAggregationBackend(AggregationStorageBackend):
    """
    File-based backend for local development and testing.
    Reads transfers from a JSON file; writes aggregates as JSON files.
    All output is in local_data/ and inspectable with cat/jq.
    """

    def __init__(
        self,
        transfers_path: str = _DEFAULT_TRANSFERS_PATH,
        local_data_dir: str = _DEFAULT_LOCAL_DATA_DIR,
    ):
        self._transfers_path = Path(transfers_path)
        self._out = Path(local_data_dir)
        self._out.mkdir(parents=True, exist_ok=True)

    def load_all_transfers(self) -> list[dict]:
        if not self._transfers_path.exists():
            raise FileNotFoundError(
                f"Transfers file not found: {self._transfers_path}. "
                "Run the ingestion Lambda first."
            )
        data = json.loads(self._transfers_path.read_text())
        return list(data.values())

    def write_diner_aggregates(self, stats: dict[str, dict]) -> None:
        path = self._out / "diners.json"
        path.write_text(json.dumps(stats, indent=2))
        logger.info("Wrote diner aggregates → %s (%d wallets)", path, len(stats))

    def write_daily_aggregates(self, stats: dict[str, dict]) -> None:
        path = self._out / "daily.json"
        path.write_text(json.dumps(stats, indent=2))
        logger.info("Wrote daily aggregates → %s (%d days)", path, len(stats))

    def write_top_lists(
        self,
        top_earners: list[dict],
        top_spenders: list[dict],
    ) -> None:
        (self._out / "top_earners.json").write_text(json.dumps(top_earners, indent=2))
        (self._out / "top_spenders.json").write_text(json.dumps(top_spenders, indent=2))
        logger.info(
            "Wrote top lists → top_earners.json (%d), top_spenders.json (%d)",
            len(top_earners),
            len(top_spenders),
        )

    def write_meta(self, meta: dict) -> None:
        path = self._out / "meta.json"
        path.write_text(json.dumps(meta, indent=2))
        logger.info("Wrote metadata → %s", path)


class AWSAggregationBackend(AggregationStorageBackend):
    """
    boto3-backed backend for the deployed Lambda environment.
    Scans the transfers DynamoDB table; writes to the aggregates table.
    """

    def __init__(self, transfers_table: str, aggregates_table: str):
        import boto3
        self._dynamodb = boto3.resource("dynamodb")
        self._transfers_table = transfers_table
        self._aggregates_table = aggregates_table

    def load_all_transfers(self) -> list[dict]:
        table = self._dynamodb.Table(self._transfers_table)
        records = []
        kwargs: dict = {}
        while True:
            response = table.scan(**kwargs)
            records.extend(response.get("Items", []))
            last = response.get("LastEvaluatedKey")
            if not last:
                break
            kwargs["ExclusiveStartKey"] = last
        logger.info("Loaded %d transfers from DynamoDB", len(records))
        return records

    def write_diner_aggregates(self, stats: dict[str, dict]) -> None:
        table = self._dynamodb.Table(self._aggregates_table)
        with table.batch_writer() as batch:
            for wallet, d in stats.items():
                batch.put_item(Item={
                    "pk": f"DINER#{wallet}",
                    "sk": "STATS",
                    **d,
                })
        logger.info("Wrote %d diner aggregates to DynamoDB", len(stats))

    def write_daily_aggregates(self, stats: dict[str, dict]) -> None:
        table = self._dynamodb.Table(self._aggregates_table)
        with table.batch_writer() as batch:
            for date, d in stats.items():
                batch.put_item(Item={
                    "pk": f"DAILY#{date}",
                    "sk": "STATS",
                    **d,
                })
        logger.info("Wrote %d daily aggregates to DynamoDB", len(stats))

    def write_top_lists(
        self,
        top_earners: list[dict],
        top_spenders: list[dict],
    ) -> None:
        table = self._dynamodb.Table(self._aggregates_table)
        with table.batch_writer() as batch:
            for entry in top_earners:
                batch.put_item(Item={
                    "pk": "LEADERBOARD#EARNERS",
                    "sk": f"RANK#{entry['rank']:05d}",
                    **entry,
                })
            for entry in top_spenders:
                batch.put_item(Item={
                    "pk": "LEADERBOARD#SPENDERS",
                    "sk": f"RANK#{entry['rank']:05d}",
                    **entry,
                })
        logger.info(
            "Wrote leaderboards to DynamoDB (%d earners, %d spenders)",
            len(top_earners),
            len(top_spenders),
        )

    def write_meta(self, meta: dict) -> None:
        table = self._dynamodb.Table(self._aggregates_table)
        table.put_item(Item={
            "pk": "META",
            "sk": "AGGREGATION",
            **meta,
        })


def get_backend(config: dict) -> AggregationStorageBackend:
    """Return the appropriate backend based on STORAGE_BACKEND env var."""
    backend_type = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend_type == "aws":
        logger.info("Using AWSAggregationBackend")
        return AWSAggregationBackend(
            transfers_table=config["transfers_table"],
            aggregates_table=config["aggregates_table"],
        )
    transfers_path = os.environ.get("TRANSFERS_DATA_PATH", _DEFAULT_TRANSFERS_PATH)
    local_data_dir = os.environ.get("LOCAL_DATA_DIR", _DEFAULT_LOCAL_DATA_DIR)
    logger.info(
        "Using LocalAggregationBackend (transfers=%s, out=%s)",
        transfers_path,
        local_data_dir,
    )
    return LocalAggregationBackend(
        transfers_path=transfers_path,
        local_data_dir=local_data_dir,
    )
