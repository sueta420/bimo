import asyncio
import time
from datetime import datetime, timezone

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
from risk import check_side_exposure, correlation_allowed, normalize_sizing_mode, returns_from_prices, round_to_step, size_position
from signals import (
    calc_indicators,
    detect_signals,
    in_funding_block,
    no_middle_range,
    oi_spike_block,
    regime_filter,
    score_signal,
    screen_coins,
)


def make_report(stats: DayStats, max_trades_per_day: int) -> tuple[str, str]:
    wins = [t for t in stats.trades if (t.pnl_usd or 0) > 0]
    losses = [t for t in stats.trades if (t.pnl_usd or 0) < 0]
    pnl = sum((t.pnl_usd or 0) for t in stats.trades)
    wr = len(wins) / len(stats.trades) * 100 if stats.trades else 0
    closed = len(stats.trades)
    lines = []
    for i, t in enumerate(stats.trades, 1):
        icon = "WIN" if (t.pnl_usd or 0) > 0 else ("LOSS" if (t.pnl_usd or 0) < 0 else "BE")
        lines.append(
            f"  #{i}[{icon}] {t.symbol} {t.direction} {t.strategy} "
            f"PnL:{(t.pnl_usd or 0):+.4f}$ score:{t.score} {t.close_reason}"
        )
    report = (
        f"\n{'=' * 52}\n  ОТЧЁТ {stats.date}\n{'=' * 52}\n"
        f"  Сделок:{closed}/{max_trades_per_day} WIN:{len(wins)} LOSS:{len(losses)}\n"
        f"  Винрейт:{wr:.1f}% | PnL:{pnl:+.4f}$\n"
        f"  Сигналов:{stats.signals_total} | Скип:{stats.skipped} | Открыто:{stats.opened} | Закрыто:{closed}\n"
        f"{'─' * 52}\n"
        + ("\n".join(lines) if lines else "  Сделок не было")
        + f"\n{'=' * 52}\n"
    )
    path = f"report_{stats.date.replace('-', '')}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return report, path


