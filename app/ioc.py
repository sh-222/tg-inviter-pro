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

            return Client(
                name=f"session_{account.id}",
                api_id=account.api_id,
                api_hash=account.api_hash,
                proxy=proxy_dict,
                workdir=str(settings.data_dir),
            )

        return factory

    @provide
    def inviter_service(
        self, client_factory: Callable[[TelegramAccount], Client]
    ) -> InviterService:
        return InviterService(client_factory=client_factory)

    @provide(scope=Scope.APP)
    def inviter_runner(self, inviter_service: InviterService) -> InviterRunner:
        return InviterRunner(inviter_service=inviter_service)
