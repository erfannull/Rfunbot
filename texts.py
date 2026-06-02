from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from ..database import Database
from ..keyboards import (
    admin_cancel,
    admin_channel_actions,
    admin_channels,
    admin_exports_menu,
    admin_health_menu,
    admin_home,
    admin_security_menu,
    admin_settings_menu,
    admin_ticket_settings,
    admin_user_actions,
    admin_user_delete_confirm,
    admin_users_menu,
    broadcast_confirm,
    admin_broadcast_menu,
)
from ..texts import emojify_lines, line
from ..utils.amounts import normalize_digits
from ..utils.jalali import jalali_date_label

router = Router(name="admin")


class AdminState(StatesGroup):
    adding_channel = State()
    broadcasting = State()
    confirming_broadcast = State()
    finding_user_id = State()
    finding_username = State()
    messaging_user = State()
    setting_ticket_group = State()
    setting_value = State()
    adding_admin = State()
    removing_admin = State()


BROADCAST_LABELS = {
    "all": "همه کاربران",
    "active": "کاربران فعال",
    "new": "کاربران جدید",
    "inactive": "کاربران غیرفعال",
    "unblocked": "کاربران مسدود نشده",
    "test": "ارسال تست به ادمین",
}

SETTING_LABELS = {
    "force_join_enabled": "جوین اجباری",
    "ticket_enabled": "تیکت",
    "transactions_enabled": "ثبت تراکنش",
    "user_backup_enabled": "بکاپ‌گیری کاربر",
    "maintenance_mode": "حالت تعمیرات",
    "auto_reports_enabled": "گزارش‌های خودکار",
    "night_reminder_global_enabled": "یادآوری شبانه",
    "anti_spam_min_interval": "محدودیت ضداسپم",
}


def _is_owner(user_id: int, settings) -> bool:
    return bool(settings.admin_ids) and user_id == next(iter(settings.admin_ids))


async def _is_admin(user_id: int, db: Database, settings) -> bool:
    return user_id in settings.admin_ids or await db.admin_role(user_id) is not None


async def _deny(callback: CallbackQuery) -> None:
    await callback.answer("این بخش فقط برای ادمین است.", show_alert=True)


def _msg(key: str, text: str, fallback: str = "•") -> str:
    return emojify_lines(key, text, fallback)


def _bool_text(value: bool) -> str:
    return "روشن" if value else "خاموش"


def _user_label(row) -> str:
    username = f"@{row['username']}" if row["username"] else "-"
    return f"{row['user_id']} | {username} | {row['full_name'] or '-'}"


async def _admin_guard(callback: CallbackQuery, db: Database, settings) -> bool:
    if await _is_admin(callback.from_user.id, db, settings):
        return True
    await _deny(callback)
    return False


async def _log(db: Database, admin_id: int, action: str, target: str | None = None, details: str | None = None) -> None:
    await db.add_admin_log(admin_id, action, target, details)


async def _settings_values(db: Database) -> dict[str, bool]:
    return {
        "force_join_enabled": await db.bool_setting("force_join_enabled", True),
        "ticket_enabled": await db.bool_setting("ticket_enabled", True),
        "transactions_enabled": await db.bool_setting("transactions_enabled", True),
        "user_backup_enabled": await db.bool_setting("user_backup_enabled", True),
        "maintenance_mode": await db.bool_setting("maintenance_mode", False),
        "auto_reports_enabled": await db.bool_setting("auto_reports_enabled", True),
        "night_reminder_global_enabled": await db.bool_setting("night_reminder_global_enabled", True),
    }


async def _user_info_text(db: Database, user_id: int, settings) -> tuple[str, bool] | None:
    row = await db.get_user(user_id)
    if row is None:
        return None
    text = "\n".join([
        line("admin_users", "اطلاعات پایه کاربر", "👤"),
        "",
        line("admin_users", f"نام: {row['full_name'] or '-'}", "👤"),
        line("admin_users", f"Username: @{row['username'] or '-'}", "👤"),
        line("admin_users", f"آیدی عددی: {row['user_id']}", "🆔"),
        line("admin_users", f"تاریخ شروع: {jalali_date_label(__import__('datetime').datetime.fromisoformat(row['started_at']).astimezone(settings.tzinfo))}", "📅"),
        line("admin_users", f"آخرین فعالیت: {jalali_date_label(__import__('datetime').datetime.fromisoformat(row['last_seen_at']).astimezone(settings.tzinfo))}", "⏱"),
        line("admin_users", f"وضعیت: {'مسدود' if row['is_blocked'] else 'آزاد'}", "🔐"),
    ])
    return text, bool(row["is_blocked"])


