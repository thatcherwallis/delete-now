import asyncio
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
from agents import Agent, Runner, WebSearchTool, function_tool
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
MEMORY = ROOT / "agents" / "reporter" / "memory.md"
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")

# Quantity and average cost per share, in USD. All prices and trades are simulated.
portfolio = {
    "AAPL": {"shares": 10, "cost_basis": 180.00},
    "MSFT": {"shares": 5, "cost_basis": 410.00},
    "NVDA": {"shares": 8, "cost_basis": 125.00},
}


class Recommendation(BaseModel):
    symbol: str = Field(description="Exactly one of AAPL, MSFT, NVDA")
    action: str = Field(description="BUY or SELL")
    reference_price: float = Field(gt=0, description="Indicative USD price per share from research")
    rationale: str


@function_tool
def simulate_trade_return() -> float:
    """Generate a uniformly distributed simulated price return from -5% to +20%."""
    return round(random.uniform(-0.05, 0.20), 6)


@function_tool
def calculate_tax(action: str, sale_price: float, cost_basis: float) -> dict:
    """Calculate simplified realized gain and 20% capital-gains tax on one sold share; BUY has zero tax."""
    gain = round(sale_price - cost_basis, 2) if action == "SELL" else 0.0
    return {"gain": gain, "tax": round(max(0.0, gain) * 0.20, 2)}


advisor = Agent(
    name="Financial advisor",
    model=MODEL,
    instructions=(
        "Research recent public information with web search and recommend exactly ONE simulated "
        "BUY or SELL of ONE share of AAPL, MSFT, or NVDA. Give an indicative reference price "
        "in USD and brief rationale. Do not claim the quote is verified or executable. "
        "Only recommend selling a symbol with positive holdings."
    ),
    tools=[WebSearchTool()],
    output_type=Recommendation,
)

trader = Agent(
    name="Trader",
    model=MODEL,
    instructions=(
        "You simulate a trade, not a real market order. ALWAYS call simulate_trade_return exactly "
        "once. Multiply the supplied reference price by (1 + return). Report the simulated "
        "execution price and return as JSON with keys execution_price and return_rate. "
        "No actual broker or best-price search exists in this exercise."
    ),
    tools=[simulate_trade_return],
)

tax = Agent(
    name="Tax advisor",
    model=MODEL,
    instructions=(
        "For stock transactions, ALWAYS call calculate_tax with action, execution sale price, "
        "and previous average per-share cost basis. Explain the returned gain and tax briefly. "
        "The tax assumption is an educational flat 20% on positive realized gains only.\n\n"
        + (ROOT / "agents" / "tax" / "skills" / "govt_bond.md").read_text(encoding="utf-8")
    ),
    tools=[calculate_tax],
)

reporter = Agent(
    name="Reporter",
    model=MODEL,
    instructions=(
        "You are the reporting and question-answering agent for a simulated trading exercise. "
        "Answer only from the supplied portfolio, current cycle results, and memory.md text. "
        "Never invent trades or claim real orders were placed. Clearly distinguish simulations "
        "from actual market prices and actual tax liabilities."
    ),
)


def snapshot():
    return {symbol: dict(values) for symbol, values in portfolio.items()}


def format_portfolio(values):
    return "\n".join(
        f"- {symbol}: {v['shares']} shares; average cost basis ${v['cost_basis']:.2f}/share"
        for symbol, v in values.items()
    )


