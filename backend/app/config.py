from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    storage_backend: Literal["firestore", "memory"] = "firestore"
    firebase_project_id: str = "demo-amazego"
    firestore_emulator_host: str | None = None
    google_application_credentials: str | None = None
    firebase_service_account_json: SecretStr | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    levels_dir: Path = ROOT / "data" / "levels"
    admin_username: str = "admin"
    admin_password_hash: SecretStr | None = None
    admin_session_secret: SecretStr | None = None
