# Flynet Restaurant Leaderboard — Project Roadmap

## Project Summary

A web service that ingests on-chain check-in and reward data from Blackbird's
Flynet (an L3 rollup on Base, accessible via the public Blockscout explorer at
explorer.flynet.org), aggregates it into restaurant-level statistics
(check-in volume, velocity/trends, rankings), and presents it through a
leaderboard web UI. The project doubles as hands-on practice with AWS
serverless architecture (Lambda, EventBridge, DynamoDB, S3, API Gateway).

**Data source:** explorer.flynet.org/api/v2 (Blockscout REST API)

Confirmed contracts:
- `FLY` (ERC-20) — `0x6D0FEFe3543212593cee1C8C50EAdf91aCE623b8` — reward/payment token
- `Blackbird Status` (ERC-721) — `0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E` — likely check-in/membership token

---

## Requirements

### Functional
- Periodically ingest transfer events for the BBST and FLY token contracts
- Identify restaurant wallet addresses (vs. diner wallets) from transfer patterns
- Compute per-restaurant metrics: check-in volume (daily/weekly/monthly),
  velocity (rate of change), FLY reward/spend totals
- Generate a ranked leaderboard, sortable by metric and time window
- Serve aggregated data via an API
- Display leaderboard, restaurant detail pages (time-series), and trends in a web UI

### Non-functional
- Cost: stay within AWS free tier as much as possible ($0–2/month target)
- Idempotent ingestion (safe to re-run, handles pagination/checkpointing)
- Incremental: only process new transfers since last run

### Technical Stack
- **Ingestion:** Python (Lambda), Blockscout REST API via `requests`
- **Storage:** S3 (raw API responses), DynamoDB (parsed transfers + aggregates + checkpoint)
- **Scheduling:** EventBridge (cron, every 5 min)
- **Aggregation:** Lambda (batch, scheduled after ingestion)
- **API:** API Gateway + Lambda (FastAPI optional, or plain Lambda handlers)
- **Frontend:** React/Next.js, hosted on S3 + CloudFront (or Vercel for simplicity)
- **Caching:** Redis (optional, later phase) or DynamoDB read patterns

---

## Roadmap

### Phase 0 — Data Investigation (1-2 days)
- [ ] Pull sample transfers from BBST contract via
      `/api/v2/tokens/0x8D8d8CB24aAAEdFe260F7cc5a3Ec7ec91c81e14E/transfers`
- [ ] Confirm check-in hypothesis: examine `from`/`to` address patterns, mint
      frequency, timestamps
- [ ] Pull sample FLY transfers; identify reward distribution vs. spend patterns
- [ ] Determine how to map wallet addresses to restaurant names (check
      Blockscout address name tags, cross-reference fly.town restaurant pages)
- [ ] Document findings (schema notes) before building ingestion

### Phase 1 — Ingestion Foundation (1 week)
- [ ] Set up AWS account/IAM roles for the project
- [ ] Create DynamoDB tables: `transfers`, `checkpoints`, `restaurants`
- [ ] Create S3 bucket for raw API response archive
- [ ] Write ingestion Lambda: paginated pull from Blockscout API using
      `next_page_params`, write raw to S3, parsed to DynamoDB
- [ ] Set up EventBridge schedule (every 5 min)
- [ ] Test end-to-end ingestion with a manual invoke

### Phase 2 — Aggregation (1 week)
- [ ] Write aggregation Lambda: compute daily/weekly check-in counts per
      restaurant address
- [ ] Compute velocity metrics (rate of change vs. prior period)
- [ ] Write leaderboard snapshot to DynamoDB aggregates table
- [ ] Schedule aggregation Lambda to run after ingestion

### Phase 3 — API Layer (1 week)
- [ ] API Gateway + Lambda endpoints:
  - `GET /leaderboard` — ranked restaurants by metric/time window
  - `GET /restaurants/{address}` — restaurant detail + time series
  - `GET /trends` — top movers / trending restaurants
- [ ] Add basic caching (DynamoDB read patterns or Redis if justified)

### Phase 4 — Frontend (1-2 weeks)
- [ ] React/Next.js leaderboard table (sortable, filterable by time window)
- [ ] Restaurant detail page with time-series chart
- [ ] Trending/"top movers" section
- [ ] Deploy to S3 + CloudFront or Vercel

### Phase 5 — Polish & Hardening
- [ ] Restaurant name resolution / mapping improvements
- [ ] Monitoring (CloudWatch alarms on Lambda errors)
- [ ] Cost review — confirm staying within free tier
- [ ] Documentation and README for the repo

---

## Open Questions / Risks
- BBST contract semantics not yet confirmed — may need adjustment once
  Phase 0 investigation completes
- Restaurant name mapping may require manual curation or scraping fly.town
- Blockscout API rate limits unknown at scale — monitor during Phase 1
