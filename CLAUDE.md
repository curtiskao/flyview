# CLAUDE.md

## Project Overview

This project is a diner and network analytics service built on top of public on-chain
data from Blackbird's Flynet (an L3 rollup on Base, purpose-built for restaurant
loyalty/payments). The system ingests FLY and BBST token transfer events, computes
diner-level and network-level statistics (FLY earned/burned, active wallets, growth
trends), and serves the results through a web UI.

Restaurant mapping was investigated thoroughly and deferred — see ROADMAP.md for the
full decision record. Do not re-investigate without new information.

This is also a learning project for AWS serverless architecture. Prefer
simple, cheap, free-tier-friendly AWS services (Lambda, EventBridge,
DynamoDB, S3, API Gateway) over managed/always-on services (RDS, ECS, EC2)
unless there's a clear reason otherwise.

## Data Source

All on-chain data comes from the Blockscout REST API at
`https://explorer.flynet.org/api/v2/`. Do not attempt to use raw
`eth_getLogs` / JSON-RPC — no public RPC endpoint has been confirmed, and the
Blockscout API is sufficient and pre-decoded.

Key endpoints:
- `/api/v2/stats` — network stats
- `/api/v2/tokens` — list of tracked tokens
- `/api/v2/tokens/{address}/transfers` — paginated transfer events (use
  `next_page_params` for pagination)
- `/api/v2/addresses/{address}` — address details/balances

Confirmed contracts:
- **FLY (ERC-20)**: `0x6D0FEFe3543212593cee1C8C50EAdf91aCE623b8` — reward/payment token
- **Blackbird Status (ERC-721, "BBST")**: `0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E` —
  per-diner status NFT; updated via `setUserStatus(diner, statusId)` on visits

## Architecture Conventions

- **Ingestion**: scheduled Lambda (EventBridge cron), pulls from Blockscout
  API, writes raw JSON to S3 and parsed records to DynamoDB. Must be
  idempotent and incremental (checkpoint table tracks last-processed
  page/block/tx).
- **Aggregation**: separate scheduled Lambda, computes leaderboard metrics
  from DynamoDB transfer records, writes to an aggregates table.
- **API**: API Gateway + Lambda, reads from aggregates table only (never
  computes aggregates on request).
- **Frontend**: React/Next.js, consumes the API.

## Local-first development requirement
All ingestion, processing, and storage logic must be written so it can run and be verified locally, without an AWS account, before any AWS deployment.
Concretely:

Dependency injection for storage/AWS services: Any code that talks to DynamoDB, S3, or other AWS services must do so through a small interface/abstract class (e.g. StorageBackend with methods like put_item, get_item, write_raw), not direct boto3 calls scattered through business logic. Provide two implementations: a LocalStorageBackend (writes to local JSON files / SQLite under a ./local_data/ directory) and an AWSStorageBackend (real boto3 calls to DynamoDB/S3). Selected via an env var (e.g. STORAGE_BACKEND=local|aws), defaulting to local.
Lambda handlers must be callable as plain scripts: handler.py's main/lambda_handler function should be invocable directly via python handler.py for local testing (e.g. via if __name__ == "__main__":), not only as an AWS Lambda entry point.
No EventBridge dependency for testing: Scheduling logic stays entirely in CDK/AWS config — never required for running or testing the underlying logic. Locally, repeated runs are triggered manually or via a simple loop/cron, not EventBridge.
External API clients (Blockscout, etc.) are environment-agnostic: These only use requests/standard libraries, with no AWS dependencies, so they run identically local or deployed.
CDK stack stays separate from logic: cdk/ only wires up real AWS resources and passes config (table names, bucket names, env vars) to Lambdas. It contains no business logic that would need separate local testing.
Every phase: before marking a phase "done," it must be demonstrated running locally end-to-end (STORAGE_BACKEND=local) with output inspectable in ./local_data/. AWS deployment and cdk deploy verification is a separate, later checklist item per phase — not a blocker for marking core logic complete.

## Coding Preferences

- Python for Lambda functions (ingestion, aggregation, API handlers)
- Keep DynamoDB schemas simple — prefer single-table design only if it
  meaningfully simplifies access patterns; otherwise use separate tables
  (`transfers`, `checkpoints`, `aggregates`, `restaurants`)
- Avoid introducing Redis, RDS, or other paid/always-on infra unless a
  specific bottleneck justifies it
