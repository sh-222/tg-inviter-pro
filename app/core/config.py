from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    app_title: str = "TG Inviter Pro"
    debug: bool = False

    # Security (Required)
    admin_username: str
    admin_password: str

    # Internal paths
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR

    # Database configuration
    db_url: str = f"sqlite://{DATA_DIR}/db.sqlite3"

    # Core logic limits and settings (Required)
    min_delay_seconds: int
    max_delay_seconds: int
    daily_invite_limit: int

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
