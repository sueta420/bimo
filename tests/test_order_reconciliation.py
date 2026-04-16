import logging
import asyncio

from exchange import normalize_order_status
from execution import Agent
from portfolio import STATE_OPEN, STATE_ORDER_SENT, STATE_PARTIALLY_CLOSED, STATE_RECONCILED, Trade


def test_normalize_order_status_maps_partial_cancel():
    row = {
        "orderStatus": "PartiallyFilledCanceled",
        "cumExecQty": "0.25",
        "avgPrice": "101.5",
        "rejectReason": "EC_NoError",
        "orderId": "abc123",
    }

    normalized = normalize_order_status(row)

    assert normalized["status"] == "PARTIALLY_FILLED_CANCELED"
    assert normalized["filled_qty"] == 0.25
    assert normalized["avg_price"] == 101.5
    assert normalized["order_id"] == "abc123"


def test_handle_order_state_without_position_reconciles_rejected_order():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    saved_trades = []
    state_changes = []
    sent_messages = []

    agent._save_trade = lambda trade: saved_trades.append(trade.state)
    agent._set_state = lambda trade, new_state, reason, payload=None: (
        setattr(trade, "state", new_state),
        state_changes.append((new_state, reason, payload or {})),
    )
    agent.tg = type("Notifier", (), {"send": lambda self, text, force=False: sent_messages.append((text, force))})()

    trade = Trade(
        id="t1",
        symbol="BTCUSDT",
        direction="LONG",
        strategy="breakout",
        entry=100.0,
        sl=99.0,
        tp=103.0,
        size_usd=100.0,
        confidence=100,
        open_time="2026-04-15T00:00:00+00:00",
        order_id="oid-1",
        state=STATE_ORDER_SENT,
    )

    remove = agent._handle_order_state_without_position(
        trade,
        {
            "status": "REJECTED",
            "raw_status": "Rejected",
            "filled_qty": 0.0,
            "avg_price": 0.0,
            "reject_reason": "balance",
        },
    )

    assert remove is True
    assert trade.state == STATE_RECONCILED
    assert trade.close_reason == "order_rejected"
    assert state_changes[-1][0] == STATE_RECONCILED
    assert "balance" in sent_messages[-1][0]


def test_close_all_keeps_trade_open_when_exchange_does_not_confirm_close():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    agent.cfg = {"close_verify_retries": 2, "close_verify_delay_ms": 0}
    agent.stats = type("Stats", (), {"trades": []})()
    agent._save_day_stats = lambda: None
    state_changes = []
    sent_errors = []

    trade = Trade(
        id="t2",
        symbol="ETHUSDT",
        direction="LONG",
        strategy="breakout",
        entry=200.0,
        sl=190.0,
        tp=230.0,
        size_usd=100.0,
        confidence=100,
        open_time="2026-04-15T00:00:00+00:00",
        state=STATE_OPEN,
        open_time_ms=1000,
    )
    agent.open_trades = {"ETHUSDT": trade}
    agent._set_state = lambda trade, new_state, reason, payload=None: state_changes.append((new_state, reason))
    agent.tg = type("Notifier", (), {"send": lambda self, text, force=False: None, "send_error": lambda self, key, text: sent_errors.append((key, text))})()

    class Exchange:
        def __init__(self):
            self.calls = 0

        def get_pos(self, symbol):
            self.calls += 1
            return {"size": "0.5", "markPrice": "201"}

        def close_pos(self, symbol, direction, qty):
            return None

        def realized_pnl_from_exchange(self, symbol, open_time_ms):
            return 1.0

    agent.ex = Exchange()

    asyncio.run(agent.close_all("manual"))

    assert "ETHUSDT" in agent.open_trades
    assert agent.stats.trades == []
    assert state_changes == []
    assert sent_errors


def test_close_all_reconciles_after_confirmed_close():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    agent.cfg = {"close_verify_retries": 3, "close_verify_delay_ms": 0}
    agent.stats = type("Stats", (), {"trades": []})()
    agent._save_day_stats = lambda: None
    state_changes = []
    sent_messages = []

    trade = Trade(
        id="t3",
        symbol="SOLUSDT",
        direction="SHORT",
        strategy="reversal",
        entry=150.0,
        sl=155.0,
        tp=135.0,
        size_usd=100.0,
        confidence=100,
        open_time="2026-04-15T00:00:00+00:00",
        state=STATE_OPEN,
        open_time_ms=1000,
    )
    agent.open_trades = {"SOLUSDT": trade}
    agent._set_state = lambda trade, new_state, reason, payload=None: (
        setattr(trade, "state", new_state),
        state_changes.append((new_state, reason, payload or {})),
    )
    agent.tg = type("Notifier", (), {"send": lambda self, text, force=False: sent_messages.append((text, force)), "send_error": lambda self, key, text: None})()

    class Exchange:
        def __init__(self):
            self.calls = 0

        def get_pos(self, symbol):
            self.calls += 1
            if self.calls == 1:
                return {"size": "1.0", "markPrice": "149"}
            return None

        def close_pos(self, symbol, direction, qty):
            return None

        def realized_pnl_from_exchange(self, symbol, open_time_ms):
            return 2.5

    agent.ex = Exchange()

    asyncio.run(agent.close_all("manual"))

    assert "SOLUSDT" not in agent.open_trades
    assert len(agent.stats.trades) == 1
    assert agent.stats.trades[0].pnl_usd == 2.5
    assert state_changes[-1][0] == STATE_RECONCILED
    assert sent_messages


