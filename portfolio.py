import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


STATE_SIGNALLED = "SIGNALLED"
STATE_ORDER_SENT = "ORDER_SENT"
STATE_FILLED = "FILLED"
STATE_OPEN = "OPEN"
STATE_PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
STATE_CLOSED = "CLOSED"
STATE_RECONCILED = "RECONCILED"

ACTIVE_STATES = {
    STATE_SIGNALLED,
    STATE_ORDER_SENT,
    STATE_FILLED,
    STATE_OPEN,
    STATE_PARTIALLY_CLOSED,
}


@dataclass
class Trade:
    id: str
    symbol: str
    direction: str
    strategy: str
    entry: float
    sl: float
    tp: float
    size_usd: float
    confidence: int
    open_time: str
    close_time: Optional[str] = None
    close_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    close_reason: Optional[str] = None
    order_id: Optional[str] = None
    state: str = STATE_SIGNALLED
    qty: float = 0.0
    filled_qty: float = 0.0
    risk_usd: float = 0.0
    open_time_ms: int = 0
    score: int = 0
    notes: str = ""
    realized_r: Optional[float] = None
    hold_minutes: Optional[float] = None
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    total_fee_usd: Optional[float] = None


@dataclass
class DayStats:
    date: str = ""
    trades: list[Trade] = field(default_factory=list)
    signals_total: int = 0
    opened: int = 0
    skipped: int = 0
    consecutive_losses: int = 0
    stopped: bool = False
    critical_errors: int = 0
    halt_reason: str = ""


@dataclass
class SessionState:
    day_stats: DayStats = field(default_factory=DayStats)


