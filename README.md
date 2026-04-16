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
- BTC context filter для альтов
- Входы на `15m`
- Фильтр `min_rr_ratio`
- Фильтр `no_middle_range`
- Фильтр funding window
- Блокировка по резкому всплеску `OI`
- Фильтр “edge after costs”
- Quality filter для `fakeout`
- Поддержка `SYMBOL_BLACKLIST`
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
- Heartbeat и alert при подозрении на простой/рестарт
- Backoff при rate-limit Bybit
- Singleton lock: защита от параллельного запуска двух копий агента

### Уведомления и отчеты
- Уведомление о старте агента
- Уведомление об открытии позиции
- Уведомление о частичном закрытии
- Уведомление о полном закрытии
- Уведомления об ошибках
- Суточный отчет по торговому дню
- Статы по стратегиям в суточном отчете
- Post-trade analytics: `R`, `hold_minutes`, `MFE`, `MAE`, `fees`
- `trade_review` по закрытым сделкам
- `skip analytics summary` по причинам отказа

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
- Исключает тикеры из `SYMBOL_BLACKLIST`
- Потом сортирует по `turnover24h`
- И берет top `MAX_SCAN_SYMBOLS`
- При rate-limit временно сам сужает universe

### Размер позиции в текущем боевом профиле
Сейчас боевой профиль настроен так:
- `POSITION_SIZING_MODE=fixed_margin_usd`
- `TARGET_MARGIN_USD=10`
- `TARGET_LEVERAGE=5`
- `MAX_LEVERAGE=20`
- `MAX_RISK_PER_TRADE_USD=1.5`
- `MIN_RR_RATIO=3.0`
- `MAX_TRADES_PER_DAY=5`
- `STOP_AFTER_LOSSES=2`

Это означает:
- агент целится использовать около `10 USDT` маржи на сделку;
- при `5x` это дает примерно `50 USDT notional`;
- сделки с заведомо плохим риском не проходят;
- сделки с плохим `R:R` не проходят;
- после серии убытков агент сам останавливает торговлю на день.

## Все режимы sizing

### `risk_pct`
Размер позиции считается как процент от equity.

Ключевые параметры:
- `POSITION_SIZING_MODE=risk_pct`
- `RISK_PER_TRADE_PCT`

Когда использовать:
- если нужен максимально классический риск-менеджмент;
- если депозит уже достаточно большой;
- если не хочется вручную контролировать размер notional/маржи.

Плюсы:
- риск автоматически масштабируется от баланса;
- удобно для более “профессионального” money management.

Минусы:
- размер позиции может заметно плавать;
- на маленьком депозите сделки часто получаются слишком маленькими.

### `risk_usd`
Размер позиции считается от фиксированного риска в долларах.

Ключевые параметры:
- `POSITION_SIZING_MODE=risk_usd`
- `RISK_PER_TRADE_USD`

Когда использовать:
- если хочется строго контролировать убыток в долларах;
- если баланс маленький, но нужен понятный денежный риск на сделку.

Плюсы:
- очень понятный контроль потерь;
- удобно сравнивать сделки между собой.

Минусы:
- размер позиции сильно зависит от ширины стопа;
- notional может получаться то маленьким, то слишком большим.

### `fixed_notional_usd`
Агент целится в фиксированный размер позиции.

Ключевые параметры:
- `POSITION_SIZING_MODE=fixed_notional_usd`
- `TARGET_NOTIONAL_USD`
- дополнительно можно ограничить через `MAX_RISK_PER_TRADE_USD`

Когда использовать:
- если нужен очень стабильный размер входа;
- если хочется мягко протестировать торговлю на малом депозите.

Плюсы:
- очень предсказуемый размер сделки;
- просто понимать загрузку капитала.

Минусы:
- реальный риск по сделке будет плавать вместе со стопом;
- без `MAX_RISK_PER_TRADE_USD` можно поймать неудобные сетапы.

### `fixed_margin_usd`
Агент целится в фиксированную маржу на сделку и использует целевое плечо.

