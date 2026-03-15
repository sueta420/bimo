import math
from typing import Optional

import numpy as np

from portfolio import ACTIVE_STATES, Trade, side_exposure_risk


def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def ceil_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.ceil(value / step) * step


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(round(value / step) * step, 12)


def size_position(cfg, direction, entry, sl, funding_rate, wallet, limits):
    equity = float(wallet.get("equity", 0) or 0)
    available = float(wallet.get("available", 0) or 0)
    if equity <= 0:
        return None, "equity<=0"

    risk_usd = equity * float(cfg["risk_per_trade_pct"]) / 100.0
    if risk_usd <= 0:
        return None, "risk_usd<=0"

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

    entry_adj = round_to_step(entry_px, tick_size)
    sl_adj = round_to_step(sl_px, tick_size)
    qty_raw = risk_usd / per_unit_loss
    qty = floor_to_step(qty_raw, qty_step) if qty_step > 0 else qty_raw
    if qty <= 0:
        return None, "qty<=0 after rounding"

    if qty < min_qty:
        qty = min_qty
    if min_notional > 0 and (qty * entry_adj) < min_notional:
        qty = ceil_to_step(min_notional / entry_adj, qty_step) if qty_step > 0 else (min_notional / entry_adj)

    notional = qty * entry_adj
    if available <= 0:
        return None, "available<=0"
    max_notional = available * float(cfg["max_leverage"])
    if notional > max_notional:
        return None, f"notional>{max_notional:.4f}"

    effective_risk = qty * per_unit_loss
    if effective_risk > risk_usd * 1.02:
        return None, f"effective_risk>{risk_usd:.4f}"

    req_lev = max(1, math.ceil(notional / available))
    if req_lev > int(cfg["max_leverage"]):
        return None, f"required_leverage>{cfg['max_leverage']}"

    return {
        "qty": round(qty, 8),
        "leverage": int(req_lev),
        "risk_usd": round(effective_risk, 6),
        "notional": round(notional, 6),
        "entry": entry_adj,
        "sl": sl_adj,
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
