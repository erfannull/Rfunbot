from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


WEEKDAY_ALIASES = {
    "0": "mon",
    "1": "tue",
    "2": "wed",
    "3": "thu",
    "4": "fri",
    "5": "sat",
    "6": "sun",
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
    "دوشنبه": "mon",
    "سه‌شنبه": "tue",
    "سه شنبه": "tue",
    "چهارشنبه": "wed",
    "پنجشنبه": "thu",
    "جمعه": "fri",
    "شنبه": "sat",
    "یکشنبه": "sun",
}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    timezone: str
    weekly_report_day: str
    weekly_report_hour: int
    weekly_report_minute: int
    monthly_report_day: int
    monthly_report_hour: int
    monthly_report_minute: int
    use_experimental_button_styles: bool

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _admin_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return frozenset(ids)


def _weekday_env(name: str, default: str) -> str:
    raw = os.getenv(name, default).strip().lower()
    normalized = WEEKDAY_ALIASES.get(raw)
    if normalized is None:
        allowed = ", ".join(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        raise RuntimeError(f"{name} must be one of: {allowed}. Got: {raw}")
    return normalized


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required. Copy .env.example to .env and set your token.")

    return Settings(
        bot_token=token,
        admin_ids=_admin_ids(os.getenv("ADMIN_IDS")),
        db_path=os.getenv("DB_PATH", "lootlog.sqlite3"),
        timezone=os.getenv("TIMEZONE", "Asia/Tehran"),
        weekly_report_day=_weekday_env("WEEKLY_REPORT_DAY", "fri"),
        weekly_report_hour=_int_env("WEEKLY_REPORT_HOUR", 21),
        weekly_report_minute=_int_env("WEEKLY_REPORT_MINUTE", 0),
        monthly_report_day=_int_env("MONTHLY_REPORT_DAY", 1),
        monthly_report_hour=_int_env("MONTHLY_REPORT_HOUR", 9),
        monthly_report_minute=_int_env("MONTHLY_REPORT_MINUTE", 0),
        use_experimental_button_styles=_bool_env("USE_EXPERIMENTAL_BUTTON_STYLES", True),
    )
