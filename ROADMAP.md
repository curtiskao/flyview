# Flynet Diner Analytics — Project Roadmap

## Project Summary

A web service that ingests on-chain activity from Blackbird's Flynet (an L3 rollup on
Base, accessible via the public Blockscout explorer at explorer.flynet.org), aggregates
it into diner and network-level statistics, and presents the results through a web UI.
The project doubles as hands-on practice with AWS serverless architecture (Lambda,
EventBridge, DynamoDB, S3, API Gateway).

**Focus:** diner/network analytics — what the on-chain data actually supports cleanly.
Restaurant mapping was investigated and explicitly deferred (see section below).

**Data source:** explorer.flynet.org/api/v2 (Blockscout REST API)

Confirmed contracts:
- `FLY` (ERC-20) — `0x6D0FEFe3543212593cee1C8C50EAdf91aCE623b8` — reward token, minted to diners on visits, burned on spend
- `Blackbird Status` (ERC-721, "BBST") — `0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E` — per-diner status NFT, updated via `setUserStatus`

---

## Requirements

### Functional
- Periodically ingest FLY and BBST transfer events from Blockscout
- Compute diner-level metrics: FLY earned (mints), FLY spent (burns), BBST status tier
- Compute network-level metrics: daily/weekly active diner wallets, new wallet growth,
  FLY mint and burn velocity, BBST tier distribution
- Surface top earner and top spender wallets
- Serve aggregated data via an API
- Display network dashboard and diner activity in a web UI

### Non-functional
- Cost: stay within AWS free tier as much as possible ($0–2/month target)
- Idempotent ingestion (safe to re-run, handles pagination/checkpointing)
- Incremental: only process new transfers since last run

### Technical Stack
- **Ingestion:** Python (Lambda), Blockscout REST API via `requests`
- **Storage:** S3 (raw API responses), DynamoDB (parsed transfers + aggregates + checkpoint)
- **Scheduling:** EventBridge (cron, every 5 min)
- **Aggregation:** Lambda (batch, scheduled after ingestion)
- **API:** API Gateway + Lambda (plain Lambda handlers)
- **Frontend:** React/Next.js, hosted on S3 + CloudFront or Vercel

---

## Roadmap

### Phase 0 — Data Investigation ✅ Complete
- [x] Pull sample transfers from BBST and FLY contracts
- [x] Confirm BBST semantics: per-diner status NFT, not per-check-in event
- [x] Confirm FLY semantics: mint to diner on earn, burn from diner on spend
- [x] Investigate restaurant wallet identification — see "Restaurant Mapping" section below
- [x] Investigate fly.town for restaurant data sources
- [x] Document findings before building ingestion

### Phase 1 — Ingestion Foundation (in progress)
- [ ] Set up AWS account/IAM roles for the project
- [ ] Create DynamoDB tables: `transfers`, `checkpoints`
- [ ] Create S3 bucket for raw API response archive
- [x] Write ingestion Lambda: paginated pull from Blockscout API using
      `next_page_params`, write raw to S3/local, parsed to DynamoDB/local
- [x] Local-first: `STORAGE_BACKEND=local` runs end-to-end without AWS
- [ ] Set up EventBridge schedule (every 5 min)
- [ ] Test end-to-end ingestion with a manual invoke against AWS

### Phase 2 — Aggregation
- [ ] Write aggregation Lambda reading from `transfers` table
- [ ] Compute per-diner: total FLY earned, total FLY burned, BBST tier
- [ ] Compute network-level per day: active wallets, new wallets, FLY minted, FLY burned, burn rate
- [ ] Compute top earners and top spenders (by wallet address)
- [ ] Write snapshots to DynamoDB `aggregates` table
- [ ] Schedule aggregation Lambda to run after ingestion
- [ ] Demonstrate locally: `STORAGE_BACKEND=local` output inspectable in `./local_data/`

### Phase 3 — API Layer
- [ ] API Gateway + Lambda endpoints:
  - `GET /network/summary` — daily/weekly active wallets, FLY velocity, growth rate
  - `GET /network/timeseries` — time-series of network metrics
  - `GET /diners/top` — top earners and spenders
  - `GET /diners/{address}` — individual diner stats (FLY earned/burned, tier, history)
- [ ] Read from `aggregates` table only (never compute on request)

### Phase 4 — Frontend
- [ ] React/Next.js network dashboard (active wallets, FLY velocity, growth charts)
- [ ] Top diners table (sortable by FLY earned / FLY spent)
- [ ] Individual diner page with activity time-series
- [ ] Deploy to S3 + CloudFront or Vercel

### Phase 5 — Polish & Hardening
- [ ] Monitoring (CloudWatch alarms on Lambda errors)
- [ ] Cost review — confirm staying within free tier
- [ ] Documentation and README

---

## Restaurant Mapping — Investigation Findings & Decision

**Status: Investigated and deferred. Do not re-investigate without new information.**

### What was established

Restaurant-specific data is entirely off-chain and private to Blackbird. After
exhaustively checking every available angle — FLY transfer decoded params, BBST transfer
decoded params, Flynet contract event logs, related/unverified contracts, fly.town public
APIs, and the fly.town homepage RSC payload — no restaurant wallet addresses or restaurant
identifiers exist anywhere in public on-chain data.

Both FLY and BBST contracts exclusively touch diner wallets:
- **FLY**: `mint(diner, amount)` and `burn(diner, amount)` — zero address as counterparty, no restaurant param
- **BBST**: `setUserStatus(diner, statusId)` — no restaurant param
- All transactions are sent by a single Blackbird backend wallet (`0xBBed7fdF8465AC61D5662b38Af06F3a25A9B3D66`)

The Blackbird app knows which restaurant triggered a check-in, but that association is
never recorded on-chain. Flynet only records diner-side effects.

### What IS available

**On-chain (via Blockscout + existing Phase 1 pipeline):**
- Which diner wallets earned FLY and when (mints)
- How much FLY each diner spent (burns)
- Which diners hold BBST status NFTs and what tier
- 310K+ diner wallets, ~58K status NFTs, continuous activity

**Off-chain public (fly.town, no auth required):**
- Homepage RSC embeds today's top-5 restaurants with live check-in counts
  (`rank`, `name`, `slug`, `restaurantUuid`, `checkIns`) — scrapeable without auth
- Full directory of ~2,363 restaurants (`name`, `slug`, `cuisine`) at `/restaurants`
- Historical data and all wallet-level info is behind Blackbird's auth-gated `api.blackbird.xyz`

### Deferred enhancements

The following are explicitly deferred — do not build until the core diner analytics
dashboard is complete and useful on its own:

1. **fly.town daily top-5 scraper**: scrape the homepage RSC payload on a schedule to
   build a historical time-series of the top-5 leaderboard that fly.town itself doesn't
   expose. Low effort to add as a second Lambda once Phase 2+ infrastructure exists.

2. **`api.blackbird.xyz` probe**: a small set of unauthenticated curl requests to check
   whether any public endpoints exist on Blackbird's private API. If accessible, revisit
   restaurant mapping. If 401/403 across the board, close this permanently.

3. **fly.town restaurant directory scrape**: ~2,363 restaurants with name/slug/cuisine
   available at `/restaurants` — useful as enrichment context alongside diner analytics,
   but not load-bearing for any core metric.

---

## Open Questions / Risks
- Blockscout API rate limits unknown at scale — monitor during Phase 1 AWS deployment
- 310K+ diner wallets means `transfers` table will grow large; watch DynamoDB costs
