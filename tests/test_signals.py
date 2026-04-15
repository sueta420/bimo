from signals import edge_after_costs, no_middle_range, regime_filter, score_signal


def test_regime_filter_long_ok():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100, "atr": 1.0, "vol_ratio": 125}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    ok, reason = regime_filter(ind1h, ind4h, "LONG")
    assert ok
    assert reason == ""


def test_regime_filter_short_fail():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100, "atr": 1.0, "vol_ratio": 125}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    ok, reason = regime_filter(ind1h, ind4h, "SHORT")
    assert not ok
    assert reason == "regime_countertrend"


def test_regime_filter_flat_rejects():
    ind1h = {"price": 100, "ema20": 100.2, "ema50": 100.1, "ema200": 99.9, "atr": 0.3, "vol_ratio": 90}
    ind4h = {"price": 100, "ema20": 100.15, "ema50": 100.05, "ema200": 99.95, "atr": 0.3, "vol_ratio": 90}
    ok, reason = regime_filter(ind1h, ind4h, "LONG")
    assert not ok
    assert reason == "regime_flat"


def test_regime_filter_breakout_rejects_chop():
    ind1h = {"price": 101, "ema20": 101.02, "ema50": 100.98, "ema200": 100.9, "atr": 0.5, "vol_ratio": 105}
    ind4h = {"price": 108, "ema20": 106, "ema50": 104, "ema200": 100, "atr": 1.0, "vol_ratio": 120}
    ok, reason = regime_filter(ind1h, ind4h, "LONG", "breakout")
    assert not ok
    assert reason in {"regime_chop", "regime_mismatch"}


def test_regime_filter_reversal_blocks_against_expansion():
    ind1h = {"price": 96, "ema20": 98, "ema50": 100, "ema200": 104, "atr": 1.3, "vol_ratio": 150}
    ind4h = {"price": 90, "ema20": 94, "ema50": 99, "ema200": 108, "atr": 1.6, "vol_ratio": 160}
    ok, reason = regime_filter(ind1h, ind4h, "LONG", "reversal")
    assert not ok
    assert reason == "regime_reversal_vs_expansion"


def test_no_middle_range_breakout_long():
    sig = {"entry": 99.5, "direction": "LONG", "strategy": "breakout"}
    ind = {"support": 90, "resistance": 100}
    ok, reason = no_middle_range(sig, ind, avoid_pct=0.25)
    assert ok
    assert reason == ""


def test_score_signal_bounds():
    sig = {"direction": "LONG", "strategy": "breakout", "entry": 100, "sl": 99, "tp": 103}
    ind = {
        "rsi": 40,
        "vol_ratio": 180,
        "macd_hist": 0.3,
        "atr": 2,
        "price": 100,
        "support": 90,
        "resistance": 100.2,
        "ema20": 101,
        "ema50": 100.2,
        "ema200": 98,
    }
    score, reasons = score_signal(sig, ind, funding=0.0001, oi_change_pct=1.0, regime_ok=True)
    assert 0 <= score <= 100
    assert isinstance(reasons, list)
    assert "rr_good" in reasons or "rr_excellent" in reasons


def test_score_signal_penalizes_weak_breakout_structure():
    sig = {"direction": "LONG", "strategy": "breakout", "entry": 95, "sl": 94, "tp": 97.2}
    ind = {
        "rsi": 52,
        "vol_ratio": 145,
        "macd_hist": 0.1,
        "atr": 0.6,
        "price": 95,
        "support": 90,
        "resistance": 100,
        "ema20": 95.2,
        "ema50": 95.1,
        "ema200": 94,
    }
    score, reasons = score_signal(sig, ind, funding=0.0001, oi_change_pct=1.0, regime_ok=True)
    assert "breakout_not_extended" in reasons
    assert "ema_compression" in reasons


def test_edge_after_costs_positive_for_clean_trade():
    sig = {"entry": 100, "tp": 103}
    cfg = {
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
    }
    edge = edge_after_costs(sig, cfg, funding=0.0001)
    assert edge["net_reward_per_unit"] > 0
    assert edge["edge_cost_ratio"] > 2.0


def test_edge_after_costs_detects_weak_trade():
    sig = {"entry": 100, "tp": 100.2}
    cfg = {
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
    }
    edge = edge_after_costs(sig, cfg, funding=0.0001)
    assert edge["net_reward_per_unit"] < 0
