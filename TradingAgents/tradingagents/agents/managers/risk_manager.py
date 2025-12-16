import time
import json


def create_risk_manager(llm, memory):
    def risk_manager_node(state) -> dict:

        company_name = state["company_of_interest"]

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        sentiment_report = state["sentiment_report"]
        trader_plan = state["investment_plan"]
        portfolio_state = state.get("portfolio_state", {}) or {}

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        portfolio_context = json.dumps(portfolio_state, indent=2) if portfolio_state else "Not provided"

        prompt = f"""As the Risk Management Judge and Debate Facilitator, you must produce three risk-adjusted plans (Aggressive, Neutral, Conservative) plus a recommended path. You are deciding on how to adapt the trader's plan **{trader_plan}** using arguments from the debate, lessons from past mistakes **{past_memory_str}**, and the current portfolio context **{portfolio_context}**. Hold is allowed only with strong justification.

Return **strict JSON only** with this shape:
{{
  "aggressive_plan": {{
    "stance": "Buy|Sell|Hold",
    "rationale": "why this stance given upside/downside and debate quotes",
    "actions": "entry/scale/size/targets/stop/hedges; be concise bullets in one string",
    "risk_controls": "max loss, drawdown guardrails, position limits, time stops",
    "timeframe": "intended holding/reeval cadence"
  }},
  "neutral_plan": {{... same fields ...}},
  "conservative_plan": {{... same fields ...}},
  "recommended_path": {{
    "pick": "aggressive|neutral|conservative",
    "summary_decision": "Buy|Sell|Hold",
    "reason": "why this pick fits current context"
  }}
}}

Use the debate below to anchor citations; do not invent history.
Debate history:
{history}
"""

        response = llm.invoke(prompt)

        aggressive_plan = ""
        neutral_plan = ""
        conservative_plan = ""
        recommended_path = ""
        final_decision = response.content

        try:
            parsed = json.loads(response.content)
            aggressive_plan = json.dumps(parsed.get("aggressive_plan", {}), indent=2)
            neutral_plan = json.dumps(parsed.get("neutral_plan", {}), indent=2)
            conservative_plan = json.dumps(parsed.get("conservative_plan", {}), indent=2)
            recommended_path = json.dumps(parsed.get("recommended_path", {}), indent=2)
            if parsed.get("recommended_path", {}).get("summary_decision"):
                final_decision = parsed["recommended_path"]["summary_decision"]
        except Exception:
            # Fall back to raw content if parsing fails
            aggressive_plan = response.content
            neutral_plan = response.content
            conservative_plan = response.content
            recommended_path = response.content

        new_risk_debate_state = {
            "judge_decision": response.content,
            "history": risk_debate_state["history"],
            "risky_history": risk_debate_state["risky_history"],
            "safe_history": risk_debate_state["safe_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_risky_response": risk_debate_state["current_risky_response"],
            "current_safe_response": risk_debate_state["current_safe_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "aggressive_plan": aggressive_plan,
            "neutral_plan": neutral_plan,
            "conservative_plan": conservative_plan,
            "recommended_path": recommended_path,
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_decision,
        }

    return risk_manager_node
