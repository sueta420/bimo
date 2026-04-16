import math
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Optional

import numpy as np

from portfolio import ACTIVE_STATES, Trade, side_exposure_risk


def normalize_sizing_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in ("risk_pct", "risk_percent", "percent"):
        return "risk_pct"
    if m in ("risk_usd", "fixed_risk_usd"):
        return "risk_usd"
    if m in ("fixed_notional_usd", "fixed_notional", "notional_usd"):
        return "fixed_notional_usd"
    if m in ("fixed_margin_usd", "margin_usd", "fixed_margin"):
        return "fixed_margin_usd"
    return "risk_pct"


def resolve_target_leverage(cfg: dict, strategy: str = "", score: int = 0, atr_ratio: float = 0.0) -> int:
    base = int(cfg.get("target_leverage", 1) or 1)
    if cfg.get("dynamic_leverage_enabled"):
        strategy_key = str(strategy or "").strip().lower()
        if strategy_key == "fakeout":
            base = int(cfg.get("fakeout_target_leverage", base) or base)
        elif strategy_key == "breakout":
            base = int(cfg.get("breakout_target_leverage", base) or base)
        elif strategy_key == "reversal":
            base = int(cfg.get("reversal_target_leverage", base) or base)

        if int(score or 0) >= int(cfg.get("dynamic_leverage_high_score", 80) or 80):
            base += int(cfg.get("dynamic_leverage_high_score_bonus", 1) or 0)
        elif int(score or 0) <= int(cfg.get("dynamic_leverage_low_score", 72) or 72):
            base -= int(cfg.get("dynamic_leverage_low_score_cut", 1) or 0)

        if float(atr_ratio or 0.0) >= float(cfg.get("dynamic_leverage_high_atr_ratio", 0.012) or 0.012):
            base -= int(cfg.get("dynamic_leverage_high_atr_cut", 1) or 0)

    max_leverage = int(cfg.get("max_leverage", 1) or 1)
    return max(1, min(base, max_leverage))


def rr_ratio(entry: float, sl: float, tp: float, direction: str) -> float:
    entry_px = float(entry or 0.0)
    sl_px = float(sl or 0.0)
    tp_px = float(tp or 0.0)
    if entry_px <= 0:
        return 0.0
    if direction == "LONG":
        risk = entry_px - sl_px
        reward = tp_px - entry_px
    else:
        risk = sl_px - entry_px
        reward = entry_px - tp_px
    if risk <= 0 or reward <= 0:
        return 0.0
    return float(reward / risk)


def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    v = Decimal(str(value))
    s = Decimal(str(step))
    q = (v / s).to_integral_value(rounding=ROUND_DOWN)
    return float(q * s)


def ceil_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    v = Decimal(str(value))
    s = Decimal(str(step))
    q = (v / s).to_integral_value(rounding=ROUND_UP)
    return float(q * s)


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    v = Decimal(str(value))
    s = Decimal(str(step))
    q = (v / s).to_integral_value(rounding=ROUND_HALF_UP)
    return float(q * s)


def align_protective_prices(entry: float, sl: float, tp: float, direction: str, tick_size: float) -> tuple[float, float]:
    entry_px = float(entry or 0.0)
    sl_px = float(sl or 0.0)
    tp_px = float(tp or 0.0)
    tick = float(tick_size or 0.0)
    if tick <= 0:
        return sl_px, tp_px

    if direction == "LONG":
        sl_adj = floor_to_step(sl_px, tick)
        tp_adj = ceil_to_step(tp_px, tick)
        if sl_adj >= entry_px:
            sl_adj = floor_to_step(entry_px - tick, tick)
        if tp_adj <= entry_px:
            tp_adj = ceil_to_step(entry_px + tick, tick)
    else:
        sl_adj = ceil_to_step(sl_px, tick)
        tp_adj = floor_to_step(tp_px, tick)
        if sl_adj <= entry_px:
            sl_adj = ceil_to_step(entry_px + tick, tick)
        if tp_adj >= entry_px:
            tp_adj = floor_to_step(entry_px - tick, tick)

    return float(sl_adj), float(tp_adj)


