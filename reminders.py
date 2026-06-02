from __future__ import annotations

import re
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile, Message

from .texts import line

if TYPE_CHECKING:
    from aiogram import Bot


TELEGRAM_SAFE_TEXT_LIMIT = 3500
TAG_RE = re.compile(r"<[^>]+>")


def report_filename(period: str) -> str:
    labels = {"today": "today", "week": "week", "month": "month"}
    return f"lootlog-report-{labels.get(period, period)}.txt"


def plain_report_text(text: str) -> str:
    return TAG_RE.sub("", text)


async def send_report_message(message: Message, text: str, period: str) -> None:
    if len(text) <= TELEGRAM_SAFE_TEXT_LIMIT:
        await message.answer(text, parse_mode="HTML")
        return
    file = BufferedInputFile(plain_report_text(text).encode("utf-8-sig"), filename=report_filename(period))
    await message.answer_document(
        file,
        caption=line("report_file", "گزارش طولانی بود؛ فایل کامل گزارش را فرستادم.", "📄"),
        parse_mode="HTML",
    )


async def send_report_to_user(bot: Bot, user_id: int, text: str, period: str) -> None:
    if len(text) <= TELEGRAM_SAFE_TEXT_LIMIT:
        await bot.send_message(user_id, text, parse_mode="HTML")
        return
    file = BufferedInputFile(plain_report_text(text).encode("utf-8-sig"), filename=report_filename(period))
    await bot.send_document(
        user_id,
        file,
        caption=line("report_file", "گزارش طولانی بود؛ فایل کامل گزارش را فرستادم.", "📄"),
        parse_mode="HTML",
    )