Ключевые параметры:
- `POSITION_SIZING_MODE=fixed_margin_usd`
- `TARGET_MARGIN_USD`
- `TARGET_LEVERAGE`
- `MAX_LEVERAGE`
- `MAX_RISK_PER_TRADE_USD`

Когда использовать:
- если нужен более “фьючерсный” режим;
- если хочется контролировать именно используемую маржу, а не только notional;
- если нужен более активный профиль на небольшом депозите.

Плюсы:
- удобно управлять загрузкой депозита;
- можно задать понятную комбинацию `маржа + плечо`.

Минусы:
- при слишком большом плече стоп придется делать слишком тесным;
- без risk-cap такой режим легко становится агрессивным.

### Динамическое плечо
В режиме `fixed_margin_usd` можно включить динамическое плечо:
- `DYNAMIC_LEVERAGE_ENABLED=true`

Тогда агент берет базовое плечо из стратегии:
- `FAKEOUT_TARGET_LEVERAGE`
- `BREAKOUT_TARGET_LEVERAGE`
- `REVERSAL_TARGET_LEVERAGE`

И потом корректирует его:
- вверх при высоком `score`
- вниз при высоком `ATR`

Это позволяет:
- не давать одинаковое плечо всем сетапам подряд;
- быть осторожнее на шумном рынке;
- чуть агрессивнее использовать действительно сильные сигналы.

## Как выбирать режим

### Если депозит маленький и нужен мягкий старт
- `fixed_notional_usd`
- `TARGET_NOTIONAL_USD=10`
- `MAX_RISK_PER_TRADE_USD=1.0`

### Если хочется активнее использовать капитал, но без безумия
- `fixed_margin_usd`
- `TARGET_MARGIN_USD=10`
- `TARGET_LEVERAGE=5`
- `MAX_RISK_PER_TRADE_USD=1.0-1.5`

### Если нужен строгий контроль потерь
- `risk_usd`
- `RISK_PER_TRADE_USD=1.0-1.5`

### Если депозит вырастет и нужен более “портфельный” режим
- `risk_pct`
- `RISK_PER_TRADE_PCT=0.5-1.0`

## Как выбирать плечо

### `1x`
- самый спокойный режим;
- подходит для `fixed_notional_usd`;
- на маленьком депозите часто слишком вялый.

### `3x-5x`
- лучший рабочий диапазон для старта на маленьком депозите;
- позволяет использовать капитал заметно активнее;
- стопы еще не становятся слишком тесными.
- в текущем боевом профиле это основной рабочий диапазон

### `8x-10x`
- уже агрессивнее;
- требует более точных входов и более чистых инструментов;
- для альтов может стать слишком чувствительным к рыночному шуму.

### `20x`
- только как верхний лимит, а не как базовый режим;
- при том же risk-cap требует очень тесного стопа;
- для большинства альтов и мемов обычно слишком агрессивно.

## Практика по риску

### Что делает `MAX_RISK_PER_TRADE_USD`
Это верхний потолок потерь на сделку.
Если структура сделки требует большего риска, сигнал будет пропущен.

### Что сейчас стоит в бою
- `MAX_RISK_PER_TRADE_USD=1.5`

Это примерно:
- около `2.6%` от депозита `58 USDT`
- при `R:R = 1:3` целевой reward около `4.5 USDT`

### Что обычно разумно
- `1.0 USDT` — спокойный старт
- `1.2-1.5 USDT` — бодро, но терпимо
- `2.0 USDT+` — уже агрессивно для малого депозита

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
- `RISK_PER_TRADE_PCT`
- `RISK_PER_TRADE_USD`
- `TARGET_NOTIONAL_USD`
- `TARGET_MARGIN_USD`
- `TARGET_LEVERAGE`
- `MAX_LEVERAGE`
- `MAX_RISK_PER_TRADE_USD`
- `MIN_RR_RATIO`
- `MIN_EDGE_COST_RATIO`
- `MIN_NET_REWARD_PCT`
- `MIN_RULE_SCORE`
- `MAX_SCAN_SYMBOLS`
- `SYMBOL_BLACKLIST`
- `FAKEOUT_EDGE_MAX_FRAC`
- `FAKEOUT_MAX_ATR_RATIO`
- `FAKEOUT_MAX_OI_CHANGE_PCT`
- `FAKEOUT_MIN_VOL_RATIO`
- `MAX_TRADES_PER_DAY`
- `STOP_AFTER_LOSSES`
- `SESSION_TIMEZONE`
- `ENABLE_EOD_CLOSE`
- `STATE_DB_PATH`

