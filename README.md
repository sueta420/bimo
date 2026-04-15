# Bimo: агент для торговли фьючерсами Bybit

## Что это
`bimo` — это торговый агент для USDT-фьючерсов Bybit с rule-based логикой входа, риск-менеджментом, SQLite-состоянием, Telegram-уведомлениями и интеграцией в локальную инфраструктуру через `OpenClaw` и `launchd`.

Текущая версия проекта уже умеет:
- искать сигналы по ликвидным USDT-инструментам;
- рассчитывать размер позиции с учетом риска, комиссий, slippage и funding;
- открывать и сопровождать сделки;
- вести состояние сделок и торгового дня в SQLite;
- восстанавливаться после рестарта;
- отправлять уведомления в Telegram;
- писать структурированные JSON-логи;
- работать в `TESTNET` и в боевом режиме.

## Главные принципы логики
- Решение о входе принимает не LLM, а rule engine.
- LLM используется только как вспомогательный слой для explain/rank и не является gatekeeper.
- Риск и исполнение важнее “красивого” сигнала.
- Открытая позиция живет до `SL`, `TP`, trailing/breakeven, ручного закрытия или аварийной логики.
- Принудительное закрытие в конце дня сейчас отключено через `ENABLE_EOD_CLOSE=false`.
- Дневная статистика и отчеты сейчас идут по `Europe/Moscow`.

## Что реализовано

### Сигналы и фильтры
- Rule-based сигналы: `breakout`, `fakeout`, `reversal`
- Regime-фильтрация по `1h` и `4h`
- Входы на `15m`
- Фильтр `min_rr_ratio`
- Фильтр `no_middle_range`
- Фильтр funding window
- Блокировка по резкому всплеску `OI`
- Фильтр “edge after costs”
- Score-модель с причинами `reasons`
- Логирование `candidate`, `skip`, `candidate_pool`

### Риск и размер позиции
- Режимы sizing:
  - `risk_pct`
  - `risk_usd`
  - `fixed_notional_usd`
- Учет:
  - taker fee
  - slippage входа
  - slippage выхода
  - funding reserve
- Контроль:
  - `max_leverage`
  - `min_qty`
  - `min_notional`
  - `max_side_risk_pct`
  - correlation guard
  - `max_risk_per_trade_usd`

### Исполнение и надежность
- State machine:
  `SIGNALLED -> ORDER_SENT -> FILLED -> OPEN -> PARTIALLY_CLOSED -> CLOSED -> RECONCILED`
- Reconciliation заявки после `ORDER_SENT`
- Проверка закрытия позиции через биржу перед финальным `CLOSED`
- Recovery после рестарта
- Kill switch по критичным execution/reconciliation ошибкам
- Session/day state сохраняется в SQLite

### Уведомления и отчеты
- Уведомление о старте агента
- Уведомление об открытии позиции
- Уведомление о частичном закрытии
- Уведомление о полном закрытии
- Уведомления об ошибках
- Суточный отчет по торговому дню

## Структура проекта
- `config.py` — чтение конфигурации из env
- `signals.py` — индикаторы, сигналы, фильтры, scoring
- `risk.py` — sizing, risk cap, leverage checks, correlation
- `exchange.py` — работа с Bybit
- `execution.py` — главный торговый цикл, state machine, reconciliation
- `portfolio.py` — модели и SQLite state store
- `notifier.py` — Telegram и LLM explain/rank
- `main.py` — точка входа
- `futures_agent_v2.py` — compatibility wrapper
- `tests/` — тесты

## Где находится runtime

### Исходники
- `/Users/bot/.openclaw/workspace-crypto/bimo`

### Боевой entrypoint
- `/Users/bot/trading/futures_agent.py`

Этот файл является тонкой оберткой, которая запускает актуальную модульную версию из `workspace-crypto/bimo`.

### LaunchAgent
- `/Users/bot/Library/LaunchAgents/com.trading.agent.plist`

### Runtime env
- `/Users/bot/trading/.env`

### Логи
- `/Users/bot/.openclaw/workspace-crypto/bimo/agent_YYYY-MM-DD.log`
- `/Users/bot/trading/startup.log`
- `/Users/bot/trading/agent_stdout.log`
- `/Users/bot/trading/agent_stderr.log`

### Состояние
- `STATE_DB_PATH`
- по умолчанию в проекте: `./agent_state.sqlite3`
- в runtime используется значение из `/Users/bot/trading/.env`

## Как агент работает сейчас

### Частота цикла
- По умолчанию `CYCLE_SEC=900`
- То есть один цикл примерно раз в `15 минут`

### Сколько инструментов сканируется
- Сейчас агент берет только `USDT`-контракты
- Фильтрует их по:
  - `MIN_VOLUME_24H`
  - `MAX_FUNDING_ABS`
- Потом сортирует по `turnover24h`
- И берет top-20

### Размер позиции в текущем боевом профиле
Сейчас боевой профиль настроен так:
- `POSITION_SIZING_MODE=fixed_notional_usd`
- `TARGET_NOTIONAL_USD=10`
- `MAX_RISK_PER_TRADE_USD=1.0`
- `MIN_RR_RATIO=3.0`
- `MAX_TRADES_PER_DAY=5`
- `STOP_AFTER_LOSSES=2`

Это означает:
- целевой вход около `10 USDT`;
- сделки с заведомо плохим риском не проходят;
- сделки с плохим `R:R` не проходят;
- после серии убытков агент сам останавливает торговлю на день.