def side_exposure_risk(open_trades: dict[str, Trade]) -> dict[str, float]:
    exp = {"LONG": 0.0, "SHORT": 0.0}
    for t in open_trades.values():
        if t.state in ACTIVE_STATES:
            exp[t.direction] = exp.get(t.direction, 0.0) + float(t.risk_usd or 0.0)
    return exp


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                state TEXT NOT NULL,
                direction TEXT NOT NULL,
                strategy TEXT NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                size_usd REAL NOT NULL,
                confidence INTEGER NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT,
                close_price REAL,
                pnl_usd REAL,
                close_reason TEXT,
                order_id TEXT,
                qty REAL NOT NULL DEFAULT 0,
                filled_qty REAL NOT NULL DEFAULT 0,
                risk_usd REAL NOT NULL DEFAULT 0,
                open_time_ms INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                realized_r REAL,
                hold_minutes REAL,
                mfe_r REAL,
                mae_r REAL,
                total_fee_usd REAL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                trade_id TEXT,
                symbol TEXT,
                from_state TEXT,
                to_state TEXT,
                reason TEXT,
                payload_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_rules (
                symbol TEXT PRIMARY KEY,
                cooldown_until TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for col, col_type in (
            ("realized_r", "REAL"),
            ("hold_minutes", "REAL"),
            ("mfe_r", "REAL"),
            ("mae_r", "REAL"),
            ("total_fee_usd", "REAL"),
        ):
            try:
                cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def save_trade(self, trade: Trade):
        payload = json.dumps(asdict(trade), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO trades (
                id, symbol, state, direction, strategy, entry, sl, tp, size_usd, confidence,
                open_time, close_time, close_price, pnl_usd, close_reason, order_id,
                qty, filled_qty, risk_usd, open_time_ms, score, notes, payload_json, updated_at
                , realized_r, hold_minutes, mfe_r, mae_r, total_fee_usd
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(id) DO UPDATE SET
                symbol=excluded.symbol,
                state=excluded.state,
                direction=excluded.direction,
                strategy=excluded.strategy,
                entry=excluded.entry,
                sl=excluded.sl,
                tp=excluded.tp,
                size_usd=excluded.size_usd,
                confidence=excluded.confidence,
                open_time=excluded.open_time,
                close_time=excluded.close_time,
                close_price=excluded.close_price,
                pnl_usd=excluded.pnl_usd,
                close_reason=excluded.close_reason,
                order_id=excluded.order_id,
                qty=excluded.qty,
                filled_qty=excluded.filled_qty,
                risk_usd=excluded.risk_usd,
                open_time_ms=excluded.open_time_ms,
                score=excluded.score,
                notes=excluded.notes,
                realized_r=excluded.realized_r,
                hold_minutes=excluded.hold_minutes,
                mfe_r=excluded.mfe_r,
                mae_r=excluded.mae_r,
                total_fee_usd=excluded.total_fee_usd,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                trade.id, trade.symbol, trade.state, trade.direction, trade.strategy, trade.entry, trade.sl, trade.tp,
                trade.size_usd, trade.confidence, trade.open_time, trade.close_time, trade.close_price, trade.pnl_usd,
                trade.close_reason, trade.order_id, trade.qty, trade.filled_qty, trade.risk_usd, trade.open_time_ms,
                trade.score, trade.notes, payload, now, trade.realized_r, trade.hold_minutes, trade.mfe_r, trade.mae_r, trade.total_fee_usd,
            ),
        )
        self.conn.commit()

    def add_event(
        self,
        trade_id: str,
        symbol: str,
        from_state: str,
        to_state: str,
        reason: str,
        payload: Optional[dict] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO events (ts, trade_id, symbol, from_state, to_state, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now, trade_id, symbol, from_state, to_state, reason, json.dumps(payload or {}, ensure_ascii=False)),
        )
        self.conn.commit()

    def load_active_trades(self) -> list[Trade]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT payload_json FROM trades WHERE state IN (?, ?, ?, ?, ?)",
            (STATE_SIGNALLED, STATE_ORDER_SENT, STATE_FILLED, STATE_OPEN, STATE_PARTIALLY_CLOSED),
        )
        out = []
        for row in cur.fetchall():
            try:
                out.append(Trade(**json.loads(row["payload_json"])))
            except Exception:
                continue
        return out

    def set_cooldown(self, symbol: str, minutes: int):
        until = (datetime.now(timezone.utc) + timedelta(minutes=max(minutes, 0))).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO symbol_rules (symbol, cooldown_until) VALUES (?, ?)
            ON CONFLICT(symbol) DO UPDATE SET cooldown_until=excluded.cooldown_until
            """,
            (symbol, until),
        )
        self.conn.commit()

    def get_cooldown_until(self, symbol: str) -> Optional[datetime]:
        cur = self.conn.cursor()
        cur.execute("SELECT cooldown_until FROM symbol_rules WHERE symbol=?", (symbol,))
        row = cur.fetchone()
        if not row or not row["cooldown_until"]:
            return None
        try:
            return datetime.fromisoformat(row["cooldown_until"])
        except Exception:
            return None

    def in_cooldown(self, symbol: str) -> bool:
        until = self.get_cooldown_until(symbol)
        if not until:
            return False
        return datetime.now(timezone.utc) < until

    def save_day_stats(self, stats: DayStats):
        payload = json.dumps(asdict(stats), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO session_state (key, payload_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            ("day_stats", payload, now),
        )
        self.conn.commit()

    def load_day_stats(self) -> Optional[DayStats]:
        cur = self.conn.cursor()
        cur.execute("SELECT payload_json FROM session_state WHERE key=?", ("day_stats",))
        row = cur.fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
            trades = [Trade(**item) for item in payload.get("trades", [])]
            payload["trades"] = trades
            return DayStats(**payload)
        except Exception:
            return None

    def save_runtime_value(self, key: str, payload: dict):
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO session_state (key, payload_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (key, json.dumps(payload, ensure_ascii=False), now),
        )
        self.conn.commit()

    def load_runtime_value(self, key: str) -> Optional[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT payload_json FROM session_state WHERE key=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