async def cycle():
    before = snapshot()
    yield "🟡 **Active agent: Financial advisor** — generating recommendations…"
    try:
        suggestion = (await Runner.run(
            advisor,
            f"Current portfolio: {json.dumps(before)}. Recommend one share trade."
        )).final_output
        symbol, action = suggestion.symbol.upper(), suggestion.action.upper()
        if symbol not in portfolio or action not in ("BUY", "SELL"):
            raise ValueError("Advisor returned an unsupported symbol or action")
        if action == "SELL" and portfolio[symbol]["shares"] < 1:
            raise ValueError("Cannot sell a share that is not held")

        yield (f"✅ Financial advisor complete: {action} 1 {symbol} — {suggestion.rationale}\n\n"
               "🟡 **Active agent: Trader** — simulating trade execution…")
        # The trader agent calls the random-return tool. Its narrative is informational;
        # a deterministic local draw supplies the authoritative simulated fill.
        trade_result = await Runner.run(
            trader,
            f"Simulate {action} 1 {symbol} using reference price ${suggestion.reference_price:.2f}."
        )
        # Read the actual function-tool result from the SDK run, not an LLM-generated number.
        tool_outputs = [item.output for item in trade_result.new_items if item.type == "tool_call_output_item"]
        if len(tool_outputs) != 1:
            raise ValueError("Trader must call its return tool exactly once")
        rate = float(tool_outputs[0])
        price = round(suggestion.reference_price * (1 + rate), 2)
        previous_cost = portfolio[symbol]["cost_basis"]
        yield (f"✅ Financial advisor complete: {action} 1 {symbol}\n\n"
               f"✅ Trader complete: simulated {action} 1 {symbol} at ${price:.2f} "
               f"({rate:+.2%} versus indicative quote).\n\n"
               "🟡 **Active agent: Tax advisor** — calculating taxes…")

        tax_result = await Runner.run(
            tax,
            f"Action={action}; execution sale price={price}; prior average per-share cost basis={previous_cost}. "
            "Call calculate_tax."
        )
        tax_outputs = [item.output for item in tax_result.new_items if item.type == "tool_call_output_item"]
        if len(tax_outputs) != 1:
            raise ValueError("Tax agent must call calculator exactly once")
        figures = json.loads(tax_outputs[0]) if isinstance(tax_outputs[0], str) else tax_outputs[0]
        gain, tax_due = float(figures["gain"]), float(figures["tax"])
        # Commit the simulated trade only after all stages succeed.
        if action == "BUY":
            old_shares = portfolio[symbol]["shares"]
            portfolio[symbol]["shares"] += 1
            portfolio[symbol]["cost_basis"] = round((old_shares * previous_cost + price) / (old_shares + 1), 2)
        else:
            portfolio[symbol]["shares"] -= 1
            if portfolio[symbol]["shares"] == 0:
                portfolio[symbol]["cost_basis"] = 0.0

        record = (f"- {datetime.now(timezone.utc).isoformat()} | {action} 1 {symbol} | "
                  f"simulated fill ${price:.2f} | realized gain ${gain:.2f} | simulated tax ${tax_due:.2f}\n")
        with MEMORY.open("a", encoding="utf-8") as file:
            file.write(record)
        after = snapshot()
        report = (f"**Old portfolio**\n{format_portfolio(before)}\n\n"
                  f"**New portfolio**\n{format_portfolio(after)}\n\n"
                  f"**Trade:** {record.strip()}\n\n"
                  "*Educational simulation: no real orders; indicative quotes and simplified taxes.*")
        yield ("✅ Financial advisor complete\n\n✅ Trader complete\n\n"
               f"✅ Tax advisor complete: realized gain ${gain:.2f}; simulated tax ${tax_due:.2f}\n\n"
               "🟡 **Active agent: Reporter** — preparing portfolio report…")
        summary = (await Runner.run(reporter, f"Present this factual report without changing numbers:\n{report}" )).final_output
        yield ("✅ **All agents complete** — Financial advisor → Trader → Tax advisor → Reporter\n\n"
               f"{summary}\n\n{report}")
    except Exception as error:
        yield f"**Cycle stopped without updating holdings:** {error}"


async def respond(message, history):
    if message.strip().lower() in ("start", "run", "trade", "again"):
        async for status in cycle():
            yield status
    else:
        context = (f"Current portfolio:\n{format_portfolio(snapshot())}\n\n"
                   f"Trade history:\n{MEMORY.read_text(encoding='utf-8')}")
        yield "🟡 **Active agent: Reporter** — answering your question…"
        result = await Runner.run(reporter, f"{context}\n\nUser question: {message}")
        yield f"✅ **Reporter complete**\n\n{result.final_output}"


with gr.Blocks(title="Four-agent trading simulation") as demo:
    gr.Markdown("# Multi-agent trading simulation\nAdvisor → Trader → Tax → Reporter report. No real trades.")
    chatbot = gr.Chatbot(height=470)
    entry = gr.Textbox(label="Ask about trades or type start", placeholder="What trades have we made?")
    run_button = gr.Button("Run trading cycle", variant="primary")

    async def submit(message, history):
        history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": ""}]
        async for update in respond(message, history):
            history[-1]["content"] = update
            yield "", history

    entry.submit(submit, [entry, chatbot], [entry, chatbot])
    run_button.click(submit, [gr.State("start"), chatbot], [entry, chatbot])

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)