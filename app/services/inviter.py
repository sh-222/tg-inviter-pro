import time
import random
import asyncio
import logging
from typing import Callable
from datetime import datetime, timedelta, timezone

from pyrogram import Client
from pyrogram.raw.functions.channels import InviteToChannel
from pyrogram.raw.functions.account import UpdateStatus
from pyrogram.errors import (
    UserPrivacyRestricted,
    PeerFlood,
    FloodWait,
    UserAlreadyParticipant,
    RPCError,
    UserDeactivated,
)
from tortoise.exceptions import IntegrityError

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
    ) -> InviteStatus | str:
        """
        Check if account is in the target chat and join if needed.
        Returns InviteStatus.WAITING if the account must wait, str (resolved chat target) if ready.
        """
        if not isinstance(account.joined_chats, dict):
            account.joined_chats = {}

        current_time = time.time()

        target = target_group_username.strip()
        if target.startswith("https://t.me/"):
            target = target.replace("https://t.me/", "")
        elif target.startswith("https://tg.me/"):
            target = target.replace("https://tg.me/", "")
        if target.startswith("@"):
            target = target[1:]

        join_time = account.joined_chats.get(target)
        join_delay = getattr(settings, "join_delay_seconds", 7200)

        if not join_time:
            try:
                await client.get_chat_member(target, "me")
                # If this succeeds, we are already a member! Skip delay.
                logger.info(f"Account {account.id} already a member of {target}")
                account.joined_chats[target] = current_time - join_delay - 60
                await account.save()
                return target
            except Exception:
                logger.info(f"Account {account.id} not in chat, joining {target}")
                try:
                    await client.join_chat(target)
                except UserAlreadyParticipant:
                    logger.info(f"Account {account.id} was already in {target}")
                    account.joined_chats[target] = current_time - join_delay - 60
                    await account.save()
                    return target
                except Exception as e:
                    logger.error(f"Account {account.id} failed to join {target}: {e}")
                    raise

            account.joined_chats[target] = current_time
            await account.save()

            logger.info(
                f"Account {account.id} joined {target}, "
                f"waiting {join_delay}s before inviting"
            )
            return InviteStatus.WAITING

        elapsed = current_time - join_time
        if elapsed < join_delay:
            remaining = join_delay - elapsed
            logger.info(
                f"Account {account.id} waiting {remaining:.0f}s "
                f"before inviting to {target}"
            )
            return InviteStatus.WAITING

        return target

    async def add_chat_members(
        self,
        account: TelegramAccount,
        target_user: TargetUser,
        target_group_username: str,
    ) -> InviteStatus:
        """Add a target user to the chat using the specified account."""
        if not target_user.username:
            logger.warning(f"Target user {target_user.id} has no username. Skipping.")
            target_user.is_invited = True
            await target_user.save()
            await InviteLog.create(
                account=account,
                target_user=target_user,
                target_group_id=target_group_username,
                status=InviteStatus.ERROR,
                error_message="No username provided",
            )
            return InviteStatus.ERROR

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
        wait_time = base_delay + jitter
        logger.info(
            f"Account {account.id} generates a random delay of {wait_time:.0f}s before connecting..."
        )
        await asyncio.sleep(wait_time)

        status = InviteStatus.ERROR
        error_msg = None
        client = None

        try:
            client = self.client_factory(account)
            # Add timeout to prevent hanging on bad proxy or interactive login prompts
            await asyncio.wait_for(client.start(), timeout=20)

            try:
                await client.invoke(UpdateStatus(offline=False))
                logger.info(f"Account {account.id} set status to 'Online' via raw API")
                await asyncio.sleep(random.uniform(2, 5))
            except Exception as e:
                logger.warning(
                    f"Failed to set status online for account {account.id}: {e}"
                )

            membership = await self._ensure_chat_membership(
                client, account, target_group_username
            )
            if membership == InviteStatus.WAITING:
                return membership
            resolved_target = (
                membership if isinstance(membership, str) else target_group_username
            )

            user_ref = target_user.username.strip("@")

            try:
                logger.info(f"Simulating profile view for {user_ref} via {account.id}")
                await client.get_users(user_ref)
                view_delay = random.uniform(3, 8)
                logger.info(f"Waiting {view_delay:.1f}s after viewing profile")
                await asyncio.sleep(view_delay)
            except Exception as e:
                logger.warning(f"Failed to view profile for {user_ref}: {e}")

            if target_user.tg_id or target_user.username:
                try:
                    contact_id = target_user.tg_id or target_user.username
                    logger.info(f"Adding contact {contact_id} via {account.id}")
                    await client.add_contact(
                        user_id=contact_id,
                        first_name=target_user.username or "User",
                    )
                    contact_delay = random.uniform(5, 15)
                    logger.info(f"Waiting {contact_delay:.1f}s after adding contact")
                    await asyncio.sleep(contact_delay)
                except Exception as e:
                    logger.warning(
                        f"Failed to add contact {target_user.tg_id or target_user.username}: {e}"
                    )

            channel_peer = await client.resolve_peer(resolved_target)
            user_peer = await client.resolve_peer(user_ref)

            await client.invoke(
                InviteToChannel(channel=channel_peer, users=[user_peer])
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
            target_user.is_invited = True
            await target_user.save()
        except UserAlreadyParticipant as e:
            status = InviteStatus.ALREADY_PARTICIPANT
            error_msg = str(e)
            logger.info(f"Already in group: {target_user.username}")
            target_user.is_invited = True
            await target_user.save()
        except UserDeactivated:
            error_msg = "UserDeactivated"
            account.status = AccountStatus.INACTIVE
            await account.save()
            logger.error(f"UserDeactivated: {account.id}")
            # If the invoking account is deactivated, do NOT mark target_user as invited
            # because the invite didn't even happen, the account just died.
        except FloodWait as e:
            error_msg = f"FloodWait: {e.value}s"
            account.status = AccountStatus.FLOOD_WAIT
            await account.save()
            logger.warning(f"FloodWait on {account.id} for {e.value}s")
        except PeerFlood:
            error_msg = "PeerFlood"
            account.status = AccountStatus.FLOOD_WAIT
            account.frozen_until = datetime.now(timezone.utc) + timedelta(hours=24)
            await account.save()
            logger.error(f"PeerFlood on {account.id}, frozen for 24h")
        except RPCError as e:
            error_msg = f"RPC Error: {e}"
            logger.error(f"RPC Error on {account.id}: {e}")
            err_str = str(e).upper()
            if any(
                term in err_str
                for term in [
                    "USER",
                    "PEER",
                    "CONTACT",
                    "BOT",
                    "PRIVACY",
                    "BANNED_RIGHTS",
                ]
            ):
                logger.info(
                    f"Marking target {target_user.username} as processed due to permanent RPC error."
                )
                target_user.is_invited = True
                await target_user.save()
        except EOFError:
            error_msg = "Session invalid / interactive login prompt"
            logger.error(
                f"Account {account.id} session invalid (EOFError). Marking INACTIVE."
            )
            try:
                account.status = AccountStatus.INACTIVE
                await account.save()
            except Exception:
                pass
        except (ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
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

        try:
            await InviteLog.create(
                account=account,
                target_user=target_user,
                target_group_id=target_group_username,
                status=status,
                error_message=error_msg,
            )
        except IntegrityError:
            logger.warning(
                f"Skipping log creation for {account.id}, account might have been deleted."
            )
        except Exception as e:
            logger.error(f"Failed to create InviteLog: {e}")

        return status