async def _show_dashboard(message_or_callback, db: Database, settings) -> None:
    stats = await db.admin_dashboard_stats()
    ticket_group = (await db.get_setting("ticket_group_chat_id") or "").strip()
    values = await _settings_values(db)
    warnings = []
    if not ticket_group:
        warnings.append("گروه تیکت تنظیم نشده")
    if stats.get("errors_total", 0):
        warnings.append(f"{stats['errors_total']} خطای ثبت‌شده")
    text = "\n".join([
        line("admin_dashboard", "داشبورد عمومی", "📊"),
        "",
        line("admin_dashboard", f"تعداد کل کاربران: {stats.get('users_total', 0)}", "👥"),
        line("admin_dashboard", f"کاربران جدید امروز: {stats.get('users_new_today', 0)}", "🆕"),
        line("admin_dashboard", f"کاربران جدید این هفته: {stats.get('users_new_week', 0)}", "🆕"),
        line("admin_dashboard", f"کاربران فعال امروز: {stats.get('users_active_today', 0)}", "✅"),
        line("admin_dashboard", f"کاربران فعال این هفته: {stats.get('users_active_week', 0)}", "✅"),
        line("admin_dashboard", f"کاربران مسدود شده: {stats.get('users_blocked', 0)}", "⛔"),
        "",
        line("admin_dashboard", f"تیکت‌های امروز: {stats.get('tickets_today', 0)}", "🎫"),
        line("admin_dashboard", f"تیکت‌های پاسخ داده نشده: {stats.get('tickets_open', 0)}", "🎫"),
        line("admin_dashboard", f"تعداد کل تیکت‌ها: {stats.get('tickets_total', 0)}", "🎫"),
        line("admin_dashboard", f"وضعیت گروه تیکت: {'تنظیم شده' if ticket_group else 'تنظیم نشده'}", "🎫"),
        "",
        line("admin_dashboard", f"کانال‌های جوین اجباری: {stats.get('channels_total', 0)}", "🔒"),
        line("admin_dashboard", f"جوین اجباری: {_bool_text(values['force_join_enabled'])}", "🔒"),
        line("admin_dashboard", f"تیکت: {_bool_text(values['ticket_enabled'])}", "🎫"),
        line("admin_dashboard", f"حالت تعمیرات: {_bool_text(values['maintenance_mode'])}", "🛠"),
        line("admin_dashboard", f"هشدارها: {', '.join(warnings) if warnings else 'موردی نیست'}", "⚠️"),
    ])
    await message_or_callback.edit_text(text, reply_markup=admin_home(), parse_mode="HTML") if isinstance(message_or_callback, Message) is False else await message_or_callback.answer(text, reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:home")
async def admin_panel(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin", "پنل مدیریت LootLog", "⚙️"), reply_markup=admin_home(), parse_mode="HTML")
    await callback.answer()


@router.message(Command("ad"))
async def admin_panel_command(message: Message, db: Database, settings) -> None:
    if str(message.chat.type) != "private":
        return
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        await message.answer(line("admin", "این دستور فقط برای ادمین است.", "⚙️"), parse_mode="HTML")
        return
    await db.add_admin_log(message.from_user.id, "admin_panel_login", str(message.from_user.id), None)
    await message.answer(line("admin", "پنل مدیریت LootLog", "⚙️"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:dashboard")
async def dashboard(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    stats = await db.admin_dashboard_stats()
    ticket_group = (await db.get_setting("ticket_group_chat_id") or "").strip()
    values = await _settings_values(db)
    warnings = []
    if not ticket_group:
        warnings.append("گروه تیکت تنظیم نشده")
    if stats.get("errors_total", 0):
        warnings.append(f"{stats['errors_total']} خطای ثبت‌شده")
    text = "\n".join([
        line("admin_dashboard", "داشبورد عمومی", "📊"),
        "",
        line("admin_dashboard", f"تعداد کل کاربران: {stats.get('users_total', 0)}", "👥"),
        line("admin_dashboard", f"کاربران جدید امروز: {stats.get('users_new_today', 0)}", "🆕"),
        line("admin_dashboard", f"کاربران جدید این هفته: {stats.get('users_new_week', 0)}", "🆕"),
        line("admin_dashboard", f"کاربران فعال امروز: {stats.get('users_active_today', 0)}", "✅"),
        line("admin_dashboard", f"کاربران فعال این هفته: {stats.get('users_active_week', 0)}", "✅"),
        line("admin_dashboard", f"کاربران مسدود شده: {stats.get('users_blocked', 0)}", "⛔"),
        "",
        line("admin_dashboard", f"تیکت‌های امروز: {stats.get('tickets_today', 0)}", "🎫"),
        line("admin_dashboard", f"تیکت‌های پاسخ داده نشده: {stats.get('tickets_open', 0)}", "🎫"),
        line("admin_dashboard", f"تعداد کل تیکت‌ها: {stats.get('tickets_total', 0)}", "🎫"),
        line("admin_dashboard", f"وضعیت گروه تیکت: {'تنظیم شده' if ticket_group else 'تنظیم نشده'}", "🎫"),
        "",
        line("admin_dashboard", f"کانال‌های جوین اجباری: {stats.get('channels_total', 0)}", "🔒"),
        line("admin_dashboard", f"جوین اجباری: {_bool_text(values['force_join_enabled'])}", "🔒"),
        line("admin_dashboard", f"تیکت: {_bool_text(values['ticket_enabled'])}", "🎫"),
        line("admin_dashboard", f"حالت تعمیرات: {_bool_text(values['maintenance_mode'])}", "🛠"),
        line("admin_dashboard", f"هشدارها: {', '.join(warnings) if warnings else 'موردی نیست'}", "⚠️"),
    ])
    await callback.message.edit_text(text, reply_markup=admin_home(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def users_menu(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin_users", "مدیریت کاربران", "👥"), reply_markup=admin_users_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user_search:"))
async def user_search_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    mode = callback.data.rsplit(":", 1)[1]
    await state.set_state(AdminState.finding_user_id if mode == "id" else AdminState.finding_username)
    prompt = "آیدی عددی کاربر را بفرست." if mode == "id" else "یوزرنیم کاربر را بدون @ یا با @ بفرست."
    await callback.message.edit_text(line("admin_users", prompt, "🔎"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


async def _send_user_card(message: Message, db: Database, user_id: int, settings) -> None:
    result = await _user_info_text(db, user_id, settings)
    if result is None:
        await message.answer(line("warning", "کاربر پیدا نشد.", "⚠️"), reply_markup=admin_users_menu(), parse_mode="HTML")
        return
    text, blocked = result
    await message.answer(text, reply_markup=admin_user_actions(user_id, blocked), parse_mode="HTML")


@router.message(AdminState.finding_user_id)
async def find_user_by_id(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    raw = normalize_digits(message.text or "").strip()
    if not raw.isdigit():
        await message.answer(line("warning", "آیدی باید عددی باشد.", "⚠️"), reply_markup=admin_cancel(), parse_mode="HTML")
        return
    await state.clear()
    await _send_user_card(message, db, int(raw), settings)


@router.message(AdminState.finding_username)
async def find_user_by_username(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    row = await db.get_user_by_username(message.text or "")
    await state.clear()
    if row is None:
        await message.answer(line("warning", "کاربر پیدا نشد.", "⚠️"), reply_markup=admin_users_menu(), parse_mode="HTML")
        return
    await _send_user_card(message, db, int(row["user_id"]), settings)


@router.callback_query(F.data.startswith("admin:users:list:"))
async def user_list(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    segment = callback.data.rsplit(":", 1)[1]
    rows = await db.users_by_segment(segment, 15)
    labels = {"new": "کاربران جدید", "active": "کاربران فعال", "blocked": "کاربران مسدود", "inactive": "کاربران غیرفعال"}
    text = line("admin_users", labels.get(segment, "کاربران"), "👥")
    if rows:
        text += "\n\n" + "\n".join(line("admin_users", _user_label(row), "👤") for row in rows)
    else:
        text += "\n\n" + line("admin_users", "موردی پیدا نشد.", "👤")
    await callback.message.edit_text(text, reply_markup=admin_users_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:open:"))
async def open_user(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    result = await _user_info_text(db, user_id, settings)
    if result is None:
        await callback.answer("کاربر پیدا نشد.", show_alert=True)
        return
    text, blocked = result
    await callback.message.edit_text(text, reply_markup=admin_user_actions(user_id, blocked), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:block:"))
async def block_user(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id in settings.admin_ids or await db.admin_role(user_id):
        await callback.answer("ادمین را نمی‌شود مسدود کرد.", show_alert=True)
        return
    await db.set_user_blocked(user_id, True)
    await _log(db, callback.from_user.id, "block_user", str(user_id))
    await callback.message.edit_text(line("admin_block", f"کاربر {user_id} مسدود شد.", "⛔"), reply_markup=admin_users_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:unblock:"))
async def unblock_user(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await db.set_user_blocked(user_id, False)
    await _log(db, callback.from_user.id, "unblock_user", str(user_id))
    await callback.message.edit_text(line("admin_unblock", f"کاربر {user_id} آزاد شد.", "✅"), reply_markup=admin_users_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:message:"))
async def message_user_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminState.messaging_user)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text(line("admin_users", f"پیام خصوصی برای کاربر {user_id} را بفرست.", "✉️"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminState.messaging_user)
async def message_user_send(message: Message, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    data = await state.get_data()
    user_id = int(data["target_user_id"])
    try:
        await bot.copy_message(user_id, message.chat.id, message.message_id)
        await db.add_admin_log(message.from_user.id, "message_user", str(user_id), None)
        await message.answer(line("admin_users", "پیام برای کاربر ارسال شد.", "✅"), reply_markup=admin_users_menu(), parse_mode="HTML")
    except Exception as exc:
        await db.add_system_error("message_user", str(exc))
        await message.answer(line("warning", "ارسال پیام به کاربر ناموفق بود.", "⚠️"), reply_markup=admin_users_menu(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data.startswith("admin:user:delete_prompt:"))
async def delete_user_prompt(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(line("admin_users", f"حذف کامل داده‌های کاربر {user_id} را تایید می‌کنی؟", "🗑"), reply_markup=admin_user_delete_confirm(user_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user:delete:"))
async def delete_user(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id in settings.admin_ids or await db.admin_role(user_id):
        await callback.answer("داده ادمین از اینجا حذف نمی‌شود.", show_alert=True)
        return
    await db.delete_user_data(user_id)
    await _log(db, callback.from_user.id, "delete_user_data", str(user_id))
    await callback.message.edit_text(line("admin_users", "داده‌های کاربر حذف شد.", "✅"), reply_markup=admin_users_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:channels")
async def channels(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    items = await db.forced_channels(active_only=False)
    active_count = await db.active_forced_channel_count()
    text = line("admin_channels", f"جوین اجباری | فعال: {active_count} از {len(items)}", "🔒")
    if items:
        text += "\n\n" + "\n".join(line("admin_channels", f"{row['id']}. {row['title']} | {row['chat_ref']} | {'روشن' if row['is_active'] else 'خاموش'}", "🔒") for row in items)
    else:
        text += "\n\n" + line("admin_channels", "فعلا کانالی ثبت نشده.", "🔒")
    await callback.message.edit_text(text, reply_markup=admin_channels(items), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:channel:"))
async def channel_action_router(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    parts = callback.data.split(":")
    if len(parts) == 3:
        channel_id = int(parts[2])
        row = await db.fetchone("SELECT * FROM forced_channels WHERE id = ?", (channel_id,))
        if row is None:
            await callback.answer("کانال پیدا نشد.", show_alert=True)
            return
        text = "\n".join([
            line("admin_channels", "مدیریت کانال", "🔒"),
            line("admin_channels", f"عنوان: {row['title']}", "🔒"),
            line("admin_channels", f"شناسه: {row['chat_ref']}", "🔒"),
            line("admin_channels", f"وضعیت: {'روشن' if row['is_active'] else 'خاموش'}", "🔒"),
        ])
        await callback.message.edit_text(text, reply_markup=admin_channel_actions(channel_id, bool(row["is_active"])), parse_mode="HTML")
        await callback.answer()
        return
    action = parts[2]
    channel_id = int(parts[3])
    row = await db.fetchone("SELECT * FROM forced_channels WHERE id = ?", (channel_id,))
    if row is None:
        await callback.answer("کانال پیدا نشد.", show_alert=True)
        return
    if action == "toggle":
        await db.toggle_channel_active(channel_id)
        await _log(db, callback.from_user.id, "toggle_channel", str(channel_id))
        await channels(callback, db, settings)
        return
    if action == "test":
        try:
            chat = await bot.get_chat(row["chat_ref"])
            await callback.answer(f"دسترسی برقرار است: {chat.title or row['title']}", show_alert=True)
        except Exception as exc:
            await db.add_system_error("channel_test", str(exc))
            await callback.answer("ربات به کانال دسترسی ندارد یا شناسه اشتباه است.", show_alert=True)
        return
    if action == "move":
        direction = -1 if parts[4] == "up" else 1
        await db.move_forced_channel(channel_id, direction)
        await channels(callback, db, settings)
        return


@router.callback_query(F.data == "admin:add_channel")
async def add_channel_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await state.set_state(AdminState.adding_channel)
    await callback.message.edit_text(
        _msg("join", "اطلاعات کانال را در یک خط بفرست:\n\n@channel_username | عنوان کانال | لینک دعوت اختیاری", "➕"),
        reply_markup=admin_cancel(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminState.adding_channel)
async def add_channel_save(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) < 2:
        await message.answer(line("warning", "فرمت درست نیست. مثال: @channel | عنوان | لینک", "⚠️"), reply_markup=admin_cancel(), parse_mode="HTML")
        return
    await db.add_forced_channel(parts[0], parts[1], parts[2] if len(parts) >= 3 and parts[2] else None)
    await db.add_admin_log(message.from_user.id, "add_forced_channel", parts[0], parts[1])
    await state.clear()
    await message.answer(line("check", "کانال اضافه شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:del_channel:"))
async def delete_channel(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    channel_id = int(callback.data.rsplit(":", 1)[1])
    await db.remove_forced_channel(channel_id)
    await _log(db, callback.from_user.id, "delete_channel", str(channel_id))
    await callback.message.edit_text(line("delete", "کانال حذف شد.", "🗑"), reply_markup=admin_home(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_menu(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin_broadcast", "ارسال همگانی", "📣"), reply_markup=admin_broadcast_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:broadcast:start:"))
async def broadcast_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    audience = callback.data.rsplit(":", 1)[1]
    await state.set_state(AdminState.broadcasting)
    await state.update_data(audience=audience)
    await callback.message.edit_text(
        _msg("admin_broadcast", f"پیام کمپین برای «{BROADCAST_LABELS.get(audience, audience)}» را بفرست.\n\nربات همان پیام را کپی می‌کند.", "📣"),
        reply_markup=admin_cancel(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminState.broadcasting)
async def broadcast_preview(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    data = await state.get_data()
    audience = data.get("audience", "unblocked")
    users = [message.from_user.id] if audience == "test" else await db.user_ids_for_audience(audience)
    await state.set_state(AdminState.confirming_broadcast)
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id, audience=audience, users=users)
    await message.answer(
        _msg("admin_broadcast_confirm", f"پیش‌نمایش دریافت شد.\n\nمخاطب: {BROADCAST_LABELS.get(audience, audience)}\nتعداد هدف: {len(users)}\nاگر مطمئنی، تایید کن.", "✅"),
        reply_markup=broadcast_confirm(),
        parse_mode="HTML",
    )


@router.callback_query(AdminState.confirming_broadcast, F.data == "admin:broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await state.clear()
    await callback.message.edit_text(line("cancel", "ارسال همگانی لغو شد.", "✖️"), reply_markup=admin_home(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(AdminState.confirming_broadcast, F.data == "admin:broadcast_confirm")
async def broadcast_confirmed(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    data = await state.get_data()
    users = [int(user_id) for user_id in data.get("users", [])]
    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])
    campaign_id = await db.create_broadcast_campaign(callback.from_user.id, data.get("audience", "unblocked"), len(users))
    sent = failed = blocked = 0
    await callback.message.edit_text(line("admin_broadcast", f"ارسال شروع شد. تعداد هدف: {len(users)}", "📣"), parse_mode="HTML")
    for user_id in users:
        try:
            await bot.copy_message(user_id, source_chat_id, source_message_id)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception as exc:
            failed += 1
            await db.add_system_error("broadcast", str(exc))
        await asyncio.sleep(0.04)
    await db.finish_broadcast_campaign(campaign_id, sent, failed, blocked)
    await db.add_admin_log(callback.from_user.id, "broadcast", data.get("audience", "unblocked"), f"sent={sent}, failed={failed}, blocked={blocked}")
    await state.clear()
    await callback.message.edit_text(
        _msg("admin_broadcast", f"گزارش ارسال\n\nموفق: {sent}\nناموفق: {failed}\nبلاک کرده: {blocked}", "📣"),
        reply_markup=admin_home(),
        parse_mode="HTML",
    )
    await callback.answer("ارسال تمام شد.")


@router.callback_query(F.data == "admin:broadcast:history")
async def broadcast_history(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.broadcast_campaigns(10)
    text = line("admin_broadcast", "تاریخچه کمپین‌ها", "📣")
    if rows:
        text += "\n\n" + "\n".join(line("admin_broadcast", f"#{row['id']} | {row['audience']} | {row['status']} | موفق {row['sent']} / خطا {row['failed']} / بلاک {row['blocked']}", "📣") for row in rows)
    else:
        text += "\n\n" + line("admin_broadcast", "کمپینی ثبت نشده.", "📣")
    await callback.message.edit_text(text, reply_markup=admin_broadcast_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:exports")
async def exports_menu(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin_exports", "گزارش‌ها و خروجی‌های امن", "📤"), reply_markup=admin_exports_menu(_is_owner(callback.from_user.id, settings)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:backup")
async def export_db_backup(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    if not _is_owner(callback.from_user.id, settings):
        await callback.answer("بکاپ دیتابیس فقط برای ادمین اصلی است.", show_alert=True)
        return
    await db.checkpoint()
    db_path = Path(settings.db_path)
    if not db_path.exists():
        await callback.message.edit_text(line("warning", "فایل دیتابیس پیدا نشد.", "⚠️"), reply_markup=admin_exports_menu(True), parse_mode="HTML")
        return
    await callback.message.answer_document(FSInputFile(db_path, filename="lootlog-backup.sqlite3"), caption=line("admin_backup", "بکاپ دیتابیس LootLog", "💾"), parse_mode="HTML")
    await db.add_admin_log(callback.from_user.id, "database_backup", None, None)
    await callback.answer("بکاپ آماده شد.")


async def _send_csv(callback: CallbackQuery, rows: list, headers: list[str], filename: str, caption_key: str, caption: str) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    file = BufferedInputFile(buffer.getvalue().encode("utf-8-sig"), filename=filename)
    await callback.message.answer_document(file, caption=line(caption_key, caption, "📤"), parse_mode="HTML")


@router.callback_query(F.data == "admin:export_users")
async def export_users(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.all_users()
    await _send_csv(callback, [[r["user_id"], r["username"] or "", r["full_name"] or "", r["language_code"] or "", r["is_blocked"], r["started_at"], r["last_seen_at"]] for r in rows], ["user_id", "username", "full_name", "language_code", "is_blocked", "started_at", "last_seen_at"], "lootlog-users.csv", "admin_export_users", "خروجی کاربران LootLog")
    await db.add_admin_log(callback.from_user.id, "export_users", None, None)
    await callback.answer("خروجی کاربران آماده شد.")


@router.callback_query(F.data == "admin:export_tickets")
async def export_tickets(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.recent_tickets(1000)
    await _send_csv(callback, [[r["id"], r["user_id"], r["username"] or "", r["status"], r["group_chat_id"], r["group_message_id"], r["created_at"], r["replied_at"] or "", r["closed_at"] or ""] for r in rows], ["id", "user_id", "username", "status", "group_chat_id", "group_message_id", "created_at", "replied_at", "closed_at"], "lootlog-tickets.csv", "admin_export_tickets", "خروجی تیکت‌ها")
    await callback.answer("خروجی تیکت‌ها آماده شد.")


@router.callback_query(F.data == "admin:export_channels")
async def export_channels(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.forced_channels(active_only=False)
    await _send_csv(callback, [[r["id"], r["chat_ref"], r["title"], r["invite_link"] or "", r["is_active"], r["sort_order"], r["created_at"]] for r in rows], ["id", "chat_ref", "title", "invite_link", "is_active", "sort_order", "created_at"], "lootlog-forced-channels.csv", "admin_export_channels", "خروجی کانال‌های جوین اجباری")
    await callback.answer("خروجی کانال‌ها آماده شد.")


@router.callback_query(F.data == "admin:export_admin_logs")
async def export_admin_logs(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.admin_logs(1000)
    await _send_csv(callback, [[r["id"], r["admin_id"], r["action"], r["target"] or "", r["details"] or "", r["created_at"]] for r in rows], ["id", "admin_id", "action", "target", "details", "created_at"], "lootlog-admin-logs.csv", "admin_export_logs", "خروجی لاگ‌های ادمین")
    await callback.answer("خروجی لاگ‌ها آماده شد.")


@router.callback_query(F.data == "admin:export_errors")
async def export_errors(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.system_errors(1000)
    await _send_csv(callback, [[r["id"], r["source"], r["message"], r["created_at"]] for r in rows], ["id", "source", "message", "created_at"], "lootlog-system-errors.csv", "admin_export_errors", "خروجی خطاهای سیستم")
    await callback.answer("خروجی خطاها آماده شد.")


@router.callback_query(F.data == "admin:settings")
async def settings_menu(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin_settings", "تنظیمات ربات", "⚙️"), reply_markup=admin_settings_menu(await _settings_values(db)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:setting:toggle:"))
async def toggle_setting(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    key = callback.data.rsplit(":", 1)[1]
    new_value = await db.toggle_setting(key, True)
    await db.add_admin_log(callback.from_user.id, "toggle_setting", key, str(new_value))
    await callback.answer(f"{SETTING_LABELS.get(key, key)} {'روشن' if new_value else 'خاموش'} شد.")
    await settings_menu(callback, db, settings)


@router.callback_query(F.data.startswith("admin:setting:set:"))
async def set_setting_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    key = callback.data.rsplit(":", 1)[1]
    await state.set_state(AdminState.setting_value)
    await state.update_data(setting_key=key)
    await callback.message.edit_text(line("admin_settings", f"مقدار جدید «{SETTING_LABELS.get(key, key)}» را بفرست.", "⚙️"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminState.setting_value)
async def set_setting_save(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    data = await state.get_data()
    key = data["setting_key"]
    await db.set_setting(key, (message.text or "").strip())
    await db.add_admin_log(message.from_user.id, "set_setting", key, (message.text or "").strip())
    await state.clear()
    await message.answer(line("admin_settings", "تنظیم ذخیره شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:settings:schedules")
async def schedule_settings(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    text = "\n".join([
        line("admin_settings", "تنظیم ساعت‌ها", "⏰"),
        line("admin_settings", f"گزارش هفتگی: {settings.weekly_report_day} {settings.weekly_report_hour:02d}:{settings.weekly_report_minute:02d}", "📊"),
        line("admin_settings", f"گزارش ماهانه: روز {settings.monthly_report_day} ساعت {settings.monthly_report_hour:02d}:{settings.monthly_report_minute:02d}", "📈"),
        line("admin_settings", "یادآوری شبانه: 22:00", "⏰"),
        "",
        line("admin_settings", "این ساعت‌ها فعلا از .env خوانده می‌شوند تا زمان‌بندی بدون ری‌استارت خراب نشود.", "⚙️"),
    ])
    await callback.message.edit_text(text, reply_markup=admin_settings_menu(await _settings_values(db)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:tickets")
async def ticket_settings(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    group_id = (await db.get_setting("ticket_group_chat_id") or "").strip()
    stats = await db.ticket_stats()
    text = "\n".join([
        line("admin_tickets", "تنظیمات تیکت", "🎫"),
        line("admin_tickets", f"گروه فعلی: {group_id or 'تنظیم نشده'}", "🎫"),
        line("admin_tickets", f"کل تیکت‌ها: {stats['tickets_total']}", "🎫"),
        line("admin_tickets", f"باز/بی‌پاسخ: {stats['tickets_open']}", "🎫"),
    ])
    await callback.message.edit_text(text, reply_markup=admin_ticket_settings(bool(group_id)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:tickets:set_group")
async def ticket_group_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await state.set_state(AdminState.setting_ticket_group)
    await callback.message.edit_text(_msg("admin_tickets", "آیدی عددی گروه دریافت تیکت را بفرست.\n\nیا ربات را داخل گروه ادمین کن و همانجا /ticket_group را بزن.", "🎫"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminState.setting_ticket_group)
async def ticket_group_save(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    raw = normalize_digits(message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer(line("warning", "آیدی گروه باید عددی باشد. مثال: -1001234567890", "⚠️"), reply_markup=admin_cancel(), parse_mode="HTML")
        return
    await db.set_setting("ticket_group_chat_id", raw)
    await db.add_admin_log(message.from_user.id, "set_ticket_group", raw, None)
    await state.clear()
    await message.answer(line("admin_tickets", f"گروه تیکت روی {raw} تنظیم شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:tickets:clear_group")
async def ticket_group_clear(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await db.set_setting("ticket_group_chat_id", "")
    await db.add_admin_log(callback.from_user.id, "clear_ticket_group", None, None)
    await callback.message.edit_text(line("admin_tickets", "گروه تیکت حذف شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")
    await callback.answer()


@router.message(Command("ticket_group"))
async def set_ticket_group_from_group(message: Message, db: Database, settings) -> None:
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    if str(message.chat.type) not in {"group", "supergroup", "ChatType.GROUP", "ChatType.SUPERGROUP"}:
        await message.answer(line("admin_tickets", "این دستور را داخل گروه دریافت تیکت بزن.", "🎫"), parse_mode="HTML")
        return
    await db.set_setting("ticket_group_chat_id", str(message.chat.id))
    await db.add_admin_log(message.from_user.id, "set_ticket_group", str(message.chat.id), "from_group")
    await message.answer(line("admin_tickets", f"این گروه برای دریافت تیکت تنظیم شد: {message.chat.id}", "✅"), parse_mode="HTML")


@router.message(F.reply_to_message)
async def reply_to_ticket(message: Message, bot: Bot, db: Database, settings) -> None:
    group_id = (await db.get_setting("ticket_group_chat_id") or "").strip()
    if not group_id or str(message.chat.id) != group_id:
        return
    if not message.from_user or not await _is_admin(message.from_user.id, db, settings):
        return
    user_id = await db.ticket_user_for_message(message.chat.id, message.reply_to_message.message_id)
    if user_id is None:
        return
    await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
    await db.mark_ticket_answered(message.chat.id, message.reply_to_message.message_id)
    await db.add_admin_log(message.from_user.id, "reply_ticket", str(user_id), None)
    await message.reply(line("admin_tickets", "پاسخ برای کاربر ارسال شد.", "✅"), parse_mode="HTML")


@router.callback_query(F.data == "admin:security")
async def security_menu(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    await callback.message.edit_text(line("admin_security", "امنیت و لاگ‌ها", "🔐"), reply_markup=admin_security_menu(_is_owner(callback.from_user.id, settings)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:logs")
async def show_logs(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.admin_logs(15)
    text = line("admin_logs", "آخرین لاگ‌های ادمین", "🧾")
    if rows:
        text += "\n\n" + "\n".join(line("admin_logs", f"#{r['id']} | {r['admin_id']} | {r['action']} | {r['target'] or '-'}", "🧾") for r in rows)
    else:
        text += "\n\n" + line("admin_logs", "لاگی ثبت نشده.", "🧾")
    await callback.message.edit_text(text, reply_markup=admin_security_menu(_is_owner(callback.from_user.id, settings)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:errors")
async def show_errors(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    rows = await db.system_errors(15)
    text = line("admin_errors", "خطاهای مهم", "⚠️")
    if rows:
        text += "\n\n" + "\n".join(line("admin_errors", f"#{r['id']} | {r['source']} | {r['message'][:80]}", "⚠️") for r in rows)
    else:
        text += "\n\n" + line("admin_errors", "خطایی ثبت نشده.", "✅")
    await callback.message.edit_text(text, reply_markup=admin_security_menu(_is_owner(callback.from_user.id, settings)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:admins")
async def list_admins(callback: CallbackQuery, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    dynamic = await db.admin_accounts()
    text = line("admin_admins", "لیست ادمین‌ها", "👮")
    text += "\n\n" + line("admin_admins", f"ادمین‌های اصلی .env: {', '.join(str(i) for i in settings.admin_ids) or '-'}", "👮")
    if dynamic:
        text += "\n" + "\n".join(line("admin_admins", f"{r['user_id']} | {r['role']}", "👮") for r in dynamic)
    await callback.message.edit_text(text, reply_markup=admin_security_menu(_is_owner(callback.from_user.id, settings)), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:admins:add")
async def add_admin_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings) or not _is_owner(callback.from_user.id, settings):
        await callback.answer("فقط ادمین اصلی می‌تواند ادمین اضافه کند.", show_alert=True)
        return
    await state.set_state(AdminState.adding_admin)
    await callback.message.edit_text(line("admin_admins", "آیدی عددی ادمین جدید را بفرست.", "👮"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminState.adding_admin)
async def add_admin_save(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not _is_owner(message.from_user.id, settings):
        return
    raw = normalize_digits(message.text or "").strip()
    if not raw.isdigit():
        await message.answer(line("warning", "آیدی باید عددی باشد.", "⚠️"), reply_markup=admin_cancel(), parse_mode="HTML")
        return
    await db.add_admin_account(int(raw), "support", message.from_user.id)
    await db.add_admin_log(message.from_user.id, "add_admin", raw, "support")
    await state.clear()
    await message.answer(line("admin_admins", "ادمین اضافه شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:admins:remove")
async def remove_admin_start(callback: CallbackQuery, state: FSMContext, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings) or not _is_owner(callback.from_user.id, settings):
        await callback.answer("فقط ادمین اصلی می‌تواند ادمین حذف کند.", show_alert=True)
        return
    await state.set_state(AdminState.removing_admin)
    await callback.message.edit_text(line("admin_admins", "آیدی عددی ادمین را برای حذف بفرست.", "👮"), reply_markup=admin_cancel(), parse_mode="HTML")
    await callback.answer()


@router.message(AdminState.removing_admin)
async def remove_admin_save(message: Message, state: FSMContext, db: Database, settings) -> None:
    if not message.from_user or not _is_owner(message.from_user.id, settings):
        return
    raw = normalize_digits(message.text or "").strip()
    if not raw.isdigit():
        await message.answer(line("warning", "آیدی باید عددی باشد.", "⚠️"), reply_markup=admin_cancel(), parse_mode="HTML")
        return
    await db.remove_admin_account(int(raw))
    await db.add_admin_log(message.from_user.id, "remove_admin", raw, None)
    await state.clear()
    await message.answer(line("admin_admins", "ادمین حذف شد.", "✅"), reply_markup=admin_home(), parse_mode="HTML")


@router.callback_query(F.data == "admin:health")
async def health(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    values = await _settings_values(db)
    ticket_group = (await db.get_setting("ticket_group_chat_id") or "").strip()
    channels = await db.forced_channels(active_only=False)
    text = "\n".join([
        line("admin_health", "سلامت سیستم", "🩺"),
        line("admin_health", "وضعیت دیتابیس: متصل", "✅"),
        line("admin_health", f"کاربران فعال این هفته: {(await db.admin_dashboard_stats()).get('users_active_week', 0)}", "👥"),
        line("admin_health", "وضعیت scheduler: فعال در runtime", "⏱"),
        line("admin_health", f"گزارش هفتگی: {_bool_text(values['auto_reports_enabled'])}", "📊"),
        line("admin_health", f"گزارش ماهانه: {_bool_text(values['auto_reports_enabled'])}", "📈"),
        line("admin_health", f"یادآوری شبانه: {_bool_text(values['night_reminder_global_enabled'])}", "⏰"),
        line("admin_health", f"گروه تیکت: {'تنظیم شده' if ticket_group else 'تنظیم نشده'}", "🎫"),
        line("admin_health", f"کانال‌های جوین اجباری: {len(channels)}", "🔒"),
    ])
    await callback.message.edit_text(text, reply_markup=admin_health_menu(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:health:test_admin")
async def health_test_admin(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    try:
        await bot.send_message(callback.from_user.id, line("admin_health", "تست ارسال پیام به ادمین موفق بود.", "✅"), parse_mode="HTML")
        await callback.answer("ارسال موفق بود.", show_alert=True)
    except Exception as exc:
        await db.add_system_error("health_test_admin", str(exc))
        await callback.answer("ارسال ناموفق بود.", show_alert=True)


@router.callback_query(F.data == "admin:health:test_ticket")
async def health_test_ticket(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    group_id = (await db.get_setting("ticket_group_chat_id") or "").strip()
    if not group_id:
        await callback.answer("گروه تیکت تنظیم نشده.", show_alert=True)
        return
    try:
        await bot.send_message(int(group_id), line("admin_health", "تست ارسال پیام به گروه تیکت موفق بود.", "✅"), parse_mode="HTML")
        await callback.answer("ارسال موفق بود.", show_alert=True)
    except Exception as exc:
        await db.add_system_error("health_test_ticket", str(exc))
        await callback.answer("ارسال به گروه تیکت ناموفق بود.", show_alert=True)


@router.callback_query(F.data == "admin:health:test_channels")
async def health_test_channels(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _admin_guard(callback, db, settings):
        return
    channels = await db.forced_channels(active_only=False)
    ok = bad = 0
    for row in channels:
        try:
            await bot.get_chat(row["chat_ref"])
            ok += 1
        except Exception as exc:
            bad += 1
            await db.add_system_error("channel_test", f"{row['chat_ref']}: {exc}")
    await callback.answer(f"کانال سالم: {ok} | مشکل‌دار: {bad}", show_alert=True)
