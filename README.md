# Multiagent trading classroom demo

Four OpenAI Agents SDK agents in **one Codespace**: reporter, financial advisor, trader, tax. This is a **simulation**, not a brokerage integration or investment/tax advice.

## Run
1. Upload the files (including `.devcontainer`) to a GitHub repository.
2. Create a Codespace. Add `OPENAI_API_KEY` as a Codespaces secret (repository or user scope), then rebuild/restart if needed.
3. Open forwarded port **7860**. Gradio starts automatically; alternatively run `python app.py`.
4. Type **start** or press **Run trading cycle**. Ask questions about previous trades in chat.

## Design
- `Agent(...)` configures each logical agent. `Runner.run(...)` makes model calls; no separate threads, processes, or servers per agent.
- Python executes a **fixed advisor → trader → tax** sequence to ensure mandatory stages cannot be skipped. Reporter reports results and answers trade-history questions. This is a hybrid orchestration demonstration, not free-form LLM-selected handoffs.
- Advisor uses OpenAI hosted `WebSearchTool` and proposes **one share** BUY or SELL of AAPL, MSFT, or NVDA. The advisor supplies a reference quote; it is **not** a verified exchange quote.
- Trader calls a Python random-return tool (uniform -5% to +20%) to simulate execution relative to that quote. It cannot short-sell or buy unsupported symbols.
- Tax agent calls a Python calculator for the **simplified 20% positive realized gain on sells**; buys update weighted-average cost basis. This is not a complete U.S. tax-lot or tax-code implementation.
- The stock portfolio is held in memory and resets when the server restarts. `agents/reporter/memory.md` retains trade records across restarts and is read for questions; it does **not** automatically reconstruct the portfolio. One Gradio server instance is assumed.
- `agents/tax/skills/govt_bond.md` is loaded into the tax agent instructions. The stock-only demo does not execute bond trades.
- No actual money, order placement, user approval, cash account, or live brokerage execution.

## Agent-specific resources
The `agents/` directory separates resources by agent. Only the tax agent loads `agents/tax/skills/govt_bond.md`; only the reporter agent is supplied trade history from `agents/reporter/memory.md`. Folder names alone do not confer access: `app.py` explicitly selects the files. The `advisor/` and `trader/` folders are intentionally empty placeholders.
