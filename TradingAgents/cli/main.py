from typing import Optional
import datetime
import os
import json
import typer
from pathlib import Path
from functools import wraps
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.live import Live
from rich.table import Table
from collections import deque
import requests
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *

console = Console()
SESSION_STATE_FILENAME = "session_state.json"


def get_session_state_path(results_dir: str) -> Path:
    """Resolve a session state path near the results directory."""
    results_root = Path(results_dir).resolve()
    return results_root.parent / SESSION_STATE_FILENAME


def load_session_state(session_path: Path) -> dict:
    """Load cross-ticker session state safely."""
    if not session_path.exists():
        return {"insights": []}
    try:
        with open(session_path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "insights" not in data:
            return {"insights": []}
        return data
    except Exception:
        console.print(
            f"[red]Failed to read {session_path}. Starting fresh session state.[/red]"
        )
        return {"insights": []}


def save_session_state(session_state: dict, session_path: Path) -> None:
    """Persist session state; caller ensures content correctness."""
    session_path.parent.mkdir(parents=True, exist_ok=True)
    with open(session_path, "w") as f:
        json.dump(session_state, f, indent=2)


def _parse_jsonish(content):
    """Try to parse JSON string content; fall back to raw."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:
            return content
    return content


def build_session_entry(final_state: dict) -> dict:
    """Extract a compact, cross-ticker insight entry from the final state."""
    risk_state = final_state.get("risk_debate_state", {}) or {}
    recommended_raw = (
        risk_state.get("recommended_path")
        or final_state.get("recommended_path")
        or final_state.get("final_trade_decision")
    )
    recommended = _parse_jsonish(recommended_raw)

    aggressive_plan = _parse_jsonish(risk_state.get("aggressive_plan"))
    neutral_plan = _parse_jsonish(risk_state.get("neutral_plan"))
    conservative_plan = _parse_jsonish(risk_state.get("conservative_plan"))

    stance = None
    if isinstance(recommended, dict):
        stance = (
            recommended.get("summary_decision")
            or recommended.get("decision")
            or recommended.get("pick")
        )
    if not stance:
        stance = final_state.get("final_trade_decision")

    summary_reason = None
    if isinstance(recommended, dict):
        summary_reason = recommended.get("reason") or recommended.get("rationale")

    return {
        "ticker": final_state.get("company_of_interest"),
        "trade_date": final_state.get("trade_date"),
        "stance": stance,
        "recommended_path": recommended,
        "risk_plans": {
            "aggressive": aggressive_plan,
            "neutral": neutral_plan,
            "conservative": conservative_plan,
        },
        "final_trade_decision": final_state.get("final_trade_decision"),
        "portfolio_state": final_state.get("portfolio_state", {}),
        "investment_plan": final_state.get("investment_plan"),
        "summary_reason": summary_reason,
    }


def append_session_entry(session_state: dict, entry: dict) -> dict:
    """Upsert a ticker/date insight into session state."""
    insights = session_state.get("insights", []) or []
    filtered = [
        i
        for i in insights
        if not (
            i.get("ticker") == entry.get("ticker")
            and i.get("trade_date") == entry.get("trade_date")
        )
    ]
    filtered.append(entry)
    session_state["insights"] = filtered
    return session_state

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {
            # Analyst Team
            "Market Analyst": "pending",
            "Social Analyst": "pending",
            "News Analyst": "pending",
            "Fundamentals Analyst": "pending",
            # Research Team
            "Bull Researcher": "pending",
            "Bear Researcher": "pending",
            "Research Manager": "pending",
            # Trading Team
            "Trader": "pending",
            # Risk Management Team
            "Risky Analyst": "pending",
            "Neutral Analyst": "pending",
            "Safe Analyst": "pending",
            # Portfolio Management Team
            "Portfolio Manager": "pending",
        }
        self.current_agent = None
        self.report_sections = {
            "market_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
            "aggressive_plan": None,
            "neutral_plan": None,
            "conservative_plan": None,
            "recommended_path": None,
        }

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
                "aggressive_plan": "Aggressive Plan",
                "neutral_plan": "Neutral Plan",
                "conservative_plan": "Conservative Plan",
                "recommended_path": "Recommended Path",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports
        if any(
            self.report_sections[section]
            for section in [
                "market_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
            ]
        ):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections["market_report"]:
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections["sentiment_report"]:
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections["news_report"]:
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections["fundamentals_report"]:
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections["investment_plan"]:
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections["trader_investment_plan"]:
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections["final_trade_decision"]:
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def update_display(layout, spinner_text=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team
    teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Risky Analyst", "Neutral Analyst", "Safe Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status[first_agent]
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status[agent]
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        # Truncate tool call args if too long
        if isinstance(args, str) and len(args) > 100:
            args = args[:97] + "..."
        all_messages.append((timestamp, "Tool", f"{tool_name}: {args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        # Convert content to string if it's not already
        content_str = content
        if isinstance(content, list):
            # Handle list of content blocks (Anthropic format)
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get('type') == 'text':
                        text_parts.append(item.get('text', ''))
                    elif item.get('type') == 'tool_use':
                        text_parts.append(f"[Tool: {item.get('name', 'unknown')}]")
                else:
                    text_parts.append(str(item))
            content_str = ' '.join(text_parts)
        elif not isinstance(content_str, str):
            content_str = str(content)
            
        # Truncate message content if too long
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp
    all_messages.sort(key=lambda x: x[0])

    # Calculate how many messages we can show based on available space
    # Start with a reasonable number and adjust based on content length
    max_messages = 12  # Increased from 8 to better fill the space

    # Get the last N messages that will fit in the panel
    recent_messages = all_messages[-max_messages:]

    # Add messages to table
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    if spinner_text:
        messages_table.add_row("", "Spinner", spinner_text)

    # Add a footer to indicate if messages were truncated
    if len(all_messages) > max_messages:
        messages_table.footer = (
            f"[dim]Showing last {max_messages} of {len(all_messages)} messages[/dim]"
        )

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    tool_calls_count = len(message_buffer.tool_calls)
    llm_calls_count = sum(
        1 for _, msg_type, _ in message_buffer.messages if msg_type == "Reasoning"
    )
    reports_count = sum(
        1 for content in message_buffer.report_sections.values() if content is not None
    )

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(
        f"Tool Calls: {tool_calls_count} | LLM Calls: {llm_calls_count} | Generated Reports: {reports_count}"
    )

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open("./cli/static/welcome.txt", "r") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()  # Add a blank line after the welcome box

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter one or more ticker symbols (comma separated)",
            "SPY or SPY,QQQ,AAPL",
        )
    )
    selected_tickers = get_tickers()

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 3: Select analysts
    console.print(
        create_question_box(
            "Step 3: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 4: Research depth
    console.print(
        create_question_box(
            "Step 4: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # Step 5: OpenAI backend
    console.print(
        create_question_box(
            "Step 5: OpenAI backend", "Select which service to talk to"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()
    
    # Step 6: Thinking agents
    console.print(
        create_question_box(
            "Step 6: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 7: Portfolio state (optional)
    console.print(
        create_question_box(
            "Step 7: Portfolio (optional)",
            "Paste JSON for your current portfolio (cash_pct, positions, target_vol, max_drawdown). Leave blank to skip.",
        )
    )
    portfolio_state = get_portfolio_state()

    return {
        "tickers": selected_tickers,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "portfolio_state": portfolio_state,
    }


def get_tickers():
    """Get one or more ticker symbols (comma separated)."""
    raw = typer.prompt("", default="SPY")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return tickers or ["SPY"]


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def fetch_alpaca_portfolio():
    """Fetch portfolio snapshot from Alpaca account."""
    api_key = os.environ.get("ALPACA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET")
    base_url = os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    if not api_key or not api_secret:
        return None
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    try:
        acct = requests.get(f"{base_url}/v2/account", headers=headers, timeout=5)
        pos = requests.get(f"{base_url}/v2/positions", headers=headers, timeout=5)
        if acct.status_code != 200 or pos.status_code != 200:
            return None
        account = acct.json()
        positions = pos.json()
    except Exception:
        return None

    equity = float(account.get("portfolio_value") or 0)
    cash = float(account.get("cash") or 0)
    cash_pct = round(cash / equity, 4) if equity > 0 else 0.0
    positions_fmt = []
    for p in positions:
        positions_fmt.append(
            {
                "symbol": p.get("symbol"),
                "qty": float(p.get("qty") or 0),
                "market_value": float(p.get("market_value") or 0),
            }
        )
    return {
        "cash_pct": cash_pct,
        "positions": positions_fmt,
        "target_vol": 0.0,
        "max_drawdown": 0.0,
    }


def get_portfolio_state():
    """Prompt for optional portfolio JSON, defaulting to Alpaca snapshot if available."""
    auto_portfolio = fetch_alpaca_portfolio()
    default_value = json.dumps(auto_portfolio) if auto_portfolio else ""
    if auto_portfolio:
        console.print(
            "[dim]Detected Alpaca portfolio. Press Enter to accept or edit the JSON below.[/dim]"
        )
    raw = typer.prompt("", default=default_value)
    if not raw.strip():
        return auto_portfolio or {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            console.print("[red]Portfolio input must be a JSON object. Ignoring.[/red]")
            return auto_portfolio or {}
        return data
    except json.JSONDecodeError:
        console.print("[red]Invalid JSON. Ignoring portfolio input.[/red]")
        return auto_portfolio or {}


def display_complete_report(final_state):
    """Display the complete analysis report with team-based panels."""
    console.print("\n[bold green]Complete Analysis Report[/bold green]\n")

    # I. Analyst Team Reports
    analyst_reports = []

    # Market Analyst Report
    if final_state.get("market_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["market_report"]),
                title="Market Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Social Analyst Report
    if final_state.get("sentiment_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["sentiment_report"]),
                title="Social Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # News Analyst Report
    if final_state.get("news_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["news_report"]),
                title="News Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Fundamentals Analyst Report
    if final_state.get("fundamentals_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["fundamentals_report"]),
                title="Fundamentals Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    if analyst_reports:
        console.print(
            Panel(
                Columns(analyst_reports, equal=True, expand=True),
                title="I. Analyst Team Reports",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        research_reports = []
        debate_state = final_state["investment_debate_state"]

        # Bull Researcher Analysis
        if debate_state.get("bull_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bull_history"]),
                    title="Bull Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Bear Researcher Analysis
        if debate_state.get("bear_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bear_history"]),
                    title="Bear Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Research Manager Decision
        if debate_state.get("judge_decision"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["judge_decision"]),
                    title="Research Manager",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if research_reports:
            console.print(
                Panel(
                    Columns(research_reports, equal=True, expand=True),
                    title="II. Research Team Decision",
                    border_style="magenta",
                    padding=(1, 2),
                )
            )

    # III. Trading Team Reports
    if final_state.get("trader_investment_plan"):
        console.print(
            Panel(
                Panel(
                    Markdown(final_state["trader_investment_plan"]),
                    title="Trader",
                    border_style="blue",
                    padding=(1, 2),
                ),
                title="III. Trading Team Plan",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    # IV. Risk Management Team Reports
    if final_state.get("risk_debate_state"):
        risk_reports = []
        risk_state = final_state["risk_debate_state"]

        # Aggressive (Risky) Analyst Analysis
        if risk_state.get("risky_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["risky_history"]),
                    title="Aggressive Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Conservative (Safe) Analyst Analysis
        if risk_state.get("safe_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["safe_history"]),
                    title="Conservative Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Neutral Analyst Analysis
        if risk_state.get("neutral_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["neutral_history"]),
                    title="Neutral Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if risk_reports:
            console.print(
                Panel(
                    Columns(risk_reports, equal=True, expand=True),
                    title="IV. Risk Management Team Decision",
                    border_style="red",
                    padding=(1, 2),
                )
            )

        # V. Portfolio Manager Decision
        plan_panels = []
        if risk_state.get("aggressive_plan"):
            plan_panels.append(
                Panel(
                    Markdown(risk_state["aggressive_plan"]),
                    title="Aggressive Plan",
                    border_style="blue",
                    padding=(1, 2),
                )
            )
        if risk_state.get("neutral_plan"):
            plan_panels.append(
                Panel(
                    Markdown(risk_state["neutral_plan"]),
                    title="Neutral Plan",
                    border_style="blue",
                    padding=(1, 2),
                )
            )
        if risk_state.get("conservative_plan"):
            plan_panels.append(
                Panel(
                    Markdown(risk_state["conservative_plan"]),
                    title="Conservative Plan",
                    border_style="blue",
                    padding=(1, 2),
                )
            )
        if plan_panels:
            console.print(
                Panel(
                    Columns(plan_panels, equal=True, expand=True),
                    title="V. Risk Management Plans",
                    border_style="green",
                    padding=(1, 2),
                )
            )

        if risk_state.get("judge_decision") or risk_state.get("recommended_path"):
            console.print(
                Panel(
                    Columns(
                        [
                            Panel(
                                Markdown(
                                    risk_state.get("recommended_path", "")
                                    or risk_state.get("final_trade_decision", "")
                                ),
                                title="Recommended Path",
                                border_style="blue",
                                padding=(1, 2),
                            ),
                            Panel(
                                Markdown(risk_state.get("judge_decision", "")),
                                title="Portfolio Manager",
                                border_style="blue",
                                padding=(1, 2),
                            ),
                        ],
                        equal=True,
                        expand=True,
                    ),
                    title="VI. Portfolio Manager Decision",
                    border_style="green",
                    padding=(1, 2),
                )
            )


def update_research_team_status(status):
    """Update status for all research team members and trader."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager", "Trader"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)

