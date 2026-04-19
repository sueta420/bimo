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

### Текущий боевой профиль
- `AGENT_PROFILE=v2-lite`
- `ENABLED_STRATEGIES=trend_pullback,range_bounce`
- `POSITION_SIZING_MODE=fixed_margin_usd`
- `TARGET_MARGIN_USD=10`
- `TARGET_RISK_USD=0`
- `MIN_RISK_UTILIZATION=0`
- `TARGET_LEVERAGE=5`
- `MAX_LEVERAGE=20`
- `MAX_RISK_PER_TRADE_USD=2.0`
- `MIN_RR_RATIO=2.7`
- `MAX_SIDE_RISK_PCT=5.0`
- `MAX_SIDE_RISK_USD=3.0`
- `MAX_SCAN_SYMBOLS=35`
- `MIN_VOLUME_24H=25000000`
- `UNIVERSE_MIN_DAILY_MOVE_PCT=0.8`
- `UNIVERSE_MAX_DAILY_MOVE_PCT=18.0`
- `CYCLE_SEC=600`

### LaunchAgent
- `/Users/bot/Library/LaunchAgents/com.trading.agent.plist`

### SQLite state
- `STATE_DB_PATH` из runtime env

## Что должен уметь OpenClaw `crypto`
- Проверять, запущен ли агент
- Читать последние строки логов
- Объяснять `skip`-причины
- Сводить результаты дня
- Сводить rolling weekly summary из `REPORTS_DIR`
- Сводить стратегию по текущему `v2-lite` режиму: `trend_pullback / range_bounce`
- Смотреть post-trade метрики: `realized_r`, `hold_minutes`, `mfe_r`, `mae_r`
- Понимать разницу между `notional`, `margin`, `leverage` и `risk cap`
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
- `sleep_sec=600` — цикл завершен, агент спит до следующего прохода
- `skip ...` — символ проверен, но сигнал не прошел фильтры
- `candidate ...` — найден сильный кандидат
- `opened ...` — сделка реально открыта

### Потенциально важные строки
- `recovery_positions_failed` — проблема при восстановлении состояния с биржи
- `sync_trade_failed` — проблема синхронизации сделки
- `close_all_failed` — ошибка при закрытии
- `critical_error_stop_triggered` — kill switch остановил торговлю
- `agent_restart_detected` — агент был недоступен и потом поднялся
- `rate_limit_backoff` — агент уткнулся в лимит API и сузил сканирование

## Как правильно просить OpenClaw `crypto`
Хорошие запросы:
- “покажи последние ошибки торгового агента”
- “почему сегодня не было сделок”
- “какие причины skip встречаются чаще всего”
- “сделай короткий отчет за день”
- “сравни текущий runtime env с рекомендованным профилем”
- “объясни текущий режим sizing и какой сейчас реальный риск на сделку”
- “проверь, что агент действительно работает в `v2-lite` и торгует только `trend_pullback/range_bounce`”

Плохие запросы:
- “сам прими сделку вместо агента”
- “обойди risk-фильтры”
- “запусти бота на реале без проверки конфигурации”

## Что проверять перед боевым запуском
- `TESTNET=false`
- ключи Bybit соответствуют боевому режиму
- `POSITION_SIZING_MODE` выставлен как нужно
- `AGENT_PROFILE=v2-lite`, если нужен упрощенный боевой режим
- `ENABLED_STRATEGIES=trend_pullback,range_bounce`
- если используется `fixed_notional_usd`, то `TARGET_NOTIONAL_USD` выставлен как нужно
- если используется `fixed_margin_usd`, то `TARGET_MARGIN_USD` и `TARGET_LEVERAGE` выставлены как нужно
- `MAX_LEVERAGE` не слишком агрессивен
- `MAX_RISK_PER_TRADE_USD` не слишком агрессивен
- `TARGET_RISK_USD=0` и `MIN_RISK_UTILIZATION=0`, если риск должен быть свободным снизу и ограниченным только сверху
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

## Как объяснять sizing правильно
- `notional` — полный размер позиции
- `margin` — сколько реальных средств выделено под позицию
- `leverage` — во сколько раз notional больше margin
- `MAX_RISK_PER_TRADE_USD` — не размер позиции, а потолок допустимого убытка по структуре сделки

Пример текущего профиля:
- `10 USDT margin @ 5x` = примерно `50 USDT notional`
- при `MAX_RISK_PER_TRADE_USD=2.0` агент пропускает сделки, где структура требует большего убытка
- нижняя граница риска сейчас не зафиксирована, то есть агент может брать и более “легкие” по риску сделки, если они проходят остальные фильтры
