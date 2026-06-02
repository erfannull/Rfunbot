from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .texts import DEFAULT_FORCE_JOIN_TEXT, DEFAULT_START_TEXT


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    username: str | None
    full_name: str | None
    is_blocked: bool
    started_at: str
    last_seen_at: str


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")

    async def close(self) -> None:
        await self.conn.close()

    async def checkpoint(self) -> None:
        await self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        await self.conn.commit()

    async def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        existing = await self.fetchall(f"PRAGMA table_info({table})")
        if any(str(row["name"]) == column for row in existing):
            return
        await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def migrate(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                language_code TEXT,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'IRR',
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                title TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK(amount > 0),
                category TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_user_created
            ON transactions(user_id, created_at);

            CREATE TABLE IF NOT EXISTS forced_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_ref TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                invite_link TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                monthly_budget INTEGER NOT NULL DEFAULT 0,
                night_reminder_enabled INTEGER NOT NULL DEFAULT 1,
                budget_alert_month TEXT
            );

            CREATE TABLE IF NOT EXISTS user_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_user_categories_user_kind
            ON user_categories(user_id, kind, is_active, sort_order);

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                group_chat_id INTEGER NOT NULL,
                group_message_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                replied_at TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(group_chat_id, group_message_id)
            );

            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS broadcast_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                audience TEXT NOT NULL,
                status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS admin_accounts (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'support',
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await self._add_column_if_missing("forced_channels", "sort_order", "INTEGER NOT NULL DEFAULT 0")
        await self._add_column_if_missing("ticket_messages", "status", "TEXT NOT NULL DEFAULT 'open'")
        await self._add_column_if_missing("ticket_messages", "replied_at", "TEXT")
        await self._add_column_if_missing("ticket_messages", "closed_at", "TEXT")
        await self.set_default_setting("start_text", DEFAULT_START_TEXT)
        await self.set_default_setting("force_join_text", DEFAULT_FORCE_JOIN_TEXT)
        for key, value in {
            "force_join_enabled": "1",
            "ticket_enabled": "1",
            "transactions_enabled": "1",
            "user_backup_enabled": "1",
            "maintenance_mode": "0",
            "auto_reports_enabled": "1",
            "night_reminder_global_enabled": "1",
            "anti_spam_min_interval": "0.65",
            "ticket_group_chat_id": "",
        }.items():
            await self.set_default_setting(key, value)
        await self.conn.commit()

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def set_default_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (key, value),
        )

    async def get_setting(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return None if row is None else str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def upsert_user(self, user: Any) -> None:
        now = utc_now_iso()
        full_name = " ".join(part for part in [user.first_name, user.last_name] if part) or None
        await self.conn.execute(
            """
            INSERT INTO users(user_id, username, full_name, language_code, started_at, last_seen_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                language_code = excluded.language_code,
                last_seen_at = excluded.last_seen_at
            """,
            (user.id, user.username, full_name, user.language_code, now, now),
        )
        await self.conn.commit()

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def ensure_preferences(self, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO user_preferences(user_id) VALUES(?)",
            (user_id,),
        )
        await self.conn.commit()

    async def get_preferences(self, user_id: int) -> aiosqlite.Row:
        await self.ensure_preferences(user_id)
        row = await self.fetchone("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
        assert row is not None
        return row

    async def set_monthly_budget(self, user_id: int, amount: int) -> None:
        await self.ensure_preferences(user_id)
        await self.conn.execute(
            "UPDATE user_preferences SET monthly_budget = ?, budget_alert_month = NULL WHERE user_id = ?",
            (amount, user_id),
        )
        await self.conn.commit()

    async def set_night_reminder(self, user_id: int, enabled: bool) -> None:
        await self.ensure_preferences(user_id)
        await self.conn.execute(
            "UPDATE user_preferences SET night_reminder_enabled = ? WHERE user_id = ?",
            (int(enabled), user_id),
        )
        await self.conn.commit()

    async def mark_budget_alert_sent(self, user_id: int, month_key: str) -> None:
        await self.ensure_preferences(user_id)
        await self.conn.execute(
            "UPDATE user_preferences SET budget_alert_month = ? WHERE user_id = ?",
            (month_key, user_id),
        )
        await self.conn.commit()

    async def users_with_night_reminder(self) -> list[int]:
        rows = await self.fetchall(
            """
            SELECT u.user_id
            FROM users u
            LEFT JOIN user_preferences p ON p.user_id = u.user_id
            WHERE u.is_blocked = 0 AND COALESCE(p.night_reminder_enabled, 1) = 1
            """
        )
        return [int(row["user_id"]) for row in rows]

    async def active_user_ids(self) -> list[int]:
        rows = await self.fetchall("SELECT user_id FROM users WHERE is_blocked = 0")
        return [int(row["user_id"]) for row in rows]

    async def all_users(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM users ORDER BY started_at ASC")

    async def delete_user_data(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def set_user_blocked(self, user_id: int, blocked: bool) -> None:
        await self.conn.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (int(blocked), user_id))
        await self.conn.commit()

    async def user_transaction_summary(self, user_id: int) -> dict[str, int]:
        rows = await self.fetchall(
            """
            SELECT
              COUNT(*) AS tx_total,
              COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount ELSE 0 END), 0) AS expense_total,
              COALESCE(SUM(CASE WHEN kind = 'income' THEN amount ELSE 0 END), 0) AS income_total
            FROM transactions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = rows[0]
        return {key: int(row[key]) for key in row.keys()}

    async def profile_summary(self, user_id: int) -> dict[str, int | str | None]:
        user = await self.get_user(user_id)
        summary = await self.user_transaction_summary(user_id)
        return {
            "user_id": user_id,
            "username": None if user is None else user["username"],
            "full_name": None if user is None else user["full_name"],
            "started_at": None if user is None else user["started_at"],
            "last_seen_at": None if user is None else user["last_seen_at"],
            "tx_total": summary["tx_total"],
            "expense_total": summary["expense_total"],
            "income_total": summary["income_total"],
            "balance": summary["income_total"] - summary["expense_total"],
        }

    async def add_transaction(
        self,
        user_id: int,
        kind: str,
        title: str,
        amount: int,
        category: str | None,
        created_at: str | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO transactions(user_id, kind, title, amount, category, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (user_id, kind, title, amount, category, created_at or utc_now_iso()),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def update_transaction(
        self,
        tx_id: int,
        user_id: int,
        kind: str,
        title: str,
        amount: int,
        category: str | None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE transactions
            SET kind = ?, title = ?, amount = ?, category = ?
            WHERE id = ? AND user_id = ?
            """,
            (kind, title, amount, category, tx_id, user_id),
        )
        await self.conn.commit()

    async def get_transaction(self, tx_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
            (tx_id, user_id),
        )

    async def delete_transaction(self, tx_id: int, user_id: int) -> aiosqlite.Row | None:
        row = await self.get_transaction(tx_id, user_id)
        if row is None:
            return None
        await self.conn.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
        await self.conn.commit()
        return row

    async def update_transaction_category(self, tx_id: int, user_id: int, category: str | None) -> None:
        await self.conn.execute(
            "UPDATE transactions SET category = ? WHERE id = ? AND user_id = ?",
            (category, tx_id, user_id),
        )
        await self.conn.commit()

    async def update_transaction_date(self, tx_id: int, user_id: int, created_at: str) -> None:
        await self.conn.execute(
            "UPDATE transactions SET created_at = ? WHERE id = ? AND user_id = ?",
            (created_at, tx_id, user_id),
        )
        await self.conn.commit()

    async def user_categories(self, user_id: int, kind: str, active_only: bool = True) -> list[aiosqlite.Row]:
        if active_only:
            return await self.fetchall(
                "SELECT * FROM user_categories WHERE user_id = ? AND kind = ? AND is_active = 1 ORDER BY sort_order ASC, id ASC",
                (user_id, kind),
            )
        return await self.fetchall(
            "SELECT * FROM user_categories WHERE user_id = ? AND kind = ? ORDER BY is_active DESC, sort_order ASC, id ASC",
            (user_id, kind),
        )

    async def add_user_category(self, user_id: int, kind: str, name: str) -> None:
        row = await self.fetchone(
            "SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM user_categories WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        )
        sort_order = 1 if row is None else int(row["max_order"]) + 1
        await self.conn.execute(
            "INSERT INTO user_categories(user_id, kind, name, sort_order, is_active, created_at) VALUES(?, ?, ?, ?, 1, ?)",
            (user_id, kind, name, sort_order, utc_now_iso()),
        )
        await self.conn.commit()

    async def get_user_category(self, category_id: int, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM user_categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        )

    async def rename_user_category(self, category_id: int, user_id: int, name: str) -> None:
        await self.conn.execute(
            "UPDATE user_categories SET name = ? WHERE id = ? AND user_id = ?",
            (name, category_id, user_id),
        )
        await self.conn.commit()

    async def deactivate_user_category(self, category_id: int, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE user_categories SET is_active = 0 WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        )
        await self.conn.commit()

    async def move_user_category(self, category_id: int, user_id: int, direction: int) -> None:
        current = await self.get_user_category(category_id, user_id)
        if current is None:
            return
        comparator = "<" if direction < 0 else ">"
        ordering = "DESC" if direction < 0 else "ASC"
        other = await self.fetchone(
            f"""
            SELECT * FROM user_categories
            WHERE user_id = ? AND kind = ? AND is_active = 1 AND sort_order {comparator} ?
            ORDER BY sort_order {ordering}, id {ordering}
            LIMIT 1
            """,
            (user_id, current["kind"], int(current["sort_order"])),
        )
        if other is None:
            return
        await self.conn.execute("UPDATE user_categories SET sort_order = ? WHERE id = ?", (int(other["sort_order"]), int(current["id"])))
        await self.conn.execute("UPDATE user_categories SET sort_order = ? WHERE id = ?", (int(current["sort_order"]), int(other["id"])))
        await self.conn.commit()

    async def add_ticket_message(self, user_id: int, group_chat_id: int, group_message_id: int) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO ticket_messages(user_id, group_chat_id, group_message_id, created_at) VALUES(?, ?, ?, ?)",
            (user_id, group_chat_id, group_message_id, utc_now_iso()),
        )
        await self.conn.commit()

    async def ticket_user_for_message(self, group_chat_id: int, group_message_id: int) -> int | None:
        row = await self.fetchone(
            "SELECT user_id FROM ticket_messages WHERE group_chat_id = ? AND group_message_id = ?",
            (group_chat_id, group_message_id),
        )
        return None if row is None else int(row["user_id"])

    async def recent_transactions(self, user_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )

    async def all_user_transactions(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at ASC, id ASC",
            (user_id,),
        )

    async def user_transactions_since(self, user_id: int, start_iso: str, end_iso: str) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT * FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC, id ASC
            """,
            (user_id, start_iso, end_iso),
        )

    async def expense_sum_between(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = await self.fetchone(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE user_id = ? AND kind = 'expense' AND created_at >= ? AND created_at < ?
            """,
            (user_id, start_iso, end_iso),
        )
        return 0 if row is None else int(row["total"])

    async def all_transactions(self) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM transactions ORDER BY created_at ASC, id ASC"
        )

    async def transactions_between(self, user_id: int, start_iso: str, end_iso: str) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT * FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (user_id, start_iso, end_iso),
        )

    async def add_forced_channel(self, chat_ref: str, title: str, invite_link: str | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO forced_channels(chat_ref, title, invite_link, is_active, sort_order, created_at)
            VALUES(?, ?, ?, 1, COALESCE((SELECT MAX(sort_order) + 1 FROM forced_channels), 1), ?)
            ON CONFLICT(chat_ref) DO UPDATE SET
                title = excluded.title,
                invite_link = excluded.invite_link,
                is_active = 1
            """,
            (chat_ref, title, invite_link, utc_now_iso()),
        )
        await self.conn.commit()

    async def remove_forced_channel(self, channel_id: int) -> None:
        await self.conn.execute("DELETE FROM forced_channels WHERE id = ?", (channel_id,))
        await self.conn.commit()

    async def forced_channels(self, active_only: bool = True) -> list[aiosqlite.Row]:
        if active_only:
            return await self.fetchall("SELECT * FROM forced_channels WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
        return await self.fetchall("SELECT * FROM forced_channels ORDER BY sort_order ASC, id ASC")

    async def bool_setting(self, key: str, default: bool = False) -> bool:
        value = await self.get_setting(key)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def toggle_setting(self, key: str, default: bool = False) -> bool:
        current = await self.bool_setting(key, default)
        new_value = not current
        await self.set_setting(key, "1" if new_value else "0")
        return new_value

    async def add_admin_log(self, admin_id: int, action: str, target: str | None = None, details: str | None = None) -> None:
        await self.conn.execute(
            "INSERT INTO admin_logs(admin_id, action, target, details, created_at) VALUES(?, ?, ?, ?, ?)",
            (admin_id, action, target, details, utc_now_iso()),
        )
        await self.conn.commit()

    async def admin_logs(self, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ?", (limit,))

    async def add_system_error(self, source: str, message: str) -> None:
        await self.conn.execute(
            "INSERT INTO system_errors(source, message, created_at) VALUES(?, ?, ?)",
            (source, message[:500], utc_now_iso()),
        )
        await self.conn.commit()

    async def system_errors(self, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM system_errors ORDER BY id DESC LIMIT ?", (limit,))

    async def add_admin_account(self, user_id: int, role: str, created_by: int) -> None:
        await self.conn.execute(
            "INSERT INTO admin_accounts(user_id, role, created_by, created_at) VALUES(?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET role = excluded.role",
            (user_id, role, created_by, utc_now_iso()),
        )
        await self.conn.commit()

    async def remove_admin_account(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM admin_accounts WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def admin_role(self, user_id: int) -> str | None:
        row = await self.fetchone("SELECT role FROM admin_accounts WHERE user_id = ?", (user_id,))
        return None if row is None else str(row["role"])

    async def admin_accounts(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM admin_accounts ORDER BY created_at DESC")

    async def users_by_segment(self, segment: str, limit: int = 15) -> list[aiosqlite.Row]:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week = (now.replace(microsecond=0) - __import__('datetime').timedelta(days=7)).isoformat()
        if segment == "new":
            return await self.fetchall("SELECT * FROM users ORDER BY started_at DESC LIMIT ?", (limit,))
        if segment == "active":
            return await self.fetchall("SELECT * FROM users WHERE last_seen_at >= ? ORDER BY last_seen_at DESC LIMIT ?", (week, limit))
        if segment == "blocked":
            return await self.fetchall("SELECT * FROM users WHERE is_blocked = 1 ORDER BY last_seen_at DESC LIMIT ?", (limit,))
        if segment == "inactive":
            return await self.fetchall("SELECT * FROM users WHERE last_seen_at < ? AND is_blocked = 0 ORDER BY last_seen_at ASC LIMIT ?", (week, limit))
        return await self.fetchall("SELECT * FROM users ORDER BY last_seen_at DESC LIMIT ?", (limit,))

    async def get_user_by_username(self, username: str) -> aiosqlite.Row | None:
        clean = username.strip().lstrip("@").lower()
        return await self.fetchone("SELECT * FROM users WHERE lower(username) = ?", (clean,))

    async def user_ids_for_audience(self, audience: str) -> list[int]:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week = (now.replace(microsecond=0) - __import__('datetime').timedelta(days=7)).isoformat()
        if audience == "all":
            rows = await self.fetchall("SELECT user_id FROM users")
        elif audience == "active":
            rows = await self.fetchall("SELECT user_id FROM users WHERE is_blocked = 0 AND last_seen_at >= ?", (week,))
        elif audience == "new":
            rows = await self.fetchall("SELECT user_id FROM users WHERE is_blocked = 0 AND started_at >= ?", (today,))
        elif audience == "inactive":
            rows = await self.fetchall("SELECT user_id FROM users WHERE is_blocked = 0 AND last_seen_at < ?", (week,))
        else:
            rows = await self.fetchall("SELECT user_id FROM users WHERE is_blocked = 0")
        return [int(row["user_id"]) for row in rows]

    async def create_broadcast_campaign(self, admin_id: int, audience: str, total: int) -> int:
        cursor = await self.conn.execute(
            "INSERT INTO broadcast_campaigns(admin_id, audience, status, total, created_at) VALUES(?, ?, 'running', ?, ?)",
            (admin_id, audience, total, utc_now_iso()),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def finish_broadcast_campaign(self, campaign_id: int, sent: int, failed: int, blocked: int, status: str = "done") -> None:
        await self.conn.execute(
            "UPDATE broadcast_campaigns SET sent = ?, failed = ?, blocked = ?, status = ?, finished_at = ? WHERE id = ?",
            (sent, failed, blocked, status, utc_now_iso(), campaign_id),
        )
        await self.conn.commit()

    async def broadcast_campaigns(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM broadcast_campaigns ORDER BY id DESC LIMIT ?", (limit,))

    async def ticket_stats(self) -> dict[str, int]:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        row = await self.fetchone(
            """
            SELECT
              COUNT(*) AS tickets_total,
              COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS tickets_today,
              COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS tickets_open
            FROM ticket_messages
            """,
            (today,),
        )
        return {key: int(row[key]) for key in row.keys()} if row else {"tickets_total": 0, "tickets_today": 0, "tickets_open": 0}

    async def recent_tickets(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT t.*, u.username, u.full_name
            FROM ticket_messages t
            LEFT JOIN users u ON u.user_id = t.user_id
            ORDER BY t.id DESC LIMIT ?
            """,
            (limit,),
        )

    async def mark_ticket_answered(self, group_chat_id: int, group_message_id: int) -> None:
        await self.conn.execute(
            "UPDATE ticket_messages SET status = 'answered', replied_at = ? WHERE group_chat_id = ? AND group_message_id = ?",
            (utc_now_iso(), group_chat_id, group_message_id),
        )
        await self.conn.commit()

    async def mark_ticket_closed(self, ticket_id: int) -> None:
        await self.conn.execute("UPDATE ticket_messages SET status = 'closed', closed_at = ? WHERE id = ?", (utc_now_iso(), ticket_id))
        await self.conn.commit()

    async def toggle_channel_active(self, channel_id: int) -> None:
        await self.conn.execute("UPDATE forced_channels SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (channel_id,))
        await self.conn.commit()

    async def move_forced_channel(self, channel_id: int, direction: int) -> None:
        current = await self.fetchone("SELECT * FROM forced_channels WHERE id = ?", (channel_id,))
        if current is None:
            return
        comparator = "<" if direction < 0 else ">"
        ordering = "DESC" if direction < 0 else "ASC"
        other = await self.fetchone(
            f"SELECT * FROM forced_channels WHERE sort_order {comparator} ? ORDER BY sort_order {ordering}, id {ordering} LIMIT 1",
            (int(current["sort_order"]),),
        )
        if other is None:
            return
        await self.conn.execute("UPDATE forced_channels SET sort_order = ? WHERE id = ?", (int(other["sort_order"]), int(current["id"])))
        await self.conn.execute("UPDATE forced_channels SET sort_order = ? WHERE id = ?", (int(current["sort_order"]), int(other["id"])))
        await self.conn.commit()

    async def active_forced_channel_count(self) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS count FROM forced_channels WHERE is_active = 1")
        return 0 if row is None else int(row["count"])

    async def admin_dashboard_stats(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week = (now.replace(microsecond=0) - __import__('datetime').timedelta(days=7)).isoformat()
        tickets = await self.ticket_stats()
        row = await self.fetchone(
            """
            SELECT
              (SELECT COUNT(*) FROM users) AS users_total,
              (SELECT COUNT(*) FROM users WHERE started_at >= ?) AS users_new_today,
              (SELECT COUNT(*) FROM users WHERE started_at >= ?) AS users_new_week,
              (SELECT COUNT(*) FROM users WHERE last_seen_at >= ?) AS users_active_today,
              (SELECT COUNT(*) FROM users WHERE last_seen_at >= ?) AS users_active_week,
              (SELECT COUNT(*) FROM users WHERE is_blocked = 1) AS users_blocked,
              (SELECT COUNT(*) FROM forced_channels) AS channels_total,
              (SELECT COUNT(*) FROM forced_channels WHERE is_active = 1) AS channels_active,
              (SELECT COUNT(*) FROM system_errors) AS errors_total
            """,
            (today, week, today, week),
        )
        data = {key: int(row[key]) for key in row.keys()} if row else {}
        data.update(tickets)
        return data

    async def stats(self) -> dict[str, int]:
        rows = await self.fetchall(
            """
            SELECT
              (SELECT COUNT(*) FROM users) AS users_total,
              (SELECT COUNT(*) FROM users WHERE is_blocked = 1) AS users_blocked,
              (SELECT COUNT(*) FROM transactions) AS tx_total,
              (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind = 'expense') AS expense_total,
              (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind = 'income') AS income_total,
              (SELECT COUNT(*) FROM forced_channels) AS channels_total
            """
        )
        row = rows[0]
        return {key: int(row[key]) for key in row.keys()}
