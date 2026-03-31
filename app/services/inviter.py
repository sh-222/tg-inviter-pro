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
    UsernameNotOccupied,
    UsernameInvalid,
    PeerIdInvalid,
)

from app.core.config import settings
from app.core.models import (
    TelegramAccount,
    TargetUser,
    InviteLog,
    InviteStatus,
    AccountStatus,
    AppSettings,
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

    async def _handle_invite_error(
        self, e: Exception, account: TelegramAccount, target_user: TargetUser
    ) -> tuple[InviteStatus, str]:
        """Classify the exception and update models accordingly."""
        if isinstance(e, UserPrivacyRestricted):
            logger.info(f"Privacy restrictions: {target_user.username}")
            target_user.is_invited = True
            await target_user.save()
            return InviteStatus.PRIVACY_RESTRICTED, str(e)
            
        elif isinstance(e, UserAlreadyParticipant):
            logger.info(f"Already in group: {target_user.username}")
            target_user.is_invited = True
            await target_user.save()
            return InviteStatus.ALREADY_PARTICIPANT, str(e)
            
        elif isinstance(e, UserDeactivated):
            account.status = AccountStatus.INACTIVE
            await account.save()
            logger.error(f"UserDeactivated: {account.id}")
            return InviteStatus.ERROR, "UserDeactivated"
            
        elif isinstance(e, FloodWait):
            account.status = AccountStatus.FLOOD_WAIT
            # Add extra safety margin (5s) to the wait time
            account.frozen_until = datetime.now(timezone.utc) + timedelta(seconds=e.value + 5)
            await account.save()
            logger.warning(f"FloodWait on {account.id} for {e.value}s. Frozen until {account.frozen_until}")
            return InviteStatus.ERROR, f"FloodWait: {e.value}s"
            
        elif isinstance(e, PeerFlood):
            account.status = AccountStatus.FLOOD_WAIT
            account.frozen_until = datetime.now(timezone.utc) + timedelta(hours=24)
            await account.save()
            logger.error(f"PeerFlood on {account.id}, frozen for 24h")
            return InviteStatus.ERROR, "PeerFlood"
            
        elif isinstance(e, RPCError):
            logger.error(f"RPC Error on {account.id}: {e}")
            err_str = str(e).upper()
            if any(term in err_str for term in ["USER", "PEER", "CONTACT", "BOT", "PRIVACY", "BANNED_RIGHTS"]):
                logger.info(f"Marking target {target_user.username} as processed due to permanent RPC error.")
                target_user.is_invited = True
                await target_user.save()
            return InviteStatus.ERROR, f"RPC Error: {e}"
            
        elif isinstance(e, EOFError):
            logger.error(f"Account {account.id} session invalid (EOFError). Marking INACTIVE.")
            try:
                account.status = AccountStatus.INACTIVE
                await account.save()
            except Exception:
                pass
            return InviteStatus.ERROR, "Session invalid / interactive login prompt"
            
        elif isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            logger.error(f"Network error on {account.id}: {e}")
            return InviteStatus.ERROR, f"Connection/Timeout Error: {e}"
            
        else:
            logger.exception(f"Unexpected error on {account.id}: {e}")
            return InviteStatus.ERROR, f"Unexpected Error: {e}"

    async def start_client(self, account: TelegramAccount) -> Client:
        """Start the client, perform warmup and set online status."""
        client = self.client_factory(account)
        await asyncio.wait_for(client.start(), timeout=20)
        
        # Simulate app opening delay
        warmup_delay = random.uniform(5, 12)
        logger.info(f"Account {account.id} warming up for {warmup_delay:.1f}s...")
        await asyncio.sleep(warmup_delay)

        try:
            await client.invoke(UpdateStatus(offline=False))
            logger.info(f"Account {account.id} set status to 'Online' via raw API")
            await asyncio.sleep(random.uniform(3, 7))
        except Exception as e:
            logger.warning(
                f"Failed to set status online for account {account.id}: {e}"
            )
            
        return client

    async def get_channel_peer(
        self, client: Client, account: TelegramAccount, target_group_username: str
    ):
        """Ensure membership and resolve channel peer."""
        membership = await self._ensure_chat_membership(
            client, account, target_group_username
        )
        if membership == InviteStatus.WAITING:
            return InviteStatus.WAITING
            
        resolved_target = (
            membership if isinstance(membership, str) else target_group_username
        )
        return await client.resolve_peer(resolved_target)

    async def add_single_user(
        self,
        client: Client,
        account: TelegramAccount,
        target_user: TargetUser,
        channel_peer: any,
        target_group_username: str,
    ) -> InviteStatus:
        """Invite a single user using an already active client and resolved channel peer."""
        if not target_user.username:
            logger.warning(f"Target user {target_user.id} has no username. Skipping.")
            target_user.is_invited = True
            await target_user.save()
            return InviteStatus.ERROR

        user_ref = target_user.username.strip("@")
        status = InviteStatus.ERROR
        error_msg = None

        try:
            # 1. Profile View / Validation
            try:
                logger.info(f"Simulating profile view for {user_ref} via {account.id}")
                user_obj = await client.get_users(user_ref)
                
                if user_obj.is_deleted:
                    logger.warning(f"Target user {user_ref} is deleted. Marking as invited.")
                    target_user.is_invited = True
                    await target_user.save()
                    return InviteStatus.ERROR

                view_delay = random.uniform(4, 10)
                await asyncio.sleep(view_delay)
                user_peer = await client.resolve_peer(user_obj.id)
            except (UsernameNotOccupied, UsernameInvalid, PeerIdInvalid):
                logger.warning(f"Target user {user_ref} does not exist. Marking as invited.")
                target_user.is_invited = True
                await target_user.save()
                return InviteStatus.ERROR
            except Exception as e:
                logger.warning(f"Failed to view profile for {user_ref}: {e}")
                # We raise to let _handle_invite_error decide if it's fatal
                raise e 

            # 2. Add Contact (Conditional/Randomized)
            # Only 20% chance to add contact if username is present
            if random.random() < 0.2:
                try:
                    logger.info(f"Randomly adding contact {user_ref} via {account.id}")
                    await client.add_contact(
                        user_id=user_obj.id,
                        first_name=user_obj.first_name or user_ref,
                    )
                    await asyncio.sleep(random.uniform(8, 20))
                except Exception as e:
                    logger.warning(f"Failed to add contact {user_ref}: {e}")

            # 3. Final Delay & Invite
            await asyncio.sleep(random.uniform(3, 6))
            await client.invoke(
                InviteToChannel(channel=channel_peer, users=[user_peer])
            )

            status = InviteStatus.SUCCESS
            logger.info(f"Successfully invited {user_ref} via {account.id}")

            account.invites_today += 1
            await account.save()
            target_user.is_invited = True
            await target_user.save()

        except Exception as e:
            status, error_msg = await self._handle_invite_error(e, account, target_user)

        # Log the attempt
        try:
            await InviteLog.create(
                account=account,
                target_user=target_user,
                target_group_id=target_group_username,
                status=status,
                error_message=error_msg,
            )
        except Exception as e:
            logger.error(f"Failed to create InviteLog: {e}")

        return status

    async def add_chat_members(
        self,
        account: TelegramAccount,
        target_user: TargetUser,
        target_group_username: str,
    ) -> InviteStatus:
        """
        Legacy method for single-invite fallback. 
        Will start client, invite one user, and stop client.
        """
        app_settings, _ = await AppSettings.get_or_create(id=1)
        if account.status != AccountStatus.ACTIVE or account.invites_today >= app_settings.daily_invite_limit:
            return InviteStatus.ERROR

        client = None
        try:
            client = await self.start_client(account)
            channel_peer = await self.get_channel_peer(client, account, target_group_username)
            if channel_peer == InviteStatus.WAITING:
                return InviteStatus.WAITING
            
            status = await self.add_single_user(client, account, target_user, channel_peer, target_group_username)
            
            if status == InviteStatus.SUCCESS:
                # Wait a bit before stopping client to seem less "transactional"
                await asyncio.sleep(random.uniform(10, 25))
            return status
        except Exception as e:
            status, error_msg = await self._handle_invite_error(e, account, target_user)
            return status
        finally:
            if client and client.is_connected:
                try:
                    await client.stop()
                except Exception as e:
                    logger.error(f"Error stopping client {account.id}: {e}")
