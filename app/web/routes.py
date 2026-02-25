import json
import os
import secrets
import shutil
from typing import Any

from fastapi import (
    APIRouter,
    Request,
    UploadFile,
    File,
    Form,
    Depends,
    HTTPException,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dishka.integrations.fastapi import FromDishka, inject

from app.core.config import settings
from app.core.models import TelegramAccount, TargetUser, AccountStatus
from app.services.csv_reader import CSVReaderService
from app.services.runner import InviterRunner

security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_username = secrets.compare_digest(
        credentials.username, settings.admin_username
    )
    correct_password = secrets.compare_digest(
        credentials.password, settings.admin_password
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


router = APIRouter(dependencies=[Depends(verify_credentials)])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Any:
    """Main dashboard view."""
    accounts = await TelegramAccount.all()

    total_targets = await TargetUser.all().count()
    invited_targets = await TargetUser.filter(is_invited=True).count()
    pending_targets = await TargetUser.filter(is_invited=False).count()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "accounts": accounts,
            "stats": {
                "total": total_targets,
                "invited": invited_targets,
                "pending": pending_targets,
            },
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
async def get_accounts(request: Request) -> Any:
    """HTMX endpoint to refresh the account table rows."""
    accounts = await TelegramAccount.all()
    return templates.TemplateResponse(
        "partials/account_rows.html", {"request": request, "accounts": accounts}
    )


@router.post("/accounts/clean", response_class=HTMLResponse)
async def cleanup_accounts(request: Request) -> Any:
    """HTMX endpoint to delete inactive/banned accounts and refresh rows."""
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
        "partials/account_rows.html", {"request": request, "accounts": accounts}
    )


@router.delete("/accounts/{account_id}", response_class=HTMLResponse)
async def delete_account(request: Request, account_id: int) -> Any:
    """HTMX endpoint to manually delete a single account."""
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
        "partials/account_rows.html", {"request": request, "accounts": accounts}
    )


@router.get("/stats", response_class=HTMLResponse)
async def get_stats(request: Request) -> Any:
    """HTMX endpoint to refresh the target statistics block."""
    total_targets = await TargetUser.all().count()
    invited_targets = await TargetUser.filter(is_invited=True).count()
    pending_targets = await TargetUser.filter(is_invited=False).count()

    return templates.TemplateResponse(
        "partials/target_stats.html",
        {
            "request": request,
            "stats": {
                "total": total_targets,
                "invited": invited_targets,
                "pending": pending_targets,
            },
        },
    )


@router.post("/upload-targets", response_class=HTMLResponse)
@inject
async def upload_targets(
    request: Request,
    file: UploadFile = File(...),
    csv_reader: FromDishka[CSVReaderService] = None,
) -> Any:
    """Endpoint to handle CSV file upload."""
    if not file.filename.endswith(".csv"):
        return "<div class='text-red-400 text-sm mt-2'>Error: Only CSV files are allowed.</div>"

    try:
        content = await file.read()
        decoded_content = content.decode("utf-8")

        targets_data = csv_reader.read_targets(decoded_content)

        added_count = 0
        for data in targets_data:
            _, created = await TargetUser.get_or_create(
                tg_id=data["tg_id"],
                username=data["username"],
                defaults={"full_name": data["full_name"]},
            )
            if created:
                added_count += 1

        return HTMLResponse(
            content=f"<div class='text-green-400 text-sm mt-2'>Success! {added_count} new targets added.</div>",
            headers={"HX-Trigger": "targetStatsUpdated"},
        )
    except Exception as e:
        return HTMLResponse(
            content=f"<div class='text-red-400 text-sm mt-2'>Error parsing CSV: {e}</div>"
        )


@router.post("/start-inviting", response_class=HTMLResponse)
@inject
async def start_inviting(
    request: Request,
    target_group: str = Form(...),
    runner: FromDishka[InviterRunner] = None,
) -> Any:
    """Endpoint to trigger the inviter process."""
    if runner.is_running:
        return "<div class='text-yellow-400 text-sm mt-2'>Inviter process is already running!</div>"

    success = runner.start(target_group_username=target_group)
    if success:
        return "<div class='text-green-400 text-sm mt-2'>Inviter process started in background!</div>"
    return "<div class='text-red-400 text-sm mt-2'>Failed to start process.</div>"


@router.post("/stop-inviting", response_class=HTMLResponse)
@inject
async def stop_inviting(
    request: Request, runner: FromDishka[InviterRunner] = None
) -> Any:
    """Endpoint to stop the inviter process."""
    if not runner.is_running:
        return "<div class='text-yellow-400 text-sm mt-2'>Process is not running.</div>"

    success = runner.stop()
    if success:
        return (
            "<div class='text-green-400 text-sm mt-2'>Stopping inviter process...</div>"
        )
    return "<div class='text-red-400 text-sm mt-2'>Failed to stop process.</div>"


@router.get("/accounts/manage", response_class=HTMLResponse)
async def manage_accounts_page(request: Request) -> Any:
    """Page to add and manage accounts."""
    return templates.TemplateResponse("accounts.html", {"request": request})


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
            session_string="file",
        )

        file_path = os.path.join(settings.data_dir, f"session_{account.id}.session")
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(session_file.file, buffer)

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
            session_string="file",
        )

        file_path = os.path.join(settings.data_dir, f"session_{account.id}.session")
        content = await session_file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(content)

        return (
            f"<div class='text-green-400 text-sm mt-2'>"
            f"Account #{account.id} imported: {phone or 'no phone'}. "
            f"Device: {device or 'default'}"
            f"</div>"
        )
    except Exception as e:
        return (
            f"<div class='text-red-400 text-sm mt-2'>Error importing account: {e}</div>"
        )
