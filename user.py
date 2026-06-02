from __future__ import annotations

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .texts import EXPENSE_CATEGORIES, custom_emoji_id
from .utils.amounts import format_rial
from .utils.jalali import jalali_date_label


BTN_ADD = "➕ ثبت"
BTN_MANAGE_TX = "🧾 مدیریت تراکنش‌ها"
BTN_PROFILE = "👤 پروفایل"
BTN_TICKET = "🎫 تیکت"
BTN_TODAY = "📍 امروز"
BTN_WEEK = "📊 هفته"
BTN_MONTH = "📈 ماه"
BTN_DELETE_DATA = "🗑 حذف‌داده"
BTN_BACKUP = "📦 بکاپ‌گیری"
BTN_HELP = "❔ راهنما"
BTN_REMINDERS = "⏰ یادآوری"


BUTTON_STYLE = {
    "primary": "primary",
    "success": "success",
    "danger": "danger",
    "neutral": None,
}

# راهنمای رنگ دکمه‌ها. برای تغییر رنگ، مقدار BUTTON_STYLE_MAP را عوض کن.
# این styleها هم برای InlineKeyboardButton و هم برای KeyboardButton منوی پایین استفاده می‌شوند.
BUTTON_STYLE_GUIDE = {
    "primary": "آبی - مسیرهای منو، گزارش‌ها، دسته‌بندی‌ها و بازگشت",
    "success": "سبز - تایید، درآمد، خروجی، افزودن و ارسال",
    "danger": "قرمز - هزینه، حذف، لغو و مسدودسازی",
    "neutral": "بدون رنگ اختصاصی - دکمه‌های ساده",
}

BUTTON_STYLE_MAP = {
    "menu_add": "success",
    "menu_manage": "success",
    "menu_profile": "primary",
    "menu_ticket": "primary",
    "menu_today": "primary",
    "menu_week": "primary",
    "menu_month": "primary",
    "menu_backup": "success",
    "menu_help": "neutral",
    "menu_delete_data": "danger",
    "menu_reminders": "primary",
    "report_today": "primary",
    "report_week": "primary",
    "report_month": "primary",
    "backup": "primary",
    "profile": "primary",
    "ticket": "success",
    "delete": "danger",
    "help": "neutral",
    "privacy_delete": "danger",
    "expense": "danger",
    "income": "success",
    "category": "primary",
    "join_check": "success",
    "admin": "primary",
    "admin_broadcast": "success",
    "admin_backup": "success",
    "admin_export": "success",
    "admin_find": "neutral",
    "admin_text": "neutral",
    "admin_block": "danger",
    "admin_unblock": "success",
    "confirm": "success",
    "bulk": "primary",
    "reminders": "primary",
    "budget": "success",
    "tx_action": "primary",
    "today": "success",
    "cancel": "neutral",
    "back": "primary",
}


def _style(name: str) -> str | None:
    return BUTTON_STYLE[BUTTON_STYLE_MAP[name]]


