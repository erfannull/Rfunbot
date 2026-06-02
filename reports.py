from __future__ import annotations

import csv
import html
import io
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..database import Database
from ..delivery import send_report_message
from ..keyboards import (
    BTN_ADD,
    BTN_BACKUP,
    BTN_DELETE_DATA,
    BTN_HELP,
    BTN_MONTH,
    BTN_MANAGE_TX,
    BTN_PROFILE,
    BTN_REMINDERS,
    BTN_TICKET,
    BTN_TODAY,
    BTN_WEEK,
    add_step_actions,
    backup_menu,
    bulk_type,
    cancel_only,
    categories,
    profile_categories_menu,
    profile_menu,
    confirm_delete_data,
    reminders_menu,
    force_join,
    reply_main_menu,
    transaction_actions,
    transaction_category,
    transaction_delete_confirm,
    transaction_date_picker,
    transaction_list,
    ticket_cancel,
    transaction_type,
    user_categories_menu,
    user_category_actions,
)
from ..middlewares import ensure_joined, user_missing_channels
from ..reports import build_report, format_recent
from ..reminders import check_budget_alert
from ..texts import EXPENSE_CATEGORIES, emojify_lines, indexed_line, line
from ..utils.amounts import format_rial, parse_amount_message
from ..utils.jalali import jalali_date_label, parse_jalali_or_gregorian_date

router = Router(name="user")
router.message.filter(F.chat.type == "private")


class TxState(StatesGroup):
    waiting_amount = State()
    choosing_kind = State()
    choosing_category = State()
    choosing_date = State()
    choosing_bulk_kind = State()
    waiting_bulk_items = State()
    editing_amount = State()
    editing_kind = State()
    editing_category = State()
    editing_date = State()
    setting_budget = State()
    submitting_ticket = State()
    adding_category = State()
    renaming_category = State()


async def _callback_join_guard(callback: CallbackQuery, bot: Bot, db: Database) -> bool:
    if callback.data != "join:check" and await db.bool_setting("maintenance_mode", False):
        await callback.answer("ربات موقتا در حالت تعمیرات است.", show_alert=True)
        return False
    if callback.data == "join:check":
        return True
    missing = await user_missing_channels(bot, db, callback.from_user.id)
    if not missing:
        return True
    text = await db.get_setting("force_join_text") or "اول عضو کانال‌های زیر شو."
    await callback.message.answer(_msg("join_check", text, "🔒"), reply_markup=force_join(missing), parse_mode="HTML")
    await callback.answer("اول عضویت کانال‌ها را کامل کن.", show_alert=True)
    return False


def _is_admin(message: Message, settings) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


def _msg(key: str, text: str, fallback: str = "•") -> str:
    return emojify_lines(key, text, fallback)


