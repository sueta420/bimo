import asyncio
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from exchange import BybitClient, normalize_order_qty
from notifier import LLMExplainer, TelegramNotifier
from portfolio import (
    ACTIVE_STATES,
    STATE_CLOSED,
    STATE_FILLED,
    STATE_OPEN,
    STATE_ORDER_SENT,
    STATE_PARTIALLY_CLOSED,
    STATE_RECONCILED,
    STATE_SIGNALLED,
    DayStats,
    StateStore,
    Trade,
)
from risk import (
    align_protective_prices,
    check_side_exposure,
    correlation_allowed,
    normalize_sizing_mode,
    returns_from_prices,
    round_to_step,
    rr_ratio,
    size_position,
)
from signals import (
    calc_indicators,
    detect_signals,
    edge_after_costs,
    in_funding_block,
    no_middle_range,
    oi_spike_block,
    regime_filter,
    score_signal,
    screen_coins,
    signal_quality_filter,
)


def make_report(
    stats: DayStats,
    max_trades_per_day: int,
    session_timezone: str = "UTC",
    reports_dir: str | None = None,
) -> tuple[str, str]:
    wins = [t for t in stats.trades if (t.pnl_usd or 0) > 0]
    losses = [t for t in stats.trades if (t.pnl_usd or 0) < 0]
    pnl = sum((t.pnl_usd or 0) for t in stats.trades)
    wr = len(wins) / len(stats.trades) * 100 if stats.trades else 0
    closed = len(stats.trades)
    lines = []
    strategy_rows = _strategy_rows(stats.trades)
    skip_rows = _skip_rows(stats.skip_reasons)
    for i, t in enumerate(stats.trades, 1):
        icon = "WIN" if (t.pnl_usd or 0) > 0 else ("LOSS" if (t.pnl_usd or 0) < 0 else "BE")
        lines.append(
            f"  #{i}[{icon}] {t.symbol} {t.direction} {t.strategy} "
            f"PnL:{(t.pnl_usd or 0):+.4f}$ R:{(t.realized_r if t.realized_r is not None else 0):+.2f} "
            f"Hold:{(t.hold_minutes if t.hold_minutes is not None else 0):.0f}m "
            f"score:{t.score} {t.close_reason}"
            + (f" | review: {t.review_text}" if t.review_text else "")
        )
    report = (
        f"\n{'=' * 52}\n  ОТЧЁТ {stats.date} ({session_timezone})\n{'=' * 52}\n"
        f"  Сделок:{closed}/{max_trades_per_day} WIN:{len(wins)} LOSS:{len(losses)}\n"
        f"  Винрейт:{wr:.1f}% | PnL:{pnl:+.4f}$\n"
        f"  Сигналов:{stats.signals_total} | Скип:{stats.skipped} | Открыто:{stats.opened} | Закрыто:{closed}\n"
    )
    if strategy_rows:
        report += f"{'─' * 52}\n{strategy_rows}\n"
    if skip_rows:
        report += f"{'─' * 52}\nSkip summary:\n{skip_rows}\n"
    report += f"{'─' * 52}\n"
    report += "\n".join(lines) if lines else "  Сделок не было"
    report += f"\n{'=' * 52}\n"
    base_dir = os.path.abspath(reports_dir or os.getcwd())
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"report_{stats.date.replace('-', '')}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return report, path


def summarize_reasons(reasons: list[str], limit: int = 4) -> str:
    head = [str(x) for x in reasons[:limit] if str(x).strip()]
    return ",".join(head) if head else "-"


def _strategy_rows(trades: list[Trade]) -> str:
    if not trades:
        return ""
    grouped = {}
    for t in trades:
        bucket = grouped.setdefault(
            t.strategy,
            {"count": 0, "wins": 0, "pnl": 0.0, "r_sum": 0.0, "r_count": 0, "avg_hold": 0.0, "hold_count": 0},
        )
        bucket["count"] += 1
        if (t.pnl_usd or 0) > 0:
            bucket["wins"] += 1
        bucket["pnl"] += float(t.pnl_usd or 0.0)
        if t.realized_r is not None:
            bucket["r_sum"] += float(t.realized_r)
            bucket["r_count"] += 1
        if t.hold_minutes is not None:
            bucket["avg_hold"] += float(t.hold_minutes)
            bucket["hold_count"] += 1
    rows = []
    for strategy, data in sorted(grouped.items()):
        count = max(int(data["count"]), 1)
        wr = (float(data["wins"]) / count) * 100.0
        avg_r = data["r_sum"] / data["r_count"] if data["r_count"] else 0.0
        avg_hold = data["avg_hold"] / data["hold_count"] if data["hold_count"] else 0.0
        rows.append(
            f"  {strategy}: n={count} WR={wr:.1f}% PnL={float(data['pnl']):+.4f}$ avgR={avg_r:+.2f} avgHold={avg_hold:.0f}m"
        )
    return "\n".join(rows)