def test_record_critical_error_stops_agent_after_threshold():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    saved = []
    sent_errors = []
    agent.cfg = {"critical_error_stop_count": 2}
    agent.stats = type(
        "Stats",
        (),
        {
            "critical_errors": 0,
            "halt_reason": "",
            "stopped": False,
        },
    )()
    agent._save_day_stats = lambda: saved.append(
        (agent.stats.critical_errors, agent.stats.halt_reason, agent.stats.stopped)
    )
    agent.tg = type("Notifier", (), {"send_error": lambda self, key, text: sent_errors.append((key, text))})()

    agent._record_critical_error("sync_trade_failed", RuntimeError("boom"), "BTCUSDT")
    assert agent.stats.critical_errors == 1
    assert agent.stats.stopped is False

    agent._record_critical_error("close_all_failed", RuntimeError("boom2"), "ETHUSDT")
    assert agent.stats.critical_errors == 2
    assert agent.stats.stopped is True
    assert "close_all_failed" in agent.stats.halt_reason
    assert sent_errors[-1][0] == "critical_stop"


def test_sync_trades_marks_partial_close_and_scales_risk_once():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    agent.cfg = {
        "be_trigger_r": 1.0,
        "trailing_trigger_r": 1.5,
        "trailing_lock_r": 0.8,
        "critical_error_stop_count": 3,
    }
    agent.stats = type(
        "Stats",
        (),
        {"trades": [], "consecutive_losses": 0, "critical_errors": 0, "halt_reason": "", "stopped": False},
    )()
    sent_messages = []
    state_changes = []
    agent._save_day_stats = lambda: None
    agent._save_trade = lambda trade: None
    agent._set_stop_cooldown = lambda symbol: None
    agent.tg = type(
        "Notifier",
        (),
        {
            "send": lambda self, text, force=False: sent_messages.append((text, force)),
            "send_error": lambda self, key, text: None,
        },
    )()
    agent._set_state = lambda trade, new_state, reason, payload=None: (
        setattr(trade, "state", new_state),
        state_changes.append((new_state, reason, payload or {})),
    )

    trade = Trade(
        id="t4",
        symbol="XRPUSDT",
        direction="LONG",
        strategy="breakout",
        entry=2.0,
        sl=1.8,
        tp=2.6,
        size_usd=4.0,
        confidence=100,
        open_time="2026-04-15T00:00:00+00:00",
        state=STATE_OPEN,
        qty=2.0,
        filled_qty=2.0,
        risk_usd=10.0,
        open_time_ms=1000,
    )
    agent.open_trades = {"XRPUSDT": trade}

    class Exchange:
        def get_pos(self, symbol):
            return {"size": "1.0", "markPrice": "2.1", "avgPrice": "2.0"}

        def realized_pnl_from_exchange(self, symbol, open_time_ms):
            return 0.0

        def get_order_state(self, symbol, order_id):
            return None

        def update_stop_loss(self, symbol, stop_loss):
            return None

    agent.ex = Exchange()

    asyncio.run(agent.sync_trades())
    assert trade.state == STATE_PARTIALLY_CLOSED
    assert trade.qty == 1.0
    assert trade.filled_qty == 2.0
    assert trade.risk_usd == 5.0
    assert sent_messages

    sent_before = len(sent_messages)
    state_before = list(state_changes)
    asyncio.run(agent.sync_trades())
    assert trade.risk_usd == 5.0
    assert len(sent_messages) == sent_before
    assert state_before == state_changes


def test_recover_external_position_enters_safe_mode_without_full_protection():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    agent.open_trades = {}
    agent.stats = type(
        "Stats",
        (),
        {
            "trades": [],
            "stopped": False,
            "halt_reason": "",
        },
    )()
    saved_trades = []
    runtime_events = []
    safe_modes = []
    agent._save_trade = lambda trade: saved_trades.append(trade)
    agent._save_day_stats = lambda: None
    agent.store = type(
        "Store",
        (),
        {"add_event": lambda self, trade_id, symbol, from_state, to_state, reason, payload=None: runtime_events.append((symbol, reason))},
    )()
    agent.tg = type("Notifier", (), {"send_error": lambda self, key, text: None})()
    agent._enter_safe_mode = lambda reason, symbol="": (
        setattr(agent.stats, "stopped", True),
        setattr(agent.stats, "halt_reason", reason),
        safe_modes.append((reason, symbol)),
    )

    class Exchange:
        def list_positions(self):
            return [{"symbol": "BTCUSDT", "side": "Buy", "avgPrice": "100000", "size": "0.01"}]

        def position_protection(self, pos):
            return {"sl": 0.0, "tp": 0.0}

    agent.ex = Exchange()

    agent._recover_from_exchange()

    assert "BTCUSDT" in agent.open_trades
    assert saved_trades[0].sl == 0.0
    assert saved_trades[0].tp == 0.0
    assert safe_modes == [("recovered_position_without_full_protection", "BTCUSDT")]
    assert agent.stats.stopped is True
    assert runtime_events == [("BTCUSDT", "startup_found_exchange_position")]


def test_log_skip_tracks_reason_counts():
    agent = Agent.__new__(Agent)
    agent.log = logging.getLogger("test-agent")
    saved = []
    agent.stats = type("Stats", (), {"skip_reasons": {}})()
    agent._save_day_stats = lambda: saved.append(dict(agent.stats.skip_reasons))

    agent._log_skip("BTCUSDT", "regime_filter", "x")
    agent._log_skip("ETHUSDT", "regime_filter", "y")
    agent._log_skip("SOLUSDT", "min_rr_ratio", "z")

    assert agent.stats.skip_reasons == {"regime_filter": 2, "min_rr_ratio": 1}
    assert saved[-1] == {"regime_filter": 2, "min_rr_ratio": 1}
