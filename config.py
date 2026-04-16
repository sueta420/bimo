import os
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip()
    return v if v else default


def env_csv(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name, "")
    if raw is None:
        return list(default or [])
    items = [x.strip().upper() for x in str(raw).split(",")]
    return [x for x in items if x] or list(default or [])


def build_config() -> dict[str, Any]:
    load_dotenv_file(os.path.join(BASE_DIR, ".env"))
    return {
        "testnet": env_bool("TESTNET", True),
        "api_key": os.getenv("BYBIT_API_KEY", ""),
        "api_secret": os.getenv("BYBIT_API_SECRET", ""),
        "openai_key": os.getenv("OPENAI_API_KEY", ""),
        "llm_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "enable_llm": env_bool("ENABLE_LLM", True),
        "use_llm_secondary_rank": env_bool("USE_LLM_SECONDARY_RANK", False),
        "llm_rank_top_n": env_int("LLM_RANK_TOP_N", 3),
        "llm_rank_min_score": env_int("LLM_RANK_MIN_SCORE", 72),
        "llm_rank_max_score_spread": env_int("LLM_RANK_MAX_SCORE_SPREAD", 6),
        "position_sizing_mode": env_str("POSITION_SIZING_MODE", "risk_pct").lower(),
        "risk_per_trade_pct": env_float("RISK_PER_TRADE_PCT", 0.5),
        "risk_per_trade_usd": env_float("RISK_PER_TRADE_USD", 0.0),
        "target_notional_usd": env_float("TARGET_NOTIONAL_USD", 5.0),
        "target_margin_usd": env_float("TARGET_MARGIN_USD", 0.0),
        "target_leverage": env_int("TARGET_LEVERAGE", 1),
        "max_risk_per_trade_usd": env_float("MAX_RISK_PER_TRADE_USD", 0.0),
        "min_rr_ratio": env_float("MIN_RR_RATIO", 3.0),
        "min_edge_cost_ratio": env_float("MIN_EDGE_COST_RATIO", 2.0),
        "min_net_reward_pct": env_float("MIN_NET_REWARD_PCT", 0.25),
        "max_side_risk_pct": env_float("MAX_SIDE_RISK_PCT", 1.5),
        "slippage_entry_bps": env_float("SLIPPAGE_ENTRY_BPS", 10.0),
        "slippage_exit_bps": env_float("SLIPPAGE_EXIT_BPS", 15.0),
        "taker_fee_bps": env_float("TAKER_FEE_BPS", 5.5),
        "funding_reserve_rate": env_float("FUNDING_RESERVE_RATE", 0.0005),
        "max_trades_per_day": env_int("MAX_TRADES_PER_DAY", 5),
        "max_leverage": env_int("MAX_LEVERAGE", 10),
        "stop_after_losses": env_int("STOP_AFTER_LOSSES", 3),
        "min_volume_24h": env_float("MIN_VOLUME_24H", 50_000_000),
        "max_scan_symbols": env_int("MAX_SCAN_SYMBOLS", 20),
        "symbol_blacklist": env_csv("SYMBOL_BLACKLIST", []),
        "max_funding_abs": env_float("MAX_FUNDING_ABS", 0.001),
        "min_atr_ratio": env_float("MIN_ATR_RATIO", 0.005),
        "fakeout_edge_max_frac": env_float("FAKEOUT_EDGE_MAX_FRAC", 0.12),
        "fakeout_max_atr_ratio": env_float("FAKEOUT_MAX_ATR_RATIO", 0.012),
        "fakeout_max_oi_change_pct": env_float("FAKEOUT_MAX_OI_CHANGE_PCT", 2.0),
        "fakeout_min_vol_ratio": env_float("FAKEOUT_MIN_VOL_RATIO", 90.0),
        "signal_tf": os.getenv("SIGNAL_TF", "15"),
        "entry_tf": os.getenv("ENTRY_TF", "15"),
        "regime_tf_1": os.getenv("REGIME_TF_1", "60"),
        "regime_tf_2": os.getenv("REGIME_TF_2", "240"),
        "min_rule_score": env_int("MIN_RULE_SCORE", 60),
        "funding_block_minutes": env_int("FUNDING_BLOCK_MINUTES", 10),
        "oi_spike_block_pct": env_float("OI_SPIKE_BLOCK_PCT", 8.0),
        "range_mid_avoid_pct": env_float("RANGE_MID_AVOID_PCT", 0.25),
        "symbol_cooldown_min": env_int("SYMBOL_COOLDOWN_MIN", 120),
        "be_trigger_r": env_float("BE_TRIGGER_R", 1.0),
        "trailing_trigger_r": env_float("TRAILING_TRIGGER_R", 1.5),
        "trailing_lock_r": env_float("TRAILING_LOCK_R", 0.8),
        "correlation_threshold": env_float("CORRELATION_THRESHOLD", 0.85),
        "max_correlated_positions_per_side": env_int("MAX_CORRELATED_POSITIONS_PER_SIDE", 2),
        "correlation_lookback": env_int("CORRELATION_LOOKBACK", 96),
        "session_timezone": env_str("SESSION_TIMEZONE", "UTC"),
        "enable_eod_close": env_bool("ENABLE_EOD_CLOSE", True),
        "close_hour_utc": env_int("CLOSE_HOUR_UTC", 23),
        "close_min_utc": env_int("CLOSE_MIN_UTC", 45),
        "cycle_sec": env_int("CYCLE_SEC", 900),
        "close_verify_retries": env_int("CLOSE_VERIFY_RETRIES", 6),
        "close_verify_delay_ms": env_int("CLOSE_VERIFY_DELAY_MS", 500),
        "critical_error_stop_count": env_int("CRITICAL_ERROR_STOP_COUNT", 3),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "tg_min_interval_sec": env_int("TG_MIN_INTERVAL_SEC", 3),
        "tg_error_cooldown_sec": env_int("TG_ERROR_COOLDOWN_SEC", 180),
        "tg_timeout_sec": env_int("TG_TIMEOUT_SEC", 10),
        "state_db_path": os.getenv("STATE_DB_PATH", os.path.join(BASE_DIR, "agent_state.sqlite3")),
        "log_json": env_bool("LOG_JSON", True),
    }


CONFIG = build_config()