def _button(text: str, data: str, style: str | None = None, emoji_key: str | None = None) -> InlineKeyboardButton:
    payload = {"text": text, "callback_data": data}
    if style:
        payload["style"] = style
    emoji_id = custom_emoji_id(emoji_key) if emoji_key else None
    if emoji_id:
        payload["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**payload)


def _reply_button(text: str, style_key: str, emoji_key: str) -> KeyboardButton:
    payload = {"text": text}
    style = _style(style_key)
    if style:
        payload["style"] = style
    emoji_id = custom_emoji_id(emoji_key)
    if emoji_id:
        payload["icon_custom_emoji_id"] = emoji_id
    return KeyboardButton(**payload)


def _url_button(text: str, url: str, style_key: str, emoji_key: str) -> InlineKeyboardButton:
    payload = {"text": text, "url": url}
    style = _style(style_key)
    if style:
        payload["style"] = style
    emoji_id = custom_emoji_id(emoji_key)
    if emoji_id:
        payload["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**payload)


def rows(buttons: list[InlineKeyboardButton], width: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]



def reply_main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            _reply_button(BTN_ADD, "menu_add", "button_menu_add"),
            _reply_button(BTN_MANAGE_TX, "menu_manage", "button_menu_manage"),
            _reply_button(BTN_PROFILE, "menu_profile", "button_menu_profile"),
        ],
        [
            _reply_button(BTN_TODAY, "menu_today", "button_menu_today"),
            _reply_button(BTN_WEEK, "menu_week", "button_menu_week"),
            _reply_button(BTN_MONTH, "menu_month", "button_menu_month"),
        ],
        [
            _reply_button(BTN_BACKUP, "menu_backup", "button_menu_backup"),
            _reply_button(BTN_TICKET, "menu_ticket", "button_menu_ticket"),
        ],
        [
            _reply_button(BTN_HELP, "menu_help", "button_menu_help"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, input_field_placeholder="یک گزینه را انتخاب کن")


def cancel_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("لغو", "tx:cancel", _style("cancel"), "cancel")]])


def add_step_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("افزودن لیستی", "bulk:start", _style("bulk"), "button_bulk")],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def bulk_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("لیست هزینه", "bulkkind:expense", _style("expense"), "button_expense"),
                _button("لیست درآمد", "bulkkind:income", _style("income"), "button_income"),
            ],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def transaction_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("هزینه", "txkind:expense", _style("expense"), "button_expense"),
                _button("درآمد", "txkind:income", _style("income"), "button_income"),
            ],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def categories(kind: str = "expense", custom_categories: list | None = None) -> InlineKeyboardMarkup:
    buttons = [_button(label, f"cat:expense:{index}", _style("category"), "button_category") for index, label in enumerate(EXPENSE_CATEGORIES)]
    for row in custom_categories or []:
        buttons.append(_button(str(row["name"]), f"catcustom:expense:{row['id']}", _style("category"), "button_category"))
    buttons.append(_button("لغو", "tx:cancel", _style("cancel"), "cancel"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def force_join(channels: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for channel in channels:
        url = channel.get("invite_link") or channel.get("chat_ref")
        if str(url).startswith("@"):
            url = f"https://t.me/{str(url).lstrip('@')}"
        keyboard.append([_url_button(channel["title"], url, "join_check", "button_join")])
    keyboard.append([_button("بررسی عضویت", "join:check", _style("join_check"), "button_join")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def transaction_date_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("امروز", "date:today", _style("today"), "button_today")],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def reminders_menu(prefs) -> InlineKeyboardMarkup:
    budget = int(prefs["monthly_budget"])
    reminder_enabled = bool(int(prefs["night_reminder_enabled"]))
    budget_text = "تنظیم بودجه" if budget <= 0 else f"بودجه: {format_rial(budget)}"
    reminder_text = "یادآوری شبانه: روشن" if reminder_enabled else "یادآوری شبانه: خاموش"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(budget_text, "reminder:set_budget", _style("budget"), "button_budget")],
            [_button(reminder_text, "reminder:toggle_night", _style("reminders"), "button_reminder")],
            [_button("حذف بودجه", "reminder:clear_budget", _style("delete"), "button_delete")],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def backup_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("۱ روز گذشته", "backup:1d", _style("backup"), "button_backup_day"),
                _button("۱ هفته گذشته", "backup:1w", _style("backup"), "button_backup_week"),
            ],
            [
                _button("۱ ماه گذشته", "backup:1m", _style("backup"), "button_backup_month"),
                _button("۳ ماه گذشته", "backup:3m", _style("backup"), "button_backup_3m"),
            ],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("تنظیمات یادآوری و بودجه", "profile:reminders", _style("profile"), "button_profile_reminders")],
            [_button("دسته‌بندی‌های من", "profile:categories", _style("category"), "button_profile_categories")],
            [_button("حذف کامل اطلاعات من", "profile:delete_data", _style("delete"), "button_menu_delete_data")],
            [_button("لغو", "tx:cancel", _style("cancel"), "cancel")],
        ]
    )


