from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIDEBENCH_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    region: str = "global"
    api_token: str = Field(default="", repr=False)
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:8080,http://127.0.0.1:8080"
    )
    build_sha: str = "development"
    worker_enabled: bool = True

    @property
    def database(self) -> Path:
        return self.data_dir / "tidebench.sqlite3"

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def hosts(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]
