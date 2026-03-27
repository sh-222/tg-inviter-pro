from typing import Any
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from dishka.integrations.fastapi import FromDishka, inject

from app.core.models import TargetUser, InviteLog, InviteStatus
from app.services.csv_reader import CSVReaderService
from app.services.runner import InviterRunner
from app.web.core import templates

router = APIRouter()

@router.post("/clear-targets", response_class=HTMLResponse)
@inject
async def clear_targets(
    request: Request,
    runner: FromDishka[InviterRunner] = None,
) -> Any:
    """HTMX endpoint to clear all pending targets."""
    # Delete targets
    await TargetUser.all().delete()

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

        encodings = ["utf-8-sig", "utf-8", "utf-16", "cp1251"]
        decoded_content = None
        for enc in encodings:
            try:
                decoded_content = content.decode(enc)
                break
            except UnicodeError:
                pass

        if decoded_content is None:
            decoded_content = content.decode("utf-8", errors="replace")

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
