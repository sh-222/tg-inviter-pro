from fastapi import APIRouter, Depends
from app.web.core import verify_credentials
from app.web.handlers import dashboard, accounts, targets

router = APIRouter(dependencies=[Depends(verify_credentials)])

router.include_router(dashboard.router)
router.include_router(accounts.router)
router.include_router(targets.router)