def profile_categories_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("دسته‌بندی‌های هزینه", "profile:categories:expense", _style("category"), "button_profile_expense_categories")],
            [_button("دسته‌بندی‌های درآمد", "profile:categories:income", _style("income"), "button_profile_income_categories")],
            [_button("بازگشت", "profile:home", _style("back"), "button_menu_profile")],
        ]
    )


def user_categories_menu(kind: str, categories: list) -> InlineKeyboardMarkup:
    keyboard = []
    for row in categories:
        keyboard.append([_button(row["name"], f"profile:category:{row['id']}", _style("category"), "button_profile_category")])
    keyboard.append([_button("افزودن دسته‌بندی", f"profile:category_add:{kind}", _style("confirm"), "button_profile_category_add")])
    keyboard.append([_button("بازگشت", "profile:categories", _style("back"), "button_profile_categories")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def user_category_actions(category_id: int, kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("تغییر نام", f"profile:category_rename:{category_id}", _style("profile"), "button_profile_category_rename")],
            [
                _button("بالا", f"profile:category_move:{category_id}:up", _style("profile"), "button_profile_category_up"),
                _button("پایین", f"profile:category_move:{category_id}:down", _style("profile"), "button_profile_category_down"),
            ],
            [_button("حذف/غیرفعال", f"profile:category_delete:{category_id}", _style("delete"), "button_profile_category_delete")],
            [_button("بازگشت", f"profile:categories:{kind}", _style("back"), "button_profile_categories")],
        ]
    )


def ticket_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("لغو", "ticket:cancel", _style("cancel"), "cancel")]])


