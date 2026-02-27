from contextlib import asynccontextmanager
from typing import AsyncGenerator
import os
import logging
import sqlite3

from fastapi import FastAPI
from dishka.integrations.fastapi import setup_dishka
from dishka import make_async_container
from tortoise.contrib.fastapi import register_tortoise

from app.core.config import settings
from app.ioc import AppProvider
from app.web.routes import router

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _migrate_db() -> None:
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    os.makedirs(settings.data_dir, exist_ok=True)
    _migrate_db()
    yield
    if hasattr(app.state, "dishka_container"):
        await app.state.dishka_container.close()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_title, debug=settings.debug, lifespan=lifespan)
    app.include_router(router)

    container = make_async_container(AppProvider())
    setup_dishka(container, app)

    register_tortoise(
        app,
        db_url=settings.db_url,
        modules={"models": ["app.core.models"]},
        generate_schemas=True,
        add_exception_handlers=True,
    )

    return app


app = create_app()
