"""
BYBIT FUTURES TRADING AGENT v2.0
- Apple Silicon (M1/M2/M3) совместим
- Библиотека ta (не pandas-ta!)
- Python 3.11
"""

import asyncio
import json
import logging
import os
import time
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import ta
from openai import OpenAI
from pybit.unified_trading import HTTP


def load_dotenv_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                if not key:
                    continue
                if value and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv_file(os.path.join(BASE_DIR, ".env"))


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


CONFIG = {
    "testnet":        True,
    "api_key":        os.getenv("BYBIT_API_KEY", ""),
    "api_secret":     os.getenv("BYBIT_API_SECRET", ""),
    "openai_key":     os.getenv("OPENAI_API_KEY", ""),
    "trade_size_usd": 5,
    "max_trades_per_day": 5,
    "risk_reward":    3,
    "max_leverage":   10,
    "stop_after_losses": 3,
    "min_volume_24h": 50_000_000,
    "max_funding_abs": 0.001,
    "min_atr_ratio":  0.005,
    "signal_tf":      "15",
    "llm_model":      os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    "llm_min_conf":   70,
    "close_hour_utc": 23,
    "close_min_utc":  45,
    "cycle_sec":      900,
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    "tg_min_interval_sec": env_int("TG_MIN_INTERVAL_SEC", 3),
    "tg_error_cooldown_sec": env_int("TG_ERROR_COOLDOWN_SEC", 180),
    "tg_timeout_sec": env_int("TG_TIMEOUT_SEC", 10),
}


@dataclass
class Trade:
    id: str; symbol: str; direction: str; strategy: str
    entry: float; sl: float; tp: float; size_usd: float
    confidence: int; open_time: str
    close_time: Optional[str] = None
    close_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    close_reason: Optional[str] = None
    order_id: Optional[str] = None


@dataclass
class DayStats:
    date: str = ""
    trades: list = field(default_factory=list)
    signals_total: int = 0
    opened: int = 0
    skipped: int = 0
    consecutive_losses: int = 0
    stopped: bool = False


