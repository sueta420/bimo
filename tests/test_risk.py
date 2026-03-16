from portfolio import STATE_OPEN, Trade
from risk import check_side_exposure, correlation_allowed, floor_to_step, size_position


def test_size_position_equity_based():
    cfg = {
        "risk_per_trade_pct": 0.5,
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
        "max_leverage": 10,
    }
    wallet = {"equity": 1000, "available": 1000}
    limits = {"tick_size": 0.1, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 5}
    res, err = size_position(cfg, "LONG", 100.0, 99.0, 0.0001, wallet, limits)
    assert err == ""
    assert res["qty"] > 0
    assert res["risk_usd"] <= 5.1
    assert res["notional"] <= wallet["available"] * cfg["max_leverage"]


def test_side_exposure_limit():
    cfg = {"max_side_risk_pct": 1.0}
    t = Trade(
        id="1",
        symbol="BTCUSDT",
        direction="LONG",
        strategy="x",
        entry=1,
        sl=0.9,
        tp=1.3,
        size_usd=10,
        confidence=0,
        open_time="x",
        state=STATE_OPEN,
        risk_usd=6.0,
    )
    ok, reason = check_side_exposure(cfg, {"BTCUSDT": t}, "LONG", new_risk_usd=5, equity=1000)
    assert not ok
    assert "side_risk_limit" in reason


def test_correlation_guard_blocks():
    t = Trade(
        id="1",
        symbol="ETHUSDT",
        direction="LONG",
        strategy="x",
        entry=1,
        sl=0.9,
        tp=1.3,
        size_usd=10,
        confidence=0,
        open_time="x",
        state=STATE_OPEN,
        risk_usd=3.0,
    )
    returns = {
        "SOLUSDT": [0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01, 0.02, 0.01],
        "ETHUSDT": [0.011, 0.021, 0.012, 0.019, 0.011, 0.021, 0.011, 0.022, 0.010, 0.020, 0.011],
    }
    ok, reason = correlation_allowed(
        symbol="SOLUSDT",
        direction="LONG",
        open_trades={"ETHUSDT": t},
        return_series=returns,
        threshold=0.8,
        max_correlated_per_side=1,
    )
    assert not ok
    assert "corr_hits" in reason


def test_floor_to_step_precision():
    assert floor_to_step(0.0161, 0.01) == 0.01


def test_size_position_rejects_missing_qty_step():
    cfg = {
        "risk_per_trade_pct": 0.5,
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
        "max_leverage": 10,
    }
    wallet = {"equity": 1000, "available": 1000}
    limits = {"tick_size": 0.1, "qty_step": 0.0, "min_qty": 0.001, "min_notional": 5}
    res, err = size_position(cfg, "LONG", 100.0, 99.0, 0.0001, wallet, limits)
    assert res is None
    assert err == "qty_step<=0"


def test_size_position_with_fixed_risk_usd_mode():
    cfg = {
        "position_sizing_mode": "risk_usd",
        "risk_per_trade_usd": 2.0,
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
        "max_leverage": 10,
    }
    wallet = {"equity": 31, "available": 31}
    limits = {"tick_size": 0.1, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 5}
    res, err = size_position(cfg, "LONG", 100.0, 99.0, 0.0001, wallet, limits)
    assert err == ""
    assert res["sizing_mode"] == "risk_usd"
    assert res["risk_budget_usd"] == 2.0
    assert res["risk_usd"] <= 2.04


def test_size_position_with_fixed_notional_mode():
    cfg = {
        "position_sizing_mode": "fixed_notional_usd",
        "target_notional_usd": 5.0,
        "slippage_entry_bps": 10,
        "slippage_exit_bps": 15,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
        "max_leverage": 10,
    }
    wallet = {"equity": 31, "available": 31}
    limits = {"tick_size": 0.1, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 5}
    res, err = size_position(cfg, "LONG", 100.0, 99.0, 0.0001, wallet, limits)
    assert err == ""
    assert res["sizing_mode"] == "fixed_notional_usd"
    assert res["notional"] >= 5.0
    assert res["notional"] < 5.2
