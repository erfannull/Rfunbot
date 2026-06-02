from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from .database import Database
from .keyboards import force_join


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: frozenset[int], min_interval: float = 0.65) -> None:
        self.admin_ids = admin_ids
        self.min_interval = min_interval
        self.last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.id in self.admin_ids:
            return await handler(event, data)

        now = time.monotonic()
        previous = self.last_seen.get(user.id, 0.0)
        if now - previous < self.min_interval:
            if isinstance(event, CallbackQuery):
                await event.answer("یه لحظه آروم‌تر؛ درخواست قبلی هنوز در حال پردازشه.", show_alert=False)
            return None

        self.last_seen[user.id] = now
        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            await self.db.upsert_user(user)
            record = await self.db.get_user(user.id)
            if record and int(record["is_blocked"]):
                if isinstance(event, Message):
                    await event.answer("دسترسی شما به ربات محدود شده است.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("دسترسی شما محدود شده است.", show_alert=True)
                return None
        return await handler(event, data)


async def user_missing_channels(bot: Bot, db: Database, user_id: int) -> list[dict]:
    if not await db.bool_setting("force_join_enabled", True):
        return []
    missing = []
    for row in await db.forced_channels(active_only=True):
        try:
            member = await bot.get_chat_member(row["chat_ref"], user_id)
        except Exception:
            missing.append(dict(row))
            continue
        if member.status in {"left", "kicked"}:
            missing.append(dict(row))
    return missing


async def ensure_joined(message: Message, bot: Bot, db: Database) -> bool:
    if not message.from_user:
        return False
    missing = await user_missing_channels(bot, db, message.from_user.id)
    if not missing:
        return True
    text = await db.get_setting("force_join_text") or "اول عضو کانال‌های زیر شو."
    await message.answer(text, reply_markup=force_join(missing))
    return False
