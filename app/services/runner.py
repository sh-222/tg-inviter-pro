import asyncio
import logging
from typing import Optional

from datetime import datetime, timezone
from app.core.models import TelegramAccount, TargetUser, AccountStatus, InviteStatus
from app.services.inviter import InviterService

logger = logging.getLogger(__name__)


class InviterRunner:
    """Manages the lifecycle of the background inviting loop."""

    def __init__(self, inviter_service: InviterService):
        self.inviter_service = inviter_service
        self._current_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._target_group_username: Optional[str] = None

    async def _run_loop(self, target_group_username: str) -> None:
        logger.info(f"Starting inviter loop for group {target_group_username}")
        account_index = 0
        try:
            while self._is_running:
                uninvited_users = await TargetUser.filter(is_invited=False).limit(20)
                if not uninvited_users:
                    logger.info("Inviter loop finished: No more uninvited users.")
                    break

                now = datetime.now(timezone.utc)
                frozen_accounts = await TelegramAccount.filter(
                    status=AccountStatus.FLOOD_WAIT
                )
                for f_acc in frozen_accounts:
                    if f_acc.frozen_until and f_acc.frozen_until <= now:
                        f_acc.status = AccountStatus.ACTIVE
                        f_acc.frozen_until = None
                        await f_acc.save()
                        logger.info(f"Account {f_acc.id} unfrozen.")

                active_accounts = await TelegramAccount.filter(
                    status=AccountStatus.ACTIVE
                )
                if not active_accounts:
                    logger.warning("Inviter loop stopped: No active accounts.")
                    break

                for user in uninvited_users:
                    if not self._is_running:
                        break

                    active_accounts = await TelegramAccount.filter(
                        status=AccountStatus.ACTIVE
                    )
                    if not active_accounts:
                        logger.warning(
                            "All accounts are inactive, restricted, or in flood wait. Pausing for 60s..."
                        )
                        await asyncio.sleep(60)
                        break  # Break inner loop to re-evaluate frozen accounts in outer loop

                    account_index = account_index % len(active_accounts)
                    account = active_accounts[account_index]
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
                        from app.core.models import AppSettings

                        app_settings, _ = await AppSettings.get_or_create(id=1)
                        global_delay = random.uniform(
                            app_settings.min_delay_seconds,
                            app_settings.max_delay_seconds
                        )
                        logger.info(
                            f"Invite successful. Waiting {global_delay:.0f}s before next invite globally."
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
            logger.info("Inviter loop stopped.")

    def start(self, target_group_username: str) -> bool:
        if self._is_running or self._current_task:
            return False

        self._is_running = True
        self._target_group_username = target_group_username
        self._current_task = asyncio.create_task(self._run_loop(target_group_username))
        return True

    def stop(self) -> bool:
        if not self._is_running or not self._current_task:
            return False

        self._is_running = False
        self._current_task.cancel()
        self._target_group_username = None
        return True

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def target_group_username(self) -> Optional[str]:
        return self._target_group_username
