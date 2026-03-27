from tortoise import fields, models
from enum import Enum


class AccountStatus(str, Enum):
    ACTIVE = "active"
    BANNED = "banned"
    FLOOD_WAIT = "flood_wait"
    INACTIVE = "inactive"
    LIMIT_REACHED = "limit_reached"


class TelegramAccount(models.Model):
    id = fields.IntField(pk=True)
    api_id = fields.IntField()
    api_hash = fields.CharField(max_length=255)
    phone_number = fields.CharField(max_length=20, unique=True, null=True)
    password = fields.CharField(
        max_length=255, null=True, description="2FA Password for login if required"
    )
    session_string = fields.TextField()
    proxy = fields.CharField(
        max_length=255,
        null=True,
        description="HTTP/SOCKS5 proxy, e.g., socks5://user:pass@ip:port",
    )
    device_model = fields.CharField(max_length=255, null=True)
    system_version = fields.CharField(max_length=255, null=True)
    app_version = fields.CharField(max_length=255, null=True)
    status = fields.CharEnumField(AccountStatus, default=AccountStatus.ACTIVE)
    invites_today = fields.IntField(default=0)
    joined_chats = fields.JSONField(default=dict)
    frozen_until = fields.DatetimeField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "telegram_accounts"

    def __str__(self):
        return f"<Account {self.phone_number or self.id} [{self.status}]>"


class TargetUser(models.Model):
    id = fields.IntField(pk=True)
    tg_id = fields.BigIntField(null=True, unique=True)
    username = fields.CharField(max_length=255, null=True, unique=True)
    full_name = fields.CharField(max_length=255, null=True)
    is_invited = fields.BooleanField(default=False)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "target_users"


class InviteStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PRIVACY_RESTRICTED = "privacy_restricted"
    ALREADY_PARTICIPANT = "already_participant"
    WAITING = "waiting"


class InviteLog(models.Model):
    id = fields.IntField(pk=True)
    account = fields.ForeignKeyField(
        "models.TelegramAccount", related_name="invite_logs"
    )
    target_user = fields.ForeignKeyField(
        "models.TargetUser", related_name="invite_logs"
    )
    target_group_id = fields.CharField(max_length=255)
    status = fields.CharEnumField(InviteStatus)
    error_message = fields.TextField(null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "invite_logs"


class AppSettings(models.Model):
    id = fields.IntField(pk=True)
    min_delay_seconds = fields.IntField(default=300)
    max_delay_seconds = fields.IntField(default=600)
    daily_invite_limit = fields.IntField(default=50)

    class Meta:
        table = "app_settings"
