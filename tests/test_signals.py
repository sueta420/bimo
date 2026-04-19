import pandas as pd

from signals import (
    btc_context_filter,
    calc_indicators,
    detect_signals,
    edge_after_costs,
    no_middle_range,
    regime_filter,
    regime_strategy_allowlist,
    score_signal,
    screen_coins,
    signal_quality_filter,
)


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


def test_regime_filter_range_bounce_allows_same_side_trend_without_expansion():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100, "atr": 0.7, "vol_ratio": 120}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 0.9, "vol_ratio": 125}
    ok, reason = regime_filter(ind1h, ind4h, "LONG", "range_bounce")
    assert ok
    assert reason == ""


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


def test_signal_quality_filter_rejects_volatile_fakeout():
    sig = {"entry": 100, "direction": "SHORT", "strategy": "fakeout"}
    ind = {
        "price": 100,
        "support": 90,
        "resistance": 100.5,
        "atr": 1.6,
        "vol_ratio": 120,
    }
    cfg = {
        "fakeout_edge_max_frac": 0.12,
        "fakeout_max_atr_ratio": 0.012,
        "fakeout_max_oi_change_pct": 2.0,
        "fakeout_min_vol_ratio": 90.0,
    }
    ok, reason = signal_quality_filter(sig, ind, cfg, oi_change_pct=0.5)
    assert not ok
    assert reason == "fakeout_too_volatile"


def test_screen_coins_respects_blacklist_and_limit():
    class Exchange:
        def tickers(self):
            return [
                {"symbol": "BTCUSDT", "turnover24h": "100000000", "fundingRate": "0.0001"},
                {"symbol": "FARTCOINUSDT", "turnover24h": "90000000", "fundingRate": "0.0001"},
                {"symbol": "ETHUSDT", "turnover24h": "80000000", "fundingRate": "0.0001"},
            ]

    cfg = {
        "min_volume_24h": 50_000_000,
        "max_funding_abs": 0.001,
        "max_scan_symbols": 2,
        "symbol_blacklist": ["FARTCOINUSDT"],
    }
    out = screen_coins(Exchange(), cfg)
    assert out == ["BTCUSDT", "ETHUSDT"]


def test_btc_context_filter_blocks_alt_countertrend():
    sig = {"direction": "SHORT", "strategy": "fakeout"}
    btc1h = {"price": 110, "ema20": 108, "ema50": 106, "ema200": 100, "atr": 1.0, "vol_ratio": 120}
    btc4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    ok, reason = btc_context_filter("ETHUSDT", sig, btc1h, btc4h)
    assert not ok
    assert reason in {"btc_countertrend", "btc_fakeout_countertrend"}


def test_btc_context_filter_allows_btc_itself():
    sig = {"direction": "SHORT", "strategy": "fakeout"}
    btc1h = {"price": 110, "ema20": 108, "ema50": 106, "ema200": 100, "atr": 1.0, "vol_ratio": 120}
    btc4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    ok, reason = btc_context_filter("BTCUSDT", sig, btc1h, btc4h)
    assert ok
    assert reason == ""


def test_calc_indicators_handles_short_history_without_crashing():
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2026-04-17", periods=6, freq="15min"),
            "open": [1, 1.1, 1.2, 1.15, 1.18, 1.2],
            "high": [1.1, 1.2, 1.25, 1.2, 1.22, 1.24],
            "low": [0.98, 1.05, 1.1, 1.1, 1.15, 1.18],
            "close": [1.05, 1.15, 1.18, 1.16, 1.2, 1.22],
            "volume": [100, 120, 130, 125, 140, 150],
            "turnover": [0, 0, 0, 0, 0, 0],
        }
    )
    ind = calc_indicators(df)
    assert ind["price"] == 1.22
    assert ind["support"] == 0.98
    assert ind["resistance"] == 1.25
    assert ind["atr"] is not None


def test_regime_strategy_allowlist_prefers_trend_setups_in_trend():
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100, "atr": 1.0, "vol_ratio": 125}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    out = regime_strategy_allowlist(ind1h, ind4h)
    assert "trend_pullback" in out
    assert "breakout" in out
    assert "range_bounce" not in out


def test_detect_signals_adds_trend_pullback_when_trend_retraces():
    ind = {
        "price": 102.0,
        "rsi": 51.0,
        "macd_hist": 0.02,
        "ema20": 101.5,
        "ema50": 100.8,
        "ema200": 98.0,
        "atr": 1.0,
        "vol_ratio": 110.0,
        "support": 100.0,
        "resistance": 106.0,
    }
    ind1h = {"price": 110, "ema20": 108, "ema50": 105, "ema200": 100, "atr": 1.0, "vol_ratio": 125}
    ind4h = {"price": 130, "ema20": 125, "ema50": 120, "ema200": 110, "atr": 1.2, "vol_ratio": 130}
    sigs = detect_signals(ind, funding=0.0001, ind1h=ind1h, ind4h=ind4h)
    assert any(s["strategy"] == "trend_pullback" and s["direction"] == "LONG" for s in sigs)


def test_detect_signals_adds_range_bounce_in_chop():
    ind = {
        "price": 90.8,
        "rsi": 43.0,
        "macd_hist": -0.01,
        "ema20": 91.0,
        "ema50": 90.9,
        "ema200": 90.8,
        "atr": 0.8,
        "vol_ratio": 95.0,
        "support": 90.0,
        "resistance": 95.0,
    }
    ind1h = {"price": 91.0, "ema20": 91.1, "ema50": 91.0, "ema200": 90.9, "atr": 0.4, "vol_ratio": 95}
    ind4h = {"price": 92.0, "ema20": 92.1, "ema50": 92.0, "ema200": 91.9, "atr": 0.5, "vol_ratio": 92}
    sigs = detect_signals(ind, funding=0.0, ind1h=ind1h, ind4h=ind4h)
    assert any(s["strategy"] == "range_bounce" and s["direction"] == "LONG" for s in sigs)
