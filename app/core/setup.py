import os
import sqlite3
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def migrate_sqlite_db() -> None:
    """Add any missing columns to the telegram_accounts table.

    Tortoise's generate_schemas only creates tables that don't exist,
    it never alters existing ones. This function fills that gap for SQLite.
    """
    db_path = os.path.join(settings.data_dir, "db.sqlite3")
    if not os.path.exists(db_path):
        return

    expected_columns: dict[str, str] = {
        "password": "VARCHAR(255)",
        "device_model": "VARCHAR(255)",
        "system_version": "VARCHAR(255)",
        "app_version": "VARCHAR(255)",
        "proxy": "VARCHAR(255)",
        "invites_today": "INT NOT NULL DEFAULT 0",
        "joined_chats": "TEXT NOT NULL DEFAULT '{}'",
        "frozen_until": "TIMESTAMP NULL",
    }

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("PRAGMA table_info(telegram_accounts)")
        existing = {row[1] for row in cursor.fetchall()}

        for col_name, col_type in expected_columns.items():
            if col_name not in existing:
                stmt = f"ALTER TABLE telegram_accounts ADD COLUMN {col_name} {col_type}"
                conn.execute(stmt)
                logger.info(
                    "Migration: added column '%s' to telegram_accounts", col_name
                )
        conn.commit()
    finally:
        conn.close()

