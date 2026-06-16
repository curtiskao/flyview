"""
Pure aggregation logic — no storage dependencies.

Operates on lists of transfer dicts as produced by ingestion/blockscout.parse_transfer.
FLY amounts are stored as raw wei strings (18 decimals); divide by 10**18 for display.
"""

FLY_CONTRACT = "0x6d0fefe3543212593cee1c8c50eadf91ace623b8"
BBST_CONTRACT = "0x8d8d8cb24aaaedfe260f7cc5a3ec7ec91c81e14e"


def compute_diner_stats(transfers: list[dict]) -> dict[str, dict]:
    """
    Aggregate per-diner metrics from all transfers.

    Returns a dict keyed by lowercase wallet address. FLY amounts are raw wei
    stored as strings to preserve precision across JSON serialization.
    """
    diners: dict[str, dict] = {}

    def _wallet(address: str) -> dict:
        if address not in diners:
            diners[address] = {
                "wallet_address": address,
                "fly_earned": 0,
                "fly_burned": 0,
                "mint_count": 0,
                "burn_count": 0,
                "has_bbst": False,
                "bbst_token_id": None,
                "first_seen": None,
                "last_seen": None,
            }
        return diners[address]

    def _touch_ts(d: dict, ts: str) -> None:
        if d["first_seen"] is None or ts < d["first_seen"]:
            d["first_seen"] = ts
        if d["last_seen"] is None or ts > d["last_seen"]:
            d["last_seen"] = ts

    for t in transfers:
        contract = t["contract_address"]
        ts = t["timestamp"]

        if contract == FLY_CONTRACT:
            if t["method"] == "mint":
                d = _wallet(t["to_address"])
                d["fly_earned"] += int(t["value"])
                d["mint_count"] += 1
                _touch_ts(d, ts)
            elif t["method"] == "burn":
                d = _wallet(t["from_address"])
                d["fly_burned"] += int(t["value"])
                d["burn_count"] += 1
                _touch_ts(d, ts)

        elif contract == BBST_CONTRACT:
            d = _wallet(t["to_address"])
            d["has_bbst"] = True
            d["bbst_token_id"] = t.get("token_id")
            _touch_ts(d, ts)

    for d in diners.values():
        earned = d["fly_earned"]
        burned = d["fly_burned"]
        d["fly_net"] = earned - burned
        # Store large ints as strings to avoid JS/JSON precision loss
        d["fly_earned"] = str(earned)
        d["fly_burned"] = str(burned)
        d["fly_net"] = str(d["fly_net"])

    return diners


def compute_daily_stats(
    transfers: list[dict],
    diner_first_seen: dict[str, str],
) -> dict[str, dict]:
    """
    Aggregate per-day network metrics from FLY transfers.

    diner_first_seen: {wallet_address: "YYYY-MM-DD"} — the date a wallet first
    appeared in any FLY transfer. Used to count new wallets per day.

    Returns a dict keyed by date string ("YYYY-MM-DD").
    """
    daily: dict[str, dict] = {}

    def _day(date: str) -> dict:
        if date not in daily:
            daily[date] = {
                "date": date,
                "fly_minted": 0,
                "fly_burned": 0,
                "tx_count": 0,
                "_active": set(),
            }
        return daily[date]

    for t in transfers:
        if t["contract_address"] != FLY_CONTRACT:
            continue
        date = t["timestamp"][:10]
        day = _day(date)
        day["tx_count"] += 1

        if t["method"] == "mint":
            day["fly_minted"] += int(t["value"])
            day["_active"].add(t["to_address"])
        elif t["method"] == "burn":
            day["fly_burned"] += int(t["value"])
            day["_active"].add(t["from_address"])

    for date, day in daily.items():
        active = day.pop("_active")
        minted = day["fly_minted"]
        burned = day["fly_burned"]
        day["active_wallets"] = len(active)
        day["new_wallets"] = sum(
            1 for w in active if diner_first_seen.get(w) == date
        )
        day["burn_rate"] = round(burned / minted, 6) if minted > 0 else 0.0
        day["fly_minted"] = str(minted)
        day["fly_burned"] = str(burned)

    return daily


def compute_top_earners(diner_stats: dict[str, dict], n: int = 100) -> list[dict]:
    """Return top N wallets by FLY earned as a ranked list."""
    ranked = sorted(
        diner_stats.values(), key=lambda d: int(d["fly_earned"]), reverse=True
    )[:n]
    return [{"rank": i + 1, **d} for i, d in enumerate(ranked)]


def compute_top_spenders(diner_stats: dict[str, dict], n: int = 100) -> list[dict]:
    """Return top N wallets by FLY burned as a ranked list."""
    ranked = sorted(
        diner_stats.values(), key=lambda d: int(d["fly_burned"]), reverse=True
    )[:n]
    return [{"rank": i + 1, **d} for i, d in enumerate(ranked)]
