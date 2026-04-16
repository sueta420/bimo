import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_PATH = "/Users/bot/trading/.env"
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "agent_state.sqlite3")


def load_env_file(path: str) -> None:
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
                    key = key[len("export ") :].strip()
                if not key:
                    continue
                if value and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def send_telegram(text: str) -> bool:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if not token or not chat_id or not text.strip():
        return False
    payload = urlencode({"chat_id": chat_id, "text": text[:4096]}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = Request(
        url=url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=10) as response:
            response.read()
        return True
    except Exception as ex:
        print(f"watchdog_telegram_failed err={ex}")
        return False


def load_runtime_value(conn: sqlite3.Connection, key: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT payload_json FROM session_state WHERE key=?", (key,))
    row = cur.fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def save_runtime_value(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO session_state (key, payload_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
        """,
        (key, json.dumps(payload, ensure_ascii=False), now),
    )
    conn.commit()


def main() -> int:
    load_env_file(DEFAULT_ENV_PATH)
    db_path = os.getenv("STATE_DB_PATH", DEFAULT_DB_PATH)
    grace_sec = env_int("WATCHDOG_HEARTBEAT_GRACE_SEC", 0)
    cycle_sec = env_int("CYCLE_SEC", 900)
    restart_cooldown_sec = env_int("WATCHDOG_RESTART_COOLDOWN_SEC", 1800)
    launchd_label = str(os.getenv("WATCHDOG_LAUNCHD_LABEL", "com.trading.agent")).strip() or "com.trading.agent"
    if grace_sec <= 0:
        grace_sec = max(cycle_sec * 2, 1800)

    if not os.path.exists(db_path):
        print(f"watchdog_skip reason=state_db_missing path={db_path}")
        return 0

    conn = sqlite3.connect(db_path)
    try:
        heartbeat = load_runtime_value(conn, "runtime_heartbeat") or {}
        last_ts_raw = str(heartbeat.get("ts") or "").strip()
        if not last_ts_raw:
            print("watchdog_skip reason=no_heartbeat")
            return 0
        try:
            last_ts = datetime.fromisoformat(last_ts_raw)
        except Exception:
            print(f"watchdog_skip reason=bad_heartbeat ts={last_ts_raw}")
            return 0

        age_sec = max((datetime.now(timezone.utc) - last_ts).total_seconds(), 0.0)
        if age_sec < grace_sec:
            print(f"watchdog_ok heartbeat_age_sec={age_sec:.1f} grace_sec={grace_sec}")
            return 0

        marker = load_runtime_value(conn, "watchdog_last_restart") or {}
        last_restart_ts_raw = str(marker.get("ts") or "").strip()
        if last_restart_ts_raw:
            try:
                last_restart_ts = datetime.fromisoformat(last_restart_ts_raw)
                since_restart = max((datetime.now(timezone.utc) - last_restart_ts).total_seconds(), 0.0)
                if since_restart < restart_cooldown_sec:
                    print(
                        f"watchdog_skip reason=restart_cooldown heartbeat_age_sec={age_sec:.1f} "
                        f"since_restart_sec={since_restart:.1f}"
                    )
                    return 0
            except Exception:
                pass

        message = (
            f"[WATCHDOG] Heartbeat stale: {age_sec/60.0:.1f} мин без обновления. "
            f"Перезапускаю {launchd_label}. Последний heartbeat: {last_ts_raw}"
        )
        print(
            f"watchdog_restart heartbeat_age_sec={age_sec:.1f} grace_sec={grace_sec} "
            f"label={launchd_label} last_heartbeat={last_ts_raw}"
        )
        send_telegram(message)
        save_runtime_value(
            conn,
            "watchdog_last_restart",
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "heartbeat_ts": last_ts_raw,
                "heartbeat_age_sec": round(age_sec, 1),
            },
        )

        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{launchd_label}"],
            check=False,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
