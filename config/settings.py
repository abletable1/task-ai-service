import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    auth_key: str = ""
    scope: str = "GIGACHAT_API_PERS"
    auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    ca_bundle_file: str = ""
    model: str = "GigaChat-2"
    base_url: str = "https://api.giga.chat/v1"
    temperature: float = 0.2
    timeout_seconds: float = 15
    max_attempts: int = 3
    retry_base_seconds: float = 1
    cache_ttl_seconds: float = 600
    cache_max_entries: int = 1000
    max_output_tokens: int = 400
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self):
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("LLM_MAX_ATTEMPTS должен быть от 1 до 3")
        for name in ("timeout_seconds", "retry_base_seconds", "cache_ttl_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} должен быть положительным конечным числом")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("TEMPERATURE должен быть от 0 до 2")
        if self.cache_max_entries < 1 or self.max_output_tokens < 1:
            raise ValueError("Размер кеша и лимит токенов должны быть положительными")
        if not self.model.strip() or not self.base_url.startswith(("https://", "http://")):
            raise ValueError("Неверные модель или GIGACHAT_BASE_URL")
        if self.scope not in {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
            raise ValueError("Неверный GIGACHAT_SCOPE")
        if not self.auth_url.startswith(("https://", "http://")):
            raise ValueError("Неверный GIGACHAT_AUTH_URL")
        if self.ca_bundle_file and not self.ca_bundle_path.is_file():
            raise ValueError("Файл GIGACHAT_CA_BUNDLE_FILE не найден")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT должен быть от 1 до 65535")

    @property
    def ca_bundle_path(self):
        path = Path(self.ca_bundle_file)
        return path if path.is_absolute() else ROOT / path

    @property
    def tls_verify(self):
        return str(self.ca_bundle_path) if self.ca_bundle_file else True

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            auth_key=os.getenv("GIGACHAT_AUTH_KEY", "").strip(),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip(),
            auth_url=os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth").strip(),
            ca_bundle_file=os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip(),
            model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
            base_url=os.getenv("GIGACHAT_BASE_URL", "https://api.giga.chat/v1").rstrip("/"),
            temperature=float(os.getenv("TEMPERATURE", "0.2")),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "15")),
            max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "3")),
            retry_base_seconds=float(os.getenv("RETRY_BASE_SECONDS", "1")),
            cache_ttl_seconds=float(os.getenv("CACHE_TTL_SECONDS", "600")),
            cache_max_entries=int(os.getenv("CACHE_MAX_ENTRIES", "1000")),
            max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "400")),
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")),
        )
