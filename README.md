# TechNest Support – Autonomous Escalation Agent

An agentic AI system for retail customer support. A customer raises a complaint on a web panel, and AI agents playing three levels of shop staff try to resolve it. If a level can't (not enough authority, or the customer rejects its offers), the case **escalates automatically to the next senior**, looping until the customer is satisfied or the case reaches human review.

**Live demo:** https://hammad1245.github.io/technest-escalation-agent/
*(The hosted demo runs rule-based agents. Run locally with an API key for the LLM-powered version.)*

## How it works

| Level | Agent | Authority |
|---|---|---|
| 1 | Rohan Verma – Sales Associate | Refund/replace up to ₹2,000, 7-day return window, accessory up to ₹200 |
| 2 | Priya Nair – Store Supervisor | Up to ₹15,000, 15-day window, 10% discount, fee waiver, priority repair |
| 3 | Arjun Mehta – Store Manager | Up to ₹1,00,000, any return window, voucher up to ₹5,000 |

**Escalation triggers**
- The fix needs more authority than the current level has
- The customer rejects 2 offers at the same level
- The agent call fails

**Design highlights**
- **LLM decides, code guards:** each agent chooses between two tools (`propose` / `escalate`) via tool use, but a deterministic `check()` function validates every offer against that level's limits. The model cannot exceed its authority.
- **Context carries upward:** seniors see all rejected offers and the customer's comments, so they improve the offer instead of repeating it.
- **Autonomous loop:** a single `run()` loop keeps going without human input until the case is `resolved` (customer accepts) or `human_review` (Level 3 also fails).
- **Fallback:** if no API key is set, rule-based agents run the same flow.

## Run locally (LLM agents)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key   # optional; omit for rule-based mode
python app.py                       # http://localhost:5000
```

`index.html` is a standalone, no-install version of the same app.

## Tech stack
Python, Flask, Anthropic API (tool use), vanilla JS/HTML/CSS.

## Limitations / next steps
- Cases are stored in memory (add SQLite/Postgres for persistence)
- Time-based SLAs (auto-escalate if a level is idle) are not implemented
- Add a staff dashboard and real tools (order lookup, refund API)
