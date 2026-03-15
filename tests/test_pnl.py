from exchange import sum_realized_pnl


def test_sum_realized_pnl_with_time_filter():
    rows = [
        {"execTime": "1000", "execPnl": "1.25"},
        {"execTime": "1500", "execPnl": "-0.50"},
        {"execTime": "2000", "execPnl": "0.25"},
    ]
    v = sum_realized_pnl(rows, pnl_field="execPnl", ts_field="execTime", open_time_ms=1200)
    assert v == -0.25


def test_sum_realized_pnl_none_on_empty():
    v = sum_realized_pnl([], pnl_field="closedPnl", ts_field="updatedTime", open_time_ms=0)
    assert v is None
