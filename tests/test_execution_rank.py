from execution import make_period_report, planned_exit_metrics, should_use_llm_rank, summarize_reasons
from portfolio import Trade


def test_summarize_reasons_limits_output():
    text = summarize_reasons(["a", "b", "c", "d", "e"], limit=3)
    assert text == "a,b,c"


def test_should_use_llm_rank_requires_close_scores():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 81}, {"score": 79}, {"score": 76}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert ok
    assert reason == "use_llm_rank"


def test_should_use_llm_rank_skips_clear_winner():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 88}, {"score": 77}, {"score": 74}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert not ok
    assert reason == "top_score_clear_winner"


def test_should_use_llm_rank_skips_low_scores():
    cfg = {
        "use_llm_secondary_rank": True,
        "llm_rank_top_n": 3,
        "llm_rank_min_score": 72,
        "llm_rank_max_score_spread": 6,
    }
    candidates = [{"score": 69}, {"score": 68}, {"score": 67}]
    ok, reason = should_use_llm_rank(candidates, cfg)
    assert not ok
    assert reason == "top_score_below_min"


def test_planned_exit_metrics_long_stop_slippage():
    trade = Trade(
        id="t1",
        symbol="ADAUSDT",
        direction="LONG",
        strategy="range_bounce",
        entry=0.2523,
        sl=0.2509,
        tp=0.2560,
        size_usd=59.0,
        confidence=80,
        open_time="2026-04-18T10:57:29+00:00",
        close_time="2026-04-18T11:51:01+00:00",
        close_price=0.2506,
        pnl_usd=-0.52,
        qty=237.0,
        filled_qty=237.0,
    )
    metrics = planned_exit_metrics(trade)
    assert metrics["planned_kind"] == "SL"
    assert round(metrics["planned_price"], 4) == 0.2509
    assert round(metrics["actual_price"], 4) == 0.2506
    assert round(metrics["slippage_price"], 4) == -0.0003
    assert round(metrics["slippage_pnl_usd"], 4) == -0.0711


def test_make_period_report_writes_weekly_summary(tmp_path):
    trade = Trade(
        id="t1",
        symbol="MUSDT",
        direction="LONG",
        strategy="range_bounce",
        entry=3.4556,
        sl=3.3719,
        tp=3.7110,
        size_usd=48.0,
        confidence=95,
        open_time="2026-04-18T10:57:29+00:00",
        close_time="2026-04-18T11:51:01+00:00",
        close_price=3.7110,
        pnl_usd=3.50,
        close_reason="tp",
        score=95,
        realized_r=3.0,
        hold_minutes=54,
    )
    report, path = make_period_report(
        "WEEKLY REPORT",
        "last_7d_2026-04-19",
        [trade],
        "Europe/Moscow",
        str(tmp_path),
        "weekly_report",
    )
    assert "WEEKLY REPORT" in report
    assert "range_bounce" in report
    assert path.endswith("weekly_report_last_7d_2026-04-19.txt")