- Write small, testable functions — separate "fetch from Blockscout",
  "parse/normalize", and "write to storage" concerns

## Important Findings

Confirmed by curling the live Blockscout API during Phase 1 development.

### Blockscout API response shape (explorer.flynet.org)
- **Pagination**: `next_page_params` is `{"block_number": <int>, "index": <int>}` — pass
  these two fields as query params on the next request. `null` means end of history.
- **Contract address field**: `token["address_hash"]`, NOT `token["address"]`. Using
  `token["address"]` raises `KeyError` on every item.
- **`block_number` and `log_index`** are integers in the response (not strings).
- **`item["method"]`** is present at top level: `"setUserStatus"` for BBST transfers,
  `"mint"` for FLY minting.
- **`item["type"]`** distinguishes `"token_minting"` from `"token_transfer"`.

### BBST token semantics (Phase 0 finding)
BBST (`0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E`) is a **per-diner status NFT**,
not a per-check-in event token. Key observations:
- All transfers are mints (`from` = zero address, `method` = `"setUserStatus"`).
- The `to` address is the **diner's wallet**, not a restaurant wallet.
- `total.token_id` is a unique NFT id per diner (e.g. `"58495"`).
- `total.token_instance.metadata.attributes` contains the diner's loyalty tier:
  `Level`, `Track`, `Fly Multiplier`, `Valid From`, etc.
- **Restaurant wallets are not visible in BBST transfers at all.**

### FLY token semantics (Phase 1 finding)
FLY (`0x6D0FEFe3543212593cee1C8C50EAdf91aCE623b8`) is also purely diner-facing:
- **`token_minting`** (`method: "mint"`): zero address → diner wallet. Protocol credits diners for visits/spend.
- **`token_burning`** (`method: "burn"`): diner wallet → zero address. Diners redeeming/spending FLY.
- **Zero wallet-to-wallet transfers** across 1,000 sampled items (20 pages).
- **Tx sender is always `0xBBed7fdF8465AC61D5662b38Af06F3a25A9B3D66`** (Blackbird backend hot wallet) for both mints and burns.
- Decoded call params: `mint(address to, uint256 amount)` and `burn(address burner, uint256 amount)` — no restaurant address anywhere.
- **Restaurant wallets are not present in FLY transfer data at all.**

### fly.town investigation (Phase 1 finding)
fly.town is a Next.js App Router site. All restaurant data is rendered server-side via
RSC payloads — there is no public REST API for leaderboard data.

**What is publicly accessible (no auth):**
- **Homepage RSC** (`https://fly.town`): today's top 5 restaurants with `checkIns` (count),
  `rank`, `name`, `slug`, `restaurantUuid`, `accentColor`. Scraped via RSC header.
- **`/restaurants` RSC**: JSON-LD list of all **1,959 restaurants** with `name` and `slug`
  (alphabetically ordered, no check-in counts).
- **Individual restaurant pages** (`/maman`): name, address, cuisine, `restaurantUuid`,
  today's rank label. No numeric check-in count or historical stats.

**What is NOT publicly accessible:**
- Leaderboard beyond top 5 (requires auth)
- Historical check-in data (requires auth)
- Restaurant wallet addresses (not exposed anywhere — Blackbird doesn't publish them)

**Auth-gated backend:**
- `https://api.blackbird.xyz` returns 401 without auth.
- fly.town uses OAuth via `https://api.blackbird.xyz/oauth/authorize`.
- Authenticated fly.town calls proxy to `/api/core{path}` with `Authorization: Bearer {token}`.

### Project direction (decided)
Restaurant-specific check-in data is entirely off-chain and private to Blackbird —
no restaurant wallet addresses exist anywhere in public data. The project focuses on
**diner and network analytics** using the on-chain data that is available. See
ROADMAP.md "Restaurant Mapping" section for the full decision record and deferred
enhancement list.

## Status / Current Phase

See ROADMAP.md for full phase breakdown. Phase 0 complete. Currently in Phase 1
(ingestion foundation) — core ingestion logic written and running locally
(`STORAGE_BACKEND=local`); AWS deployment deferred until account is set up.

## Things to Avoid

- Don't assume Flynet has a public RPC — it doesn't (confirmed). Blockscout
  API is the source of truth.
- Don't hardcode restaurant name mappings without noting they're
  best-effort/manual until a better source is found.
- Don't over-engineer caching or scaling for a project with unknown/low
  traffic — start simple (Phase 1-4 simplicity), optimize later if needed.


