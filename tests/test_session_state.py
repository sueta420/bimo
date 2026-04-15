from portfolio import DayStats, StateStore, Trade


def test_day_stats_roundtrip(tmp_path):
    store = StateStore(str(tmp_path / "state.sqlite3"))
    trade = Trade(
        id="t1",
        symbol="BTCUSDT",
        direction="LONG",
        strategy="breakout",
        entry=100.0,
        sl=99.0,
        tp=103.0,
        size_usd=500.0,
        confidence=100,
        open_time="2026-04-15T00:00:00+00:00",
        pnl_usd=4.25,
        close_reason="tp",
    )
    stats = DayStats(
        date="2026-04-15",
        trades=[trade],
        signals_total=7,
        opened=2,
        skipped=4,
        consecutive_losses=1,
        stopped=True,
        critical_errors=2,
        halt_reason="sync_trade_failed: boom",
    )

    store.save_day_stats(stats)
    loaded = store.load_day_stats()

    assert loaded is not None
    assert loaded.date == "2026-04-15"
    assert loaded.signals_total == 7
    assert loaded.opened == 2
    assert loaded.skipped == 4
    assert loaded.consecutive_losses == 1
    assert loaded.stopped is True
    assert loaded.critical_errors == 2
    assert loaded.halt_reason == "sync_trade_failed: boom"
    assert len(loaded.trades) == 1
    assert loaded.trades[0].symbol == "BTCUSDT"
    assert loaded.trades[0].pnl_usd == 4.25
