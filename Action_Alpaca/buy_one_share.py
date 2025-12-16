import os
import sys
import json
import requests


def main():
    api_key = os.environ.get("ALPACA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    base_url = os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not api_secret:
        print("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET in environment.")
        sys.exit(1)

    symbol = "SPY"
    qty = "1"
    payload = {
        "symbol": symbol,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "qty": qty,
    }

    url = f"{base_url.rstrip('/')}/v2/orders"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Content-Type": "application/json",
    }

    print(f"Submitting test buy: {symbol} x {qty} via {base_url}")
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    print(f"Status: {resp.status_code}")
    print(body)


if __name__ == "__main__":
    main()
