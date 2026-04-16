import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Optional

import pandas as pd
from pybit.unified_trading import HTTP


def sum_realized_pnl(rows: list[dict], pnl_field: str, ts_field: str, open_time_ms: int = 0) -> Optional[float]:
    total = 0.0
    got = False
    for row in rows:
        try:
            ts = int(row.get(ts_field, 0) or 0)
            if open_time_ms and ts and ts < open_time_ms:
                continue
            total += float(row.get(pnl_field, 0) or 0)
            got = True
        except Exception:
            continue
    return round(total, 6) if got else None


def latest_realized_pnl(rows: list[dict], pnl_field: str, ts_fields: list[str], open_time_ms: int = 0) -> Optional[float]:
    latest_ts = -1
    latest_val = None
    for row in rows:
        try:
            ts = 0
            for field in ts_fields:
                ts = int(row.get(field, 0) or 0)
                if ts:
                    break
            if open_time_ms and ts and ts < open_time_ms:
                continue
            val = float(row.get(pnl_field, 0) or 0)
            if ts >= latest_ts:
                latest_ts = ts
                latest_val = val
        except Exception:
            continue
    return round(latest_val, 6) if latest_val is not None else None


def latest_closed_trade_info(rows: list[dict], open_time_ms: int = 0) -> Optional[dict]:
    latest_ts = -1
    latest_row = None
    for row in rows:
        try:
            created_ts = int(row.get("createdTime", 0) or 0)
            updated_ts = int(row.get("updatedTime", 0) or 0)
            ts = created_ts or updated_ts
            if open_time_ms and ts and ts < open_time_ms:
                continue
            if ts >= latest_ts:
                latest_ts = ts
                latest_row = row
        except Exception:
            continue
    if not latest_row:
        return None
    try:
        return {
            "pnl_usd": round(float(latest_row.get("closedPnl", 0) or 0), 6),
            "exit_price": float(latest_row.get("avgExitPrice", 0) or 0),
            "total_fee_usd": round(
                float(latest_row.get("openFee", 0) or 0) + float(latest_row.get("closeFee", 0) or 0),
                6,
            ),
            "closed_size": float(latest_row.get("closedSize", latest_row.get("qty", 0)) or 0),
            "created_time_ms": int(latest_row.get("createdTime", 0) or 0),
            "updated_time_ms": int(latest_row.get("updatedTime", 0) or 0),
        }
    except Exception:
        return None


def _d(value) -> Decimal:
    return Decimal(str(value))


