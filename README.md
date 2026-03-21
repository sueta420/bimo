# Bybit Futures Agent (Refactored)

## English

### Features
- Rule-based signal selection (LLM is optional for explain/rank)
- Position sizing with exchange constraints (`qtyStep`, `minOrderQty`, `minNotional`)
- Risk and execution guards (side-risk, correlation, funding/OI filters, cooldowns)
- SQLite state machine:
  `SIGNALLED -> ORDER_SENT -> FILLED -> OPEN -> PARTIALLY_CLOSED -> CLOSED -> RECONCILED`
- Telegram notifications and JSON logs

### Position Sizing Modes
- `POSITION_SIZING_MODE=risk_pct`
  uses `RISK_PER_TRADE_PCT` (% of equity)
- `POSITION_SIZING_MODE=risk_usd`
  uses fixed `RISK_PER_TRADE_USD` risk per trade
- `POSITION_SIZING_MODE=fixed_notional_usd`
  uses fixed `TARGET_NOTIONAL_USD` position notional

Examples:
```bash
# Fixed risk per trade
POSITION_SIZING_MODE=risk_usd
RISK_PER_TRADE_USD=5.0

# Fixed position size (notional)
POSITION_SIZING_MODE=fixed_notional_usd
TARGET_NOTIONAL_USD=5.0
```

### Side-Risk Guard (Test Switch)
- `DISABLE_SIDE_RISK_GUARD=false` (default): side-risk check is active (`MAX_SIDE_RISK_PCT`)
- `DISABLE_SIDE_RISK_GUARD=true`: side-risk check is bypassed (for testing only)

Example:
```bash
DISABLE_SIDE_RISK_GUARD=true
```

### Setup
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
# fill keys in .env
python3 main.py
```

Compatibility command:
```bash
python3 futures_agent_v2.py
```

### Required Environment Variables
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

Optional:
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Crash Alerts
- If Telegram is configured, fatal startup/runtime crashes now trigger a forced crash message with host, PID and traceback.
- This does not cover hard kills outside Python process control (`SIGKILL`, OOM-killer, host reboot/power loss).
- For full reliability in production, run the agent under `systemd`/`supervisor` with auto-restart and external health monitoring.

### Tests
```bash
pytest -q
```

### Notes
- Live Bybit behavior should be validated in your runtime environment.

## Русский

### Что реализовано
- Отбор сигналов по правилам (LLM опционален для объяснений/ранжирования)
- Расчёт размера позиции с учётом ограничений биржи (`qtyStep`, `minOrderQty`, `minNotional`)
- Риск- и execution-фильтры (side-risk, корреляции, funding/OI, cooldown)
- Машина состояний в SQLite:
  `SIGNALLED -> ORDER_SENT -> FILLED -> OPEN -> PARTIALLY_CLOSED -> CLOSED -> RECONCILED`
- Уведомления в Telegram и JSON-логи

### Режимы размера позиции
- `POSITION_SIZING_MODE=risk_pct`
  риск как `%` от equity (`RISK_PER_TRADE_PCT`)
- `POSITION_SIZING_MODE=risk_usd`
  фиксированный риск на сделку в USD (`RISK_PER_TRADE_USD`)
- `POSITION_SIZING_MODE=fixed_notional_usd`
  фиксированный объём входа в USD (`TARGET_NOTIONAL_USD`)

Примеры:
```bash
# Фиксированный риск на сделку
POSITION_SIZING_MODE=risk_usd
RISK_PER_TRADE_USD=5.0

# Фиксированный размер входа (номинал)
POSITION_SIZING_MODE=fixed_notional_usd
TARGET_NOTIONAL_USD=5.0
```

### Side-Risk Guard (переключатель для тестов)
- `DISABLE_SIDE_RISK_GUARD=false` (по умолчанию): проверка side-risk включена (`MAX_SIDE_RISK_PCT`)
- `DISABLE_SIDE_RISK_GUARD=true`: проверка side-risk отключается (только для тестов)

Пример:
```bash
DISABLE_SIDE_RISK_GUARD=true
```

### Запуск
```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
# заполните ключи в .env
python3 main.py
```

Совместимый старый запуск:
```bash
python3 futures_agent_v2.py
```

### Обязательные переменные окружения
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

Опционально:
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Аварийные уведомления
- Если Telegram настроен, при фатальном падении на старте/рантайме отправляется принудительное сообщение с host, PID и traceback.
- Этот механизм не покрывает «жёсткие» остановки вне контроля Python-процесса (`SIGKILL`, OOM-killer, перезагрузка/потеря питания хоста).
- Для продакшена используйте запуск через `systemd`/`supervisor` с авто-перезапуском и внешним health-monitoring.

### Тесты
```bash
pytest -q
```

### Примечание
- Поведение на реальной Bybit проверяйте в вашей рабочей среде.