def admin_ticket_settings(has_group: bool) -> InlineKeyboardMarkup:
    keyboard = [[_button("تنظیم گروه تیکت", "admin:tickets:set_group", _style("admin"), "admin_tickets")]]
    if has_group:
        keyboard.append([_button("حذف گروه تیکت", "admin:tickets:clear_group", _style("delete"), "button_delete")])
    keyboard.append([_button("بازگشت", "admin:home", _style("back"), "admin")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _shorten_button_text(text: str, limit: int = 58) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tx_button_label(row) -> str:
    sign = "-" if row["kind"] == "expense" else "+"
    category = f" | {row['category']}" if row["kind"] == "expense" and row["category"] else ""
    date_label = jalali_date_label(datetime.fromisoformat(row["created_at"]))
    return _shorten_button_text(f"{date_label} | {sign} {row['title']} | {format_rial(int(row['amount']))}{category}")


def transaction_list(transactions: list) -> InlineKeyboardMarkup:
    keyboard = [[_button(_tx_button_label(row), f"tx:open:{row['id']}", _style("tx_action"), "button_tx_select")] for row in transactions]
    keyboard.append([_button("لغو", "tx:cancel", _style("cancel"), "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def transaction_actions(row) -> InlineKeyboardMarkup:
    tx_id = int(row["id"])
    keyboard = [
        [_button("ویرایش مبلغ/عنوان", f"tx:edit:{tx_id}", _style("tx_action"), "button_tx_edit")],
        [_button("حذف", f"tx:delete_prompt:{tx_id}", _style("delete"), "button_tx_delete")],
        [_button("اصلاح تاریخ", f"tx:date:{tx_id}", _style("tx_action"), "button_tx_date")],
    ]
    if row["kind"] == "expense":
        keyboard.insert(2, [_button("تغییر دسته‌بندی", f"tx:category:{tx_id}", _style("category"), "button_tx_category")])
    keyboard.append([_button("بازگشت", "tx:manage", _style("back"), "button_menu_manage")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def transaction_category(tx_id: int, custom_categories: list | None = None) -> InlineKeyboardMarkup:
    buttons = [_button(label, f"txcat:{tx_id}:{index}", _style("category"), "button_tx_category") for index, label in enumerate(EXPENSE_CATEGORIES)]
    for row in custom_categories or []:
        buttons.append(_button(str(row["name"]), f"txcatcustom:{tx_id}:{row['id']}", _style("category"), "button_tx_category"))
    buttons.append(_button("لغو", "tx:cancel", _style("cancel"), "cancel"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def transaction_delete_confirm(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("بله، حذف شود", f"tx:delete:{tx_id}", _style("delete"), "button_tx_delete")],
            [_button("لغو", f"tx:open:{tx_id}", _style("cancel"), "cancel")],
        ]
    )


def admin_channels(channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for channel in channels:
        keyboard.append([
            _button(f"حذف {channel['title']}", f"admin:del_channel:{channel['id']}", _style("delete"), "button_delete")
        ])
    keyboard.append([
        _button("افزودن کانال", "admin:add_channel", _style("admin_backup"), "join"),
        _button("بازگشت", "admin:home", _style("back"), "admin"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("لغو", "admin:home", _style("cancel"), "cancel")]])


def broadcast_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("تایید ارسال همگانی", "admin:broadcast_confirm", _style("confirm"), "button_broadcast_confirm")],
            [_button("لغو", "admin:broadcast_cancel", _style("cancel"), "cancel")],
        ]
    )


def confirm_delete_data() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("بله، همه اطلاعاتم حذف شود", "privacy:confirm_delete", _style("delete"), "button_delete")],
            [_button("لغو", "menu:home", _style("cancel"), "cancel")],
        ]
    )


def admin_user_actions(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    action = _button("آزاد کردن کاربر", f"admin:user:unblock:{user_id}", _style("admin_unblock"), "admin_unblock")
    if not is_blocked:
        action = _button("مسدود کردن کاربر", f"admin:user:block:{user_id}", _style("admin_block"), "admin_block")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action],
            [_button("بازگشت", "admin:home", _style("back"), "admin")],
        ]
    )

# Admin Panel v2 keyboards. These later definitions intentionally replace the older admin helpers.
def admin_home() -> InlineKeyboardMarkup:
    buttons = [
        _button("داشبورد", "admin:dashboard", _style("admin"), "admin_dashboard"),
        _button("کاربران", "admin:users", _style("admin"), "admin_users"),
        _button("جوین اجباری", "admin:channels", _style("admin"), "admin_channels"),
        _button("ارسال همگانی", "admin:broadcast", _style("admin_broadcast"), "admin_broadcast"),
        _button("خروجی امن", "admin:exports", _style("admin_export"), "admin_exports"),
        _button("تنظیمات", "admin:settings", _style("admin"), "admin_settings"),
        _button("امنیت و لاگ‌ها", "admin:security", _style("admin"), "admin_security"),
        _button("سلامت سیستم", "admin:health", _style("admin"), "admin_health"),
        _button("بازگشت", "menu:home", _style("back"), "menu"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def admin_users_menu() -> InlineKeyboardMarkup:
    buttons = [
        _button("جستجو با آیدی", "admin:user_search:id", _style("admin_find"), "admin_user_search_id"),
        _button("جستجو با یوزرنیم", "admin:user_search:username", _style("admin_find"), "admin_user_search_username"),
        _button("کاربران جدید", "admin:users:list:new", _style("admin"), "admin_users_new"),
        _button("کاربران فعال", "admin:users:list:active", _style("admin"), "admin_users_active"),
        _button("کاربران مسدود", "admin:users:list:blocked", _style("admin_block"), "admin_users_blocked"),
        _button("بازگشت", "admin:home", _style("back"), "admin"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def admin_user_actions(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    action = _button("آزاد کردن کاربر", f"admin:user:unblock:{user_id}", _style("admin_unblock"), "admin_unblock")
    if not is_blocked:
        action = _button("مسدود کردن کاربر", f"admin:user:block:{user_id}", _style("admin_block"), "admin_block")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action],
            [_button("ارسال پیام خصوصی", f"admin:user:message:{user_id}", _style("admin_broadcast"), "admin_user_message")],
            [_url_button("رفتن به پیوی کاربر", f"tg://user?id={user_id}", "admin", "admin_user_pm")],
            [_button("حذف کامل داده‌های کاربر", f"admin:user:delete_prompt:{user_id}", _style("delete"), "admin_user_delete")],
            [_button("بازگشت به کاربران", "admin:users", _style("back"), "admin_users")],
        ]
    )


def admin_user_delete_confirm(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("بله، حذف شود", f"admin:user:delete:{user_id}", _style("delete"), "admin_user_delete")],
            [_button("لغو", f"admin:user:open:{user_id}", _style("cancel"), "cancel")],
        ]
    )


