from risk import check_side_exposure, size_position


def _base_cfg():
    return {
        "position_sizing_mode": "fixed_margin_usd",
        "target_margin_usd": 10.0,
        "target_risk_usd": 1.5,
        "min_risk_utilization": 0.8,
        "target_leverage": 5,
        "dynamic_leverage_enabled": True,
        "fakeout_target_leverage": 4,
        "breakout_target_leverage": 5,
        "reversal_target_leverage": 3,
        "dynamic_leverage_high_score": 80,
        "dynamic_leverage_low_score": 72,
        "dynamic_leverage_high_score_bonus": 1,
        "dynamic_leverage_low_score_cut": 1,
        "dynamic_leverage_high_atr_ratio": 0.012,
        "dynamic_leverage_high_atr_cut": 1,
        "max_risk_per_trade_usd": 1.5,
        "slippage_entry_bps": 10.0,
        "slippage_exit_bps": 15.0,
        "taker_fee_bps": 5.5,
        "funding_reserve_rate": 0.0005,
        "max_leverage": 20,
    }


def _wallet():
    return {"equity": 58.0, "available": 58.0}


def _limits():
    return {"qty_step": 1.0, "min_qty": 1.0, "min_notional": 5.0, "tick_size": 0.0001}


def test_fixed_margin_rejects_too_small_risk_utilization():
    sizing, reason = size_position(
        _base_cfg(),
        "LONG",
        entry=0.2522,
        sl=0.2509,
        funding_rate=-0.000066,
        wallet=_wallet(),
        limits=_limits(),
        strategy="range_bounce",
        score=100,
        atr_ratio=0.0049,
    )
    assert sizing is None
    assert reason.startswith("risk_too_small")


def test_fixed_margin_uses_dynamic_target_leverage_for_required_leverage():
    cfg = _base_cfg()
    sizing, reason = size_position(
        cfg,
        "LONG",
        entry=10.0,
        sl=9.7,
        funding_rate=0.0,
        wallet=_wallet(),
        limits={"qty_step": 0.1, "min_qty": 0.1, "min_notional": 5.0, "tick_size": 0.1},
        strategy="fakeout",
        score=85,
        atr_ratio=0.005,
    )
    assert reason == ""
    assert sizing is not None
    assert sizing["target_leverage"] == 5
    assert sizing["leverage"] == 5


def test_side_exposure_allows_single_trade_when_pct_limit_is_too_low():
    cfg = _base_cfg() | {
        "max_side_risk_pct": 1.5,
        "max_side_risk_usd": 0.0,
    }
    ok, reason = check_side_exposure(cfg, {}, "LONG", new_risk_usd=1.48, equity=58.0)
    assert ok
    assert reason == ""


def test_side_exposure_uses_absolute_usd_limit_when_configured():
    cfg = _base_cfg() | {
        "max_side_risk_pct": 1.5,
        "max_side_risk_usd": 3.0,
    }
    ok, reason = check_side_exposure(cfg, {}, "LONG", new_risk_usd=2.4, equity=58.0)
    assert ok
    assert reason == ""