class TelegramNotifier:
    def __init__(self, cfg, log):
        self.log = log
        self.token = str(cfg.get("telegram_bot_token", "")).strip()
        self.chat_id = str(cfg.get("telegram_chat_id", "")).strip()
        self.enabled = bool(self.token and self.chat_id)
        self.min_interval_sec = max(int(cfg.get("tg_min_interval_sec", 3)), 0)
        self.error_cooldown_sec = max(int(cfg.get("tg_error_cooldown_sec", 180)), 1)
        self.timeout_sec = max(int(cfg.get("tg_timeout_sec", 10)), 1)
        self.last_sent_ts = 0.0
        self.last_error_ts = {}

    def send(self, text: str, force: bool = False) -> bool:
        if not self.enabled:
            return False
        body = (text or "").strip()
        if not body:
            return False
        now = time.time()
        if not force and (now - self.last_sent_ts) < self.min_interval_sec:
            return False
        payload = urlencode({"chat_id": self.chat_id, "text": body[:4096]}).encode("utf-8")
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        req = Request(url=url, data=payload, method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(req, timeout=self.timeout_sec) as r:
                r.read()
            self.last_sent_ts = now
            return True
        except Exception as ex:
            self.log.error(f"Telegram send failed: {ex}")
            return False

    def send_error(self, key: str, text: str) -> bool:
        if not self.enabled:
            return False
        now = time.time()
        last = self.last_error_ts.get(key, 0.0)
        if (now - last) < self.error_cooldown_sec:
            return False
        self.last_error_ts[key] = now
        return self.send(f"[ERROR] {text}", force=True)


class BybitClient:
    def __init__(self, cfg):
        self.s = HTTP(testnet=cfg["testnet"],
                      api_key=cfg["api_key"],
                      api_secret=cfg["api_secret"])

    def tickers(self):
        return self.s.get_tickers(category="linear")["result"]["list"]

    def klines(self, symbol, interval, limit=200):
        r = self.s.get_kline(category="linear", symbol=symbol,
                             interval=interval, limit=limit)
        df = pd.DataFrame(r["result"]["list"],
                          columns=["ts","open","high","low","close","volume","turnover"])
        df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
        df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
        return df.sort_values("ts").reset_index(drop=True)

    def funding(self, symbol):
        r = self.s.get_tickers(category="linear", symbol=symbol)
        return float(r["result"]["list"][0].get("fundingRate", 0))

    def open_interest(self, symbol):
        try:
            r = self.s.get_open_interest(category="linear", symbol=symbol,
                                         intervalTime="1h", limit=2)
            items = r["result"]["list"]
            if len(items) >= 2:
                c = float(items[0]["openInterest"])
                p = float(items[1]["openInterest"])
                return {"change_pct": round((c-p)/p*100 if p else 0, 2)}
        except Exception:
            pass
        return {"change_pct": 0}

    def place_order(self, symbol, direction, size_usd, entry, sl, tp, leverage):
        side = "Buy" if direction == "LONG" else "Sell"
        qty = round(size_usd * leverage / entry, 4)
        try:
            self.s.set_leverage(category="linear", symbol=symbol,
                                buyLeverage=str(leverage), sellLeverage=str(leverage))
        except Exception:
            pass
        r = self.s.place_order(category="linear", symbol=symbol, side=side,
                               orderType="Market", qty=str(qty),
                               stopLoss=str(round(sl,6)), takeProfit=str(round(tp,6)),
                               slTriggerBy="MarkPrice", tpTriggerBy="MarkPrice")
        return r["result"]["orderId"]

    def close_pos(self, symbol, direction, qty):
        side = "Sell" if direction == "LONG" else "Buy"
        self.s.place_order(category="linear", symbol=symbol, side=side,
                           orderType="Market", qty=str(qty), reduceOnly=True)

    def get_pos(self, symbol):
        r = self.s.get_positions(category="linear", symbol=symbol)
        for p in r["result"]["list"]:
            if float(p.get("size", 0)) > 0:
                return p
        return None


def calc_indicators(df):
    c = df["close"]; h = df["high"]; l = df["low"]; v = df["volume"]

    def safe(series):
        try:
            val = float(series.iloc[-1])
            return round(val, 6) if not np.isnan(val) else None
        except Exception:
            return None

    rsi   = safe(ta.momentum.RSIIndicator(close=c, window=14).rsi())
    macd  = ta.trend.MACD(close=c, window_slow=26, window_fast=12, window_sign=9)
    mhist = safe(macd.macd_diff())
    ema20 = safe(ta.trend.EMAIndicator(close=c, window=20).ema_indicator())
    ema50 = safe(ta.trend.EMAIndicator(close=c, window=50).ema_indicator())
    ema200= safe(ta.trend.EMAIndicator(close=c, window=200).ema_indicator())
    atr   = safe(ta.volatility.AverageTrueRange(high=h,low=l,close=c,window=14).average_true_range())
    bb    = ta.volatility.BollingerBands(close=c, window=20, window_dev=2)
    bb_up = safe(bb.bollinger_hband())
    bb_dn = safe(bb.bollinger_lband())
    vol_ma = v.rolling(20).mean().iloc[-1]
    vol_r  = round(v.iloc[-1] / vol_ma * 100 if vol_ma else 100, 1)
    recent = df.tail(48)
    price  = round(float(c.iloc[-1]), 6)

    return {"price": price, "rsi": rsi, "macd_hist": mhist,
            "ema20": ema20, "ema50": ema50, "ema200": ema200,
            "atr": atr, "bb_up": bb_up, "bb_dn": bb_dn, "vol_ratio": vol_r,
            "support": round(float(recent["low"].min()),6),
            "resistance": round(float(recent["high"].max()),6)}


def detect_signals(ind, funding):
    sigs = []
    p=ind["price"]; atr=ind.get("atr") or 0; rsi=ind.get("rsi")
    mh=ind.get("macd_hist"); vol=ind.get("vol_ratio",0)
    e20=ind.get("ema20") or p; e50=ind.get("ema50") or p; e200=ind.get("ema200") or p
    sup=ind["support"]; res=ind["resistance"]
    if not atr: return sigs

    if p>sup*0.999 and rsi and rsi<40 and mh and mh>0 and funding<=0:
        sl=round(sup-atr*0.5,6); tp=round(p+(p-sl)*3,6)
        sigs.append({"strategy":"fakeout","direction":"LONG","entry":p,"sl":sl,"tp":tp,
                     "why":f"Fakeout LONG RSI={rsi:.1f}"})
    if p<res*1.001 and rsi and rsi>60 and mh and mh<0 and funding>=0:
        sl=round(res+atr*0.5,6); tp=round(p-(sl-p)*3,6)
        sigs.append({"strategy":"fakeout","direction":"SHORT","entry":p,"sl":sl,"tp":tp,
                     "why":f"Fakeout SHORT RSI={rsi:.1f}"})
    if p>res and rsi and 50<rsi<72 and vol>150 and e20>e50 and abs(funding)<0.0008:
        sl=round(res-atr*0.3,6); tp=round(p+(p-sl)*3,6)
        sigs.append({"strategy":"breakout","direction":"LONG","entry":p,"sl":sl,"tp":tp,
                     "why":f"Breakout LONG vol={vol:.0f}%"})
    if p<sup and rsi and 28<rsi<50 and vol>150 and e20<e50 and abs(funding)<0.0008:
        sl=round(sup+atr*0.3,6); tp=round(p-(sl-p)*3,6)
        sigs.append({"strategy":"breakout","direction":"SHORT","entry":p,"sl":sl,"tp":tp,
                     "why":f"Breakout SHORT vol={vol:.0f}%"})
    if rsi and rsi<35 and mh and mh>0 and p<=e200*1.005 and funding<-0.0003:
        sl=round(p-atr*1.5,6); tp=round(p+(p-sl)*3,6)
        sigs.append({"strategy":"reversal","direction":"LONG","entry":p,"sl":sl,"tp":tp,
                     "why":f"Reversal LONG RSI={rsi:.1f}"})
    if rsi and rsi>65 and mh and mh<0 and p>=e200*0.995 and funding>0.0003:
        sl=round(p+atr*1.5,6); tp=round(p-(sl-p)*3,6)
        sigs.append({"strategy":"reversal","direction":"SHORT","entry":p,"sl":sl,"tp":tp,
                     "why":f"Reversal SHORT RSI={rsi:.1f}"})
    return sigs


class LLM:
    def __init__(self, key, model):
        self.client = OpenAI(api_key=key)
        self.model = model

    def _ask(self, prompt: str, max_tokens: int) -> str:
        try:
            r = self.client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=max_tokens,
            )
            text = (getattr(r, "output_text", "") or "").strip()
            if text:
                return text
        except Exception as ex_resp:
            resp_error = ex_resp
        else:
            resp_error = None

        try:
            r = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            content = r.choices[0].message.content
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                return "\n".join(parts).strip()
            return str(content).strip()
        except Exception as ex_chat:
            if resp_error:
                raise RuntimeError(f"responses API: {resp_error}; chat API: {ex_chat}") from ex_chat
            raise

    def decide(self, symbol, sig, ind, funding, oi):
        e=sig["entry"]; sl=sig["sl"]; tp=sig["tp"]
        sl_pct=abs(e-sl)/e*100
        rr=abs(tp-e)/abs(e-sl) if abs(e-sl)>0 else 0
        prompt = (
            f"Ты строгий торговый аналитик фьючерсов. Пропускай только сильные сигналы.\n\n"
            f"Монета: {symbol} | {sig['strategy'].upper()} | {sig['direction']}\n"
            f"Цена: {ind['price']} | RSI: {ind.get('rsi')} | MACD hist: {ind.get('macd_hist')}\n"
            f"EMA20/50/200: {ind.get('ema20')}/{ind.get('ema50')}/{ind.get('ema200')}\n"
            f"Volume/MA: {ind.get('vol_ratio')}% | ATR: {ind.get('atr')}\n"
            f"Funding: {funding*100:.4f}% | OI change: {oi.get('change_pct')}%\n"
            f"Support: {ind['support']} | Resistance: {ind['resistance']}\n"
            f"Сигнал: {sig['why']}\n"
            f"Entry={e} | SL={sl} ({sl_pct:.2f}%) | TP={tp} | R:R=1:{rr:.1f}\n\n"
            f"Ответь ТОЛЬКО валидным JSON без markdown:\n"
            f'{{"decision":"TRADE или SKIP","confidence":0-100,"strategy":"{sig["strategy"]}",'
            f'"direction":"{sig["direction"]}","entry":{e},"sl":{sl},"tp":{tp},'
            f'"reasoning":"причина на русском","risks":["риск1"]}}'
        )
        try:
            text = self._ask(prompt, max_tokens=400).replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as ex:
            return {"decision":"SKIP","confidence":0,"reasoning":str(ex),"risks":[]}

    def summary(self, stats):
        lines = [f"- {t.symbol} {t.direction} {t.strategy}: {t.pnl_usd or 0:+.4f}$"
                 for t in stats.trades]
        try:
            prompt = (
                f"Итог торгового дня {stats.date}. 2-3 предложения:\n"
                + ("\n".join(lines) if lines else "Сделок не было")
            )
            return self._ask(prompt, max_tokens=250)
        except Exception as ex:
            return str(ex)


