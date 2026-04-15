import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import CONFIG
from execution import Agent


def resolve_session_tz(name: str):
    try:
        return ZoneInfo(str(name or "UTC"))
    except Exception:
        return timezone.utc


class JsonFormatter(logging.Formatter):
    def __init__(self, session_tz):
        super().__init__()
        self.session_tz = session_tz

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(self.session_tz).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "symbol"):
            payload["symbol"] = getattr(record, "symbol")
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def init_logging(cfg):
    session_tz = resolve_session_tz(cfg.get("session_timezone", "UTC"))
    date_str = datetime.now(session_tz).strftime("%Y-%m-%d")
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"agent_{date_str}.log")
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.INFO)

    stream = logging.StreamHandler()
    fileh = logging.FileHandler(log_path, encoding="utf-8")
    if cfg.get("log_json", True):
        fmt = JsonFormatter(session_tz)
    else:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream.setFormatter(fmt)
    fileh.setFormatter(fmt)
    root.addHandler(stream)
    root.addHandler(fileh)
    return logging.getLogger("agent")


def main():
    log = init_logging(CONFIG)
    missing = [k for k in ("api_key", "api_secret") if not CONFIG.get(k)]
    if missing:
        mapping = {"api_key": "BYBIT_API_KEY", "api_secret": "BYBIT_API_SECRET"}
        print("\n❌ Missing required env vars:")
        for k in missing:
            print(f"   {mapping[k]}")
        sys.exit(1)

    if CONFIG.get("enable_llm", True) and not CONFIG.get("openai_key"):
        log.warning("OPENAI_API_KEY missing: LLM explain/rank will be disabled")
        CONFIG["enable_llm"] = False

    mode = "TESTNET" if CONFIG["testnet"] else "REAL"
    print(f"\n🤖 Agent starting [{mode}] | Ctrl+C to stop\n")
    asyncio.run(Agent(CONFIG, log).run())


if __name__ == "__main__":
    main()
