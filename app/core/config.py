import json
from typing import List, Union
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    SECRET_KEY: str = "changeme-very-long-random-string"
    DATABASE_URL: str = "sqlite+aiosqlite:////app/data/woi_autotrader.db"
    CORS_ORIGINS: Union[str, List[str]] = '["http://localhost:3000"]'
    ACCESS_TOKEN_EXPIRE_DAYS: int = 7

    MOFSL_BASE_URL: str = "https://openapi.motilaloswal.com"
    MOFSL_WS_URL: str = "wss://openapi.motilaloswal.com/ws"
    MOFSL_PRODUCT_NAME: str = "WOIAutoTrader"
    MOFSL_PRODUCT_VERSION: str = "1.0.0"
    STATIC_IP: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_cors_origins(self) -> List[str]:
        origins = self.CORS_ORIGINS
        if isinstance(origins, list):
            return origins
        # Try JSON list first
        try:
            parsed = json.loads(origins)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # Comma-separated fallback
        return [o.strip() for o in origins.split(",") if o.strip()]


settings = Settings()
