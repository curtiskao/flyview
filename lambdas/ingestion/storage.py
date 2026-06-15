"""
S3 and DynamoDB helpers for the ingestion pipeline.

All functions accept explicit resource/client objects so callers can inject
mocks in tests. No module-level boto3 state.

DynamoDB schema
---------------
transfers table:
  PK  pk   = "CONTRACT#{contract_address}"
  SK  sk   = "BLOCK#{block_number:010d}#LOG#{log_index:05d}"

checkpoints table:
  PK  contract_address  (plain string)
  Attrs: last_block_number (N), last_log_index (N), last_tx_hash (S), updated_at (S)

restaurants table:
  PK  address  (plain string)
  Attrs: name (S), first_seen_block (N), last_seen_block (N)
"""

import json
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def write_raw_to_s3(s3_client, bucket: str, contract_address: str, data: dict) -> str:
    """
    Write a raw Blockscout API response to S3.

    Returns the S3 key used.

    Key layout:
      transfers/{contract_address}/{YYYY}/{MM}/{DD}/{HH}/{iso_ts}.json
    """
    now = datetime.now(timezone.utc)
    prefix = (
        f"transfers/{contract_address.lower()}/"
        f"{now:%Y/%m/%d/%H}/"
        f"{now.strftime('%Y%m%dT%H%M%S%f')}.json"
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=prefix,
        Body=json.dumps(data),
        ContentType="application/json",
    )
    return prefix


# ---------------------------------------------------------------------------
# DynamoDB — transfers
# ---------------------------------------------------------------------------

def _transfer_keys(record: dict) -> dict:
    return {
        "pk": f"CONTRACT#{record['contract_address']}",
        "sk": f"BLOCK#{record['block_number']:010d}#LOG#{record['log_index']:05d}",
    }


def batch_write_transfers(dynamodb_resource, table_name: str, records: list[dict]) -> None:
    """
    Write a list of parsed transfer records to DynamoDB in batches of 25.
    Overwrites on conflict (idempotent).
    """
    table = dynamodb_resource.Table(table_name)
    # DynamoDB batch_writer handles chunking into 25-item batches automatically
    with table.batch_writer() as batch:
        for record in records:
            item = {**_transfer_keys(record), **record}
            batch.put_item(Item=item)


# ---------------------------------------------------------------------------
# DynamoDB — checkpoints
# ---------------------------------------------------------------------------

def load_checkpoint(dynamodb_resource, table_name: str, contract_address: str) -> dict:
    """
    Load the ingestion checkpoint for a contract.
    Returns {} if no checkpoint exists yet (first run).
    """
    table = dynamodb_resource.Table(table_name)
    response = table.get_item(Key={"contract_address": contract_address.lower()})
    return response.get("Item", {})


def save_checkpoint(
    dynamodb_resource,
    table_name: str,
    contract_address: str,
    block_number: int,
    log_index: int,
    tx_hash: str,
) -> None:
    table = dynamodb_resource.Table(table_name)
    table.put_item(Item={
        "contract_address": contract_address.lower(),
        "last_block_number": block_number,
        "last_log_index": log_index,
        "last_tx_hash": tx_hash,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_new_transfer(record: dict, checkpoint: dict) -> bool:
    """
    Return True if this transfer is newer than the stored checkpoint.
    Transfers arrive newest-first from Blockscout, so we stop fetching
    once we see a record that is not new.
    """
    if not checkpoint:
        return True
    last_block = int(checkpoint["last_block_number"])
    last_log = int(checkpoint["last_log_index"])
    b, l = record["block_number"], record["log_index"]
    return b > last_block or (b == last_block and l > last_log)
