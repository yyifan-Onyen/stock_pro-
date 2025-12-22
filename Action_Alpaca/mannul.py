import json
import os
import sys
from typing import Dict, List

import requests


def require_env() -> Dict[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    base_url = os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets")
    if not api_key or not api_secret:
        print("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET in environment.")
        sys.exit(1)
    return {"api_key": api_key, "api_secret": api_secret, "base_url": base_url.rstrip("/")}


def fetch_positions(env: Dict[str, str]) -> List[dict]:
    url = f"{env['base_url']}/v2/positions"
    resp = requests.get(
        url,
        headers={
            "APCA-API-KEY-ID": env["api_key"],
            "APCA-API-SECRET-KEY": env["api_secret"],
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch positions: {resp.status_code} {resp.text}")
    return resp.json()


def fetch_open_orders(env: Dict[str, str]) -> List[dict]:
    url = f"{env['base_url']}/v2/orders"
    resp = requests.get(
        url,
        headers={
            "APCA-API-KEY-ID": env["api_key"],
            "APCA-API-SECRET-KEY": env["api_secret"],
        },
        params={"status": "open"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch open orders: {resp.status_code} {resp.text}")
    return resp.json()


def print_positions(positions: List[dict]) -> None:
    if not positions:
        print("No open positions.")
        return
    print("Current positions:")
    for p in positions:
        symbol = p.get("symbol")
        qty = p.get("qty")
        market_value = p.get("market_value")
        unrealized_pl = p.get("unrealized_pl")
        print(f"- {symbol}: {qty} shares, value ${market_value}, P/L ${unrealized_pl}")


def print_open_orders(orders: List[dict]) -> None:
    if not orders:
        print("No open orders.")
        return
    print("Open orders:")
    for o in orders:
        symbol = o.get("symbol")
        side = o.get("side")
        qty = o.get("qty")
        status = o.get("status")
        order_id = o.get("id")
        print(f"- {symbol} {side} {qty} (status: {status}, id: {order_id})")


def submit_order(env: Dict[str, str], symbol: str, side: str, qty: int) -> dict:
    url = f"{env['base_url']}/v2/orders"
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "qty": str(qty),
    }
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


def prompt_action() -> str:
    while True:
        action = input("What do you want to do? (buy/sell/exit): ").strip().lower()
        if action in {"buy", "sell", "exit"}:
            return action
        print("Please enter 'buy', 'sell', or 'exit'.")


def prompt_symbol() -> str:
    while True:
        symbol = input("Symbol (e.g., SPY): ").strip().upper()
        if symbol:
            return symbol
        print("Symbol cannot be empty.")


def prompt_qty() -> int:
    while True:
        raw = input("Quantity (integer > 0): ").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("Please enter a positive integer.")


def main():
    env = require_env()

    try:
        positions = fetch_positions(env)
        open_orders = fetch_open_orders(env)
    except Exception as exc:
        print(exc)
        return

    print_positions(positions)
    print_open_orders(open_orders)

    action = prompt_action()
    if action == "exit":
        print("No action taken.")
        return

    symbol = prompt_symbol()
    qty = prompt_qty()

    print(f"Submitting {action} order: {symbol} x {qty} via {env['base_url']}")
    resp = submit_order(env, symbol, action, qty)
    print(f"Status: {resp['status_code']}")
    print(resp["body"])


if __name__ == "__main__":
    main()
