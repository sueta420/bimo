# Bybit Futures Agent (Refactored)

## What is implemented now
- Rule-based signal selection (LLM is not a gatekeeper)
- Risk sizing: `wallet equity -> risk_per_trade -> stop distance -> qty`
- Execution constraints: tick/qty step, min qty, min notional, max leverage, available balance guard
- Cost reserves: taker fees + slippage + funding reserve
- State machine with SQLite persistence:
  `SIGNALLED -> ORDER_SENT -> FILLED -> OPEN -> PARTIALLY_CLOSED -> CLOSED -> RECONCILED`
- Startup recovery/reconciliation from Bybit positions
- Strategy filters:
  - 1h/4h regime + 15m entries
  - funding window block
  - OI spike block
  - no-entry in middle of range
  - symbol cooldown after losing close
  - max side risk exposure
  - correlation guard for same-side positions
  - break-even/trailing only after confirmed move
- Telegram notifications
- JSON structured logs

## Project layout
- `config.py`
- `exchange.py`
- `signals.py`
- `risk.py`
- `execution.py`
- `portfolio.py`
- `notifier.py`
- `main.py`
- `futures_agent_v2.py` (compat wrapper)

## Setup
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
# fill keys in .env
python3 main.py
```

Or keep old command:
```bash
python3 futures_agent_v2.py
```

## Required env vars
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

Optional but recommended:
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Tests
```bash
pytest -q
```

## Notes
- In this environment, external network may be restricted; live Bybit integration should be validated on your side with real connectivity.
