import time

import numpy as np
import ta
import pandas as pd


ALL_STRATEGIES = {"trend_pullback", "range_bounce"}
V2_LITE_STRATEGIES = {"trend_pullback", "range_bounce"}


def calc_indicators(df):
    if df is None or len(df) < 6:
        return {}
    if len(df) < 35:
        recent = df.tail(min(len(df), 48))
        c = df["close"]
        v = df["volume"]
        price = round(float(c.iloc[-1]), 6)
        try:
            vol_ma = v.rolling(min(20, len(v))).mean().iloc[-1]
            vol_r = round(v.iloc[-1] / vol_ma * 100 if vol_ma else 100, 1)
        except Exception:
            vol_r = 100.0
        atr = None
        try:
            h = df["high"]
            l = df["low"]
            prev_close = c.shift(1)
            tr = pd.concat(
                [
                    (h - l).abs(),
                    (h - prev_close).abs(),
                    (l - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr_window = min(14, len(tr))
            atr = round(float(tr.tail(atr_window).mean()), 6) if atr_window > 0 else None
        except Exception:
            atr = None
        return {
            "price": price,
            "rsi": None,
            "macd_hist": None,
            "ema20": None,
            "ema50": None,
            "ema200": None,
            "atr": atr,
            "vol_ratio": vol_r,
            "support": round(float(recent["low"].min()), 6),
            "resistance": round(float(recent["high"].max()), 6),
        }

    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    def safe(series):
        try:
            val = float(series.iloc[-1])
            return round(val, 6) if not np.isnan(val) else None
        except Exception:
            return None

    rsi = safe(ta.momentum.RSIIndicator(close=c, window=14).rsi())
    macd = ta.trend.MACD(close=c, window_slow=26, window_fast=12, window_sign=9)
    mhist = safe(macd.macd_diff())
    ema20 = safe(ta.trend.EMAIndicator(close=c, window=20).ema_indicator())
    ema50 = safe(ta.trend.EMAIndicator(close=c, window=50).ema_indicator())
    ema200 = safe(ta.trend.EMAIndicator(close=c, window=200).ema_indicator())
    atr = safe(ta.volatility.AverageTrueRange(high=h, low=l, close=c, window=14).average_true_range())
    vol_ma = v.rolling(20).mean().iloc[-1]
    vol_r = round(v.iloc[-1] / vol_ma * 100 if vol_ma else 100, 1)
    recent = df.tail(48)
    price = round(float(c.iloc[-1]), 6)

    return {
        "price": price,
        "rsi": rsi,
        "macd_hist": mhist,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "atr": atr,
        "vol_ratio": vol_r,
        "support": round(float(recent["low"].min()), 6),
        "resistance": round(float(recent["high"].max()), 6),
    }


def _apply_enabled_strategies(allowed: set[str], cfg: dict | None = None) -> set[str]:
    profile = str((cfg or {}).get("agent_profile") or "").strip().lower()
    configured = {
        str(x).strip().lower()
        for x in (cfg or {}).get("enabled_strategies", [])
        if str(x).strip()
    }
    configured &= ALL_STRATEGIES
    if profile == "v2-lite":
        base = V2_LITE_STRATEGIES
        if configured:
            configured &= base
        return allowed & (configured or base)
    if not configured:
        return allowed
    return allowed & configured


def regime_strategy_allowlist(ind1h: dict | None, ind4h: dict | None, cfg: dict | None = None) -> set[str]:
    if not ind1h or not ind4h:
        return _apply_enabled_strategies(set(ALL_STRATEGIES), cfg)
    r1 = _regime_profile(ind1h)
    r4 = _regime_profile(ind4h)
    biases = {r1["bias"], r4["bias"]}
    trend_biases = {"bull_expansion", "bear_expansion", "bull_trend", "bear_trend"}
    range_biases = {"flat", "chop"}

    if biases <= range_biases:
        return _apply_enabled_strategies({"range_bounce"}, cfg)
    if biases & trend_biases and not (biases & range_biases):
        return _apply_enabled_strategies({"trend_pullback"}, cfg)
    return _apply_enabled_strategies({"trend_pullback", "range_bounce"}, cfg)


def detect_signals(ind, funding, ind1h=None, ind4h=None, cfg=None):
    sigs = []
    allowed = regime_strategy_allowlist(ind1h, ind4h, cfg)
    p = ind["price"]
    atr = ind.get("atr") or 0
    rsi = ind.get("rsi")
    mh = ind.get("macd_hist")
    vol = ind.get("vol_ratio", 0)
    e20 = ind.get("ema20") or p
    e50 = ind.get("ema50") or p
    e200 = ind.get("ema200") or p
    sup = ind["support"]
    res = ind["resistance"]
    if not atr:
        return sigs
    active = set(allowed)

    pullback_dist_20 = abs(p - e20) / p if p else 0.0
    pullback_dist_50 = abs(p - e50) / p if p else 0.0
    if (
        "trend_pullback" in allowed
        and rsi is not None
        and mh is not None
        and atr
        and vol >= 95
        and abs(funding) < 0.0012
    ):
        if e20 and e50 and e200 and p > e20 >= e50 > e200 and 44 <= rsi <= 62 and mh >= -0.05 and min(pullback_dist_20, pullback_dist_50) <= 0.012:
            anchor = min(e20, e50)
            sl = round(min(anchor, sup) - atr * 0.6, 6)
            tp = round(p + (p - sl) * 3, 6)
            if sl < p:
                sigs.append({"strategy": "trend_pullback", "direction": "LONG", "entry": p, "sl": sl, "tp": tp, "why": f"Trend pullback LONG RSI={rsi:.1f}"})
        if e20 and e50 and e200 and p < e20 <= e50 < e200 and 38 <= rsi <= 56 and mh <= 0.05 and min(pullback_dist_20, pullback_dist_50) <= 0.012:
            anchor = max(e20, e50)
            sl = round(max(anchor, res) + atr * 0.6, 6)
            tp = round(p - (sl - p) * 3, 6)
            if sl > p:
                sigs.append({"strategy": "trend_pullback", "direction": "SHORT", "entry": p, "sl": sl, "tp": tp, "why": f"Trend pullback SHORT RSI={rsi:.1f}"})

    range_frac = (p - sup) / max(res - sup, 1e-9)
    if "range_bounce" in allowed and rsi is not None and mh is not None and atr and abs(funding) < 0.0012:
        if range_frac <= 0.18 and rsi <= 47 and mh >= -0.02 and vol >= 80:
            sl = round(sup - atr * 0.45, 6)
            tp = round(p + (p - sl) * 3, 6)
            if sl < p:
                sigs.append({"strategy": "range_bounce", "direction": "LONG", "entry": p, "sl": sl, "tp": tp, "why": f"Range bounce LONG RSI={rsi:.1f}"})
        if range_frac >= 0.82 and rsi >= 53 and mh <= 0.02 and vol >= 80:
            sl = round(res + atr * 0.45, 6)
            tp = round(p - (sl - p) * 3, 6)
            if sl > p:
                sigs.append({"strategy": "range_bounce", "direction": "SHORT", "entry": p, "sl": sl, "tp": tp, "why": f"Range bounce SHORT RSI={rsi:.1f}"})
    return sigs


def screen_coins(ex, cfg):
    result = []
    blacklist = {str(x).upper() for x in cfg.get("symbol_blacklist", [])}
    major_symbols = {str(x).upper() for x in cfg.get("major_symbols", [])}
    min_move_pct = float(cfg.get("universe_min_daily_move_pct", 0.8) or 0.8)
    max_move_pct = float(cfg.get("universe_max_daily_move_pct", 18.0) or 18.0)

    def universe_score(sym: str, turnover_24h: float, funding_abs: float, day_move_pct: float) -> float:
        liquidity_score = min(turnover_24h / max(float(cfg["min_volume_24h"]), 1.0), 6.0)
        funding_score = max(
            0.0,
            1.5 - (funding_abs / max(float(cfg.get("max_funding_abs", 0.001) or 0.001), 1e-9)),
        )
        if day_move_pct <= 0:
            move_score = 0.4
        elif day_move_pct < min_move_pct:
            move_score = 0.6 + (day_move_pct / max(min_move_pct, 1e-9))
        elif day_move_pct <= max_move_pct:
            move_score = 2.0
        else:
            move_score = max(0.25, 2.0 - min((day_move_pct - max_move_pct) / max(max_move_pct, 1.0), 1.5))
        major_bonus = 1.5 if sym in major_symbols else 0.0
        return round(liquidity_score * 2.0 + funding_score + move_score + major_bonus, 6)

    for t in ex.tickers():
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if sym.upper() in blacklist:
            continue
        try:
            vol = float(t.get("turnover24h", 0))
            fr = abs(float(t.get("fundingRate", 0)))
            day_move_pct = abs(float(t.get("price24hPcnt", 0) or 0.0)) * 100.0
            if vol >= cfg["min_volume_24h"] and fr < cfg["max_funding_abs"]:
                result.append((sym, universe_score(sym.upper(), vol, fr, day_move_pct), vol))
        except Exception:
            continue
    result.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return [s for s, _, _ in result[: max(int(cfg.get("max_scan_symbols", 20) or 20), 1)]]


def signal_quality_filter(sig: dict, ind: dict, cfg: dict, oi_change_pct: float = 0.0) -> tuple[bool, str]:
    return True, ""


def _regime_profile(ind: dict) -> dict:
    price = float(ind.get("price") or 0.0)
    ema20 = float(ind.get("ema20") or 0.0)
    ema50 = float(ind.get("ema50") or 0.0)
    ema200 = float(ind.get("ema200") or 0.0)
    atr = float(ind.get("atr") or 0.0)
    vol_ratio = float(ind.get("vol_ratio") or 100.0)
    if min(price, ema20, ema50, ema200) <= 0:
        return {"bias": "missing", "trend_strength": 0.0, "compression": 1.0, "atr_ratio": 0.0, "vol_ratio": vol_ratio}

    spread_fast = abs(ema20 - ema50) / price
    spread_slow = abs(ema50 - ema200) / price
    compression = spread_fast + spread_slow
    atr_ratio = atr / price if price > 0 else 0.0

    bull_strong = price > ema20 > ema50 > ema200
    bear_strong = price < ema20 < ema50 < ema200
    bull_soft = price > ema200 and ema20 >= ema50
    bear_soft = price < ema200 and ema20 <= ema50

    if compression < 0.004 and atr_ratio < 0.006:
        bias = "flat"
    elif compression < 0.006 and atr_ratio < 0.008:
        bias = "chop"
    elif bull_strong:
        bias = "bull_expansion" if atr_ratio >= 0.009 or vol_ratio >= 140 else "bull_trend"
    elif bear_strong:
        bias = "bear_expansion" if atr_ratio >= 0.009 or vol_ratio >= 140 else "bear_trend"
    elif bull_soft:
        bias = "bull_soft"
    elif bear_soft:
        bias = "bear_soft"
    else:
        bias = "mixed"

    return {
        "bias": bias,
        "trend_strength": round(compression, 6),
        "compression": round(compression, 6),
        "atr_ratio": round(atr_ratio, 6),
        "vol_ratio": round(vol_ratio, 2),
        "price": price,
    }


def _same_side_biases(direction: str) -> set[str]:
    return {"bull_expansion", "bull_trend", "bull_soft"} if direction == "LONG" else {"bear_expansion", "bear_trend", "bear_soft"}


def _opp_side_biases(direction: str) -> set[str]:
    return {"bear_expansion", "bear_trend", "bear_soft"} if direction == "LONG" else {"bull_expansion", "bull_trend", "bull_soft"}


def regime_filter(ind1h, ind4h, direction: str, strategy: str = "trend_pullback") -> tuple[bool, str]:
    try:
        r1 = _regime_profile(ind1h)
        r4 = _regime_profile(ind4h)
    except Exception:
        return False, "missing_regime_data"

    same_side = _same_side_biases(direction)
    opp_side = _opp_side_biases(direction)
    if strategy == "range_bounce":
        if r1["bias"] in opp_side and r4["bias"] in opp_side:
            return False, "regime_countertrend"
        if r1["bias"] in {"bull_expansion", "bear_expansion"} or r4["bias"] in {"bull_expansion", "bear_expansion"}:
            return False, "regime_range_vs_expansion"
        ok = (
            r1["bias"] in {"flat", "chop", "mixed", "bull_soft", "bear_soft"}
            or r4["bias"] in {"flat", "chop", "mixed", "bull_soft", "bear_soft"}
            or (r1["bias"] in same_side and r4["bias"] not in opp_side)
            or (r4["bias"] in same_side and r1["bias"] not in opp_side)
        )
        return ok, "" if ok else "regime_mismatch"

    if strategy != "trend_pullback":
        return False, "strategy_disabled"
    if r1["bias"] in opp_side or r4["bias"] in opp_side:
        return False, "regime_countertrend"
    if r1["bias"] in {"flat", "chop"} and r4["bias"] in {"flat", "chop"}:
        return False, "regime_flat"
    ok = (
        r1["bias"] in (same_side | {"mixed"})
        and r4["bias"] in (same_side | {"mixed"})
    )
    return ok, "" if ok else "regime_mismatch"


def btc_context_filter(symbol: str, sig: dict, btc1h: dict, btc4h: dict) -> tuple[bool, str]:
    if str(symbol or "").upper() == "BTCUSDT":
        return True, ""
    direction = str(sig.get("direction") or "")
    strategy = str(sig.get("strategy") or "")
    r1 = _regime_profile(btc1h)
    r4 = _regime_profile(btc4h)
    opp_side = _opp_side_biases(direction)

    if r1["bias"] in opp_side and r4["bias"] in opp_side:
        return False, "btc_countertrend"
    if strategy == "range_bounce":
        if r1["bias"] in opp_side and r4["bias"] in opp_side:
            return False, "btc_range_countertrend"
        if r1["bias"] in {"bull_expansion", "bear_expansion"} and r4["bias"] in {"bull_expansion", "bear_expansion"}:
            return False, "btc_range_vs_expansion"
    if strategy not in {"trend_pullback", "range_bounce"}:
        return False, "strategy_disabled"
    return True, ""


def edge_after_costs(sig: dict, cfg: dict, funding: float) -> dict:
    entry = float(sig.get("entry") or 0.0)
    tp = float(sig.get("tp") or 0.0)
    if entry <= 0 or tp <= 0:
        return {"cost_per_unit": 0.0, "gross_reward_per_unit": 0.0, "net_reward_per_unit": 0.0, "edge_cost_ratio": 0.0, "net_reward_pct": 0.0}

    slip_in = float(cfg.get("slippage_entry_bps", 0) or 0) / 10_000.0
    slip_out = float(cfg.get("slippage_exit_bps", 0) or 0) / 10_000.0
    taker_fee = float(cfg.get("taker_fee_bps", 0) or 0) / 10_000.0
    funding_reserve = max(abs(float(funding or 0.0)), float(cfg.get("funding_reserve_rate", 0.0) or 0.0))

    gross_reward = abs(tp - entry)
    cost_per_unit = (entry * slip_in) + (tp * slip_out) + ((entry + tp) * taker_fee) + (entry * funding_reserve)
    net_reward = gross_reward - cost_per_unit
    edge_cost_ratio = (gross_reward / cost_per_unit) if cost_per_unit > 0 else 0.0
    net_reward_pct = (net_reward / entry) if entry > 0 else 0.0
    return {
        "cost_per_unit": round(cost_per_unit, 6),
        "gross_reward_per_unit": round(gross_reward, 6),
        "net_reward_per_unit": round(net_reward, 6),
        "edge_cost_ratio": round(edge_cost_ratio, 6),
        "net_reward_pct": round(net_reward_pct * 100.0, 6),
    }


def in_funding_block(next_funding_ms: int, block_minutes: int) -> bool:
    if not next_funding_ms:
        return False
    now_ms = int(time.time() * 1000)
    left = next_funding_ms - now_ms
    if left < 0:
        return False
    return left <= int(block_minutes * 60_000)


def oi_spike_block(oi_change_pct: float, threshold_pct: float) -> bool:
    return abs(float(oi_change_pct or 0.0)) >= float(threshold_pct)


def no_middle_range(sig: dict, ind: dict, avoid_pct: float) -> tuple[bool, str]:
    p = float(sig["entry"])
    sup = float(ind["support"])
    res = float(ind["resistance"])
    rng = max(res - sup, 1e-9)
    frac = (p - sup) / rng
    direction = sig["direction"]
    edge = float(avoid_pct)
    if direction == "LONG":
        ok = frac <= edge
    else:
        ok = frac >= (1.0 - edge)
    return ok, "" if ok else "middle_of_range"


def effective_middle_avoid_pct(sig: dict, cfg: dict) -> float:
    strategy = str(sig.get("strategy") or "")
    if strategy == "trend_pullback":
        return float(cfg.get("trend_pullback_mid_avoid_pct", cfg.get("range_mid_avoid_pct", 0.30)) or 0.30)
    return float(cfg.get("range_mid_avoid_pct", 0.30) or 0.30)


def score_signal(sig: dict, ind: dict, funding: float, oi_change_pct: float, regime_ok: bool) -> tuple[int, list[str]]:
    score = 50
    reasons = []
    if regime_ok:
        score += 15
        reasons.append("regime_ok")
    else:
        score -= 20
        reasons.append("regime_bad")

    rsi = float(ind.get("rsi") or 50)
    vol = float(ind.get("vol_ratio") or 100)
    mh = float(ind.get("macd_hist") or 0)
    atr = float(ind.get("atr") or 0)
    price = float(ind.get("price") or 1)
    atr_ratio = atr / price if price else 0
    support = float(ind.get("support") or price)
    resistance = float(ind.get("resistance") or price)
    entry = float(sig.get("entry") or price)
    sl = float(sig.get("sl") or entry)
    tp = float(sig.get("tp") or entry)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0.0
    range_size = max(resistance - support, 1e-9)
    range_frac = (entry - support) / range_size

    if sig["direction"] == "LONG":
        if rsi < 45:
            score += 8
            reasons.append("rsi_long_supportive")
        if mh > 0:
            score += 8
            reasons.append("macd_long_supportive")
    else:
        if rsi > 55:
            score += 8
            reasons.append("rsi_short_supportive")
        if mh < 0:
            score += 8
            reasons.append("macd_short_supportive")

    if vol >= 140:
        score += 8
        reasons.append("volume_impulse")
    if atr_ratio >= 0.008:
        score += 6
        reasons.append("high_atr")
    if 0.004 <= atr_ratio <= 0.02:
        score += 3
        reasons.append("tradable_volatility")
    if abs(funding) < 0.0008:
        score += 4
        reasons.append("funding_ok")
    if abs(oi_change_pct) > 6:
        score -= 10
        reasons.append("oi_jump")
    if abs(oi_change_pct) <= 3:
        score += 3
        reasons.append("oi_stable")

    if rr >= 3.5:
        score += 6
        reasons.append("rr_excellent")
    elif rr >= 3.0:
        score += 3
        reasons.append("rr_good")
    elif rr < 2.5:
        score -= 12
        reasons.append("rr_weak")

    if sig["strategy"] == "trend_pullback":
        score += 5
        ema20 = float(ind.get("ema20") or price)
        ema50 = float(ind.get("ema50") or price)
        dist = min(abs(entry - ema20), abs(entry - ema50)) / max(price, 1e-9)
        if dist <= 0.008:
            score += 6
            reasons.append("pullback_near_value")
        elif dist >= 0.02:
            score -= 6
            reasons.append("pullback_far_from_value")
        if vol >= 100:
            score += 3
            reasons.append("pullback_volume_ok")
    elif sig["strategy"] == "range_bounce":
        score += 3
        if sig["direction"] == "LONG":
            if range_frac <= 0.12:
                score += 6
                reasons.append("range_near_support")
            elif range_frac >= 0.3:
                score -= 5
                reasons.append("range_not_near_support")
        else:
            if range_frac >= 0.88:
                score += 6
                reasons.append("range_near_resistance")
            elif range_frac <= 0.7:
                score -= 5
                reasons.append("range_not_near_resistance")
        if vol < 85:
            score -= 4
            reasons.append("range_volume_soft")

    if abs(float(ind.get("ema20") or price) - float(ind.get("ema50") or price)) / price >= 0.003:
        score += 3
        reasons.append("ema_separation")
    else:
        score -= 4
        reasons.append("ema_compression")

    score = int(max(0, min(100, score)))
    return score, reasons
