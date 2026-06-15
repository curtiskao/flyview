"""
StorageBackend abstraction for ingestion storage operations.

Two implementations:
  LocalStorageBackend  — writes to ./local_data/ (JSON files), no AWS needed
  AWSStorageBackend    — writes to DynamoDB + S3 via boto3

Selected at runtime via STORAGE_BACKEND env var (default: "local").
Use LOCAL_DATA_DIR to override the local data directory (default: ./local_data).

Local file layout:
  {data_dir}/checkpoints.json              — all checkpoints, keyed by contract address
  {data_dir}/transfers.json                — all parsed transfers, keyed by pk:sk for dedup
  {data_dir}/raw/{contract}/{date}/{ts}.json — raw API page responses
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class StorageBackend(ABC):
    @abstractmethod
    def write_raw(self, contract_address: str, data: dict) -> str:
        """Archive a raw API response. Returns a path/key string for logging."""

    @abstractmethod
    def get_checkpoint(self, contract_address: str) -> dict:
        """Return the stored checkpoint for a contract, or {} if none."""

    @abstractmethod
    def save_checkpoint(
        self,
        contract_address: str,
        block_number: int,
        log_index: int,
        tx_hash: str,
    ) -> None:
        """Persist the latest processed position for a contract."""

    @abstractmethod
    def batch_write_transfers(self, records: list[dict]) -> None:
        """Write parsed transfer records. Must be idempotent (safe to re-run)."""


# ---------------------------------------------------------------------------
# Local implementation
# ---------------------------------------------------------------------------

class LocalStorageBackend(StorageBackend):
    """
    File-based backend for local development and testing.
    All data lands under data_dir and is plain JSON — inspectable with cat/jq.
    """

    def __init__(self, data_dir: str = "./local_data"):
        self._root = Path(data_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "raw").mkdir(exist_ok=True)

    @property
    def _checkpoints_path(self) -> Path:
        return self._root / "checkpoints.json"

    @property
    def _transfers_path(self) -> Path:
        return self._root / "transfers.json"

    def write_raw(self, contract_address: str, data: dict) -> str:
        now = datetime.now(timezone.utc)
        raw_dir = self._root / "raw" / contract_address.lower() / now.strftime("%Y-%m-%d")
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{now.strftime('%Y%m%dT%H%M%S%f')}.json"
        path.write_text(json.dumps(data, indent=2))
        return str(path)

    def get_checkpoint(self, contract_address: str) -> dict:
        if not self._checkpoints_path.exists():
            return {}
        data = json.loads(self._checkpoints_path.read_text())
        return data.get(contract_address.lower(), {})

    def save_checkpoint(
        self,
        contract_address: str,
        block_number: int,
        log_index: int,
        tx_hash: str,
    ) -> None:
        data = json.loads(self._checkpoints_path.read_text()) if self._checkpoints_path.exists() else {}
        data[contract_address.lower()] = {
            "contract_address": contract_address.lower(),
            "last_block_number": block_number,
            "last_log_index": log_index,
            "last_tx_hash": tx_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._checkpoints_path.write_text(json.dumps(data, indent=2))

    def batch_write_transfers(self, records: list[dict]) -> None:
        # Keyed by pk:sk string — re-running is safe, duplicates overwrite.
        data = json.loads(self._transfers_path.read_text()) if self._transfers_path.exists() else {}
        for record in records:
            key = (
                f"CONTRACT#{record['contract_address']}"
                f":BLOCK#{record['block_number']:010d}"
                f"#LOG#{record['log_index']:05d}"
            )
            data[key] = record
        self._transfers_path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# AWS implementation
# ---------------------------------------------------------------------------

class AWSStorageBackend(StorageBackend):
    """
    boto3-backed storage for Lambda / deployed environments.
    Delegates to the helpers in storage.py so that module stays independently testable.
    """

    def __init__(self, transfers_table: str, checkpoints_table: str, raw_bucket: str):
        import boto3
        from storage import (
            batch_write_transfers as _batch,
            load_checkpoint as _load_cp,
            save_checkpoint as _save_cp,
            write_raw_to_s3 as _write_raw,
        )
        self._s3 = boto3.client("s3")
        self._dynamodb = boto3.resource("dynamodb")
        self._transfers_table = transfers_table
        self._checkpoints_table = checkpoints_table
        self._raw_bucket = raw_bucket
        self._batch = _batch
        self._load_cp = _load_cp
        self._save_cp = _save_cp
        self._write_raw = _write_raw

    def write_raw(self, contract_address: str, data: dict) -> str:
        return self._write_raw(self._s3, self._raw_bucket, contract_address, data)

    def get_checkpoint(self, contract_address: str) -> dict:
        return self._load_cp(self._dynamodb, self._checkpoints_table, contract_address)

    def save_checkpoint(
        self,
        contract_address: str,
        block_number: int,
        log_index: int,
        tx_hash: str,
    ) -> None:
        self._save_cp(self._dynamodb, self._checkpoints_table, contract_address, block_number, log_index, tx_hash)

    def batch_write_transfers(self, records: list[dict]) -> None:
        self._batch(self._dynamodb, self._transfers_table, records)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend(config: dict) -> StorageBackend:
    """
    Return the appropriate StorageBackend based on STORAGE_BACKEND env var.
    Defaults to "local" so the pipeline runs without any AWS setup.
    """
    backend_type = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend_type == "aws":
        logger.info("Using AWSStorageBackend")
        return AWSStorageBackend(
            transfers_table=config["transfers_table"],
            checkpoints_table=config["checkpoints_table"],
            raw_bucket=config["raw_bucket"],
        )
    data_dir = os.environ.get("LOCAL_DATA_DIR", "./local_data")
    logger.info("Using LocalStorageBackend (data_dir=%s)", data_dir)
    return LocalStorageBackend(data_dir=data_dir)
