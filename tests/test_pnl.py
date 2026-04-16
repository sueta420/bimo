from exchange import latest_realized_pnl, normalize_order_qty, sum_realized_pnl


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


def test_latest_realized_pnl_prefers_latest_closed_record():
    rows = [
        {"createdTime": "1000", "updatedTime": "1001", "closedPnl": "-0.25"},
        {"createdTime": "2000", "updatedTime": "2001", "closedPnl": "-0.42"},
    ]
    v = latest_realized_pnl(rows, pnl_field="closedPnl", ts_fields=["createdTime", "updatedTime"], open_time_ms=1500)
    assert v == -0.42


def test_normalize_order_qty_by_step():
    assert normalize_order_qty(0.0161, 0.01, 0.01) == "0.01"
    assert normalize_order_qty(0.0229, 0.01, 0.01) == "0.02"
    assert normalize_order_qty(0.5345, 0.1, 0.1) == "0.5"
    assert normalize_order_qty(3.5968, 0.1, 0.1) == "3.5"
    assert normalize_order_qty(8593.5262, 100, 100) == "8500"


def test_normalize_order_qty_respects_min_notional():
    assert normalize_order_qty(0.01, 0.01, 0.01, min_notional=5, ref_price=2200) == "0.01"
    assert normalize_order_qty(0.01, 0.01, 0.01, min_notional=5, ref_price=1000) == "0.01"
    assert normalize_order_qty(0.01, 0.01, 0.01, min_notional=5, ref_price=400) == "0.02"