async def _clear_panel_buttons(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _replace_panel(message: Message | None, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        await _clear_panel_buttons(message)
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def _remember_flow_panel(state: FSMContext, message: Message | None) -> None:
    if message is None:
        return
    await state.update_data(_flow_panel_chat_id=message.chat.id, _flow_panel_message_id=message.message_id)


async def _answer_flow_panel(message: Message, state: FSMContext, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
    sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    await _remember_flow_panel(state, sent)


async def _replace_flow_panel(callback: CallbackQuery, state: FSMContext, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
    await _replace_panel(callback.message, text, reply_markup=reply_markup, parse_mode=parse_mode)
    await _remember_flow_panel(state, callback.message)


async def _clear_flow_panel(bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("_flow_panel_chat_id")
    message_id = data.get("_flow_panel_message_id")
    if not chat_id or not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=int(chat_id), message_id=int(message_id), reply_markup=None)
    except Exception:
        pass
    await state.update_data(_flow_panel_chat_id=None, _flow_panel_message_id=None)


def _parse_bulk_lines(text: str) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    invalid: list[str] = []
    for raw_line in (text or "").splitlines():
        line_text = raw_line.strip()
        if not line_text:
            continue
        parsed = parse_amount_message(line_text)
        if parsed is None:
            invalid.append(line_text)
            continue
        items.append({"title": parsed.title, "amount": parsed.amount})
    return items, invalid

def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _tx_summary(row, tz) -> str:
    kind = "هزینه" if row["kind"] == "expense" else "درآمد"
    category = row["category"] or "بدون دسته‌بندی"
    local_dt = datetime.fromisoformat(row["created_at"]).astimezone(tz)
    return "\n".join([
        line("tx_manage", "مدیریت تراکنش", "🧾"),
        "",
        line("tx_manage", f"نوع: {kind}", "🧾"),
        line("tx_manage", f"عنوان: {row['title']}", "🧾"),
        line("tx_manage", f"مبلغ: {format_rial(int(row['amount']))}", "🧾"),
        line("tx_manage", f"دسته‌بندی: {category if row['kind'] == 'expense' else '-'}", "🧾"),
        line("tx_manage", f"تاریخ شمسی: {jalali_date_label(local_dt)}", "📅"),
    ])


async def _ask_transaction_date(target_message: Message, state: FSMContext, *, replace: bool = False) -> None:
    await state.set_state(TxState.choosing_date)
    text = _msg(
        "tx_date_edit",
        "تاریخ تراکنش رو انتخاب کن. اگر برای امروز است، دکمه امروز رو بزن؛ اگر برای روز دیگری است، تاریخ شمسی را بنویس. مثال:\n\n۱۴۰۳/۰۳/۱۰",
        "📅",
    )
    if replace:
        await _replace_panel(target_message, text, reply_markup=transaction_date_picker(), parse_mode="HTML")
        await _remember_flow_panel(state, target_message)
        return
    await _answer_flow_panel(target_message, state, text, reply_markup=transaction_date_picker(), parse_mode="HTML")


async def _save_pending_transaction(
    target_message: Message,
    state: FSMContext,
    db: Database,
    settings,
    user_id: int,
    created_at: datetime,
    bot: Bot | None = None,
) -> None:
    data = await state.get_data()
    created_iso = _to_utc_iso(created_at)
    date_label = jalali_date_label(created_at)

    if "bulk_items" in data:
        kind = data["bulk_kind"]
        items = data.get("bulk_items", [])
        total = 0
        for item in items:
            total += int(item["amount"])
            await db.add_transaction(
                user_id=user_id,
                kind=kind,
                title=item["title"],
                amount=int(item["amount"]),
                category=None,
                created_at=created_iso,
            )
        await state.clear()
        title = "لیست درآمد ثبت شد" if kind == "income" else "لیست هزینه ثبت شد"
        total_label = "جمع درآمد" if kind == "income" else "جمع هزینه"
        await target_message.answer(
            "\n".join([
                indexed_line("tx_bulk_saved", title, 0, "✅"),
                "",
                indexed_line("tx_bulk_saved", f"تعداد: {len(items)}", 1, "✅"),
                indexed_line("tx_bulk_saved", f"{total_label}: {format_rial(total)}", 2, "✅"),
                indexed_line("tx_bulk_saved", f"تاریخ: {date_label}", 3, "✅"),
            ]),
            reply_markup=reply_main_menu(user_id in settings.admin_ids),
            parse_mode="HTML",
        )
        if bot and kind == "expense":
            await check_budget_alert(bot, db, settings, user_id)
        return

    kind = data["kind"]
    category_value = data.get("category_value")
    category_label = data.get("category_label", "-")
    tx_id = await db.add_transaction(
        user_id=user_id,
        kind=kind,
        title=data["title"],
        amount=int(data["amount"]),
        category=category_value,
        created_at=created_iso,
    )
    await state.clear()
    label = "هزینه" if kind == "expense" else "درآمد"
    lines = [
        indexed_line("tx_saved", "ثبت شد", 0, "✅"),
        "",
        indexed_line("tx_saved", f"نوع: {label}", 1, "✅"),
        indexed_line("tx_saved", f"عنوان: {data['title']}", 2, "✅"),
        indexed_line("tx_saved", f"مبلغ: {format_rial(int(data['amount']))}", 3, "✅"),
    ]
    if kind == "expense":
        lines.append(indexed_line("tx_saved", f"دسته‌بندی: {category_label}", 4, "✅"))
        lines.append(indexed_line("tx_saved", f"تاریخ: {date_label}", 5, "✅"))
        lines.append(indexed_line("tx_saved", f"کد ثبت: {tx_id}", 6, "✅"))
    else:
        lines.append(indexed_line("tx_saved", f"تاریخ: {date_label}", 4, "✅"))
        lines.append(indexed_line("tx_saved", f"کد ثبت: {tx_id}", 5, "✅"))
    await target_message.answer(
        "\n".join(lines),
        reply_markup=reply_main_menu(user_id in settings.admin_ids),
        parse_mode="HTML",
    )
    if bot and kind == "expense":
        await check_budget_alert(bot, db, settings, user_id)


async def _send_menu(message: Message, settings, text: str | None = None) -> None:
    await message.answer(
        text or line("menu", "منوی اصلی LootLog", "📌"),
        reply_markup=reply_main_menu(_is_admin(message, settings)),
        parse_mode="HTML",
    )


async def _start_add_flow(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if await db.bool_setting("maintenance_mode", False):
        await message.answer(line("warning", "ربات موقتا در حالت تعمیرات است.", "🛠"), parse_mode="HTML")
        return
    if not await db.bool_setting("transactions_enabled", True):
        await message.answer(line("warning", "ثبت تراکنش فعلا غیرفعال است.", "⚠️"), parse_mode="HTML")
        return
    if not await ensure_joined(message, bot, db):
        return
    await state.set_state(TxState.waiting_amount)
    await _answer_flow_panel(
        message,
        state,
        _msg(
            "tx_step_amount",
            "مرحله ۱\n\n"
            "مبلغ و عنوان رو بفرست. مثال:\n\n"
            "قهوه ۸۰ هزار",
            "➕",
        ),
        reply_markup=add_step_actions(),
        parse_mode="HTML",
    )


async def _send_week_report(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await send_report_message(message, await build_report(db, message.from_user.id, "week", settings.tzinfo), "week")


async def _send_today_report(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await send_report_message(message, await build_report(db, message.from_user.id, "today", settings.tzinfo), "today")


async def _send_month_report(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await send_report_message(message, await build_report(db, message.from_user.id, "month", settings.tzinfo), "month")


async def _send_recent(message: Message, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    rows = await db.recent_transactions(message.from_user.id, 10)
    if not rows:
        await message.answer(format_recent(rows), parse_mode="HTML")
        return
    await message.answer(
        line("tx_manage", "یکی از تراکنش‌ها را انتخاب کن.", "🧾"),
        reply_markup=transaction_list(rows),
        parse_mode="HTML",
    )



BACKUP_PERIODS = {
    "1d": (timedelta(days=1), "۱ روز گذشته", "lootlog-last-1-day.csv"),
    "1w": (timedelta(days=7), "۱ هفته گذشته", "lootlog-last-1-week.csv"),
    "1m": (timedelta(days=31), "۱ ماه گذشته", "lootlog-last-1-month.csv"),
    "3m": (timedelta(days=92), "۳ ماه گذشته", "lootlog-last-3-months.csv"),
}


async def _send_backup_menu(message: Message, bot: Bot, db: Database) -> None:
    if not await db.bool_setting("user_backup_enabled", True):
        await message.answer(line("backup", "بکاپ‌گیری کاربر فعلا غیرفعال است.", "📦"), parse_mode="HTML")
        return
    if not await ensure_joined(message, bot, db):
        return
    await message.answer(
        line("backup", "بازه بکاپ‌گیری را انتخاب کن. فایل خروجی به صورت CSV ارسال می‌شود.", "📦"),
        reply_markup=backup_menu(),
        parse_mode="HTML",
    )


async def _export_backup(message: Message, user_id: int, db: Database, settings, period_key: str) -> bool:
    period = BACKUP_PERIODS.get(period_key)
    if period is None:
        return False
    delta, label, filename = period
    end = datetime.now(settings.tzinfo).replace(microsecond=0)
    start = (end - delta).replace(microsecond=0)
    rows = await db.user_transactions_since(user_id, _to_utc_iso(start), _to_utc_iso(end))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "kind", "title", "amount_toman", "category", "created_at", "jalali_date"])
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        writer.writerow([row["id"], row["kind"], row["title"], row["amount"], row["category"] or "", row["created_at"], jalali_date_label(created)])
    file = BufferedInputFile(buffer.getvalue().encode("utf-8-sig"), filename=filename)
    caption_key = "backup_ready" if rows else "backup_empty"
    caption_text = f"بکاپ {label} آماده شد." if rows else f"در بازه {label} تراکنشی نبود؛ فایل خالی آماده شد."
    await message.answer_document(file, caption=line(caption_key, caption_text, "📦"), parse_mode="HTML")
    return True


async def _send_reminders_menu(message: Message, bot: Bot, db: Database, *, replace: bool = False) -> None:
    if not await ensure_joined(message, bot, db):
        return
    prefs = await db.get_preferences(message.from_user.id)
    text = "\n".join([
        line("reminders", "تنظیمات یادآوری", "⏰"),
        "",
        line("budget", f"بودجه ماهانه: {format_rial(int(prefs['monthly_budget'])) if int(prefs['monthly_budget']) else 'تنظیم نشده'}", "💰"),
        line("reminders", f"یادآوری شبانه: {'روشن' if int(prefs['night_reminder_enabled']) else 'خاموش'}", "⏰"),
    ])
    if replace:
        await _replace_panel(message, text, reply_markup=reminders_menu(prefs), parse_mode="HTML")
        return
    await message.answer(text, reply_markup=reminders_menu(prefs), parse_mode="HTML")


async def _send_profile(message: Message, bot: Bot, db: Database, settings, *, replace: bool = False) -> None:
    if not await ensure_joined(message, bot, db):
        return
    summary = await db.profile_summary(message.from_user.id)
    prefs = await db.get_preferences(message.from_user.id)

    def date_value(value: str | None) -> str:
        if not value:
            return "-"
        local = datetime.fromisoformat(value).astimezone(settings.tzinfo)
        return f"{jalali_date_label(local)} - {local:%H:%M}"

    name = html.escape(str(summary.get("full_name") or summary.get("username") or "-"))
    username = html.escape(f"@{summary['username']}" if summary.get("username") else "-")
    text = "\n".join([
        line("profile", "پروفایل LootLog", "👤"),
        "",
        line("profile", "خلاصه حساب", "📌"),
        line("profile", f"نام کاربر: {name}", "👤"),
        line("profile", f"نام کاربری: {username}", "👤"),
        line("profile", f"آیدی عددی تلگرام: {summary['user_id']}", "🆔"),
        line("profile", f"تاریخ شروع استفاده: {date_value(summary.get('started_at'))}", "📅"),
        line("profile", f"تعداد کل تراکنش‌ها: {summary['tx_total']}", "🧾"),
        line("profile", f"مجموع هزینه‌ها: {format_rial(int(summary['expense_total']))}", "💸"),
        line("profile", f"مجموع درآمدها: {format_rial(int(summary['income_total']))}", "💰"),
        line("profile", f"مانده کل: {format_rial(int(summary['balance']))}", "📊"),
        line("profile", f"آخرین زمان فعالیت: {date_value(summary.get('last_seen_at'))}", "⏱"),
        "",
        line("profile_settings", "تنظیمات فعال", "⚙️"),
        line("profile_settings", f"بودجه ماهانه: {format_rial(int(prefs['monthly_budget'])) if int(prefs['monthly_budget']) else 'تنظیم نشده'}", "💰"),
        line("profile_settings", f"یادآوری شبانه: {'روشن' if int(prefs['night_reminder_enabled']) else 'خاموش'}", "⏰"),
    ])
    if replace:
        await _replace_panel(message, text, reply_markup=profile_menu(), parse_mode="HTML")
        return
    await message.answer(text, reply_markup=profile_menu(), parse_mode="HTML")


async def _send_profile_categories(message: Message, *, replace: bool = False) -> None:
    text = line("profile_categories", "کدام دسته‌بندی را می‌خواهی مدیریت کنی؟", "🏷")
    if replace:
        await _replace_panel(message, text, reply_markup=profile_categories_menu(), parse_mode="HTML")
        return
    await message.answer(text, reply_markup=profile_categories_menu(), parse_mode="HTML")


async def _send_user_category_list(message: Message, db: Database, user_id: int, kind: str, *, replace: bool = False) -> None:
    custom = await db.user_categories(user_id, kind)
    if kind == "expense":
        defaults = "، ".join(EXPENSE_CATEGORIES)
        header = f"دسته‌بندی‌های هزینه\n\nپیش‌فرض‌ها: {defaults}\n\nدسته‌بندی‌های شخصی:"
    else:
        header = "دسته‌بندی‌های درآمد\n\nدرآمدها فعلا در ثبت تراکنش بدون دسته‌بندی ذخیره می‌شوند، اما دسته‌بندی‌های شخصی درآمد را می‌توانی برای نسخه‌های بعدی آماده کنی."
    if custom:
        body = "\n".join(f"{index}. {row['name']}" for index, row in enumerate(custom, start=1))
    else:
        body = "هنوز دسته‌بندی شخصی نداری."
    text = _msg("profile_categories", f"{header}\n\n{body}", "🏷")
    if replace:
        await _replace_panel(message, text, reply_markup=user_categories_menu(kind, custom), parse_mode="HTML")
        return
    await message.answer(text, reply_markup=user_categories_menu(kind, custom), parse_mode="HTML")


async def _start_ticket(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await db.bool_setting("ticket_enabled", True):
        await message.answer(line("ticket", "سیستم تیکت فعلا غیرفعال است.", "🎫"), parse_mode="HTML")
        return
    if not await ensure_joined(message, bot, db):
        return
    ticket_group = (await db.get_setting("ticket_group_chat_id") or "").strip()
    if not ticket_group:
        await message.answer(line("ticket", "فعلا گروه دریافت تیکت تنظیم نشده. بعدا دوباره امتحان کن.", "🎫"), parse_mode="HTML")
        return
    await state.set_state(TxState.submitting_ticket)
    await _answer_flow_panel(
        message,
        state,
        _msg("ticket", "پیامت را برای ادمین بفرست. متن پیام از طرف ربات داخل گروه تیکت ارسال می‌شود و پاسخ ادمین همینجا به تو می‌رسد.", "🎫"),
        reply_markup=ticket_cancel(),
        parse_mode="HTML",
    )


async def _send_help(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await message.answer(
        _msg(
            "help",
            "راهنمای LootLog\n\n"
            "برای ثبت هزینه یا درآمد، از کیبورد پایین صفحه روی «ثبت جدید» بزن.\n"
            "بعد ربات مرحله‌به‌مرحله مبلغ، نوع و دسته‌بندی رو می‌پرسه.\n\n"
            "نمونه مبلغ‌ها:\n"
            "قهوه ۸۰ هزار\n"
            "تاکسی 80 هزارتومن\n"
            "حقوق ۵۰ میلیون",
            "❔",
        ),
        reply_markup=reply_main_menu(_is_admin(message, settings)),
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    text = await db.get_setting("start_text")
    await message.answer(
        _msg("start", text or "به LootLog خوش اومدی.", "👋"),
        reply_markup=reply_main_menu(_is_admin(message, settings)),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "join:check")
async def check_join(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    missing = await user_missing_channels(bot, db, callback.from_user.id)
    if missing:
        await callback.answer("هنوز عضویت همه کانال‌ها تایید نشده.", show_alert=True)
        return
    await callback.answer("عضویت تایید شد.")
    text = await db.get_setting("start_text")
    await callback.message.answer(
        _msg("start_join_confirmed", f"عضویت تایید شد.\n\n{text or 'به LootLog خوش اومدی.'}", "✅"),
        reply_markup=reply_main_menu(callback.from_user.id in settings.admin_ids),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_ADD)
async def add_from_keyboard(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    await _start_add_flow(message, state, bot, db)


@router.message(F.text == BTN_TODAY)
async def today_from_keyboard(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_today_report(message, bot, db, settings)


@router.message(F.text == BTN_WEEK)
async def week_from_keyboard(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_week_report(message, bot, db, settings)


@router.message(F.text == BTN_MONTH)
async def month_from_keyboard(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_month_report(message, bot, db, settings)


@router.message(F.text == BTN_MANAGE_TX)
async def manage_transactions_from_keyboard(message: Message, bot: Bot, db: Database) -> None:
    await _send_recent(message, bot, db)


@router.message(F.text == BTN_BACKUP)
async def backup_from_keyboard(message: Message, bot: Bot, db: Database) -> None:
    await _send_backup_menu(message, bot, db)


@router.message(F.text == BTN_PROFILE)
async def profile_from_keyboard(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_profile(message, bot, db, settings)


@router.message(F.text == BTN_TICKET)
async def ticket_from_keyboard(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    await _start_ticket(message, state, bot, db)


@router.message(F.text == BTN_REMINDERS)
async def reminders_from_keyboard(message: Message, bot: Bot, db: Database) -> None:
    await _send_reminders_menu(message, bot, db)



@router.message(F.text == BTN_HELP)
async def help_from_keyboard(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_help(message, bot, db, settings)


@router.message(F.text == BTN_DELETE_DATA)
async def delete_data_from_keyboard(message: Message) -> None:
    await message.answer(
        line("delete_confirm", "حذف کامل اطلاعات را تایید می‌کنی؟", "🗑"),
        reply_markup=confirm_delete_data(),
        parse_mode="HTML",
    )


@router.message(TxState.submitting_ticket, F.text & ~F.text.startswith("/"))
async def capture_ticket(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    ticket_group_raw = (await db.get_setting("ticket_group_chat_id") or "").strip()
    if not ticket_group_raw:
        await state.clear()
        await message.answer(line("ticket", "گروه تیکت تنظیم نشده و پیام ارسال نشد.", "🎫"), parse_mode="HTML")
        return
    text = (message.text or "").strip()
    if not text:
        await _answer_flow_panel(message, state, line("ticket", "فعلا تیکت فقط به صورت متن دریافت می‌شود. لطفا متن پیام را بفرست.", "🎫"), reply_markup=ticket_cancel(), parse_mode="HTML")
        return
    user = await db.get_user(message.from_user.id)
    username = f"@{user['username']}" if user and user["username"] else "-"
    full_name = user["full_name"] if user and user["full_name"] else "-"
    group_text = "\n".join([
        line("ticket", "تیکت جدید", "🎫"),
        "",
        line("ticket", f"کاربر: {html.escape(str(full_name))}", "👤"),
        line("ticket", f"Username: {html.escape(username)}", "👤"),
        line("ticket", f"User ID: {message.from_user.id}", "🆔"),
        "",
        line("ticket", f"پیام: {html.escape(text)}", "💬"),
        "",
        line("ticket", "برای پاسخ، روی همین پیام ریپلای کن.", "↩️"),
    ])
    try:
        sent = await bot.send_message(int(ticket_group_raw), group_text, parse_mode="HTML")
    except Exception:
        await message.answer(line("ticket", "ارسال تیکت به گروه انجام نشد. تنظیمات گروه را در پنل ادمین بررسی کن.", "⚠️"), parse_mode="HTML")
        return
    await db.add_ticket_message(message.from_user.id, int(ticket_group_raw), sent.message_id)
    await state.clear()
    await message.answer(line("ticket", "تیکتت برای ادمین ارسال شد. پاسخ همینجا برات میاد.", "✅"), reply_markup=reply_main_menu(False), parse_mode="HTML")


@router.message(TxState.adding_category, F.text & ~F.text.startswith("/"))
async def capture_new_category(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    data = await state.get_data()
    kind = data.get("category_kind", "expense")
    name = (message.text or "").strip()
    if not name or len(name) > 32:
        await _answer_flow_panel(message, state, line("profile_categories", "نام دسته‌بندی باید بین ۱ تا ۳۲ کاراکتر باشد.", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    if any(char in name for char in "<>&"):
        await _answer_flow_panel(message, state, line("profile_categories", "برای نام دسته‌بندی از علامت‌های < و > و & استفاده نکن.", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    await db.add_user_category(message.from_user.id, kind, name)
    await state.clear()
    await message.answer(line("profile_categories", f"دسته‌بندی «{name}» اضافه شد.", "✅"), parse_mode="HTML")
    await _send_user_category_list(message, db, message.from_user.id, kind)


@router.message(TxState.renaming_category, F.text & ~F.text.startswith("/"))
async def capture_rename_category(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    data = await state.get_data()
    category_id = int(data["category_id"])
    kind = data.get("category_kind", "expense")
    name = (message.text or "").strip()
    if not name or len(name) > 32:
        await _answer_flow_panel(message, state, line("profile_categories", "نام جدید باید بین ۱ تا ۳۲ کاراکتر باشد.", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    if any(char in name for char in "<>&"):
        await _answer_flow_panel(message, state, line("profile_categories", "برای نام دسته‌بندی از علامت‌های < و > و & استفاده نکن.", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    await db.rename_user_category(category_id, message.from_user.id, name)
    await state.clear()
    await message.answer(line("profile_categories", f"نام دسته‌بندی به «{name}» تغییر کرد.", "✅"), parse_mode="HTML")
    await _send_user_category_list(message, db, message.from_user.id, kind)


@router.message(TxState.waiting_amount, F.text & ~F.text.startswith("/"))
async def capture_transaction(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    parsed = parse_amount_message(message.text or "")
    if parsed is None:
        await _answer_flow_panel(message, state, line("tx_parse_error", "مبلغ رو متوجه نشدم. مثلا بنویس: قهوه ۸۰ هزار", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    await state.set_state(TxState.choosing_kind)
    await state.update_data(title=parsed.title, amount=parsed.amount)
    await _answer_flow_panel(
        message,
        state,
        _msg(
            "tx_step_type",
            f"مرحله ۲ از ۳\n\nعنوان: {parsed.title}\nمبلغ: {format_rial(parsed.amount)}\n\nاین مورد هزینه است یا درآمد؟",
            "💰",
        ),
        reply_markup=transaction_type(),
        parse_mode="HTML",
    )


@router.message(TxState.editing_amount, F.text & ~F.text.startswith("/"))
async def capture_edit_amount(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    parsed = parse_amount_message(message.text or "")
    if parsed is None:
        await _answer_flow_panel(message, state, line("tx_parse_error", "مبلغ رو متوجه نشدم. مثلا بنویس: قهوه ۹۰ هزار", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    await state.set_state(TxState.editing_kind)
    await state.update_data(title=parsed.title, amount=parsed.amount)
    await _answer_flow_panel(
        message,
        state,
        _msg("tx_edit", f"نوع جدید را انتخاب کن:\n\nعنوان: {parsed.title}\nمبلغ: {format_rial(parsed.amount)}", "✏️"),
        reply_markup=transaction_type(),
        parse_mode="HTML",
    )


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def text_outside_flow(message: Message, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await message.answer(
        line("tx_outside_flow", "برای ثبت هزینه یا درآمد، اول از کیبورد پایین صفحه روی «ثبت جدید» بزن.", "➕"),
        reply_markup=reply_main_menu(_is_admin(message, settings)),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:add")
async def add_hint(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await state.set_state(TxState.waiting_amount)
    await _replace_flow_panel(
        callback,
        state,
        _msg("tx_step_amount", "مرحله ۱\n\nمبلغ و عنوان رو بفرست. مثال:\n\nقهوه ۸۰ هزار", "➕"),
        reply_markup=add_step_actions(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def home(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await _replace_panel(callback.message, line("menu", "منوی اصلی LootLog", "📌"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def help_text(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await _replace_panel(
        callback.message,
        line("help", "از کیبورد پایین صفحه استفاده کن. دکمه‌های شیشه‌ای برای انتخاب‌های مرحله‌ای، تاییدها و عملیات حساس نمایش داده می‌شوند.", "❔"),
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "bulk:start")
async def start_bulk_from_step(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await state.set_state(TxState.choosing_bulk_kind)
    await _replace_flow_panel(
        callback,
        state,
        _msg("tx_bulk_type", "افزودن لیستی\n\nنوع لیست رو انتخاب کن. هزینه و درآمد نباید داخل یک لیست قاطی شوند.", "🧾"),
        reply_markup=bulk_type(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(TxState.choosing_bulk_kind, F.data.startswith("bulkkind:"))
async def choose_bulk_kind(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    kind = callback.data.split(":", 1)[1]
    await state.set_state(TxState.waiting_bulk_items)
    await state.update_data(bulk_kind=kind)
    label = "هزینه" if kind == "expense" else "درآمد"
    await _replace_flow_panel(
        callback,
        state,
        _msg(
            "tx_bulk_items",
            f"لیست {label}ها رو بفرست. هر مورد باید در یک خط جدا باشد. مثال:\n\nقهوه ۸۰ هزار\nتاکسی ۲۰ هزار",
            "🧾",
        ),
        reply_markup=cancel_only(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TxState.waiting_bulk_items, F.text & ~F.text.startswith("/"))
async def capture_bulk_items(message: Message, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await ensure_joined(message, bot, db):
        return
    await _clear_flow_panel(bot, state)
    items, invalid = _parse_bulk_lines(message.text or "")
    if invalid or not items:
        details = "\n".join(invalid[:5]) if invalid else "هیچ خط قابل ثبت پیدا نشد."
        await _answer_flow_panel(
            message,
            state,
            _msg("tx_bulk_parse_error", f"چند خط رو متوجه نشدم:\n{details}\n\nهر مورد را جداگانه مثل «قهوه ۸۰ هزار» در یک خط بفرست.", "⚠️"),
            reply_markup=cancel_only(),
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    kind = data["bulk_kind"]
    await state.update_data(bulk_items=items)
    await _ask_transaction_date(message, state)


@router.callback_query(TxState.choosing_kind, F.data.startswith("txkind:"))
async def choose_kind(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    kind = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if kind == "income":
        await state.update_data(kind="income", category_value=None, category_label="-")
        await _ask_transaction_date(callback.message, state, replace=True)
        await callback.answer()
        return
    await state.set_state(TxState.choosing_category)
    await state.update_data(kind=kind)
    await _replace_flow_panel(
        callback,
        state,
        _msg("tx_step_category", "مرحله ۳ از ۳\n\nدسته‌بندی هزینه رو انتخاب کن. اگر نمی‌خوای دسته‌بندی داشته باشه، بدون دسته‌بندی رو بزن.", "🏷"),
        reply_markup=categories(kind, await db.user_categories(callback.from_user.id, "expense")),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(TxState.editing_kind, F.data.startswith("txkind:"))
async def choose_edit_kind(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    kind = callback.data.split(":", 1)[1]
    data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    if kind == "income":
        await db.update_transaction(
            tx_id=int(data["edit_tx_id"]),
            user_id=callback.from_user.id,
            kind="income",
            title=data["title"],
            amount=int(data["amount"]),
            category=None,
        )
        await state.clear()
        await _replace_panel(
            callback.message,
            "\n".join([
                indexed_line("tx_edit_saved", "درآمد ویرایش شد", 0, "✅"),
                "",
                indexed_line("tx_edit_saved", f"عنوان: {data['title']}", 1, "✅"),
                indexed_line("tx_edit_saved", f"مبلغ: {format_rial(int(data['amount']))}", 2, "✅"),
            ]),
            reply_markup=None,
            parse_mode="HTML",
        )
        await callback.answer()
        return
    await state.set_state(TxState.editing_category)
    await state.update_data(kind=kind)
    await _replace_flow_panel(
        callback,
        state,
        line("tx_step_category", "دسته‌بندی جدید هزینه را انتخاب کن.", "🏷"),
        reply_markup=categories(kind, await db.user_categories(callback.from_user.id, "expense")),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(TxState.choosing_category, F.data.startswith("catcustom:"))
async def choose_custom_category(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, kind, category_id_raw = callback.data.split(":")
    row = await db.get_user_category(int(category_id_raw), callback.from_user.id)
    if row is None or not int(row["is_active"]):
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    category = str(row["name"])
    await state.update_data(kind=kind, category_value=category, category_label=category)
    await _ask_transaction_date(callback.message, state, replace=True)
    await callback.answer()


@router.callback_query(TxState.choosing_category, F.data.startswith("cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, kind, index_raw = callback.data.split(":")
    index = int(index_raw)
    source = EXPENSE_CATEGORIES
    category = source[index]
    category_value = None if category == "بدون دسته‌بندی" else category
    await state.update_data(kind=kind, category_value=category_value, category_label=category)
    await _ask_transaction_date(callback.message, state, replace=True)
    await callback.answer()


@router.callback_query(TxState.editing_category, F.data.startswith("catcustom:"))
async def choose_edit_custom_category(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, kind, category_id_raw = callback.data.split(":")
    row = await db.get_user_category(int(category_id_raw), callback.from_user.id)
    if row is None or not int(row["is_active"]):
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    category = str(row["name"])
    data = await state.get_data()
    await db.update_transaction(
        tx_id=int(data["edit_tx_id"]),
        user_id=callback.from_user.id,
        kind=kind,
        title=data["title"],
        amount=int(data["amount"]),
        category=category,
    )
    await state.clear()
    await _replace_panel(
        callback.message,
        "\n".join([
            indexed_line("tx_edit_saved", "تراکنش ویرایش شد", 0, "✅"),
            "",
            indexed_line("tx_edit_saved", "نوع: هزینه", 1, "✅"),
            indexed_line("tx_edit_saved", f"عنوان: {data['title']}", 2, "✅"),
            indexed_line("tx_edit_saved", f"مبلغ: {format_rial(int(data['amount']))}", 3, "✅"),
            indexed_line("tx_edit_saved", f"دسته‌بندی: {category}", 4, "✅"),
        ]),
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(TxState.editing_category, F.data.startswith("cat:"))
async def choose_edit_category(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, kind, index_raw = callback.data.split(":")
    index = int(index_raw)
    source = EXPENSE_CATEGORIES
    category = source[index]
    category_value = None if category == "بدون دسته‌بندی" else category
    data = await state.get_data()
    await db.update_transaction(
        tx_id=int(data["edit_tx_id"]),
        user_id=callback.from_user.id,
        kind=kind,
        title=data["title"],
        amount=int(data["amount"]),
        category=category_value,
    )
    await state.clear()
    label = "هزینه" if kind == "expense" else "درآمد"
    await _replace_panel(
        callback.message,
        "\n".join([
            indexed_line("tx_edit_saved", "تراکنش ویرایش شد", 0, "✅"),
            "",
            indexed_line("tx_edit_saved", f"نوع: {label}", 1, "✅"),
            indexed_line("tx_edit_saved", f"عنوان: {data['title']}", 2, "✅"),
            indexed_line("tx_edit_saved", f"مبلغ: {format_rial(int(data['amount']))}", 3, "✅"),
            indexed_line("tx_edit_saved", f"دسته‌بندی: {category}", 4, "✅"),
        ]),
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tx:open:"))
async def open_transaction(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_transaction(tx_id, callback.from_user.id)
    if row is None:
        await _replace_panel(callback.message, line("warning", "این تراکنش پیدا نشد.", "⚠️"), reply_markup=None, parse_mode="HTML")
        await callback.answer()
        return
    await _replace_panel(callback.message, _tx_summary(row, settings.tzinfo), reply_markup=transaction_actions(row), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tx:edit:"))
async def edit_transaction(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_transaction(tx_id, callback.from_user.id)
    if row is None:
        await callback.answer("تراکنش پیدا نشد.", show_alert=True)
        return
    await state.set_state(TxState.editing_amount)
    await state.update_data(edit_tx_id=tx_id)
    await _replace_flow_panel(
        callback,
        state,
        _msg("tx_edit", f"مبلغ و عنوان جدید را بفرست. مثال:\n\nقهوه ۹۰ هزار", "✏️"),
        reply_markup=cancel_only(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tx:delete_prompt:"))
async def delete_transaction_prompt(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_transaction(tx_id, callback.from_user.id)
    if row is None:
        await callback.answer("تراکنش پیدا نشد.", show_alert=True)
        return
    await _replace_panel(
        callback.message,
        line("delete_confirm", f"حذف تراکنش «{row['title']}» را تایید می‌کنی؟", "🗑"),
        reply_markup=transaction_delete_confirm(tx_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tx:delete:"))
async def delete_transaction_confirm(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.delete_transaction(tx_id, callback.from_user.id)
    if row is None:
        await callback.answer("تراکنش پیدا نشد.", show_alert=True)
        return
    await _replace_panel(callback.message, line("delete_done", "تراکنش حذف شد.", "✅"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tx:category:"))
async def change_transaction_category(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_transaction(tx_id, callback.from_user.id)
    if row is None:
        await callback.answer("تراکنش پیدا نشد.", show_alert=True)
        return
    if row["kind"] != "expense":
        await callback.answer("درآمد دسته‌بندی ندارد.", show_alert=True)
        return
    await _replace_panel(callback.message, line("tx_manage", "دسته‌بندی جدید را انتخاب کن.", "🏷"), reply_markup=transaction_category(tx_id, await db.user_categories(callback.from_user.id, "expense")), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("txcatcustom:"))
async def save_custom_transaction_category(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, tx_raw, category_id_raw = callback.data.split(":")
    row = await db.get_user_category(int(category_id_raw), callback.from_user.id)
    if row is None or not int(row["is_active"]):
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    category = str(row["name"])
    await db.update_transaction_category(int(tx_raw), callback.from_user.id, category)
    await _replace_panel(callback.message, line("tx_manage", f"دسته‌بندی به «{category}» تغییر کرد.", "✅"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("txcat:"))
async def save_transaction_category(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, tx_raw, index_raw = callback.data.split(":")
    tx_id = int(tx_raw)
    category = EXPENSE_CATEGORIES[int(index_raw)]
    category_value = None if category == "بدون دسته‌بندی" else category
    await db.update_transaction_category(tx_id, callback.from_user.id, category_value)
    await _replace_panel(callback.message, line("tx_manage", f"دسته‌بندی به «{category}» تغییر کرد.", "✅"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tx:date:"))
async def edit_transaction_date(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    tx_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_transaction(tx_id, callback.from_user.id)
    if row is None:
        await callback.answer("تراکنش پیدا نشد.", show_alert=True)
        return
    await state.set_state(TxState.editing_date)
    await state.update_data(edit_tx_id=tx_id)
    await _replace_flow_panel(
        callback,
        state,
        _msg("tx_date_edit", "تاریخ جدید را بفرست. مثال:\n\n۱۴۰۳/۰۳/۱۰\nیا بنویس امروز / دیروز", "📅"),
        reply_markup=transaction_date_picker(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(TxState.editing_date, F.data == "date:today")
async def choose_today_for_edit_date(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    data = await state.get_data()
    now = datetime.now(settings.tzinfo)
    await db.update_transaction_date(int(data["edit_tx_id"]), callback.from_user.id, _to_utc_iso(now))
    await state.clear()
    await _replace_panel(callback.message, line("tx_date_edit", f"تاریخ تراکنش به {jalali_date_label(now)} تغییر کرد.", "✅"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.message(TxState.editing_date, F.text & ~F.text.startswith("/"))
async def capture_transaction_date(message: Message, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    await _clear_flow_panel(bot, state)
    parsed = parse_jalali_or_gregorian_date(message.text or "", settings.tzinfo)
    if parsed is None:
        await _answer_flow_panel(message, state, line("warning", "تاریخ را متوجه نشدم. مثال: ۱۴۰۳/۰۳/۱۰", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    data = await state.get_data()
    await db.update_transaction_date(int(data["edit_tx_id"]), message.from_user.id, _to_utc_iso(parsed))
    await state.clear()
    await message.answer(line("tx_date_edit", f"تاریخ تراکنش به {jalali_date_label(parsed)} تغییر کرد.", "✅"), reply_markup=reply_main_menu(_is_admin(message, settings)), parse_mode="HTML")


@router.callback_query(F.data == "profile:home")
async def profile_home(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await _send_profile(callback.message, bot, db, settings, replace=True)
    await callback.answer()


@router.callback_query(F.data == "profile:reminders")
async def profile_reminders(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await _send_reminders_menu(callback.message, bot, db, replace=True)
    await callback.answer()


@router.callback_query(F.data == "profile:delete_data")
async def profile_delete_data(callback: CallbackQuery) -> None:
    await _replace_panel(
        callback.message,
        line("delete_confirm", "حذف کامل اطلاعات را تایید می‌کنی؟", "🗑"),
        reply_markup=confirm_delete_data(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile:categories")
async def profile_categories(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await _send_profile_categories(callback.message, replace=True)
    await callback.answer()


@router.callback_query(F.data.startswith("profile:categories:"))
async def profile_category_list(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    kind = callback.data.rsplit(":", 1)[1]
    await _send_user_category_list(callback.message, db, callback.from_user.id, kind, replace=True)
    await callback.answer()


@router.callback_query(F.data.startswith("profile:category_add:"))
async def profile_category_add(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    kind = callback.data.rsplit(":", 1)[1]
    await state.set_state(TxState.adding_category)
    await state.update_data(category_kind=kind)
    await _replace_flow_panel(callback, state, line("profile_categories", "نام دسته‌بندی جدید را بفرست.", "🏷"), reply_markup=cancel_only(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("profile:category:"))
async def profile_category_open(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    category_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_user_category(category_id, callback.from_user.id)
    if row is None or not int(row["is_active"]):
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    kind_label = "هزینه" if row["kind"] == "expense" else "درآمد"
    await _replace_panel(
        callback.message,
        _msg("profile_categories", f"دسته‌بندی {kind_label}: {row['name']}\n\nچه کاری انجام بدهم؟", "🏷"),
        reply_markup=user_category_actions(category_id, row["kind"]),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("profile:category_rename:"))
async def profile_category_rename(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    category_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_user_category(category_id, callback.from_user.id)
    if row is None:
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    await state.set_state(TxState.renaming_category)
    await state.update_data(category_id=category_id, category_kind=row["kind"])
    await _replace_flow_panel(callback, state, line("profile_categories", "نام جدید دسته‌بندی را بفرست.", "✏️"), reply_markup=cancel_only(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("profile:category_delete:"))
async def profile_category_delete(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    category_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_user_category(category_id, callback.from_user.id)
    if row is None:
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    await db.deactivate_user_category(category_id, callback.from_user.id)
    await _send_user_category_list(callback.message, db, callback.from_user.id, row["kind"], replace=True)
    await callback.answer()


@router.callback_query(F.data.startswith("profile:category_move:"))
async def profile_category_move(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    _, _, category_id_raw, direction_raw = callback.data.split(":")
    category_id = int(category_id_raw)
    row = await db.get_user_category(category_id, callback.from_user.id)
    if row is None:
        await callback.answer("دسته‌بندی پیدا نشد.", show_alert=True)
        return
    await db.move_user_category(category_id, callback.from_user.id, -1 if direction_raw == "up" else 1)
    await _send_user_category_list(callback.message, db, callback.from_user.id, row["kind"], replace=True)
    await callback.answer("ترتیب تغییر کرد.")


@router.callback_query(F.data == "reminder:set_budget")
async def set_budget_start(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await state.set_state(TxState.setting_budget)
    await _replace_flow_panel(
        callback,
        state,
        _msg("budget", "بودجه ماهانه را بفرست. مثال:\n\n۱۰ میلیون", "💰"),
        reply_markup=cancel_only(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TxState.setting_budget, F.text & ~F.text.startswith("/"))
async def capture_budget(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    await _clear_flow_panel(bot, state)
    parsed = parse_amount_message(message.text or "")
    if parsed is None:
        await _answer_flow_panel(message, state, line("warning", "بودجه را متوجه نشدم. مثال: ۱۰ میلیون", "⚠️"), reply_markup=cancel_only(), parse_mode="HTML")
        return
    await db.set_monthly_budget(message.from_user.id, int(parsed.amount))
    await state.clear()
    await message.answer(line("budget", f"بودجه ماهانه روی {format_rial(parsed.amount)} تنظیم شد.", "✅"), parse_mode="HTML")


@router.callback_query(F.data == "reminder:toggle_night")
async def toggle_night_reminder(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    prefs = await db.get_preferences(callback.from_user.id)
    enabled = not bool(int(prefs["night_reminder_enabled"]))
    await db.set_night_reminder(callback.from_user.id, enabled)
    prefs = await db.get_preferences(callback.from_user.id)
    await _replace_panel(callback.message, line("reminders", f"یادآوری شبانه {'روشن' if enabled else 'خاموش'} شد.", "⏰"), reply_markup=reminders_menu(prefs), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "reminder:clear_budget")
async def clear_budget(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await db.set_monthly_budget(callback.from_user.id, 0)
    prefs = await db.get_preferences(callback.from_user.id)
    await _replace_panel(callback.message, line("budget", "بودجه ماهانه حذف شد.", "✅"), reply_markup=reminders_menu(prefs), parse_mode="HTML")
    await callback.answer()


@router.callback_query(TxState.choosing_date, F.data == "date:today")
async def choose_today_for_new_transaction(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_pending_transaction(callback.message, state, db, settings, callback.from_user.id, datetime.now(settings.tzinfo), bot)
    await callback.answer()


@router.message(TxState.choosing_date, F.text & ~F.text.startswith("/"))
async def capture_new_transaction_date(message: Message, state: FSMContext, bot: Bot, db: Database, settings) -> None:
    await _clear_flow_panel(bot, state)
    parsed = parse_jalali_or_gregorian_date(message.text or "", settings.tzinfo)
    if parsed is None:
        await _answer_flow_panel(message, state, line("warning", "تاریخ را متوجه نشدم. مثال: ۱۴۰۳/۰۳/۱۰", "⚠️"), reply_markup=transaction_date_picker(), parse_mode="HTML")
        return
    await _save_pending_transaction(message, state, db, settings, message.from_user.id, parsed, bot)


@router.callback_query(F.data == "ticket:cancel")
async def cancel_ticket(callback: CallbackQuery, state: FSMContext, settings) -> None:
    await state.clear()
    await _replace_panel(callback.message, line("ticket", "ارسال تیکت لغو شد.", "✖️"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "tx:cancel")
async def cancel_tx(callback: CallbackQuery, state: FSMContext, settings) -> None:
    await state.clear()
    await _replace_panel(callback.message, line("cancel", "عملیات لغو شد.", "✖️"), reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "report:week")
async def week_report(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await send_report_message(callback.message, await build_report(db, callback.from_user.id, "week", settings.tzinfo), "week")
    await callback.answer()


@router.callback_query(F.data == "report:today")
async def today_report(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await send_report_message(callback.message, await build_report(db, callback.from_user.id, "today", settings.tzinfo), "today")
    await callback.answer()


@router.callback_query(F.data == "report:month")
async def month_report(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    await send_report_message(callback.message, await build_report(db, callback.from_user.id, "month", settings.tzinfo), "month")
    await callback.answer()


@router.callback_query(F.data == "tx:recent")
@router.callback_query(F.data == "tx:manage")
async def manage_transactions(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    rows = await db.recent_transactions(callback.from_user.id, 10)
    if not rows:
        await _replace_panel(callback.message, format_recent(rows), reply_markup=None, parse_mode="HTML")
    else:
        await _replace_panel(
            callback.message,
            line("tx_manage", "یکی از تراکنش‌ها را انتخاب کن.", "🧾"),
            reply_markup=transaction_list(rows),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("backup:"))
async def export_backup(callback: CallbackQuery, bot: Bot, db: Database, settings) -> None:
    if not await _callback_join_guard(callback, bot, db):
        return
    period_key = str(callback.data).split(":", 1)[1]
    exported = await _export_backup(callback.message, callback.from_user.id, db, settings, period_key)
    if not exported:
        await callback.answer("بازه بکاپ معتبر نیست.", show_alert=True)
        return
    await callback.answer("فایل آماده شد.")


@router.callback_query(F.data == "privacy:delete")
async def delete_data_prompt(callback: CallbackQuery) -> None:
    await _replace_panel(
        callback.message,
        _msg("delete_confirm", "اگر تایید کنی، حساب کاربری و همه تراکنش‌های ذخیره‌شده‌ات از LootLog حذف می‌شود.\nاین کار قابل برگشت نیست.", "🗑"),
        reply_markup=confirm_delete_data(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "privacy:confirm_delete")
async def delete_data_confirm(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await db.delete_user_data(callback.from_user.id)
    await _replace_panel(callback.message, line("delete_done", "اطلاعاتت از LootLog حذف شد. هر وقت خواستی، دوباره /start بزن.", "✅"), reply_markup=None, parse_mode="HTML")
    await callback.answer("حذف شد.")


@router.message(Command("help"))
async def help_command(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_help(message, bot, db, settings)


@router.message(Command("week"))
async def week_command(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_week_report(message, bot, db, settings)


@router.message(Command("today"))
async def today_command(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_today_report(message, bot, db, settings)


@router.message(Command("month"))
async def month_command(message: Message, bot: Bot, db: Database, settings) -> None:
    await _send_month_report(message, bot, db, settings)


@router.message(Command("backup"))
async def backup_command(message: Message, bot: Bot, db: Database) -> None:
    await _send_backup_menu(message, bot, db)


@router.message(Command("reminders"))
async def reminders_command(message: Message, bot: Bot, db: Database) -> None:
    await _send_reminders_menu(message, bot, db)


@router.message(Command("delete_my_data"))
async def delete_data_command(message: Message) -> None:
    await message.answer(
        line("delete_confirm", "حذف کامل اطلاعات را تایید می‌کنی؟", "🗑"),
        reply_markup=confirm_delete_data(),
        parse_mode="HTML",
    )
