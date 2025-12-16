import argparse
import json
import os
from pathlib import Path
from typing import Optional, Tuple

import requests


def strip_fences(text: str) -> str:
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```") and s.endswith("```"):
        inner = s.split("\n", 1)[1]
        if inner.endswith("```"):
            inner = inner.rsplit("\n", 1)[0]
        return inner
    return text


def load_recommended(results_root: Path, ticker: str, date: str) -> Tuple[Optional[dict], str]:
    report_path = results_root / ticker / date / "reports" / "recommended_path.md"
    if not report_path.exists():
        return None, f"recommended_path.md not found at {report_path}"
    raw = report_path.read_text(encoding="utf-8").strip()
    unfenced = strip_fences(raw)
    try:
        data = json.loads(unfenced)
        return data, ""
    except Exception as exc:
        return None, f"Failed to parse recommended_path.md as JSON: {exc}"


def decide_action(data: dict) -> str:
    # Prefer summary_decision; fall back to pick or stance keywords
    decision = (
        data.get("summary_decision")
        or data.get("decision")
        or data.get("pick")
        or ""
    ).lower()
    if "buy" in decision:
        return "buy"
    if "sell" in decision:
        return "sell"
    return "hold"


def fetch_last_price(base_url: str, api_key: str, api_secret: str, symbol: str) -> Optional[float]:
    # Lightweight quote fetch; if fails, return None.
    try:
        url = f"{base_url.rstrip('/')}/v2/stocks/{symbol}/quotes/latest"
        resp = requests.get(
            url,
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return float(data.get("quote", {}).get("ap", 0)) or None
    except Exception:
        return None


def build_order_payload(symbol: str, side: str, qty: Optional[int], notional: Optional[float]) -> dict:
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    if qty:
        payload["qty"] = str(qty)
    elif notional:
        payload["notional"] = str(notional)
    else:
        payload["qty"] = "1"
    return payload


def submit_order(base_url: str, api_key: str, api_secret: str, payload: dict) -> dict:
    url = f"{base_url.rstrip('/')}/v2/orders"
    resp = requests.post(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
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
    parser = argparse.ArgumentParser(description="Submit Alpaca order from TradingAgents recommended_path.")
    parser.add_argument("--ticker", required=True, help="Ticker symbol used in TradingAgents run and order symbol.")
    parser.add_argument("--date", required=True, help="Analysis date (YYYY-MM-DD).")
    parser.add_argument("--results-root", default="../TradingAgents/results", help="Root path to TradingAgents/results.")
    parser.add_argument("--qty", type=int, default=None, help="Fixed share quantity to submit.")
    parser.add_argument("--notional", type=float, default=None, help="Notional amount (USD). Used if qty not provided.")
    parser.add_argument("--submit", action="store_true", help="Actually submit order to Alpaca. Without this is dry-run.")
    args = parser.parse_args()

    base_url = os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets")
    api_key = os.environ.get("ALPACA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET")

    if not api_key or not api_secret:
        print("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET in environment. Exiting.")
        return

    results_root = Path(args.results_root).expanduser().resolve()
    rec_data, err = load_recommended(results_root, args.ticker, args.date)
    if rec_data is None:
        print(f"Failed to load recommended path: {err}")
        return

    action = decide_action(rec_data)
    if action == "hold":
        print("Recommended decision is HOLD. No order submitted.")
        return

    qty = args.qty
    notional = args.notional
    if not qty and not notional:
        last = fetch_last_price(base_url, api_key, api_secret, args.ticker)
        if last and notional:
            qty = max(1, int(notional / last))
        if not qty and not notional:
            qty = 1  # fallback

    payload = build_order_payload(args.ticker, action, qty, notional)
    print(f"Prepared payload: {json.dumps(payload, indent=2)}")

    if not args.submit:
        print("Dry-run mode (use --submit to send).")
        return

    resp = submit_order(base_url, api_key, api_secret, payload)
    print(f"Order response: {json.dumps(resp, indent=2)}")


if __name__ == "__main__":
    main()
