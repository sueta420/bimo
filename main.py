import asyncio
import json
import logging
import os
import socket
import sys
import traceback
from datetime import datetime, timezone

from config import CONFIG
from execution import Agent
from notifier import TelegramNotifier


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
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
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"agent_{date_str}.log")
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.INFO)

    stream = logging.StreamHandler()
    fileh = logging.FileHandler(log_path, encoding="utf-8")
    if cfg.get("log_json", True):
        fmt = JsonFormatter()
    else:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream.setFormatter(fmt)
    fileh.setFormatter(fmt)
    root.addHandler(stream)
    root.addHandler(fileh)
    return logging.getLogger("agent")


def notify_fatal_crash(cfg, log: logging.Logger, stage: str, err: BaseException):
    try:
        tg = TelegramNotifier(cfg, log)
        if not tg.enabled:
            return
        host = socket.gethostname()
        pid = os.getpid()
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        if len(tb) > 2400:
            tb = tb[:2400] + "\n... traceback truncated ..."
        text = (
            "Агент аварийно остановлен\n"
            f"Stage: {stage}\n"
            f"Host: {host}\n"
            f"PID: {pid}\n"
            f"Error: {type(err).__name__}: {err}\n"
            f"Traceback:\n{tb}"
        )
        tg.send(text, force=True)
    except Exception as notify_ex:
        log.error(f"fatal_notify_failed err={notify_ex}")


def main():
    log = init_logging(CONFIG)
    try:
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
    except KeyboardInterrupt:
        log.info("agent_stopped_by_keyboard_interrupt")
    except BaseException as ex:
        log.exception("agent_fatal_crash")
        notify_fatal_crash(CONFIG, log, "main", ex)
        raise


if __name__ == "__main__":
    main()
