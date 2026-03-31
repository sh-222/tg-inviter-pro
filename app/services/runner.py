import asyncio
import random
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
        logger.info(f"Starting batch-based inviter loop for group {target_group_username}")
        try:
            while self._is_running:
                app_settings, _ = await AppSettings.get_or_create(id=1)
                now = datetime.now(timezone.utc)

                # 1. Maintenance: Unfreeze and Reactivate accounts
                await self._maintenance(app_settings, now)

                # 2. Identify available accounts
                active_accounts = await TelegramAccount.filter(status=AccountStatus.ACTIVE)
                available_accounts = [
                    acc for acc in active_accounts
                    if acc.invites_today < app_settings.daily_invite_limit
                ]

                if not available_accounts:
                    all_active = await TelegramAccount.filter(
                        status__in=[AccountStatus.ACTIVE, AccountStatus.LIMIT_REACHED]
                    )
                    if all_active:
                        self._status = "paused_limit"
                        logger.info("All accounts at limit. Waiting 1 hour...")
                        await asyncio.sleep(3600)
                        continue
                    else:
                        logger.warning("No active accounts. Stopping.")
                        break

                self._status = "running"
                processed_any = False

                # 3. Process each account in a batch
                for account in available_accounts:
                    if not self._is_running:
                        break

                    # Fetch a small batch of users for this account session
                    batch_size = random.randint(3, 6)
                    users = await TargetUser.filter(is_invited=False).limit(batch_size)
                    
                    if not users:
                        logger.info("No more uninvited users.")
                        self._is_running = False
                        break

                    logger.info(f"Account {account.id} starting batch of {len(users)} invites.")
                    client = None
                    try:
                        client = await self.inviter_service.start_client(account)
                        channel_peer = await self.inviter_service.get_channel_peer(
                            client, account, target_group_username
                        )
                        
                        if channel_peer == InviteStatus.WAITING:
                            logger.info(f"Account {account.id} must wait for membership delay.")
                            continue

                        for user in users:
                            if not self._is_running:
                                break
                            
                            # Check local limit for this account again
                            if account.invites_today >= app_settings.daily_invite_limit:
                                account.status = AccountStatus.LIMIT_REACHED
                                await account.save()
                                break

                            logger.info(f"Inviting {user.username or user.tg_id} via {account.id}")
                            status = await self.inviter_service.add_single_user(
                                client, account, user, channel_peer, target_group_username
                            )
                            processed_any = True

                            if status == InviteStatus.SUCCESS:
                                # Delay between invites in the same session
                                delay = random.uniform(
                                    app_settings.min_delay_seconds, 
                                    app_settings.max_delay_seconds
                                )
                                logger.info(f"Invite success. Waiting {delay:.0f}s within session.")
                                await asyncio.sleep(delay)
                            elif status == InviteStatus.ERROR:
                                # Potential FloodWait or other error
                                # Refresh account from DB to check status
                                await account.refresh_from_db()
                                if account.status == AccountStatus.FLOOD_WAIT:
                                    logger.warning(f"Account {account.id} hit FloodWait. Aborting batch.")
                                    break
                                # Small pause for non-fatal errors
                                await asyncio.sleep(10)

                        # Simulating some natural app use before closing
                        post_batch_idle = random.uniform(15, 40)
                        logger.info(f"Batch finished for {account.id}. Idling for {post_batch_idle:.1f}s before closing.")
                        await asyncio.sleep(post_batch_idle)

                    except Exception as e:
                        logger.error(f"Error in batch for account {account.id}: {e}")
                    finally:
                        if client and client.is_connected:
                            await client.stop()

                if not processed_any and self._is_running:
                    logger.info("No users processed in this cycle. Waiting 60s...")
                    await asyncio.sleep(60)

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

    async def _maintenance(self, app_settings: AppSettings, now: datetime):
        """Unfreeze and reactivate accounts."""
        frozen_accounts = await TelegramAccount.filter(status=AccountStatus.FLOOD_WAIT)
        for f_acc in frozen_accounts:
            if f_acc.frozen_until and f_acc.frozen_until <= now:
                f_acc.status = AccountStatus.ACTIVE
                f_acc.frozen_until = None
                await f_acc.save()
                logger.info(f"Account {f_acc.id} unfrozen.")
            elif not f_acc.frozen_until:
                f_acc.frozen_until = now + timedelta(hours=24)
                await f_acc.save()

        limit_accounts = await TelegramAccount.filter(status=AccountStatus.LIMIT_REACHED)
        for l_acc in limit_accounts:
            if l_acc.invites_today < app_settings.daily_invite_limit:
                l_acc.status = AccountStatus.ACTIVE
                await l_acc.save()
                logger.info(f"Account {l_acc.id} reactivated (limit reset).")

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
