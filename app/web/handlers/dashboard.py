from typing import Any
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from dishka.integrations.fastapi import FromDishka, inject

from app.core.models import TelegramAccount, TargetUser, InviteLog, InviteStatus, AccountStatus
from app.services.runner import InviterRunner
from app.web.core import templates

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
@inject
async def dashboard(
    request: Request,
    runner: FromDishka[InviterRunner] = None,
) -> Any:
    """Main dashboard view."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    
    accounts = await TelegramAccount.all()
    
    total_targets = await TargetUser.all().count()
    invited_targets = await TargetUser.filter(is_invited=True).count()
    pending_targets = await TargetUser.filter(is_invited=False).count()

    success_invites = await InviteLog.filter(status=InviteStatus.SUCCESS).count()
    privacy_restricted = await InviteLog.filter(
        status=InviteStatus.PRIVACY_RESTRICTED
    ).count()
    already_participant = await InviteLog.filter(
        status=InviteStatus.ALREADY_PARTICIPANT
    ).count()
    failed_invites = await InviteLog.exclude(
        status__in=[
            InviteStatus.SUCCESS,
            InviteStatus.WAITING,
            InviteStatus.PRIVACY_RESTRICTED,
            InviteStatus.ALREADY_PARTICIPANT,
        ]
    ).count()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "app_settings": app_settings,
            "accounts": accounts,
            "runner_state": {
                "is_running": runner.is_running,
                "status": runner.status,
                "target_group": runner.target_group_username,
            },
            "stats": {
                "total": total_targets,
                "invited": invited_targets,
                "pending": pending_targets,
                "success": success_invites,
                "privacy_restricted": privacy_restricted,
                "already_participant": already_participant,
                "failed": failed_invites,
            },
        },
    )


@router.get("/stats", response_class=HTMLResponse)
@inject
async def get_stats(
    request: Request,
    runner: FromDishka[InviterRunner] = None,
) -> Any:
    """HTMX endpoint to refresh the target statistics block."""
    total_targets = await TargetUser.all().count()
    invited_targets = await TargetUser.filter(is_invited=True).count()
    pending_targets = await TargetUser.filter(is_invited=False).count()

    success_invites = await InviteLog.filter(status=InviteStatus.SUCCESS).count()
    privacy_restricted = await InviteLog.filter(
        status=InviteStatus.PRIVACY_RESTRICTED
    ).count()
    already_participant = await InviteLog.filter(
        status=InviteStatus.ALREADY_PARTICIPANT
    ).count()
    failed_invites = await InviteLog.exclude(
        status__in=[
            InviteStatus.SUCCESS,
            InviteStatus.WAITING,
            InviteStatus.PRIVACY_RESTRICTED,
            InviteStatus.ALREADY_PARTICIPANT,
        ]
    ).count()

    return templates.TemplateResponse(
        request=request,
        name="partials/target_stats.html",
        context={
            "request": request,
            "runner_state": {
                "is_running": runner.is_running,
                "status": runner.status,
                "target_group": runner.target_group_username,
            },
            "stats": {
                "total": total_targets,
                "invited": invited_targets,
                "pending": pending_targets,
                "success": success_invites,
                "privacy_restricted": privacy_restricted,
                "already_participant": already_participant,
                "failed": failed_invites,
            },
        },
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


@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request) -> Any:
    """HTMX endpoint to retrieve settings block."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    return templates.TemplateResponse(
        request=request, name="partials/settings_form.html", context={"request": request, "app_settings": app_settings}
    )


@router.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    min_delay_seconds: int = Form(...),
    max_delay_seconds: int = Form(...),
    daily_invite_limit: int = Form(...),
) -> Any:
    """HTMX endpoint to update application settings."""
    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    
    app_settings.min_delay_seconds = min_delay_seconds
    app_settings.max_delay_seconds = max_delay_seconds
    app_settings.daily_invite_limit = daily_invite_limit
    await app_settings.save()
    
    return "<div class='text-green-400 text-sm mt-2'>Settings updated successfully!</div>"


@router.post("/accounts/reset-counters", response_class=HTMLResponse)
async def reset_invite_counters(request: Request) -> Any:
    """HTMX endpoint to manually reset all daily invite counters."""
    await TelegramAccount.all().update(invites_today=0)

    limit_accounts = await TelegramAccount.filter(
        status=AccountStatus.LIMIT_REACHED
    )
    for acc in limit_accounts:
        acc.status = AccountStatus.ACTIVE
        await acc.save()

    from app.core.models import AppSettings
    app_settings, _ = await AppSettings.get_or_create(id=1)
    accounts = await TelegramAccount.all()

    return templates.TemplateResponse(
        request=request,
        name="partials/account_rows.html",
        context={
            "request": request,
            "accounts": accounts,
            "app_settings": app_settings,
        },
    )