def _skip_rows(skip_reasons: dict[str, int]) -> str:
    if not skip_reasons:
        return ""
    ordered = sorted(skip_reasons.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    return "\n".join(f"  {reason}: {count}" for reason, count in ordered[:8])


def should_use_llm_rank(candidates: list[dict], cfg: dict) -> tuple[bool, str]:
    if not candidates:
        return False, "no_candidates"
    if not cfg.get("use_llm_secondary_rank"):
        return False, "disabled"
    top_n = max(2, int(cfg.get("llm_rank_top_n", 3) or 3))
    head = candidates[:top_n]
    if len(head) < 2:
        return False, "too_few_candidates"
    top_score = int(head[0].get("score", 0) or 0)
    low_score = int(head[-1].get("score", 0) or 0)
    min_score = int(cfg.get("llm_rank_min_score", 72) or 72)
    max_spread = int(cfg.get("llm_rank_max_score_spread", 6) or 6)
    if top_score < min_score:
        return False, "top_score_below_min"
    if (top_score - low_score) > max_spread:
        return False, "top_score_clear_winner"
    return True, "use_llm_rank"


def resolve_session_tz(name: str):
    try:
        return ZoneInfo(str(name or "UTC"))
    except Exception:
        return timezone.utc


class Agent:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.ex = BybitClient(cfg)
        self.tg = TelegramNotifier(cfg, self.log)
        self.llm = LLMExplainer(cfg["openai_key"], cfg["llm_model"], enabled=cfg["enable_llm"])
        self.store = StateStore(cfg["state_db_path"])
        self.session_tz = resolve_session_tz(cfg.get("session_timezone", "UTC"))
        self.stats = self._load_day_stats()
        self.open_trades: dict[str, Trade] = {}
        self._ret_cache: dict[str, list[float]] = {}
        self._ret_cache_ts = 0.0
        self._rate_limit_hits = 0
        self._rate_limit_backoff_until = 0.0
        self._cycle_seq = 0
        self._load_local_state()
        self._notify_if_restarted_after_gap()
        self._recover_from_exchange()

    def _save_trade(self, t: Trade):
        self.store.save_trade(t)

    def _safe_save_trade(self, t: Trade):
        try:
            self._save_trade(t)
        except Exception:
            pass

    def _save_day_stats(self):
        self.store.save_day_stats(self.stats)

    def _load_day_stats(self) -> DayStats:
        today = datetime.now(self.session_tz).strftime("%Y-%m-%d")
        stats = self.store.load_day_stats()
        if not stats or stats.date != today:
            stats = DayStats(date=today)
            self.store.save_day_stats(stats)
        return stats

    def _set_state(self, t: Trade, new_state: str, reason: str, payload=None):
        prev = t.state
        t.state = new_state
        self._save_trade(t)
        if prev != new_state:
            self.store.add_event(t.id, t.symbol, prev, new_state, reason, payload or {})

    def _sizing_label(self) -> str:
        mode = normalize_sizing_mode(self.cfg.get("position_sizing_mode", "risk_pct"))
        if mode == "risk_usd":
            return f"risk_usd=${float(self.cfg.get('risk_per_trade_usd', 0) or 0):.4f}"
        if mode == "fixed_notional_usd":
            return f"fixed_notional=${float(self.cfg.get('target_notional_usd', 0) or 0):.4f}"
        if mode == "fixed_margin_usd":
            return (
                f"fixed_margin=${float(self.cfg.get('target_margin_usd', 0) or 0):.4f}"
                f"@{int(self.cfg.get('target_leverage', 1) or 1)}x"
            )
        return f"risk_pct={float(self.cfg.get('risk_per_trade_pct', 0) or 0):.4f}%"

    def _clear_critical_errors(self):
        current_errors = int(getattr(self.stats, "critical_errors", 0) or 0)
        current_reason = str(getattr(self.stats, "halt_reason", "") or "")
        if current_errors == 0 and not current_reason:
            return
        self.stats.critical_errors = 0
        self.stats.halt_reason = ""
        self._save_day_stats()

    def _notify_if_restarted_after_gap(self):
        runtime = self.store.load_runtime_value("runtime_heartbeat") or {}
        last_ts_raw = str(runtime.get("ts") or "").strip()
        if not last_ts_raw:
            return
        try:
            last_ts = datetime.fromisoformat(last_ts_raw)
        except Exception:
            return
        gap_sec = max((datetime.now(timezone.utc) - last_ts).total_seconds(), 0.0)
        warn_gap = max(int(self.cfg.get("cycle_sec", 900) or 900) * 2, 1800)
        if gap_sec < warn_gap:
            return
        gap_min = gap_sec / 60.0
        self.log.error(f"agent_restart_detected gap_min={gap_min:.1f} last_heartbeat={last_ts_raw}")
        self.tg.send_error(
            "agent_restart",
            f"Агент был перезапущен или недоступен примерно {gap_min:.1f} мин. "
            f"Последний heartbeat: {last_ts_raw}",
        )

    def _enter_safe_mode(self, reason: str, symbol: str = ""):
        self.stats.stopped = True
        self.stats.halt_reason = reason
        self._save_day_stats()
        suffix = f" symbol={symbol}" if symbol else ""
        self.log.error(f"safe_mode_entered reason={reason}{suffix}")
        self.tg.send_error("safe_mode", f"Агент переведен в safe mode: {reason}" + (f" ({symbol})" if symbol else ""))

    def _save_heartbeat(self):
        self.store.save_runtime_value("runtime_heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})

    def _record_rate_limit_hit(self, symbol: str, ex: Exception):
        now = time.time()
        if now >= self._rate_limit_backoff_until:
            self._rate_limit_hits = 0
        self._rate_limit_hits += 1
        self._rate_limit_backoff_until = now + max(int(self.cfg.get("cycle_sec", 900) or 900), 300)
        self.log.warning(
            f"rate_limit_backoff hits={self._rate_limit_hits} symbol={symbol} backoff_until={self._rate_limit_backoff_until:.0f}"
        )
        if self._rate_limit_hits == 1:
            self.tg.send_error(
                "rate_limit",
                f"Bybit rate-limit при анализе {symbol}. Временно сужаю сканирование и снижаю нагрузку.",
            )

    def _effective_scan_limit(self) -> int:
        base = max(int(self.cfg.get("max_scan_symbols", 20) or 20), 1)
        if time.time() < self._rate_limit_backoff_until:
            return max(5, base // 2)
        return base

    def _btc_filter(self, symbol: str, sig: dict, btc1h: dict | None, btc4h: dict | None) -> tuple[bool, str]:
        if symbol == "BTCUSDT":
            return True, ""
        if not btc1h or not btc4h:
            return True, ""
        from signals import btc_context_filter

        return btc_context_filter(symbol, sig, btc1h, btc4h)

    def _finalize_trade_analytics(self, t: Trade, closed_info: dict | None = None):
        if closed_info:
            if closed_info.get("pnl_usd") is not None:
                t.pnl_usd = float(closed_info.get("pnl_usd"))
            exit_price = float(closed_info.get("exit_price", 0) or 0)
            if exit_price > 0:
                t.close_price = exit_price
            fee = closed_info.get("total_fee_usd")
            if fee is not None:
                t.total_fee_usd = float(fee)
        initial_risk = abs(float(t.entry or 0.0) - float(t.sl or 0.0))
        if initial_risk > 0 and t.close_price:
            if t.direction == "LONG":
                realized_r = (float(t.close_price) - float(t.entry)) / initial_risk
            else:
                realized_r = (float(t.entry) - float(t.close_price)) / initial_risk
            t.realized_r = round(float(realized_r), 4)
        if t.open_time and t.close_time:
            try:
                open_dt = datetime.fromisoformat(t.open_time)
                close_dt = datetime.fromisoformat(t.close_time)
                t.hold_minutes = round(max((close_dt - open_dt).total_seconds(), 0.0) / 60.0, 2)
            except Exception:
                pass
        initial_risk = abs(float(t.entry or 0.0) - float(t.sl or 0.0))
        if initial_risk > 0 and t.open_time_ms:
            try:
                df = self.ex.klines(t.symbol, self.cfg["entry_tf"], limit=200)
                open_ts = t.open_time_ms
                close_dt = datetime.fromisoformat(t.close_time) if t.close_time else datetime.now(timezone.utc)
                close_ts = int(close_dt.timestamp() * 1000)
                window = df[(df["ts"].astype("int64") // 10**6 >= open_ts) & (df["ts"].astype("int64") // 10**6 <= close_ts)]
                if not window.empty:
                    high = float(window["high"].max())
                    low = float(window["low"].min())
                    if t.direction == "LONG":
                        t.mfe_r = round((high - float(t.entry)) / initial_risk, 4)
                        t.mae_r = round((float(t.entry) - low) / initial_risk, 4)
                    else:
                        t.mfe_r = round((float(t.entry) - low) / initial_risk, 4)
                        t.mae_r = round((high - float(t.entry)) / initial_risk, 4)
            except Exception:
                pass

        tags = []
        if t.realized_r is not None:
            if t.realized_r >= 2.0:
                tags.append("strong_winner")
            elif t.realized_r <= -1.0:
                tags.append("full_stop")
        if t.mfe_r is not None and t.realized_r is not None and t.mfe_r >= 2.0 and t.realized_r < 0.5:
            tags.append("gave_back_edge")
        if t.mae_r is not None and t.mae_r > 1.0:
            tags.append("deep_adverse_move")
        if t.hold_minutes is not None and t.hold_minutes < 30:
            tags.append("fast_resolution")
        t.review_tags = ",".join(tags)

        review_parts = []
        if t.realized_r is not None:
            if t.realized_r >= 1.0:
                review_parts.append("Сделка реализовала импульс в нашу сторону.")
            elif t.realized_r <= -0.8:
                review_parts.append("Идея быстро не подтвердилась и рынок пошел против входа.")
            else:
                review_parts.append("Сделка не раскрыла сильного преимущества после входа.")
        if t.mfe_r is not None and t.realized_r is not None and t.mfe_r >= 2.0 and t.realized_r < 0.5:
            review_parts.append("Часть потенциального движения была отдана обратно.")
        base_review = " ".join(review_parts[:2]).strip()
        llm_obj = getattr(self, "llm", None)
        llm_review = ""
        if llm_obj and hasattr(llm_obj, "review_trade"):
            llm_review = llm_obj.review_trade(
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "direction": t.direction,
                    "pnl_usd": t.pnl_usd,
                    "realized_r": t.realized_r,
                    "mfe_r": t.mfe_r,
                    "mae_r": t.mae_r,
                    "hold_minutes": t.hold_minutes,
                    "close_reason": t.close_reason,
                    "score": t.score,
                    "notes": t.notes,
                }
            )
        t.review_text = llm_review or base_review

    def _closed_trade_info(self, symbol: str, open_time_ms: int) -> dict | None:
        getter = getattr(self.ex, "closed_trade_info", None)
        if not callable(getter):
            return None
        try:
            return getter(symbol, open_time_ms)
        except Exception:
            return None

    def _record_critical_error(self, key: str, ex: Exception, symbol: str = ""):
        self.stats.critical_errors = int(getattr(self.stats, "critical_errors", 0) or 0) + 1
        self.stats.halt_reason = f"{key}: {ex}"
        self._save_day_stats()
        self.log.error(
            f"critical_error key={key} symbol={symbol or '-'} count={self.stats.critical_errors} err={ex}"
        )
        stop_count = int(self.cfg.get("critical_error_stop_count", 3) or 3)
        if self.stats.critical_errors >= stop_count:
            self.stats.stopped = True
            self._save_day_stats()
            self.log.error(
                f"critical_error_stop_triggered count={self.stats.critical_errors} reason={self.stats.halt_reason}"
            )
            self.tg.send_error(
                "critical_stop",
                f"Торговля остановлена: слишком много критичных ошибок ({self.stats.critical_errors}). "
                f"Последняя: {key} {symbol} {ex}",
            )

    def _load_local_state(self):
        for t in self.store.load_active_trades():
            self.open_trades[t.symbol] = t
        if self.open_trades:
            self.log.info(f"loaded_active_trades={len(self.open_trades)}")

    def _recover_from_exchange(self):
        try:
            positions = self.ex.list_positions()
        except Exception as ex:
            self.log.error(f"recovery_positions_failed={ex}")
            self.tg.send_error("recovery", f"Recovery positions failed: {ex}")
            return

        pos_map = {p.get("symbol"): p for p in positions if p.get("symbol")}

        for sym, t in list(self.open_trades.items()):
            pos = pos_map.get(sym)
            if pos:
                qty = float(pos.get("size", 0) or 0)
                t.qty = qty
                t.filled_qty = max(t.filled_qty, qty)
                if t.state != STATE_OPEN:
                    self._set_state(t, STATE_OPEN, "startup_recover_open")
                continue

            t.close_time = datetime.now(timezone.utc).isoformat()
            t.close_reason = "recovered_closed"
            t.pnl_usd = self.ex.realized_pnl_from_exchange(sym, t.open_time_ms)
            self._set_state(t, STATE_CLOSED, "startup_recover_closed", {"pnl": t.pnl_usd})
            self._set_state(t, STATE_RECONCILED, "startup_reconciled")
            self.stats.trades.append(t)
            self._save_day_stats()
            del self.open_trades[sym]

        for sym, pos in pos_map.items():
            if sym in self.open_trades:
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            now_ms = int(time.time() * 1000)
            side = str(pos.get("side", "Buy"))
            direction = "LONG" if side == "Buy" else "SHORT"
            avg_price = float(pos.get("avgPrice", 0) or 0)
            qty = float(pos.get("size", 0) or 0)
            protection = self.ex.position_protection(pos)
            recovered_sl = float(protection.get("sl", 0) or 0)
            recovered_tp = float(protection.get("tp", 0) or 0)
            t = Trade(
                id=f"REC_{sym}_{int(time.time())}",
                symbol=sym,
                direction=direction,
                strategy="recovered",
                entry=avg_price,
                sl=recovered_sl,
                tp=recovered_tp,
                size_usd=avg_price * qty,
                confidence=0,
                open_time=now_iso,
                order_id=None,
                state=STATE_OPEN,
                qty=qty,
                filled_qty=qty,
                open_time_ms=now_ms,
                notes="Recovered from exchange position",
            )
            self.open_trades[sym] = t
            self._save_trade(t)
            self.store.add_event(t.id, t.symbol, "", STATE_OPEN, "startup_found_exchange_position", {})
            self.log.warning(
                f"recovery_external_position symbol={sym} sl={recovered_sl:.6f} tp={recovered_tp:.6f}"
            )
            if recovered_sl <= 0 or recovered_tp <= 0:
                self._enter_safe_mode("recovered_position_without_full_protection", sym)

    def day_summary_text(self, title, open_positions):
        wins = [t for t in self.stats.trades if (t.pnl_usd or 0) > 0]
        losses = [t for t in self.stats.trades if (t.pnl_usd or 0) < 0]
        pnl = sum((t.pnl_usd or 0) for t in self.stats.trades)
        wr = len(wins) / len(self.stats.trades) * 100 if self.stats.trades else 0
        strategy_rows = _strategy_rows(self.stats.trades)
        skip_rows = _skip_rows(self.stats.skip_reasons)
        return (
            f"{title}\n"
            f"Дата: {self.stats.date} ({self.cfg.get('session_timezone', 'UTC')})\n"
            f"Сигналов: {self.stats.signals_total}\n"
            f"Скип: {self.stats.skipped}\n"
            f"Открыто: {self.stats.opened}\n"
            f"Закрыто: {len(self.stats.trades)}\n"
            f"Открытые позиции: {open_positions}\n"
            f"WIN: {len(wins)} LOSS: {len(losses)} WR: {wr:.1f}%\n"
            f"Итог PnL: {pnl:+.4f}$\n"
            + (f"Стратегии:\n{strategy_rows}\n" if strategy_rows else "")
            + (f"Skip summary:\n{skip_rows}" if skip_rows else "")
        )

    def _set_stop_cooldown(self, symbol: str):
        self.store.set_cooldown(symbol, self.cfg["symbol_cooldown_min"])

    def _in_symbol_cooldown(self, symbol: str) -> bool:
        return self.store.in_cooldown(symbol)

    async def day_reset(self):
        today = datetime.now(self.session_tz).strftime("%Y-%m-%d")
        if today != self.stats.date:
            open_before_close = len(self.open_trades)
            await self.close_all("new_day")
            report, path = make_report(
                self.stats,
                self.cfg["max_trades_per_day"],
                self.cfg.get("session_timezone", "UTC"),
                self.cfg.get("reports_dir"),
            )
            self.log.info(f"report_path={path}")
            print(report)
            self.tg.send(self.day_summary_text("Суточный отчёт агента", open_before_close), force=True)
            self.stats = DayStats(date=today)
            self._save_day_stats()

    def _manage_protection(self, t: Trade, pos: dict):
        if t.sl <= 0 or t.entry <= 0:
            return
        mark = float(pos.get("markPrice", 0) or 0)
        if mark <= 0:
            return
        r = abs(t.entry - t.sl)
        if r <= 0:
            return

        if t.direction == "LONG":
            move_r = (mark - t.entry) / r
            be_sl = t.entry
            tr_sl = mark - self.cfg["trailing_lock_r"] * r
            better = max(t.sl, be_sl if move_r >= self.cfg["be_trigger_r"] else t.sl)
            if move_r >= self.cfg["trailing_trigger_r"]:
                better = max(better, tr_sl)
        else:
            move_r = (t.entry - mark) / r
            be_sl = t.entry
            tr_sl = mark + self.cfg["trailing_lock_r"] * r
            better = min(t.sl, be_sl if move_r >= self.cfg["be_trigger_r"] else t.sl)
            if move_r >= self.cfg["trailing_trigger_r"]:
                better = min(better, tr_sl)

        if t.direction == "LONG" and better > t.sl * 1.000001:
            t.sl = better
            self._save_trade(t)
            if not self.cfg["testnet"]:
                try:
                    self.ex.update_stop_loss(t.symbol, t.sl)
                except Exception as ex:
                    self.log.error(f"update_stop_loss_failed symbol={t.symbol} err={ex}")
        if t.direction == "SHORT" and better < t.sl * 0.999999:
            t.sl = better
            self._save_trade(t)
            if not self.cfg["testnet"]:
                try:
                    self.ex.update_stop_loss(t.symbol, t.sl)
                except Exception as ex:
                    self.log.error(f"update_stop_loss_failed symbol={t.symbol} err={ex}")

    def _handle_order_state_without_position(self, t: Trade, order_state: dict) -> bool:
        status = str(order_state.get("status", "UNKNOWN"))
        raw_status = str(order_state.get("raw_status", status))
        filled_qty = float(order_state.get("filled_qty", 0.0) or 0.0)
        avg_price = float(order_state.get("avg_price", 0.0) or 0.0)
        reject_reason = str(order_state.get("reject_reason", "") or "")

        if status == "PENDING":
            self.log.info(f"order_pending symbol={t.symbol} order_id={t.order_id} raw_status={raw_status}")
            return False

        if filled_qty > 0:
            t.filled_qty = max(t.filled_qty, filled_qty)
        if avg_price > 0:
            t.entry = avg_price
            self._save_trade(t)

        if status == "FILLED":
            self._set_state(t, STATE_FILLED, "order_history_filled", {"raw_status": raw_status, "filled_qty": filled_qty})
            return False

        if status == "PARTIALLY_FILLED":
            self._set_state(
                t,
                STATE_FILLED,
                "order_history_partially_filled",
                {"raw_status": raw_status, "filled_qty": filled_qty},
            )
            return False

        if status in {"CANCELLED", "REJECTED", "PARTIALLY_FILLED_CANCELED"}:
            reason = f"order_{status.lower()}"
            t.close_time = datetime.now(timezone.utc).isoformat()
            t.close_reason = reason
            if filled_qty <= 0:
                self._set_state(
                    t,
                    STATE_RECONCILED,
                    reason,
                    {"raw_status": raw_status, "reject_reason": reject_reason},
                )
                self.tg.send(
                    f"Ордер не открыл позицию: {t.symbol} {t.direction}\n"
                    f"Статус: {raw_status}\n"
                    + (f"Причина: {reject_reason}" if reject_reason else ""),
                    force=True,
                )
                return True

            self._set_state(
                t,
                STATE_FILLED,
                "order_terminal_with_fill",
                {"raw_status": raw_status, "filled_qty": filled_qty},
            )
            return False

        self.log.warning(
            f"order_state_unknown symbol={t.symbol} order_id={t.order_id} status={status} raw_status={raw_status}"
        )
        return False

    def _refresh_trade_from_position(self, t: Trade, pos: dict, prev_open_qty: float) -> tuple[float, float]:
        pos_qty = float(pos.get("size", 0) or 0)
        avg_price = float(pos.get("avgPrice", 0) or 0)
        if avg_price > 0:
            t.entry = avg_price

        max_filled_qty = max(float(t.filled_qty or 0.0), float(prev_open_qty or 0.0), pos_qty)
        t.qty = pos_qty
        t.filled_qty = max_filled_qty
        t.size_usd = round(t.entry * pos_qty, 6) if t.entry > 0 and pos_qty > 0 else t.size_usd

        if prev_open_qty > 0 and pos_qty > 0 and pos_qty < prev_open_qty and t.risk_usd > 0:
            current_risk = float(t.risk_usd) * (pos_qty / prev_open_qty)
            t.risk_usd = round(max(current_risk, 0.0), 6)

        return pos_qty, max_filled_qty

    async def _wait_until_position_closed(self, symbol: str) -> tuple[bool, dict | None]:
        retries = max(int(self.cfg.get("close_verify_retries", 6) or 0), 1)
        delay_ms = max(int(self.cfg.get("close_verify_delay_ms", 500) or 0), 0)
        pos = None
        for _ in range(retries):
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)
            pos = self.ex.get_pos(symbol)
            if not pos:
                return True, None
        return False, pos

    async def close_all(self, reason):
        for sym, t in list(self.open_trades.items()):
            try:
                pos = self.ex.get_pos(sym)
                if pos:
                    qty = float(pos.get("size", 0) or 0)
                    close_mark = float(pos.get("markPrice", t.entry) or t.entry)
                    self.ex.close_pos(sym, t.direction, qty)
                    closed, remaining_pos = await self._wait_until_position_closed(sym)
                    if not closed:
                        remain_qty = float((remaining_pos or {}).get("size", 0) or 0)
                        self.log.warning(
                            f"close_all_unconfirmed symbol={sym} reason={reason} requested_qty={qty} remaining_qty={remain_qty}"
                        )
                        self.tg.send_error(
                            f"close_all_pending_{sym}",
                            f"Позиция еще не подтверждена как закрытая: {sym} {t.direction} "
                            f"(осталось {remain_qty})",
                        )
                        self._record_critical_error("close_all_unconfirmed", RuntimeError(f"remaining_qty={remain_qty}"), sym)
                        continue
                    t.close_price = float(pos.get("markPrice", t.entry) or t.entry)
                    t.close_price = close_mark
                    t.close_time = datetime.now(timezone.utc).isoformat()
                    t.close_reason = reason
                    closed_info = self._closed_trade_info(sym, t.open_time_ms)
                    t.pnl_usd = (closed_info or {}).get("pnl_usd", self.ex.realized_pnl_from_exchange(sym, t.open_time_ms))
                    self._finalize_trade_analytics(t, closed_info)
                    self._safe_save_trade(t)
                    self.stats.trades.append(t)
                    self._save_day_stats()
                    self._set_state(t, STATE_CLOSED, "close_all", {"reason": reason, "pnl": t.pnl_usd})
                    self._set_state(t, STATE_RECONCILED, "close_all_reconciled")
                    self.tg.send(
                        f"Закрыта позиция {sym} {t.direction}\nПричина: {reason}\nPnL: {(t.pnl_usd or 0):+.4f}$",
                        force=True,
                    )
                    self._clear_critical_errors()
                else:
                    self._set_state(t, STATE_RECONCILED, "close_all_no_position")
                del self.open_trades[sym]
            except Exception as ex:
                self.log.error(f"close_all_failed symbol={sym} err={ex}")
                self.tg.send_error("close_all", f"close_all {sym}: {ex}")
                self._record_critical_error("close_all_failed", ex, sym)

    async def sync_trades(self):
        for sym, t in list(self.open_trades.items()):
            try:
                pos = self.ex.get_pos(sym)
                if pos:
                    prev_open_qty = float(t.qty or 0.0)
                    prev_qty = max(prev_open_qty, float(t.filled_qty or 0.0))
                    pos_qty, max_filled_qty = self._refresh_trade_from_position(t, pos, prev_open_qty)
                    if t.state in (STATE_SIGNALLED, STATE_ORDER_SENT):
                        self._set_state(t, STATE_FILLED, "sync_position_detected")
                        self._set_state(t, STATE_OPEN, "sync_position_open")
                        if 0 < pos_qty < prev_qty:
                            self.log.warning(
                                f"partial_entry_detected symbol={sym} requested_qty={prev_qty} filled_qty={pos_qty}"
                            )
                    elif prev_qty > 0 and 0 < pos_qty < prev_qty:
                        was_partial = t.state == STATE_PARTIALLY_CLOSED
                        partial_ratio = pos_qty / max_filled_qty if max_filled_qty > 0 else 0.0
                        if was_partial:
                            self._save_trade(t)
                        else:
                            self._set_state(t, STATE_PARTIALLY_CLOSED, "sync_partial_close")
                        if not was_partial:
                            self.tg.send(
                                f"Частичное закрытие: {sym} {t.direction}\n"
                                f"Осталось qty: {pos_qty}\n"
                                f"Доля позиции: {partial_ratio:.2%}",
                                force=True,
                            )
                    else:
                        self._save_trade(t)
                    self._manage_protection(t, pos)
                    self._clear_critical_errors()
                    continue

                if t.state == STATE_ORDER_SENT and t.order_id:
                    order_state = self.ex.get_order_state(sym, t.order_id)
                    if order_state:
                        remove_trade = self._handle_order_state_without_position(t, order_state)
                        if remove_trade:
                            del self.open_trades[sym]
                            self._save_day_stats()
                            self._clear_critical_errors()
                            continue

                if t.state in ACTIVE_STATES:
                    t.close_time = datetime.now(timezone.utc).isoformat()
                    t.close_reason = "SL/TP/Manual"
                    closed_info = self._closed_trade_info(sym, t.open_time_ms)
                    t.pnl_usd = (closed_info or {}).get("pnl_usd", self.ex.realized_pnl_from_exchange(sym, t.open_time_ms))
                    if t.pnl_usd is None:
                        self.log.warning(f"pnl_unresolved symbol={sym} open_time_ms={t.open_time_ms}")
                    self._finalize_trade_analytics(t, closed_info)
                    self._safe_save_trade(t)
                    self.stats.trades.append(t)
                    self._set_state(t, STATE_CLOSED, "sync_closed", {"pnl": t.pnl_usd})
                    self._set_state(t, STATE_RECONCILED, "sync_reconciled")
                    loss = (t.pnl_usd or 0) < 0
                    self.stats.consecutive_losses = self.stats.consecutive_losses + 1 if loss else 0
                    if loss:
                        self._set_stop_cooldown(sym)
                    self._save_day_stats()
                    self.tg.send(
                        f"Позиция закрыта: {sym} {t.direction}\nStrategy: {t.strategy}\nPnL: {(t.pnl_usd or 0):+.4f}$",
                        force=True,
                    )
                    del self.open_trades[sym]
                    self._clear_critical_errors()
            except Exception as ex:
                self.log.error(f"sync_trade_failed symbol={sym} err={ex}")
                self.tg.send_error("sync", f"sync {sym}: {ex}")
                self._record_critical_error("sync_trade_failed", ex, sym)

    def _symbol_returns(self, symbol: str) -> list[float]:
        now = time.time()
        if (now - self._ret_cache_ts) > 30:
            self._ret_cache = {}
            self._ret_cache_ts = now
        if symbol in self._ret_cache:
            return self._ret_cache[symbol]
        try:
            df = self.ex.klines(symbol, "60", limit=self.cfg["correlation_lookback"])
            series = returns_from_prices(df["close"].tolist())
        except Exception:
            series = []
        self._ret_cache[symbol] = series
        return series

    def _candidate_llm_rank(self, candidates: list[dict]) -> list[dict]:
        ok_rank, rank_reason = should_use_llm_rank(candidates, self.cfg)
        if not ok_rank:
            if candidates:
                self.log.info(
                    f"llm_rank_skip reason={rank_reason} top_score={candidates[0]['score']} candidates={len(candidates)}"
                )
            return candidates
        top_n = max(2, int(self.cfg["llm_rank_top_n"]))
        head = candidates[:top_n]
        tail = candidates[top_n:]
        self.log.info(
            "llm_rank_apply "
            f"top_n={top_n} scores={[int(x.get('score', 0) or 0) for x in head]} "
            f"symbols={[x.get('sym') for x in head]}"
        )
        ranked = self.llm.rank_candidates(head)
        return ranked + tail

    def _log_skip(self, symbol: str, reason: str, extra: str = ""):
        key = str(reason or "unknown_skip")
        self.stats.skip_reasons[key] = int(self.stats.skip_reasons.get(key, 0) or 0) + 1
        self._save_day_stats()
        suffix = f" {extra}" if extra else ""
        self.log.info(f"skip symbol={symbol} reason={reason}{suffix}")

    def _log_candidate(self, item: dict):
        self.log.info(
            "candidate "
            f"symbol={item['sym']} direction={item['sig']['direction']} strategy={item['sig']['strategy']} "
            f"score={item['score']} rr={item.get('rr', 0.0):.3f} funding={float(item.get('fr', 0.0) or 0.0):.6f} "
            f"oi_change_pct={float(item.get('oi', {}).get('change_pct', 0.0) or 0.0):+.3f} "
            f"reasons={summarize_reasons(item.get('reasons', []), limit=5)}"
        )

    def _log_candidate_pool(self, candidates: list[dict]):
        if not candidates:
            return
        preview = " | ".join(
            f"{x['sym']}:{x['score']}/rr{x.get('rr', 0.0):.2f}/{summarize_reasons(x.get('reasons', []), limit=3)}"
            for x in candidates[:5]
        )
        self.log.info(f"candidate_pool count={len(candidates)} top={preview}")

    def _log_cycle_summary(self, label: str, started_skipped: int, candidates_count: int = 0, summary: dict | None = None):
        skipped_delta = int(self.stats.skipped) - int(started_skipped)
        extra = ""
        if summary:
            extra = (
                f" status={label}"
                f" scanned={int(summary.get('scanned', 0))}"
                f" no_signal={int(summary.get('no_signal', 0))}"
                f" skips={int(summary.get('skips', 0))}"
                f" candidates={int(candidates_count)}"
                f" opened={int(summary.get('opened', 0))}"
                f" errors={int(summary.get('errors', 0))}"
            )
        self.log.info(
            f"cycle_id={self._cycle_seq} summary open_trades={len(self.open_trades)} closed_trades={len(self.stats.trades)} "
            f"signals_total={self.stats.signals_total} skipped_delta={skipped_delta}{extra}"
        )

    async def cycle(self):
        self._cycle_seq += 1
        cycle_started_skipped = int(self.stats.skipped)
        cycle_summary = {"scanned": 0, "no_signal": 0, "skips": 0, "opened": 0, "errors": 0}
        self.log.info(
            f"cycle_id={self._cycle_seq} cycle_start open_trades={len(self.open_trades)} closed_trades={len(self.stats.trades)} "
            f"signals_total={self.stats.signals_total} stopped={self.stats.stopped}"
        )
        now = datetime.now(timezone.utc)
        if (
            self.cfg.get("enable_eod_close", True)
            and now.hour == self.cfg["close_hour_utc"]
            and now.minute >= self.cfg["close_min_utc"]
        ):
            self.log.info(f"cycle_id={self._cycle_seq} cycle_stage=eod_close")
            await self.close_all("eod_close")
            return

        self.log.info(f"cycle_id={self._cycle_seq} cycle_stage=sync_trades")
        await self.sync_trades()
        if self.stats.stopped:
            self.log.warning(f"cycle_id={self._cycle_seq} why_not_trading=stats_stopped halt_reason={self.stats.halt_reason}")
            self.log.warning(f"trading_stopped_for_day=true halt_reason={self.stats.halt_reason}")
            return
        if self.stats.consecutive_losses >= self.cfg["stop_after_losses"]:
            self.stats.stopped = True
            self.stats.halt_reason = "stop_after_losses"
            self._save_day_stats()
            self.log.warning(f"cycle_id={self._cycle_seq} why_not_trading=stop_after_losses consecutive_losses={self.stats.consecutive_losses}")
            self.log.warning("stop_after_losses_triggered=true")
            return

        done = len(self.stats.trades) + len(self.open_trades)
        if done >= self.cfg["max_trades_per_day"]:
            self.log.info(f"cycle_id={self._cycle_seq} why_not_trading=max_trades_per_day done={done} limit={self.cfg['max_trades_per_day']}")
            self.log.info(f"cycle_blocked=max_trades_per_day done={done} limit={self.cfg['max_trades_per_day']}")
            return

        symbols = screen_coins(self.ex, self.cfg)[: self._effective_scan_limit()]
        self.log.info(
            f"cycle_id={self._cycle_seq} cycle_stage=scan_universe symbols={len(symbols)} max_scan_symbols={self.cfg.get('max_scan_symbols', 20)} "
            f"effective_limit={self._effective_scan_limit()}"
        )
        btc1h = None
        btc4h = None
        try:
            btc1h = calc_indicators(self.ex.klines("BTCUSDT", self.cfg["regime_tf_1"]))
            btc4h = calc_indicators(self.ex.klines("BTCUSDT", self.cfg["regime_tf_2"]))
        except Exception as ex:
            self.log.warning(f"btc_context_unavailable err={ex}")
        candidates = []

        for idx, sym in enumerate(symbols, 1):
            cycle_summary["scanned"] += 1
            self.log.info(f"cycle_id={self._cycle_seq} analyze_symbol_start symbol={sym} progress={idx}/{len(symbols)}")
            if sym in self.open_trades:
                self.log.info(f"cycle_id={self._cycle_seq} analyze_symbol_skip symbol={sym} reason=already_open")
                cycle_summary["skips"] += 1
                continue
            if self._in_symbol_cooldown(sym):
                self.stats.skipped += 1
                self._save_day_stats()
                self._log_skip(sym, "symbol_cooldown")
                cycle_summary["skips"] += 1
                continue
            try:
                df = self.ex.klines(sym, self.cfg["entry_tf"])
                ind = calc_indicators(df)
                if not ind.get("atr") or ind["atr"] / ind["price"] < self.cfg["min_atr_ratio"]:
                    self.log.info(
                        f"cycle_id={self._cycle_seq} analyze_symbol_skip symbol={sym} reason=min_atr_ratio "
                        f"atr_ratio={(float(ind.get('atr') or 0.0) / max(float(ind.get('price') or 1.0), 1e-9)):.4f} "
                        f"min_atr_ratio={self.cfg['min_atr_ratio']}"
                    )
                    cycle_summary["skips"] += 1
                    continue

                fmeta = self.ex.funding_meta(sym)
                fr = fmeta["rate"]
                if in_funding_block(fmeta.get("next_funding_ms", 0), self.cfg["funding_block_minutes"]):
                    self.stats.skipped += 1
                    self._save_day_stats()
                    cycle_summary["skips"] += 1
                    continue

                oi = self.ex.open_interest(sym)
                if oi_spike_block(oi.get("change_pct", 0), self.cfg["oi_spike_block_pct"]):
                    self.stats.skipped += 1
                    self._save_day_stats()
                    cycle_summary["skips"] += 1
                    continue

                ind1h = calc_indicators(self.ex.klines(sym, self.cfg["regime_tf_1"]))
                ind4h = calc_indicators(self.ex.klines(sym, self.cfg["regime_tf_2"]))

                sigs = detect_signals(ind, fr)
                self.stats.signals_total += len(sigs)
                self._save_day_stats()
                self.log.info(f"cycle_id={self._cycle_seq} analyze_symbol_signals symbol={sym} signals={len(sigs)}")
                if not sigs:
                    self.log.info(f"cycle_id={self._cycle_seq} analyze_symbol_done symbol={sym} status=no_signal")
                    cycle_summary["no_signal"] += 1
                    await asyncio.sleep(0.12)
                    continue
                for s in sigs:
                    signal_rr = rr_ratio(float(s["entry"]), float(s["sl"]), float(s["tp"]), s["direction"])
                    if signal_rr < float(self.cfg.get("min_rr_ratio", 0) or 0):
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            "min_rr_ratio",
                            f"rr={signal_rr:.4f} min_rr_ratio={self.cfg.get('min_rr_ratio')} "
                            f"strategy={s['strategy']} direction={s['direction']}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    ok_mid, mid_reason = no_middle_range(s, ind, self.cfg["range_mid_avoid_pct"])
                    if not ok_mid:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            mid_reason,
                            f"strategy={s['strategy']} direction={s['direction']} rr={signal_rr:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    ok_quality, quality_reason = signal_quality_filter(s, ind, self.cfg, oi.get("change_pct", 0))
                    if not ok_quality:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            quality_reason,
                            f"strategy={s['strategy']} direction={s['direction']} rr={signal_rr:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    ok_btc, btc_reason = self._btc_filter(sym, s, btc1h, btc4h)
                    if not ok_btc:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            btc_reason,
                            f"strategy={s['strategy']} direction={s['direction']} rr={signal_rr:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    edge = edge_after_costs(s, self.cfg, fr)
                    min_edge_cost_ratio = float(self.cfg.get("min_edge_cost_ratio", 0) or 0)
                    min_net_reward_pct = float(self.cfg.get("min_net_reward_pct", 0) or 0)
                    if edge["net_reward_per_unit"] <= 0:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            "net_edge<=0",
                            f"net_reward={edge['net_reward_per_unit']:.6f} cost={edge['cost_per_unit']:.6f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    if min_edge_cost_ratio > 0 and edge["edge_cost_ratio"] < min_edge_cost_ratio:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            "edge_cost_ratio",
                            f"ratio={edge['edge_cost_ratio']:.4f} min={min_edge_cost_ratio:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    if min_net_reward_pct > 0 and edge["net_reward_pct"] < min_net_reward_pct:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            "net_reward_pct",
                            f"net_reward_pct={edge['net_reward_pct']:.4f} min={min_net_reward_pct:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    ok_regime, reg_reason = regime_filter(ind1h, ind4h, s["direction"], s["strategy"])
                    if not ok_regime:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            reg_reason or "regime_filter",
                            f"strategy={s['strategy']} direction={s['direction']} rr={signal_rr:.4f}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    score, reasons = score_signal(s, ind, fr, oi.get("change_pct", 0), ok_regime)
                    if reg_reason:
                        reasons.append(reg_reason)
                    if edge["edge_cost_ratio"] >= (min_edge_cost_ratio + 1.0):
                        reasons.append("edge_after_costs_strong")
                    elif edge["edge_cost_ratio"] >= min_edge_cost_ratio:
                        reasons.append("edge_after_costs_ok")
                    if score < self.cfg["min_rule_score"]:
                        self.stats.skipped += 1
                        self._save_day_stats()
                        self._log_skip(
                            sym,
                            "min_rule_score",
                            f"score={score} min_rule_score={self.cfg['min_rule_score']} "
                            f"rr={signal_rr:.4f} reasons={summarize_reasons(reasons, limit=5)}",
                        )
                        cycle_summary["skips"] += 1
                        continue
                    item = {
                        "sym": sym,
                        "sig": s,
                        "ind": ind,
                        "fr": fr,
                        "oi": oi,
                        "score": score,
                        "rr": signal_rr,
                        "edge": edge,
                        "reasons": reasons,
                    }
                    candidates.append(item)
                    self._log_candidate(item)
                self.log.info(f"cycle_id={self._cycle_seq} analyze_symbol_done symbol={sym} status=ok candidates_total={len(candidates)}")
                await asyncio.sleep(0.12)
            except Exception as ex:
                self.log.error(f"analyze_failed symbol={sym} err={ex}")
                self.tg.send_error("analysis", f"Анализ {sym}: {ex}")
                cycle_summary["errors"] += 1
                if "10006" in str(ex) or "x-bapi-limit-reset-timestamp" in str(ex):
                    self._record_rate_limit_hit(sym, ex)
                    if self._rate_limit_hits >= 2:
                        self.log.warning("scan_cycle_cut_short_due_rate_limit=true")
                        break

        if not candidates:
            self._log_cycle_summary("cycle_end_no_candidates", cycle_started_skipped, 0, cycle_summary)
            return

        candidates.sort(key=lambda x: x["score"], reverse=True)
        self._log_candidate_pool(candidates)
        candidates = self._candidate_llm_rank(candidates)
        slots = self.cfg["max_trades_per_day"] - done

        for item in candidates[: slots + 3]:
            if len(self.open_trades) + len(self.stats.trades) >= self.cfg["max_trades_per_day"]:
                break

            sym = item["sym"]
            if sym in self.open_trades:
                continue

            try:
                s = item["sig"]
                direction = s["direction"]
                wallet = self.ex.wallet_snapshot()
                limits = self.ex.instrument_constraints(sym)
                sizing, reject_reason = size_position(
                    self.cfg,
                    direction,
                    float(s["entry"]),
                    float(s["sl"]),
                    item["fr"],
                    wallet,
                    limits,
                    strategy=s["strategy"],
                    score=item["score"],
                    atr_ratio=(float(item["ind"].get("atr", 0.0) or 0.0) / max(float(item["ind"].get("price", 1.0) or 1.0), 1e-9)),
                )
                if not sizing:
                    self.stats.skipped += 1
                    self._save_day_stats()
                    self._log_skip(sym, reject_reason, f"score={item['score']} rr={item.get('rr', 0.0):.4f}")
                    cycle_summary["skips"] += 1
                    continue
                item["sizing"] = sizing

                ok_side, side_reason = check_side_exposure(
                    self.cfg,
                    self.open_trades,
                    direction,
                    sizing["risk_usd"],
                    float(wallet.get("equity", 0) or 0),
                )
                if not ok_side:
                    self.stats.skipped += 1
                    self._save_day_stats()
                    self._log_skip(
                        sym,
                        side_reason,
                        f"risk_usd={float(sizing['risk_usd']):.4f} direction={direction}",
                    )
                    cycle_summary["skips"] += 1
                    continue

                return_series = {sym: self._symbol_returns(sym)}
                for t in self.open_trades.values():
                    if t.direction == direction:
                        return_series[t.symbol] = self._symbol_returns(t.symbol)
                ok_corr, corr_reason = correlation_allowed(
                    symbol=sym,
                    direction=direction,
                    open_trades=self.open_trades,
                    return_series=return_series,
                    threshold=float(self.cfg["correlation_threshold"]),
                    max_correlated_per_side=int(self.cfg["max_correlated_positions_per_side"]),
                )
                if not ok_corr:
                    self.stats.skipped += 1
                    self._save_day_stats()
                    self._log_skip(sym, corr_reason, f"direction={direction} score={item['score']}")
                    cycle_summary["skips"] += 1
                    continue

                tp = round_to_step(float(s["tp"]), float(limits.get("tick_size", 0) or 0))
                sl, tp = align_protective_prices(
                    entry=float(sizing["entry"]),
                    sl=float(sizing["sl"]),
                    tp=float(tp),
                    direction=direction,
                    tick_size=float(limits.get("tick_size", 0) or 0),
                )
                post_align_rr = rr_ratio(float(sizing["entry"]), float(sl), float(tp), direction)
                if post_align_rr < float(self.cfg.get("min_rr_ratio", 0) or 0):
                    self.stats.skipped += 1
                    self._save_day_stats()
                    self._log_skip(
                        sym,
                        "rr_after_tick_align",
                        f"rr={post_align_rr:.4f} min_rr_ratio={self.cfg.get('min_rr_ratio')}",
                    )
                    cycle_summary["skips"] += 1
                    continue
                now_iso = datetime.now(timezone.utc).isoformat()
                now_ms = int(time.time() * 1000)
                t = Trade(
                    id=f"{sym}_{int(time.time())}",
                    symbol=sym,
                    direction=direction,
                    strategy=s["strategy"],
                    entry=sizing["entry"],
                    sl=sl,
                    tp=tp,
                    size_usd=sizing["notional"],
                    confidence=100,
                    open_time=now_iso,
                    order_id=None,
                    state=STATE_SIGNALLED,
                    qty=sizing["qty"],
                    risk_usd=sizing["risk_usd"],
                    open_time_ms=now_ms,
                    score=item["score"],
                    notes=", ".join(item["reasons"][:6]),
                )
                tick = self.ex.ticker(sym)
                ref_price = float(tick.get("markPrice") or tick.get("lastPrice") or 0.0)
                qty_final_str = normalize_order_qty(
                    t.qty,
                    float(limits.get("qty_step", 0.0) or 0.0),
                    float(limits.get("min_qty", 0.0) or 0.0),
                    float(limits.get("min_notional", 0.0) or 0.0),
                    ref_price,
                )
                qty_raw = t.qty
                t.qty = float(qty_final_str)
                self.log.info(
                    f"order_qty_prepare symbol={sym} qty_raw={qty_raw} qty_final={qty_final_str} "
                    f"qty_step={limits.get('qty_step')} min_qty={limits.get('min_qty')} "
                    f"min_notional={limits.get('min_notional')} ref_price={ref_price}"
                )
                self.open_trades[sym] = t
                self._save_trade(t)
                self._set_state(t, STATE_ORDER_SENT, "order_sending", {"score": item["score"]})

                if self.cfg["testnet"]:
                    t.order_id = f"TEST_{sym}_{int(time.time())}"
                    t.filled_qty = t.qty
                    self._set_state(t, STATE_FILLED, "testnet_filled")
                    self._set_state(t, STATE_OPEN, "testnet_open")
                else:
                    oid = self.ex.place_order(
                        symbol=sym,
                        direction=direction,
                        qty=t.qty,
                        sl=t.sl,
                        tp=t.tp,
                        leverage=sizing["leverage"],
                    )
                    t.order_id = oid
                    self._save_trade(t)

                self.stats.opened += 1
                self._save_day_stats()
                cycle_summary["opened"] += 1
                explain = self.llm.explain(
                    context={
                        "symbol": sym,
                        "score": item["score"],
                        "strategy": t.strategy,
                        "direction": direction,
                        "rr": round(float(item.get("rr", 0.0) or 0.0), 3),
                        "risk_usd": round(float(sizing["risk_usd"] or 0.0), 4),
                        "reasons": item["reasons"][:5],
                    },
                    decision="TRADE",
                    reasons=item["reasons"],
                )
                self.log.info(
                    f"opened symbol={sym} direction={direction} qty={t.qty} lev={sizing['leverage']} "
                    f"target_lev={sizing.get('target_leverage', sizing['leverage'])} "
                    f"strategy={t.strategy} atr_ratio={(float(item['ind'].get('atr', 0.0) or 0.0) / max(float(item['ind'].get('price', 1.0) or 1.0), 1e-9)):.4f} "
                    f"risk={sizing['risk_usd']:.4f} score={item['score']} rr={item.get('rr', 0.0):.3f} "
                    f"reasons={summarize_reasons(item['reasons'], limit=4)}"
                )
                self.tg.send(
                    f"Открыта позиция {sym} {direction}\n"
                    f"Score: {item['score']} | Qty: {t.qty} | Lev: {sizing['leverage']}x\n"
                    f"Risk: {sizing['risk_usd']:.4f}$ | Entry: {t.entry} | SL: {t.sl} | TP: {t.tp}\n"
                    + (f"LLM: {explain}" if explain else ""),
                    force=True,
                )
            except Exception as ex:
                self.log.error(f"open_failed symbol={sym} err={ex}")
                self.tg.send_error("open_trade", f"Открытие {sym}: {ex}")
                bad = self.open_trades.pop(sym, None)
                if bad:
                    self._set_state(bad, STATE_RECONCILED, "order_send_failed", {"error": str(ex)})
                cycle_summary["errors"] += 1
        self._log_cycle_summary("cycle_end", cycle_started_skipped, len(candidates), cycle_summary)

    async def run(self):
        mode = "TESTNET" if self.cfg["testnet"] else "*** REAL MONEY ***"
        self.log.info(f"agent_start mode={mode} model={self.cfg['llm_model']} sizing={self._sizing_label()}")
        self.tg.send(
            f"Агент запущен\nРежим: {mode}\nМодель: {self.cfg['llm_model']}\n"
            f"Sizing: {self._sizing_label()} | Max/day: {self.cfg['max_trades_per_day']}",
            force=True,
        )
        self._save_heartbeat()
        while True:
            try:
                await self.day_reset()
                await self.cycle()
            except KeyboardInterrupt:
                open_before_close = len(self.open_trades)
                await self.close_all("stopped")
                report, _ = make_report(
                    self.stats,
                    self.cfg["max_trades_per_day"],
                    self.cfg.get("session_timezone", "UTC"),
                    self.cfg.get("reports_dir"),
                )
                print(report)
                self.tg.send(self.day_summary_text("Агент остановлен. Итог дня", open_before_close), force=True)
                break
            except Exception as ex:
                self.log.error(f"cycle_failed err={ex}")
                self.tg.send_error("cycle", f"Цикл: {ex}")
                self._record_critical_error("cycle_failed", ex)
            self._save_heartbeat()
            self.log.info(f"sleep_sec={self.cfg['cycle_sec']}")
            await asyncio.sleep(self.cfg["cycle_sec"])