### 6. Рекомендуемый боевой профиль для малого депозита
```env
TESTNET=false
POSITION_SIZING_MODE=fixed_margin_usd
TARGET_MARGIN_USD=10
TARGET_LEVERAGE=5
MAX_LEVERAGE=20
DYNAMIC_LEVERAGE_ENABLED=true
FAKEOUT_TARGET_LEVERAGE=4
BREAKOUT_TARGET_LEVERAGE=5
REVERSAL_TARGET_LEVERAGE=3
DYNAMIC_LEVERAGE_HIGH_SCORE=80
DYNAMIC_LEVERAGE_LOW_SCORE=72
DYNAMIC_LEVERAGE_HIGH_SCORE_BONUS=1
DYNAMIC_LEVERAGE_LOW_SCORE_CUT=1
DYNAMIC_LEVERAGE_HIGH_ATR_RATIO=0.012
DYNAMIC_LEVERAGE_HIGH_ATR_CUT=1
MAX_RISK_PER_TRADE_USD=1.5
MIN_RR_RATIO=3.0
MIN_EDGE_COST_RATIO=2.0
MIN_NET_REWARD_PCT=0.25
MIN_RULE_SCORE=70
MAX_SCAN_SYMBOLS=20
SYMBOL_BLACKLIST=FARTCOINUSDT,1000PEPEUSDT,RAVEUSDT
FAKEOUT_EDGE_MAX_FRAC=0.12
FAKEOUT_MAX_ATR_RATIO=0.012
FAKEOUT_MAX_OI_CHANGE_PCT=2.0
FAKEOUT_MIN_VOL_RATIO=90
MAX_TRADES_PER_DAY=5
STOP_AFTER_LOSSES=2
SESSION_TIMEZONE=Europe/Moscow
ENABLE_EOD_CLOSE=false
```

### 7. Более спокойный профиль
```env
POSITION_SIZING_MODE=fixed_notional_usd
TARGET_NOTIONAL_USD=10
MAX_RISK_PER_TRADE_USD=1.0
MIN_RR_RATIO=3.0
```

### 8. Что не стоит делать сразу
- не ставить `20x` как базовое рабочее плечо;
- не поднимать `MAX_RISK_PER_TRADE_USD` слишком резко;
- не отключать risk-cap ради “больших движений”;
- не торговать шумные low-cap без blacklist или whitelist.

### Как сейчас выбирается плечо в бою
- `fakeout` базово: `4x`
- `breakout` базово: `5x`
- `reversal` базово: `3x`
- сильный `score` может дать небольшой бонус
- высокий `ATR` может уменьшить плечо
- итог все равно ограничен `MAX_LEVERAGE` и `MAX_RISK_PER_TRADE_USD`

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
- alert о простое/рестарте после заметного gap;
- суточный отчет.

## Что смотреть по качеству торговли
- `realized_r` — сколько фактически принесла сделка в единицах риска
- `hold_minutes` — сколько позиция жила
- `mfe_r` — максимальное благоприятное движение в `R`
- `mae_r` — максимальное неблагоприятное движение в `R`
- `total_fee_usd` — суммарные комиссии по сделке, если биржа их вернула
- `review_text` — короткий разбор закрытой сделки
- `review_tags` — быстрые флаги вроде `full_stop`, `gave_back_edge`, `strong_winner`

Эти поля сохраняются в SQLite и используются в суточном отчете.

## Skip Analytics
Агент теперь агрегирует причины `skip` в течение дня.

Что это дает:
- видно, какие фильтры чаще всего режут рынок;
- можно понять, агент слишком строгий или слишком мягкий;
- проще калибровать `regime`, `fakeout` и `risk`-пороги.

В отчетах и summary это показывается как `Skip summary`.

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