def _fmt_decimal(value: Decimal) -> str:
    s = format(value, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _first_non_empty(*values):
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def normalize_order_status(row: dict) -> dict:
    raw_status = str(
        _first_non_empty(
            row.get("orderStatus"),
            row.get("order_status"),
            row.get("status"),
            "UNKNOWN",
        )
    )
    filled_qty = float(
        _first_non_empty(
            row.get("cumExecQty"),
            row.get("cum_exec_qty"),
            row.get("cumFilledQty"),
            row.get("cum_filled_qty"),
            0.0,
        )
        or 0.0
    )
    avg_price = float(
        _first_non_empty(
            row.get("avgPrice"),
            row.get("avg_price"),
            row.get("price"),
            0.0,
        )
        or 0.0
    )
    reject_reason = str(
        _first_non_empty(
            row.get("rejectReason"),
            row.get("reject_reason"),
            row.get("cancelType"),
            row.get("cancel_type"),
            "",
        )
        or ""
    )

    status_upper = raw_status.upper()
    if status_upper in {"NEW", "CREATED", "UNTRIGGERED", "TRIGGERED", "ACTIVE", "PENDING"}:
        normalized = "PENDING"
    elif status_upper in {"PARTIALLYFILLED", "PARTIALLY_FILLED"}:
        normalized = "PARTIALLY_FILLED"
    elif status_upper in {"FILLED"}:
        normalized = "FILLED"
    elif status_upper in {"CANCELLED", "CANCELED", "DEACTIVATED"}:
        normalized = "CANCELLED"
    elif status_upper in {"REJECTED"}:
        normalized = "REJECTED"
    elif status_upper in {"PARTIALLYFILLEDCANCELED", "PARTIALLY_FILLED_CANCELED"}:
        normalized = "PARTIALLY_FILLED_CANCELED"
    else:
        normalized = "UNKNOWN"

    return {
        "status": normalized,
        "raw_status": raw_status,
        "filled_qty": filled_qty,
        "avg_price": avg_price,
        "reject_reason": reject_reason,
        "order_id": str(_first_non_empty(row.get("orderId"), row.get("order_id"), "") or ""),
    }


def normalize_order_qty(
    qty: float,
    qty_step: float,
    min_qty: float,
    min_notional: float = 0.0,
    ref_price: float = 0.0,
) -> str:
    q = _d(qty)
    step = _d(qty_step)
    minimum = _d(min_qty)
    min_notional_dec = _d(min_notional)
    ref_price_dec = _d(ref_price)
    if q <= 0:
        raise ValueError("qty<=0")
    if step <= 0:
        raise ValueError("qty_step<=0")

    q = (q / step).to_integral_value(rounding=ROUND_DOWN) * step
    minimum = (minimum / step).to_integral_value(rounding=ROUND_UP) * step
    if min_notional_dec > 0 and ref_price_dec > 0:
        by_notional = (min_notional_dec / ref_price_dec / step).to_integral_value(rounding=ROUND_UP) * step
        if by_notional > minimum:
            minimum = by_notional
    if q < minimum:
        q = minimum
    if q <= 0:
        raise ValueError("qty<=0 after normalize")
    return _fmt_decimal(q)


class BybitClient:
    def __init__(self, cfg):
        self.s = HTTP(
            testnet=cfg["testnet"],
            api_key=cfg["api_key"],
            api_secret=cfg["api_secret"],
        )
        self._instrument_cache = {}
        self._ticker_cache = {}
        self._ticker_cache_ts = 0.0

    def tickers(self):
        now = time.time()
        if self._ticker_cache and (now - self._ticker_cache_ts) < 3:
            return list(self._ticker_cache.values())
        out = self.s.get_tickers(category="linear")["result"]["list"]
        self._ticker_cache = {x.get("symbol"): x for x in out}
        self._ticker_cache_ts = now
        return out

    def ticker(self, symbol: str):
        if symbol in self._ticker_cache:
            return self._ticker_cache[symbol]
        r = self.s.get_tickers(category="linear", symbol=symbol)
        items = r.get("result", {}).get("list", [])
        if items:
            self._ticker_cache[symbol] = items[0]
            return items[0]
        return {}

    def klines(self, symbol, interval, limit=200):
        r = self.s.get_kline(category="linear", symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(
            r["result"]["list"],
            columns=["ts", "open", "high", "low", "close", "volume", "turnover"],
        )
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
        return df.sort_values("ts").reset_index(drop=True)

    def funding_meta(self, symbol):
        t = self.ticker(symbol)
        rate = float(t.get("fundingRate", 0) or 0)
        next_ts = int(t.get("nextFundingTime", 0) or 0)
        return {"rate": rate, "next_funding_ms": next_ts}

    def open_interest(self, symbol):
        try:
            r = self.s.get_open_interest(category="linear", symbol=symbol, intervalTime="1h", limit=2)
            items = r["result"]["list"]
            if len(items) >= 2:
                c = float(items[0]["openInterest"])
                p = float(items[1]["openInterest"])
                return {"change_pct": round((c - p) / p * 100 if p else 0, 2)}
        except Exception:
            pass
        return {"change_pct": 0}

    def place_order(self, symbol, direction, qty, sl, tp, leverage):
        side = "Buy" if direction == "LONG" else "Sell"
        limits = self.instrument_constraints(symbol)
        tick = self.ticker(symbol)
        ref_price = float(tick.get("markPrice") or tick.get("lastPrice") or 0.0)
        qty_str = normalize_order_qty(
            qty,
            limits["qty_step"],
            limits["min_qty"],
            limits.get("min_notional", 0.0),
            ref_price,
        )
        try:
            self.s.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception:
            pass
        r = self.s.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty_str,
            stopLoss=str(round(sl, 6)),
            takeProfit=str(round(tp, 6)),
            slTriggerBy="MarkPrice",
            tpTriggerBy="MarkPrice",
        )
        return r["result"]["orderId"]

    def close_pos(self, symbol, direction, qty):
        side = "Sell" if direction == "LONG" else "Buy"
        limits = self.instrument_constraints(symbol)
        qty_str = normalize_order_qty(qty, limits["qty_step"], limits["min_qty"])
        self.s.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty_str,
            reduceOnly=True,
        )

    def update_stop_loss(self, symbol: str, stop_loss: float):
        self.s.set_trading_stop(
            category="linear",
            symbol=symbol,
            stopLoss=str(round(stop_loss, 6)),
            tpslMode="Full",
            slTriggerBy="MarkPrice",
        )

    def get_pos(self, symbol):
        r = self.s.get_positions(category="linear", symbol=symbol)
        for p in r["result"]["list"]:
            if float(p.get("size", 0) or 0) > 0:
                return p
        return None

    def list_positions(self):
        r = self.s.get_positions(category="linear", settleCoin="USDT")
        out = []
        for p in r.get("result", {}).get("list", []):
            try:
                size = float(p.get("size", 0) or 0)
            except Exception:
                size = 0.0
            if size > 0:
                out.append(p)
        return out

    def position_protection(self, pos: dict) -> dict:
        try:
            sl = float(pos.get("stopLoss", 0) or 0)
        except Exception:
            sl = 0.0
        try:
            tp = float(pos.get("takeProfit", 0) or 0)
        except Exception:
            tp = 0.0
        return {"sl": sl, "tp": tp}

    def wallet_snapshot(self):
        r = self.s.get_wallet_balance(accountType="UNIFIED")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            return {"equity": 0.0, "available": 0.0}
        top = lst[0]
        equity = float(top.get("totalEquity", 0) or 0)
        available = float(top.get("totalAvailableBalance", 0) or 0)
        return {"equity": equity, "available": available}

    def instrument_constraints(self, symbol):
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        r = self.s.get_instruments_info(category="linear", symbol=symbol)
        items = r.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"instrument info not found for {symbol}")
        it = items[0]
        lot = it.get("lotSizeFilter", {})
        price = it.get("priceFilter", {})
        data = {
            "tick_size": float(price.get("tickSize") or price.get("tick_size") or 0.0),
            "qty_step": float(lot.get("qtyStep") or lot.get("qty_step") or 0.0),
            "min_qty": float(lot.get("minOrderQty") or lot.get("min_qty") or 0.0),
            "min_notional": float(lot.get("minNotionalValue") or lot.get("min_notional") or 0.0),
        }
        if data["qty_step"] <= 0:
            raise RuntimeError(f"invalid qty_step for {symbol}: {data}")
        self._instrument_cache[symbol] = data
        return data

    def realized_pnl_from_exchange(self, symbol, open_time_ms: int = 0):
        try:
            params = {"category": "linear", "symbol": symbol, "limit": 100}
            if open_time_ms > 0:
                params["startTime"] = int(open_time_ms)
            r = self.s.get_closed_pnl(**params)
            rows = r.get("result", {}).get("list", [])
            v = latest_realized_pnl(
                rows,
                pnl_field="closedPnl",
                ts_fields=["createdTime", "updatedTime"],
                open_time_ms=open_time_ms,
            )
            if v is not None:
                return v
        except Exception:
            pass

        try:
            r = self.s.get_executions(category="linear", symbol=symbol, limit=200)
            rows = r.get("result", {}).get("list", [])
            v = sum_realized_pnl(rows, pnl_field="execPnl", ts_field="execTime", open_time_ms=open_time_ms)
            if v is not None:
                return v
        except Exception:
            pass

        try:
            params = {"category": "linear", "symbol": symbol, "limit": 100}
            if open_time_ms > 0:
                params["startTime"] = int(open_time_ms)
            r = self.s.get_closed_pnl(**params)
            rows = r.get("result", {}).get("list", [])
            v = sum_realized_pnl(rows, pnl_field="closedPnl", ts_field="updatedTime", open_time_ms=open_time_ms)
            if v is not None:
                return v
            return latest_realized_pnl(
                rows,
                pnl_field="closedPnl",
                ts_fields=["updatedTime", "createdTime"],
                open_time_ms=open_time_ms,
            )
        except Exception:
            return None

    def closed_trade_info(self, symbol: str, open_time_ms: int = 0) -> Optional[dict]:
        try:
            params = {"category": "linear", "symbol": symbol, "limit": 100}
            if open_time_ms > 0:
                params["startTime"] = int(open_time_ms)
            r = self.s.get_closed_pnl(**params)
            rows = r.get("result", {}).get("list", [])
            return latest_closed_trade_info(rows, open_time_ms=open_time_ms)
        except Exception:
            return None

    def get_order_state(self, symbol: str, order_id: str) -> Optional[dict]:
        if not order_id:
            return None

        try:
            r = self.s.get_open_orders(category="linear", symbol=symbol, orderId=order_id)
            rows = r.get("result", {}).get("list", [])
            for row in rows:
                current_id = str(_first_non_empty(row.get("orderId"), row.get("order_id"), "") or "")
                if current_id == order_id:
                    return normalize_order_status(row)
        except Exception:
            pass

        try:
            r = self.s.get_order_history(category="linear", symbol=symbol, orderId=order_id, limit=20)
            rows = r.get("result", {}).get("list", [])
            for row in rows:
                current_id = str(_first_non_empty(row.get("orderId"), row.get("order_id"), "") or "")
                if current_id == order_id:
                    return normalize_order_status(row)
        except Exception:
            pass

        return None
