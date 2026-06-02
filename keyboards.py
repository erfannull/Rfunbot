from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import load_settings
from .database import Database
from .middlewares import RateLimitMiddleware, UserMiddleware
from .routers import admin, user
from .scheduler import setup_scheduler


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    db = Database(settings.db_path)
    await db.connect()
    await db.migrate()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage(), db=db, settings=settings)
    dp.message.middleware(RateLimitMiddleware(settings.admin_ids))
    dp.callback_query.middleware(RateLimitMiddleware(settings.admin_ids))
    dp.message.middleware(UserMiddleware(db))
    dp.callback_query.middleware(UserMiddleware(db))
    dp.include_router(admin.router)
    dp.include_router(user.router)

    scheduler = setup_scheduler(bot, db, settings)
    scheduler.start()

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="شروع و منوی اصلی"),
                BotCommand(command="help", description="راهنمای ثبت هزینه و درآمد"),
                BotCommand(command="today", description="گزارش امروز"),
                BotCommand(command="week", description="گزارش هفته"),
                BotCommand(command="month", description="گزارش ماه"),
                BotCommand(command="backup", description="بکاپ‌گیری تراکنش‌ها"),
                BotCommand(command="reminders", description="یادآوری و بودجه"),
                BotCommand(command="delete_my_data", description="حذف کامل اطلاعات من"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())
