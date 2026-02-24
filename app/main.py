from contextlib import asynccontextmanager
from typing import AsyncGenerator
import os
import logging

from fastapi import FastAPI
from dishka.integrations.fastapi import setup_dishka
from dishka import make_async_container
from tortoise.contrib.fastapi import register_tortoise

from app.core.config import settings
from app.ioc import AppProvider
from app.web.routes import router

# Configure application-wide logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    os.makedirs(settings.data_dir, exist_ok=True)
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
