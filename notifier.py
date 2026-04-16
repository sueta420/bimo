import json
import time
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from openai import OpenAI


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
        req = Request(
            url=url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
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


class LLMExplainer:
    def __init__(self, api_key: str, model: str, enabled: bool = True):
        self.enabled = bool(enabled and api_key and model)
        self.model = model
        self.client = OpenAI(api_key=api_key) if self.enabled else None

    def _ask(self, prompt: str, max_tokens: int = 180) -> str:
        if not self.enabled:
            return ""
        try:
            r = self.client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=max_tokens,
            )
            txt = (getattr(r, "output_text", "") or "").strip()
            if txt:
                return txt
        except Exception:
            pass
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
                for x in content:
                    if isinstance(x, dict) and x.get("type") == "text":
                        parts.append(x.get("text", ""))
                return "\n".join(parts).strip()
            return str(content).strip()
        except Exception:
            return ""

    def explain(self, context: dict, decision: str, reasons: list[str]) -> str:
        if not self.enabled:
            return ""
        prompt = (
            "Ты помощник трейдера. Дай краткое объяснение (1-2 предложения, русский язык). "
            f"Решение: {decision}. Причины: {', '.join(reasons[:6])}. "
            f"Контекст: {json.dumps(context, ensure_ascii=False)}"
        )
        return self._ask(prompt, max_tokens=180)

    def rank_candidates(self, items: list[dict]) -> list[dict]:
        if not self.enabled or len(items) < 2:
            return items
        brief = [
            {
                "symbol": x.get("sym"),
                "score": x.get("score"),
                "rr": round(float(x.get("rr", 0.0) or 0.0), 3),
                "direction": x.get("sig", {}).get("direction"),
                "strategy": x.get("sig", {}).get("strategy"),
                "risk_usd": x.get("sizing", {}).get("risk_usd"),
                "funding": round(float(x.get("fr", 0.0) or 0.0), 6),
                "oi_change_pct": round(float(x.get("oi", {}).get("change_pct", 0.0) or 0.0), 3),
                "edge_cost_ratio": round(float(x.get("edge", {}).get("edge_cost_ratio", 0.0) or 0.0), 3),
                "net_reward_pct": round(float(x.get("edge", {}).get("net_reward_pct", 0.0) or 0.0), 3),
                "atr_ratio": round(float(x.get("ind", {}).get("atr", 0.0) or 0.0) / max(float(x.get("ind", {}).get("price", 1.0) or 1.0), 1e-9), 6),
                "reasons": list(x.get("reasons", [])[:5]),
            }
            for x in items
        ]
        prompt = (
            "Отранжируй кандидаты от лучшего к худшему. "
            "Входные данные уже прошли риск-фильтры. "
            "Отдавай приоритет чистой структуре, адекватному rr, устойчивому режиму и нормальному oi/funding. "
            "Не выдумывай новые данные. Ответь только JSON-массивом символов в порядке приоритета.\n"
            f"{json.dumps(brief, ensure_ascii=False)}"
        )
        txt = self._ask(prompt, max_tokens=180)
        if not txt:
            return items
        try:
            arr = json.loads(txt.replace("```json", "").replace("```", "").strip())
            if not isinstance(arr, list):
                return items
            rank = {str(sym): i for i, sym in enumerate(arr)}
            return sorted(items, key=lambda x: rank.get(x.get("sym"), 10_000))
        except Exception:
            return items

    def review_trade(self, context: dict) -> str:
        if not self.enabled:
            return ""
        prompt = (
            "Сделай очень краткий post-trade review на русском языке. "
            "1 короткое предложение: что подтвердилось или что было слабым в сделке. "
            "Не выдумывай данные. Контекст сделки:\n"
            f"{json.dumps(context, ensure_ascii=False)}"
        )
        return self._ask(prompt, max_tokens=120)
