import random
import asyncio
from typing import Callable
import logging
from pyrogram import Client
from pyrogram.errors import (
    UserPrivacyRestricted,
    PeerFlood,
    FloodWait,
    UserAlreadyParticipant,
    RPCError,
)

from app.core.config import settings
from app.core.models import (
    TelegramAccount,
    TargetUser,
    InviteLog,
    InviteStatus,
    AccountStatus,
)

logger = logging.getLogger(__name__)


class InviterService:
    def __init__(self, client_factory: Callable[[TelegramAccount], Client]):
        self.client_factory = client_factory

    async def add_chat_members(
        self,
        account: TelegramAccount,
        target_user: TargetUser,
        target_group_username: str,
    ) -> bool:
        """
        Add members to a chat using the specified account.
        """
        if (
            account.status != AccountStatus.ACTIVE
            or account.invites_today >= settings.daily_invite_limit
        ):
            logger.warning(f"Account {account.id} is not active or reached limit.")
            return False

        base_delay = random.uniform(
            settings.min_delay_seconds, settings.max_delay_seconds
        )
        jitter = base_delay * random.uniform(-0.1, 0.1)
        total_delay = base_delay + jitter

        logger.info(
            f"Delay {total_delay:.2f}s before invite using account {account.id}"
        )
        await asyncio.sleep(total_delay)

        status = InviteStatus.ERROR
        error_msg = None
        client = None

        try:
            client = self.client_factory(account)
            await client.start()

            user_ref = (
                target_user.username if target_user.username else target_user.tg_id
            )
            await client.add_chat_members(
                chat_id=target_group_username, user_ids=[user_ref]
            )

            status = InviteStatus.SUCCESS
            logger.info(f"Invited {user_ref} via {account.id}")

            account.invites_today += 1
            await account.save()
            target_user.is_invited = True
            await target_user.save()

        except UserPrivacyRestricted as e:
            status = InviteStatus.PRIVACY_RESTRICTED
            error_msg = str(e)
            logger.info(f"Privacy restrictions: {target_user.username}")
        except UserAlreadyParticipant as e:
            status = InviteStatus.ALREADY_PARTICIPANT
            error_msg = str(e)
            logger.info(f"Already in group: {target_user.username}")
        except FloodWait as e:
            error_msg = f"FloodWait: {e.value}s"
            account.status = AccountStatus.FLOOD_WAIT
            await account.save()
            logger.warning(f"FloodWait on {account.id} for {e.value}s")
        except PeerFlood:
            error_msg = "PeerFlood - stopping account"
            account.status = AccountStatus.FLOOD_WAIT
            await account.save()
            logger.error(f"PeerFlood on {account.id}")
        except RPCError as e:
            error_msg = f"RPC Error: {str(e)}"
            logger.error(f"RPC Error on {account.id}: {e}")
            # Consider specific RPC errors like UserBannedInChannel
        except (ConnectionError, TimeoutError) as e:
            error_msg = f"Connection/Timeout Error: {str(e)}"
            logger.error(f"Network error on {account.id}: {e}")
            # Don't ban the account, just record the failure
        except Exception as e:
            error_msg = f"Unexpected Error: {str(e)}"
            logger.exception(f"Unexpected error on {account.id}: {e}")
        finally:
            if client and client.is_connected:
                try:
                    await client.stop()
                except Exception as e:
                    logger.error(f"Error stopping client {account.id}: {e}")

        await InviteLog.create(
            account=account,
            target_user=target_user,
            target_group_id=target_group_username,
            status=status,
            error_message=error_msg,
        )

        return status == InviteStatus.SUCCESS