def admin_channels(channels: list) -> InlineKeyboardMarkup:
    keyboard = []
    for channel in channels:
        status = "روشن" if int(channel["is_active"]) else "خاموش"
        keyboard.append([_button(f"{channel['title']} | {status}", f"admin:channel:{channel['id']}", _style("admin"), "admin_channels")])
    keyboard.append([_button("افزودن کانال", "admin:add_channel", _style("admin_backup"), "join")])
    keyboard.append([_button("بازگشت", "admin:home", _style("back"), "admin")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_channel_actions(channel_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "غیرفعال کردن" if is_active else "فعال کردن"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(toggle_text, f"admin:channel:toggle:{channel_id}", _style("admin"), "admin_channel_toggle")],
            [_button("تست دسترسی ربات", f"admin:channel:test:{channel_id}", _style("admin_find"), "admin_channel_test")],
            [
                _button("بالا", f"admin:channel:move:{channel_id}:up", _style("admin"), "admin_channel_up"),
                _button("پایین", f"admin:channel:move:{channel_id}:down", _style("admin"), "admin_channel_down"),
            ],
            [_button("حذف کانال", f"admin:del_channel:{channel_id}", _style("delete"), "button_delete")],
            [_button("بازگشت", "admin:channels", _style("back"), "admin_channels")],
        ]
    )


def admin_broadcast_menu() -> InlineKeyboardMarkup:
    buttons = [
        _button("همه کاربران", "admin:broadcast:start:all", _style("admin_broadcast"), "admin_broadcast_all"),
        _button("کاربران فعال", "admin:broadcast:start:active", _style("admin_broadcast"), "admin_broadcast_active"),
        _button("کاربران جدید", "admin:broadcast:start:new", _style("admin_broadcast"), "admin_broadcast_new"),
        _button("کاربران غیرفعال", "admin:broadcast:start:inactive", _style("admin_broadcast"), "admin_broadcast_inactive"),
        _button("مسدود نشده‌ها", "admin:broadcast:start:unblocked", _style("admin_broadcast"), "admin_broadcast_unblocked"),
        _button("ارسال تست به خودم", "admin:broadcast:start:test", _style("admin_find"), "admin_broadcast_test"),
        _button("تاریخچه کمپین‌ها", "admin:broadcast:history", _style("admin"), "admin_broadcast_history"),
        _button("بازگشت", "admin:home", _style("back"), "admin"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def broadcast_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("تایید ارسال", "admin:broadcast_confirm", _style("confirm"), "button_broadcast_confirm")],
            [_button("لغو", "admin:broadcast_cancel", _style("cancel"), "cancel")],
        ]
    )


