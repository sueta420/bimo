import time

import numpy as np
import ta


def calc_indicators(df):
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


def detect_signals(ind, funding):
    sigs = []
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

    if p > sup * 0.999 and rsi and rsi < 40 and mh and mh > 0 and funding <= 0:
        sl = round(sup - atr * 0.5, 6)
        tp = round(p + (p - sl) * 3, 6)
        sigs.append({"strategy": "fakeout", "direction": "LONG", "entry": p, "sl": sl, "tp": tp, "why": f"Fakeout LONG RSI={rsi:.1f}"})
    if p < res * 1.001 and rsi and rsi > 60 and mh and mh < 0 and funding >= 0:
        sl = round(res + atr * 0.5, 6)
        tp = round(p - (sl - p) * 3, 6)
        sigs.append({"strategy": "fakeout", "direction": "SHORT", "entry": p, "sl": sl, "tp": tp, "why": f"Fakeout SHORT RSI={rsi:.1f}"})
    if p > res and rsi and 50 < rsi < 72 and vol > 150 and e20 > e50 and abs(funding) < 0.0008:
        sl = round(res - atr * 0.3, 6)
        tp = round(p + (p - sl) * 3, 6)
        sigs.append({"strategy": "breakout", "direction": "LONG", "entry": p, "sl": sl, "tp": tp, "why": f"Breakout LONG vol={vol:.0f}%"})
    if p < sup and rsi and 28 < rsi < 50 and vol > 150 and e20 < e50 and abs(funding) < 0.0008:
        sl = round(sup + atr * 0.3, 6)
        tp = round(p - (sl - p) * 3, 6)
        sigs.append({"strategy": "breakout", "direction": "SHORT", "entry": p, "sl": sl, "tp": tp, "why": f"Breakout SHORT vol={vol:.0f}%"})
    if rsi and rsi < 35 and mh and mh > 0 and p <= e200 * 1.005 and funding < -0.0003:
        sl = round(p - atr * 1.5, 6)
        tp = round(p + (p - sl) * 3, 6)
        sigs.append({"strategy": "reversal", "direction": "LONG", "entry": p, "sl": sl, "tp": tp, "why": f"Reversal LONG RSI={rsi:.1f}"})
    if rsi and rsi > 65 and mh and mh < 0 and p >= e200 * 0.995 and funding > 0.0003:
        sl = round(p + atr * 1.5, 6)
        tp = round(p - (sl - p) * 3, 6)
        sigs.append({"strategy": "reversal", "direction": "SHORT", "entry": p, "sl": sl, "tp": tp, "why": f"Reversal SHORT RSI={rsi:.1f}"})
    return sigs


def screen_coins(ex, cfg):
    result = []
    for t in ex.tickers():
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            vol = float(t.get("turnover24h", 0))
            fr = abs(float(t.get("fundingRate", 0)))
            if vol >= cfg["min_volume_24h"] and fr < cfg["max_funding_abs"]:
                result.append((sym, vol))
        except Exception:
            continue
    result.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in result[:20]]


def regime_filter(ind1h, ind4h, direction: str) -> tuple[bool, str]:
    try:
        p1, a1, b1, c1 = ind1h["price"], ind1h["ema20"], ind1h["ema50"], ind1h["ema200"]
        p4, a4, b4, c4 = ind4h["price"], ind4h["ema20"], ind4h["ema50"], ind4h["ema200"]
    except Exception:
        return False, "missing_regime_data"

    if direction == "LONG":
        ok = p1 > c1 and a1 >= b1 and p4 > c4 and a4 >= b4
    else:
        ok = p1 < c1 and a1 <= b1 and p4 < c4 and a4 <= b4
    return ok, "" if ok else "regime_mismatch"


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
    strategy = sig["strategy"]
    edge = float(avoid_pct)
    if strategy == "breakout":
        if direction == "LONG":
            ok = frac >= (1.0 - edge)
        else:
            ok = frac <= edge
    else:
        if direction == "LONG":
            ok = frac <= edge
        else:
            ok = frac >= (1.0 - edge)
    return ok, "" if ok else "middle_of_range"


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

    if sig["direction"] == "LONG":
        if rsi < 45:
            score += 8
        if mh > 0:
            score += 8
    else:
        if rsi > 55:
            score += 8
        if mh < 0:
            score += 8

    if vol >= 140:
        score += 8
        reasons.append("volume_impulse")
    if atr_ratio >= 0.008:
        score += 6
        reasons.append("high_atr")
    if abs(funding) < 0.0008:
        score += 4
    if abs(oi_change_pct) > 6:
        score -= 10
        reasons.append("oi_jump")

    if sig["strategy"] == "breakout":
        score += 3
    if sig["strategy"] == "reversal":
        score -= 1

    score = int(max(0, min(100, score)))
    return score, reasons
