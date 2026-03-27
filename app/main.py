from contextlib import asynccontextmanager
from typing import AsyncGenerator
import asyncio
import os
import logging
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from dishka.integrations.fastapi import setup_dishka
from dishka import make_async_container
from tortoise.contrib.fastapi import register_tortoise

from app.core.config import settings
from app.ioc import AppProvider
from app.web.router import router
from app.core.setup import migrate_sqlite_db

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _daily_reset_task() -> None:
    """Background task that resets invites_today at midnight UTC every day."""
    while True:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seconds_until_midnight = (tomorrow - now).total_seconds()
        logger.info(
            f"Daily reset scheduled in {seconds_until_midnight:.0f}s "
            f"(at {tomorrow.isoformat()})"
        )
        await asyncio.sleep(seconds_until_midnight)

        try:
            from app.core.models import TelegramAccount, AccountStatus

            updated = await TelegramAccount.all().update(invites_today=0)
            logger.info(f"Daily reset: cleared invites_today for {updated} accounts.")

            limit_accounts = await TelegramAccount.filter(
                status=AccountStatus.LIMIT_REACHED
            )
            for acc in limit_accounts:
                acc.status = AccountStatus.ACTIVE
                await acc.save()
            if limit_accounts:
                logger.info(
                    f"Daily reset: reactivated {len(limit_accounts)} "
                    f"LIMIT_REACHED accounts."
                )
        except Exception as e:
            logger.exception(f"Daily reset failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    os.makedirs(settings.data_dir, exist_ok=True)
    migrate_sqlite_db()

    reset_task = asyncio.create_task(_daily_reset_task())
    logger.info("Application started up (daily reset task scheduled)")

    yield

    reset_task.cancel()
    try:
        await reset_task
    except asyncio.CancelledError:
        pass
    if hasattr(app.state, "dishka_container"):
        await app.state.dishka_container.close()
    logger.info("Application shutdown completed")


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
