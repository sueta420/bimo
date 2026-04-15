# Инструкция для OpenClaw `bimo crypto`

## Роли
- `bimo` — торговый исполнитель, который реально открывает и сопровождает сделки.
- `crypto` в `OpenClaw` — аналитик и оркестратор, который помогает наблюдать, объяснять, проверять конфиг и разбирать логи.
- `crypto alert` — отдельный контур алертов и уведомлений, если ты используешь его параллельно.

## Важное правило
`OpenClaw crypto` не должен сам принимать сделки автоматически и не должен подменять собой торговый runtime.

Правильная схема:
1. `bimo` торгует.
2. `OpenClaw crypto` читает логи, отчеты, конфиги и помогает с анализом.
3. Пользователь принимает организационные решения и подтверждает изменения.

## Где смотреть состояние агента

### Главный лог
- `/Users/bot/.openclaw/workspace-crypto/bimo/agent_YYYY-MM-DD.log`

### Runtime env
- `/Users/bot/trading/.env`

### LaunchAgent
- `/Users/bot/Library/LaunchAgents/com.trading.agent.plist`

### SQLite state
- `STATE_DB_PATH` из runtime env

## Что должен уметь OpenClaw `crypto`
- Проверять, запущен ли агент
- Читать последние строки логов
- Объяснять `skip`-причины
- Сводить результаты дня
- Предлагать изменения конфигурации
- Помогать анализировать качество сигналов
- Не лезть напрямую в торговые ключи без необходимости

## Какие команды полезны для оператора

### Проверить старт агента
```bash
rg 'agent_start|REAL MONEY|TESTNET' /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Проверить кандидатов и скипы
```bash
rg 'candidate |skip |opened ' /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Проверить ошибки
```bash
rg '"level": "ERROR"' /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

### Следить в реальном времени
```bash
tail -f /Users/bot/.openclaw/workspace-crypto/bimo/agent_2026-04-16.log
```

## Как интерпретировать основные события

### Нормальные рабочие строки
- `agent_start` — агент поднялся
- `sleep_sec=900` — цикл завершен, агент спит до следующего прохода
- `skip ...` — символ проверен, но сигнал не прошел фильтры
- `candidate ...` — найден сильный кандидат
- `opened ...` — сделка реально открыта

### Потенциально важные строки
- `recovery_positions_failed` — проблема при восстановлении состояния с биржи
- `sync_trade_failed` — проблема синхронизации сделки
- `close_all_failed` — ошибка при закрытии
- `critical_error_stop_triggered` — kill switch остановил торговлю

## Как правильно просить OpenClaw `crypto`
Хорошие запросы:
- “покажи последние ошибки торгового агента”
- “почему сегодня не было сделок”
- “какие причины skip встречаются чаще всего”
- “сделай короткий отчет за день”
- “сравни текущий runtime env с рекомендованным профилем”

Плохие запросы:
- “сам прими сделку вместо агента”
- “обойди risk-фильтры”
- “запусти бота на реале без проверки конфигурации”

## Что проверять перед боевым запуском
- `TESTNET=false`
- ключи Bybit соответствуют боевому режиму
- `TARGET_NOTIONAL_USD` выставлен как нужно
- `MAX_RISK_PER_TRADE_USD` не слишком агрессивен
- `ENABLE_EOD_CLOSE=false`, если позиции должны жить до `SL/TP`
- `SESSION_TIMEZONE=Europe/Moscow`, если нужен московский торговый день

## Что проверять после запуска
- есть строка `agent_start mode=*** REAL MONEY ***`
- нет постоянных `ERROR`
- появляются `skip` или `candidate`, то есть цикл реально жив
- Telegram-уведомления доходят

## Что делать при проблемах
1. Проверить `agent_YYYY-MM-DD.log`
2. Проверить `startup.log`, `agent_stdout.log`, `agent_stderr.log`
3. Проверить `/Users/bot/trading/.env`
4. Проверить, что `LaunchAgent` загружен
5. Если нужно, перезапустить `launchd`

## Перезапуск
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.trading.agent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.trading.agent.plist
```

## Граница ответственности
- `bimo` отвечает за торговую механику
- `OpenClaw crypto` отвечает за анализ, оркестрацию и сопровождение
- пользователь отвечает за режим торговли, депозит и принятие риска
