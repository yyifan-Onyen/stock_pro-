import json
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, unquote

BASE_DIR = Path(__file__).resolve().parent
TA_DIR = BASE_DIR.parent / "TradingAgents"
RESULTS_DIR = TA_DIR / "results"
LOGS_ROOT = TA_DIR / "eval_results"


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as exc:
        return f"Error reading {path.name}: {exc}"


def list_runs():
    runs = []
    if not RESULTS_DIR.exists():
        return runs
    for ticker_dir in RESULTS_DIR.iterdir():
        if ticker_dir.is_dir():
            dates = [d.name for d in ticker_dir.iterdir() if d.is_dir()]
            runs.append({"ticker": ticker_dir.name, "dates": sorted(dates)})
    return sorted(runs, key=lambda r: r["ticker"])


def load_reports(ticker: str, date: str):
    report_dir = RESULTS_DIR / ticker / date / "reports"
    report_keys = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
        "aggressive_plan",
        "neutral_plan",
        "conservative_plan",
        "recommended_path",
    ]
    reports = {}
    for key in report_keys:
        path = report_dir / f"{key}.md"
        reports[key] = read_text_file(path) if path.exists() else ""
    return reports


def load_full_state(ticker: str, date: str):
    log_path = LOGS_ROOT / ticker / "TradingAgentsStrategy_logs" / f"full_states_log_{date}.json"
    if not log_path.exists():
        return {}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(str(date), data)
    except Exception:
        return {}


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/runs":
            return self.handle_runs()
        if path.startswith("/api/run/"):
            return self.handle_run(path)
        return super().do_GET()

    def handle_runs(self):
        payload = {"runs": list_runs()}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_run(self, path: str):
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3:
            self.send_error(400, "Usage: /api/run/<ticker>/<date>")
            return
        ticker = unquote(parts[2])
        date = unquote(parts[3]) if len(parts) > 3 else ""
        reports = load_reports(ticker, date)
        state = load_full_state(ticker, date)
        if not any(reports.values()) and not state:
            self.send_error(404, "Run not found")
            return
        payload = {
            "ticker": ticker,
            "date": date,
            "reports": reports,
            "state": state,
            "base_path": str(RESULTS_DIR),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run(host="127.0.0.1", port=8008):
    handler = lambda *args, **kwargs: DashboardHandler(  # noqa: E731
        *args, directory=str(BASE_DIR), **kwargs
    )
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"Tianming's Lab dashboard running at http://{host}:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    run()