## Полная инструкция по настройке

### 1. Установить зависимости
```bash
cd /Users/bot/.openclaw/workspace-crypto/bimo
python3 -m pip install -r requirements.txt
```

### 2. Подготовить env
```bash
cp .env.example .env
```

Либо использовать runtime env:
- `/Users/bot/trading/.env`

### 3. Обязательные переменные
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

### 4. Рекомендуемые переменные
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 5. Ключевые настройки
- `TESTNET`
- `POSITION_SIZING_MODE`
- `TARGET_NOTIONAL_USD`
- `MAX_RISK_PER_TRADE_USD`
- `MIN_RR_RATIO`
- `MIN_EDGE_COST_RATIO`
- `MIN_NET_REWARD_PCT`
- `MAX_TRADES_PER_DAY`
- `STOP_AFTER_LOSSES`
- `SESSION_TIMEZONE`
- `ENABLE_EOD_CLOSE`
- `STATE_DB_PATH`

### 6. Рекомендуемый боевой профиль для малого депозита
```env
TESTNET=false
POSITION_SIZING_MODE=fixed_notional_usd
TARGET_NOTIONAL_USD=10
MAX_RISK_PER_TRADE_USD=1.0
MIN_RR_RATIO=3.0
MIN_EDGE_COST_RATIO=2.0
MIN_NET_REWARD_PCT=0.25
MAX_TRADES_PER_DAY=5
STOP_AFTER_LOSSES=2
SESSION_TIMEZONE=Europe/Moscow
ENABLE_EOD_CLOSE=false
```

## Как запускать

### Локальный запуск из исходников
```bash
cd /Users/bot/.openclaw/workspace-crypto/bimo
python3 main.py
```

### Через compatibility wrapper
```bash
cd /Users/bot/.openclaw/workspace-crypto/bimo
python3 futures_agent_v2.py
```

### Боевой запуск через launchd
Фактически используется:
- `/Users/bot/trading/futures_agent.py`
- `/Users/bot/Library/LaunchAgents/com.trading.agent.plist`

Перезапуск:
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.trading.agent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.trading.agent.plist
```

## Как смотреть логи

### Главный лог агента
```bash
tail -f /Users/bot/.openclaw/workspace-crypto/bimo/agent_$(date +%Y-%m-%d).log
```

### Последние строки
```bash
tail -n 100 /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Только ошибки
```bash
rg '"level": "ERROR"' /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Кандидаты, скипы, открытия
```bash
rg 'candidate |skip |opened ' /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Runtime-логи launchd
```bash
tail -n 100 /Users/bot/trading/startup.log
tail -n 100 /Users/bot/trading/agent_stdout.log
tail -n 100 /Users/bot/trading/agent_stderr.log
```

## Что приходит в Telegram
- старт агента;
- открытие позиции;
- частичное закрытие;
- закрытие позиции с `PnL`;
- критичные ошибки;
- суточный отчет.

Важно:
- торговый агент шлет уведомления напрямую через `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`;
- это отдельный контур от `OpenClaw crypto`;
- `OpenClaw crypto` не исполняет сделки автоматически.

## Как считается торговый день
Сейчас торговый день:
- считается по `SESSION_TIMEZONE`
- в runtime выставлен как `Europe/Moscow`

Это влияет на:
- `DayStats.date`
- суточный отчет
- имя daily log-файла

При этом:
- сделки больше не закрываются автоматически из-за наступления конца дня;
- позиции живут до `SL/TP` или другой торговой логики.

## Как работают отчеты
- Суточный отчет формируется на границе нового торгового дня
- Сначала агент завершает предыдущий день
- Затем пишет текстовый отчет `report_YYYYMMDD.txt`
- И отправляет краткий summary в Telegram

Отчет содержит:
- количество сделок;
- количество `WIN/LOSS`;
- winrate;
- итоговый `PnL`;
- число сигналов;
- число skipped сетапов;
- список закрытых сделок.

## Тесты
```bash
cd /Users/bot/.openclaw/workspace-crypto/bimo
PYTHONPATH=/Users/bot/.openclaw/workspace-crypto/bimo /Users/bot/bimo_cloned/.venv311/bin/pytest -q tests
```

Сейчас ожидаемое состояние:
- все тесты зеленые

## Подготовка проекта к git

### Что должно попасть в git
- исходники `*.py`
- `README.md`
- `.env.example`
- `requirements.txt`
- тесты
- полезная документация

### Что не должно попадать в git
- `.env`
- реальные API-ключи
- SQLite state
- runtime-логи
- временные кэши
- локальные IDE-файлы

### Перед push
Проверь:
```bash
git status
git diff -- .env
git diff -- /Users/bot/trading/.env
```

Если есть сомнения, отдельно проверь, что в staged-изменениях нет:
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`

## Откат
Точки отката:
- `/Users/bot/trading/futures_agent_legacy_20260415.py`
- `/Users/bot/Library/LaunchAgents/com.trading.agent_20260415.plist.bak`
- `/Users/bot/backup_bimo_snapshot_20260415_230529`

## OpenClaw
Для оркестратора `OpenClaw crypto` отдельная инструкция лежит рядом:
- `BIMO_CRYPTO_GUIDE.md`

Коротко:
- `OpenClaw crypto` — это аналитик и оркестратор
- `bimo` — это торговый исполнитель
- не стоит смешивать эти роли в одном процессе
