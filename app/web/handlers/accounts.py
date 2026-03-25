import os
import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.models import TelegramAccount, AccountStatus
from app.services.session_converter import ensure_pyrogram_session
from app.web.core import templates

router = APIRouter()

def is_valid_proxy(proxy_url: str) -> bool:
    if not proxy_url:
        return True
    try:
        parsed = urlparse(proxy_url)
        return (
            parsed.scheme in ["http", "socks4", "socks5"]
            and parsed.hostname is not None
        )
    except Exception:
        return False


@router.get("/accounts", response_class=HTMLResponse)
async def get_accounts(request: Request) -> Any:
    """HTMX endpoint to refresh the account table rows."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    accounts = await TelegramAccount.all()
    return templates.TemplateResponse(
        request=request, name="partials/account_rows.html", context={"request": request, "accounts": accounts, "app_settings": app_settings}
    )


@router.post("/accounts/clean", response_class=HTMLResponse)
async def cleanup_accounts(request: Request) -> Any:
    """HTMX endpoint to delete inactive/banned accounts and refresh rows."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    dead_accounts = await TelegramAccount.filter(
        status__in=[AccountStatus.BANNED, AccountStatus.FLOOD_WAIT]
    )

    for acc in dead_accounts:
        session_path = os.path.join(settings.data_dir, f"session_{acc.id}.session")
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
            except OSError:
                pass
        await acc.delete()

    accounts = await TelegramAccount.all()
    return templates.TemplateResponse(
        request=request, name="partials/account_rows.html", context={"request": request, "accounts": accounts, "app_settings": app_settings}
    )


@router.delete("/accounts/{account_id}", response_class=HTMLResponse)
async def delete_account(request: Request, account_id: int) -> Any:
    """HTMX endpoint to manually delete a single account."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    account = await TelegramAccount.get_or_none(id=account_id)
    if account:
        session_path = os.path.join(settings.data_dir, f"session_{account.id}.session")
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
            except OSError:
                pass
        await account.delete()

    accounts = await TelegramAccount.all()
    return templates.TemplateResponse(
        request=request, name="partials/account_rows.html", context={"request": request, "accounts": accounts, "app_settings": app_settings}
    )


@router.get("/accounts/manage", response_class=HTMLResponse)
async def manage_accounts_page(request: Request) -> Any:
    """Page to add and manage accounts."""
    return templates.TemplateResponse(request=request, name="accounts.html", context={"request": request})


@router.post("/accounts/new", response_class=HTMLResponse)
async def add_new_account(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone_number: str = Form(""),
    password: str = Form(""),
    proxy: str = Form(""),
    device_model: str = Form(""),
    system_version: str = Form(""),
    app_version: str = Form(""),
    session_file: UploadFile = File(...),
) -> Any:
    """Endpoint to create a new account with a session file upload."""
    if not session_file.filename.endswith(".session"):
        return "<div class='text-red-400 text-sm mt-2'>Error: Please upload a valid .session file.</div>"
    if proxy and not is_valid_proxy(proxy):
        return "<div class='text-red-400 text-sm mt-2'>Error: Invalid proxy format. Use http://, socks4://, or socks5://</div>"

    try:
        account = await TelegramAccount.create(
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number or None,
            password=password or None,
            proxy=proxy or None,
            device_model=device_model or None,
            system_version=system_version or None,
            app_version=app_version or None,
            session_string="pending",
        )

        session_name = f"session_{account.id}"
        file_path = os.path.join(settings.data_dir, f"{session_name}.session")
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(session_file.file, buffer)

        account.session_string = f"file:{session_name}.session"
        await account.save()

        return f"<div class='text-green-400 text-sm mt-2'>Account #{account.id} saved securely.</div>"
    except Exception as e:
        return (
            f"<div class='text-red-400 text-sm mt-2'>Error creating account: {e}</div>"
        )


@router.post("/accounts/import-json", response_class=HTMLResponse)
async def import_account_from_json(
    request: Request,
    proxy: str = Form(""),
    session_file: UploadFile = File(...),
    json_file: UploadFile = File(...),
) -> Any:
    """Endpoint to import an account from a .session + .json file pair."""
    if not session_file.filename.endswith(".session"):
        return "<div class='text-red-400 text-sm mt-2'>Error: Please upload a valid .session file.</div>"
    if not json_file.filename.endswith(".json"):
        return "<div class='text-red-400 text-sm mt-2'>Error: Please upload a valid .json file.</div>"
    if proxy and not is_valid_proxy(proxy):
        return "<div class='text-red-400 text-sm mt-2'>Error: Invalid proxy format. Use http://, socks4://, or socks5://</div>"

    try:
        raw = await json_file.read()
        data = json.loads(raw.decode("utf-8"))

        api_id = data.get("app_id")
        api_hash = data.get("app_hash")
        phone = data.get("phone")
        two_fa = data.get("twoFA")
        device = data.get("device")
        sdk = data.get("sdk")
        app_ver = data.get("app_version")

        if not api_id or not api_hash:
            return "<div class='text-red-400 text-sm mt-2'>Error: JSON must contain app_id and app_hash.</div>"

        account = await TelegramAccount.create(
            api_id=int(api_id),
            api_hash=api_hash,
            phone_number=str(phone) if phone else None,
            password=two_fa or None,
            proxy=proxy or None,
            device_model=device or None,
            system_version=sdk or None,
            app_version=app_ver or None,
            session_string="pending",
        )

        session_name = f"session_{account.id}"
        file_path = os.path.join(settings.data_dir, f"{session_name}.session")
        content = await session_file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(content)

        converted = ensure_pyrogram_session(Path(file_path))
        fmt = "Telethon → Kurigram" if converted else "Kurigram"

        account.session_string = f"file:{session_name}.session"
        await account.save()

        return (
            f"<div class='text-green-400 text-sm mt-2'>"
            f"Account #{account.id} imported: {phone or 'no phone'}. "
            f"Device: {device or 'default'}. Format: {fmt}"
            f"</div>"
        )
    except Exception as e:
        return (
            f"<div class='text-red-400 text-sm mt-2'>Error importing account: {e}</div>"
        )