def size_position(cfg, direction, entry, sl, funding_rate, wallet, limits, strategy: str = "", score: int = 0, atr_ratio: float = 0.0):
    equity = float(wallet.get("equity", 0) or 0)
    available = float(wallet.get("available", 0) or 0)
    if equity <= 0:
        return None, "equity<=0"
    mode = normalize_sizing_mode(cfg.get("position_sizing_mode", "risk_pct"))
    risk_budget_usd = 0.0
    target_notional_usd = 0.0
    if mode == "risk_pct":
        risk_budget_usd = equity * float(cfg.get("risk_per_trade_pct", 0) or 0) / 100.0
        if risk_budget_usd <= 0:
            return None, "risk_usd<=0"
    elif mode == "risk_usd":
        risk_budget_usd = float(cfg.get("risk_per_trade_usd", 0) or 0)
        if risk_budget_usd <= 0:
            return None, "risk_per_trade_usd<=0"
    elif mode == "fixed_notional_usd":
        target_notional_usd = float(cfg.get("target_notional_usd", 0) or 0)
        if target_notional_usd <= 0:
            return None, "target_notional_usd<=0"
    elif mode == "fixed_margin_usd":
        target_margin_usd = float(cfg.get("target_margin_usd", 0) or 0)
        target_leverage = resolve_target_leverage(cfg, strategy=strategy, score=score, atr_ratio=atr_ratio)
        if target_margin_usd <= 0:
            return None, "target_margin_usd<=0"
        if target_leverage <= 0:
            return None, "target_leverage<=0"
        target_notional_usd = target_margin_usd * target_leverage

    slip_in = float(cfg["slippage_entry_bps"]) / 10_000.0
    slip_out = float(cfg["slippage_exit_bps"]) / 10_000.0
    taker_fee = float(cfg["taker_fee_bps"]) / 10_000.0
    funding_reserve = max(abs(float(funding_rate or 0)), float(cfg["funding_reserve_rate"]))

    entry_px = float(entry)
    sl_px = float(sl)
    if direction == "LONG":
        entry_worst = entry_px * (1.0 + slip_in)
        sl_worst = sl_px * (1.0 - slip_out)
    else:
        entry_worst = entry_px * (1.0 - slip_in)
        sl_worst = sl_px * (1.0 + slip_out)

    price_loss_per_unit = abs(entry_worst - sl_worst)
    fee_per_unit = (entry_px + sl_px) * taker_fee
    funding_per_unit = entry_px * funding_reserve
    per_unit_loss = price_loss_per_unit + fee_per_unit + funding_per_unit
    if per_unit_loss <= 0:
        return None, "per_unit_loss<=0"

    qty_step = float(limits.get("qty_step", 0) or 0)
    min_qty = float(limits.get("min_qty", 0) or 0)
    min_notional = float(limits.get("min_notional", 0) or 0)
    tick_size = float(limits.get("tick_size", 0) or 0)
    if qty_step <= 0:
        return None, "qty_step<=0"

    entry_adj = round_to_step(entry_px, tick_size)
    sl_adj = round_to_step(sl_px, tick_size)
    if mode in {"fixed_notional_usd", "fixed_margin_usd"}:
        if entry_adj <= 0:
            return None, "entry<=0"
        qty_raw = target_notional_usd / entry_adj
    else:
        qty_raw = risk_budget_usd / per_unit_loss
    qty = floor_to_step(qty_raw, qty_step) if qty_step > 0 else qty_raw
    if qty <= 0:
        return None, "qty<=0 after rounding"

    if qty < min_qty:
        qty = ceil_to_step(min_qty, qty_step)
    if min_notional > 0 and (qty * entry_adj) < min_notional:
        qty = ceil_to_step(min_notional / entry_adj, qty_step) if qty_step > 0 else (min_notional / entry_adj)
    qty = floor_to_step(qty, qty_step)
    if qty < min_qty:
        return None, "qty<min_qty after rounding"

    notional = qty * entry_adj
    if available <= 0:
        return None, "available<=0"
    max_notional = available * float(cfg["max_leverage"])
    if notional > max_notional:
        return None, f"notional>{max_notional:.4f}"

    effective_risk = qty * per_unit_loss
    max_risk_cap = float(cfg.get("max_risk_per_trade_usd", 0) or 0)
    if max_risk_cap > 0 and effective_risk > max_risk_cap:
        return None, f"risk_cap_exceeded {effective_risk:.4f}>{max_risk_cap:.4f}"
    if mode not in {"fixed_notional_usd", "fixed_margin_usd"} and effective_risk > risk_budget_usd * 1.02:
        return None, f"effective_risk>{risk_budget_usd:.4f}"

    if mode == "fixed_margin_usd":
        req_lev = max(1, int(cfg.get("target_leverage", 1) or 1))
    else:
        req_lev = max(1, math.ceil(notional / available))
    if req_lev > int(cfg["max_leverage"]):
        return None, f"required_leverage>{cfg['max_leverage']}"

    return {
        "qty": round(qty, 8),
        "leverage": int(req_lev),
        "risk_usd": round(effective_risk, 6),
        "risk_budget_usd": round(risk_budget_usd, 6),
        "sizing_mode": mode,
        "notional": round(notional, 6),
        "entry": entry_adj,
        "sl": sl_adj,
        "target_leverage": int(resolve_target_leverage(cfg, strategy=strategy, score=score, atr_ratio=atr_ratio)) if mode == "fixed_margin_usd" else int(req_lev),
    }, ""


def check_side_exposure(cfg, open_trades: dict[str, Trade], direction: str, new_risk_usd: float, equity: float) -> tuple[bool, str]:
    exp = side_exposure_risk(open_trades)
    limit = equity * float(cfg["max_side_risk_pct"]) / 100.0
    after = exp.get(direction, 0.0) + float(new_risk_usd or 0.0)
    if after > limit:
        return False, f"side_risk_limit {after:.4f}>{limit:.4f}"
    return True, ""


def series_corr(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n < 10:
        return 0.0
    x = np.array(a[-n:], dtype=float)
    y = np.array(b[-n:], dtype=float)
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def returns_from_prices(prices: list[float]) -> list[float]:
    if len(prices) < 3:
        return []
    out = []
    for i in range(1, len(prices)):
        p0 = float(prices[i - 1] or 0)
        p1 = float(prices[i] or 0)
        if p0 <= 0:
            continue
        out.append((p1 - p0) / p0)
    return out


def correlation_allowed(
    symbol: str,
    direction: str,
    open_trades: dict[str, Trade],
    return_series: dict[str, list[float]],
    threshold: float,
    max_correlated_per_side: int,
) -> tuple[bool, str]:
    same_side = [t for t in open_trades.values() if t.direction == direction and t.state in ACTIVE_STATES and t.symbol != symbol]
    if not same_side:
        return True, ""
    base = return_series.get(symbol, [])
    if not base:
        return True, ""
    hits = 0
    for t in same_side:
        corr = series_corr(base, return_series.get(t.symbol, []))
        if abs(corr) >= threshold:
            hits += 1
    if hits >= max_correlated_per_side:
        return False, f"corr_hits={hits} threshold={threshold}"
    return True, ""