def screen_coins(ex, cfg):
    result = []
    for t in ex.tickers():
        sym = t.get("symbol","")
        if not sym.endswith("USDT"): continue
        try:
            vol = float(t.get("turnover24h",0))
            fr  = abs(float(t.get("fundingRate",0)))
            if vol >= cfg["min_volume_24h"] and fr < cfg["max_funding_abs"]:
                result.append((sym, vol))
        except Exception:
            continue
    result.sort(key=lambda x: x[1], reverse=True)
    return [s for s,_ in result[:20]]


def make_report(stats, llm, max_trades_per_day):
    wins   = [t for t in stats.trades if (t.pnl_usd or 0)>0]
    losses = [t for t in stats.trades if (t.pnl_usd or 0)<0]
    pnl    = sum((t.pnl_usd or 0) for t in stats.trades)
    wr     = len(wins)/len(stats.trades)*100 if stats.trades else 0
    closed = len(stats.trades)
    lines  = []
    for i,t in enumerate(stats.trades,1):
        icon = "WIN" if (t.pnl_usd or 0)>0 else ("LOSS" if (t.pnl_usd or 0)<0 else "BE")
        lines.append(f"  #{i}[{icon}] {t.symbol} {t.direction} {t.strategy} "
                     f"PnL:{(t.pnl_usd or 0):+.4f}$ conf:{t.confidence}% {t.close_reason}")
    report = (
        f"\n{'='*52}\n  ОТЧЁТ {stats.date}\n{'='*52}\n"
        f"  Сделок:{closed}/{max_trades_per_day} WIN:{len(wins)} LOSS:{len(losses)}\n"
        f"  Винрейт:{wr:.1f}% | PnL:{pnl:+.4f}$\n"
        f"  Сигналов:{stats.signals_total} | Скип:{stats.skipped} | Открыто:{stats.opened} | Закрыто:{closed}\n"
        f"{'─'*52}\n"
        + ("\n".join(lines) if lines else "  Сделок не было")
        + f"\n{'─'*52}\n  {llm.summary(stats)}\n{'='*52}\n"
    )
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"report_{stats.date.replace('-','')}.txt")
    with open(path,"w",encoding="utf-8") as f:
        f.write(report)
    return report, path


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ex  = BybitClient(cfg)
        self.llm = LLM(cfg["openai_key"], cfg["llm_model"])
        self.stats = DayStats(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.open_trades: dict[str, Trade] = {}
        log_path = os.path.join(BASE_DIR, f"agent_{self.stats.date}.log")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.StreamHandler(),
                      logging.FileHandler(log_path, encoding="utf-8")])
        self.log = logging.getLogger("agent")
        self.tg = TelegramNotifier(cfg, self.log)

    def day_summary_text(self, title, open_positions):
        wins = [t for t in self.stats.trades if (t.pnl_usd or 0) > 0]
        losses = [t for t in self.stats.trades if (t.pnl_usd or 0) < 0]
        pnl = sum((t.pnl_usd or 0) for t in self.stats.trades)
        wr = len(wins) / len(self.stats.trades) * 100 if self.stats.trades else 0
        return (
            f"{title}\n"
            f"Дата: {self.stats.date}\n"
            f"Сигналов: {self.stats.signals_total}\n"
            f"Скип: {self.stats.skipped}\n"
            f"Открыто: {self.stats.opened}\n"
            f"Закрыто: {len(self.stats.trades)}\n"
            f"Открытые позиции: {open_positions}\n"
            f"WIN: {len(wins)} LOSS: {len(losses)} WR: {wr:.1f}%\n"
            f"Итог PnL: {pnl:+.4f}$"
        )

    def day_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.stats.date:
            open_before_close = len(self.open_trades)
            self.close_all("новый день")
            report, path = make_report(self.stats, self.llm, self.cfg["max_trades_per_day"])
            print(report); self.log.info(f"Отчёт: {path}")
            self.tg.send(self.day_summary_text("Суточный отчёт агента", open_before_close), force=True)
            self.stats = DayStats(date=today)

    def close_all(self, reason):
        for sym, t in list(self.open_trades.items()):
            try:
                pos = self.ex.get_pos(sym)
                if pos:
                    qty = float(pos["size"])
                    self.ex.close_pos(sym, t.direction, qty)
                    cp = float(pos.get("markPrice", t.entry))
                    pnl = ((cp-t.entry) if t.direction=="LONG" else (t.entry-cp)) / t.entry * self.cfg["trade_size_usd"]
                    t.close_time=datetime.now(timezone.utc).isoformat(); t.close_price=cp
                    t.close_reason=reason; t.pnl_usd=round(pnl,4)
                    self.stats.trades.append(t)
                    self.log.info(f"Закрыта {sym}: {pnl:+.4f}$ ({reason})")
                    self.tg.send(
                        f"Закрыта позиция {sym} {t.direction}\n"
                        f"Причина: {reason}\n"
                        f"PnL: {pnl:+.4f}$",
                        force=True,
                    )
                del self.open_trades[sym]
            except Exception as ex:
                self.log.error(f"close_all {sym}: {ex}")
                self.tg.send_error("close_all", f"close_all {sym}: {ex}")

    def sync_trades(self):
        for sym, t in list(self.open_trades.items()):
            try:
                if not self.ex.get_pos(sym):
                    t.close_time=datetime.now(timezone.utc).isoformat()
                    t.close_reason="SL/TP"; t.pnl_usd=None
                    self.stats.trades.append(t)
                    self.stats.consecutive_losses = self.stats.consecutive_losses+1 if (t.pnl_usd or 0)<0 else 0
                    self.log.info(f"SL/TP сработал: {sym}")
                    self.tg.send(
                        f"Позиция закрыта по SL/TP: {sym} {t.direction}\n"
                        f"Strategy: {t.strategy}\n"
                        f"Entry: {t.entry} | SL: {t.sl} | TP: {t.tp}",
                        force=True,
                    )
                    del self.open_trades[sym]
            except Exception as ex:
                self.log.error(f"sync {sym}: {ex}")
                self.tg.send_error("sync", f"sync {sym}: {ex}")

    async def cycle(self):
        now = datetime.now(timezone.utc)
        if now.hour==self.cfg["close_hour_utc"] and now.minute>=self.cfg["close_min_utc"]:
            self.close_all("23:45 UTC"); return
        self.sync_trades()
        if self.stats.stopped:
            self.log.warning("Стоп — торговля на сегодня завершена"); return
        if self.stats.consecutive_losses >= self.cfg["stop_after_losses"]:
            self.stats.stopped=True; self.log.warning("3 убытка подряд — стоп"); return
        done = len(self.stats.trades) + len(self.open_trades)
        if done >= self.cfg["max_trades_per_day"]: return

        self.log.info("── Скрининг...")
        symbols = screen_coins(self.ex, self.cfg)
        self.log.info(f"Топ монеты: {symbols[:6]}")
        all_sigs = []
        for sym in symbols:
            if sym in self.open_trades: continue
            try:
                df  = self.ex.klines(sym, self.cfg["signal_tf"])
                ind = calc_indicators(df)
                if not ind.get("atr") or ind["atr"]/ind["price"] < self.cfg["min_atr_ratio"]: continue
                fr = self.ex.funding(sym)
                oi = self.ex.open_interest(sym)
                for s in detect_signals(ind, fr):
                    all_sigs.append({"sym":sym,"sig":s,"ind":ind,"fr":fr,"oi":oi})
                time.sleep(0.15)
            except Exception as ex:
                self.log.error(f"Анализ {sym}: {ex}")
                self.tg.send_error("analysis", f"Анализ {sym}: {ex}")

        if not all_sigs: self.log.info("Сигналов нет"); return
        self.stats.signals_total += len(all_sigs)
        self.log.info(f"Найдено {len(all_sigs)} сигналов → LLM")
        slots = self.cfg["max_trades_per_day"] - done

        for item in all_sigs[:slots+3]:
            if len(self.open_trades)+len(self.stats.trades) >= self.cfg["max_trades_per_day"]: break
            sym = item["sym"]
            if sym in self.open_trades: continue
            dec  = self.llm.decide(sym, item["sig"], item["ind"], item["fr"], item["oi"])
            conf = dec.get("confidence",0)
            self.log.info(f"LLM {sym}: {dec.get('decision')} conf={conf}% | {dec.get('reasoning','')[:55]}")
            if dec.get("decision")!="TRADE" or conf<self.cfg["llm_min_conf"]:
                self.stats.skipped+=1; continue
            try:
                e=dec["entry"]; sl=dec["sl"]; tp=dec["tp"]; dr=dec["direction"]
                sl_dist=abs(e-sl)
                lev=min(max(int(self.cfg["trade_size_usd"]/(sl_dist/e*self.cfg["trade_size_usd"]*10)),1),self.cfg["max_leverage"])
                if self.cfg["testnet"]:
                    oid=f"TEST_{sym}_{int(time.time())}"
                    self.log.info(f"[TESTNET] {dr} {sym} @ {e} SL={sl} TP={tp} {lev}x")
                else:
                    oid=self.ex.place_order(sym,dr,self.cfg["trade_size_usd"],e,sl,tp,lev)
                t=Trade(id=f"{sym}_{int(time.time())}",symbol=sym,direction=dr,
                        strategy=dec.get("strategy",item["sig"]["strategy"]),
                        entry=e,sl=sl,tp=tp,size_usd=self.cfg["trade_size_usd"],
                        confidence=conf,open_time=datetime.now(timezone.utc).isoformat(),order_id=oid)
                self.open_trades[sym]=t
                self.stats.opened += 1
                self.log.info(f"ОТКРЫТА: {dr} {sym} {lev}x conf={conf}%")
                self.tg.send(
                    f"Открыта позиция {sym} {dr}\n"
                    f"Strategy: {t.strategy} | Conf: {conf}% | Lev: {lev}x\n"
                    f"Entry: {e} | SL: {sl} | TP: {tp}",
                    force=True,
                )
            except Exception as ex:
                self.log.error(f"Открытие {sym}: {ex}")
                self.tg.send_error("open_trade", f"Открытие {sym}: {ex}")

    async def run(self):
        mode = "TESTNET" if self.cfg["testnet"] else "*** REAL MONEY ***"
        self.log.info("="*52)
        self.log.info(f"FUTURES AGENT START | {mode}")
        self.log.info(f"Model:{self.cfg['llm_model']} | ${self.cfg['trade_size_usd']} R:R=1:{self.cfg['risk_reward']} | max:{self.cfg['max_trades_per_day']}/day")
        self.log.info("="*52)
        self.tg.send(
            f"Агент запущен\n"
            f"Режим: {mode}\n"
            f"Модель: {self.cfg['llm_model']}\n"
            f"Лимит/день: {self.cfg['max_trades_per_day']} | Размер: ${self.cfg['trade_size_usd']}",
            force=True
        )
        while True:
            try:
                self.day_reset()
                await self.cycle()
            except KeyboardInterrupt:
                open_before_close = len(self.open_trades)
                self.close_all("остановка")
                report, _ = make_report(self.stats, self.llm, self.cfg["max_trades_per_day"])
                self.tg.send(self.day_summary_text("Агент остановлен. Итог дня", open_before_close), force=True)
                print(report); break
            except Exception as ex:
                self.log.error(f"Цикл: {ex}")
                self.tg.send_error("cycle", f"Цикл: {ex}")
            self.log.info(f"Следующий цикл через {self.cfg['cycle_sec']}с")
            await asyncio.sleep(self.cfg["cycle_sec"])


if __name__ == "__main__":
    import sys
    missing = [k for k in ("api_key", "api_secret", "openai_key") if not CONFIG[k]]
    if missing:
        m = {"api_key": "BYBIT_API_KEY", "api_secret": "BYBIT_API_SECRET", "openai_key": "OPENAI_API_KEY"}
        print("\n❌ Задай переменные окружения:")
        for k in missing: print(f"   export {m[k]}='твой_ключ'")
        print("   или создай файл .env рядом со скриптом")
        sys.exit(1)
    print(f"\n🤖 Агент запущен [{'TESTNET' if CONFIG['testnet'] else 'REAL'}] | Ctrl+C для остановки\n")
    asyncio.run(Agent(CONFIG).run())
