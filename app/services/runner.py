import asyncio
import logging
import random
from typing import Optional

from app.core.models import TelegramAccount, TargetUser, AccountStatus
from app.services.inviter import InviterService

logger = logging.getLogger(__name__)


class InviterRunner:
    """
    Manages the lifecycle of the background inviting loop.
    """

    def __init__(self, inviter_service: InviterService):
        self.inviter_service = inviter_service
        self._current_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def _run_loop(self, target_group_username: str) -> None:
        logger.info(f"Starting inviter loop for group {target_group_username}")
        try:
            while self._is_running:
                # Fetch a batch of uninvited users
                uninvited_users = await TargetUser.filter(is_invited=False).limit(20)
                if not uninvited_users:
                    logger.info("Inviter loop finished: No more uninvited users.")
                    break

                # Fetch active accounts
                active_accounts = await TelegramAccount.filter(
                    status=AccountStatus.ACTIVE
                )
                if not active_accounts:
                    logger.warning(
                        "Inviter loop stopped: No active accounts available."
                    )
                    break

                for user in uninvited_users:
                    if not self._is_running:
                        break

                    # Simple rotation: pick a random active account
                    account = random.choice(active_accounts)

                    logger.info(
                        f"Attempting to invite {user.username or user.tg_id} via account {account.id}"
                    )
                    success = await self.inviter_service.add_chat_members(
                        account=account,
                        target_user=user,
                        target_group_username=target_group_username,
                    )

                    if not success:
                        # If failed, the account might have been banned or flood waited.
                        # Re-fetch active accounts for the next user.
                        active_accounts = await TelegramAccount.filter(
                            status=AccountStatus.ACTIVE
                        )
                        if not active_accounts:
                            logger.error(
                                "Inviter loop stopped: All accounts are inactive or restricted."
                            )
                            self._is_running = False
                            break

        except asyncio.CancelledError:
            logger.info("Inviter loop was cancelled.")
        except Exception as e:
            logger.exception(f"Inviter loop crashed with error: {e}")
        finally:
            self._is_running = False
            self._current_task = None
            logger.info("Inviter loop stopped cleanly.")

    def start(self, target_group_username: str) -> bool:
        if self._is_running or self._current_task:
            return False

        self._is_running = True
        self._current_task = asyncio.create_task(self._run_loop(target_group_username))
        return True

    def stop(self) -> bool:
        if not self._is_running or not self._current_task:
            return False

        self._is_running = False
        self._current_task.cancel()
        return True

    @property
    def is_running(self) -> bool:
        return self._is_running
