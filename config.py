from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .database import Database
from .delivery import send_report_to_user
from .reports import build_report
from .reminders import send_budget_alerts, send_nightly_reminders
from .utils.jalali import local_jalali


async def send_periodic_reports(bot: Bot, db: Database, settings, period: str) -> None:
    for user_id in await db.active_user_ids():
        try:
            text = await build_report(db, user_id, period, settings.tzinfo)
            await send_report_to_user(bot, user_id, text, period)
        except Exception:
            pass
        await asyncio.sleep(0.04)


async def send_monthly_reports_if_jalali_day(bot: Bot, db: Database, settings) -> None:
    _, _, jalali_day = local_jalali(datetime.now(settings.tzinfo))
    if jalali_day != settings.monthly_report_day:
        return
    await send_periodic_reports(bot, db, settings, "month")


def setup_scheduler(bot: Bot, db: Database, settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        send_periodic_reports,
        "cron",
        day_of_week=settings.weekly_report_day,
        hour=settings.weekly_report_hour,
        minute=settings.weekly_report_minute,
        args=[bot, db, settings, "week"],
        id="weekly_reports",
        replace_existing=True,
    )
    scheduler.add_job(
        send_monthly_reports_if_jalali_day,
        "cron",
        hour=settings.monthly_report_hour,
        minute=settings.monthly_report_minute,
        args=[bot, db, settings],
        id="monthly_reports",
        replace_existing=True,
    )
    scheduler.add_job(
        send_nightly_reminders,
        "cron",
        hour=22,
        minute=0,
        args=[bot, db, settings],
        id="nightly_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        send_budget_alerts,
        "cron",
        hour=21,
        minute=30,
        args=[bot, db, settings],
        id="budget_alerts",
        replace_existing=True,
    )
    return scheduler
