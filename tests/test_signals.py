from signals import no_middle_range, regime_filter, score_signal


def test_regime_filter_long_ok():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110}
    ok, reason = regime_filter(ind1h, ind4h, "LONG")
    assert ok
    assert reason == ""


def test_regime_filter_short_fail():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110}
    ok, reason = regime_filter(ind1h, ind4h, "SHORT")
    assert not ok
    assert reason == "regime_mismatch"


def test_no_middle_range_breakout_long():
    sig = {"entry": 99.5, "direction": "LONG", "strategy": "breakout"}
    ind = {"support": 90, "resistance": 100}
    ok, reason = no_middle_range(sig, ind, avoid_pct=0.25)
    assert ok
    assert reason == ""


def test_score_signal_bounds():
    sig = {"direction": "LONG", "strategy": "breakout"}
    ind = {"rsi": 40, "vol_ratio": 180, "macd_hist": 0.3, "atr": 2, "price": 100}
    score, reasons = score_signal(sig, ind, funding=0.0001, oi_change_pct=1.0, regime_ok=True)
    assert 0 <= score <= 100
    assert isinstance(reasons, list)
