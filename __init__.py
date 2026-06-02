from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot

from .database import Database
from .texts import line
from .utils.amounts import format_rial
from .utils.jalali import jalali_month_bounds, jalali_month_key


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


async def check_budget_alert(bot: Bot, db: Database, settings, user_id: int) -> None:
    prefs = await db.get_preferences(user_id)
    budget = int(prefs["monthly_budget"])
    if budget <= 0:
        return
    now = datetime.now(settings.tzinfo)
    month_key = jalali_month_key(now)
    if prefs["budget_alert_month"] == month_key:
        return
    start, end = jalali_month_bounds(now)
    spent = await db.expense_sum_between(user_id, _to_utc_iso(start), _to_utc_iso(end))
    if spent < int(budget * 0.8):
        return
    percent = int((spent / budget) * 100)
    text = "\n".join([
        line("warning", "هشدار بودجه ماهانه", "⚠️"),
        "",
        line("expense", f"مصرف فعلی: {format_rial(spent)}", "💸"),
        line("money", f"بودجه ماهانه: {format_rial(budget)}", "💰"),
        line("chart", f"درصد مصرف: {percent}٪", "📊"),
    ])
    await bot.send_message(user_id, text, parse_mode="HTML")
    await db.mark_budget_alert_sent(user_id, month_key)


async def send_nightly_reminders(bot: Bot, db: Database, settings) -> None:
    for user_id in await db.users_with_night_reminder():
        try:
            await bot.send_message(
                user_id,
                line("warning", "یادآوری شبانه: خرج‌های امروزت رو اگر جا مونده، ثبت کن.", "⏰"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await asyncio.sleep(0.04)


async def send_budget_alerts(bot: Bot, db: Database, settings) -> None:
    for user_id in await db.active_user_ids():
        try:
            await check_budget_alert(bot, db, settings, user_id)
        except Exception:
            pass
        await asyncio.sleep(0.04)