def admin_exports_menu(is_owner: bool) -> InlineKeyboardMarkup:
    buttons = [
        _button("خروجی کاربران", "admin:export_users", _style("admin_export"), "admin_export_users"),
        _button("خروجی تیکت‌ها", "admin:export_tickets", _style("admin_export"), "admin_export_tickets"),
        _button("خروجی کانال‌ها", "admin:export_channels", _style("admin_export"), "admin_export_channels"),
        _button("خروجی لاگ ادمین", "admin:export_admin_logs", _style("admin_export"), "admin_export_logs"),
        _button("خروجی خطاها", "admin:export_errors", _style("admin_export"), "admin_export_errors"),
    ]
    if is_owner:
        buttons.insert(0, _button("بکاپ دیتابیس", "admin:backup", _style("admin_backup"), "admin_backup"))
    buttons.append(_button("بازگشت", "admin:home", _style("back"), "admin"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def admin_settings_menu(values: dict[str, bool]) -> InlineKeyboardMarkup:
    def label(title: str, key: str) -> str:
        return f"{title}: {'روشن' if values.get(key) else 'خاموش'}"
    buttons = [
        _button(label("جوین اجباری", "force_join_enabled"), "admin:setting:toggle:force_join_enabled", _style("admin"), "admin_setting_join"),
        _button(label("تیکت", "ticket_enabled"), "admin:setting:toggle:ticket_enabled", _style("admin"), "admin_setting_ticket"),
        _button(label("ثبت تراکنش", "transactions_enabled"), "admin:setting:toggle:transactions_enabled", _style("admin"), "admin_setting_transactions"),
        _button(label("بکاپ‌گیری کاربر", "user_backup_enabled"), "admin:setting:toggle:user_backup_enabled", _style("admin"), "admin_setting_backup"),
        _button(label("حالت تعمیرات", "maintenance_mode"), "admin:setting:toggle:maintenance_mode", _style("admin_block"), "admin_setting_maintenance"),
        _button(label("گزارش‌های خودکار", "auto_reports_enabled"), "admin:setting:toggle:auto_reports_enabled", _style("admin"), "admin_setting_reports"),
        _button(label("یادآوری شبانه", "night_reminder_global_enabled"), "admin:setting:toggle:night_reminder_global_enabled", _style("admin"), "admin_setting_reminder"),
        _button("تنظیم گروه تیکت", "admin:tickets:set_group", _style("admin"), "admin_tickets"),
        _button("تنظیم ضداسپم", "admin:setting:set:anti_spam_min_interval", _style("admin_find"), "admin_setting_antispam"),
        _button("تنظیم ساعت‌ها", "admin:settings:schedules", _style("admin_find"), "admin_setting_time"),
        _button("بازگشت", "admin:home", _style("back"), "admin"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def admin_security_menu(is_owner: bool) -> InlineKeyboardMarkup:
    buttons = [
        _button("لاگ‌های ادمین", "admin:logs", _style("admin"), "admin_logs"),
        _button("خطاهای مهم", "admin:errors", _style("admin_block"), "admin_errors"),
        _button("لیست ادمین‌ها", "admin:admins", _style("admin"), "admin_admins"),
    ]
    if is_owner:
        buttons.extend([
            _button("افزودن ادمین", "admin:admins:add", _style("admin_unblock"), "admin_add_admin"),
            _button("حذف ادمین", "admin:admins:remove", _style("delete"), "admin_remove_admin"),
        ])
    buttons.append(_button("بازگشت", "admin:home", _style("back"), "admin"))
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))


def admin_health_menu() -> InlineKeyboardMarkup:
    buttons = [
        _button("تست پیام به ادمین", "admin:health:test_admin", _style("admin_find"), "admin_health_test_admin"),
        _button("تست پیام گروه تیکت", "admin:health:test_ticket", _style("admin_find"), "admin_health_test_ticket"),
        _button("تست کانال‌ها", "admin:health:test_channels", _style("admin_find"), "admin_channel_test"),
        _button("بازگشت", "admin:home", _style("back"), "admin"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows(buttons, 2))
