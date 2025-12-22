import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import requests


def require_env() -> Dict[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    base_url = os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets")
    if not api_key or not api_secret:
        print("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET in environment.")
        sys.exit(1)
    return {"api_key": api_key, "api_secret": api_secret, "base_url": base_url.rstrip("/")}


def load_tickers(path: Path, limit: int) -> List[Tuple[str, float]]:
    df = pd.read_excel(path)
    # Expect columns "Ticker" and "% of Net Assets"
    tickers = []
    for _, row in df.head(limit).iterrows():
        ticker = str(row.get("Ticker", "")).strip().upper()
        if not ticker:
            continue
        weight = row.get("% of Net Assets", 0)
        try:
            weight = float(weight) / 100 if weight else 0.0
        except Exception:
            weight = 0.0
        tickers.append((ticker, weight))
    return tickers


def fetch_last_price(env: Dict[str, str], symbol: str) -> float:
    url = f"{env['base_url']}/v2/stocks/{symbol}/quotes/latest"
    resp = requests.get(
        url,
        headers={
            "APCA-API-KEY-ID": env["api_key"],
            "APCA-API-SECRET-KEY": env["api_secret"],
        },
        timeout=5,
    )
    if resp.status_code != 200:
        return 0.0
    try:
        data = resp.json()
        return float(data.get("quote", {}).get("ap") or 0)
    except Exception:
        return 0.0


def build_order(ticker: str, qty: int) -> dict:
    return {
        "symbol": ticker,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "qty": str(qty),
    }


def submit_order(env: Dict[str, str], payload: dict) -> dict:
    url = f"{env['base_url']}/v2/orders"
    resp = requests.post(
        url,
        headers={
            "APCA-API-KEY-ID": env["api_key"],
            "APCA-API-SECRET-KEY": env["api_secret"],
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=10,
    )
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "body": body}


def main():
    parser = argparse.ArgumentParser(description="Buy top holdings from the Excel list.")
    parser.add_argument("--file", default="../jpm_lcg_holdings_2025-05-31.xlsx", help="Path to holdings Excel.")
    parser.add_argument("--limit", type=int, default=30, help="Number of tickers to buy from the top.")
    parser.add_argument("--qty", type=int, default=1, help="Quantity per ticker if --budget not provided.")
    parser.add_argument("--budget", type=float, default=None, help="Total USD to allocate across tickers by weight.")
    parser.add_argument("--submit", action="store_true", help="Actually place orders. Otherwise dry-run.")
    args = parser.parse_args()

    env = require_env()
    tickers_with_weight = load_tickers(Path(args.file), args.limit)

    if not tickers_with_weight:
        print("No tickers found.")
        return

    tickers = [t for t, _ in tickers_with_weight]
    print(f"Loaded {len(tickers)} tickers (top {args.limit}): {', '.join(tickers)}")

    payloads = []

    if args.budget:
        print(f"Allocating total budget ${args.budget} by weight (fallback to equal if weight missing).")
        total_weight = sum(w for _, w in tickers_with_weight) or len(tickers_with_weight)
        for ticker, weight in tickers_with_weight:
            target_weight = weight if weight > 0 else 1 / total_weight
            notional = args.budget * target_weight
            price = fetch_last_price(env, ticker)
            qty = max(1, int(notional / price)) if price > 0 else 1
            payloads.append(build_order(ticker, qty))
    else:
        print(f"Qty per ticker: {args.qty}")
        payloads = [build_order(t, args.qty) for t in tickers]


    if not args.submit:
        print("Dry-run. Payloads:")
        for p in payloads:
            print(json.dumps(p))
        print("Use --submit to send orders.")
        return

    print("Submitting orders...")
    for payload in payloads:
        resp = submit_order(env, payload)
        ticker = payload["symbol"]
        print(f"{ticker}: status {resp['status_code']} -> {resp['body']}")


if __name__ == "__main__":
    main()