class Agent:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.ex = BybitClient(cfg)
        self.tg = TelegramNotifier(cfg, self.log)
        self.llm = LLMExplainer(cfg["openai_key"], cfg["llm_model"], enabled=cfg["enable_llm"])
        self.store = StateStore(cfg["state_db_path"])
        self.stats = DayStats(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.open_trades: dict[str, Trade] = {}
        self._ret_cache: dict[str, list[float]] = {}
        self._ret_cache_ts = 0.0
        self._load_local_state()
        self._recover_from_exchange()

    def _save_trade(self, t: Trade):
        self.store.save_trade(t)

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
        return f"risk_pct={float(self.cfg.get('risk_per_trade_pct', 0) or 0):.4f}%"

    def _side_risk_guard_label(self) -> str:
        return "disabled" if self.cfg.get("disable_side_risk_guard", False) else "enabled"

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
            t = Trade(
                id=f"REC_{sym}_{int(time.time())}",
                symbol=sym,
                direction=direction,
                strategy="recovered",
                entry=avg_price,
                sl=0.0,
                tp=0.0,
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
            self.log.warning(f"recovery_external_position symbol={sym}")

    def day_summary_text(self, title, open_positions):
        wins = [t for t in self.stats.trades if (t.pnl_usd or 0) > 0]
        losses = [t for t in self.stats.trades if (t.pnl_usd or 0) < 0]
        pnl = sum((t.pnl_usd or 0) for t in self.stats.trades)
        wr = len(wins) / len(self.stats.trades) * 100 if self.stats.trades else 0
        return (
            f"{title}\n"
            f"Дата: {self.stats.date}\n"
            f"Сигналов: {self.stats.signals_total}\n"
            f"Скип: {self.stats.skipped}\n"
            f"Открыто: {self.stats.opened}\n"
            f"Закрыто: {len(self.stats.trades)}\n"
            f"Открытые позиции: {open_positions}\n"
            f"WIN: {len(wins)} LOSS: {len(losses)} WR: {wr:.1f}%\n"
            f"Итог PnL: {pnl:+.4f}$"
        )

    def _set_stop_cooldown(self, symbol: str):
        self.store.set_cooldown(symbol, self.cfg["symbol_cooldown_min"])

    def _in_symbol_cooldown(self, symbol: str) -> bool:
        return self.store.in_cooldown(symbol)

    def day_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.stats.date:
            open_positions = len(self.open_trades)
            report, path = make_report(self.stats, self.cfg["max_trades_per_day"])
            self.log.info(f"report_path={path}")
            print(report)
            self.tg.send(self.day_summary_text("Суточный отчёт агента", open_positions), force=True)
            self.stats = DayStats(date=today)

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

    def close_all(self, reason):
        for sym, t in list(self.open_trades.items()):
            try:
                pos = self.ex.get_pos(sym)
                if pos:
                    qty = float(pos.get("size", 0) or 0)
                    self.ex.close_pos(sym, t.direction, qty)
                    time.sleep(0.4)
                    t.close_price = float(pos.get("markPrice", t.entry) or t.entry)
                    t.close_time = datetime.now(timezone.utc).isoformat()
                    t.close_reason = reason
                    t.pnl_usd = self.ex.realized_pnl_from_exchange(sym, t.open_time_ms)
                    self.stats.trades.append(t)
                    self._set_state(t, STATE_CLOSED, "close_all", {"reason": reason, "pnl": t.pnl_usd})
                    self._set_state(t, STATE_RECONCILED, "close_all_reconciled")
                    self.tg.send(
                        f"Закрыта позиция {sym} {t.direction}\nПричина: {reason}\nPnL: {(t.pnl_usd or 0):+.4f}$",
                        force=True,
                    )
                else:
                    self._set_state(t, STATE_RECONCILED, "close_all_no_position")
                del self.open_trades[sym]
            except Exception as ex:
                self.log.error(f"close_all_failed symbol={sym} err={ex}")
                self.tg.send_error("close_all", f"close_all {sym}: {ex}")

    def sync_trades(self):
        for sym, t in list(self.open_trades.items()):
            try:
                pos = self.ex.get_pos(sym)
                if pos:
                    pos_qty = float(pos.get("size", 0) or 0)
                    prev_qty = max(t.qty, t.filled_qty)
                    t.qty = pos_qty
                    t.filled_qty = max(t.filled_qty, pos_qty)
                    if t.state in (STATE_SIGNALLED, STATE_ORDER_SENT):
                        self._set_state(t, STATE_FILLED, "sync_position_detected")
                        self._set_state(t, STATE_OPEN, "sync_position_open")
                    elif prev_qty > 0 and 0 < pos_qty < prev_qty:
                        self._set_state(t, STATE_PARTIALLY_CLOSED, "sync_partial_close")
                    else:
                        self._save_trade(t)
                    self._manage_protection(t, pos)
                    continue

                if t.state in ACTIVE_STATES:
                    t.close_time = datetime.now(timezone.utc).isoformat()
                    t.close_reason = "SL/TP/Manual"
                    t.pnl_usd = self.ex.realized_pnl_from_exchange(sym, t.open_time_ms)
                    self.stats.trades.append(t)
                    self._set_state(t, STATE_CLOSED, "sync_closed", {"pnl": t.pnl_usd})
                    self._set_state(t, STATE_RECONCILED, "sync_reconciled")
                    loss = (t.pnl_usd or 0) < 0
                    self.stats.consecutive_losses = self.stats.consecutive_losses + 1 if loss else 0
                    if loss:
                        self._set_stop_cooldown(sym)
                    self.tg.send(
                        f"Позиция закрыта: {sym} {t.direction}\nStrategy: {t.strategy}\nPnL: {(t.pnl_usd or 0):+.4f}$",
                        force=True,
                    )
                    del self.open_trades[sym]
            except Exception as ex:
                self.log.error(f"sync_trade_failed symbol={sym} err={ex}")
                self.tg.send_error("sync", f"sync {sym}: {ex}")

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
        if not candidates:
            return candidates
        if not self.cfg["use_llm_secondary_rank"]:
            return candidates
        top_n = max(2, int(self.cfg["llm_rank_top_n"]))
        head = candidates[:top_n]
        tail = candidates[top_n:]
        ranked = self.llm.rank_candidates(head)
        return ranked + tail

    async def cycle(self):
        now = datetime.now(timezone.utc)
        self.log.info(
            f"cycle_start ts={now.isoformat()} open_positions={len(self.open_trades)} "
            f"closed_today={len(self.stats.trades)} skipped_today={self.stats.skipped}"
        )

        self.sync_trades()
        if self.stats.stopped:
            self.log.warning("trading_stopped_for_day=true")
            return
        if self.stats.consecutive_losses >= self.cfg["stop_after_losses"]:
            self.stats.stopped = True
            self.log.warning("stop_after_losses_triggered=true")
            return

        done = len(self.stats.trades) + len(self.open_trades)
        if done >= self.cfg["max_trades_per_day"]:
            self.log.info(f"daily_trade_limit_reached done={done} max={self.cfg['max_trades_per_day']}")
            return

        symbols = screen_coins(self.ex, self.cfg)
        self.log.info(f"scan_universe count={len(symbols)} symbols={','.join(symbols)}")
        candidates = []
        scan_stats = {
            "in_open_trades": 0,
            "cooldown": 0,
            "low_atr": 0,
            "funding_block": 0,
            "oi_spike": 0,
            "no_signals": 0,
            "middle_range": 0,
            "low_score": 0,
            "candidates": 0,
        }

        for sym in symbols:
            self.log.info(f"scan_symbol_start symbol={sym}")
            if sym in self.open_trades:
                scan_stats["in_open_trades"] += 1
                self.log.info(f"scan_symbol_skip symbol={sym} reason=already_open")
                continue
            if self._in_symbol_cooldown(sym):
                self.stats.skipped += 1
                scan_stats["cooldown"] += 1
                self.log.info(f"scan_symbol_skip symbol={sym} reason=cooldown")
                continue
            try:
                df = self.ex.klines(sym, self.cfg["entry_tf"])
                ind = calc_indicators(df)
                if not ind.get("atr") or ind["atr"] / ind["price"] < self.cfg["min_atr_ratio"]:
                    scan_stats["low_atr"] += 1
                    self.log.info(
                        f"scan_symbol_skip symbol={sym} reason=low_atr atr={ind.get('atr')} "
                        f"price={ind.get('price')} min_atr_ratio={self.cfg['min_atr_ratio']}"
                    )
                    continue

                fmeta = self.ex.funding_meta(sym)
                fr = fmeta["rate"]
                if in_funding_block(fmeta.get("next_funding_ms", 0), self.cfg["funding_block_minutes"]):
                    self.stats.skipped += 1
                    scan_stats["funding_block"] += 1
                    self.log.info(
                        f"scan_symbol_skip symbol={sym} reason=funding_block funding_rate={fr} "
                        f"block_minutes={self.cfg['funding_block_minutes']}"
                    )
                    continue

                oi = self.ex.open_interest(sym)
                if oi_spike_block(oi.get("change_pct", 0), self.cfg["oi_spike_block_pct"]):
                    self.stats.skipped += 1
                    scan_stats["oi_spike"] += 1
                    self.log.info(
                        f"scan_symbol_skip symbol={sym} reason=oi_spike oi_change_pct={oi.get('change_pct', 0)} "
                        f"threshold={self.cfg['oi_spike_block_pct']}"
                    )
                    continue

                ind1h = calc_indicators(self.ex.klines(sym, self.cfg["regime_tf_1"]))
                ind4h = calc_indicators(self.ex.klines(sym, self.cfg["regime_tf_2"]))

                sigs = detect_signals(ind, fr)
                self.stats.signals_total += len(sigs)
                if not sigs:
                    scan_stats["no_signals"] += 1
                    self.log.info(f"scan_symbol_signals symbol={sym} count=0")
                    continue
                self.log.info(
                    f"scan_symbol_signals symbol={sym} count={len(sigs)} "
                    f"strategies={','.join([s.get('strategy', '?') for s in sigs])}"
                )
                for s in sigs:
                    ok_mid, mid_reason = no_middle_range(s, ind, self.cfg["range_mid_avoid_pct"])
                    if not ok_mid:
                        self.stats.skipped += 1
                        scan_stats["middle_range"] += 1
                        self.log.info(
                            f"scan_signal_skip symbol={sym} strategy={s.get('strategy')} direction={s.get('direction')} "
                            f"reason={mid_reason}"
                        )
                        continue
                    ok_regime, reg_reason = regime_filter(ind1h, ind4h, s["direction"])
                    score, reasons = score_signal(s, ind, fr, oi.get("change_pct", 0), ok_regime)
                    if not ok_regime:
                        reasons.append(reg_reason)
                    if not ok_mid:
                        reasons.append(mid_reason)
                    if score < self.cfg["min_rule_score"]:
                        self.stats.skipped += 1
                        scan_stats["low_score"] += 1
                        self.log.info(
                            f"scan_signal_skip symbol={sym} strategy={s.get('strategy')} direction={s.get('direction')} "
                            f"reason=low_score score={score} min_rule_score={self.cfg['min_rule_score']}"
                        )
                        continue
                    candidates.append(
                        {
                            "sym": sym,
                            "sig": s,
                            "ind": ind,
                            "fr": fr,
                            "oi": oi,
                            "score": score,
                            "reasons": reasons,
                        }
                    )
                    scan_stats["candidates"] += 1
                    self.log.info(
                        f"scan_signal_candidate symbol={sym} strategy={s.get('strategy')} direction={s.get('direction')} "
                        f"score={score} reasons={','.join(reasons[:6])}"
                    )
                time.sleep(0.12)
            except Exception as ex:
                self.log.error(f"analyze_failed symbol={sym} err={ex}")
                self.tg.send_error("analysis", f"Анализ {sym}: {ex}")

        self.log.info(
            "scan_summary "
            f"symbols={len(symbols)} in_open={scan_stats['in_open_trades']} cooldown={scan_stats['cooldown']} "
            f"low_atr={scan_stats['low_atr']} funding_block={scan_stats['funding_block']} "
            f"oi_spike={scan_stats['oi_spike']} no_signals={scan_stats['no_signals']} "
            f"middle_range={scan_stats['middle_range']} low_score={scan_stats['low_score']} "
            f"candidates={scan_stats['candidates']}"
        )
        if not candidates:
            self.log.info("cycle_result candidates=0 action=no_trade")
            return

        candidates.sort(key=lambda x: x["score"], reverse=True)
        candidates = self._candidate_llm_rank(candidates)
        slots = self.cfg["max_trades_per_day"] - done
        top_preview = ",".join([f"{x['sym']}:{x['score']}" for x in candidates[:5]])
        self.log.info(f"cycle_candidates total={len(candidates)} slots={slots} top={top_preview}")

        for item in candidates[: slots + 3]:
            if len(self.open_trades) + len(self.stats.trades) >= self.cfg["max_trades_per_day"]:
                self.log.info("cycle_order_loop_stop reason=max_trades_reached")
                break

            sym = item["sym"]
            if sym in self.open_trades:
                self.log.info(f"cycle_order_loop_skip symbol={sym} reason=already_open")
                continue

            try:
                s = item["sig"]
                direction = s["direction"]
                self.log.info(
                    f"cycle_order_try symbol={sym} strategy={s.get('strategy')} direction={direction} "
                    f"score={item.get('score')}"
                )
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
                )
                if not sizing:
                    self.stats.skipped += 1
                    self.log.info(f"skip symbol={sym} reason={reject_reason}")
                    continue

                if self.cfg.get("disable_side_risk_guard", False):
                    self.log.warning(
                        f"side_risk_guard_disabled symbol={sym} risk_usd={sizing['risk_usd']:.4f} "
                        f"max_side_risk_pct={self.cfg['max_side_risk_pct']}"
                    )
                else:
                    ok_side, side_reason = check_side_exposure(
                        self.cfg,
                        self.open_trades,
                        direction,
                        sizing["risk_usd"],
                        float(wallet.get("equity", 0) or 0),
                    )
                    if not ok_side:
                        self.stats.skipped += 1
                        self.log.info(f"skip symbol={sym} reason={side_reason}")
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
                    self.log.info(f"skip symbol={sym} reason={corr_reason}")
                    continue

                tp = round_to_step(float(s["tp"]), float(limits.get("tick_size", 0) or 0))
                now_iso = datetime.now(timezone.utc).isoformat()
                now_ms = int(time.time() * 1000)
                t = Trade(
                    id=f"{sym}_{int(time.time())}",
                    symbol=sym,
                    direction=direction,
                    strategy=s["strategy"],
                    entry=sizing["entry"],
                    sl=sizing["sl"],
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
                explain = self.llm.explain(
                    context={"symbol": sym, "score": item["score"], "strategy": t.strategy, "direction": direction},
                    decision="TRADE",
                    reasons=item["reasons"],
                )
                self.log.info(
                    f"opened symbol={sym} direction={direction} qty={t.qty} lev={sizing['leverage']} "
                    f"risk={sizing['risk_usd']:.4f} score={item['score']}"
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

    async def run(self):
        mode = "TESTNET" if self.cfg["testnet"] else "*** REAL MONEY ***"
        self.log.info(
            f"agent_start mode={mode} model={self.cfg['llm_model']} sizing={self._sizing_label()} "
            f"side_risk_guard={self._side_risk_guard_label()}"
        )
        self.tg.send(
            f"Агент запущен\nРежим: {mode}\nМодель: {self.cfg['llm_model']}\n"
            f"Sizing: {self._sizing_label()} | SideRiskGuard: {self._side_risk_guard_label()} | "
            f"Max/day: {self.cfg['max_trades_per_day']}",
            force=True,
        )
        while True:
            try:
                self.day_reset()
                await self.cycle()
            except KeyboardInterrupt:
                open_before_close = len(self.open_trades)
                self.close_all("stopped")
                report, _ = make_report(self.stats, self.cfg["max_trades_per_day"])
                print(report)
                self.tg.send(self.day_summary_text("Агент остановлен. Итог дня", open_before_close), force=True)
                break
            except Exception as ex:
                self.log.error(f"cycle_failed err={ex}")
                self.tg.send_error("cycle", f"Цикл: {ex}")
            self.log.info(f"sleep_sec={self.cfg['cycle_sec']}")
            await asyncio.sleep(self.cfg["cycle_sec"])
