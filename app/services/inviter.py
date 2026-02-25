import time
import random
import asyncio
import logging
from typing import Callable

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

    async def _ensure_chat_membership(
        self,
        client: Client,
        account: TelegramAccount,
        target_group_username: str,
    ) -> InviteStatus | None:
        """
        Check if account is in the target chat and join if needed.
        Returns InviteStatus.WAITING if the account must wait, None if ready.
        """
        if not isinstance(account.joined_chats, dict):
            account.joined_chats = {}

        current_time = time.time()
        join_time = account.joined_chats.get(target_group_username)
        join_delay = getattr(settings, "join_delay_seconds", 7200)

        if not join_time:
            try:
                await client.get_chat(target_group_username)
            except Exception:
                logger.info(
                    f"Account {account.id} not in chat, joining {target_group_username}"
                )
                await client.join_chat(target_group_username)

            account.joined_chats[target_group_username] = current_time
            await account.save()

            logger.info(
                f"Account {account.id} joined {target_group_username}, "
                f"waiting {join_delay}s before inviting"
            )
            return InviteStatus.WAITING

        elapsed = current_time - join_time
        if elapsed < join_delay:
            remaining = join_delay - elapsed
            logger.info(
                f"Account {account.id} waiting {remaining:.0f}s "
                f"before inviting to {target_group_username}"
            )
            return InviteStatus.WAITING

        return None

    async def add_chat_members(
        self,
        account: TelegramAccount,
        target_user: TargetUser,
        target_group_username: str,
    ) -> InviteStatus:
        """Add a target user to the chat using the specified account."""
        if (
            account.status != AccountStatus.ACTIVE
            or account.invites_today >= settings.daily_invite_limit
        ):
            logger.warning(f"Account {account.id} is not active or reached limit.")
            return InviteStatus.ERROR

        base_delay = random.uniform(
            settings.min_delay_seconds, settings.max_delay_seconds
        )
        jitter = base_delay * random.uniform(-0.1, 0.1)
        await asyncio.sleep(base_delay + jitter)

        status = InviteStatus.ERROR
        error_msg = None
        client = None

        try:
            client = self.client_factory(account)
            await client.start()

            membership = await self._ensure_chat_membership(
                client, account, target_group_username
            )
            if membership is not None:
                return membership

            user_ref = target_user.username or target_user.tg_id
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
            error_msg = "PeerFlood"
            account.status = AccountStatus.FLOOD_WAIT
            await account.save()
            logger.error(f"PeerFlood on {account.id}")
        except RPCError as e:
            error_msg = f"RPC Error: {e}"
            logger.error(f"RPC Error on {account.id}: {e}")
        except (ConnectionError, TimeoutError) as e:
            error_msg = f"Connection/Timeout Error: {e}"
            logger.error(f"Network error on {account.id}: {e}")
        except Exception as e:
            error_msg = f"Unexpected Error: {e}"
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

        return status
