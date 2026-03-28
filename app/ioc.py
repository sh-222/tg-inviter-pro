from typing import Callable
from dishka import Provider, Scope, provide
from pyrogram import Client

from app.core.config import settings
from app.core.models import TelegramAccount
from app.services.csv_reader import CSVReaderService
from app.services.inviter import InviterService
from app.services.runner import InviterRunner


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def csv_reader(self) -> CSVReaderService:
        return CSVReaderService()

    @provide(scope=Scope.APP)
    def kurigram_client_factory(self) -> Callable[[TelegramAccount], Client]:
        """
        Factory for creating Kurigram sessions.
        """

        def factory(account: TelegramAccount) -> Client:
            import urllib.parse

            proxy_dict = None
            if account.proxy:
                parsed = urllib.parse.urlparse(account.proxy)
                proxy_dict = {
                    "scheme": parsed.scheme,
                    "hostname": parsed.hostname,
                    "port": parsed.port,
                    "username": parsed.username,
                    "password": parsed.password,
                }

            session_kwargs = {
                "api_id": account.api_id,
                "api_hash": account.api_hash,
                "proxy": proxy_dict,
                "workdir": str(settings.data_dir),
                "password": account.password,
                "no_updates": True,
            }

            if account.device_model:
                session_kwargs["device_model"] = account.device_model
            if account.system_version:
                session_kwargs["system_version"] = account.system_version
            if account.app_version:
                session_kwargs["app_version"] = account.app_version

            if account.session_string.startswith("file:"):
                file_name = account.session_string[5:]
                if file_name.endswith(".session"):
                    file_name = file_name[:-8]
                session_kwargs["name"] = file_name
            else:
                session_kwargs["name"] = f"session_{account.id}"
                session_kwargs["session_string"] = account.session_string

            return Client(**session_kwargs)

        return factory

    @provide
    def inviter_service(
        self, client_factory: Callable[[TelegramAccount], Client]
    ) -> InviterService:
        return InviterService(client_factory=client_factory)

    @provide(scope=Scope.APP)
    def inviter_runner(self, inviter_service: InviterService) -> InviterRunner:
        return InviterRunner(inviter_service=inviter_service)