def extract_content_string(content):
    """Extract string content from various message formats."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # Handle Anthropic's list format
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
                elif item.get('type') == 'tool_use':
                    text_parts.append(f"[Tool: {item.get('name', 'unknown')}]")
            else:
                text_parts.append(str(item))
        return ' '.join(text_parts)
    else:
        return str(content)

def _ensure_logging_wrappers():
    """Wrap message buffer methods once, using dynamic paths set per ticker."""
    if getattr(message_buffer, "_logging_wrapped", False):
        return

    base_add_message = message_buffer.add_message
    base_add_tool_call = message_buffer.add_tool_call
    base_update_report_section = message_buffer.update_report_section

    @wraps(base_add_message)
    def add_message_wrapper(*args, **kwargs):
        base_add_message(*args, **kwargs)
        log_path = getattr(message_buffer, "log_file_path", None)
        if log_path and message_buffer.messages:
            timestamp, message_type, content = message_buffer.messages[-1]
            content = content.replace("\n", " ")
            with open(log_path, "a") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")

    @wraps(base_add_tool_call)
    def add_tool_call_wrapper(*args, **kwargs):
        base_add_tool_call(*args, **kwargs)
        log_path = getattr(message_buffer, "log_file_path", None)
        if log_path and message_buffer.tool_calls:
            timestamp, tool_name, call_args = message_buffer.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in call_args.items())
            with open(log_path, "a") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")

    @wraps(base_update_report_section)
    def update_report_section_wrapper(section_name, content):
        base_update_report_section(section_name, content)
        report_dir = getattr(message_buffer, "report_dir_path", None)
        if (
            report_dir
            and section_name in message_buffer.report_sections
            and message_buffer.report_sections[section_name] is not None
        ):
            section_content = message_buffer.report_sections[section_name]
            if section_content:
                file_name = f"{section_name}.md"
                with open(report_dir / file_name, "w") as f:
                    f.write(section_content)

    message_buffer.add_message = add_message_wrapper
    message_buffer.add_tool_call = add_tool_call_wrapper
    message_buffer.update_report_section = update_report_section_wrapper
    message_buffer._logging_wrapped = True


def reset_message_buffer():
    """Reset buffer state before each ticker run."""
    message_buffer.messages.clear()
    message_buffer.tool_calls.clear()
    message_buffer.current_report = None
    message_buffer.final_report = None
    for agent in message_buffer.agent_status:
        message_buffer.agent_status[agent] = "pending"
    for section in message_buffer.report_sections:
        message_buffer.report_sections[section] = None


def run_single_ticker(
    ticker, selections, base_config, graph, layout, session_state, session_state_path
):
    reset_message_buffer()

    config = base_config.copy()
    results_dir = Path(config["results_dir"]) / ticker / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    message_buffer.log_file_path = log_file
    message_buffer.report_dir_path = report_dir

    update_display(layout)

    message_buffer.add_message("System", f"Selected ticker: {ticker}")
    message_buffer.add_message(
        "System", f"Analysis date: {selections['analysis_date']}"
    )
    message_buffer.add_message(
        "System",
        f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
    )
    update_display(layout)

    first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
    message_buffer.update_agent_status(first_analyst, "in_progress")
    update_display(layout)

    spinner_text = f"Analyzing {ticker} on {selections['analysis_date']}..."
    update_display(layout, spinner_text)

    init_agent_state = graph.propagator.create_initial_state(
        ticker, selections["analysis_date"], prior_insights=session_state.get("insights", [])
    )
    init_agent_state["portfolio_state"] = selections.get("portfolio_state", {}) or {}
    args = graph.propagator.get_graph_args()

    trace = []
    for chunk in graph.graph.stream(init_agent_state, **args):
        if len(chunk["messages"]) > 0:
            last_message = chunk["messages"][-1]

            if hasattr(last_message, "content"):
                content = extract_content_string(last_message.content)
                msg_type = "Reasoning"
            else:
                content = str(last_message)
                msg_type = "System"

            message_buffer.add_message(msg_type, content)

            if hasattr(last_message, "tool_calls"):
                for tool_call in last_message.tool_calls:
                    if isinstance(tool_call, dict):
                        message_buffer.add_tool_call(
                            tool_call["name"], tool_call["args"]
                        )
                    else:
                        message_buffer.add_tool_call(tool_call.name, tool_call.args)

            if "market_report" in chunk and chunk["market_report"]:
                message_buffer.update_report_section(
                    "market_report", chunk["market_report"]
                )
                message_buffer.update_agent_status("Market Analyst", "completed")
                if "social" in selections["analysts"]:
                    message_buffer.update_agent_status(
                        "Social Analyst", "in_progress"
                    )

            if "sentiment_report" in chunk and chunk["sentiment_report"]:
                message_buffer.update_report_section(
                    "sentiment_report", chunk["sentiment_report"]
                )
                message_buffer.update_agent_status("Social Analyst", "completed")
                if "news" in selections["analysts"]:
                    message_buffer.update_agent_status(
                        "News Analyst", "in_progress"
                    )

            if "news_report" in chunk and chunk["news_report"]:
                message_buffer.update_report_section(
                    "news_report", chunk["news_report"]
                )
                message_buffer.update_agent_status("News Analyst", "completed")
                if "fundamentals" in selections["analysts"]:
                    message_buffer.update_agent_status(
                        "Fundamentals Analyst", "in_progress"
                    )

            if "fundamentals_report" in chunk and chunk["fundamentals_report"]:
                message_buffer.update_report_section(
                    "fundamentals_report", chunk["fundamentals_report"]
                )
                message_buffer.update_agent_status("Fundamentals Analyst", "completed")
                update_research_team_status("in_progress")

            if "investment_debate_state" in chunk and chunk["investment_debate_state"]:
                debate_state = chunk["investment_debate_state"]

                if "bull_history" in debate_state and debate_state["bull_history"]:
                    update_research_team_status("in_progress")
                    bull_responses = debate_state["bull_history"].split("\n")
                    latest_bull = bull_responses[-1] if bull_responses else ""
                    if latest_bull:
                        message_buffer.add_message("Reasoning", latest_bull)
                        message_buffer.update_report_section(
                            "investment_plan",
                            f"### Bull Researcher Analysis\n{latest_bull}",
                        )

                if "bear_history" in debate_state and debate_state["bear_history"]:
                    update_research_team_status("in_progress")
                    bear_responses = debate_state["bear_history"].split("\n")
                    latest_bear = bear_responses[-1] if bear_responses else ""
                    if latest_bear:
                        message_buffer.add_message("Reasoning", latest_bear)
                        message_buffer.update_report_section(
                            "investment_plan",
                            f"{message_buffer.report_sections['investment_plan']}\n\n### Bear Researcher Analysis\n{latest_bear}",
                        )

                if "judge_decision" in debate_state and debate_state["judge_decision"]:
                    update_research_team_status("in_progress")
                    message_buffer.add_message(
                        "Reasoning",
                        f"Research Manager: {debate_state['judge_decision']}",
                    )
                    message_buffer.update_report_section(
                        "investment_plan",
                        f"{message_buffer.report_sections['investment_plan']}\n\n### Research Manager Decision\n{debate_state['judge_decision']}",
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Risky Analyst", "in_progress")

            if "trader_investment_plan" in chunk and chunk["trader_investment_plan"]:
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                message_buffer.update_agent_status("Risky Analyst", "in_progress")

            if "risk_debate_state" in chunk and chunk["risk_debate_state"]:
                risk_state = chunk["risk_debate_state"]

                if (
                    "current_risky_response" in risk_state
                    and risk_state["current_risky_response"]
                ):
                    message_buffer.update_agent_status("Risky Analyst", "in_progress")
                    message_buffer.add_message(
                        "Reasoning",
                        f"Risky Analyst: {risk_state['current_risky_response']}",
                    )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Risky Analyst Analysis\n{risk_state['current_risky_response']}",
                    )

                if (
                    "current_safe_response" in risk_state
                    and risk_state["current_safe_response"]
                ):
                    message_buffer.update_agent_status("Safe Analyst", "in_progress")
                    message_buffer.add_message(
                        "Reasoning",
                        f"Safe Analyst: {risk_state['current_safe_response']}",
                    )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Safe Analyst Analysis\n{risk_state['current_safe_response']}",
                    )

                if (
                    "current_neutral_response" in risk_state
                    and risk_state["current_neutral_response"]
                ):
                    message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.add_message(
                        "Reasoning",
                        f"Neutral Analyst: {risk_state['current_neutral_response']}",
                    )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Neutral Analyst Analysis\n{risk_state['current_neutral_response']}",
                    )

                if "judge_decision" in risk_state and risk_state["judge_decision"]:
                    message_buffer.update_agent_status(
                        "Portfolio Manager", "in_progress"
                    )
                    message_buffer.add_message(
                        "Reasoning",
                        f"Portfolio Manager: {risk_state['judge_decision']}",
                    )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Portfolio Manager Decision\n{risk_state['judge_decision']}",
                    )
                    message_buffer.update_agent_status("Risky Analyst", "completed")
                    message_buffer.update_agent_status("Safe Analyst", "completed")
                    message_buffer.update_agent_status("Neutral Analyst", "completed")
                    message_buffer.update_agent_status("Portfolio Manager", "completed")

                if risk_state.get("aggressive_plan"):
                    message_buffer.update_report_section(
                        "aggressive_plan", risk_state["aggressive_plan"]
                    )
                if risk_state.get("neutral_plan"):
                    message_buffer.update_report_section(
                        "neutral_plan", risk_state["neutral_plan"]
                    )
                if risk_state.get("conservative_plan"):
                    message_buffer.update_report_section(
                        "conservative_plan", risk_state["conservative_plan"]
                    )
                if risk_state.get("recommended_path"):
                    message_buffer.update_report_section(
                        "recommended_path", risk_state["recommended_path"]
                    )

        trace.append(chunk)

    final_state = trace[-1]
    graph.process_signal(final_state["final_trade_decision"])

    for agent in message_buffer.agent_status:
        message_buffer.update_agent_status(agent, "completed")

    message_buffer.add_message(
        "Analysis", f"Completed analysis for {selections['analysis_date']}"
    )

    for section in message_buffer.report_sections.keys():
        if section in final_state:
            message_buffer.update_report_section(section, final_state[section])

    try:
        entry = build_session_entry(final_state)
        session_state = append_session_entry(session_state, entry)
        save_session_state(session_state, session_state_path)
        message_buffer.add_message(
            "System", f"Session state updated → {session_state_path}"
        )
    except Exception as exc:
        message_buffer.add_message(
            "System",
            f"Failed to update session state ({exc}). Continue without persistence.",
        )

    display_complete_report(final_state)
    update_display(layout)
    return session_state


def run_analysis():
    selections = get_user_selections()

    base_config = DEFAULT_CONFIG.copy()
    base_config["max_debate_rounds"] = selections["research_depth"]
    base_config["max_risk_discuss_rounds"] = selections["research_depth"]
    base_config["quick_think_llm"] = selections["shallow_thinker"]
    base_config["deep_think_llm"] = selections["deep_thinker"]
    base_config["backend_url"] = selections["backend_url"]
    base_config["llm_provider"] = selections["llm_provider"].lower()

    graph = TradingAgentsGraph(
        [analyst.value for analyst in selections["analysts"]],
        config=base_config,
        debug=True,
    )

    _ensure_logging_wrappers()

    layout = create_layout()

    session_state_path = get_session_state_path(base_config["results_dir"])
    session_state = load_session_state(session_state_path)

    with Live(layout, refresh_per_second=4) as live:
        for ticker in selections["tickers"]:
            session_state = run_single_ticker(
                ticker,
                selections,
                base_config,
                graph,
                layout,
                session_state,
                session_state_path,
            )

@app.command()
def analyze():
    run_analysis()


if __name__ == "__main__":
    app()
