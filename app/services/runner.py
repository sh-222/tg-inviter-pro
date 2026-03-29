import asyncio
import logging
from typing import Optional

from datetime import datetime, timezone, timedelta
from app.core.models import TelegramAccount, TargetUser, AccountStatus, InviteStatus, AppSettings
from app.services.inviter import InviterService

logger = logging.getLogger(__name__)


class InviterRunner:
    """Manages the lifecycle of the background inviting loop."""

    def __init__(self, inviter_service: InviterService):
        self.inviter_service = inviter_service
        self._current_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._target_group_username: Optional[str] = None
        self._status: str = "stopped"  # "stopped", "running", "paused_limit"

    async def _run_loop(self, target_group_username: str) -> None:
        logger.info(f"Starting inviter loop for group {target_group_username}")
        account_index = 0
        try:
            while self._is_running:
                uninvited_users = await TargetUser.filter(is_invited=False).limit(20)
                if not uninvited_users:
                    logger.info("Inviter loop finished: No more uninvited users.")
                    break

                app_settings, _ = await AppSettings.get_or_create(id=1)

                # Unfreeze flood_wait accounts whose freeze period has expired
                now = datetime.now(timezone.utc)
                frozen_accounts = await TelegramAccount.filter(
                    status=AccountStatus.FLOOD_WAIT
                )
                for f_acc in frozen_accounts:
                    if f_acc.frozen_until:
                        if f_acc.frozen_until <= now:
                            f_acc.status = AccountStatus.ACTIVE
                            f_acc.frozen_until = None
                            await f_acc.save()
                            logger.info(f"Account {f_acc.id} unfrozen (time expired).")
                    else:
                        # If frozen_until is missing, it's stuck. 
                        # We set it to 24h from now as a safe recovery default.
                        f_acc.frozen_until = now + timedelta(hours=24)
                        await f_acc.save()
                        logger.warning(
                            f"Account {f_acc.id} was in FLOOD_WAIT without expiration. "
                            f"Set default 24h freeze until {f_acc.frozen_until}."
                        )

                # Reactivate limit_reached accounts whose counter has been reset
                limit_accounts = await TelegramAccount.filter(
                    status=AccountStatus.LIMIT_REACHED
                )
                for l_acc in limit_accounts:
                    if l_acc.invites_today < app_settings.daily_invite_limit:
                        l_acc.status = AccountStatus.ACTIVE
                        await l_acc.save()
                        logger.info(
                            f"Account {l_acc.id} reactivated (limit reset)."
                        )

                # Filter active accounts that haven't reached the daily limit
                active_accounts = await TelegramAccount.filter(
                    status=AccountStatus.ACTIVE
                )
                available_accounts = [
                    acc for acc in active_accounts
                    if acc.invites_today < app_settings.daily_invite_limit
                ]

                if not available_accounts:
                    # Check if there are active accounts that are all at limit
                    all_active = await TelegramAccount.filter(
                        status__in=[AccountStatus.ACTIVE, AccountStatus.LIMIT_REACHED]
                    )
                    if all_active:
                        self._status = "paused_limit"
                        logger.info(
                            "Inviter loop paused: All active accounts reached "
                            "daily invite limit. Waiting 1 hour..."
                        )
                        await asyncio.sleep(3600)
                        continue
                    else:
                        logger.warning(
                            "Inviter loop stopped: No active accounts."
                        )
                        break

                self._status = "running"

                for user in uninvited_users:
                    if not self._is_running:
                        break

                    # Re-fetch available accounts for the inner loop
                    app_settings, _ = await AppSettings.get_or_create(id=1)
                    active_accounts = await TelegramAccount.filter(
                        status=AccountStatus.ACTIVE
                    )
                    available_accounts = [
                        acc for acc in active_accounts
                        if acc.invites_today < app_settings.daily_invite_limit
                    ]

                    if not available_accounts:
                        logger.warning(
                            "All accounts reached limit or inactive. "
                            "Pausing for 60s..."
                        )
                        await asyncio.sleep(60)
                        break

                    account_index = account_index % len(available_accounts)
                    account = available_accounts[account_index]
                    account_index += 1

                    logger.info(
                        f"Inviting {user.username or user.tg_id} "
                        f"via account {account.id}"
                    )
                    status = await self.inviter_service.add_chat_members(
                        account=account,
                        target_user=user,
                        target_group_username=target_group_username,
                    )

                    if status == InviteStatus.WAITING:
                        await asyncio.sleep(2)
                        continue

                    if status == InviteStatus.SUCCESS:
                        import random

                        global_delay = random.uniform(
                            app_settings.min_delay_seconds,
                            app_settings.max_delay_seconds
                        )
                        logger.info(
                            f"Invite successful. Waiting {global_delay:.0f}s "
                            f"before next invite globally."
                        )
                        await asyncio.sleep(global_delay)

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("Inviter loop was cancelled.")
        except Exception as e:
            logger.exception(f"Inviter loop crashed: {e}")
        finally:
            self._is_running = False
            self._current_task = None
            self._target_group_username = None
            self._status = "stopped"
            logger.info("Inviter loop stopped.")

    def start(self, target_group_username: str) -> bool:
        if self._is_running or self._current_task:
            return False

        self._is_running = True
        self._status = "running"
        self._target_group_username = target_group_username
        self._current_task = asyncio.create_task(self._run_loop(target_group_username))
        return True

    def stop(self) -> bool:
        if not self._is_running or not self._current_task:
            return False

        self._is_running = False
        self._current_task.cancel()
        self._target_group_username = None
        self._status = "stopped"
        return True

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def status(self) -> str:
        return self._status

    @property
    def target_group_username(self) -> Optional[str]:
        return self._target_group_username
